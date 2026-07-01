"""Bone-specific helpers for VMD animation conversion."""

from typing import Dict, List, Optional

import maya.cmds as cmds

from . import vmd_profile
from .vmd_scene_keying import VmdKeyingError, _ensure_fallback_allowed


def convert_bone_animation(converter, bone_frames: List) -> bool:
    """Convert VMD bone frames using the converter's shared Maya helpers."""
    bone_frame_map: Dict[str, List] = {}

    with vmd_profile.scope("bone_frame_grouping", count=len(bone_frames)):
        for frame in bone_frames:
            if hasattr(frame, "bone_name"):
                bone_name = frame.bone_name
            else:
                bone_name = frame.get("bone_name", "")
            if bone_name not in bone_frame_map:
                bone_frame_map[bone_name] = []
            bone_frame_map[bone_name].append(frame)
    vmd_profile.set_extra("animated_bone_count", len(bone_frame_map))

    success_count = 0
    total_count = len(bone_frame_map)
    animated_joints = []
    key_routes = converter._build_legacy_bone_key_routes()

    for vmd_bone_name, frames in bone_frame_map.items():
        if vmd_bone_name in converter.bone_name_mapping:
            maya_joint = converter.bone_name_mapping[vmd_bone_name]

            try:
                with vmd_profile.scope("bone_frame_sort", count=len(frames)):
                    frames.sort(key=lambda x: x.frame_number if hasattr(x, "frame_number") else x.get("frame_number", 0))

                converter._set_bone_keyframes(
                    maya_joint,
                    frames,
                    vmd_bone_name,
                    key_routes.get(maya_joint),
                )
                animated_joints.append(maya_joint)
                success_count += 1

            except VmdKeyingError:
                raise
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


