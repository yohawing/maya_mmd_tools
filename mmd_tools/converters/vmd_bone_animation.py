"""Bone-specific helpers for VMD animation conversion."""

from typing import Dict, List


def convert_bone_animation(converter, bone_frames: List) -> bool:
    """Convert VMD bone frames using the converter's shared Maya helpers."""
    bone_frame_map: Dict[str, List] = {}

    for frame in bone_frames:
        if hasattr(frame, "bone_name"):
            bone_name = frame.bone_name
        else:
            bone_name = frame.get("bone_name", "")
        if bone_name not in bone_frame_map:
            bone_frame_map[bone_name] = []
        bone_frame_map[bone_name].append(frame)

    success_count = 0
    total_count = len(bone_frame_map)
    animated_joints = []
    key_routes = converter._build_legacy_bone_key_routes()

    for vmd_bone_name, frames in bone_frame_map.items():
        if vmd_bone_name in converter.bone_name_mapping:
            maya_joint = converter.bone_name_mapping[vmd_bone_name]

            try:
                frames.sort(key=lambda x: x.frame_number if hasattr(x, "frame_number") else x.get("frame_number", 0))

                converter._set_bone_keyframes(
                    maya_joint,
                    frames,
                    vmd_bone_name,
                    key_routes.get(maya_joint),
                )
                animated_joints.append(maya_joint)
                success_count += 1

            except Exception as e:
                converter.logger.error(f"Error setting animation for bone '{vmd_bone_name}': {str(e)}")
                converter._failed_bones.add(vmd_bone_name)
        else:
            if vmd_bone_name not in converter._failed_bones:
                converter.logger.info(f"Bone '{vmd_bone_name}' not found")
                converter._failed_bones.add(vmd_bone_name)

    if converter.use_animation_layers and converter.anim_layer and animated_joints:
        ik_link_joints = converter._collect_ik_link_joints()
        append_target_joints = {
            joint
            for joint, route in key_routes.items()
            if route.get("attr_targets")
        }
        layer_joints = [
            joint
            for joint in animated_joints
            if joint not in ik_link_joints and joint not in append_target_joints
        ]
        converter._add_objects_to_layer(layer_joints)

    converter.logger.info(f"Converted {success_count}/{total_count} bone animations")
    return success_count > 0
