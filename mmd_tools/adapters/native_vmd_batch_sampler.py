"""Python gateway for the optional native Mode C scalar sampler.

The gateway owns only the wire protocol and conservative route classification.
It deliberately does not evaluate Maya plugs itself: callers can fall back to
the existing collector evaluator when the command is unavailable or when a
packed result cannot be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Optional, Sequence


_BONE_EXPORT_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)
_DIRECT_ANIM_CURVE_TYPES = {"animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU"}
_NUMERIC_ATTR_TYPES = {
    "bool",
    "byte",
    "double",
    "doubleAngle",
    "doubleLinear",
    "float",
    "floatAngle",
    "floatLinear",
    "integer",
    "long",
    "short",
    "time",
    "enum",
}
_HEADER_SIZE = 6
_PROTOCOL_VERSION = 1
# Must stay in lock-step with the native command's request sample guard.
MAX_NATIVE_SAMPLES = 4_194_304
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


class NativeVmdBatchSamplerError(RuntimeError):
    """Raised when the native command or its packed result is not trusted."""


@dataclass(frozen=True)
class DenseBoneSampleChannel:
    """One logical bone channel and its deduplicated physical plug."""

    joint: str
    attr: str
    plug: str
    unit: str
    hint: str
    physical_index: int

    @property
    def logical_key(self) -> tuple[str, str]:
        return self.joint, self.attr

    def request(self) -> dict[str, str]:
        return {"plug": self.plug, "unit": self.unit, "hint": self.hint}


@dataclass(frozen=True)
class DenseBoneSamplePlan:
    """Deterministic frame/physical-channel request plus logical mapping."""

    frames: tuple[float, ...]
    physical_channels: tuple[DenseBoneSampleChannel, ...]
    logical_channels: tuple[DenseBoneSampleChannel, ...]
    _frame_indices: Mapping[float, int] = field(init=False, repr=False)
    _logical_indices: Mapping[tuple[str, str], int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Dense collection performs one lookup per scalar channel.  Keep both
        # indices immutable-at-the-API-boundary so the hot path is O(1), not a
        # repeated linear search through all frames/logical aliases.
        object.__setattr__(
            self,
            "_frame_indices",
            {frame: index for index, frame in enumerate(self.frames)},
        )
        object.__setattr__(
            self,
            "_logical_indices",
            {
                channel.logical_key: channel.physical_index
                for channel in self.logical_channels
            },
        )

    @property
    def request_channels(self) -> tuple[dict[str, str], ...]:
        return tuple(channel.request() for channel in self.physical_channels)


@dataclass(frozen=True)
class NativeDenseBoneSamples:
    """Validated frame-major samples, addressable by logical bone channel."""

    plan: DenseBoneSamplePlan
    rows: tuple[tuple[float, ...], ...]
    strategy_counts: Mapping[str, int]
    wall_sec: float
    chunk_count: int = 1
    max_frames_per_chunk: int = MAX_NATIVE_SAMPLES
    max_samples_per_chunk: int = MAX_NATIVE_SAMPLES
    chunk_wall_secs: tuple[float, ...] = ()

    def value(self, joint: str, attr: str, frame: float) -> float:
        """Return one logical sample, including duplicate-plug aliases."""

        frame_value = float(frame)
        frame_index = self.plan._frame_indices.get(frame_value)
        physical_index = self.plan._logical_indices.get((str(joint), str(attr)))
        if frame_index is None or physical_index is None:
            raise KeyError((joint, attr, frame))
        return self.rows[frame_index][physical_index]

    @property
    def sample_count(self) -> int:
        return len(self.rows) * len(self.plan.physical_channels)

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "available": True,
            "used": True,
            "strategy_counts": dict(self.strategy_counts),
            "physical_channel_count": len(self.plan.physical_channels),
            "logical_channel_count": len(self.plan.logical_channels),
            "frame_count": len(self.plan.frames),
            "sample_count": self.sample_count,
            "wall_sec": self.wall_sec,
            "chunk_count": self.chunk_count,
            "max_frames_per_chunk": self.max_frames_per_chunk,
            "max_samples_per_chunk": self.max_samples_per_chunk,
            "chunk_wall_sec": list(self.chunk_wall_secs),
        }


def _canonical_node(node: Any, cmds_module: Any) -> str:
    value = str(node)
    if cmds_module is None:
        return value
    ls = getattr(cmds_module, "ls", None)
    if not callable(ls):
        return value
    matches = ls(value, long=True) or []
    if isinstance(matches, (str, bytes)) or len(matches) != 1:
        raise NativeVmdBatchSamplerError(f"node is not uniquely resolvable: {value!r}")
    return str(matches[0])


def _route_for_joint(
    joint: str,
    input_routes: Mapping[str, Mapping[str, Sequence[str]]],
    cmds_module: Any,
) -> Mapping[str, Sequence[str]]:
    long_name = _canonical_node(joint, cmds_module)
    return input_routes.get(long_name) or input_routes.get(str(joint)) or {}


def _connections(cmds_module: Any, plug: str) -> list[str]:
    if cmds_module is None:
        return []
    method = getattr(cmds_module, "listConnections", None)
    if not callable(method):
        return []
    try:
        values = method(plug, source=True, destination=False, plugs=True) or []
    except Exception:
        return []
    return [str(value) for value in values]


def _node_type(cmds_module: Any, node: str) -> str:
    method = getattr(cmds_module, "nodeType", None) if cmds_module is not None else None
    if not callable(method):
        return ""
    try:
        return str(method(node) or "")
    except Exception:
        return ""


def _has_parent_incoming(cmds_module: Any, node: str, attr: str) -> bool:
    """Conservatively reject compound/array routes for direct/static hints."""

    if "[" in attr or "." in attr:
        return True
    if cmds_module is None:
        return True
    attribute_query = getattr(cmds_module, "attributeQuery", None)
    if not callable(attribute_query):
        return False
    try:
        parent = attribute_query(attr, node=node, listParent=True) or []
    except Exception:
        return True
    if not parent:
        return False
    parent_name = str(parent[0])
    return bool(_connections(cmds_module, f"{node}.{parent_name}"))


def _direct_curve_hint(cmds_module: Any, node: str, attr: str, plug: str) -> bool:
    if _has_parent_incoming(cmds_module, node, attr):
        return False
    incoming = _connections(cmds_module, plug)
    if len(incoming) != 1:
        return False
    source_node, separator, source_attr = incoming[0].rpartition(".")
    if not separator or source_attr != "output":
        return False
    return _node_type(cmds_module, source_node) in _DIRECT_ANIM_CURVE_TYPES


def _static_hint(cmds_module: Any, node: str, attr: str, plug: str) -> bool:
    if _has_parent_incoming(cmds_module, node, attr) or _connections(cmds_module, plug):
        return False
    get_attr = getattr(cmds_module, "getAttr", None) if cmds_module is not None else None
    if not callable(get_attr):
        return False
    try:
        value_type = str(get_attr(plug, type=True) or "")
    except Exception:
        return False
    return value_type in _NUMERIC_ATTR_TYPES


def _hint_for_plug(cmds_module: Any, node: str, attr: str, plug: str) -> str:
    if _direct_curve_hint(cmds_module, node, attr, plug):
        return "direct_curve"
    if _static_hint(cmds_module, node, attr, plug):
        return "static"
    return "timed_mplug"


def build_dense_bone_sample_plan(
    joints: Sequence[str],
    frames: Sequence[float],
    input_routes: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    cmds_module: Any = None,
) -> DenseBoneSamplePlan:
    """Build stable logical order and deduplicated physical channel order."""

    normalized_frames = tuple(float(frame) for frame in frames)
    if not normalized_frames or any(not math.isfinite(frame) for frame in normalized_frames):
        raise NativeVmdBatchSamplerError("dense sampler requires finite frames")
    if any(right <= left for left, right in zip(normalized_frames, normalized_frames[1:])):
        raise NativeVmdBatchSamplerError("dense sampler frames must be strictly increasing")
    routes = input_routes or {}
    physical: list[DenseBoneSampleChannel] = []
    logical: list[DenseBoneSampleChannel] = []
    physical_by_plug: dict[str, int] = {}
    for joint in joints:
        joint_name = str(joint)
        long_joint = _canonical_node(joint_name, cmds_module)
        route = _route_for_joint(joint_name, routes, cmds_module)
        for attr in _BONE_EXPORT_ATTRS:
            routed = route.get(attr, (long_joint, attr))
            try:
                node, target_attr = routed
            except (TypeError, ValueError) as exc:
                raise NativeVmdBatchSamplerError(
                    f"invalid authored route for {joint_name}.{attr}"
                ) from exc
            node = _canonical_node(node, cmds_module)
            target_attr = str(target_attr)
            plug = f"{node}.{target_attr}"
            unit = "angle" if attr.startswith("rotate") else "distance"
            physical_index = physical_by_plug.get(plug)
            if physical_index is None:
                hint = _hint_for_plug(cmds_module, node, target_attr, plug)
                physical_index = len(physical)
                physical_by_plug[plug] = physical_index
                physical.append(
                    DenseBoneSampleChannel(
                        joint=joint_name,
                        attr=attr,
                        plug=plug,
                        unit=unit,
                        hint=hint,
                        physical_index=physical_index,
                    )
                )
            elif physical[physical_index].unit != unit:
                raise NativeVmdBatchSamplerError(
                    f"physical plug has conflicting logical units: {plug!r}"
                )
            logical.append(
                DenseBoneSampleChannel(
                    joint=joint_name,
                    attr=attr,
                    plug=plug,
                    unit=physical[physical_index].unit,
                    hint=physical[physical_index].hint,
                    physical_index=physical_index,
                )
            )
    return DenseBoneSamplePlan(
        frames=normalized_frames,
        physical_channels=tuple(physical),
        logical_channels=tuple(logical),
    )


def _header_int(value: Any, name: str) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeVmdBatchSamplerError(f"packed header {name} is not numeric") from exc
    if not math.isfinite(numeric) or numeric != int(numeric):
        raise NativeVmdBatchSamplerError(f"packed header {name} is not an exact integer")
    return int(numeric)


def parse_packed_result(
    packed: Sequence[Any],
    plan: DenseBoneSamplePlan,
) -> tuple[tuple[tuple[float, ...], ...], dict[str, int]]:
    """Validate C++ frame-major output and return rows plus actual strategies."""

    if (
        not plan.frames
        or any(not math.isfinite(frame) for frame in plan.frames)
        or any(right <= left for left, right in zip(plan.frames, plan.frames[1:]))
    ):
        raise NativeVmdBatchSamplerError("native sampler plan frames are not ordered")
    try:
        values = list(packed)
    except TypeError as exc:
        raise NativeVmdBatchSamplerError("native sampler result is not iterable") from exc
    if len(values) < _HEADER_SIZE:
        raise NativeVmdBatchSamplerError("native sampler result is shorter than its header")
    version = _header_int(values[0], "version")
    frame_count = _header_int(values[1], "frame_count")
    channel_count = _header_int(values[2], "channel_count")
    direct_count = _header_int(values[3], "direct_count")
    static_count = _header_int(values[4], "static_count")
    timed_count = _header_int(values[5], "timed_count")
    if version != _PROTOCOL_VERSION:
        raise NativeVmdBatchSamplerError(f"unsupported native sampler protocol: {version}")
    if frame_count != len(plan.frames) or channel_count != len(plan.physical_channels):
        raise NativeVmdBatchSamplerError("native sampler frame/channel header mismatch")
    if min(direct_count, static_count, timed_count) < 0:
        raise NativeVmdBatchSamplerError("native sampler strategy count is negative")
    if direct_count + static_count + timed_count != channel_count:
        raise NativeVmdBatchSamplerError("native sampler strategy counts do not sum to channels")
    expected_length = _HEADER_SIZE + frame_count * channel_count
    if len(values) != expected_length:
        raise NativeVmdBatchSamplerError("native sampler packed result has unexpected length")
    rows = []
    offset = _HEADER_SIZE
    for _frame_index in range(frame_count):
        row = []
        for _channel_index in range(channel_count):
            try:
                number = float(values[offset])
            except (TypeError, ValueError, OverflowError) as exc:
                raise NativeVmdBatchSamplerError("native sampler value is not numeric") from exc
            if not math.isfinite(number):
                raise NativeVmdBatchSamplerError("native sampler value is not finite")
            row.append(number)
            offset += 1
        rows.append(tuple(row))
    return tuple(rows), {
        "direct_curve": direct_count,
        "static": static_count,
        "timed_mplug": timed_count,
    }


def _chunk_plan(plan: DenseBoneSamplePlan, start: int, end: int) -> DenseBoneSamplePlan:
    """Make a local frame-index plan while preserving channel/logical order."""

    return DenseBoneSamplePlan(
        frames=plan.frames[start:end],
        physical_channels=plan.physical_channels,
        logical_channels=plan.logical_channels,
    )


class NativeVmdBatchSampler:
    """Invoke ``mmdVmdBatchSample`` without changing Maya current time."""

    command_name = "mmdVmdBatchSample"

    def __init__(self, cmds_module: Any = None, diagnostics_sink=None) -> None:
        self._cmds = cmds_module
        self._diagnostics_sink = diagnostics_sink
        self._plugin_attempted = False
        self._plugin_path: Optional[str] = None
        self.last_diagnostics: dict[str, Any] = {
            "available": callable(
                getattr(cmds_module, self.command_name, None)
            )
            if cmds_module is not None
            else False,
            "used": False,
            "plugin_load_status": "not_attempted",
        }

    def set_diagnostics_sink(self, sink) -> None:
        """Attach a low-volume sink for pre-command timeout evidence."""

        self._diagnostics_sink = sink

    def _publish_diagnostics(self) -> None:
        sink = self._diagnostics_sink
        if not callable(sink):
            return
        try:
            sink(dict(self.last_diagnostics))
        except Exception:
            # Diagnostics must never change native sampling semantics.
            return

    @property
    def available(self) -> bool:
        if self._cmds is None:
            try:
                from maya import cmds as maya_cmds
            except Exception:
                self._plugin_attempted = True
                self.last_diagnostics.update(
                    {
                        "available": False,
                        "plugin_load_status": "maya_unavailable",
                    }
                )
                return False
            self._cmds = maya_cmds
        if callable(getattr(self._cmds, self.command_name, None)):
            self.last_diagnostics.update(
                {
                    "available": True,
                    "plugin_load_status": self.last_diagnostics.get(
                        "plugin_load_status"
                    )
                    if self.last_diagnostics.get("plugin_load_status")
                    not in {"not_attempted", "already_available"}
                    else "already_available",
                }
            )
            return True
        if not self._plugin_attempted:
            self._load_plugin_once()
        available = callable(getattr(self._cmds, self.command_name, None))
        self.last_diagnostics["available"] = available
        if not available and self.last_diagnostics.get("plugin_load_status") == "loaded":
            self.last_diagnostics["plugin_load_status"] = "registration_missing"
        return available

    def _load_plugin_once(self) -> None:
        """Locate/load the canonical C++ plugin at most once per gateway."""

        self._plugin_attempted = True
        try:
            from mmd_tools.core import cpp_plugin_locator

            maya_version = cpp_plugin_locator.running_maya_major_version(
                self._cmds,
                default="2024",
            )
            candidates = cpp_plugin_locator.plugin_candidate_paths(
                [_PLUGIN_ROOT], maya_version=maya_version
            )
            path = cpp_plugin_locator.find_plugin_path(candidates)
            if path is None:
                # Keep the deterministic explicit candidate in diagnostics so
                # a missing build is actionable, including env overrides.
                self._plugin_path = str(candidates[0]) if candidates else None
                self.last_diagnostics.update(
                    {
                        "plugin_path": self._plugin_path,
                        "plugin_load_status": "missing",
                        "plugin_load_error": "canonical C++ plugin was not found",
                    }
                )
                return
            self._plugin_path = str(path)
            self.last_diagnostics["plugin_path"] = self._plugin_path
            cpp_plugin_locator.prepare_plugin_directory(path)
            loaded = cpp_plugin_locator.load_plugin(
                path,
                self._cmds,
                prepare=False,
            )
            self.last_diagnostics["plugin_load_status"] = (
                "loaded" if loaded else "already_loaded"
            )
        except Exception as exc:
            self.last_diagnostics.update(
                {
                    "plugin_path": self._plugin_path,
                    "plugin_load_status": "error",
                    "plugin_load_error": f"{type(exc).__name__}: {exc}",
                }
            )

    def sample_dense_bone_channels(
        self,
        frames: Sequence[float],
        joints: Sequence[str],
        input_routes: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    ) -> NativeDenseBoneSamples:
        started = time.perf_counter()
        if not self.available:
            self.last_diagnostics.update(
                {
                    "available": False,
                    "used": False,
                    "fallback_reason": "native command is unavailable",
                }
            )
            self._publish_diagnostics()
            raise NativeVmdBatchSamplerError("native command is unavailable")
        plugin_diagnostics = {
            key: value
            for key, value in self.last_diagnostics.items()
            if key.startswith("plugin_")
            or key
            in {
                "plugin_path",
                "plugin_load_status",
                "plugin_load_error",
            }
        }
        plan = build_dense_bone_sample_plan(
            joints,
            frames,
            input_routes=input_routes,
            cmds_module=self._cmds,
        )
        if not plan.physical_channels:
            raise NativeVmdBatchSamplerError("native sampler requires at least one channel")
        command = getattr(self._cmds, self.command_name)
        physical_channel_count = len(plan.physical_channels)
        max_frames_per_chunk = max(
            1,
            MAX_NATIVE_SAMPLES // physical_channel_count,
        )
        chunk_count = (
            len(plan.frames) + max_frames_per_chunk - 1
        ) // max_frames_per_chunk
        rows = []
        strategy_counts = None
        chunk_wall_secs = []
        current_chunk_index = -1
        self.last_diagnostics = {
            **plugin_diagnostics,
            "available": True,
            "used": True,
            "status": "sampling",
            "chunk_index": -1,
            "chunk_count": chunk_count,
            "channel_count": physical_channel_count,
            "frame_count": len(plan.frames),
            "sample_count": len(plan.frames) * physical_channel_count,
            "max_frames_per_chunk": max_frames_per_chunk,
            "max_samples_per_chunk": max_frames_per_chunk * physical_channel_count,
        }
        self._publish_diagnostics()
        try:
            for _chunk_index, start in enumerate(
                range(0, len(plan.frames), max_frames_per_chunk)
            ):
                end = min(start + max_frames_per_chunk, len(plan.frames))
                chunk_plan = _chunk_plan(plan, start, end)
                current_chunk_index = start // max_frames_per_chunk
                self.last_diagnostics.update(
                    {
                        "status": "sampling_chunk",
                        "chunk_index": current_chunk_index,
                        "chunk_frame_start": start,
                        "chunk_frame_end": end - 1,
                        "chunk_frame_count": end - start,
                        "chunk_sample_count": (end - start) * physical_channel_count,
                    }
                )
                self._publish_diagnostics()
                payload = json.dumps(
                    {
                        "version": _PROTOCOL_VERSION,
                        "frames": list(chunk_plan.frames),
                        "channels": list(chunk_plan.request_channels),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                chunk_started = time.perf_counter()
                packed = command(payload=payload)
                chunk_rows, chunk_strategies = parse_packed_result(
                    packed,
                    chunk_plan,
                )
                chunk_wall_secs.append(
                    round(time.perf_counter() - chunk_started, 6)
                )
                if strategy_counts is None:
                    strategy_counts = chunk_strategies
                elif strategy_counts != chunk_strategies:
                    raise NativeVmdBatchSamplerError(
                        "native sampler strategy counts differ between chunks"
                    )
                rows.extend(chunk_rows)
        except Exception as exc:
            self.last_diagnostics = {
                **plugin_diagnostics,
                "available": True,
                "used": False,
                "fallback_reason": f"{type(exc).__name__}: {exc}",
                "wall_sec": round(time.perf_counter() - started, 6),
                "chunk_count": chunk_count,
                "chunk_index": current_chunk_index,
                "channel_count": physical_channel_count,
                "frame_count": len(plan.frames),
                "sample_count": len(plan.frames) * physical_channel_count,
                "max_frames_per_chunk": max_frames_per_chunk,
                "max_samples_per_chunk": max_frames_per_chunk * physical_channel_count,
                "chunk_wall_sec": chunk_wall_secs,
                "status": "failed",
                "protocol_failure": isinstance(exc, NativeVmdBatchSamplerError),
                "protocol_error": str(exc)
                if isinstance(exc, NativeVmdBatchSamplerError)
                else None,
            }
            self._publish_diagnostics()
            if isinstance(exc, NativeVmdBatchSamplerError):
                raise
            raise NativeVmdBatchSamplerError("native sampler invocation failed") from exc
        if strategy_counts is None:
            raise NativeVmdBatchSamplerError("native sampler produced no chunks")
        result = NativeDenseBoneSamples(
            plan=plan,
            rows=tuple(rows),
            strategy_counts=strategy_counts,
            wall_sec=round(time.perf_counter() - started, 6),
            chunk_count=chunk_count,
            max_frames_per_chunk=max_frames_per_chunk,
            max_samples_per_chunk=max_frames_per_chunk * physical_channel_count,
            chunk_wall_secs=tuple(chunk_wall_secs),
        )
        self.last_diagnostics = {**plugin_diagnostics, **result.diagnostics}
        self.last_diagnostics.update(
            {
                "status": "completed",
                "chunk_index": chunk_count - 1,
                "protocol_failure": False,
            }
        )
        self._publish_diagnostics()
        return result

    # Keep a short alias for injected test doubles and future adapter callers.
    sample_dense_bones = sample_dense_bone_channels


__all__ = [
    "DenseBoneSampleChannel",
    "DenseBoneSamplePlan",
    "NativeDenseBoneSamples",
    "NativeVmdBatchSampler",
    "NativeVmdBatchSamplerError",
    "MAX_NATIVE_SAMPLES",
    "build_dense_bone_sample_plan",
    "parse_packed_result",
]
