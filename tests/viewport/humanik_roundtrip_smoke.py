"""Maya 2024 S5 HumanIK self-retarget round-trip gate.

The smoke imports one PMX/VMD fixture twice, uses the first copy as a direct
HumanIK SOURCE, bakes a TARGET preview through the S4 boundary, and compares
the resulting deformation matrices and local quaternions.  Determinism is
measured separately from source-to-target fidelity for sequential and random
seek playback.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel
import maya.standalone

from mmd_tools.core.humanik_bake import bake_humanik_target_preview
from mmd_tools.core.humanik_builder import (
    create_humanik_definition,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_preview import BLOCKING_CLASSIFICATIONS, begin_humanik_target_preview


FRAME_START = 0
FRAME_END = 10
MATRIX_MAX_TOLERANCE = 1.0e-3
MATRIX_MEAN_TOLERANCE = 2.5e-4
QUATERNION_MAX_TOLERANCE = math.radians(2.0)
DETERMINISM_TOLERANCE = 1.0e-8
MOTION_TOLERANCE = 1.0e-5
CHANNELS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/humanik_roundtrip_smoke.json")
    parser.add_argument("--evaluation-mode", choices=("off", "serial", "parallel"), default="off")
    parser.add_argument("--hik-profile", choices=("full", "body-only"), default="full")
    parser.add_argument("--characterization-stance", choices=("bind", "t-pose"), default="bind")
    parser.add_argument("--start", type=int, default=FRAME_START)
    parser.add_argument("--end", type=int, default=FRAME_END)
    return parser.parse_args()


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def _load_model(path: Path, *, setup_rig: bool) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": setup_rig,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _load_motion(path: Path, pmx: Path, target_model: str) -> None:
    from mmd_tools.io.mmd_importer import import_mmd_file

    if not import_mmd_file(
        str(path),
        options={
            "target_model": target_model,
            "pmx_path": str(pmx),
            "bake_mode": True,
            "clear_existing_motion": True,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    ):
        raise RuntimeError(f"VMD import failed: {path}")


def _long_name(node: str) -> str:
    values = cmds.ls(node, long=True) or []
    return str(values[0] if values else node)


def _assignment_slots(result) -> Dict[int, Any]:
    return {int(item.hik_index): item for item in result.assignments}


def _skin_clusters(root: str) -> List[str]:
    clusters: List[str] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(str(node))
    return sorted(clusters)


def _skin_influences(root: str) -> Dict[str, Tuple[str, int]]:
    result: Dict[str, Tuple[str, int]] = {}
    for skin in _skin_clusters(root):
        for logical_index in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{logical_index}]", source=True, destination=False, plugs=True
            ) or []
            if not sources:
                continue
            joint = _long_name(str(sources[0]).rsplit(".", 1)[0])
            result.setdefault(joint, (skin, int(logical_index)))
    return result


def _common_skin_slots(source_result, target_result, source_root: str, target_root: str) -> Tuple[Dict[int, Any], Dict[int, Any]]:
    """Resolve matched HIK slots that have a skinCluster influence on both copies."""
    source_slots_raw = _assignment_slots(source_result)
    target_slots_raw = _assignment_slots(target_result)
    source_skin = _skin_influences(source_root)
    target_skin = _skin_influences(target_root)
    source_slots: Dict[int, Any] = {}
    target_slots: Dict[int, Any] = {}
    for slot in sorted(set(source_slots_raw) & set(target_slots_raw)):
        source_joint = _long_name(source_slots_raw[slot].joint)
        target_joint = _long_name(target_slots_raw[slot].joint)
        if source_joint not in source_skin or target_joint not in target_skin:
            continue
        source_skin_cluster, source_index = source_skin[source_joint]
        target_skin_cluster, target_index = target_skin[target_joint]
        source_slots[slot] = {
            "joint": source_joint,
            "hikBone": source_slots_raw[slot].hik_bone,
            "skin": source_skin_cluster,
            "logicalIndex": source_index,
        }
        target_slots[slot] = {
            "joint": target_joint,
            "hikBone": target_slots_raw[slot].hik_bone,
            "skin": target_skin_cluster,
            "logicalIndex": target_index,
        }
    return source_slots, target_slots


def _profile_result(result, profile: str):
    """Filter resolved assignments before HIK characterization for a profile."""
    if profile == "full":
        return result
    assignments = tuple(
        assignment
        for assignment in result.assignments
        if _quaternion_region(assignment.hik_bone) != "finger"
    )
    return replace(result, assignments=assignments)


def _assignment_rows(assignments: Sequence[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "hikSlot": int(assignment.hik_index),
            "hikBone": str(assignment.hik_bone),
            "mmdBone": str(assignment.mmd_bone),
            "joint": _long_name(assignment.joint),
            "category": _quaternion_region(assignment.hik_bone),
        }
        for assignment in assignments
    ]


def _assignment_profile_evidence(
    profile: str,
    source_original,
    target_original,
    source_characterized,
    target_characterized,
) -> Dict[str, Any]:
    source_excluded = [
        assignment
        for assignment in source_original.assignments
        if assignment not in source_characterized.assignments
    ]
    target_excluded = [
        assignment
        for assignment in target_original.assignments
        if assignment not in target_characterized.assignments
    ]
    excluded_slots = sorted({int(assignment.hik_index) for assignment in source_excluded + target_excluded})
    excluded_categories = {"body": 0, "finger": 0, "roll": 0}
    for assignment in source_excluded:
        excluded_categories[_quaternion_region(assignment.hik_bone)] += 1
    return {
        "profile": profile,
        "source": {
            "originalAssignmentCount": len(source_original.assignments),
            "characterizedAssignmentCount": len(source_characterized.assignments),
            "excludedAssignments": _assignment_rows(source_excluded),
        },
        "target": {
            "originalAssignmentCount": len(target_original.assignments),
            "characterizedAssignmentCount": len(target_characterized.assignments),
            "excludedAssignments": _assignment_rows(target_excluded),
        },
        "excludedSlotIds": excluded_slots,
        "excludedCategoryCounts": excluded_categories,
    }


def _common_slot_categories(source_slots: Mapping[int, Mapping[str, Any]]) -> Dict[str, Any]:
    categories = {"body": [], "finger": [], "roll": []}
    for slot, info in source_slots.items():
        categories[_quaternion_region(str(info["hikBone"]))].append(int(slot))
    return {
        "counts": {category: len(slots) for category, slots in categories.items()},
        "slotIds": {category: sorted(slots) for category, slots in categories.items()},
        "total": sum(len(slots) for slots in categories.values()),
    }


STANCE_ELEVATION_TOLERANCE = 1.0e-4
STANCE_DIRECTION_TOLERANCE = 1.0e-8
# Maya may retain sub-micro-unit floating noise in JO-aware bind products.
# Keep this stricter than S5 fidelity thresholds while admitting that noise.
STANCE_RESTORE_TOLERANCE = 1.0e-6


def _vector_length(vector: om.MVector) -> float:
    return math.sqrt(float(vector.x) ** 2 + float(vector.y) ** 2 + float(vector.z) ** 2)


def _unit_vector(vector: om.MVector) -> om.MVector:
    length = _vector_length(vector)
    if length <= 1.0e-12:
        raise RuntimeError("Cannot normalize a zero-length T-pose arm segment")
    return vector / length


def _shortest_arc(source: om.MVector, target: om.MVector) -> om.MQuaternion:
    """Return the shortest quaternion rotating one direction onto another."""
    source_unit = _unit_vector(source)
    target_unit = _unit_vector(target)
    dot = max(-1.0, min(1.0, float(source_unit * target_unit)))
    axis = source_unit ^ target_unit
    axis_length = _vector_length(axis)
    if axis_length <= 1.0e-12:
        if dot >= 0.0:
            return om.MQuaternion()
        fallback = source_unit ^ om.MVector(0.0, 1.0, 0.0)
        if _vector_length(fallback) <= 1.0e-12:
            fallback = source_unit ^ om.MVector(1.0, 0.0, 0.0)
        return om.MQuaternion(math.pi, _unit_vector(fallback))
    return om.MQuaternion(math.acos(dot), _unit_vector(axis))


def _joint_world_direction(joint: str, child: str) -> om.MVector:
    parent = _matrix(f"{joint}.worldMatrix[0]")
    child_matrix = _matrix(f"{child}.worldMatrix[0]")
    return om.MVector(
        float(child_matrix[12] - parent[12]),
        float(child_matrix[13] - parent[13]),
        float(child_matrix[14] - parent[14]),
    )


def _direction_evidence(joint: str, child: str) -> Dict[str, Any]:
    direction = _joint_world_direction(joint, child)
    length = _vector_length(direction)
    horizontal_length = math.sqrt(float(direction.x) ** 2 + float(direction.z) ** 2)
    elevation = math.atan2(float(direction.y), horizontal_length)
    return {
        "joint": str(joint),
        "child": str(child),
        "worldDirection": [float(direction.x), float(direction.y), float(direction.z)],
        "length": length,
        "elevationRadians": elevation,
        "elevationDegrees": math.degrees(elevation),
        "absoluteElevationRadians": abs(elevation),
        "absoluteElevationDegrees": abs(math.degrees(elevation)),
    }


def _set_joint_world_direction(joint: str, child: str, target_direction: om.MVector) -> Dict[str, Any]:
    """Set joint.rotate so its child points to a world-space direction, preserving JO."""
    current_direction = _joint_world_direction(joint, child)
    delta = _shortest_arc(current_direction, target_direction)
    current_world = _matrix(f"{joint}.worldMatrix[0]")
    current_world_rotation = om.MTransformationMatrix(current_world).rotation(asQuaternion=True)
    desired_world = om.MTransformationMatrix(current_world)
    desired_world.setRotation(current_world_rotation * delta)
    desired_world_matrix = desired_world.asMatrix()
    cmds.xform(joint, worldSpace=True, matrix=_matrix_values(desired_world_matrix), preserve=False)
    cmds.refresh(force=True)
    actual_world_matrix = _matrix(f"{joint}.worldMatrix[0]")
    return {
        "method": "world_shortest_arc_with_world_matrix_jointOrient_preservation",
        "axis": [float(delta.x), float(delta.y), float(delta.z)],
        "deltaQuaternion": [float(delta.x), float(delta.y), float(delta.z), float(delta.w)],
        "deltaDegrees": math.degrees(2.0 * math.acos(max(-1.0, min(1.0, float(delta.w))))),
        "targetDirection": [float(value) for value in (target_direction.x, target_direction.y, target_direction.z)],
        "resultingRotateDegrees": [float(value) for value in cmds.getAttr(f"{joint}.rotate")[0]],
        "worldMatrixResidual": _matrix_error(_matrix_values(desired_world_matrix), _matrix_values(actual_world_matrix))[0],
    }


def _stance_joint_map(result) -> Dict[str, Tuple[str, str]]:
    by_hik = {str(assignment.hik_bone): _long_name(assignment.joint) for assignment in result.assignments}
    required = {
        "LeftArm": "LeftForeArm",
        "RightArm": "RightForeArm",
    }
    missing = [name for name, child_name in required.items() if name not in by_hik or child_name not in by_hik]
    if missing:
        raise RuntimeError(f"T-pose requires characterized HIK slots: {', '.join(missing)}")
    return {name: (by_hik[name], by_hik[child_name]) for name, child_name in required.items()}


def _snapshot_stance(root: str, result, common_slots: Mapping[int, Mapping[str, Any]]) -> Dict[str, Any]:
    joints = _stance_joint_map(result)
    skin = _skin_influences(root)
    snapshot: Dict[str, Any] = {"root": root, "modifiedJoints": {}, "commonSkinSlots": {}, "chains": {}}
    for slot, info in sorted(common_slots.items()):
        snapshot["commonSkinSlots"][int(slot)] = {
            "hikBone": str(info["hikBone"]),
            "joint": str(info["joint"]),
            "skin": str(info["skin"]),
            "logicalIndex": int(info["logicalIndex"]),
            "skinMatrix": _skin_matrix(info["joint"], info["skin"], int(info["logicalIndex"])),
        }
    for side, (joint, child) in joints.items():
        skin_info = skin.get(joint)
        if skin_info is None:
            raise RuntimeError(f"T-pose modified joint has no skinCluster bind matrix: {joint}")
        skin_cluster, logical_index = skin_info
        snapshot["chains"][side] = {"joint": joint, "child": child}
        snapshot["modifiedJoints"][joint] = {
            "rotate": [float(value) for value in cmds.getAttr(f"{joint}.rotate")[0]],
            "jointOrient": [float(value) for value in cmds.getAttr(f"{joint}.jointOrient")[0]],
            "skin": skin_cluster,
            "logicalIndex": int(logical_index),
            "skinMatrix": _skin_matrix(joint, skin_cluster, logical_index),
        }
    return snapshot


def _restore_stance(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    rows = []
    for joint, info in snapshot["modifiedJoints"].items():
        cmds.setAttr(f"{joint}.rotate", *info["rotate"], type="double3")
    cmds.refresh(force=True)
    for joint, info in snapshot["modifiedJoints"].items():
        current_rotate = [float(value) for value in cmds.getAttr(f"{joint}.rotate")[0]]
        rotate_residual = max(
            (abs(current - original) for current, original in zip(current_rotate, info["rotate"])),
            default=0.0,
        )
        current_joint_orient = [float(value) for value in cmds.getAttr(f"{joint}.jointOrient")[0]]
        joint_orient_residual = max(
            (abs(current - original) for current, original in zip(current_joint_orient, info["jointOrient"])),
            default=0.0,
        )
        current_skin = _skin_matrix(joint, info["skin"], int(info["logicalIndex"]))
        skin_residual = _matrix_error(info["skinMatrix"], current_skin)[0]
        rows.append(
            {
                "joint": str(joint),
                "rotateResidual": rotate_residual,
                "jointOrientResidual": joint_orient_residual,
                "skinMatrixResidual": skin_residual,
                "passed": rotate_residual <= STANCE_RESTORE_TOLERANCE
                and joint_orient_residual <= STANCE_RESTORE_TOLERANCE
                and skin_residual <= STANCE_RESTORE_TOLERANCE,
            }
        )
    all_skin_rows = []
    for slot, info in sorted(snapshot["commonSkinSlots"].items()):
        current_skin = _skin_matrix(str(info["joint"]), str(info["skin"]), int(info["logicalIndex"]))
        residual = _matrix_error(info["skinMatrix"], current_skin)[0]
        all_skin_rows.append(
            {
                "hikSlot": int(slot),
                "hikBone": str(info["hikBone"]),
                "joint": str(info["joint"]),
                "skinMatrixResidual": residual,
            }
        )
    return {
        "rows": rows,
        "maxRotateResidual": max((row["rotateResidual"] for row in rows), default=0.0),
        "maxJointOrientResidual": max((row["jointOrientResidual"] for row in rows), default=0.0),
        "maxSkinMatrixResidual": max((row["skinMatrixResidual"] for row in rows), default=0.0),
        "allSkinInfluenceCount": len(all_skin_rows),
        "maxAllSkinMatrixResidual": max((row["skinMatrixResidual"] for row in all_skin_rows), default=0.0),
        "worstAllSkinRows": sorted(all_skin_rows, key=lambda row: row["skinMatrixResidual"], reverse=True)[:10],
        "passed": all(row["passed"] for row in rows)
        and all(row["skinMatrixResidual"] <= STANCE_RESTORE_TOLERANCE for row in all_skin_rows),
        "tolerance": STANCE_RESTORE_TOLERANCE,
    }


def _common_skin_snapshot_evaluation(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """Measure current common-skin products against a pre-T-pose snapshot."""
    rows = []
    for slot, info in sorted(snapshot["commonSkinSlots"].items()):
        current_skin = _skin_matrix(str(info["joint"]), str(info["skin"]), int(info["logicalIndex"]))
        residual = _matrix_error(info["skinMatrix"], current_skin)[0]
        rows.append(
            {
                "hikSlot": int(slot),
                "hikBone": str(info["hikBone"]),
                "joint": str(info["joint"]),
                "skinMatrixResidual": residual,
            }
        )
    return {
        "allSkinInfluenceCount": len(rows),
        "maxAllSkinMatrixResidual": max((row["skinMatrixResidual"] for row in rows), default=0.0),
        "worstAllSkinRows": sorted(rows, key=lambda row: row["skinMatrixResidual"], reverse=True)[:10],
        "tolerance": STANCE_RESTORE_TOLERANCE,
        "passed": all(row["skinMatrixResidual"] <= STANCE_RESTORE_TOLERANCE for row in rows),
    }


EXPECTED_TPOSE_ISOLATED_EDGE_COUNT = 6


def _json_attribute_value(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_json_attribute_value(item) for item in value]
    if isinstance(value, (int, float, bool)):
        return float(value) if isinstance(value, float) else value
    return str(value)


def _incoming_sources(destination: str) -> List[str]:
    return sorted(
        {str(value) for value in (cmds.listConnections(destination, source=True, destination=False, plugs=True) or [])}
    )


def _edge_connected(edge: Mapping[str, Any]) -> bool:
    return str(edge["source"]) in set(_incoming_sources(str(edge["destination"])))


def _isolate_hik_writer_edges(hik_joints: Sequence[str]) -> Dict[str, Any]:
    """Temporarily isolate only reviewed ``mute_for_hik`` writer edges."""
    report = classify_humanik_constraints(collect_humanik_constraint_facts(), hik_joints)
    blockers = [row for row in report["rows"] if row["classification"] in BLOCKING_CLASSIFICATIONS]
    if blockers:
        labels = ", ".join(f"{row['node']}:{row['classification']}" for row in blockers)
        raise RuntimeError(f"T-pose HIK ownership blocked: {labels}")
    mute_rows = [row for row in report["rows"] if row["classification"] == "mute_for_hik"]
    edges: List[Dict[str, Any]] = []
    seen = set()
    for row in mute_rows:
        for destination in row["writes"]:
            sources = cmds.listConnections(destination, source=True, destination=False, plugs=True) or []
            matched = [str(source) for source in sources if str(source).rsplit(".", 1)[0] == str(row["node"])]
            if len(matched) != 1:
                raise RuntimeError(f"Expected one {row['node']} writer for {destination}, got {matched}")
            edge = (matched[0], str(destination))
            if edge in seen:
                continue
            seen.add(edge)
            edges.append(
                {
                    "source": edge[0],
                    "destination": edge[1],
                    "node": str(row["node"]),
                    "nodeType": str(row["nodeType"]),
                    "destinationValue": _json_attribute_value(cmds.getAttr(edge[1])),
                    "baselineIncomingSources": _incoming_sources(edge[1]),
                }
            )
    state: Dict[str, Any] = {
        "enabled": True,
        "classificationCounts": report["counts"],
        "muteForHikNodes": [
            {"node": str(row["node"]), "nodeType": str(row["nodeType"]), "classification": row["classification"]}
            for row in mute_rows
        ],
        "edges": sorted(edges, key=lambda item: (item["destination"], item["source"])),
        "isolatedEdgeCount": len(edges),
        "expectedIsolatedEdgeCount": EXPECTED_TPOSE_ISOLATED_EDGE_COUNT,
        "topologyIsolated": False,
        "topologyRestored": False,
        "topologyMismatches": [],
        "reconnectErrors": [],
    }
    if len(edges) != EXPECTED_TPOSE_ISOLATED_EDGE_COUNT:
        raise RuntimeError(
            f"T-pose expected {EXPECTED_TPOSE_ISOLATED_EDGE_COUNT} isolated writer edges, got {len(edges)}"
        )
    disconnected: List[Dict[str, Any]] = []
    try:
        for edge in state["edges"]:
            if not _edge_connected(edge):
                raise RuntimeError(f"Writer edge disappeared before isolation: {edge['source']} -> {edge['destination']}")
            cmds.disconnectAttr(edge["source"], edge["destination"])
            disconnected.append(edge)
        destinations = sorted({str(edge["destination"]) for edge in state["edges"]})
        mismatches = []
        for destination in destinations:
            baseline = next(edge["baselineIncomingSources"] for edge in state["edges"] if edge["destination"] == destination)
            isolated = sorted(
                set(baseline)
                - {str(edge["source"]) for edge in state["edges"] if edge["destination"] == destination}
            )
            actual = _incoming_sources(destination)
            if actual != isolated:
                mismatches.append({"destination": destination, "expected": isolated, "actual": actual})
        state["topologyMismatches"] = mismatches
        state["topologyIsolated"] = not mismatches
        if not state["topologyIsolated"]:
            raise RuntimeError("T-pose writer isolation topology verification failed")
    except Exception:
        for edge in disconnected:
            if not _edge_connected(edge):
                cmds.connectAttr(edge["source"], edge["destination"], force=False)
        raise
    return state


def _reconnect_hik_writer_edges(state: Dict[str, Any]) -> Dict[str, Any]:
    """Restore each exact isolated source/destination edge and verify topology."""
    errors = []
    for edge in state.get("edges", []):
        try:
            if not _edge_connected(edge):
                cmds.connectAttr(edge["source"], edge["destination"], force=False)
        except Exception as exc:
            errors.append({"source": edge["source"], "destination": edge["destination"], "error": str(exc)})
    mismatches = []
    for edge in state.get("edges", []):
        actual = _incoming_sources(str(edge["destination"]))
        baseline = list(edge["baselineIncomingSources"])
        if actual != baseline:
            mismatches.append({"destination": edge["destination"], "expected": baseline, "actual": actual})
    state["reconnectErrors"] = errors
    state["topologyMismatches"] = mismatches
    state["topologyRestored"] = not errors and not mismatches
    if not state["topologyRestored"]:
        raise RuntimeError("T-pose writer topology restore failed")
    return state


def _force_constraint_evaluation(frame: int) -> None:
    """Dirty and evaluate native constraint outputs after topology changes."""
    cmds.currentTime(int(frame), edit=True)
    cmds.refresh(force=True)
    for node in cmds.ls(type="mmdCcdIk") or []:
        for index in cmds.getAttr(f"{node}.outputRotate", multiIndices=True) or []:
            cmds.getAttr(f"{node}.outputRotate[{int(index)}]")


def _apply_t_pose(
    source_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the same horizontal arm directions to source and target copies."""
    source_chains = source_snapshot["chains"]
    target_chains = target_snapshot["chains"]
    rows = []
    for side in ("LeftArm", "RightArm"):
        source_joint, source_child = source_chains[side]["joint"], source_chains[side]["child"]
        target_joint, target_child = target_chains[side]["joint"], target_chains[side]["child"]
        source_direction = _joint_world_direction(source_joint, source_child)
        horizontal = om.MVector(float(source_direction.x), 0.0, float(source_direction.z))
        if _vector_length(horizontal) <= 1.0e-12:
            raise RuntimeError(f"Cannot derive horizontal T-pose direction for {side}")
        target_unit = _unit_vector(horizontal)
        source_apply = _set_joint_world_direction(source_joint, source_child, target_unit)
        target_apply = _set_joint_world_direction(target_joint, target_child, target_unit)
        source_after = _direction_evidence(source_joint, source_child)
        target_after = _direction_evidence(target_joint, target_child)
        source_unit = _unit_vector(_joint_world_direction(source_joint, source_child))
        target_unit_after = _unit_vector(_joint_world_direction(target_joint, target_child))
        direction_residual = _vector_length(source_unit - target_unit_after)
        rows.append(
            {
                "hikBone": side,
                "source": {"apply": source_apply, "direction": source_after},
                "target": {"apply": target_apply, "direction": target_after},
                "directionResidual": direction_residual,
                "passed": max(source_after["absoluteElevationRadians"], target_after["absoluteElevationRadians"]) <= STANCE_ELEVATION_TOLERANCE
                and direction_residual <= STANCE_DIRECTION_TOLERANCE,
            }
        )
    evidence = {
        "mode": "t-pose",
        "method": "world-space shortest arc to source-derived horizontal projection; Maya world-matrix setter preserving jointOrient/rotateAxis",
        "elevationToleranceRadians": STANCE_ELEVATION_TOLERANCE,
        "directionTolerance": STANCE_DIRECTION_TOLERANCE,
        "rows": rows,
        "passed": all(row["passed"] for row in rows),
        "restore": None,
    }
    return evidence


