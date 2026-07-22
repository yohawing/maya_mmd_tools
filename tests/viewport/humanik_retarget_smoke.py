"""Maya 2024 mayapy smoke for direct HumanIK retarget S0 evidence.

The smoke imports source/target PMX fixtures and a source VMD, creates HIK
definitions without UI calls, locks both definitions, connects the target
through ``hikSetCharacterInput``, and writes VMD propagation, deterministic
connection/writer, and root-locomotion evidence. It intentionally does not
mute constraints, bake animation, or create a proxy skeleton.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any, Dict, Optional

import maya.cmds as cmds
import maya.standalone

from mmd_tools.core.humanik_builder import (
    create_humanik_definition_from_scene,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_retarget import (
    build_humanik_writer_report,
    connect_humanik_source,
    diff_humanik_connections,
    snapshot_humanik_connections,
    verify_root_locomotion,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke direct HumanIK retarget evidence under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument(
        "--pmx-base64",
        default=None,
        help="UTF-8/base64 source-PMX path; takes precedence over --pmx.",
    )
    parser.add_argument(
        "--target-pmx",
        default=None,
        help="Optional target PMX; defaults to --pmx for self-retarget coverage.",
    )
    parser.add_argument(
        "--target-pmx-base64",
        default=None,
        help="UTF-8/base64 target-PMX path; takes precedence over --target-pmx.",
    )
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument(
        "--vmd-base64",
        default=None,
        help="UTF-8/base64 source-VMD path; takes precedence over --vmd.",
    )
    parser.add_argument("--out", default="build/reports/humanik_retarget_smoke.json")
    parser.add_argument("--name-prefix", default="MMDToolsS0_")
    parser.add_argument("--translation", default="1,0,0", help="Root probe translation as x,y,z")
    parser.add_argument("--tolerance", type=float, default=1.0e-4)
    parser.add_argument(
        "--motion-frames",
        default="0,30,60",
        help="Comma-separated source-VMD frames used to prove TARGET motion propagation.",
    )
    parser.add_argument(
        "--evaluation-modes",
        default="dg,serial,parallel",
        help="Comma-separated Maya evaluation modes (dg maps to off)",
    )
    args = parser.parse_args()
    for text_option, encoded_option in (
        ("pmx", "pmx_base64"),
        ("target_pmx", "target_pmx_base64"),
        ("vmd", "vmd_base64"),
    ):
        encoded_value = getattr(args, encoded_option)
        if encoded_value:
            setattr(args, text_option, base64.b64decode(encoded_value).decode("utf-8"))
    return args


def _import_model(path: Path, *, use_namespace: bool) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": use_namespace,
            "setup_rig": False,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"MMD model import failed: {path}")
    return str(root)


def _import_motion(path: Path, target_model: str, pmx: Path) -> None:
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
        raise RuntimeError(f"VMD motion import failed: {path}")


def _vmd_preflight(path: Path) -> Dict[str, Any]:
    """Capture VMD content counts before Maya applies the file."""
    from mmd_tools.core.vmd_data import VmdData

    data = VmdData().parse_file(str(path))
    bone_frames = list(getattr(data, "bone_frames", []) or [])
    bone_names = sorted({str(frame.bone_name) for frame in bone_frames})
    frame_numbers = [int(frame.frame_number) for frame in bone_frames]
    return {
        "headerModelName": str(getattr(getattr(data, "header", None), "model_name", "")),
        "boneFrameCount": len(bone_frames),
        "boneNameCount": len(bone_names),
        "boneNameSample": bone_names[:24],
        "boneFrameRange": [min(frame_numbers), max(frame_numbers)] if frame_numbers else None,
        "morphFrameCount": len(getattr(data, "morph_frames", []) or []),
        "cameraFrameCount": len(getattr(data, "camera_frames", []) or []),
        "lightFrameCount": len(getattr(data, "light_frames", []) or []),
        "shadowFrameCount": len(getattr(data, "shadow_frames", []) or []),
        "ikShowHideFrameCount": len(getattr(data, "ik_show_hide_frames", []) or []),
        "hasBoneMotion": bool(bone_frames),
    }


def _find_assignment(result, hik_bone: str):
    for assignment in result.assignments:
        if assignment.hik_bone == hik_bone:
            return assignment
    raise RuntimeError(f"Required HIK assignment is missing: {hik_bone}")


def _parse_translation(value: str):
    values = tuple(float(part.strip()) for part in value.split(","))
    if len(values) != 3:
        raise ValueError("--translation must be x,y,z")
    return values


def _parse_evaluation_modes(value: str):
    aliases = {"dg": "off", "off": "off", "serial": "serial", "parallel": "parallel"}
    modes = []
    for item in value.split(","):
        label = item.strip().lower()
        if label not in aliases:
            raise ValueError(f"Unsupported evaluation mode: {item}")
        mode = aliases[label]
        if mode not in modes:
            modes.append(mode)
    if not modes:
        raise ValueError("--evaluation-modes must contain at least one mode")
    return modes


def _parse_motion_frames(value: str):
    frames = []
    for item in value.split(","):
        frame = int(item.strip())
        if frame not in frames:
            frames.append(frame)
    if not frames:
        raise ValueError("--motion-frames must contain at least one frame")
    return frames


def _world_matrix(joint: str):
    return tuple(float(value) for value in cmds.xform(joint, query=True, worldSpace=True, matrix=True))


def _matrix_delta(reference, value) -> float:
    return max((abs(left - right) for left, right in zip(reference, value)), default=0.0)


def _motion_transfer_evidence(source_result, target_result, frames, tolerance: float):
    """Capture whether source VMD evaluation produces motion on the HIK target."""
    source_by_hik = {assignment.hik_bone: assignment.joint for assignment in source_result.assignments}
    target_by_hik = {assignment.hik_bone: assignment.joint for assignment in target_result.assignments}
    original_time = cmds.currentTime(query=True)
    rows = []
    try:
        for hik_bone in ("Hips", "Spine", "LeftArm", "RightArm"):
            source_joint = source_by_hik.get(hik_bone)
            target_joint = target_by_hik.get(hik_bone)
            if not source_joint or not target_joint:
                continue
            source_values = []
            target_values = []
            for frame in frames:
                cmds.currentTime(frame, edit=True)
                source_values.append(_world_matrix(source_joint))
                target_values.append(_world_matrix(target_joint))
            source_delta = max((_matrix_delta(source_values[0], value) for value in source_values[1:]), default=0.0)
            target_delta = max((_matrix_delta(target_values[0], value) for value in target_values[1:]), default=0.0)
            rows.append(
                {
                    "hikBone": hik_bone,
                    "sourceJoint": source_joint,
                    "targetJoint": target_joint,
                    "sourceMaxDelta": source_delta,
                    "targetMaxDelta": target_delta,
                    "sourceAnimated": source_delta > tolerance,
                    "targetAnimated": target_delta > tolerance,
                }
            )
    finally:
        cmds.currentTime(original_time, edit=True)
    return {
        "frames": list(frames),
        "rows": rows,
        "sourceAnimated": any(row["sourceAnimated"] for row in rows),
        "targetAnimated": any(row["targetAnimated"] for row in rows),
        "passed": bool(rows)
        and any(row["sourceAnimated"] for row in rows)
        and any(row["targetAnimated"] for row in rows),
    }


def _motion_transfer_by_mode(source_result, target_result, frames, tolerance: float, modes):
    """Run the source/target motion probe under every requested evaluator mode."""
    original_modes = cmds.evaluationManager(query=True, mode=True) or ["off"]
    reports = {}
    try:
        for mode in modes:
            cmds.evaluationManager(mode=mode)
            reports[mode] = _motion_transfer_evidence(source_result, target_result, frames, tolerance)
    finally:
        cmds.evaluationManager(mode=original_modes[0])
    return {
        "modes": reports,
        "passed": bool(reports) and all(report["passed"] for report in reports.values()),
    }


def _source_anim_curve_evidence(result) -> Dict[str, Any]:
    """Capture VMD animCurve nodes and direct HIK-joint channel connections."""
    channels = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
    rows = []
    curve_names = set()
    for assignment in result.assignments:
        joint = str(assignment.joint)
        for channel in channels:
            plug = f"{joint}.{channel}"
            incoming = cmds.listConnections(plug, source=True, destination=False, plugs=True) or []
            curves = []
            for source in incoming:
                node = str(source).rsplit(".", 1)[0]
                if not str(cmds.nodeType(node)).startswith("animCurve"):
                    continue
                curves.append(str(source))
                curve_names.add(node)
            if curves:
                rows.append(
                    {
                        "hikBone": str(assignment.hik_bone),
                        "hikIndex": int(assignment.hik_index),
                        "channel": channel,
                        "destination": plug,
                        "animCurvePlugs": sorted(curves),
                    }
                )
    scene_curves = sorted(str(node) for node in (cmds.ls(type="animCurve") or []))
    return {
        "sceneAnimCurveCount": len(scene_curves),
        "sourceHikAnimCurveCount": len(curve_names),
        "sourceHikDrivenChannelCount": len(rows),
        "sourceHikAnimCurveNodes": sorted(curve_names),
        "sourceHikDrivenChannels": rows,
    }


def _verify_locomotion_modes(
    source_hips: str,
    target_hips: str,
    target_groups,
    translation,
    tolerance: float,
    modes,
    source_model_root: Optional[str] = None,
):
    original_modes = cmds.evaluationManager(query=True, mode=True) or ["off"]
    reports = {}
    try:
        for mode in modes:
            cmds.evaluationManager(mode=mode)
            reports[mode] = verify_root_locomotion(
                source_hips,
                target_groups,
                translation=translation,
                tolerance=tolerance,
                observed_root_joint=target_hips,
                source_model_root=source_model_root,
            )
    finally:
        cmds.evaluationManager(mode=original_modes[0])
    return {
        "modes": reports,
        "passed": bool(reports)
        and all(report.get("supported", True) and report["passed"] for report in reports.values()),
    }


def _load_mmd_plugin() -> None:
    plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(plugin_path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(plugin_path), quiet=True)


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "status": "fail",
        "fixtures": {
            "sourcePmx": str(args.pmx),
            "targetPmx": str(args.target_pmx or args.pmx),
            "vmd": str(args.vmd),
        },
    }

    maya.standalone.initialize(name="python")
    try:
        payload["mayaVersion"] = cmds.about(version=True)
        _load_mmd_plugin()
        source_pmx = Path(args.pmx).resolve()
        target_pmx = Path(args.target_pmx or args.pmx).resolve()
        vmd = Path(args.vmd).resolve()
        if not source_pmx.is_file() or not target_pmx.is_file() or not vmd.is_file():
            raise FileNotFoundError(
                "S0 fixtures not found: "
                f"source_pmx={source_pmx} target_pmx={target_pmx} vmd={vmd}"
            )

        motion_frames = _parse_motion_frames(args.motion_frames)
        evaluation_modes = _parse_evaluation_modes(args.evaluation_modes)
        payload["vmdPreflight"] = _vmd_preflight(vmd)
        source_root = _import_model(source_pmx, use_namespace=True)
        _import_motion(vmd, source_root, source_pmx)
        source_result = resolve_scene_humanik_assignments(source_root)
        payload["sourceAnimCurveEvidence"] = _source_anim_curve_evidence(source_result)
        target_root = _import_model(target_pmx, use_namespace=True)

        target_result = resolve_scene_humanik_assignments(target_root)
        if not source_result.assignments or not target_result.assignments:
            raise RuntimeError("Fixture produced no HumanIK assignments")
        source_before_connections = snapshot_humanik_connections(source_result)
        target_before_connections = snapshot_humanik_connections(target_result)
        payload.update(
            {
                "stage": "definitions",
                "sourceRoot": source_root,
                "targetRoot": target_root,
                "sourceAssignmentCount": len(source_result.assignments),
                "targetAssignmentCount": len(target_result.assignments),
                "sourceConnectionsBefore": source_before_connections,
                "targetConnectionsBefore": target_before_connections,
            }
        )

        source_character = create_humanik_definition_from_scene(
            source_root,
            name_hint=f"{args.name_prefix}Source",
            create_control_rig=False,
            update_ui=False,
        )
        target_character = create_humanik_definition_from_scene(
            target_root,
            name_hint=f"{args.name_prefix}Target",
            create_control_rig=True,
            update_ui=False,
        )
        payload.update(
            {
                "sourceCharacter": source_character,
                "targetCharacter": target_character,
                "stage": "lock",
            }
        )
        try:
            source_locked = lock_humanik_definition(source_character)
        except Exception:
            payload["sourceLockState"] = False
            raise
        payload["sourceLockState"] = bool(source_locked)
        try:
            target_locked = lock_humanik_definition(target_character)
        except Exception:
            payload["targetLockState"] = False
            raise
        payload["targetLockState"] = bool(target_locked)

        payload["stage"] = "source_connection"
        source_report = connect_humanik_source(
            target_character,
            source_character,
            require_connected=False,
        )
        payload["sourceConnection"] = source_report
        motion_transfer_by_mode = _motion_transfer_by_mode(
            source_result,
            target_result,
            motion_frames,
            args.tolerance,
            evaluation_modes,
        )
        payload["motionTransferByEvaluationMode"] = motion_transfer_by_mode["modes"]
        payload["motionTransfer"] = motion_transfer_by_mode["modes"][evaluation_modes[0]]
        source_vmd_motion = {
            "vmdHasBoneMotion": bool(payload["vmdPreflight"]["hasBoneMotion"]),
            "sourceHikAnimCurveCount": int(payload["sourceAnimCurveEvidence"]["sourceHikAnimCurveCount"]),
            "sourceAnimatedByEvaluationMode": {
                mode: bool(report["sourceAnimated"])
                for mode, report in motion_transfer_by_mode["modes"].items()
            },
            "targetAnimatedByEvaluationMode": {
                mode: bool(report["targetAnimated"])
                for mode, report in motion_transfer_by_mode["modes"].items()
            },
        }
        payload["sourceVmdMotion"] = source_vmd_motion
        if not source_vmd_motion["vmdHasBoneMotion"]:
            payload["diagnosis"] = "vmd_fixture_has_no_bone_frames"
        elif not all(source_vmd_motion["sourceAnimatedByEvaluationMode"].values()):
            payload["diagnosis"] = "source_vmd_application_or_bone_name_mapping_failed"
        elif not all(source_vmd_motion["targetAnimatedByEvaluationMode"].values()):
            payload["diagnosis"] = "humanik_retarget_failed"
        else:
            payload["diagnosis"] = "source_vmd_propagated_to_target"
        source_after_connections = snapshot_humanik_connections(source_result)
        target_after_connections = snapshot_humanik_connections(target_result)
        source_changed_connections = diff_humanik_connections(
            source_before_connections,
            source_after_connections,
        )
        target_changed_connections = diff_humanik_connections(
            target_before_connections,
            target_after_connections,
        )
        payload.update(
            {
                "sourceConnectionsAfter": source_after_connections,
                "sourceChangedConnections": source_changed_connections,
                "targetConnectionsAfter": target_after_connections,
                "targetChangedConnections": target_changed_connections,
                "sourceWriterCensus": build_humanik_writer_report(source_result),
                "targetWriterCensus": build_humanik_writer_report(target_result),
            }
        )

        source_hips = _find_assignment(source_result, "Hips").joint
        target_hips = _find_assignment(target_result, "Hips").joint
        target_spine = _find_assignment(target_result, "Spine").joint
        target_legs = [
            _find_assignment(target_result, hik_bone).joint
            for hik_bone in ("LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg")
            if any(item.hik_bone == hik_bone for item in target_result.assignments)
        ]
        payload["stage"] = "locomotion"
        locomotion = _verify_locomotion_modes(
            source_hips,
            target_hips,
            {"upperBody": [target_spine], "lowerBody": [target_hips], "legs": target_legs},
            _parse_translation(args.translation),
            args.tolerance,
            evaluation_modes,
            source_model_root=source_root,
        )

        stop_reasons = []
        if not source_locked or not target_locked:
            stop_reasons.append("character_definition_lock_failed")
        if not source_report["retargetConnected"]:
            stop_reasons.append("direct_source_connection_failed")
        if not motion_transfer_by_mode["passed"]:
            stop_reasons.append("source_vmd_did_not_propagate_to_target")
        if source_changed_connections:
            stop_reasons.append("source_writer_connections_changed")
        if not locomotion["passed"]:
            reports = list(locomotion.get("modes", {}).values())
            unsupported = any(report.get("supported") is False for report in reports)
            if unsupported:
                stop_reasons.append("root_locomotion_probe_unsupported")
            else:
                if any(not report.get("writeSucceeded", True) for report in reports):
                    stop_reasons.append("root_locomotion_probe_write_failed")
                if any(
                    report.get("writeSucceeded", True)
                    and not report.get("writeReadbackPassed", True)
                    for report in reports
                ):
                    stop_reasons.append("root_locomotion_probe_readback_failed")
                if any(
                    report.get("writeSucceeded", True)
                    and not report.get("restoreSucceeded", True)
                    for report in reports
                ):
                    stop_reasons.append("root_locomotion_probe_restore_failed")
                if any(
                    report.get("writeSucceeded", True)
                    and report.get("writeReadbackPassed", True)
                    and report.get("restoreSucceeded", True)
                    and not report.get("rootMotionPassed", True)
                    for report in reports
                ):
                    stop_reasons.append("root_motion_lost")
                if any(
                    report.get("writeSucceeded", True)
                    and report.get("writeReadbackPassed", True)
                    and report.get("restoreSucceeded", True)
                    and not all(
                        group.get("passed", False)
                        for group in report.get("groups", {}).values()
                    )
                    for report in reports
                ):
                    stop_reasons.append("root_locomotion_body_split")
                known_reasons = {
                    "root_locomotion_probe_write_failed",
                    "root_locomotion_probe_readback_failed",
                    "root_locomotion_probe_restore_failed",
                    "root_motion_lost",
                    "root_locomotion_body_split",
                }
                if not known_reasons.intersection(stop_reasons):
                    stop_reasons.append("root_locomotion_probe_failed")
        payload.update(
            {
                "locomotion": locomotion,
                "stopReasons": stop_reasons,
                "status": "pass" if not stop_reasons else "fail",
                "stage": "complete",
            }
        )
        if payload["status"] != "pass":
            reasons = ", ".join(payload["stopReasons"]) or "unknown"
            raise RuntimeError(f"HumanIK S0 acceptance failed: {reasons}")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "mayaVersion": payload["mayaVersion"],
                    "sourceAssignmentCount": payload["sourceAssignmentCount"],
                    "targetAssignmentCount": payload["targetAssignmentCount"],
                    "sourceLockState": payload["sourceLockState"],
                    "targetLockState": payload["targetLockState"],
                    "inputType": payload["sourceConnection"]["inputType"],
                    "sourceChangedConnectionCount": len(payload["sourceChangedConnections"]),
                    "targetChangedConnectionCount": len(payload["targetChangedConnections"]),
                    "evaluationModes": {
                        mode: report["passed"]
                        for mode, report in payload["locomotion"]["modes"].items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
