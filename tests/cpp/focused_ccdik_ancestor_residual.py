"""Focused standalone Maya reproduction for CCD residual under a rotated ancestor."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _mmd_to_maya_point(point):
    return [float(point[0]), float(point[1]), -float(point[2])]


def _plugin_path() -> Path:
    """Resolve the Python or C++ MMD Tools plugin used by the focused test."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    return Path(explicit) if explicit else ROOT / "mmd_tools" / "plugin_main.py"


def main() -> int:
    """Run deterministic one-ancestor CCD probe and print exact residuals."""
    import maya.cmds as cmds
    import maya.standalone
    import maya.api.OpenMaya as om

    os.environ.setdefault("MMD_TOOLS_SKIP_SHADER_OVERRIDE", "1")
    maya.standalone.initialize(name="python")
    try:
        plugin_path = _plugin_path()
        if plugin_path.suffix.lower() == ".mll":
            os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(plugin_path.parent))
        cmds.loadPlugin(str(plugin_path), quiet=True)
        rests = ([0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, 0.0])
        joint_orients = ([0.0, 10.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
        bind_worlds = []
        no_orient_worlds = []
        for index, (translate, jo) in enumerate(zip(rests, joint_orients)):
            tfm = om.MTransformationMatrix()
            tfm.setTranslation(om.MVector(*translate), om.MSpace.kTransform)
            tfm.setRotation(om.MEulerRotation(*(math.radians(value) for value in jo)).asQuaternion())
            no_tfm = om.MTransformationMatrix()
            no_tfm.setTranslation(om.MVector(*translate), om.MSpace.kTransform)
            parent = index - 1
            local = tfm.asMatrix()
            no_local = no_tfm.asMatrix()
            bind_worlds.append(local * bind_worlds[parent] if parent >= 0 else local)
            no_orient_worlds.append(no_local * no_orient_worlds[parent] if parent >= 0 else no_local)
        bind_matrices = [[float(matrix[i]) for i in range(16)] for matrix in bind_worlds]
        no_orient_matrices = [[float(matrix[i]) for i in range(16)] for matrix in no_orient_worlds]
        chain = {
            "bones": [
                {"rest_position": rests[0], "maya_rest_translate": rests[0], "parent_slot": -1, "joint_orient_deg": joint_orients[0], "maya_bind_world_matrix": bind_matrices[0], "no_orient_bind_world_matrix": no_orient_matrices[0]},
                {"rest_position": rests[1], "maya_rest_translate": rests[1], "parent_slot": 0, "joint_orient_deg": joint_orients[1], "maya_bind_world_matrix": bind_matrices[1], "no_orient_bind_world_matrix": no_orient_matrices[1]},
                {"rest_position": rests[2], "maya_rest_translate": rests[2], "parent_slot": 1, "joint_orient_deg": joint_orients[2], "maya_bind_world_matrix": bind_matrices[2], "no_orient_bind_world_matrix": no_orient_matrices[2]},
                {"rest_position": rests[3], "maya_rest_translate": rests[3], "parent_slot": 2, "joint_orient_deg": joint_orients[3], "maya_bind_world_matrix": bind_matrices[3], "no_orient_bind_world_matrix": no_orient_matrices[3]},
            ],
            "links": [{"bone_slot": 2}, {"bone_slot": 1}],
            "targetBoneSlot": 3,
            "controllerBoneSlot": -1,
            "iterationCount": 40,
            "limitAngle": math.pi,
        }
        node = cmds.createNode("mmdCcdIk", name="focused_ccdik_ancestor_residual")
        root = cmds.createNode("transform", name="focused_ccdik_ancestor")
        goal = cmds.createNode("transform", name="focused_ccdik_goal")
        joints = []
        try:
            cmds.setAttr(f"{node}.chainJson", json.dumps(chain), type="string")
            cmds.setAttr(f"{node}.enabled", True)
            cmds.connectAttr(f"{root}.rotate", f"{node}.inputRotate[0]")
            target_mmd = [0.0, 1.70, 0.30]
            target_maya = _mmd_to_maya_point(target_mmd)
            cmds.setAttr(f"{node}.goal", *target_maya, type="double3")
            rows = []
            for ancestor_rotate_x in (0.0, 20.0):
                cmds.setAttr(f"{root}.rotateX", ancestor_rotate_x)
                output = []
                for index in range(len(chain["links"])):
                    output.append([
                        float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementX")),
                        float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementY")),
                        float(cmds.getAttr(f"{node}.outputRotate[{index}].outputRotateElementZ")),
                    ])
                cmds.select(clear=True)
                joints = [
                    cmds.joint(name=f"focused_ccdik_waist_{int(ancestor_rotate_x)}", position=[0.0, 0.0, 0.0]),
                    cmds.joint(name=f"focused_ccdik_link_{int(ancestor_rotate_x)}", position=[0.0, 1.0, 0.0]),
                    cmds.joint(name=f"focused_ccdik_link2_{int(ancestor_rotate_x)}", position=[0.0, 2.0, 0.0]),
                    cmds.joint(name=f"focused_ccdik_target_{int(ancestor_rotate_x)}", position=[0.0, 3.0, 0.0]),
                ]
                for index, jo in enumerate(joint_orients):
                    cmds.setAttr(f"{joints[index]}.jointOrient", *jo, type="double3")
                cmds.setAttr(f"{joints[0]}.rotateX", ancestor_rotate_x)
                cmds.setAttr(f"{joints[1]}.rotate", *output[1], type="double3")
                cmds.setAttr(f"{joints[2]}.rotate", *output[0], type="double3")
                first_link = cmds.xform(joints[1], query=True, worldSpace=True, translation=True)
                reach = math.sqrt(sum((float(first_link[i]) - target_maya[i]) ** 2 for i in range(3)))
                if reach > 2.0 + 1.0e-6:
                    raise RuntimeError(f"unreachable focused target at ancestorRotateX={ancestor_rotate_x}: reach={reach}")
                actual = cmds.xform(joints[3], query=True, worldSpace=True, translation=True)
                residual = math.sqrt(sum((float(actual[i]) - target_maya[i]) ** 2 for i in range(3)))
                if residual > 1.0e-3:
                    raise RuntimeError(
                        f"ancestorRotateX={ancestor_rotate_x} residual={residual} exceeds 1e-3; "
                        f"target={target_maya}, effector={actual}"
                    )
                rows.append({
                    "ancestorRotateX": ancestor_rotate_x,
                    "targetMaya": target_maya,
                    "effectorMaya": [float(value) for value in actual],
                    "firstLinkReach": reach,
                    "residual": residual,
                    "outputRotateDegrees": output,
                })
                cmds.delete(*joints)
                joints = []
            print(json.dumps({
                "targetMmd": target_mmd,
                "iterationCount": chain["iterationCount"],
                "limitAngle": chain["limitAngle"],
                "rows": rows,
            }, ensure_ascii=False, sort_keys=True))
        finally:
            if joints:
                cmds.delete(*joints)
            cmds.delete(node, root, goal)
    finally:
        maya.standalone.uninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