def _matrix(plug: str) -> om.MMatrix:
    return om.MMatrix(cmds.getAttr(plug))


def _matrix_values(value: om.MMatrix) -> List[float]:
    return [float(value[index]) for index in range(16)]


def _matrix_error(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float]:
    differences = [abs(float(a) - float(b)) for a, b in zip(left, right)]
    return max(differences, default=0.0), statistics.fmean(differences) if differences else 0.0


def _skin_matrix(joint: str, skin: str, logical_index: int) -> List[float]:
    bind_pre = _matrix(f"{skin}.bindPreMatrix[{logical_index}]")
    world = _matrix(f"{joint}.worldMatrix[0]")
    return _matrix_values(bind_pre * world)


def _world_translation(joint: str) -> Tuple[float, float, float]:
    world = _matrix(f"{joint}.worldMatrix[0]")
    return float(world[12]), float(world[13]), float(world[14])


def _vector_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _identity_residual(value: Sequence[float]) -> float:
    identity = [1.0 if row == column else 0.0 for row in range(4) for column in range(4)]
    return max((abs(float(actual) - expected) for actual, expected in zip(value, identity)), default=0.0)


def _bind_identity_evidence(slot_maps: Mapping[str, Mapping[int, Mapping[str, Any]]]) -> Dict[str, Any]:
    evidence = {}
    for label, slots in slot_maps.items():
        rows = []
        for slot, info in sorted(slots.items()):
            bind_pre = _matrix(f"{info['skin']}.bindPreMatrix[{info['logicalIndex']}]")
            frame_product = _matrix_values(bind_pre * _matrix(f"{info['joint']}.worldMatrix[0]"))
            rows.append(
                {
                    "hikSlot": int(slot),
                    "hikBone": str(info["hikBone"]),
                    "joint": str(info["joint"]),
                    "frame0SkinIdentityResidual": _identity_residual(frame_product),
                }
            )
        evidence[label] = {
            "matrixOrder": "bindPreMatrix[index] * joint.worldMatrix[0]",
            "count": len(rows),
            "maxFrame0SkinIdentityResidual": max((row["frame0SkinIdentityResidual"] for row in rows), default=0.0),
            "rows": rows,
        }
    return evidence


