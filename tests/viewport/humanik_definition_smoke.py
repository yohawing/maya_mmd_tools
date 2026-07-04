"""Mayapy smoke for creating a HumanIK definition from MMD joint metadata.

This script builds an in-memory skeleton with MMD bone metadata, runs the
HumanIK builder, and verifies that Maya creates an HIKCharacterNode.  The
optional body fixture can also request control rig creation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import maya.cmds as cmds
import maya.standalone

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX, ATTR_MMD_BONE_NAME
from mmd_tools.core.humanik_builder import create_humanik_definition_from_scene, resolve_scene_humanik_assignments


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test HumanIK definition creation under mayapy.")
    parser.add_argument("--out", default="build/reports/humanik_definition_smoke.json")
    parser.add_argument("--name", default="MMDToolsHumanIKSmoke")
    parser.add_argument("--fixture", choices=("minimal", "body"), default="minimal")
    parser.add_argument("--create-control-rig", action="store_true")
    return parser.parse_args()


def _set_mmd_bone_attrs(joint: str, mmd_name: str, index: int) -> None:
    cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", mmd_name, type="string")
    cmds.addAttr(joint, longName=ATTR_MMD_BONE_INDEX, attributeType="long")
    cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}", index)


def _create_joint(model: str, parent: Optional[str], name: str, mmd_name: str, index: int, position) -> str:
    cmds.select(parent or model, replace=True)
    joint = cmds.joint(name=name, position=position)
    _set_mmd_bone_attrs(joint, mmd_name, index)
    return joint


def _build_minimal_fixture_scene() -> str:
    model = cmds.createNode("transform", name="humanik_smoke_model")
    lower = _create_joint(model, None, "lower_body_joint", "\u4e0b\u534a\u8eab", 1, (0, 10, 0))
    _create_joint(model, lower, "upper_body_joint", "\u4e0a\u534a\u8eab", 2, (0, 15, 0))
    return model


def _build_body_fixture_scene() -> str:
    model = cmds.createNode("transform", name="humanik_body_smoke_model")
    hips = _create_joint(model, None, "hips_joint", "\u4e0b\u534a\u8eab", 1, (0, 10, 0))
    spine = _create_joint(model, hips, "spine_joint", "\u4e0a\u534a\u8eab", 2, (0, 13, 0))
    neck = _create_joint(model, spine, "neck_joint", "\u9996", 3, (0, 16, 0))
    _create_joint(model, neck, "head_joint", "\u982d", 4, (0, 18, 0))

    left_leg = _create_joint(model, hips, "left_up_leg_joint", "\u5de6\u8db3", 5, (1, 8, 0))
    left_knee = _create_joint(model, left_leg, "left_leg_joint", "\u5de6\u3072\u3056", 6, (1, 4, 0))
    _create_joint(model, left_knee, "left_foot_joint", "\u5de6\u8db3\u9996", 7, (1, 0, 1))
    right_leg = _create_joint(model, hips, "right_up_leg_joint", "\u53f3\u8db3", 8, (-1, 8, 0))
    right_knee = _create_joint(model, right_leg, "right_leg_joint", "\u53f3\u3072\u3056", 9, (-1, 4, 0))
    _create_joint(model, right_knee, "right_foot_joint", "\u53f3\u8db3\u9996", 10, (-1, 0, 1))

    left_shoulder = _create_joint(model, spine, "left_shoulder_joint", "\u5de6\u80a9", 11, (1.2, 15.5, 0))
    left_arm = _create_joint(model, left_shoulder, "left_arm_joint", "\u5de6\u8155", 12, (3, 15.5, 0))
    left_elbow = _create_joint(model, left_arm, "left_forearm_joint", "\u5de6\u3072\u3058", 13, (5, 15.5, 0))
    _create_joint(model, left_elbow, "left_hand_joint", "\u5de6\u624b\u9996", 14, (7, 15.5, 0))
    right_shoulder = _create_joint(model, spine, "right_shoulder_joint", "\u53f3\u80a9", 15, (-1.2, 15.5, 0))
    right_arm = _create_joint(model, right_shoulder, "right_arm_joint", "\u53f3\u8155", 16, (-3, 15.5, 0))
    right_elbow = _create_joint(model, right_arm, "right_forearm_joint", "\u53f3\u3072\u3058", 17, (-5, 15.5, 0))
    _create_joint(model, right_elbow, "right_hand_joint", "\u53f3\u624b\u9996", 18, (-7, 15.5, 0))
    return model


def _build_fixture_scene(name: str) -> str:
    if name == "body":
        return _build_body_fixture_scene()
    return _build_minimal_fixture_scene()


def main() -> int:
    args = _parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    maya.standalone.initialize(name="python")
    try:
        model = _build_fixture_scene(args.fixture)
        result = resolve_scene_humanik_assignments(model)
        character = create_humanik_definition_from_scene(
            model,
            name_hint=args.name,
            create_control_rig=args.create_control_rig,
            update_ui=False,
        )
        control_rigs = cmds.ls(type="HIKControlSetNode") or []
        payload = {
            "assignmentCount": len(result.assignments),
            "character": character,
            "characterExists": bool(cmds.objExists(character)),
            "characterType": cmds.nodeType(character) if cmds.objExists(character) else "",
            "controlRigCount": len(control_rigs),
            "fixture": args.fixture,
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
