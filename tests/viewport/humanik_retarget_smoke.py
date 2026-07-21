"""Maya 2024 mayapy smoke for direct HumanIK retarget S0 evidence.

The smoke imports source/target PMX fixtures and a source VMD, creates HIK
definitions without UI calls, locks both definitions, connects the target
through ``hikSetCharacterInput``, and writes VMD propagation, deterministic
connection/writer, and root-locomotion evidence. It intentionally does not
mute constraints, bake animation, or create a proxy skeleton.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

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
        "--target-pmx",
        default=None,
        help="Optional target PMX; defaults to --pmx for self-retarget coverage.",
    )
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
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
    return parser.parse_args()


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


def _verify_locomotion_modes(
    source_hips: str,
    target_hips: str,
    target_groups,
    translation,
    tolerance: float,
    modes,
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
            )
    finally:
        cmds.evaluationManager(mode=original_modes[0])
    return {
        "modes": reports,
        "passed": bool(reports) and all(report["passed"] for report in reports.values()),
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

        source_root = _import_model(source_pmx, use_namespace=True)
        _import_motion(vmd, source_root, source_pmx)
        target_root = _import_model(target_pmx, use_namespace=True)

        source_result = resolve_scene_humanik_assignments(source_root)
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
        payload["motionTransfer"] = _motion_transfer_evidence(
            source_result,
            target_result,
            _parse_motion_frames(args.motion_frames),
            args.tolerance,
        )
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
            _parse_evaluation_modes(args.evaluation_modes),
        )

        stop_reasons = []
        if not source_locked or not target_locked:
            stop_reasons.append("character_definition_lock_failed")
        if not source_report["retargetConnected"]:
            stop_reasons.append("direct_source_connection_failed")
        if not payload["motionTransfer"]["passed"]:
            stop_reasons.append("source_vmd_did_not_propagate_to_target")
        if source_changed_connections:
            stop_reasons.append("source_writer_connections_changed")
        if not locomotion["passed"]:
            if any(
                not report["rootMotionPassed"]
                for report in locomotion["modes"].values()
            ):
                stop_reasons.append("root_motion_lost")
            if any(
                not all(group["passed"] for group in report["groups"].values())
                for report in locomotion["modes"].values()
            ):
                stop_reasons.append("root_locomotion_body_split")
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