def _fidelity_pattern(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    for row in rows:
        bone = str(row.get("hikBone", ""))
        if "Hips" in bone or "Spine" in bone:
            region = "root_waist"
        elif "Arm" in bone or "Hand" in bone or "ForeArm" in bone:
            region = "upper_limb"
        elif "Leg" in bone or "Foot" in bone or "Toe" in bone or "UpLeg" in bone:
            region = "lower_limb"
        else:
            region = "other"
        counts[region] = counts.get(region, 0) + 1
    return {"top10RegionCounts": dict(sorted(counts.items())), "note": "Top rows are grouped by HIK slot family; inspect jointOrient/stance in rows."}


def _fidelity_bundle(matrix: Mapping[str, Any], quaternion: Mapping[str, Any]) -> Dict[str, Any]:
    """Group the JO-aware matrix/quaternion residuals with region diagnostics."""
    return {
        "skinMatrix": dict(matrix),
        "localQuaternion": dict(quaternion),
        "fidelityPattern": _fidelity_pattern(matrix.get("worst", ())),
    }


def _local_quaternion(joint: str) -> Tuple[float, float, float, float]:
    """Extract local orientation from Maya's evaluated local matrix."""
    transform = om.MTransformationMatrix(_matrix(f"{joint}.matrix"))
    try:
        quaternion = transform.rotation(asQuaternion=True)
    except TypeError:
        quaternion = transform.rotation().asQuaternion()
    return float(quaternion.x), float(quaternion.y), float(quaternion.z), float(quaternion.w)


def _quaternion_angle(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm <= 1.0e-12 or right_norm <= 1.0e-12:
        raise RuntimeError("Zero local quaternion encountered")
    dot = abs(sum(float(a) * float(b) for a, b in zip(left, right)) / (left_norm * right_norm))
    return 2.0 * math.acos(max(-1.0, min(1.0, dot)))


def _quaternion_region(hik_bone: str) -> str:
    """Classify each HIK slot into one mutually exclusive residual region."""
    name = str(hik_bone)
    if "Roll" in name:
        return "roll"
    if any(token in name for token in ("Index", "Middle", "Ring", "Pinky", "Thumb")):
        return "finger"
    return "body"


def _quaternion_slot_diagnostics(
    source_slots: Mapping[int, Mapping[str, Any]],
    target_slots: Mapping[int, Mapping[str, Any]],
    samples: Mapping[int, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Summarize every slot's sampled local quaternion deltas and coverage."""
    per_slot = []
    category_values: Dict[str, List[float]] = {"body": [], "finger": [], "roll": []}
    category_slots: Dict[str, List[int]] = {"body": [], "finger": [], "roll": []}
    for slot in sorted(source_slots):
        info = source_slots[slot]
        category = _quaternion_region(str(info["hikBone"]))
        rows = [dict(row) for row in samples.get(slot, ())]
        values = [float(row["residualRadians"]) for row in rows]
        max_value = max(values, default=0.0)
        max_row = max(rows, key=lambda row: float(row["residualRadians"]), default=None)
        mean_value = statistics.fmean(values) if values else 0.0
        variance = statistics.pvariance(values) if values else 0.0
        category_slots[category].append(int(slot))
        category_values[category].extend(values)
        per_slot.append(
            {
                "hikSlot": int(slot),
                "hikBone": str(info["hikBone"]),
                "sourceJoint": str(info["joint"]),
                "targetJoint": str(target_slots[slot]["joint"]),
                "category": category,
                "sampleCount": len(rows),
                "samples": rows,
                "maxResidualRadians": max_value,
                "maxResidualDegrees": math.degrees(max_value),
                "meanResidualRadians": mean_value,
                "meanResidualDegrees": math.degrees(mean_value),
                "populationVarianceRadiansSquared": variance,
                "populationStddevRadians": math.sqrt(variance),
                "maxFrame": int(max_row["frame"]) if max_row is not None else None,
            }
        )
    all_slots = sorted(int(slot) for slot in source_slots)
    duplicate_slots = sorted(slot for slot in set(all_slots) if all_slots.count(slot) > 1)
    category_summary = {}
    for category in ("body", "finger", "roll"):
        values = category_values[category]
        category_summary[category] = {
            "slotCount": len(category_slots[category]),
            "sampleCount": len(values),
            "aggregateMaxResidualRadians": max(values, default=0.0),
            "aggregateMaxResidualDegrees": math.degrees(max(values, default=0.0)),
            "aggregateMeanResidualRadians": statistics.fmean(values) if values else 0.0,
            "aggregateMeanResidualDegrees": math.degrees(statistics.fmean(values)) if values else 0.0,
            "aggregatePopulationVarianceRadiansSquared": statistics.pvariance(values) if values else 0.0,
            "slotIds": sorted(category_slots[category]),
        }
    return {
        "slotCount": len(all_slots),
        "coveredSlotCount": len(set(all_slots)),
        "duplicateSlotIds": duplicate_slots,
        "categoryCounts": {category: len(category_slots[category]) for category in ("body", "finger", "roll")},
        "categories": category_summary,
        "perSlot": per_slot,
    }


def _capture_slots(slot_map: Mapping[int, Mapping[str, Any]], *, skin: bool) -> Dict[int, Any]:
    captured: Dict[int, Any] = {}
    for slot, info in slot_map.items():
        joint = info["joint"]
        if skin:
            captured[int(slot)] = _skin_matrix(joint, info["skin"], info["logicalIndex"])
        else:
            captured[int(slot)] = _local_quaternion(joint)
    return captured


def _frame_matrix_fidelity(
    source_slots: Mapping[int, Mapping[str, Any]],
    target_slots: Mapping[int, Mapping[str, Any]],
    frames: Sequence[int],
) -> Dict[str, Any]:
    rows = []
    worst = []
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        source_values = _capture_slots(source_slots, skin=True)
        target_values = _capture_slots(target_slots, skin=True)
        errors = []
        for slot in sorted(source_values):
            source_matrix = source_values[slot]
            target_matrix = target_values[slot]
            max_error, mean_error = _matrix_error(source_matrix, target_matrix)
            translation_indices = (12, 13, 14)
            translation_error = max(
                (abs(source_matrix[index] - target_matrix[index]) for index in translation_indices),
                default=0.0,
            )
            rotation_scale_error = max(
                (abs(source_matrix[index] - target_matrix[index]) for index in range(16) if index not in translation_indices),
                default=0.0,
            )
            errors.append((max_error, mean_error))
            info = source_slots[slot]
            worst.append(
                {
                    "frame": int(frame),
                    "hikSlot": int(slot),
                    "hikBone": str(info["hikBone"]),
                    "sourceJoint": str(info["joint"]),
                    "targetJoint": str(target_slots[slot]["joint"]),
                    "residual": max_error,
                    "meanElementResidual": mean_error,
                    "translationResidual": translation_error,
                    "rotationScaleResidual": rotation_scale_error,
                    "sourceMatrix": source_matrix,
                    "targetMatrix": target_matrix,
                }
            )
        max_error = max((item[0] for item in errors), default=float("inf"))
        mean_error = statistics.fmean(item[1] for item in errors) if errors else float("inf")
        translation_errors = [item["translationResidual"] for item in worst if item["frame"] == frame]
        rotation_scale_errors = [item["rotationScaleResidual"] for item in worst if item["frame"] == frame]
        rows.append({"frame": int(frame), "max": max_error, "mean": mean_error, "count": len(errors)})
        rows[-1].update(
            {
                "translationMax": max(translation_errors, default=0.0),
                "rotationScaleMax": max(rotation_scale_errors, default=0.0),
            }
        )
    maxima = [row["max"] for row in rows]
    means = [row["mean"] for row in rows]
    return {
        "frames": rows,
        "max": max(maxima, default=float("inf")),
        "mean": statistics.fmean(means) if means else float("inf"),
        "commonInfluenceCount": len(source_slots),
        "worst": sorted(worst, key=lambda item: item["residual"], reverse=True)[:10],
    }


def _frame_quaternion_fidelity(
    source_slots: Mapping[int, Mapping[str, Any]],
    target_slots: Mapping[int, Mapping[str, Any]],
    frames: Sequence[int],
    *,
    include_slot_diagnostics: bool = False,
) -> Dict[str, Any]:
    rows = []
    worst = []
    slot_samples: Dict[int, List[Dict[str, Any]]] = {int(slot): [] for slot in source_slots}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        source_values = _capture_slots(source_slots, skin=False)
        target_values = _capture_slots(target_slots, skin=False)
        errors = []
        for slot in sorted(source_values):
            residual = _quaternion_angle(source_values[slot], target_values[slot])
            errors.append(residual)
            info = source_slots[slot]
            slot_samples[int(slot)].append(
                {
                    "frame": int(frame),
                    "residualRadians": residual,
                    "residualDegrees": math.degrees(residual),
                }
            )
            worst.append(
                {
                    "frame": int(frame),
                    "hikSlot": int(slot),
                    "hikBone": str(info["hikBone"]),
                    "sourceJoint": str(info["joint"]),
                    "targetJoint": str(target_slots[slot]["joint"]),
                    "residualRadians": residual,
                    "residualDegrees": math.degrees(residual),
                    "sourceQuaternion": list(source_values[slot]),
                    "targetQuaternion": list(target_values[slot]),
                }
            )
        rows.append({"frame": int(frame), "max": max(errors, default=float("inf")), "mean": statistics.fmean(errors) if errors else float("inf"), "count": len(errors)})
    twist_slots = {slot for slot, info in source_slots.items() if "Roll" in str(info["hikBone"])}
    twist_rows = []
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        source_values = _capture_slots({slot: source_slots[slot] for slot in twist_slots}, skin=False)
        target_values = _capture_slots({slot: target_slots[slot] for slot in twist_slots}, skin=False)
        errors = [_quaternion_angle(source_values[slot], target_values[slot]) for slot in sorted(twist_slots)]
        twist_rows.append({"frame": int(frame), "max": max(errors, default=float("inf")), "mean": statistics.fmean(errors) if errors else float("inf"), "count": len(errors)})
    result = {
        "frames": rows,
        "max": max((row["max"] for row in rows), default=float("inf")),
        "mean": statistics.fmean(row["mean"] for row in rows) if rows else float("inf"),
        "count": len(source_slots),
        "worst": sorted(worst, key=lambda item: item["residualRadians"], reverse=True)[:10],
        "twistRoll": {
            "slots": sorted(twist_slots),
            "frames": twist_rows,
            "max": max((row["max"] for row in twist_rows), default=0.0),
            "mean": statistics.fmean(row["mean"] for row in twist_rows) if twist_rows else 0.0,
        },
    }
    if include_slot_diagnostics:
        result["slotDiagnostics"] = _quaternion_slot_diagnostics(source_slots, target_slots, slot_samples)
    return result


def _capture_target_sequence(target_slots: Mapping[int, Mapping[str, Any]], frames: Sequence[int]) -> Dict[int, List[float]]:
    captured: Dict[int, List[float]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        for slot, info in target_slots.items():
            captured.setdefault(int(frame), []).extend(_skin_matrix(info["joint"], info["skin"], info["logicalIndex"]))
    return captured


def _sequence_error(first: Mapping[int, Sequence[float]], second: Mapping[int, Sequence[float]]) -> float:
    return max(
        (_matrix_error(first[frame], second[frame])[0] for frame in sorted(set(first) & set(second))),
        default=float("inf"),
    )


def _determinism_report(target_slots: Mapping[int, Mapping[str, Any]], frames: Sequence[int]) -> Dict[str, Any]:
    sequential = _capture_target_sequence(target_slots, frames)
    repeated = _capture_target_sequence(target_slots, frames)
    random_order = list(reversed(frames[::2])) + list(frames[1::2])
    random_seek = _capture_target_sequence(target_slots, random_order)
    return {
        "sequentialFrames": list(frames),
        "randomSeekOrder": random_order,
        "repeatedSequentialMaxError": _sequence_error(sequential, repeated),
        "randomSeekMaxError": _sequence_error(sequential, random_seek),
        "tolerance": DETERMINISM_TOLERANCE,
        "passed": _sequence_error(sequential, repeated) <= DETERMINISM_TOLERANCE
        and _sequence_error(sequential, random_seek) <= DETERMINISM_TOLERANCE,
    }


def _motion_evidence(source_slots: Mapping[int, Mapping[str, Any]], frames: Sequence[int]) -> Dict[str, Any]:
    by_name = {str(info["hikBone"]): info["joint"] for info in source_slots.values()}
    evidence = {}
    for label, candidates in (("root", ("Hips",)), ("waist", ("Spine", "Spine1"))):
        joint = next((by_name[name] for name in candidates if name in by_name), None)
        if not joint:
            evidence[label] = {"joint": None, "maxDelta": 0.0, "passed": False}
            continue
        values = []
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            values.append(_world_translation(joint))
        max_delta = max((_vector_distance(values[0], value) for value in values[1:]), default=0.0)
        evidence[label] = {"joint": joint, "maxDelta": max_delta, "passed": max_delta > MOTION_TOLERANCE}
    return evidence


def _h_out_and_excluded(source_root: str, source_slots: Mapping[int, Mapping[str, Any]]) -> Dict[str, Any]:
    h_joints = {_long_name(info["joint"]) for info in source_slots.values()}
    h_out = []
    joints = cmds.listRelatives(source_root, allDescendents=True, type="joint", fullPath=True) or []
    for joint in joints:
        long_joint = _long_name(joint)
        if long_joint in h_joints:
            continue
        animated = any(
            any(str(cmds.nodeType(str(source).rsplit(".", 1)[0])).startswith("animCurve") for source in (cmds.listConnections(f"{joint}.{channel}", source=True, destination=False, plugs=True) or []))
            for channel in CHANNELS
        )
        if animated:
            h_out.append(long_joint)
    return {
        "hOutAnimatedBones": sorted(h_out),
        "hOutAnimatedCount": len(h_out),
        "excluded": {
            "morphNodes": sorted(set(cmds.ls(type="blendShape") or []) | set(cmds.ls(type="mmdMorphController") or [])),
            "physicsNodes": sorted(set(cmds.ls(type="mmdPhysicsBoneDriver") or [])),
            "note": "Morph and physics are outside the HIK primary round-trip claim.",
        },
    }


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "status": "fail",
        "evaluationMode": args.evaluation_mode,
        "hikProfile": args.hik_profile,
        "characterizationStanceMode": args.characterization_stance,
        "characterizationOrder": "bind_before_motion",
    }
    maya.standalone.initialize(name="python")
    isolation_state: Optional[Dict[str, Any]] = None
    try:
        _load_plugin()
        cmds.evaluationManager(mode=args.evaluation_mode)
        pmx = Path(args.pmx).resolve()
        vmd = Path(args.vmd).resolve()
        if not pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(f"S5 fixtures not found: pmx={pmx} vmd={vmd}")
        frames = list(range(int(args.start), int(args.end) + 1))
        source_root = _load_model(pmx, setup_rig=False)
        target_root = _load_model(pmx, setup_rig=True)
        source_original_result = resolve_scene_humanik_assignments(source_root)
        target_original_result = resolve_scene_humanik_assignments(target_root)
        source_result = _profile_result(source_original_result, args.hik_profile)
        target_result = _profile_result(target_original_result, args.hik_profile)
        assignment_profile = _assignment_profile_evidence(
            args.hik_profile,
            source_original_result,
            target_original_result,
            source_result,
            target_result,
        )
        source_slots, target_slots = _common_skin_slots(source_result, target_result, source_root, target_root)
        if not source_slots:
            raise RuntimeError("S5 has no common HIK skin influences")
        target_joints = tuple(item.joint for item in target_result.assignments)
        if args.characterization_stance == "t-pose":
            source_stance_snapshot = _snapshot_stance(source_root, source_result, source_slots)
            target_stance_snapshot = _snapshot_stance(target_root, target_result, target_slots)
            isolation_state = _isolate_hik_writer_edges(target_joints)
        if args.characterization_stance == "t-pose":
            stance_evidence = _apply_t_pose(source_stance_snapshot, target_stance_snapshot)
        else:
            stance_evidence = {
                "mode": "bind",
                "applied": False,
                "passed": True,
            }
            source_stance_snapshot = None
            target_stance_snapshot = None
        payload["characterizationStance"] = stance_evidence
        if isolation_state is not None:
            stance_evidence["writerIsolation"] = isolation_state
        if not stance_evidence["passed"]:
            raise RuntimeError("T-pose stance agreement failed before HumanIK characterization")
        cmds.currentTime(frames[0], edit=True)
        cmds.refresh(force=True)
        pre_hik_matrix = _frame_matrix_fidelity(source_slots, target_slots, [frames[0]])
        pre_hik_quaternion = _frame_quaternion_fidelity(source_slots, target_slots, [frames[0]])
        pre_hik_bind_identity = _bind_identity_evidence({"source": source_slots, "target": target_slots})
        source_character = create_humanik_definition(source_result, name_hint="MMDToolsS5_Source", update_ui=False)
        target_character = create_humanik_definition(target_result, name_hint="MMDToolsS5_Target", update_ui=False)
        lock_humanik_definition(source_character)
        lock_humanik_definition(target_character)
        if args.characterization_stance == "t-pose":
            source_restore = _restore_stance(source_stance_snapshot)
            target_restore = _restore_stance(target_stance_snapshot)
            stance_evidence["restore"] = {
                "phase": "after_lock_before_vmd_before_reconnect",
                "interpretation": "Original pose restoration is validated before source VMD import while reviewed target writer edges remain isolated.",
                "source": source_restore,
                "target": target_restore,
                "passed": source_restore["passed"] and target_restore["passed"],
            }
            payload["characterizationStance"] = stance_evidence
            if not stance_evidence["restore"]["passed"]:
                raise RuntimeError("T-pose stance restore failed before source VMD import")
        _load_motion(vmd, pmx, source_root)
        if isolation_state is not None:
            _reconnect_hik_writer_edges(isolation_state)
            _force_constraint_evaluation(frames[0])
            post_reconnect = _common_skin_snapshot_evaluation(target_stance_snapshot)
            post_reconnect.pop("passed", None)
            stance_evidence["postReconnectConstraintEvaluation"] = {
                "phase": "after_source_vmd_before_s3_preview",
                "interpretation": "Reconnecting the original target constraints immediately before S3 preview re-enables their evaluation; this residual is not pose-restoration evidence.",
                **post_reconnect,
            }
            payload["characterizationStance"] = stance_evidence
        ownership = classify_humanik_constraints(collect_humanik_constraint_facts(), target_joints)
        preview = begin_humanik_target_preview("mmd-tools:s5:roundtrip", target_character, source_character, ownership, target_joints)
        live_matrix = _frame_matrix_fidelity(source_slots, target_slots, frames)
        live_quaternion = _frame_quaternion_fidelity(source_slots, target_slots, frames)
        bake = bake_humanik_target_preview(preview, target_joints, frames[0], frames[-1], mel_module=mel)
        baked_matrix = _frame_matrix_fidelity(source_slots, target_slots, frames)
        cmds.currentTime(frames[0], edit=True)
        cmds.refresh(force=True)
        bind_identity = _bind_identity_evidence({"source": source_slots, "target": target_slots})
        quaternion_source = {slot: {**info, "joint": info["joint"]} for slot, info in source_slots.items()}
        quaternion_target = {slot: {**target_slots[slot], "hikBone": source_slots[slot]["hikBone"]} for slot in source_slots}
        baked_quaternion = _frame_quaternion_fidelity(
            quaternion_source,
            quaternion_target,
            frames,
            include_slot_diagnostics=True,
        )
        legacy_baked_quaternion = dict(baked_quaternion)
        legacy_baked_quaternion.pop("slotDiagnostics", None)
        determinism = _determinism_report(target_slots, frames)
        motion = _motion_evidence(source_slots, frames)
        h_out = _h_out_and_excluded(source_root, source_slots)
        payload.update(
            {
                "mayaVersion": cmds.about(version=True),
                "frameRange": {"start": frames[0], "end": frames[-1]},
                "sourceRoot": source_root,
                "targetRoot": target_root,
                "sourceCharacter": source_character,
                "targetCharacter": target_character,
                "ownershipCounts": ownership["counts"],
                "assignmentProfile": assignment_profile,
                "commonSlotCategories": _common_slot_categories(source_slots),
                "targetAssignmentCount": len(target_result.assignments),
                "commonSkinInfluenceCount": len(source_slots),
                "motion": motion,
                "preHikSourceVsTarget": {
                    **_fidelity_bundle(pre_hik_matrix, pre_hik_quaternion),
                    "sourceSetupRig": False,
                    "targetSetupRig": True,
                    "frame": int(frames[0]),
                },
                "preHikBindMatrixEvidence": pre_hik_bind_identity,
                "liveVsSource": _fidelity_bundle(live_matrix, live_quaternion),
                "bakedVsSource": _fidelity_bundle(baked_matrix, baked_quaternion),
                "skinMatrixFidelity": baked_matrix,
                "bindMatrixEvidence": bind_identity,
                "localQuaternionFidelity": legacy_baked_quaternion,
                "fidelityPattern": _fidelity_pattern(baked_matrix["worst"]),
                "determinism": determinism,
                "hOutAndExcluded": h_out,
                "bake": bake.to_dict(),
                "bakeWarnings": list(bake.warnings),
                "acceptanceThresholds": {
                    "matrixMax": MATRIX_MAX_TOLERANCE,
                    "matrixMean": MATRIX_MEAN_TOLERANCE,
                    "quaternionMaxRadians": QUATERNION_MAX_TOLERANCE,
                    "quaternionMaxDegrees": math.degrees(QUATERNION_MAX_TOLERANCE),
                    "determinismMax": DETERMINISM_TOLERANCE,
                },
            }
        )
        payload["status"] = "pass" if all(
            (
                motion["root"]["passed"],
                motion["waist"]["passed"],
                bool(source_slots),
                baked_matrix["max"] <= MATRIX_MAX_TOLERANCE,
                baked_matrix["mean"] <= MATRIX_MEAN_TOLERANCE,
                baked_quaternion["max"] <= QUATERNION_MAX_TOLERANCE,
                determinism["passed"],
                bake.pre_bake_journal_restored,
            )
        ) else "fail"
        if payload["status"] != "pass":
            raise RuntimeError(
                "HumanIK S5 round-trip acceptance failed: "
                f"matrixMax={baked_matrix['max']} quaternionMax={baked_quaternion['max']} "
                f"determinism={determinism['passed']}"
            )
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": payload["status"], "mode": args.evaluation_mode, "common": len(source_slots), "matrixMax": baked_matrix["max"], "quatMax": baked_quaternion["max"]}, sort_keys=True))
        return 0
    except Exception as exc:
        if isolation_state is not None and not isolation_state.get("topologyRestored", False):
            try:
                _reconnect_hik_writer_edges(isolation_state)
            except Exception as reconnect_exc:
                isolation_state["exceptionReconnectError"] = str(reconnect_exc)
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
