"""Mayapy smoke for creating a HumanIK definition from MMD joint metadata.

This script builds a tiny in-memory skeleton with MMD bone metadata, runs the
HumanIK builder, and verifies that Maya creates an HIKCharacterNode.  It does
not create a control rig; that is a later UI/rigging slice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import maya.cmds as cmds
import maya.standalone

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from mmd_tools.core.humanik_builder import create_humanik_definition_from_scene, resolve_scene_humanik_assignments


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test HumanIK definition creation under mayapy.")
    parser.add_argument("--out", default="build/reports/humanik_definition_smoke.json")
    parser.add_argument("--name", default="MMDToolsHumanIKSmoke")
    return parser.parse_args()


def _set_mmd_bone_attrs(joint: str, mmd_name: str, index: int) -> None:
    cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", mmd_name, type="string")
    cmds.addAttr(joint, longName=ATTR_MMD_BONE_INDEX, attributeType="long")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}", index)


def _build_fixture_scene() -> str:
    model = cmds.createNode("transform", name="humanik_smoke_model")
    lower = cmds.joint(name="lower_body_joint", position=(0, 10, 0))
    _set_mmd_bone_attrs(lower, "\u4e0b\u534a\u8eab", 1)
    spine = cmds.joint(name="upper_body_joint", position=(0, 15, 0))
    _set_mmd_bone_attrs(spine, "\u4e0a\u534a\u8eab", 2)
    if not (cmds.listRelatives(lower, parent=True, fullPath=True) or [""])[0].endswith(model):
        cmds.parent(lower, model)
    return model


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    maya.standalone.initialize(name="python")
    try:
        model = _build_fixture_scene()
        result = resolve_scene_humanik_assignments(model)
        character = create_humanik_definition_from_scene(model, name_hint=args.name, update_ui=False)
        payload = {
            "assignmentCount": len(result.assignments),
            "character": character,
            "characterExists": bool(cmds.objExists(character)),
            "characterType": cmds.nodeType(character) if cmds.objExists(character) else "",
            "status": "pass",
        }
        if not payload["characterExists"] or payload["characterType"] != "HIKCharacterNode":
            payload["status"] = "fail"
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            raise RuntimeError(f"HumanIK character was not created: {payload}")
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
