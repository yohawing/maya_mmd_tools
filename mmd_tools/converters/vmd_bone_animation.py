"""Bone-specific helpers for VMD animation conversion."""

from collections.abc import Mapping
import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import vmd_profile
from .vmd_context import VmdBoneAnimationContext
from .vmd_scene_keying import VmdKeyingError, _ensure_fallback_allowed


def _euler_degrees_to_quaternion(values, rotate_order=0):
    """Convert Maya Euler degrees to xyzw without losing rotateOrder."""

    euler = om.MEulerRotation(*(math.radians(float(value)) for value in values))
    euler.order = int(rotate_order)
    quaternion = euler.asQuaternion()
    return (quaternion.x, quaternion.y, quaternion.z, quaternion.w)


def _quaternion_to_euler_degrees(quaternion, rotate_order=0):
    """Convert xyzw to Maya Euler degrees in the destination rotateOrder."""

    euler = om.MQuaternion(*map(float, quaternion)).asEulerRotation()
    euler.reorderIt(int(rotate_order))
    return tuple(math.degrees(value) for value in (euler.x, euler.y, euler.z))


def _sparse_rotation_samples(
    context: VmdBoneAnimationContext,
    joint: str,
    frames: List,
    key_route: Optional[dict] = None,
) -> List[tuple]:
    """Convert only authored VMD rotation keys for editable rig curves."""
    samples_by_time = {}
    for frame in frames:
        if hasattr(frame, "frame_number"):
            frame_number = frame.frame_number
            rotation_quat = frame.rotation
        else:
            frame_number = frame.get("frame_number", 0)
            rotation_quat = frame.get("rotation", [0, 0, 0, 1])
        maya_time = context.vmd_frame_to_maya_time(frame_number)
        rotation = context.convert_vmd_quat_to_joint_rotate(joint, *rotation_quat)
        basis = (key_route or {}).get("authoring_basis")
        if basis:
            from ..core.mmd_control_rig_basis import (
                bone_to_control,
            )

            joint_order = int(cmds.getAttr(f"{joint}.rotateOrder"))
            bone_quaternion = _euler_degrees_to_quaternion(rotation, joint_order)
            control_quaternion = bone_to_control(bone_quaternion, basis)
            attr_targets = (key_route or {}).get("attr_targets", {})
            control = attr_targets.get("rotateX", (joint, "rotateX"))[0]
            control_order = int(cmds.getAttr(f"{control}.rotateOrder"))
            rotation = _quaternion_to_euler_degrees(
                control_quaternion, control_order
            )
        samples_by_time[float(maya_time)] = tuple(float(value) for value in rotation)
    return sorted(samples_by_time.items())


def _apply_quaternion_interpolation(
    context: VmdBoneAnimationContext,
    plugs: List[str],
    *,
    animation_layer: Optional[str] = None,
) -> bool:
    """Apply Maya quaternion slerp to a Transform rotation track."""
    try:
        from .vmd_rotation_time_curve import _resolve_quaternion_curves

        curves = _resolve_quaternion_curves(
            plugs,
            animation_layer=animation_layer,
            require_quaternion=False,
        )
        cmds.scriptEditorInfo(suppressWarnings=True)
        cmds.rotationInterpolation(*curves, convert="quaternionSlerp")
        if any(
            cmds.rotationInterpolation(curve, query=True) != "quaternionSlerp"
            for curve in curves
        ):
            raise RuntimeError("Maya did not retain quaternionSlerp on rotation curves")
        return True
    except Exception as exc:
        context.logger.warning(f"Failed to apply quaternion interpolation to {plugs[0]}: {exc}")
        return False
    finally:
        cmds.scriptEditorInfo(suppressWarnings=False)


def _has_registered_rotation_curve(frames: List) -> bool:
    """Return whether frames carry compiled semantic rotation controls."""
    for frame in frames:
        interpolation = getattr(frame, "semantic_interpolation", None)
        if isinstance(frame, dict):
            interpolation = frame.get("semantic_interpolation", interpolation)
        if isinstance(interpolation, Mapping) and "rotation" in interpolation:
            return True
    return False


