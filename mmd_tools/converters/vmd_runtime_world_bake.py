"""Runtime world-matrix bake helpers for VMD conversion."""

from __future__ import annotations

import maya.cmds as cmds


def bake_bone_poses_from_world_matrices(converter, frame: int, world_matrices: list, model_bone_count: int) -> None:
    """Bake runtime PMX-order world matrices directly to Maya joints."""
    del model_bone_count
    if not world_matrices or not converter.bone_index_to_joint:
        for _vmd_bone_name, maya_joint in converter.bone_name_mapping.items():
            if cmds.objExists(maya_joint):
                try:
                    for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                        key_args = {
                            "attribute": attr,
                            "time": frame,
                        }
                        if converter.anim_layer:
                            key_args["animLayer"] = converter.anim_layer
                        cmds.setKeyframe(maya_joint, **key_args)
                except Exception:
                    pass
        return

    for bone_idx in sorted(converter.bone_index_to_joint.keys()):
        maya_joint = converter.bone_index_to_joint[bone_idx]
        if not cmds.objExists(maya_joint):
            continue
        if bone_idx >= len(world_matrices):
            continue

        mmd_mat = world_matrices[bone_idx]

        try:
            maya_world = converter._convert_mmd_world_matrix_to_maya(mmd_mat)
            cmds.xform(maya_joint, worldSpace=True, matrix=maya_world)

            for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
                key_args = {
                    "attribute": attr,
                    "time": frame,
                }
                if converter.anim_layer:
                    key_args["animLayer"] = converter.anim_layer
                cmds.setKeyframe(maya_joint, **key_args)
        except Exception as e:
            converter.logger.debug(f"world matrix bake error for {maya_joint} at frame {frame}: {e}")


def convert_mmd_world_matrix_to_maya(mmd_matrix: list) -> list:
    """Convert an mmd-anim flat world matrix to a Maya cmds.xform matrix."""
    if len(mmd_matrix) != 16:
        raise ValueError("mmd_matrix must contain 16 values")

    signs = (1.0, 1.0, -1.0)
    maya_matrix = [float(v) for v in mmd_matrix]

    for row in range(3):
        for col in range(3):
            idx = row * 4 + col
            maya_matrix[idx] = float(mmd_matrix[idx]) * signs[row] * signs[col]

    for col in range(3):
        maya_matrix[12 + col] = float(mmd_matrix[12 + col]) * signs[col]

    return maya_matrix
