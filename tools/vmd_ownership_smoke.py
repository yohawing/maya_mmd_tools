"""Maya 2024 smoke for bone-morph ownership across name translation."""

import json
from pathlib import Path
import sys

import maya.standalone

maya.standalone.initialize(name="python")
import maya.cmds as cmds  # noqa: E402

from mmd_tools.converters.bone_morph_runtime import (  # noqa: E402
    resolve_owned_bone_morph_base_routes,
)
from mmd_tools.core.name_translation import (  # noqa: E402
    NameEntry,
    NameTranslationError,
    apply_translation_plan,
    build_translation_plan,
)


def _route(joint):
    result = resolve_owned_bone_morph_base_routes([joint])
    return bool(result.routes.get(joint)) and not result.blocked


def main(output):
    root = Path(__file__).resolve().parents[1]
    cmds.loadPlugin(str(root / "plug-ins/2024/Release/mmd_tools_cpp.mll"), quiet=True)
    cmds.loadPlugin(str(root / "plug-ins/mmd_tools_plugin.py"), quiet=True)
    cmds.file(new=True, force=True)
    cmds.createNode("transform", name="root")
    parent = cmds.createNode("joint", name="OldParent", parent="root")
    joint = cmds.createNode("joint", name="Center", parent=parent)
    accum = cmds.createNode("mmdBoneMorphAccum", name="accum")
    cmds.addAttr(accum, longName="mmd_bone_morph_accum", attributeType="bool")
    cmds.setAttr(f"{accum}.mmd_bone_morph_accum", True)
    cmds.addAttr(accum, longName="mmd_target_joint", dataType="string")
    old_joint = cmds.ls(joint, long=True)[0]
    cmds.setAttr(f"{accum}.mmd_target_joint", old_joint, type="string")
    for kind in ("Translate", "Rotate"):
        cmds.connectAttr(f"{accum}.output{kind}", f"{joint}.{kind.lower()}", force=True)
    assert _route(old_joint), "baseline route is unresolved"

    old_parent = cmds.ls(parent, long=True)[0]
    entry = NameEntry("bone", old_parent, "旧親", "", "mmd_bone_name_en")
    plan = build_translation_plan([entry], {"旧親": "Parent"}, set_english=False, rename_nodes=True)
    foreign = cmds.createNode("mmdBoneMorphAccum", name="foreignAccum")
    cmds.addAttr(foreign, longName="mmd_target_joint", dataType="string")
    cmds.setAttr(f"{foreign}.mmd_target_joint", old_joint, type="string")
    duplicate = cmds.createNode("mmdBoneMorphAccum", name="duplicateAccum")
    cmds.addAttr(duplicate, longName="mmd_bone_morph_accum", attributeType="bool")
    cmds.setAttr(f"{duplicate}.mmd_bone_morph_accum", True)
    cmds.addAttr(duplicate, longName="mmd_target_joint", dataType="string")
    cmds.setAttr(f"{duplicate}.mmd_target_joint", old_joint, type="string")
    ambiguous_blocked = False
    try:
        apply_translation_plan(plan)
    except NameTranslationError:
        ambiguous_blocked = True
    assert ambiguous_blocked and cmds.objExists(old_parent)
    cmds.delete(duplicate)
    cmds.disconnectAttr(f"{accum}.outputRotate", f"{joint}.rotate")
    incomplete_blocked = False
    try:
        apply_translation_plan(plan)
    except NameTranslationError:
        incomplete_blocked = True
    assert incomplete_blocked and cmds.objExists(old_parent)
    cmds.connectAttr(f"{accum}.outputRotate", f"{joint}.rotate", force=True)
    apply_translation_plan(plan)
    new_joint = "|root|Parent|Center"
    after_translate = _route(new_joint)
    target_after_translate = cmds.getAttr(f"{accum}.mmd_target_joint")
    foreign_target_unchanged = cmds.getAttr(f"{foreign}.mmd_target_joint") == old_joint

    cmds.undo()
    after_undo = _route(old_joint)
    target_after_undo = cmds.getAttr(f"{accum}.mmd_target_joint")
    cmds.redo()
    after_redo = _route(new_joint)
    cmds.setAttr(f"{accum}.mmd_target_joint", old_joint, type="string")
    stale_marker_blocks = not _route(new_joint)
    cmds.setAttr(f"{accum}.mmd_target_joint", new_joint, type="string")

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    scene = output.with_suffix(".ma")
    cmds.file(rename=str(scene))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(str(scene), open=True, force=True)
    reopened = _route(new_joint)
    result = {
        "after_translate": after_translate,
        "ambiguous_blocked": ambiguous_blocked,
        "incomplete_blocked": incomplete_blocked,
        "foreign_target_unchanged": foreign_target_unchanged,
        "after_undo": after_undo,
        "after_redo": after_redo,
        "stale_marker_blocks": stale_marker_blocks,
        "reopened": reopened,
        "target_after_translate": target_after_translate,
        "target_after_undo": target_after_undo,
    }
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert all(result[key] for key in ("after_translate", "ambiguous_blocked", "incomplete_blocked", "foreign_target_unchanged", "after_undo", "after_redo", "stale_marker_blocks", "reopened")), result


if __name__ == "__main__":
    main(sys.argv[1])