def _is_complete_xyz_sibling_route(attributes: Tuple[str, ...]) -> bool:
    """Return whether attributes are one ordered X/Y/Z compound sibling set."""
    if len(attributes) != 3:
        return False
    stems = []
    for attribute, axis in zip(attributes, "XYZ"):
        if not attribute.endswith(axis):
            return False
        stems.append(attribute[:-1])
    return bool(stems[0] and len(set(stems)) == 1)


def _configure_sparse_rotation_track(
    context: VmdBoneAnimationContext,
    joint: str,
    frames: List,
    vmd_bone_name: str,
    key_route: dict,
    *,
    skip_rotate: bool,
    animation_layer: Optional[str],
) -> Optional[List[str]]:
    """Apply quaternion interpolation and semantic time warping when safe."""
    rotation_attrs = ("rotateX", "rotateY", "rotateZ")
    attr_targets = key_route.get("attr_targets", {})
    rotation_targets = [
        attr_targets.get(attr, (joint, attr)) for attr in rotation_attrs
    ]
    rotate_redirected = any(
        target_node != joint for target_node, _ in rotation_targets
    )
    registered_rotation_curve = _has_registered_rotation_curve(frames)
    direct_rotation_route = (
        not skip_rotate
        and not rotate_redirected
        and not key_route.get("ik_solver_rotate")
    )
    owned_rotation_route_safe = bool(key_route.get("quaternion_interpolation_safe"))
    if registered_rotation_curve and rotate_redirected and not owned_rotation_route_safe:
        raise VmdKeyingError(
            "VMD semantic rotation cannot be authored across mixed or unsafe owners: "
            f"joint={joint}; targets={rotation_targets!r}"
        )
    quaternion_requested = (
        registered_rotation_curve and (direct_rotation_route or owned_rotation_route_safe)
    ) or (
        context.use_quaternion_interpolation and owned_rotation_route_safe
    )
    if not quaternion_requested or skip_rotate:
        return None

    quaternion_plugs = None
    if not rotate_redirected:
        quaternion_plugs = [f"{joint}.{attr}" for attr in rotation_attrs]
    elif owned_rotation_route_safe:
        target_nodes = {target_node for target_node, _ in rotation_targets}
        target_attrs = tuple(target_attr for _, target_attr in rotation_targets)
        if len(target_nodes) == 1 and _is_complete_xyz_sibling_route(target_attrs):
            target_node = next(iter(target_nodes))
            quaternion_plugs = [
                f"{target_node}.{attr}" for attr in target_attrs
            ]
    if not quaternion_plugs:
        return None

    quaternion_applied = _apply_quaternion_interpolation(
        context,
        quaternion_plugs,
        animation_layer=animation_layer,
    )
    if not quaternion_applied:
        if owned_rotation_route_safe:
            raise VmdKeyingError(
                "VMD quaternion interpolation could not be established on owned route: "
                f"{quaternion_plugs!r}"
            )
        return None
    if (
        context.use_vmd_rotation_time_curve or registered_rotation_curve
    ) and len(frames) >= 2:
        from .vmd_rotation_time_curve import apply_vmd_rotation_time_curve

        context.rotation_time_curve_records.append(
            apply_vmd_rotation_time_curve(
                frames,
                quaternion_plugs,
                vmd_bone_name,
                time_converter=context.vmd_frame_to_maya_time,
                animation_layer=animation_layer,
            )
        )
    return quaternion_plugs


