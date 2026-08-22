"""Maya GUI gate for read-only Control Rig direct VMD export.

External PMX/VMD paths are read only from an ASCII-named UTF-8 JSON config;
they are never placed on Maya's command line or commandPort payload.  Every
configured range gets a fresh source import, a production Bake Timeline
prepare/validate/execute cycle, and a fresh legacy-rig import of the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402


REPORT_KIND = "control_rig_direct_vmd_export_probe"
DEFAULT_POSE_TOLERANCE = 1.0e-4
DEFAULT_SOLVER_POSE_TOLERANCE = 5.0e-3
DEFAULT_SOLVER_AUTHORED_TOLERANCE = 1.0e-5
MARKER = "CONTROL_RIG_DIRECT_VMD_EXPORT_PROBE_COMPLETE"
DEFAULT_OUT = ROOT / "build" / "reports" / "control-rig-direct-vmd"


def _ascii_path(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not str(resolved).isascii():
        raise ValueError(f"{description} must use an ASCII path: {resolved}")
    return resolved


def load_config(path: Path) -> dict[str, Any]:
    """Load and validate the external-asset UTF-8 JSON contract."""

    source = _ascii_path(path, "config")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read UTF-8 JSON config: {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")
    result = dict(raw)
    for field in ("pmx", "vmd"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config.{field} must be a non-empty path string")
        asset = Path(value).expanduser().resolve()
        if not asset.is_file():
            raise ValueError(f"config.{field} does not exist: {asset}")
        result[field] = str(asset)
    ranges = raw.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("config.ranges must be a non-empty array")
    normalized_ranges = []
    names = set()
    for index, row in enumerate(ranges):
        if not isinstance(row, Mapping):
            raise ValueError(f"config.ranges[{index}] must be an object")
        name = str(row.get("name") or f"range-{index:02d}")
        if name in names or not name.isascii():
            raise ValueError("range names must be unique ASCII strings")
        names.add(name)
        try:
            start = int(row["start"])
            end = int(row["end"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"config.ranges[{index}] needs integer start/end") from exc
        if start < 0 or end < start:
            raise ValueError(f"config.ranges[{index}] is not an ordered non-negative range")
        raw_frames = row.get("oracle_frames", (start, (start + end) // 2, end))
        if raw_frames == "all":
            frames = list(range(start, end + 1))
        elif isinstance(raw_frames, (list, tuple)):
            frames = sorted({int(frame) for frame in raw_frames})
        else:
            raise ValueError(
                f"config.ranges[{index}].oracle_frames must be an array or 'all'"
            )
        if not frames or any(frame < start or frame > end for frame in frames):
            raise ValueError(f"config.ranges[{index}].oracle_frames must stay in range")
        normalized_ranges.append(
            {"name": name, "start": start, "end": end, "oracle_frames": frames}
        )
    result["ranges"] = normalized_ranges
    result["pose_tolerance"] = float(raw.get("pose_tolerance", DEFAULT_POSE_TOLERANCE))
    if not math.isfinite(result["pose_tolerance"]) or result["pose_tolerance"] < 0:
        raise ValueError("config.pose_tolerance must be finite and non-negative")
    result["solver_pose_tolerance"] = float(
        raw.get("solver_pose_tolerance", DEFAULT_SOLVER_POSE_TOLERANCE)
    )
    if (
        not math.isfinite(result["solver_pose_tolerance"])
        or result["solver_pose_tolerance"] < 0
    ):
        raise ValueError("config.solver_pose_tolerance must be finite and non-negative")
    result["solver_authored_tolerance"] = float(
        raw.get("solver_authored_tolerance", DEFAULT_SOLVER_AUTHORED_TOLERANCE)
    )
    if (
        not math.isfinite(result["solver_authored_tolerance"])
        or result["solver_authored_tolerance"] < 0
    ):
        raise ValueError(
            "config.solver_authored_tolerance must be finite and non-negative"
        )
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _import_pair(pmx: Path, vmd: Path, *, control_rig: bool) -> str:
    """Import a PMX/VMD pair through production actions."""

    from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest
    from mmd_tools.actions.import_vmd_action import ImportVmdAction, ImportVmdRequest

    options = {
        "scale": 1.0,
        # Direct VMD export deliberately excludes runtime physics outputs.
        # Keep the source/fresh pose oracle on the authoring rig instead of
        # comparing two stateful cloth simulations at different wall times.
        "import_physics": False,
        "setup_rig": True,
        "setup_bone_orientation": True,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
        "profile": {},
    }
    model_result = ImportModelAction().execute(
        ImportModelRequest(str(pmx), options=options, create_new_scene=True)
    )
    if not model_result.succeeded or not model_result.root_node:
        raise RuntimeError(f"PMX import failed: {model_result.error or model_result.warnings}")
    matches = __import__("maya.cmds", fromlist=["cmds"]).ls(
        str(model_result.root_node), long=True
    ) or []
    if len(matches) != 1:
        raise RuntimeError(f"PMX root is ambiguous: {model_result.root_node!r} -> {matches!r}")
    root = str(matches[0])
    motion_options = {
        **options,
        "target_model": root,
        "pmx_path": str(pmx),
        "bake_mode": False,
        "clear_existing_motion": True,
        "create_mmd_control_rig": bool(control_rig),
    }
    motion_result = ImportVmdAction().execute(
        ImportVmdRequest(str(vmd), options=motion_options, create_new_scene=False)
    )
    if not motion_result.succeeded:
        raise RuntimeError(f"VMD import failed: {motion_result.error or motion_result.warnings}")
    return root


def _capture_morph_values(root: str, frames: Iterable[int]) -> dict[str, dict[str, float]]:
    from maya import cmds

    if not cmds.attributeQuery("mmd_morph_controller", node=root, exists=True):
        return {}
    controllers = cmds.listConnections(
        f"{root}.mmd_morph_controller",
        source=True,
        destination=False,
        type="mmdMorphController",
    ) or []
    if len(controllers) != 1:
        raise RuntimeError(f"expected one model morph controller, got {controllers!r}")
    controller = str(controllers[0])
    indices = cmds.getAttr(f"{controller}.inputWeight", multiIndices=True) or []
    routes = [
        (
            str(cmds.aliasAttr(f"{controller}.inputWeight[{index}]", query=True) or index),
            f"{controller}.inputWeight[{index}]",
        )
        for index in sorted(int(value) for value in indices)
    ]
    result: dict[str, dict[str, float]] = {alias: {} for alias, _plug in routes}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        for alias, plug in routes:
            result[alias][str(int(frame))] = round(float(cmds.getAttr(plug)), 7)
    return result


def _capture_ik_values(root: str, frames: Iterable[int]) -> dict[str, dict[str, int]]:
    from maya import cmds

    def long_names(values: Iterable[Any]) -> set[str]:
        result = set()
        for value in values:
            result.update(str(item) for item in (cmds.ls(str(value), long=True) or []))
        return result

    root_joints = long_names(
        cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True)
        or []
    )
    nodes = {}
    for node_value in cmds.ls(type="mmdCcdIk", long=True) or []:
        node = str(node_value)
        connected = long_names(
            cmds.listConnections(node, source=True, destination=True, type="joint")
            or []
        )
        if connected and not connected.intersection(root_joints):
            continue
        if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
            continue
        name = str(cmds.getAttr(f"{node}.mmd_ik_bone_name") or "")
        if name:
            nodes[name] = node
    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        result[str(int(frame))] = {
            name: int(bool(cmds.getAttr(f"{node}.enabled")))
            for name, node in sorted(nodes.items())
        }
    return result


def _capture_selected_world_matrices(
    root: str,
    frames: Iterable[int],
    bone_names: set[str],
) -> dict[str, dict[str, list[float]]]:
    """Capture selected Control-bound joint rotations as full world matrices."""

    from maya import cmds

    joints_by_name: dict[str, str] = {}
    for joint in cmds.listRelatives(
        root, allDescendents=True, type="joint", fullPath=True
    ) or []:
        if not cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
            continue
        name = str(cmds.getAttr(f"{joint}.mmd_bone_name") or "")
        if name not in bone_names:
            continue
        if name in joints_by_name:
            raise RuntimeError(f"selected VMD bone name is ambiguous: {name}")
        joints_by_name[name] = str(joint)
    missing = sorted(bone_names - set(joints_by_name))
    if missing:
        raise RuntimeError(f"selected VMD bones are missing from scene: {missing!r}")
    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        result[str(int(frame))] = {
            name: [
                round(float(value), 7)
                for value in (
                    cmds.xform(joint, query=True, worldSpace=True, matrix=True) or []
                )
            ]
            for name, joint in sorted(joints_by_name.items())
        }
    return result


def _capture_solver_affected_bone_names(root: str, selected: set[str]) -> set[str]:
    """Return selected bones whose world matrices inherit native IK output."""

    from maya import cmds

    root_joints = {
        str(joint)
        for joint in (
            cmds.listRelatives(
                root, allDescendents=True, type="joint", fullPath=True
            )
            or []
        )
    }
    affected = set()
    for solver in cmds.ls(type="mmdCcdIk", long=True) or []:
        for slot in range(64):
            for destination in cmds.listConnections(
                f"{solver}.outputRotate[{slot}]",
                source=False,
                destination=True,
                type="joint",
            ) or []:
                joints = cmds.ls(destination, long=True) or [destination]
                joint = str(joints[0])
                if joint not in root_joints:
                    continue
                affected.add(joint)
                affected.update(
                    str(value)
                    for value in (
                        cmds.listRelatives(
                            joint,
                            allDescendents=True,
                            type="joint",
                            fullPath=True,
                        )
                        or []
                    )
                    if str(value) in root_joints
                )
    names = set()
    for joint in affected:
        if cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
            name = str(cmds.getAttr(f"{joint}.mmd_bone_name") or "")
            if name in selected:
                names.add(name)
    return names


def _compare_selected_world_matrices(
    expected: Mapping[str, Mapping[str, list[float]]],
    actual: Mapping[str, Mapping[str, list[float]]],
    tolerance: float,
    *,
    solver_affected_bones: set[str] | None = None,
    solver_tolerance: float | None = None,
) -> list[str]:
    """Return failures for selected-bone world translation or rotation drift."""

    failures = []
    if set(expected) != set(actual):
        return ["selected world-matrix frames differ"]
    for frame, expected_bones in expected.items():
        actual_bones = actual[frame]
        if set(expected_bones) != set(actual_bones):
            failures.append(f"selected world-matrix bones differ at frame {frame}")
            continue
        for name, expected_matrix in expected_bones.items():
            actual_matrix = actual_bones[name]
            difference = (
                float("inf")
                if len(expected_matrix) != len(actual_matrix)
                else max(
                    (
                        abs(float(left) - float(right))
                        for left, right in zip(expected_matrix, actual_matrix)
                    ),
                    default=0.0,
                )
            )
            threshold = (
                float(solver_tolerance)
                if solver_tolerance is not None
                and name in (solver_affected_bones or set())
                else tolerance
            )
            if difference > threshold:
                failures.append(
                    f"selected world matrix frame {frame} bone {name} max error {difference:g}"
                )
    return failures


def _filter_scene_pose(scene: Mapping[str, Any], bone_names: set[str]) -> dict[str, Any]:
    """Keep only the dedicated Control-bound bones in a scene pose oracle."""

    result = dict(scene)
    pose = dict(scene.get("pose", {}))
    frames = {
        str(frame): [row for row in rows if str(row.get("name", "")) in bone_names]
        for frame, rows in pose.get("frames", {}).items()
    }
    joints = [
        row for row in pose.get("joints", []) if str(row.get("name", "")) in bone_names
    ]
    pose.update({"joint_count": len(joints), "joints": joints, "frames": frames})
    result["pose"] = pose
    return result


def _require_selected_control_tracks(bone_names: set[str]) -> set[str]:
    """Fail closed instead of accepting an empty Control-pose oracle."""

    if not bone_names:
        raise RuntimeError("direct export probe found no keyed Control tracks")
    return bone_names


def _capture_parity(
    root: str,
    frames: list[int],
    *,
    pose_bone_names: set[str] | None = None,
) -> dict[str, Any]:
    from tools.export_release_maya_probe import _capture_scene_oracle

    # The shared release oracle walks every PMX joint per requested frame.
    # Direct export gates only dedicated Control-bound bones, which are checked
    # below as full matrices; capture shared metadata without duplicating that
    # all-joint timeline walk.
    scene = _capture_scene_oracle(root, () if pose_bone_names is not None else frames)
    if pose_bone_names is not None:
        scene = _filter_scene_pose(scene, pose_bone_names)
    return {
        "scene": scene,
        "selected_world_matrices": _capture_selected_world_matrices(
            root,
            frames,
            pose_bone_names or set(),
        )
        if pose_bone_names is not None
        else {},
        "solver_affected_bone_names": sorted(
            _capture_solver_affected_bone_names(
                root,
                pose_bone_names or set(),
            )
        )
        if pose_bone_names is not None
        else [],
        "morph_values": _capture_morph_values(root, frames),
        "ik_values": _capture_ik_values(root, frames),
    }


def _compare_parity(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    tolerance: float,
    solver_tolerance: float,
) -> list[str]:
    from tools.export_release_maya_probe import _compare_scene_oracles

    failures = _compare_scene_oracles(
        expected["scene"],
        actual["scene"],
        pose=True,
        pose_tolerance=tolerance,
        mesh=False,
        materials=False,
    )
    expected_solver_bones = set(expected.get("solver_affected_bone_names", []))
    actual_solver_bones = set(actual.get("solver_affected_bone_names", []))
    if expected_solver_bones != actual_solver_bones:
        failures.append("solver-affected selected bone names differ")
    failures.extend(
        _compare_selected_world_matrices(
            expected.get("selected_world_matrices", {}),
            actual.get("selected_world_matrices", {}),
            tolerance,
            solver_affected_bones=expected_solver_bones,
            solver_tolerance=solver_tolerance,
        )
    )
    if expected.get("ik_values") != actual.get("ik_values"):
        failures.append("IK state parity differs")
    expected_morphs = expected.get("morph_values", {})
    actual_morphs = actual.get("morph_values", {})
    if set(expected_morphs) != set(actual_morphs):
        failures.append("Morph track names differ")
    else:
        for name, values in expected_morphs.items():
            if set(values) != set(actual_morphs[name]) or any(
                abs(float(value) - float(actual_morphs[name][frame])) > tolerance
                for frame, value in values.items()
            ):
                failures.append(f"Morph values differ: {name}")
    return failures


def _parity_section_status(failures: Iterable[str]) -> dict[str, bool]:
    """Classify parity failures without reporting a contradictory section pass."""

    values = list(failures)
    return {
        "pose": not any(
            failure.startswith(("pose ", "selected world matrix "))
            for failure in values
        ),
        "morph": not any(failure.startswith("Morph ") for failure in values),
        "ik": "IK state parity differs" not in values,
    }


def _compare_solver_authored_vmd_keys(
    source_frames: Iterable[Any],
    output_frames: Iterable[Any],
    solver_bone_names: set[str],
    start_frame: int,
    end_frame: int,
    tolerance: float,
) -> dict[str, Any]:
    """Compare pre-solver VMD values at authoritative source key times."""

    def index_frames(frames: Iterable[Any]) -> tuple[dict[tuple[str, int], Any], list[str]]:
        result = {}
        failures = []
        for frame in frames:
            key = (str(frame.bone_name), int(frame.frame_number))
            if key in result:
                failures.append(f"duplicate VMD bone frame: {key!r}")
            result[key] = frame
        return result, failures

    source, failures = index_frames(source_frames)
    output, output_failures = index_frames(output_frames)
    failures.extend(output_failures)
    checked = 0
    max_error = 0.0
    for key, source_frame in sorted(source.items()):
        name, frame_number = key
        if (
            name not in solver_bone_names
            or frame_number < start_frame
            or frame_number > end_frame
        ):
            continue
        checked += 1
        output_frame = output.get(key)
        if output_frame is None:
            failures.append(f"solver-authored output frame is missing: {key!r}")
            continue
        source_position = [float(value) for value in source_frame.position]
        output_position = [float(value) for value in output_frame.position]
        position_error = max(
            (
                abs(left - right)
                for left, right in zip(source_position, output_position)
            ),
            default=float("inf"),
        )
        source_rotation = [float(value) for value in source_frame.rotation]
        output_rotation = [float(value) for value in output_frame.rotation]
        direct_error = max(
            (abs(left - right) for left, right in zip(source_rotation, output_rotation)),
            default=float("inf"),
        )
        negated_error = max(
            (abs(left + right) for left, right in zip(source_rotation, output_rotation)),
            default=float("inf"),
        )
        error = max(position_error, min(direct_error, negated_error))
        max_error = max(max_error, error)
        if error > tolerance:
            failures.append(
                f"solver-authored VMD frame {frame_number} bone {name} max error {error:g}"
            )
    if solver_bone_names and checked == 0:
        failures.append("solver-authored VMD comparison found no source keys")
    return {
        "pass": not failures,
        "checked_source_keys": checked,
        "max_error": max_error,
        "tolerance": tolerance,
        "failures": failures,
    }


def _plug_snapshot(plug: str) -> dict[str, Any]:
    from maya import cmds

    def tangent_values(flag: str) -> list[Any]:
        return list(cmds.keyTangent(plug, query=True, **{flag: True}) or [])

    value = cmds.getAttr(plug)
    return {
        "plug": plug,
        "value": _normalize_snapshot_value(value),
        "incoming": sorted(
            str(value)
            for value in (
                cmds.listConnections(
                    plug, source=True, destination=False, plugs=True
                )
                or []
            )
        ),
        "key_times": [
            float(value)
            for value in (cmds.keyframe(plug, query=True, timeChange=True) or [])
        ],
        "key_values": [
            float(value)
            for value in (cmds.keyframe(plug, query=True, valueChange=True) or [])
        ],
        "tangents": {
            flag: tangent_values(flag)
            for flag in (
                "inTangentType",
                "outTangentType",
                "inAngle",
                "outAngle",
                "inWeight",
                "outWeight",
                "weightedTangents",
                "lock",
                "weightLock",
            )
        },
    }


def _normalize_snapshot_value(value: Any) -> Any:
    """Normalize Maya scalar/compound values into stable JSON primitives."""

    if isinstance(value, (list, tuple)):
        return [_normalize_snapshot_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _scene_snapshot(root: str) -> dict[str, Any]:
    """Capture direct-route state that a read-only export must preserve."""

    from maya import cmds
    from mmd_tools.core.constants import ATTR_MMD_CONTROL_RIG_JSON
    from mmd_tools.core.mmd_control_rig_motion import (
        resolve_control_rig_direct_vmd_export_routes,
    )

    resolved = resolve_control_rig_direct_vmd_export_routes(root)
    plugs = set()
    for candidate in resolved["candidates"].values():
        plugs.update(str(value) for value in candidate["selectorPlugs"])
        plugs.update(
            f"{node}.{attribute}"
            for node, attribute in candidate["valueRoutes"].values()
        )
    plugs.update(
        f"{node}.{attribute}"
        for node, attribute in resolved.get("ikStateRoutes", {}).values()
    )
    if cmds.attributeQuery("mmd_morph_controller", node=root, exists=True):
        controllers = cmds.listConnections(
            f"{root}.mmd_morph_controller",
            source=True,
            destination=False,
            type="mmdMorphController",
        ) or []
        if len(controllers) != 1:
            raise RuntimeError(
                f"expected one model morph controller, got {controllers!r}"
            )
        controller = str(controllers[0])
        plugs.update(
            f"{controller}.inputWeight[{int(index)}]"
            for index in (
                cmds.getAttr(f"{controller}.inputWeight", multiIndices=True) or []
            )
        )
    descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    snapshot = {
        "current_time": float(cmds.currentTime(query=True)),
        "selection": sorted(str(value) for value in (cmds.ls(selection=True, long=True) or [])),
        "scene_modified": bool(cmds.file(query=True, modified=True)),
        "metadata": cmds.getAttr(f"{root}.{ATTR_MMD_CONTROL_RIG_JSON}"),
        "descendants": sorted(str(value) for value in descendants),
        "scene_node_count": len(cmds.ls(long=True) or []),
        "routes": [_plug_snapshot(plug) for plug in sorted(plugs)],
    }
    return {"sha256": _digest(snapshot), "payload": snapshot}


def _workflow_report(report: Any) -> dict[str, Any]:
    return {
        "state": str(getattr(report, "state", "")),
        "blocking": bool(getattr(getattr(report, "report", None), "is_blocking", False)),
        "issues": [
            {
                "code": str(getattr(issue, "code", "")),
                "severity": str(getattr(issue, "severity", "")),
                "blocking": bool(getattr(issue, "blocking", False)),
            }
            for issue in getattr(getattr(report, "report", None), "issues", ())
        ],
    }


def _run_range(config: Mapping[str, Any], row: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    from maya import cmds

    from mmd_tools.adapters.maya_vmd_prepare_backend import create_maya_vmd_prepare_action
    from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata
    from mmd_tools.core.mmd_control_rig_motion import (
        resolve_control_rig_direct_vmd_export_routes,
    )
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.services.export_workflow_service import (
        ExportWorkflowRequest,
        ExportWorkflowService,
    )

    pmx = Path(str(config["pmx"]))
    source_vmd = Path(str(config["vmd"]))
    frames = [int(value) for value in row["oracle_frames"]]
    root = _import_pair(pmx, source_vmd, control_rig=True)
    metadata = read_mmd_control_rig_metadata(root) or {}
    if metadata.get("state") != "EDIT" or metadata.get("owner") != "CONTROL_OWNED":
        raise RuntimeError(f"source import did not enter EDIT/CONTROL_OWNED: {metadata}")
    resolved = resolve_control_rig_direct_vmd_export_routes(root)
    selected_control_names = _require_selected_control_tracks(
        {
            str(candidate["boneName"])
            for candidate in resolved["candidates"].values()
            if any(
                cmds.keyframe(plug, query=True, timeChange=True) or []
                for plug in candidate["selectorPlugs"]
            )
        }
    )
    expected = _capture_parity(
        root,
        frames,
        pose_bone_names=selected_control_names,
    )
    before = _scene_snapshot(root)
    output = out_dir / f"{row['name']}.vmd"
    request = ExportWorkflowRequest(
        str(output),
        {
            "export_format": "vmd",
            "export_strategy": "bake_timeline",
            "authoring_semantics": "auto",
            "require_target": True,
            "require_current_model": True,
            "current_model_root": root,
            "target_model": root,
            "start_frame": int(row["start"]),
            "end_frame": int(row["end"]),
            "validation_report_dir": str(out_dir / f"{row['name']}-validation"),
        },
    )
    workflow = ExportWorkflowService(
        prepare_vmd_action=create_maya_vmd_prepare_action()
    )
    preparation = workflow.prepare_vmd(request)
    token = getattr(preparation, "token", None)
    if not getattr(preparation, "succeeded", False) or token is None:
        raise RuntimeError(f"Bake Timeline prepare failed: {getattr(preparation, 'error', None)}")
    request.prepared_vmd_token = token
    try:
        validation = workflow.validate(request)
        if validation.error is not None or validation.report.is_blocking:
            raise RuntimeError(f"Bake Timeline validation blocked: {_workflow_report(validation)}")
        execution = workflow.execute(request, acknowledge_warnings=True)
        if not execution.succeeded:
            raise RuntimeError(f"Bake Timeline export failed: {execution.error}")
    finally:
        workflow.invalidate_prepared_vmd(token)
    after = _scene_snapshot(root)
    scene_unchanged = before["sha256"] == after["sha256"]
    parsed = VmdData().parse_file(str(output))
    output_bone_names = {str(frame.bone_name) for frame in parsed.bone_frames}
    missing_selected = sorted(selected_control_names - output_bone_names)
    unexpected_output = sorted(output_bone_names - selected_control_names)
    source_data = VmdData().parse_file(str(source_vmd))
    source_frame_counts: dict[str, int] = {}
    for frame in source_data.bone_frames:
        name = str(frame.bone_name)
        source_frame_counts[name] = source_frame_counts.get(name, 0) + 1
    omitted_non_control = {
        name: source_frame_counts[name]
        for name in sorted(set(source_frame_counts) - selected_control_names)
        if name not in output_bone_names
    }
    fresh_root = _import_pair(pmx, output, control_rig=False)
    actual = _capture_parity(
        fresh_root,
        frames,
        pose_bone_names=selected_control_names,
    )
    failures = _compare_parity(
        expected,
        actual,
        float(config["pose_tolerance"]),
        float(config["solver_pose_tolerance"]),
    )
    solver_authored = _compare_solver_authored_vmd_keys(
        source_data.bone_frames,
        parsed.bone_frames,
        set(expected.get("solver_affected_bone_names", [])),
        int(row["start"]),
        int(row["end"]),
        float(config["solver_authored_tolerance"]),
    )
    failures.extend(solver_authored["failures"])
    if missing_selected:
        failures.append(f"selected Control tracks missing from output: {missing_selected!r}")
    if unexpected_output:
        failures.append(f"unexpected non-Control tracks in output: {unexpected_output!r}")
    parity_sections = _parity_section_status(failures)
    passed = scene_unchanged and not failures
    return {
        "status": "pass" if passed else "fail",
        "name": row["name"],
        "range": [row["start"], row["end"]],
        "oracle_frames": frames,
        "output": str(output),
        "output_counts": {
            "bones": len(parsed.bone_frames),
            "morphs": len(parsed.morph_frames),
            "ik": len(parsed.ik_show_hide_frames),
        },
        "track_coverage": {
            "selected_control_tracks": sorted(selected_control_names),
            "missing_selected_control_tracks": missing_selected,
            "unexpected_output_tracks": unexpected_output,
            "omitted_non_control_authored_tracks": omitted_non_control,
        },
        "solver_authored_vmd_parity": solver_authored,
        "prepare": {
            "status": str(getattr(preparation, "status", "")),
            "token_published": True,
        },
        "validation": _workflow_report(validation),
        "execution": _workflow_report(execution),
        "scene_immutability": {
            "pass": scene_unchanged,
            "before_sha256": before["sha256"],
            "after_sha256": after["sha256"],
        },
        "fresh_import_parity": {
            "pass": not failures,
            **parity_sections,
            "solver_affected_bones": sorted(
                expected.get("solver_affected_bone_names", [])
            ),
            "pose_tolerance": float(config["pose_tolerance"]),
            "solver_pose_tolerance": float(config["solver_pose_tolerance"]),
            "failures": failures[:100],
        },
    }


def maya_main(config_path: str, report_path: str, log_path: str) -> None:
    """CommandPort entrypoint. All path arguments here are ASCII."""

    from maya import cmds
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    report: dict[str, Any] = {"kind": REPORT_KIND, "status": "fail", "cases": []}
    log = Path(log_path)

    def emit(message: str) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    try:
        config = load_config(Path(config_path))
        report.update(
            {
                "maya_version": str(cmds.about(version=True)),
                "pmx": config["pmx"],
                "vmd": config["vmd"],
                "pose_tolerance": config["pose_tolerance"],
                "solver_pose_tolerance": config["solver_pose_tolerance"],
                "solver_authored_tolerance": config[
                    "solver_authored_tolerance"
                ],
            }
        )
        load_mmd_tools_plugin(ROOT)
        out_dir = Path(report_path).resolve().parent / "artifacts"
        for row in config["ranges"]:
            emit(f"RUN {row['name']} {row['start']}..{row['end']}")
            try:
                report["cases"].append(_run_range(config, row, out_dir))
            except Exception as exc:  # noqa: BLE001 - continue to the next fresh case
                report["cases"].append(
                    {
                        "status": "fail",
                        "name": row["name"],
                        "range": [row["start"], row["end"]],
                        "oracle_frames": row["oracle_frames"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                )
        report["status"] = (
            "pass"
            if report["cases"]
            and all(case.get("status") == "pass" for case in report["cases"])
            else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - the JSON artifact is the gate evidence
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        _write_json(Path(report_path), report)
        emit("RESULT_JSON:" + json.dumps({"status": report["status"]}))
        emit(MARKER)


def _command(config: Path, report: Path, log: Path) -> str:
    for path, description in (
        (config, "config"),
        (report, "report"),
        (log, "log"),
    ):
        _ascii_path(path, description)
    return (
        "from tools.control_rig_direct_vmd_export_probe import maya_main\n"
        f"maya_main({str(config.resolve())!r}, {str(report.resolve())!r}, {str(log.resolve())!r})\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="ASCII path to UTF-8 JSON config")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="ASCII output directory")
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--port", type=int, default=7731)
    parser.add_argument("--timeout", type=float, default=1800.0)
    args = parser.parse_args(argv)
    config_path = _ascii_path(Path(args.config), "config")
    load_config(config_path)
    out_dir = _ascii_path(Path(args.out), "output directory")
    report_path = out_dir / "report.json"
    log_path = out_dir / "probe.log"
    report = run_maya_e2e(
        project_root=ROOT,
        version=str(args.maya),
        out_dir=out_dir,
        port=int(args.port),
        timeout=float(args.timeout),
        log_path=log_path,
        report_path=report_path,
        command=_command(config_path, report_path, log_path),
        marker=MARKER,
        send_label="<control-rig-direct-vmd-export-probe>",
        stale_paths=(report_path, log_path),
        report_error=f"Control Rig direct export report missing: {report_path}",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
