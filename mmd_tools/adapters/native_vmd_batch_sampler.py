"""Python gateway for the native Bake Timeline scalar sampler.

The gateway owns the strict wire protocol and conservative route
classification.  Bake Timeline has one production policy: Maya Timeline evaluation.
Native command, protocol, and value failures are surfaced to the collector;
there is no alternate evaluator fallback for dense bone sampling.  Constant,
unconnected pre-physics inputs are read once in Python so a Maya session that
already loaded an older sampler binary can still export without altering its
scene graph.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
import json
import math
import mmap
import os
from pathlib import Path
import struct
import tempfile
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
_TIMING_HEADER_SIZE = 9
_TIMING_PROTOCOL = "wall_v3"
_DIRECT_SPOOL_MODE = "direct_spool"
_DIRECT_ACK_VERSION = 1
_DIRECT_CHECKPOINT_RECORD_SIZE = 10
_PROTOCOL_VERSION = 2
EVALUATION_POLICY = "maya_timeline_bake_v1"
# Must stay in lock-step with the native command's request sample guard.
MAX_NATIVE_SAMPLES = 4_194_304
# Keep one native command bounded even when the sample-count guard permits a
# much larger request.  Chunk boundaries are intentionally visible in the
# diagnostics and preserve the production Timeline transaction contract.
MAX_NATIVE_FRAMES = 120
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_DOUBLE_ITEM_SIZE = array("d").itemsize
if _DOUBLE_ITEM_SIZE != struct.calcsize("=d"):
    raise RuntimeError("native sampler requires an 8-byte double array")


class NativeVmdBatchSamplerError(RuntimeError):
    """Raised when the native command or its direct-spool result is not trusted."""


class NativeDirectSpoolUnsupportedError(NativeVmdBatchSamplerError):
    """Raised only when an already-loaded old plugin rejects direct-spool mode."""


_DIRECT_SPOOL_UNSUPPORTED_MARKERS = (
    # Current binaries expose a typed capability diagnostic.
    "unsupported native sampler request mode",
    # A pre-direct-spool binary rejects the additional mode/spool keys using
    # its legacy request-shape diagnostic.  This exact text is safe to treat
    # as capability-only; other command failures remain fatal.
    "payload requires version=2, frames, channels, and evaluation_policy=maya_timeline_bake_v1",
)


def _is_direct_spool_unsupported_error(exc: BaseException) -> bool:
    """Recognize the capability error emitted by an old loaded plug-in."""

    return (
        isinstance(exc, RuntimeError)
        and not isinstance(exc, NativeVmdBatchSamplerError)
        and any(marker in str(exc).lower() for marker in _DIRECT_SPOOL_UNSUPPORTED_MARKERS)
    )


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
class NativeDenseBoneTrack:
    """Detached, reiterable SoA values for one logical bone.

    Public construction and accessors copy component arrays.  The sampler's
    internal constructor accepts arrays already detached from its mmap, so the
    trusted collector path can consume them without another six-array copy.
    The track remains usable after :meth:`NativeDenseBoneSamples.close`.
    Components always follow ``translateX/Y/Z, rotateX/Y/Z`` order.
    """

    _frames: tuple[float, ...]
    _components: tuple[array, ...]

    def __post_init__(self) -> None:
        frames = tuple(float(frame) for frame in self._frames)
        components = tuple(array("d", component) for component in self._components)
        self._validate_shape(frames, components)
        object.__setattr__(self, "_frames", frames)
        object.__setattr__(self, "_components", components)

    @staticmethod
    def _validate_shape(
        frames: tuple[float, ...], components: tuple[array, ...]
    ) -> None:
        if (
            not frames
            or any(not math.isfinite(frame) for frame in frames)
            or any(right <= left for left, right in zip(frames, frames[1:]))
            or len(components) != len(_BONE_EXPORT_ATTRS)
            or any(len(component) != len(frames) for component in components)
        ):
            raise NativeVmdBatchSamplerError("native bone track has invalid shape")

    @classmethod
    def _from_detached(
        cls,
        frames: Sequence[float],
        components: tuple[array, ...],
    ) -> "NativeDenseBoneTrack":
        """Build a track from arrays already detached from sampler storage."""

        normalized_frames = tuple(float(frame) for frame in frames)
        detached_components = tuple(components)
        cls._validate_shape(normalized_frames, detached_components)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_frames", normalized_frames)
        object.__setattr__(instance, "_components", detached_components)
        return instance

    @property
    def frames(self) -> tuple[float, ...]:
        """Return the immutable requested frame sequence."""

        return self._frames

    @property
    def translate_x(self) -> array:
        return self.component("translateX")

    @property
    def translate_y(self) -> array:
        return self.component("translateY")

    @property
    def translate_z(self) -> array:
        return self.component("translateZ")

    @property
    def rotate_x(self) -> array:
        return self.component("rotateX")

    @property
    def rotate_y(self) -> array:
        return self.component("rotateY")

    @property
    def rotate_z(self) -> array:
        return self.component("rotateZ")

    def component(self, attr: str) -> array:
        """Return a detached component copy for one named bone channel."""

        try:
            component_index = _BONE_EXPORT_ATTRS.index(str(attr))
        except ValueError as exc:
            raise KeyError(attr) from exc
        return array("d", self._components[component_index])

    def _components_for_collector(self) -> tuple[array, ...]:
        """Return detached fixed-order arrays for the trusted collector path."""

        return self._components

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self):
        return iter(self._frames)


@dataclass(frozen=True)
class NativeDenseScalarTrack:
    """Detached SoA values for one logical scalar channel."""

    frames: tuple[float, ...]
    values: array

    def __post_init__(self) -> None:
        normalized_frames = tuple(float(frame) for frame in self.frames)
        normalized_values = array("d", self.values)
        if (
            not normalized_frames
            or len(normalized_frames) != len(normalized_values)
            or any(not math.isfinite(frame) for frame in normalized_frames)
            or any(
                right <= left
                for left, right in zip(normalized_frames, normalized_frames[1:])
            )
        ):
            raise NativeVmdBatchSamplerError("native scalar track has invalid shape")
        object.__setattr__(self, "frames", normalized_frames)
        object.__setattr__(self, "values", normalized_values)


class NativeDenseBoneSamples:
    """Validated frame-major samples backed by a read-only mmap spool."""

    def __init__(
        self,
        plan: DenseBoneSamplePlan,
        strategy_counts: Mapping[str, int],
        wall_sec: float,
        mapping: mmap.mmap,
        spool_file: Any,
        spool_path: str,
        storage_bytes: int,
        chunk_count: int = 1,
        max_frames_per_chunk: int = MAX_NATIVE_SAMPLES,
        max_samples_per_chunk: int = MAX_NATIVE_SAMPLES,
        chunk_wall_secs: Sequence[float] = (),
        chunk_set_current_time_wall_secs: Sequence[float] = (),
        chunk_first_timed_mplug_read_wall_secs: Sequence[float] = (),
        chunk_channel_loop_wall_secs: Sequence[float] = (),
        chunk_classified_compound_group_counts: Sequence[int] = (),
        chunk_classified_compound_covered_channel_counts: Sequence[int] = (),
        chunk_compound_success_group_counts: Sequence[int] = (),
        chunk_compound_success_covered_channel_counts: Sequence[int] = (),
        chunk_compound_fallback_group_counts: Sequence[int] = (),
        chunk_compound_fallback_covered_channel_counts: Sequence[int] = (),
    ) -> None:
        self.plan = plan
        self.strategy_counts = dict(strategy_counts)
        self.wall_sec = wall_sec
        self.chunk_count = chunk_count
        self.max_frames_per_chunk = max_frames_per_chunk
        self.max_samples_per_chunk = max_samples_per_chunk
        self.chunk_wall_secs = tuple(chunk_wall_secs)
        self.chunk_set_current_time_wall_secs = tuple(
            chunk_set_current_time_wall_secs
        )
        self.chunk_first_timed_mplug_read_wall_secs = tuple(
            chunk_first_timed_mplug_read_wall_secs
        )
        self.chunk_channel_loop_wall_secs = tuple(chunk_channel_loop_wall_secs)
        self.chunk_classified_compound_group_counts = tuple(
            int(count) for count in chunk_classified_compound_group_counts
        )
        self.chunk_classified_compound_covered_channel_counts = tuple(
            int(count) for count in chunk_classified_compound_covered_channel_counts
        )
        self.chunk_compound_success_group_counts = tuple(
            int(count) for count in chunk_compound_success_group_counts
        )
        self.chunk_compound_success_covered_channel_counts = tuple(
            int(count) for count in chunk_compound_success_covered_channel_counts
        )
        self.chunk_compound_fallback_group_counts = tuple(
            int(count) for count in chunk_compound_fallback_group_counts
        )
        self.chunk_compound_fallback_covered_channel_counts = tuple(
            int(count) for count in chunk_compound_fallback_covered_channel_counts
        )
        self._mapping = mapping
        self._spool_file = spool_file
        self._spool_path = spool_path
        self._storage_bytes = int(storage_bytes)
        self._python_scalar_unpack_count = 0
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed or self._mapping is None:
            raise RuntimeError("native sampler samples are closed")

    def _value_at(self, frame_index: int, physical_index: int) -> float:
        self._ensure_open()
        offset = (
            (frame_index * len(self.plan.physical_channels) + physical_index)
            * _DOUBLE_ITEM_SIZE
        )
        try:
            self._python_scalar_unpack_count += 1
            return float(struct.unpack_from("=d", self._mapping, offset)[0])
        except (TypeError, ValueError, struct.error) as exc:
            raise NativeVmdBatchSamplerError(
                "native sampler spool has an invalid value"
            ) from exc

    def value(self, joint: str, attr: str, frame: float) -> float:
        """Return one logical sample, including duplicate-plug aliases."""

        frame_value = float(frame)
        frame_index = self.plan._frame_indices.get(frame_value)
        physical_index = self.plan._logical_indices.get((str(joint), str(attr)))
        if frame_index is None or physical_index is None:
            raise KeyError((joint, attr, frame))
        return self._value_at(frame_index, physical_index)

    def bone_track(
        self,
        joint: str,
        frames: Optional[Sequence[float]] = None,
    ) -> NativeDenseBoneTrack:
        """Return one detached six-component SoA track for ``joint``.

        ``frames`` may be any strictly increasing subset of the sampled plan;
        omitting it returns the complete plan.  Physical channel aliases are
        resolved once, so duplicate logical routes read the same source value
        without exposing the mmap-backed storage.
        """

        self._ensure_open()
        joint_key = str(joint)
        if frames is None:
            requested_frames = self.plan.frames
        else:
            try:
                requested_frames = tuple(float(frame) for frame in frames)
            except (TypeError, ValueError, OverflowError) as exc:
                raise NativeVmdBatchSamplerError(
                    "native bone track frames are not numeric"
                ) from exc
        if (
            not requested_frames
            or any(not math.isfinite(frame) for frame in requested_frames)
            or any(
                right <= left
                for left, right in zip(requested_frames, requested_frames[1:])
            )
        ):
            raise NativeVmdBatchSamplerError(
                "native bone track frames must be strictly increasing"
            )
        frame_indices = []
        for frame in requested_frames:
            frame_index = self.plan._frame_indices.get(frame)
            if frame_index is None:
                raise KeyError((joint, frame))
            frame_indices.append(frame_index)
        physical_indices = []
        for attr in _BONE_EXPORT_ATTRS:
            physical_index = self.plan._logical_indices.get((joint_key, attr))
            if physical_index is None:
                raise KeyError((joint, attr))
            physical_indices.append(physical_index)

        physical_channel_count = len(self.plan.physical_channels)
        spool_values = None
        try:
            # The wire spool is frame-major.  A native double memoryview lets
            # us gather one requested channel with a strided copy, avoiding a
            # Python struct.unpack_from call for every scalar in the track.
            spool_values = memoryview(self._mapping).cast("d")
            full_components = tuple(
                array("d", spool_values[physical_index::physical_channel_count])
                for physical_index in physical_indices
            )
        except (BufferError, TypeError, ValueError, IndexError, struct.error) as exc:
            raise NativeVmdBatchSamplerError(
                "native sampler spool has an invalid value"
            ) from exc
        finally:
            if spool_values is not None:
                spool_values.release()

        if len(frame_indices) == len(self.plan.frames):
            components = full_components
        else:
            components = tuple(
                array("d", (values[index] for index in frame_indices))
                for values in full_components
            )
        return NativeDenseBoneTrack._from_detached(requested_frames, tuple(components))

    def scalar_track(
        self,
        logical_name: str,
        frames: Optional[Sequence[float]] = None,
    ) -> NativeDenseScalarTrack:
        """Return one detached scalar track without per-value Python unpacking."""

        self._ensure_open()
        if frames is None:
            requested_frames = self.plan.frames
        else:
            requested_frames = tuple(float(frame) for frame in frames)
        if (
            not requested_frames
            or any(not math.isfinite(frame) for frame in requested_frames)
            or any(
                right <= left
                for left, right in zip(requested_frames, requested_frames[1:])
            )
        ):
            raise NativeVmdBatchSamplerError(
                "native scalar track frames must be strictly increasing"
            )
        physical_index = self.plan._logical_indices.get((str(logical_name), "value"))
        if physical_index is None:
            raise KeyError(logical_name)
        frame_indices = []
        for frame in requested_frames:
            frame_index = self.plan._frame_indices.get(frame)
            if frame_index is None:
                raise KeyError((logical_name, frame))
            frame_indices.append(frame_index)

        spool_values = None
        try:
            physical_channel_count = len(self.plan.physical_channels)
            spool_values = memoryview(self._mapping).cast("d")
            full_values = array(
                "d", spool_values[physical_index::physical_channel_count]
            )
        except (BufferError, TypeError, ValueError, IndexError, struct.error) as exc:
            raise NativeVmdBatchSamplerError(
                "native sampler spool has an invalid scalar value"
            ) from exc
        finally:
            if spool_values is not None:
                spool_values.release()
        values = (
            full_values
            if len(frame_indices) == len(self.plan.frames)
            else array("d", (full_values[index] for index in frame_indices))
        )
        return NativeDenseScalarTrack(requested_frames, values)

    @property
    def sample_count(self) -> int:
        return len(self.plan.frames) * len(self.plan.physical_channels)

    @property
    def diagnostics(self) -> dict[str, Any]:
        classified_compound_group_count = (
            self.chunk_classified_compound_group_counts[0]
            if self.chunk_classified_compound_group_counts
            else 0
        )
        classified_compound_covered_channel_count = (
            self.chunk_classified_compound_covered_channel_counts[0]
            if self.chunk_classified_compound_covered_channel_counts
            else 0
        )
        compound_success_group_count = (
            self.chunk_compound_success_group_counts[0]
            if self.chunk_compound_success_group_counts
            else 0
        )
        compound_success_covered_channel_count = (
            self.chunk_compound_success_covered_channel_counts[0]
            if self.chunk_compound_success_covered_channel_counts
            else 0
        )
        compound_fallback_group_count = (
            self.chunk_compound_fallback_group_counts[0]
            if self.chunk_compound_fallback_group_counts
            else 0
        )
        compound_fallback_covered_channel_count = (
            self.chunk_compound_fallback_covered_channel_counts[0]
            if self.chunk_compound_fallback_covered_channel_counts
            else 0
        )
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
            "set_current_time_wall_sec": sum(self.chunk_set_current_time_wall_secs),
            "first_timed_mplug_read_wall_sec": sum(
                self.chunk_first_timed_mplug_read_wall_secs
            ),
            "channel_loop_wall_sec": sum(self.chunk_channel_loop_wall_secs),
            "classified_compound_group_count": classified_compound_group_count,
            "classified_compound_covered_channel_count": classified_compound_covered_channel_count,
            "compound_success_group_count": compound_success_group_count,
            "compound_success_covered_channel_count": compound_success_covered_channel_count,
            "compound_fallback_group_count": compound_fallback_group_count,
            "compound_fallback_covered_channel_count": compound_fallback_covered_channel_count,
            "chunk_set_current_time_wall_sec": list(
                self.chunk_set_current_time_wall_secs
            ),
            "chunk_first_timed_mplug_read_wall_sec": list(
                self.chunk_first_timed_mplug_read_wall_secs
            ),
            "chunk_channel_loop_wall_sec": list(self.chunk_channel_loop_wall_secs),
            "chunk_classified_compound_group_count": list(
                self.chunk_classified_compound_group_counts
            ),
            "chunk_classified_compound_covered_channel_count": list(
                self.chunk_classified_compound_covered_channel_counts
            ),
            "chunk_compound_success_group_count": list(
                self.chunk_compound_success_group_counts
            ),
            "chunk_compound_success_covered_channel_count": list(
                self.chunk_compound_success_covered_channel_counts
            ),
            "chunk_compound_fallback_group_count": list(
                self.chunk_compound_fallback_group_counts
            ),
            "chunk_compound_fallback_covered_channel_count": list(
                self.chunk_compound_fallback_covered_channel_counts
            ),
            "storage_backend": "read_only_mmap",
            "storage_bytes": self._storage_bytes,
            "storage_value_count": self.sample_count,
            "python_scalar_unpack_count": self._python_scalar_unpack_count,
        }

    def close(self) -> None:
        """Close the mmap and remove its private spool; safe to call repeatedly."""

        if self._closed:
            return
        self._closed = True
        mapping = self._mapping
        self._mapping = None
        if mapping is not None:
            try:
                mapping.close()
            except Exception:
                pass
        spool_file = self._spool_file
        self._spool_file = None
        if spool_file is not None:
            try:
                spool_file.close()
            except Exception:
                pass
        spool_path = self._spool_path
        self._spool_path = None
        if spool_path:
            try:
                os.unlink(spool_path)
            except FileNotFoundError:
                pass
            except OSError:
                # Cleanup is best effort for interpreter shutdown and must not
                # turn a completed export into a failure.
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


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


def _source_connections_or_none(
    cmds_module: Any, plug: str
) -> Optional[list[str]]:
    """Read source connections while distinguishing failure from no input."""

    if cmds_module is None:
        return None
    method = getattr(cmds_module, "listConnections", None)
    if not callable(method):
        return None
    try:
        values = method(plug, source=True, destination=False, plugs=True) or []
    except Exception:
        return None
    return [str(value) for value in values]


def _node_type(cmds_module: Any, node: str) -> str:
    method = getattr(cmds_module, "nodeType", None) if cmds_module is not None else None
    if not callable(method):
        return ""
    try:
        return str(method(node) or "")
    except Exception:
        return ""


def _static_physics_input_value(
    cmds_module: Any,
    channel: DenseBoneSampleChannel,
) -> Optional[float]:
    """Read a constant pre-physics input rejected by older native binaries.

    This is deliberately narrower than a sampler fallback: only a channel
    already classified as static, on an unconnected ``inPre*`` physics input,
    is handled here.  Every time-varying channel still goes through Maya's
    native Timeline evaluator.
    """

    if channel.hint != "static":
        return None
    node, separator, attr = channel.plug.rpartition(".")
    if not separator or not attr.startswith("inPre"):
        return None
    node_type = _node_type(cmds_module, node).lower()
    if not any(token in node_type for token in ("physics", "rigidbody", "rigid_body")):
        return None
    get_attr = getattr(cmds_module, "getAttr", None) if cmds_module is not None else None
    if not callable(get_attr):
        raise NativeVmdBatchSamplerError(
            f"static pre-physics input could not be read: {channel.plug}"
        )
    try:
        value = float(get_attr(channel.plug))
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeVmdBatchSamplerError(
            f"static pre-physics input is not numeric: {channel.plug}"
        ) from exc
    if not math.isfinite(value):
        raise NativeVmdBatchSamplerError(
            f"static pre-physics input is not finite: {channel.plug}"
        )
    return value


def _native_request_plan(
    plan: DenseBoneSamplePlan,
    cmds_module: Any,
) -> tuple[DenseBoneSamplePlan, dict[int, int], dict[int, float]]:
    """Split constant physics inputs from channels requiring native sampling."""

    request_channels = []
    request_index_by_physical = {}
    static_values = {}
    for physical_index, channel in enumerate(plan.physical_channels):
        static_value = _static_physics_input_value(cmds_module, channel)
        if static_value is not None:
            static_values[physical_index] = static_value
            continue
        request_index = len(request_channels)
        request_index_by_physical[physical_index] = request_index
        request_channels.append(
            DenseBoneSampleChannel(
                joint=channel.joint,
                attr=channel.attr,
                plug=channel.plug,
                unit=channel.unit,
                hint=channel.hint,
                physical_index=request_index,
            )
        )
    return (
        DenseBoneSamplePlan(
            frames=plan.frames,
            physical_channels=tuple(request_channels),
            logical_channels=(),
        ),
        request_index_by_physical,
        static_values,
    )


def _has_parent_incoming(cmds_module: Any, node: str, attr: str) -> bool:
    """Conservatively reject compound/array routes for direct/static hints."""

    if "." in attr:
        return True
    if "[" in attr:
        # A numeric array element such as inputWeight[0] is an independent
        # scalar plug.  Only a connection on the array parent makes its
        # static classification ambiguous; the C++ side revalidates the exact
        # element before trusting the hint.
        parent_name = attr.split("[", 1)[0]
        return bool(_connections(cmds_module, f"{node}.{parent_name}"))
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
    incoming = _source_connections_or_none(cmds_module, plug)
    if incoming is None or len(incoming) != 1:
        return False
    source_node, separator, source_attr = incoming[0].rpartition(".")
    if not separator or source_attr != "output":
        return False
    if _node_type(cmds_module, source_node) not in _DIRECT_ANIM_CURVE_TYPES:
        return False

    # MFnAnimCurve.evaluate() receives a time directly and therefore bypasses
    # Maya's DG time graph.  A curve whose input is driven by anything other
    # than the canonical global time node can contain a time remap (for
    # example an animCurveTT Bezier curve).  Keep those routes on the timed
    # MPlug path so the requested Timeline frame is evaluated by Maya.
    curve_input = _source_connections_or_none(cmds_module, f"{source_node}.input")
    if curve_input is None:
        return False
    if not curve_input:
        return True
    if len(curve_input) != 1:
        return False
    time_node, time_separator, time_attr = curve_input[0].rpartition(".")
    return (
        time_separator
        and time_attr == "outTime"
        and _node_type(cmds_module, time_node) == "time"
    )


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


def build_dense_scalar_sample_plan(
    channels: Sequence[Sequence[str]],
    frames: Sequence[float],
    cmds_module: Any = None,
) -> DenseBoneSamplePlan:
    """Build a native plan for named scalar plugs such as Morph weights.

    Each channel is ``(logical_name, node, attribute)``.  Logical names must
    be unique; physical plugs are deduplicated exactly as in the Bone plan.
    """

    normalized_frames = tuple(float(frame) for frame in frames)
    if not normalized_frames or any(
        not math.isfinite(frame) for frame in normalized_frames
    ):
        raise NativeVmdBatchSamplerError("dense scalar sampler requires finite frames")
    if any(
        right <= left
        for left, right in zip(normalized_frames, normalized_frames[1:])
    ):
        raise NativeVmdBatchSamplerError(
            "dense scalar sampler frames must be strictly increasing"
        )
    physical: list[DenseBoneSampleChannel] = []
    logical: list[DenseBoneSampleChannel] = []
    physical_by_plug: dict[str, int] = {}
    logical_names: set[str] = set()
    for channel in channels:
        try:
            logical_name, node, attr = channel
        except (TypeError, ValueError) as exc:
            raise NativeVmdBatchSamplerError(
                "scalar channel requires logical name, node, and attribute"
            ) from exc
        logical_name = str(logical_name)
        if not logical_name or logical_name in logical_names:
            raise NativeVmdBatchSamplerError(
                f"scalar channel logical name is empty or duplicated: {logical_name!r}"
            )
        logical_names.add(logical_name)
        node = _canonical_node(node, cmds_module)
        attr = str(attr)
        plug = f"{node}.{attr}"
        physical_index = physical_by_plug.get(plug)
        if physical_index is None:
            physical_index = len(physical)
            physical_by_plug[plug] = physical_index
            physical.append(
                DenseBoneSampleChannel(
                    joint=logical_name,
                    attr="value",
                    plug=plug,
                    unit="scalar",
                    hint=_hint_for_plug(cmds_module, node, attr, plug),
                    physical_index=physical_index,
                )
            )
        logical.append(
            DenseBoneSampleChannel(
                joint=logical_name,
                attr="value",
                plug=plug,
                unit="scalar",
                hint=physical[physical_index].hint,
                physical_index=physical_index,
            )
        )
    if not physical:
        raise NativeVmdBatchSamplerError("dense scalar sampler requires channels")
    return DenseBoneSamplePlan(
        frames=normalized_frames,
        physical_channels=tuple(physical),
        logical_channels=tuple(logical),
    )


def _header_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise NativeVmdBatchSamplerError(f"native sampler header {name} is not numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeVmdBatchSamplerError(f"native sampler header {name} is not numeric") from exc
    if not math.isfinite(numeric) or numeric != int(numeric):
        raise NativeVmdBatchSamplerError(f"native sampler header {name} is not an exact integer")
    return int(numeric)


def _header_nonnegative_int(value: Any, name: str) -> int:
    numeric = _header_int(value, name)
    if numeric < 0:
        raise NativeVmdBatchSamplerError(
            f"native sampler header {name} must be a non-negative exact integer"
        )
    return numeric


def _header_seconds(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise NativeVmdBatchSamplerError(f"native sampler header {name} is not numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeVmdBatchSamplerError(
            f"native sampler header {name} is not numeric"
        ) from exc
    if not math.isfinite(numeric) or numeric < 0.0:
        raise NativeVmdBatchSamplerError(
            f"native sampler header {name} must be finite and non-negative"
        )
    return numeric


def _unlink_spool(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _close_partial_spool(
    mapping: Optional[mmap.mmap], spool_file: Any, spool_path: Optional[str]
) -> None:
    """Release spool resources in Windows-safe mmap -> file -> unlink order."""

    if mapping is not None:
        try:
            mapping.close()
        except Exception:
            pass
    if spool_file is not None:
        try:
            spool_file.close()
        except Exception:
            pass
    _unlink_spool(spool_path)


def _parse_direct_spool_result(
    ack: Sequence[Any],
    *,
    frame_count: int,
    output_channel_count: int,
    native_channel_count: int,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Validate the direct-spool acknowledgement and its diagnostics."""

    try:
        values = list(ack)
    except TypeError as exc:
        raise NativeVmdBatchSamplerError(
            "native direct spool result is not iterable"
        ) from exc
    direct_ack_offset = _HEADER_SIZE + _TIMING_HEADER_SIZE
    if len(values) < direct_ack_offset + 2:
        raise NativeVmdBatchSamplerError(
            "native direct spool result has an invalid header size"
        )
    version = _header_int(values[0], "version")
    actual_frames = _header_int(values[1], "frame_count")
    actual_outputs = _header_int(values[2], "channel_count")
    direct_count = _header_int(values[3], "direct_count")
    static_count = _header_int(values[4], "static_count")
    timed_count = _header_int(values[5], "timed_count")
    if version != _PROTOCOL_VERSION or actual_frames != frame_count:
        raise NativeVmdBatchSamplerError("native direct spool identity mismatch")
    if actual_outputs != output_channel_count or direct_count + static_count + timed_count != native_channel_count:
        raise NativeVmdBatchSamplerError("native direct spool strategy shape mismatch")
    timing = {
        "set_current_time_wall_sec": _header_seconds(
            values[6], "set_current_time_wall_sec"
        ),
        "first_timed_mplug_read_wall_sec": _header_seconds(
            values[7], "first_timed_mplug_read_wall_sec"
        ),
        "channel_loop_wall_sec": _header_seconds(values[8], "channel_loop_wall_sec"),
    }
    header_runtime_shape = tuple(
        _header_nonnegative_int(values[index], name)
        for index, name in zip(
            range(9, 15),
            (
                "classified_compound_group_count",
                "classified_compound_covered_channel_count",
                "compound_success_group_count",
                "compound_success_covered_channel_count",
                "compound_fallback_group_count",
                "compound_fallback_covered_channel_count",
            ),
        )
    )
    ack_version = _header_int(values[15], "direct_ack_version")
    checkpoint_count = _header_int(values[16], "checkpoint_count")
    expected_checkpoint_count = (
        frame_count + MAX_NATIVE_FRAMES - 1
    ) // MAX_NATIVE_FRAMES
    if ack_version != _DIRECT_ACK_VERSION or checkpoint_count != expected_checkpoint_count:
        raise NativeVmdBatchSamplerError("native direct spool checkpoint identity mismatch")
    expected_length = direct_ack_offset + 2 + checkpoint_count * _DIRECT_CHECKPOINT_RECORD_SIZE
    if len(values) != expected_length:
        raise NativeVmdBatchSamplerError("native direct spool result has an invalid checkpoint size")
    checkpoint_set_current_time_wall_sec = []
    checkpoint_first_timed_mplug_read_wall_sec = []
    checkpoint_channel_loop_wall_sec = []
    checkpoint_wall_sec = []
    checkpoint_classified_group_count = []
    checkpoint_classified_covered_count = []
    checkpoint_success_group_count = []
    checkpoint_success_covered_count = []
    checkpoint_fallback_group_count = []
    checkpoint_fallback_covered_count = []
    runtime_shape = None
    offset = direct_ack_offset + 2
    for _checkpoint_index in range(checkpoint_count):
        checkpoint_set_current_time_wall_sec.append(
            _header_seconds(values[offset], "checkpoint_set_current_time_wall_sec")
        )
        checkpoint_first_timed_mplug_read_wall_sec.append(
            _header_seconds(values[offset + 1], "checkpoint_first_timed_mplug_read_wall_sec")
        )
        checkpoint_channel_loop_wall_sec.append(
            _header_seconds(values[offset + 2], "checkpoint_channel_loop_wall_sec")
        )
        checkpoint_wall_sec.append(
            _header_seconds(values[offset + 9], "checkpoint_wall_sec")
        )
        group_count = _header_nonnegative_int(
            values[offset + 3], "classified_compound_group_count"
        )
        covered_count = _header_nonnegative_int(
            values[offset + 4], "classified_compound_covered_channel_count"
        )
        success_groups = _header_nonnegative_int(
            values[offset + 5], "compound_success_group_count"
        )
        success_covered = _header_nonnegative_int(
            values[offset + 6], "compound_success_covered_channel_count"
        )
        fallback_groups = _header_nonnegative_int(
            values[offset + 7], "compound_fallback_group_count"
        )
        fallback_covered = _header_nonnegative_int(
            values[offset + 8], "compound_fallback_covered_channel_count"
        )
        shape = (
            group_count,
            covered_count,
            success_groups,
            success_covered,
            fallback_groups,
            fallback_covered,
        )
        if (
            covered_count != group_count * 3
            or success_covered != success_groups * 3
            or fallback_covered != fallback_groups * 3
            or success_groups + fallback_groups != group_count
            or success_covered + fallback_covered != covered_count
        ):
            raise NativeVmdBatchSamplerError(
                "native direct spool compound diagnostics are inconsistent"
            )
        if runtime_shape is None:
            runtime_shape = shape
        elif runtime_shape != shape:
            raise NativeVmdBatchSamplerError(
                "native direct spool compound diagnostics differ between checkpoints"
            )
        checkpoint_classified_group_count.append(group_count)
        checkpoint_classified_covered_count.append(covered_count)
        checkpoint_success_group_count.append(success_groups)
        checkpoint_success_covered_count.append(success_covered)
        checkpoint_fallback_group_count.append(fallback_groups)
        checkpoint_fallback_covered_count.append(fallback_covered)
        offset += _DIRECT_CHECKPOINT_RECORD_SIZE
    if runtime_shape is None:
        raise NativeVmdBatchSamplerError("native direct spool has no checkpoint diagnostics")
    group_count, covered_count, success_groups, success_covered, fallback_groups, fallback_covered = runtime_shape
    if runtime_shape != header_runtime_shape:
        raise NativeVmdBatchSamplerError(
            "native direct spool header diagnostics differ from checkpoints"
        )
    for key, checkpoint_values in (
        ("set_current_time_wall_sec", checkpoint_set_current_time_wall_sec),
        ("first_timed_mplug_read_wall_sec", checkpoint_first_timed_mplug_read_wall_sec),
        ("channel_loop_wall_sec", checkpoint_channel_loop_wall_sec),
    ):
        if not math.isclose(
            timing[key], sum(checkpoint_values), rel_tol=1.0e-9, abs_tol=1.0e-9
        ):
            raise NativeVmdBatchSamplerError(
                f"native direct spool {key} differs from checkpoint totals"
            )
    timing.update(
        {
            "classified_compound_group_count": group_count,
            "classified_compound_covered_channel_count": covered_count,
            "compound_success_group_count": success_groups,
            "compound_success_covered_channel_count": success_covered,
            "compound_fallback_group_count": fallback_groups,
            "compound_fallback_covered_channel_count": fallback_covered,
            "chunk_count": checkpoint_count,
            "chunk_set_current_time_wall_sec": checkpoint_set_current_time_wall_sec,
            "chunk_first_timed_mplug_read_wall_sec": checkpoint_first_timed_mplug_read_wall_sec,
            "chunk_channel_loop_wall_sec": checkpoint_channel_loop_wall_sec,
            "chunk_wall_sec": checkpoint_wall_sec,
            "chunk_classified_compound_group_count": checkpoint_classified_group_count,
            "chunk_classified_compound_covered_channel_count": checkpoint_classified_covered_count,
            "chunk_compound_success_group_count": checkpoint_success_group_count,
            "chunk_compound_success_covered_channel_count": checkpoint_success_covered_count,
            "chunk_compound_fallback_group_count": checkpoint_fallback_group_count,
            "chunk_compound_fallback_covered_channel_count": checkpoint_fallback_covered_count,
        }
    )
    return (
        {
            "direct_curve": direct_count,
            "static": static_count,
            "timed_mplug": timed_count,
        },
        timing,
    )