def set_bone_keyframes(converter, joint: str, frames: List, vmd_bone_name: str, key_route: Optional[dict] = None) -> None:
    """Set legacy VMD bone keyframes using the converter's shared scene helpers."""
    key_route = key_route or {}
    attr_targets = key_route.get("attr_targets", {})
    skip_rotate = bool(key_route.get("skip_rotate"))
    attrs = ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"]
    channel_interp_map = {
        attr: converter._vmd_interp_channel_for_attr(attr)
        for attr in attrs
        if converter._vmd_interp_channel_for_attr(attr)
    }

    keyed_attrs_by_node: Dict[str, List[str]] = {}
    for attr in attrs:
        if skip_rotate and attr.startswith("rotate"):
            continue
        target_node, target_attr = attr_targets.get(attr, (joint, attr))
        keyed_attrs_by_node.setdefault(target_node, []).append(target_attr)

    use_layer = converter.use_animation_layers and converter.anim_layer is not None and not skip_rotate
    if use_layer:
        cmds.animLayer(converter.anim_layer, edit=True, selected=True)
        for target_node, target_attrs in keyed_attrs_by_node.items():
            converter._add_attrs_to_anim_layer(target_node, target_attrs)
    elif converter.use_animation_layers and converter.anim_layer is not None:
        cmds.animLayer(converter.anim_layer, edit=True, selected=False)

    bind_pos = converter._bone_bind_poses.get(
        vmd_bone_name,
        converter._bone_bind_poses.get(joint, (0.0, 0.0, 0.0)),
    )

    batch_simple_bone = (
        not attr_targets
        and not skip_rotate
        and not key_route.get("ik_solver_rotate")
    )
    if batch_simple_bone:
        channel_samples = {attr: [] for attr in attrs}
        with vmd_profile.scope("channel_sample_build", count=len(frames)):
            for frame in frames:
                if hasattr(frame, "frame_number"):
                    frame_number = frame.frame_number
                    vmd_pos = frame.position
                    rotation_quat = frame.rotation
                else:
                    frame_number = frame.get("frame_number", 0)
                    vmd_pos = frame.get("position", [0, 0, 0])
                    rotation_quat = frame.get("rotation", [0, 0, 0, 1])
                maya_time = converter.vmd_frame_to_maya_time(frame_number)

                tx = float(bind_pos[0]) + float(vmd_pos[0]) * converter.motion_scale
                ty = float(bind_pos[1]) + float(vmd_pos[1]) * converter.motion_scale
                tz = float(bind_pos[2]) - float(vmd_pos[2]) * converter.motion_scale
                rx, ry, rz = converter._convert_vmd_quat_to_joint_rotate(joint, *rotation_quat)
                values = {
                    "translateX": tx,
                    "translateY": ty,
                    "translateZ": tz,
                    "rotateX": rx,
                    "rotateY": ry,
                    "rotateZ": rz,
                }
                for attr, value in values.items():
                    channel_samples[attr].append((maya_time, float(value)))

        animation_layer = converter.anim_layer if use_layer else None
        if animation_layer:
            channel_samples = converter._samples_as_anim_layer_deltas(joint, channel_samples)

        if converter._batch_key_scalar_channels(joint, channel_samples, animation_layer=animation_layer):
            if converter.use_quaternion_interpolation:
                try:
                    cmds.scriptEditorInfo(suppressWarnings=True)
                    cmds.rotationInterpolation(
                        f"{joint}.rotateX",
                        f"{joint}.rotateY",
                        f"{joint}.rotateZ",
                        convert="quaternionSlerp",
                    )
                except Exception as e:
                    converter.logger.warning(f"Failed to apply quaternion interpolation to {joint}: {str(e)}")
                finally:
                    cmds.scriptEditorInfo(suppressWarnings=False)
            tangent_attrs = attrs
            if converter.use_quaternion_interpolation:
                tangent_attrs = [a for a in attrs if not a.startswith("rotate")]
            converter._apply_vmd_bezier_tangents(joint, frames, tangent_attrs, channel_interp_map)
            return

        converter.logger.debug(f"legacy bone batch keying produced no keys for {joint}; using setKeyframe fallback")

    routed_samples: Dict[str, Dict[str, List[tuple]]] = {}
    with vmd_profile.scope("channel_sample_build", count=len(frames)):
        for frame in frames:
            if hasattr(frame, "frame_number"):
                frame_number = frame.frame_number
                vmd_pos = frame.position
                rotation_quat = frame.rotation
            else:
                frame_number = frame.get("frame_number", 0)
                vmd_pos = frame.get("position", [0, 0, 0])
                rotation_quat = frame.get("rotation", [0, 0, 0, 1])
            maya_time = converter.vmd_frame_to_maya_time(frame_number)

            tx = float(bind_pos[0]) + float(vmd_pos[0]) * converter.motion_scale
            ty = float(bind_pos[1]) + float(vmd_pos[1]) * converter.motion_scale
            tz = float(bind_pos[2]) - float(vmd_pos[2]) * converter.motion_scale

            values = {
                "translateX": tx,
                "translateY": ty,
                "translateZ": tz,
            }
            if not skip_rotate:
                rx, ry, rz = converter._convert_vmd_quat_to_joint_rotate(joint, *rotation_quat)
                values["rotateX"] = rx
                values["rotateY"] = ry
                values["rotateZ"] = rz

            for attr, value in values.items():
                target_node, target_attr = attr_targets.get(attr, (joint, attr))
                routed_samples.setdefault(target_node, {}).setdefault(target_attr, []).append((maya_time, float(value)))

    animation_layer = converter.anim_layer if use_layer else None
    routed_success = False
    for target_node, target_samples in routed_samples.items():
        keyed_samples = (
            converter._samples_as_anim_layer_deltas(target_node, target_samples)
            if animation_layer
            else target_samples
        )
        if converter._batch_key_scalar_channels(target_node, keyed_samples, animation_layer=animation_layer):
            routed_success = True
            continue

        converter.logger.debug(f"routed bone batch keying produced no keys for {target_node}; using setKeyframe fallback")
        for target_attr, samples in target_samples.items():
            _ensure_fallback_allowed(
                target_node,
                target_attr,
                animation_layer,
                "batch_key_scalar_channels returned False for routed bone samples",
            )
            for maya_time, value in samples:
                key_args = {
                    "attribute": target_attr,
                    "time": maya_time,
                    "value": float(value),
                }
                if use_layer:
                    key_args["animLayer"] = converter.anim_layer
                with vmd_profile.scope("fallback_setKeyframe"):
                    cmds.setKeyframe(target_node, **key_args)
                routed_success = True

    if not routed_success and routed_samples:
        converter.logger.debug(f"legacy bone routed keying produced no keys for {joint}")

    ik_info = key_route.get("ik_solver_rotate") if key_route else None
    if ik_info:
        solver_node = ik_info.get("solver")
        slot = ik_info.get("slot")
        if not solver_node or slot is None:
            return
        ir_attrs = [
            f"inputRotate[{slot}].inputRotateElementX",
            f"inputRotate[{slot}].inputRotateElementY",
            f"inputRotate[{slot}].inputRotateElementZ",
        ]
        solver_samples = {attr: [] for attr in ir_attrs}
        for frame in frames:
            if hasattr(frame, "frame_number"):
                fn = frame.frame_number
                rq = frame.rotation
            else:
                fn = frame.get("frame_number", 0)
                rq = frame.get("rotation", [0, 0, 0, 1])
            maya_time = converter.vmd_frame_to_maya_time(fn)
            rx, ry, rz = converter._convert_vmd_quat_to_joint_rotate(joint, *rq)
            for attr, val in zip(ir_attrs, [rx, ry, rz]):
                solver_samples[attr].append((maya_time, float(val)))
        if not converter._batch_key_scalar_channels(solver_node, solver_samples):
            converter.logger.debug(f"IK solver batch keying produced no keys for {solver_node}; using setKeyframe fallback")
            for attr, samples in solver_samples.items():
                _ensure_fallback_allowed(
                    solver_node,
                    attr,
                    None,
                    "batch_key_scalar_channels returned False for IK solver samples",
                )
                for maya_time, value in samples:
                    with vmd_profile.scope("fallback_setKeyframe"):
                        cmds.setKeyframe(f"{solver_node}.{attr}", time=maya_time, value=value)

    rotate_redirected = any(
        attr_targets.get(attr, (joint, attr))[0] != joint
        for attr in ("rotateX", "rotateY", "rotateZ")
    )
    if converter.use_quaternion_interpolation and not skip_rotate and not rotate_redirected:
        try:
            cmds.scriptEditorInfo(suppressWarnings=True)
            cmds.rotationInterpolation(
                f"{joint}.rotateX",
                f"{joint}.rotateY",
                f"{joint}.rotateZ",
                convert="quaternionSlerp",
            )
        except Exception as e:
            converter.logger.warning(f"Failed to apply quaternion interpolation to {joint}: {str(e)}")
        finally:
            cmds.scriptEditorInfo(suppressWarnings=False)

    skip_rotate_tangent = skip_rotate or (
        converter.use_quaternion_interpolation and not rotate_redirected
    )
    tangent_targets = {
        attr: attr_targets.get(attr, (joint, attr))
        for attr in attrs
        if not (skip_rotate_tangent and attr.startswith("rotate"))
    }
    converter._apply_vmd_bezier_tangents(joint, frames, tangent_targets, channel_interp_map)

    if ik_info and solver_node and slot is not None:
        solver_tangent_targets = {
            "rotateX": (solver_node, f"inputRotate[{slot}].inputRotateElementX"),
            "rotateY": (solver_node, f"inputRotate[{slot}].inputRotateElementY"),
            "rotateZ": (solver_node, f"inputRotate[{slot}].inputRotateElementZ"),
        }
        converter._apply_vmd_bezier_tangents(joint, frames, solver_tangent_targets, channel_interp_map)
