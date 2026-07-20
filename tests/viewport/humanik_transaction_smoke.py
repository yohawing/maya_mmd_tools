"""Maya 2024 smoke for HumanIK journal rollback and idempotent restore."""

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
)
from mmd_tools.core.humanik_retarget import connect_humanik_source
from mmd_tools.core.humanik_transaction import humanik_transaction, restore_humanik_journal


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke HumanIK transaction rollback under mayapy.")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--out", default="build/reports/humanik_transaction_smoke.json")
    return parser.parse_args()


def _load_model(path: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(path),
        options={
            "use_namespace": True,
            "setup_rig": False,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {path}")
    return str(root)


def _load_plugin() -> None:
    path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(path.stem, query=True, loaded=True):
        cmds.loadPlugin(str(path), quiet=True)


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "fail", "pmx": str(args.pmx)}
    maya.standalone.initialize(name="python")
    try:
        _load_plugin()
        pmx = Path(args.pmx).resolve()
        source_root, target_root = _load_model(pmx), _load_model(pmx)
        source_character = create_humanik_definition_from_scene(
            source_root, name_hint="MMDToolsS2_Source", update_ui=False
        )
        target_character = create_humanik_definition_from_scene(
            target_root,
            name_hint="MMDToolsS2_Target",
            create_control_rig=True,
            update_ui=False,
        )
        lock_humanik_definition(source_character)
        lock_humanik_definition(target_character)
        connect_humanik_source(target_character, source_character)

        source = cmds.createNode("transform", name="s2_value_source")
        destination = cmds.createNode("transform", name="s2_value_destination")
        state_node = cmds.createNode("multiplyDivide", name="s2_state_node")
        cmds.setAttr(f"{source}.translateX", 3.0)
        cmds.connectAttr(f"{source}.translateX", f"{destination}.translateX", force=True)
        journal = None
        rollback_triggered = False
        try:
            with humanik_transaction(
                "mmd-tools:s2:target",
                target_character,
                [f"{destination}.translateX"],
                [state_node],
            ) as captured:
                journal = captured
                cmds.disconnectAttr(f"{source}.translateX", f"{destination}.translateX")
                cmds.setAttr(f"{destination}.translateX", 42.0)
                cmds.setAttr(f"{state_node}.nodeState", 2)
                mel.eval(f'hikSetCharacterInput("{target_character}", "");')
                raise RuntimeError("intentional_s2_rollback")
        except RuntimeError as exc:
            if str(exc) != "intentional_s2_rollback":
                raise
            rollback_triggered = True
        if journal is None:
            raise RuntimeError("journal was not captured")
        restore_humanik_journal(journal, "mmd-tools:s2:target")

        restored_sources = cmds.listConnections(
            f"{destination}.translateX", source=True, destination=False, plugs=True
        ) or []
        input_source = str(mel.eval(f'hikGetRetargetCharacterInput("{target_character}")') or "")
        payload.update(
            {
                "mayaVersion": cmds.about(version=True),
                "rollbackTriggered": rollback_triggered,
                "ownershipId": journal.ownership_id,
                "connectionRestored": restored_sources == [f"{source}.translateX"],
                "nodeStateRestored": cmds.getAttr(f"{state_node}.nodeState") == 0,
                "inputSourceRestored": input_source == source_character,
                "inputType": int(mel.eval(f'hikGetInputType("{target_character}")')),
                "journal": journal.to_dict(),
            }
        )
        payload["status"] = "pass" if all(
            (
                payload["rollbackTriggered"],
                payload["connectionRestored"],
                payload["nodeStateRestored"],
                payload["inputSourceRestored"],
                payload["inputType"] == 3,
            )
        ) else "fail"
        if payload["status"] != "pass":
            raise RuntimeError("HumanIK transaction rollback acceptance failed")
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({key: payload[key] for key in (
            "status", "rollbackTriggered", "connectionRestored", "nodeStateRestored",
            "inputSourceRestored", "inputType",
        )}, sort_keys=True))
        return 0
    except Exception as exc:
        payload["error"] = str(exc)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