def _sample_direct_spool(
    command: Any,
    plan: DenseBoneSamplePlan,
    request_plan: DenseBoneSamplePlan,
    request_index_by_physical: Mapping[int, int],
    static_physics_values: Mapping[int, float],
) -> tuple[NativeDenseBoneSamples, dict[str, Any]]:
    """Run one Prepare-scoped native plan and let C++ write the full spool."""

    output_channel_count = len(plan.physical_channels)
    native_channel_count = len(request_plan.physical_channels)
    expected_bytes = len(plan.frames) * output_channel_count * _DOUBLE_ITEM_SIZE
    if expected_bytes <= 0:
        raise NativeVmdBatchSamplerError("native direct spool has an invalid size")
    output_slots = [
        physical_index
        for physical_index in range(output_channel_count)
        if physical_index not in static_physics_values
    ]
    if len(output_slots) != native_channel_count or set(output_slots) != set(request_index_by_physical):
        raise NativeVmdBatchSamplerError("native direct spool output mapping is inconsistent")
    output_defaults = [
        float(static_physics_values.get(physical_index, 0.0))
        for physical_index in range(output_channel_count)
    ]
    spool_fd, spool_path = tempfile.mkstemp(prefix="mmd_bake_timeline_direct_", suffix=".bin")
    spool_file = None
    mapping = None
    started = time.perf_counter()
    try:
        os.ftruncate(spool_fd, expected_bytes)
        os.close(spool_fd)
        spool_fd = -1
        payload = json.dumps(
            {
                "version": _PROTOCOL_VERSION,
                "evaluation_policy": EVALUATION_POLICY,
                "timing": _TIMING_PROTOCOL,
                "mode": _DIRECT_SPOOL_MODE,
                "frames": list(plan.frames),
                "channels": list(request_plan.request_channels),
                "spool_path": spool_path,
                "spool_bytes": expected_bytes,
                "output_channel_count": output_channel_count,
                "output_slots": output_slots,
                "output_defaults": output_defaults,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ack = command(payload=payload)
        strategy_counts, timing = _parse_direct_spool_result(
            ack,
            frame_count=len(plan.frames),
            output_channel_count=output_channel_count,
            native_channel_count=native_channel_count,
        )
        strategy_counts["static"] += len(static_physics_values)
        spool_file = open(spool_path, "rb")
        mapping = mmap.mmap(spool_file.fileno(), expected_bytes, access=mmap.ACCESS_READ)
        chunk_count = timing["chunk_count"]
        diagnostics = {
            "available": True,
            "used": True,
            "mode": _DIRECT_SPOOL_MODE,
            "chunk_count": chunk_count,
            "max_frames_per_chunk": MAX_NATIVE_FRAMES,
            "max_samples_per_chunk": MAX_NATIVE_FRAMES * output_channel_count,
            "wall_sec": round(time.perf_counter() - started, 6),
            **timing,
            "chunk_wall_sec": timing["chunk_wall_sec"],
            "chunk_set_current_time_wall_sec": timing["chunk_set_current_time_wall_sec"],
            "chunk_first_timed_mplug_read_wall_sec": timing[
                "chunk_first_timed_mplug_read_wall_sec"
            ],
            "chunk_channel_loop_wall_sec": timing["chunk_channel_loop_wall_sec"],
            "chunk_classified_compound_group_count": timing[
                "chunk_classified_compound_group_count"
            ],
            "chunk_classified_compound_covered_channel_count": timing[
                "chunk_classified_compound_covered_channel_count"
            ],
            "chunk_compound_success_group_count": timing[
                "chunk_compound_success_group_count"
            ],
            "chunk_compound_success_covered_channel_count": timing[
                "chunk_compound_success_covered_channel_count"
            ],
            "chunk_compound_fallback_group_count": timing[
                "chunk_compound_fallback_group_count"
            ],
            "chunk_compound_fallback_covered_channel_count": timing[
                "chunk_compound_fallback_covered_channel_count"
            ],
        }
        result = NativeDenseBoneSamples(
            plan=plan,
            strategy_counts=strategy_counts,
            wall_sec=diagnostics["wall_sec"],
            mapping=mapping,
            spool_file=spool_file,
            spool_path=spool_path,
            storage_bytes=expected_bytes,
            chunk_count=chunk_count,
            max_frames_per_chunk=MAX_NATIVE_FRAMES,
            max_samples_per_chunk=MAX_NATIVE_FRAMES * output_channel_count,
            chunk_wall_secs=diagnostics["chunk_wall_sec"],
            chunk_set_current_time_wall_secs=diagnostics[
                "chunk_set_current_time_wall_sec"
            ],
            chunk_first_timed_mplug_read_wall_secs=diagnostics[
                "chunk_first_timed_mplug_read_wall_sec"
            ],
            chunk_channel_loop_wall_secs=diagnostics["chunk_channel_loop_wall_sec"],
            chunk_classified_compound_group_counts=diagnostics[
                "chunk_classified_compound_group_count"
            ],
            chunk_classified_compound_covered_channel_counts=diagnostics[
                "chunk_classified_compound_covered_channel_count"
            ],
            chunk_compound_success_group_counts=diagnostics[
                "chunk_compound_success_group_count"
            ],
            chunk_compound_success_covered_channel_counts=diagnostics[
                "chunk_compound_success_covered_channel_count"
            ],
            chunk_compound_fallback_group_counts=diagnostics[
                "chunk_compound_fallback_group_count"
            ],
            chunk_compound_fallback_covered_channel_counts=diagnostics[
                "chunk_compound_fallback_covered_channel_count"
            ],
        )
        mapping = None
        spool_file = None
        spool_path = None
        return result, diagnostics
    except BaseException as exc:
        if spool_fd >= 0:
            try:
                os.close(spool_fd)
            except OSError:
                pass
        _close_partial_spool(mapping, spool_file, spool_path)
        if _is_direct_spool_unsupported_error(exc):
            raise NativeDirectSpoolUnsupportedError(
                "loaded native sampler does not support direct spool mode"
            ) from exc
        raise


class NativeVmdBatchSampler:
    """Invoke ``mmdVmdBatchSample`` under the explicit Timeline policy."""

    command_name = "mmdVmdBatchSample"

    def __init__(
        self,
        cmds_module: Any,
        diagnostics_sink=None,
    ) -> None:
        self._cmds = cmds_module
        self._diagnostics_sink = diagnostics_sink
        self._plugin_attempted = False
        self._plugin_path: Optional[str] = None
        self.last_diagnostics: dict[str, Any] = {
            "available": callable(getattr(cmds_module, self.command_name, None)),
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
        request_plan, request_index_by_physical, static_physics_values = (
            _native_request_plan(plan, self._cmds)
        )
        command = getattr(self._cmds, self.command_name)
        try:
            result, direct_diagnostics = _sample_direct_spool(
                command,
                plan,
                request_plan,
                request_index_by_physical,
                static_physics_values,
            )
        except NativeDirectSpoolUnsupportedError as exc:
            raise NativeVmdBatchSamplerError(
                "native bone sampling requires a direct-spool capable plug-in; "
                "rebuild it and restart Maya"
            ) from exc
        self.last_diagnostics = {
            **plugin_diagnostics,
            **direct_diagnostics,
            "status": "completed",
            "protocol_failure": False,
            "python_static_physics_compat_count": len(static_physics_values),
        }
        self._publish_diagnostics()
        return result

    def sample_dense_scalar_channels(
        self,
        frames: Sequence[float],
        channels: Sequence[Sequence[str]],
    ) -> NativeDenseBoneSamples:
        """Sample named scalar plugs through the C++ direct-spool path.

        This is the production Bake Timeline Morph boundary.  It never falls back to
        Python Timeline evaluation; an unavailable or stale native plug-in is
        a fatal, actionable export error.
        """

        if not self.available:
            raise NativeVmdBatchSamplerError("native command is unavailable")
        plan = build_dense_scalar_sample_plan(channels, frames, self._cmds)
        request_plan, request_index_by_physical, static_values = (
            _native_request_plan(plan, self._cmds)
        )
        command = getattr(self._cmds, self.command_name)
        try:
            result, diagnostics = _sample_direct_spool(
                command,
                plan,
                request_plan,
                request_index_by_physical,
                static_values,
            )
        except NativeDirectSpoolUnsupportedError as exc:
            raise NativeVmdBatchSamplerError(
                "native Morph sampling requires a direct-spool capable plug-in; "
                "rebuild it and restart Maya"
            ) from exc
        self.last_diagnostics = {
            **{
                key: value
                for key, value in self.last_diagnostics.items()
                if key.startswith("plugin_") or key == "plugin_path"
            },
            **diagnostics,
            "status": "completed",
            "protocol_failure": False,
            "sample_kind": "scalar",
        }
        self._publish_diagnostics()
        return result

__all__ = [
    "DenseBoneSampleChannel",
    "DenseBoneSamplePlan",
    "NativeDenseBoneTrack",
    "NativeDenseScalarTrack",
    "NativeDenseBoneSamples",
    "NativeVmdBatchSampler",
    "NativeVmdBatchSamplerError",
    "NativeDirectSpoolUnsupportedError",
    "EVALUATION_POLICY",
    "MAX_NATIVE_SAMPLES",
    "MAX_NATIVE_FRAMES",
    "build_dense_bone_sample_plan",
    "build_dense_scalar_sample_plan",
]
