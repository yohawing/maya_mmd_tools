"""Maya 2024 S5 HumanIK self-retarget round-trip gate.

The smoke imports one PMX/VMD fixture twice, uses the first copy as a direct
HumanIK SOURCE, bakes a TARGET preview through the S4 boundary, and compares
the resulting deformation matrices and local quaternions.  Determinism is
measured separately from source-to-target fidelity for sequential and random
seek playback.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel
import maya.standalone

from mmd_tools.core.humanik_bake import bake_humanik_target_preview
from mmd_tools.core.humanik_builder import (
    create_humanik_definition_from_scene,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
)
from mmd_tools.core.humanik_preview import begin_humanik_target_preview


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
) -> Dict[str, Any]:
    rows = []
    worst = []
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
    return {
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
        "characterizationOrder": "bind_before_motion",
    }
    maya.standalone.initialize(name="python")
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
        source_result = resolve_scene_humanik_assignments(source_root)
        target_result = resolve_scene_humanik_assignments(target_root)
        source_slots, target_slots = _common_skin_slots(source_result, target_result, source_root, target_root)
        if not source_slots:
            raise RuntimeError("S5 has no common HIK skin influences")
        cmds.currentTime(frames[0], edit=True)
        cmds.refresh(force=True)
        pre_hik_matrix = _frame_matrix_fidelity(source_slots, target_slots, [frames[0]])
        pre_hik_quaternion = _frame_quaternion_fidelity(source_slots, target_slots, [frames[0]])
        pre_hik_bind_identity = _bind_identity_evidence({"source": source_slots, "target": target_slots})
        source_character = create_humanik_definition_from_scene(source_root, name_hint="MMDToolsS5_Source", update_ui=False)
        target_character = create_humanik_definition_from_scene(target_root, name_hint="MMDToolsS5_Target", update_ui=False)
        lock_humanik_definition(source_character)
        lock_humanik_definition(target_character)
        _load_motion(vmd, pmx, source_root)
        target_joints = tuple(item.joint for item in target_result.assignments)
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
        baked_quaternion = _frame_quaternion_fidelity(quaternion_source, quaternion_target, frames)
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
                "localQuaternionFidelity": baked_quaternion,
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
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