def convert_bone_animation(
    context: VmdBoneAnimationContext,
    bone_frames: List,
    *,
    key_routes: Optional[Mapping[str, dict]] = None,
) -> bool:
    """Convert VMD bone frames using explicit bone keying context."""
    bone_frame_map: Dict[object, List] = {}

    with vmd_profile.scope("bone_frame_grouping", count=len(bone_frames)):
        for frame in bone_frames:
            if hasattr(frame, "bone_index"):
                bone_name = ("index", int(frame.bone_index))
            elif isinstance(frame, dict) and "bone_index" in frame:
                bone_name = ("index", int(frame["bone_index"]))
            elif hasattr(frame, "bone_name"):
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
    if key_routes is None:
        key_routes = context.build_legacy_bone_key_routes()

    for bone_identity, frames in bone_frame_map.items():
        indexed_identity = isinstance(bone_identity, tuple) and bone_identity[0] == "index"
        first_frame = frames[0]
        vmd_bone_name = str(
            first_frame.bone_name
            if hasattr(first_frame, "bone_name")
            else first_frame.get("bone_name", "")
        )
        maya_joint = (
            context.bone_index_to_joint.get(int(bone_identity[1]))
            if indexed_identity
            else context.bone_name_mapping.get(vmd_bone_name)
        )
        if maya_joint:

            try:
                with vmd_profile.scope("bone_frame_sort", count=len(frames)):
                    frames.sort(key=lambda x: x.frame_number if hasattr(x, "frame_number") else x.get("frame_number", 0))

                context.set_bone_keyframes(
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
                context.logger.error(f"Error setting animation for bone '{vmd_bone_name}': {str(e)}")
                context.failed_bones.add(vmd_bone_name)
        else:
            if vmd_bone_name not in context.failed_bones:
                context.logger.debug(f"Bone '{vmd_bone_name}' not found")
                context.failed_bones.add(vmd_bone_name)

    if context.use_animation_layers and context.anim_layer and animated_joints:
        ik_link_joints = context.collect_ik_link_joints()
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
        context.add_objects_to_layer(layer_joints)

    context.logger.info(f"Converted {success_count}/{total_count} bone animations")
    return success_count > 0


def set_bone_keyframes(
    context: VmdBoneAnimationContext,
    joint: str,
    frames: List,
    vmd_bone_name: str,
    key_route: Optional[dict] = None,
) -> None:
    """Set legacy VMD bone keys while preserving hidden Twist channel locks."""

    route = key_route or {}
    from .vmd_redirected_authoring_proxy import ensure_redirected_authoring_proxy

    try:
        redirected_proxy_route = ensure_redirected_authoring_proxy(
            joint,
            route.get("attr_targets", {}),
        )
    except Exception as exc:
        raise VmdKeyingError(
            "VMD bone keying blocked because a redirected authoring proxy "
            f"could not be established: joint={joint}; error={exc}"
        ) from exc
    if redirected_proxy_route:
        route = dict(route)
        route["attr_targets"] = dict(route.get("attr_targets", {}))
        route["attr_targets"].update(redirected_proxy_route)
        if all(
            channel in redirected_proxy_route
            for channel in ("rotateX", "rotateY", "rotateZ")
        ):
            route["quaternion_interpolation_safe"] = True
    blocked_channels = tuple(route.get("blocked_channels") or ())
    if blocked_channels:
        raise VmdKeyingError(
            "VMD bone keying blocked because an authored input owner is unresolved: "
            f"joint={joint}; channels={blocked_channels!r}; "
            f"reason={route.get('block_reason') or 'authored_route_unresolved'}"
        )
    states = []
    if route.get("fixed_axis_twist"):
        attr_targets = route.get("attr_targets", {})
        for attr in ("rotateX", "rotateY"):
            target_node, target_attr = attr_targets.get(attr, (joint, attr))
            plug = f"{target_node}.{target_attr}"
            if not cmds.objExists(plug) or not bool(cmds.getAttr(plug, lock=True)):
                continue
            states.append(
                (
                    plug,
                    bool(cmds.getAttr(plug, keyable=True)),
                    bool(cmds.getAttr(plug, channelBox=True)),
                )
            )
            cmds.setAttr(plug, lock=False)
    try:
        _set_bone_keyframes_impl(context, joint, frames, vmd_bone_name, route)
    finally:
        for plug, keyable, channel_box in states:
            if not cmds.objExists(plug):
                continue
            cmds.setAttr(plug, lock=False)
            cmds.setAttr(plug, keyable=keyable, channelBox=channel_box)
            cmds.setAttr(plug, lock=True)


def _set_bone_keyframes_impl(
    context: VmdBoneAnimationContext,
    joint: str,
    frames: List,
    vmd_bone_name: str,
    key_route: Optional[dict] = None,
) -> None:
    """Author one legacy VMD bone track using an explicit keying context."""
    key_route = key_route or {}
    attr_targets = key_route.get("attr_targets", {})
    skip_rotate = bool(key_route.get("skip_rotate"))
    rotation_attrs = ["rotateX", "rotateY", "rotateZ"]
    attrs = ["translateX", "translateY", "translateZ", *rotation_attrs]
    channel_interp_map = {
        attr: context.vmd_interp_channel_for_attr(attr)
        for attr in attrs
        if context.vmd_interp_channel_for_attr(attr)
    }

    keyed_attrs_by_node: Dict[str, List[str]] = {}
    for attr in attrs:
        if skip_rotate and attr.startswith("rotate"):
            continue
        target_node, target_attr = attr_targets.get(attr, (joint, attr))
        keyed_attrs_by_node.setdefault(target_node, []).append(target_attr)

    use_layer = context.use_animation_layers and context.anim_layer is not None and not skip_rotate
    if use_layer:
        cmds.animLayer(context.anim_layer, edit=True, selected=True)
        for target_node, target_attrs in keyed_attrs_by_node.items():
            context.add_attrs_to_anim_layer(target_node, target_attrs)
    elif context.use_animation_layers and context.anim_layer is not None:
        cmds.animLayer(context.anim_layer, edit=True, selected=False)

    bind_pos = context.bone_bind_poses.get(
        vmd_bone_name,
        context.bone_bind_poses.get(joint, (0.0, 0.0, 0.0)),
    )
    control_owned_channels = set(key_route.get("control_owned_channels", ()))
    needs_rotation_samples = not skip_rotate or bool(key_route.get("ik_solver_rotate"))
    rotation_samples = (
        _sparse_rotation_samples(context, joint, frames, key_route)
        if needs_rotation_samples
        else []
    )
    rotation_by_time = dict(rotation_samples)

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
                else:
                    frame_number = frame.get("frame_number", 0)
                    vmd_pos = frame.get("position", [0, 0, 0])
                maya_time = context.vmd_frame_to_maya_time(frame_number)

                tx = float(bind_pos[0]) + float(vmd_pos[0]) * context.motion_scale
                ty = float(bind_pos[1]) + float(vmd_pos[1]) * context.motion_scale
                tz = float(bind_pos[2]) - float(vmd_pos[2]) * context.motion_scale
                rx, ry, rz = rotation_by_time[maya_time]
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

        animation_layer = context.anim_layer if use_layer else None
        if animation_layer:
            channel_samples = context.samples_as_anim_layer_deltas(joint, channel_samples)

        if context.batch_key_scalar_channels(joint, channel_samples, animation_layer=animation_layer):
            quaternion_plugs = _configure_sparse_rotation_track(
                context,
                joint,
                frames,
                vmd_bone_name,
                key_route,
                skip_rotate=skip_rotate,
                animation_layer=animation_layer,
            )
            tangent_attrs = [
                attr
                for attr in attrs
                if not (quaternion_plugs and attr.startswith("rotate"))
            ]
            context.apply_vmd_bezier_tangents(
                joint, frames, tangent_attrs, channel_interp_map
            )
            return

        context.logger.debug(f"legacy bone batch keying produced no keys for {joint}; using setKeyframe fallback")

    routed_samples: Dict[str, Dict[str, List[tuple]]] = {}
    with vmd_profile.scope("channel_sample_build", count=len(frames)):
        for frame in frames:
            if hasattr(frame, "frame_number"):
                frame_number = frame.frame_number
                vmd_pos = frame.position
            else:
                frame_number = frame.get("frame_number", 0)
                vmd_pos = frame.get("position", [0, 0, 0])
            maya_time = context.vmd_frame_to_maya_time(frame_number)

            tx = float(bind_pos[0]) + float(vmd_pos[0]) * context.motion_scale
            ty = float(bind_pos[1]) + float(vmd_pos[1]) * context.motion_scale
            tz = float(bind_pos[2]) - float(vmd_pos[2]) * context.motion_scale

            values = {
                "translateX": tx,
                "translateY": ty,
                "translateZ": tz,
            }
            # Control Rig EDIT owns an additive translate baseline between
            # each controller and the joint.  Controller keys are therefore
            # motion deltas; writing the ordinary joint-space ``bind + VMD``
            # value here would apply the bind translation twice.  Append and
            # other legacy routes are intentionally left absolute.
            for index, attr in enumerate(("translateX", "translateY", "translateZ")):
                if attr in control_owned_channels:
                    values[attr] -= float(bind_pos[index])
            if not skip_rotate:
                rx, ry, rz = rotation_by_time[maya_time]
                rotation_values = {
                    "rotateX": rx,
                    "rotateY": ry,
                    "rotateZ": rz,
                }
                values.update(
                    {
                        attr: rotation_values[attr]
                        for attr in rotation_attrs
                    }
                )

            for attr, value in values.items():
                target_node, target_attr = attr_targets.get(attr, (joint, attr))
                routed_samples.setdefault(target_node, {}).setdefault(target_attr, []).append((maya_time, float(value)))

    animation_layer = context.anim_layer if use_layer else None
    routed_success = False
    for target_node, target_samples in routed_samples.items():
        keyed_samples = (
            context.samples_as_anim_layer_deltas(target_node, target_samples)
            if animation_layer
            else target_samples
        )
        if context.batch_key_scalar_channels(target_node, keyed_samples, animation_layer=animation_layer):
            routed_success = True
            continue

        context.logger.debug(f"routed bone batch keying produced no keys for {target_node}; using setKeyframe fallback")
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
                    key_args["animLayer"] = context.anim_layer
                with vmd_profile.scope("fallback_setKeyframe"):
                    cmds.setKeyframe(target_node, **key_args)
                routed_success = True

    if not routed_success and routed_samples:
        context.logger.debug(f"legacy bone routed keying produced no keys for {joint}")

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
        for maya_time, rotation in rotation_samples:
            for attr, value in zip(ir_attrs, rotation):
                solver_samples[attr].append((maya_time, float(value)))
        if not context.batch_key_scalar_channels(solver_node, solver_samples, animation_layer=None):
            context.logger.debug(f"IK solver batch keying produced no keys for {solver_node}; using setKeyframe fallback")
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

    quaternion_plugs = _configure_sparse_rotation_track(
        context,
        joint,
        frames,
        vmd_bone_name,
        key_route,
        skip_rotate=skip_rotate,
        animation_layer=animation_layer,
    )

    skip_rotate_tangent = skip_rotate or bool(quaternion_plugs)
    tangent_targets = {
        attr: attr_targets.get(attr, (joint, attr))
        for attr in attrs
        if not (skip_rotate_tangent and attr.startswith("rotate"))
    }
    context.apply_vmd_bezier_tangents(joint, frames, tangent_targets, channel_interp_map)

    if ik_info and solver_node and slot is not None:
        solver_tangent_targets = {
            "rotateX": (solver_node, f"inputRotate[{slot}].inputRotateElementX"),
            "rotateY": (solver_node, f"inputRotate[{slot}].inputRotateElementY"),
            "rotateZ": (solver_node, f"inputRotate[{slot}].inputRotateElementZ"),
        }
        context.apply_vmd_bezier_tangents(joint, frames, solver_tangent_targets, channel_interp_map)
