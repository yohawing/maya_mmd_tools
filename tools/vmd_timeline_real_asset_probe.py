"""Run isolated real-asset VMD production Timeline sampling probes.

The controller launches one fresh mayapy process for every prefix.  Only the
ASCII config path and numeric selectors cross argv;
PMX/VMD paths are decoded from the UTF-8 JSON config inside each worker.
This is a sampling probe, not an export runner: it imports through production
Actions, reuses production route discovery and sample-plan construction, and
samples through ``NativeVmdBatchSampler`` under its production Timeline policy.
It never writes a VMD or reuses raw VMD transforms.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCHEMA_VERSION = 1
PREFIX_FRAMES = (120, 300, 600)
DEFAULT_FULL_FRAME_COUNT = 6786
THIRD_ORACLE_FRAMES = (0.0, 100.0, 110.0, 119.0)
THIRD_ORACLE_MAX_ERRORS = 20
THIRD_ORACLE_TOLERANCE = {
    "angle": 1.0e-7,
    "distance": 1.0e-7,
    "scalar": 1.0e-9,
}
PHYSICS_NODE_TYPES = frozenset(
    {
        "mmdPhysicsSolver",
        "mmdPhysicsBoneDriver",
        "mmdPhysicsWorldShape",
        "mmdRigidBodyShape",
        "mmdPhysicsJointShape",
    }
)


class ProbeConfigurationError(ValueError):
    """Raised before Maya starts when the local probe config is invalid."""


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--prefix", type=int, choices=PREFIX_FRAMES)
    return parser.parse_args(argv)


def load_config(path: Path, *, require_assets: bool = True) -> dict[str, Any]:
    """Read and strictly normalize the UTF-8 real-asset probe config."""

    config_path = Path(path)
    if not str(config_path).isascii():
        raise ProbeConfigurationError("config path must be ASCII-safe for mayapy argv")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeConfigurationError(f"could not read UTF-8 config: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProbeConfigurationError("config root must be an object")
    allowed = {
        "schema_version",
        "pmx_path",
        "vmd_path",
        "prefix_frames",
        "out_dir",
        "full_frame_count",
        "mayapy",
        "worker_timeout_sec",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ProbeConfigurationError(f"unknown config fields: {', '.join(unknown)}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ProbeConfigurationError(f"schema_version must be {SCHEMA_VERSION}")
    try:
        prefixes = tuple(int(value) for value in raw["prefix_frames"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("prefix_frames must contain 120, 300, and 600") from exc
    if prefixes != PREFIX_FRAMES:
        raise ProbeConfigurationError("prefix_frames must be exactly [120, 300, 600]")
    try:
        full_frame_count = int(raw.get("full_frame_count", DEFAULT_FULL_FRAME_COUNT))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("full_frame_count must be an integer") from exc
    if full_frame_count <= max(PREFIX_FRAMES):
        raise ProbeConfigurationError("full_frame_count must exceed the largest prefix")
    try:
        pmx_path = Path(str(raw["pmx_path"])).expanduser()
        vmd_path = Path(str(raw["vmd_path"])).expanduser()
        out_dir = Path(str(raw["out_dir"])).expanduser()
    except KeyError as exc:
        raise ProbeConfigurationError(f"missing config field: {exc.args[0]}") from exc
    if pmx_path.suffix.casefold() not in {".pmx", ".pmd"}:
        raise ProbeConfigurationError("pmx_path must name a PMX or PMD file")
    if vmd_path.suffix.casefold() != ".vmd":
        raise ProbeConfigurationError("vmd_path must name a VMD file")
    if require_assets:
        for label, asset in (("pmx_path", pmx_path), ("vmd_path", vmd_path)):
            if not asset.is_file():
                raise ProbeConfigurationError(f"{label} does not exist: {asset}")
    mayapy = raw.get("mayapy")
    if mayapy is not None and not str(mayapy).strip():
        raise ProbeConfigurationError("mayapy must not be empty")
    try:
        worker_timeout_sec = float(raw.get("worker_timeout_sec", 900.0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProbeConfigurationError("worker_timeout_sec must be numeric") from exc
    if not math.isfinite(worker_timeout_sec) or worker_timeout_sec <= 0.0:
        raise ProbeConfigurationError("worker_timeout_sec must be positive and finite")
    return {
        "schema_version": SCHEMA_VERSION,
        "pmx_path": pmx_path,
        "vmd_path": vmd_path,
        "prefix_frames": prefixes,
        "out_dir": out_dir,
        "full_frame_count": full_frame_count,
        "mayapy": str(mayapy) if mayapy is not None else None,
        "worker_timeout_sec": worker_timeout_sec,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _result_stem(prefix: int) -> str:
    return f"production-{int(prefix):04d}"


def _result_path(config: Mapping[str, Any], prefix: int) -> Path:
    return Path(config["out_dir"]) / f"{_result_stem(prefix)}.json"


def _values_path(config: Mapping[str, Any], prefix: int) -> Path:
    return Path(config["out_dir"]) / f"{_result_stem(prefix)}.values.bin"


def _value_bytes(values: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(values)}d", *(float(value) for value in values))


def _canonical_root(value: Any, cmds_module: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"ImportModelAction returned an invalid root: {value!r}")
    matches = cmds_module.ls(value, long=True) or []
    if isinstance(matches, (str, bytes)) or len(matches) != 1:
        raise RuntimeError(f"imported root is not unique: {value!r} -> {matches!r}")
    root = str(matches[0])
    if not root.startswith("|"):
        raise RuntimeError(f"imported root is not a canonical DAG path: {root!r}")
    return root


def _import_options() -> dict[str, Any]:
    return {
        "scale": 1.0,
        "import_physics": True,
        "setup_rig": True,
        "setup_bone_orientation": True,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
    }


def _require_action_success(result: Any, name: str, *, require_root: bool) -> Any:
    warnings = list(getattr(result, "warnings", ()) or ())
    outcome = str(getattr(result, "outcome", "") or "").casefold()
    if not getattr(result, "succeeded", False) or outcome not in {"", "success"} or warnings:
        raise RuntimeError(
            f"{name} failed or was partial: error={getattr(result, 'error', None)!r} "
            f"outcome={outcome!r} warnings={warnings!r}"
        )
    root = getattr(result, "root_node", None)
    if require_root and not root:
        raise RuntimeError(f"{name} returned no model root")
    return root


def _import_assets(config: Mapping[str, Any], cmds_module: Any) -> str:
    from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest
    from mmd_tools.actions.import_vmd_action import ImportVmdAction, ImportVmdRequest

    model_result = ImportModelAction().execute(
        ImportModelRequest(
            file_path=str(config["pmx_path"]),
            options={**_import_options(), "profile": {}},
            create_new_scene=True,
        )
    )
    root = _canonical_root(
        _require_action_success(model_result, "ImportModelAction", require_root=True),
        cmds_module,
    )
    motion_result = ImportVmdAction().execute(
        ImportVmdRequest(
            file_path=str(config["vmd_path"]),
            options={
                **_import_options(),
                "target_model": root,
                "pmx_path": str(config["pmx_path"]),
                "bake_mode": False,
            },
            create_new_scene=False,
        )
    )
    _require_action_success(motion_result, "ImportVmdAction", require_root=False)
    return root


def _upstream_physics_nodes(cmds_module: Any, plugs: Sequence[str]) -> list[dict[str, str]]:
    """Return a bounded unique inventory of physics nodes upstream of channels."""

    queue: deque[str] = deque()
    for plug in plugs:
        queue.extend(
            str(value).split(".", 1)[0]
            for value in (
                cmds_module.listConnections(
                    plug,
                    source=True,
                    destination=False,
                    plugs=True,
                )
                or []
            )
        )
    visited: set[str] = set()
    physics: dict[str, str] = {}
    while queue and len(visited) < 4096:
        node = str(queue.popleft())
        matches = cmds_module.ls(node, long=True) or [node]
        canonical = str(matches[0])
        if canonical in visited:
            continue
        visited.add(canonical)
        node_type = str(cmds_module.nodeType(canonical) or "")
        if node_type in PHYSICS_NODE_TYPES:
            physics[canonical] = node_type
        queue.extend(
            str(value)
            for value in (
                cmds_module.listConnections(
                    canonical,
                    source=True,
                    destination=False,
                )
                or []
            )
        )
    return [
        {"node": node, "node_type": node_type}
        for node, node_type in sorted(physics.items())
    ]


def _route_inventory(plan: Any, routes: Mapping[str, Mapping[str, Sequence[str]]], cmds_module: Any) -> dict[str, Any]:
    rows = []
    node_types: Counter[str] = Counter()
    hint_counts: Counter[str] = Counter()
    routed_count = 0
    for channel in plan.logical_channels:
        node = channel.plug.rsplit(".", 1)[0]
        node_type = str(cmds_module.nodeType(node) or "")
        route = routes.get(channel.joint) or {}
        routed = channel.attr in route
        routed_count += int(routed)
        node_types[node_type] += 1
        hint_counts[channel.hint] += 1
        rows.append(
            {
                "joint": channel.joint,
                "attr": channel.attr,
                "plug": channel.plug,
                "physical_index": channel.physical_index,
                "hint": channel.hint,
                "unit": channel.unit,
                "node_type": node_type,
                "routed": routed,
                "physical_node_is_physics": node_type in PHYSICS_NODE_TYPES,
            }
        )
    physics_nodes = _upstream_physics_nodes(
        cmds_module,
        [channel.plug for channel in plan.physical_channels],
    )
    return {
        "logical_channel_count": len(plan.logical_channels),
        "physical_channel_count": len(plan.physical_channels),
        "routed_logical_channel_count": routed_count,
        "hint_counts": dict(sorted(hint_counts.items())),
        "physical_node_type_counts": dict(sorted(node_types.items())),
        "physics_upstream_nodes": physics_nodes,
        "channels": rows,
    }


def _finite_matrix(values: Any) -> list[float] | None:
    """Normalize one Maya matrix and reject malformed/non-finite values."""

    if isinstance(values, (list, tuple)) and len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = values[0]
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(result) != 16 or any(not math.isfinite(value) for value in result):
        return None
    return result


def _matrix_product(left: Sequence[float], right: Sequence[float]) -> list[float]:
    """Multiply two Maya row-major 4x4 matrices."""

    return [
        sum(float(left[row * 4 + index]) * float(right[index * 4 + column]) for index in range(4))
        for row in range(4)
        for column in range(4)
    ]


def _canonical_node_path(node: Any, cmds_module: Any) -> str:
    matches = cmds_module.ls(str(node), long=True) or []
    if isinstance(matches, (str, bytes)) or len(matches) != 1:
        return str(node)
    return str(matches[0])


def _joint_bone_index(joint: str, cmds_module: Any) -> int:
    try:
        value = cmds_module.getAttr(f"{joint}.mmd_bone_index")
        return int(value)
    except (RuntimeError, TypeError, ValueError, OverflowError):
        return 2**31 - 1


def _is_finger_joint(joint: str, cmds_module: Any) -> bool:
    """Use stable English PMX aliases so Japanese scene encoding is irrelevant."""

    leaf = str(joint).rsplit("|", 1)[-1].rsplit(":", 1)[-1].casefold()
    return any(token in leaf for token in ("finger", "thumb", "index", "middle", "ring", "pinky"))


def _skin_bind_for_joint(joint: str, root: str, cmds_module: Any) -> list[float] | None:
    """Find the first model-owned skin bind-pre matrix for a joint."""

    meshes = cmds_module.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for mesh in meshes:
        for skin in cmds_module.listHistory(mesh, pruneDagObjects=True) or ():
            if cmds_module.nodeType(skin) != "skinCluster":
                continue
            influences = cmds_module.skinCluster(skin, query=True, influence=True) or []
            canonical_influences = {_canonical_node_path(value, cmds_module): index for index, value in enumerate(influences)}
            index = canonical_influences.get(_canonical_node_path(joint, cmds_module))
            if index is None:
                continue
            try:
                raw = cmds_module.getAttr(f"{skin}.bindPreMatrix[{index}]")
            except RuntimeError:
                continue
            matrix = _finite_matrix(raw)
            if matrix is not None:
                return matrix
    return None


def _matrix_witness_candidates(
    root: str,
    routes: Mapping[str, Mapping[str, Sequence[str]]],
    cmds_module: Any,
) -> dict[str, dict[str, Any] | None]:
    """Resolve deterministic finger/Append/CCD-IK witness joints.

    A category is deliberately returned as ``None`` when no uniquely usable
    joint exists.  The caller converts that into a failed oracle; missing
    evidence must never silently become a pass.
    """

    joints = list(cmds_module.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or [])
    if cmds_module.nodeType(root) == "joint":
        joints.insert(0, root)
    joints = sorted({_canonical_node_path(joint, cmds_module) for joint in joints}, key=lambda value: (_joint_bone_index(value, cmds_module), value))
    categories: dict[str, dict[str, Any] | None] = {"finger": None, "mmdAppend": None, "mmdCcdIk": None}
    for joint in joints:
        route = routes.get(joint) or {}
        route_nodes: dict[str, str] = {}
        route_node_types = set()
        for routed in _logical_route_values(joint, route):
            try:
                candidate = _canonical_node_path(routed[0], cmds_module)
            except (IndexError, TypeError):
                continue
            node_type = str(cmds_module.nodeType(candidate) or "")
            route_node_types.add(node_type)
            if node_type in {"mmdAppend", "mmdCcdIk"}:
                route_nodes.setdefault(node_type, candidate)
                if node_type == "mmdCcdIk" and categories[node_type] is None:
                    # CCD IK controllers can drive helper/goal bones which are
                    # deliberately absent from every skinCluster.  Their
                    # world matrix is still a valid timeline witness; retain
                    # an explicit no-skin state instead of fabricating one.
                    bind = _skin_bind_for_joint(joint, root, cmds_module)
                    categories[node_type] = {
                        "joint": joint,
                        "bone_index": _joint_bone_index(joint, cmds_module),
                        "binding_identity": joint,
                        "bind_pre_matrix": bind,
                        "skin_available": bind is not None,
                        "route_node": candidate,
                        "route_node_type": node_type,
                    }
        needs_skin = _is_finger_joint(joint, cmds_module) or "mmdAppend" in route_node_types
        bind = _skin_bind_for_joint(joint, root, cmds_module) if needs_skin else None
        if "mmdAppend" in route_node_types and bind is not None and categories["mmdAppend"] is None:
            categories["mmdAppend"] = {
                "joint": joint,
                "bone_index": _joint_bone_index(joint, cmds_module),
                "binding_identity": joint,
                "bind_pre_matrix": bind,
                "skin_available": True,
                "route_node": route_nodes["mmdAppend"],
                "route_node_type": "mmdAppend",
            }
        if categories["finger"] is None and _is_finger_joint(joint, cmds_module) and bind is not None:
            categories["finger"] = {
                "joint": joint,
                "bone_index": _joint_bone_index(joint, cmds_module),
                "binding_identity": joint,
                "bind_pre_matrix": bind,
                "skin_available": True,
                "route_node": None,
                "route_node_type": None,
            }
    return categories


def _logical_route_values(
    joint: str,
    route: Mapping[str, Sequence[str]],
) -> tuple[Sequence[str], ...]:
    """Expose the logical owners hidden behind a validated authoring proxy."""
    values = tuple(route.values())
    try:
        from mmd_tools.converters.vmd_redirected_authoring_proxy import (
            resolve_redirected_authoring_proxy_authority,
        )

        proxy_route, authority, claimed = resolve_redirected_authoring_proxy_authority(
            joint
        )
    except Exception:
        return values
    if not claimed or not proxy_route or set(proxy_route.values()) != set(values):
        return values
    return values + tuple(authority.values())


def _witness_category_status(
    category: str,
    sample_count: int,
    expected_count: int,
    skin_available: bool,
) -> str:
    """Apply the explicit skin contract for matrix witness categories."""

    if sample_count != expected_count:
        return "fail"
    if category in {"finger", "mmdAppend"} and not skin_available:
        return "fail"
    return "pass"


def _compare_third_oracle_values(
    channels: Sequence[Any],
    frames: Sequence[float],
    native_rows: Sequence[Sequence[float]],
    normal_rows: Sequence[Sequence[float]],
    *,
    max_errors: int = THIRD_ORACLE_MAX_ERRORS,
) -> dict[str, Any]:
    """Compare C++ timeline values with normal currentTime/getAttr values."""

    mismatches: list[dict[str, Any]] = []
    max_abs_error = 0.0
    max_relative_error = 0.0
    compared = 0
    malformed = 0
    mismatch_count = 0
    for frame_index, frame in enumerate(frames):
        native_row = native_rows[frame_index] if frame_index < len(native_rows) else ()
        normal_row = normal_rows[frame_index] if frame_index < len(normal_rows) else ()
        for channel_index, channel in enumerate(channels):
            try:
                native_value = float(native_row[channel_index])
                normal_value = float(normal_row[channel_index])
            except (IndexError, TypeError, ValueError, OverflowError):
                malformed += 1
                mismatch_count += 1
                if len(mismatches) < max_errors:
                    mismatches.append({"frame": frame, "channel": getattr(channel, "plug", channel_index), "error": "missing_or_non_numeric"})
                continue
            if not math.isfinite(native_value) or not math.isfinite(normal_value):
                malformed += 1
                mismatch_count += 1
                if len(mismatches) < max_errors:
                    mismatches.append({"frame": frame, "channel": channel.plug, "error": "non_finite"})
                continue
            compared += 1
            absolute = abs(native_value - normal_value)
            scale = max(abs(native_value), abs(normal_value), 1.0)
            relative = absolute / scale
            max_abs_error = max(max_abs_error, absolute)
            max_relative_error = max(max_relative_error, relative)
            tolerance = float(THIRD_ORACLE_TOLERANCE.get(str(channel.unit), THIRD_ORACLE_TOLERANCE["scalar"]))
            if absolute > tolerance:
                mismatch_count += 1
                if len(mismatches) < max_errors:
                    mismatches.append({
                        "frame": frame,
                        "channel": channel.plug,
                        "unit": channel.unit,
                        "native": native_value,
                        "normal_getAttr": normal_value,
                        "abs_error": absolute,
                        "tolerance": tolerance,
                    })
    expected = len(frames) * len(channels)
    return {
        "status": "pass" if compared == expected and mismatch_count == 0 else "fail",
        "frame_count": len(frames),
        "channel_count": len(channels),
        "sample_count": expected,
        "compared_count": compared,
        "malformed_count": malformed,
        "mismatch_count": mismatch_count,
        "max_abs_error": max_abs_error,
        "max_relative_error": max_relative_error,
        "mismatches": mismatches,
        "tolerance_by_unit": dict(THIRD_ORACLE_TOLERANCE),
    }


def _third_oracle_frames(prefix: int) -> tuple[float, ...]:
    frames = tuple(frame for frame in THIRD_ORACLE_FRAMES if frame < float(prefix))
    if not frames:
        raise RuntimeError(f"prefix {prefix} has no third-oracle frames")
    return frames


def _run_third_oracle(
    root: str,
    prefix: int,
    routes: Mapping[str, Mapping[str, Sequence[str]]],
    cmds_module: Any,
) -> dict[str, Any]:
    """Cross-check native timeline sampling against normal Maya timeline reads."""

    from mmd_tools.adapters.native_vmd_batch_sampler import NativeVmdBatchSampler
    from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector

    frames = _third_oracle_frames(prefix)
    categories = _matrix_witness_candidates(root, routes, cmds_module)
    entry_time = float(cmds_module.currentTime(query=True))
    normal_rows: list[list[float]] = []
    matrix_samples: dict[str, list[dict[str, Any]]] = {key: [] for key in categories}
    errors: list[str] = []
    native_rows: Sequence[Sequence[float]] = ()
    plan = None
    native_diagnostics: dict[str, Any] = {}
    restored_time = None
    try:
        collector = VmdSceneCollector()
        joints = collector._find_joints(root)
        # Keep the same animated-joint ownership/order as the production plan.
        if not joints:
            raise RuntimeError("Current Model has no joints for third oracle")
        authored_joints = []
        from mmd_tools.converters.vmd_scene_collector import _routed_key_times
        for joint in joints:
            long_joint = str((cmds_module.ls(joint, long=True) or [joint])[0])
            route = routes.get(long_joint, {})
            if _routed_key_times(joint, route):
                authored_joints.append(joint)
        if not authored_joints:
            raise RuntimeError("Current Model has no animated bone routes for third oracle")
        sampler = NativeVmdBatchSampler(cmds_module)
        if not sampler.available:
            raise RuntimeError(f"mmdVmdBatchSample unavailable: {sampler.last_diagnostics!r}")
        samples = sampler.sample_dense_bone_channels(
            frames,
            authored_joints,
            input_routes=routes,
        )
        plan = samples.plan
        native_rows = samples.rows
        native_diagnostics = dict(sampler.last_diagnostics)
        for frame in frames:
            cmds_module.currentTime(frame, edit=True)
            row = []
            for channel in plan.physical_channels:
                value = cmds_module.getAttr(channel.plug)
                if isinstance(value, (list, tuple)):
                    if len(value) != 1 or isinstance(value[0], (list, tuple)):
                        raise ValueError(f"normal getAttr returned non-scalar for {channel.plug}: {value!r}")
                    value = value[0]
                row.append(float(value))
            normal_rows.append(row)
            for category, witness in categories.items():
                if witness is None:
                    continue
                world = _finite_matrix(cmds_module.xform(witness["joint"], query=True, worldSpace=True, matrix=True))
                if world is None:
                    raise ValueError(f"{category} witness world matrix is missing or non-finite")
                skin = None
                if witness.get("skin_available"):
                    skin = _matrix_product(world, witness["bind_pre_matrix"])
                    if not all(math.isfinite(value) for value in skin):
                        raise ValueError(f"{category} witness skin matrix is non-finite")
                matrix_samples[category].append({
                    "frame": frame,
                    "world_matrix": [round(value, 9) for value in world],
                    "skin_available": bool(witness.get("skin_available")),
                    "skin_matrix": [round(value, 9) for value in skin] if skin is not None else None,
                })
    except Exception as exc:
        errors.append(f"third oracle evaluation failed: {type(exc).__name__}: {exc}")
    finally:
        try:
            cmds_module.currentTime(entry_time, edit=True)
            restored_time = float(cmds_module.currentTime(query=True))
        except Exception as exc:
            errors.append(f"currentTime restoration failed: {type(exc).__name__}: {exc}")
    restored_exactly = restored_time is not None and entry_time == restored_time
    if not restored_exactly:
        errors.append(f"currentTime restoration differs: entry={entry_time} restored={restored_time}")
    scalar = {"status": "fail", "mismatches": [], "frame_count": len(frames), "channel_count": len(plan.physical_channels) if plan is not None else 0}
    if plan is not None and not errors:
        scalar = _compare_third_oracle_values(plan.physical_channels, frames, native_rows, normal_rows)
    witness_report: dict[str, Any] = {"status": "pass", "categories": {}, "missing_categories": []}
    for category, witness in categories.items():
        if witness is None:
            witness_report["status"] = "fail"
            witness_report["missing_categories"].append(category)
            witness_report["categories"][category] = {"status": "fail", "error": "unresolved witness category"}
            continue
        samples = matrix_samples[category]
        category_status = _witness_category_status(
            category,
            len(samples),
            len(frames),
            bool(witness.get("skin_available")),
        )
        if category_status != "pass":
            witness_report["status"] = "fail"
        witness_report["categories"][category] = {
            "status": category_status,
            "joint": witness["joint"],
            "bone_index": witness["bone_index"],
            "binding_identity": witness["binding_identity"],
            "skin_available": bool(witness.get("skin_available")),
            "route_node": witness["route_node"],
            "route_node_type": witness["route_node_type"],
            "samples": samples[:20],
        }
    status = "pass" if not errors and restored_exactly and scalar.get("status") == "pass" and witness_report["status"] == "pass" else "fail"
    return {
        "status": status,
        "frames": list(frames),
        "scalar": scalar,
        "witnesses": witness_report,
        "current_time": {"entry": entry_time, "restored": restored_time, "restored_exactly": restored_exactly},
        "native": native_diagnostics,
        "errors": errors[:THIRD_ORACLE_MAX_ERRORS],
    }


def _animated_joint_plan(
    root: str,
    prefix: int,
    cmds_module: Any,
) -> tuple[Any, Mapping[str, Any], list[str]]:
    from mmd_tools.adapters.native_vmd_batch_sampler import build_dense_bone_sample_plan
    from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector, _routed_key_times

    collector = VmdSceneCollector()
    joints = collector._find_joints(root)
    routes = collector._scene_authored_input_routes(joints, root)
    animated = []
    for joint in joints:
        long_joint = str((cmds_module.ls(joint, long=True) or [joint])[0])
        route = routes.get(long_joint, {})
        if _routed_key_times(joint, route):
            animated.append(joint)
    if not animated:
        raise RuntimeError("Current Model has no animated bone routes")
    plan = build_dense_bone_sample_plan(
        animated,
        range(prefix),
        input_routes=routes,
        cmds_module=cmds_module,
    )
    return plan, routes, animated


def _run_worker(config: Mapping[str, Any], prefix: int) -> dict[str, Any]:
    import maya.standalone
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    from maya import cmds
    from mmd_tools.adapters.native_vmd_batch_sampler import (
        EVALUATION_POLICY,
        NativeVmdBatchSampler,
    )

    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    load_mmd_tools_plugin(ROOT)
    import_started = time.perf_counter()
    root = _import_assets(config, cmds)
    import_wall_sec = time.perf_counter() - import_started
    plan, routes, animated = _animated_joint_plan(root, prefix, cmds)
    inventory = _route_inventory(plan, routes, cmds)
    inventory_sha256 = hashlib.sha256(
        json.dumps(
            inventory,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    gateway = NativeVmdBatchSampler(cmds)
    if not gateway.available:
        raise RuntimeError(f"mmdVmdBatchSample unavailable: {gateway.last_diagnostics!r}")
    entry_time = float(cmds.currentTime(query=True))
    samples = gateway.sample_dense_bone_channels(
        range(prefix),
        animated,
        input_routes=routes,
    )
    wall_sec = samples.wall_sec
    restored_time = float(cmds.currentTime(query=True))
    if not math.isclose(entry_time, restored_time, rel_tol=0.0, abs_tol=1.0e-9):
        raise RuntimeError(
            f"currentTime was not restored: entry={entry_time} restored={restored_time}"
        )
    values_blob = _value_bytes(
        [value for row in samples.rows for value in row]
    )
    values_path = _values_path(config, prefix)
    values_path.write_bytes(values_blob)
    full_count = int(config["full_frame_count"])
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "evaluation_policy": EVALUATION_POLICY,
        "prefix_frames": prefix,
        "full_frame_count": full_count,
        "wall_sec": round(wall_sec, 6),
        "estimated_full_wall_sec": round(wall_sec * full_count / prefix, 6),
        "import_wall_sec": round(import_wall_sec, 6),
        "current_time": {
            "entry": entry_time,
            "restored": restored_time,
            "restored_exactly": entry_time == restored_time,
        },
        "sampled_values": {
            "float_count": samples.sample_count,
            "byte_count": len(values_blob),
            "sha256": hashlib.sha256(values_blob).hexdigest(),
            "artifact": str(values_path),
        },
        "channel_path_counts": dict(samples.strategy_counts),
        "route_inventory": inventory,
        "route_inventory_sha256": inventory_sha256,
        "assets": {
            "pmx_path": str(config["pmx_path"]),
            "pmx_sha256": _sha256_file(Path(config["pmx_path"])),
            "vmd_path": str(config["vmd_path"]),
            "vmd_sha256": _sha256_file(Path(config["vmd_path"])),
            "current_model_root": root,
        },
        "native": dict(gateway.last_diagnostics),
    }
    third_oracle = _run_third_oracle(root, prefix, routes, cmds)
    result["third_oracle"] = third_oracle
    if third_oracle.get("status") != "pass":
        result["status"] = "fail"
    result_path = _result_path(config, prefix)
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def estimate_full_wall(results: Sequence[Mapping[str, Any]], full_frame_count: int) -> float:
    """Return the least-squares-through-origin prefix extrapolation."""

    denominator = sum(float(item["prefix_frames"]) ** 2 for item in results)
    if denominator <= 0.0:
        raise ValueError("prefix results are empty")
    slope = sum(
        float(item["prefix_frames"]) * float(item["wall_sec"])
        for item in results
    ) / denominator
    return round(slope * int(full_frame_count), 6)


def _run_controller(config_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    mayapy = config.get("mayapy") or sys.executable
    results: dict[int, dict[str, Any]] = {}
    launches = []
    for prefix in config["prefix_frames"]:
        stem = _result_stem(prefix)
        stdout_path = out_dir / f"{stem}.stdout.log"
        stderr_path = out_dir / f"{stem}.stderr.log"
        command = [
            str(mayapy),
            str(Path(__file__).resolve()),
            "--config",
            str(config_path),
            "--worker",
            "--prefix",
            str(prefix),
        ]
        environment = dict(os.environ)
        environment["PYTHONUTF8"] = "1"
        environment.setdefault("MMD_TOOLS_CPP_CONFIG", "Release")
        result_path = _result_path(config, prefix)
        if result_path.exists():
            result_path.unlink()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=float(config["worker_timeout_sec"]),
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(str(exc.stdout or ""), encoding="utf-8")
            stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
            raise RuntimeError(
                f"probe worker timed out: prefix={prefix} "
                f"timeout_sec={config['worker_timeout_sec']}"
            ) from exc
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        launches.append(
            {
                "prefix_frames": prefix,
                "return_code": completed.returncode,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
            }
        )
        if not result_path.exists():
            raise RuntimeError(
                f"probe worker failed without structured result: prefix={prefix} "
                f"return_code={completed.returncode}; stderr={stderr_path}"
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        results[prefix] = result
    estimate = estimate_full_wall(
        [results[prefix] for prefix in config["prefix_frames"]],
        int(config["full_frame_count"]),
    )
    third_oracle_pass = all(
        result.get("third_oracle", {}).get("status") == "pass"
        for result in results.values()
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if third_oracle_pass else "fail",
        "fresh_process_per_prefix": True,
        "prefix_frames": list(config["prefix_frames"]),
        "full_frame_count": int(config["full_frame_count"]),
        "estimated_full_wall_sec": estimate,
        "third_oracle_pass": third_oracle_pass,
        "launches": launches,
        "results": [results[prefix] for prefix in config["prefix_frames"]],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _console_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep terminal output bounded; detailed evidence already lives in JSON."""

    summary = {
        key: result[key]
        for key in (
            "schema_version",
            "status",
            "evaluation_policy",
            "prefix_frames",
            "wall_sec",
            "estimated_full_wall_sec",
        )
        if key in result
    }
    sampled_values = result.get("sampled_values")
    if isinstance(sampled_values, Mapping):
        summary["sampled_values"] = {
            key: sampled_values[key]
            for key in ("float_count", "sha256")
            if key in sampled_values
        }
    third_oracle = result.get("third_oracle")
    if isinstance(third_oracle, Mapping):
        scalar = third_oracle.get("scalar")
        witnesses = third_oracle.get("witnesses")
        summary["third_oracle"] = {
            "status": third_oracle.get("status"),
            "frames": third_oracle.get("frames"),
            "scalar": {
                key: scalar[key]
                for key in ("status", "sample_count", "compared_count", "mismatch_count", "max_abs_error")
                if isinstance(scalar, Mapping) and key in scalar
            },
            "witnesses": {
                "status": witnesses.get("status"),
                "missing_categories": witnesses.get("missing_categories"),
            }
            if isinstance(witnesses, Mapping)
            else None,
            "current_time": third_oracle.get("current_time"),
            "errors": list(third_oracle.get("errors", ()))[:THIRD_ORACLE_MAX_ERRORS],
        }
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    config = load_config(args.config)
    if args.worker:
        if args.prefix is None:
            raise ProbeConfigurationError("worker requires --prefix")
        result = _run_worker(config, args.prefix)
    else:
        if args.prefix is not None:
            raise ProbeConfigurationError("--prefix requires --worker")
        result = _run_controller(args.config, config)
    print(json.dumps(_console_summary(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
