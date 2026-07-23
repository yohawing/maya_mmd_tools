"""Maya 2024 smoke for S3 exclusive HumanIK TARGET preview."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import maya.cmds as cmds
import maya.mel as mel
import maya.standalone

from mmd_tools.core.humanik_builder import (
    create_humanik_definition_from_scene,
    lock_humanik_definition,
    resolve_scene_humanik_assignments,
)
from mmd_tools.core.humanik_constraints import (
    classify_humanik_constraints,
    collect_humanik_constraint_facts,
    snapshot_constraint_connections,
)
from mmd_tools.core.humanik_preview import (
    begin_humanik_target_preview,
    stop_humanik_target_preview,
)
from mmd_tools.core.humanik_retarget import verify_root_locomotion


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke HumanIK TARGET preview under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--out", default="build/reports/humanik_target_preview_smoke.json")
    return parser.parse_args()


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def _load_model(path: Path, setup_rig: bool) -> str:
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


def _assignment(result, name: str) -> str:
    for item in result.assignments:
        if item.hik_bone == name:
            return item.joint
    raise RuntimeError(f"Missing HIK assignment: {name}")


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "fail", "pmx": str(args.pmx)}
    maya.standalone.initialize(name="python")
    try:
        _load_plugin()
        pmx = Path(args.pmx).resolve()
        source_root = _load_model(pmx, setup_rig=False)
        target_root = _load_model(pmx, setup_rig=True)
        source_result = resolve_scene_humanik_assignments(source_root)
        target_result = resolve_scene_humanik_assignments(target_root)
        source_character = create_humanik_definition_from_scene(
            source_root, name_hint="MMDToolsS3_Source", update_ui=False
        )
        target_character = create_humanik_definition_from_scene(
            target_root, name_hint="MMDToolsS3_Target", update_ui=False
        )
        lock_humanik_definition(source_character)
        lock_humanik_definition(target_character)

        before = snapshot_constraint_connections()
        report = classify_humanik_constraints(
            collect_humanik_constraint_facts(),
            (item.joint for item in target_result.assignments),
        )
        preview = begin_humanik_target_preview(
            "mmd-tools:s3:target",
            target_character,
            source_character,
            report,
            (item.joint for item in target_result.assignments),
        )
        active_snapshot = snapshot_constraint_connections()
        retained_unchanged = all(
            active_snapshot.get(node) == before.get(node)
            for node in preview.retained_nodes
        )

        source_hips = _assignment(source_result, "Hips")
        target_hips = _assignment(target_result, "Hips")
        groups = {
            "lowerBody": [target_hips],
            "upperBody": [_assignment(target_result, "Spine")],
            "legs": [
                _assignment(target_result, name)
                for name in ("LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg")
            ],
        }
        original_mode = (cmds.evaluationManager(query=True, mode=True) or ["off"])[0]
        locomotion = {}
        try:
            for mode in ("off", "serial", "parallel"):
                cmds.evaluationManager(mode=mode)
                locomotion[mode] = verify_root_locomotion(
                    source_hips,
                    groups,
                    observed_root_joint=target_hips,
                    source_model_root=source_root,
                )
        finally:
            cmds.evaluationManager(mode=original_mode)
        active_input_type = int(mel.eval(f'hikGetInputType("{target_character}")'))
        stop_humanik_target_preview(preview)
        stop_humanik_target_preview(preview)
        after = snapshot_constraint_connections()
        restored_source = str(
            mel.eval(f'hikGetRetargetCharacterInput("{target_character}")') or ""
        )
        payload.update(
            {
                "mayaVersion": cmds.about(version=True),
                "ownershipCounts": report["counts"],
                "disconnectedCount": len(preview.disconnected),
                "retainedNodeCount": len(preview.retained_nodes),
                "retainedConnectionsUnchanged": retained_unchanged,
                "activeInputType": active_input_type,
                "locomotion": locomotion,
                "neutralConnectionsRestored": before == after,
                "neutralInputRestored": restored_source == "",
                "preview": preview.to_dict(),
            }
        )
        payload["status"] = "pass" if all(
            (
                payload["disconnectedCount"] > 0,
                payload["retainedConnectionsUnchanged"],
                payload["activeInputType"] == 3,
                all(item["passed"] for item in locomotion.values()),
                payload["neutralConnectionsRestored"],
                payload["neutralInputRestored"],
            )
        ) else "fail"
        if payload["status"] != "pass":
            raise RuntimeError("HumanIK TARGET preview acceptance failed")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": payload["status"],
            "ownershipCounts": payload["ownershipCounts"],
            "disconnectedCount": payload["disconnectedCount"],
            "retainedNodeCount": payload["retainedNodeCount"],
            "retainedConnectionsUnchanged": payload["retainedConnectionsUnchanged"],
            "neutralConnectionsRestored": payload["neutralConnectionsRestored"],
            "evaluationModes": {mode: item["passed"] for mode, item in locomotion.items()},
        }, sort_keys=True))
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
