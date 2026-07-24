"""Minimum Maya scene collector for VMD export.

This collector gathers keyed joint transforms and blendShape weights into the
dict contract consumed by ``VmdExporter``. Bone translation can be converted
back to VMD offsets when a bind-pose map is supplied, and XYZ joint rotations
are converted back to VMD quaternions with jointOrient compensation.
"""

import json
import math
from typing import Any, Iterable, Mapping, Optional, Sequence

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_EDIT,
    read_mmd_control_rig_metadata,
)
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_names
from mmd_tools.converters.vmd_camera_animation import (
    ATTR_MMD_CAMERA_ROOT_NODE,
    ATTR_MMD_CAMERA_TARGET_NODE,
    MMD_CAMERA_EXPR_SCALE_ATTR,
    mmd_camera_rotation_from_maya_forward_up,
)
from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name


_BONE_EXPORT_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)
_CAMERA_EXPORT_ATTRS = (
    "translateX",
    "translateY",
    "translateZ",
    "mmd_camera_target_x",
    "mmd_camera_target_y",
    "mmd_camera_target_z",
    "rotateX",
    "rotateY",
    "rotateZ",
    "mmd_camera_rotation_x",
    "mmd_camera_rotation_y",
    "mmd_camera_rotation_z",
    "mmd_camera_distance",
    "mmd_camera_viewing_angle",
    "mmd_camera_perspective",
)
_LIGHT_ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
_LIGHT_COLOR_ATTRS = ("mmd_light_colorR", "mmd_light_colorG", "mmd_light_colorB")
_ATTR_MMD_CAMERA_RIG_TYPE = "mmd_camera_rig_type"
_MMD_CAMERA_AIM_ROLL_RIG_TYPE = "mmd_aim_roll"
_TRANSFORM_EXPORT_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CAMERA_SHAPE_EXPORT_ATTRS = ("focalLength", "orthographic", "orthographicWidth")


class VmdSceneCollector:
    """Collect minimum VMD-compatible animation data from a Maya scene."""

    def collect(self, options: Optional[Mapping[str, Any]] = None) -> dict:
        """Collect VMD exporter input from the current Maya scene.

        Args:
            options: Optional mapping. Supported keys are ``target_model``,
                ``joints``, ``blend_shapes``, ``start_frame``, ``end_frame``,
                ``model_name``, ``motion_scale``, and ``bone_bind_poses``.
        """
        options = options or {}
        target_model = options.get("target_model")
        joints = list(options.get("joints") or self._find_joints(target_model))
        blend_shapes = list(options.get("blend_shapes") or self._find_blend_shapes())
        cameras = list(options.get("cameras") or self._find_tagged_nodes(ATTR_MMD_CAMERA))
        lights = list(options.get("lights") or self._find_tagged_nodes(ATTR_MMD_LIGHT))
        start_frame = _optional_float(options.get("start_frame"))
        end_frame = _optional_float(options.get("end_frame"))
        motion_scale = float(options.get("motion_scale", 1.0) or 1.0)
        bone_bind_poses = options.get("bone_bind_poses") or {}
        control_rig_routes = self._control_rig_export_routes(target_model)

        return {
            "model_name": str(options.get("model_name") or self._model_name(target_model)),
            "bone_frames": self.collect_bone_frames(
                joints,
                start_frame,
                end_frame,
                motion_scale=motion_scale,
                bone_bind_poses=bone_bind_poses,
                input_routes=control_rig_routes,
            ),
            "morph_frames": self.collect_morph_frames(blend_shapes, start_frame, end_frame),
            "camera_frames": self.collect_camera_frames(cameras, start_frame, end_frame),
            "light_frames": self.collect_light_frames(lights, start_frame, end_frame),
            "ik_show_hide_frames": self.collect_ik_show_hide_frames(
                target_model,
                start_frame,
                end_frame,
            ),
        }

    def collect_bone_frames(
        self,
        joints: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        motion_scale: float = 1.0,
        bone_bind_poses: Optional[Mapping[str, Sequence[float]]] = None,
        input_routes: Optional[Mapping[str, Mapping[str, tuple[str, str]]]] = None,
    ) -> list[dict]:
        """Collect keyed local joint transform frames."""
        bone_bind_poses = bone_bind_poses or {}
        input_routes = input_routes or {}
        frames = []
        for joint in joints:
            bone_name = self._mmd_bone_name(joint)
            bind_pose = _resolve_bind_pose(bone_bind_poses, bone_name, joint)
            long_names = cmds.ls(joint, long=True) or [joint]
            route = input_routes.get(str(long_names[0]), {})
            keyed_frames = _filter_frame_range(
                _routed_key_times(joint, route),
                start_frame,
                end_frame,
            )
            for frame_number in keyed_frames:
                rotation = _maya_joint_rotate_to_vmd_quaternion(
                    joint,
                    _routed_plug_float(joint, "rotateX", frame_number, route),
                    _routed_plug_float(joint, "rotateY", frame_number, route),
                    _routed_plug_float(joint, "rotateZ", frame_number, route),
                )
                frames.append(
                    {
                        "bone_name": bone_name,
                        "frame_number": int(round(frame_number)),
                        "position": _maya_translate_to_vmd_position(
                            (
                                _routed_plug_float(joint, "translateX", frame_number, route),
                                _routed_plug_float(joint, "translateY", frame_number, route),
                                _routed_plug_float(joint, "translateZ", frame_number, route),
                            ),
                            bind_pose,
                            motion_scale,
                        ),
                        "rotation": rotation,
                    }
                )
        frames.sort(key=lambda item: (item["bone_name"], item["frame_number"]))
        return frames

    def collect_ik_show_hide_frames(
        self,
        target_model: Optional[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
    ) -> list[dict]:
        """Collect keyed owned ``mmdCcdIk.enabled`` values as VMD properties."""
        if not target_model:
            return []
        nodes_by_name = collect_ik_nodes_by_bone_name(target_model=target_model)
        keyed_frames = _filter_frame_range(
            sorted(
                {
                    frame
                    for node in nodes_by_name.values()
                    for frame in _key_times(node, ("enabled",))
                }
            ),
            start_frame,
            end_frame,
        )
        return [
            {
                "frame_number": int(round(frame)),
                "visible": True,
                "ik_states": [
                    (name, bool(_plug_float(node, "enabled", frame)))
                    for name, node in sorted(nodes_by_name.items())
                ],
            }
            for frame in keyed_frames
        ]

    def _control_rig_export_routes(
        self,
        target_model: Optional[str],
    ) -> dict[str, dict[str, tuple[str, str]]]:
        if not target_model:
            return {}
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return {}
        if metadata["state"] == CONTROL_RIG_EDIT:
            raise ValueError("Bake the MMD control rig before VMD export")
        routes = {}
        for binding in metadata.get("bindings", {}).values():
            joint_names = cmds.ls(binding.get("joint"), long=True) or []
            if len(joint_names) != 1:
                continue
            joint = str(joint_names[0])
            for plug in binding.get("authoredPlugs", []):
                if plug.endswith((".translate", ".rotate")):
                    base_attr = plug.rsplit(".", 1)[-1]
                    node = plug.rsplit(".", 1)[0]
                    for axis in "XYZ":
                        attr = f"{base_attr}{axis}"
                        routes.setdefault(joint, {})[attr] = (node, attr)
        return routes

    def collect_morph_frames(
        self,
        blend_shapes: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
    ) -> list[dict]:
        """Collect keyed blendShape weight frames."""
        frames = []
        for blend_shape in blend_shapes:
            for weight_index, morph_name in self._blendshape_morph_names(blend_shape).items():
                attr = f"weight[{weight_index}]"
                keyed_frames = _filter_frame_range(
                    _key_times(blend_shape, (attr,)),
                    start_frame,
                    end_frame,
                )
                for frame_number in keyed_frames:
                    frames.append(
                        {
                            "morph_name": morph_name,
                            "frame_number": int(round(frame_number)),
                            "weight": _plug_float(blend_shape, attr, frame_number),
                        }
                    )
        frames.sort(key=lambda item: (item["morph_name"], item["frame_number"]))
        return frames

    def collect_camera_frames(
        self,
        cameras: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
    ) -> list[dict]:
        """Collect keyed MMD camera controller frames."""
        frames = []
        restore_time = None
        try:
            for camera in cameras:
                camera_target = _camera_target_node(camera)
                camera_root = _camera_root_node(camera)
                camera_shape = _camera_shape(camera)
                keyed_frames = _filter_frame_range(
                    sorted(
                        set(_key_times(camera, _CAMERA_EXPORT_ATTRS))
                        | (set(_key_times(camera_root, _BONE_EXPORT_ATTRS)) if camera_root else set())
                        | (set(_key_times(camera_target, _TRANSFORM_EXPORT_ATTRS)) if camera_target else set())
                        | (set(_key_times(camera_shape, _CAMERA_SHAPE_EXPORT_ATTRS)) if camera_shape else set())
                    ),
                    start_frame,
                    end_frame,
                )
                for frame_number in keyed_frames:
                    uses_raw_mmd_attrs = _uses_raw_mmd_camera_attrs(camera)
                    uses_aim_roll_rig = _uses_aim_roll_camera(camera) and camera_target
                    if uses_aim_roll_rig:
                        if restore_time is None:
                            restore_time = _query_current_time()
                        cmds.currentTime(frame_number, edit=True)
                        motion_scale = _camera_motion_scale(camera)
                        eye = om.MVector(*cmds.xform(camera, query=True, worldSpace=True, translation=True))
                        target = om.MVector(*cmds.xform(camera_target, query=True, worldSpace=True, translation=True))
                        position = (
                            float(target.x) / motion_scale,
                            float(target.y) / motion_scale,
                            -float(target.z) / motion_scale,
                        )
                        matrix = om.MMatrix(cmds.getAttr(f"{camera}.worldMatrix[0]"))
                        forward = om.MVector(0.0, 0.0, -1.0) * matrix
                        up = om.MVector(0.0, 1.0, 0.0) * matrix
                        if forward.length() > 1e-12:
                            forward.normalize()
                        if up.length() > 1e-12:
                            up.normalize()
                        distance = _signed_camera_distance(eye, target, forward) / motion_scale
                        rotation = mmd_camera_rotation_from_maya_forward_up(
                            (forward.x, forward.y, forward.z),
                            (up.x, up.y, up.z),
                        )
                        viewing_angle = _camera_viewing_angle(camera, camera_shape, frame_number)
                        perspective = _camera_perspective_value(camera, camera_shape, frame_number)
                    elif uses_raw_mmd_attrs and all(
                        _has_attr(camera, attr) for attr in ("mmd_camera_target_x", "mmd_camera_target_y", "mmd_camera_target_z")
                    ):
                        position = (
                            _plug_float(camera, "mmd_camera_target_x", frame_number),
                            _plug_float(camera, "mmd_camera_target_y", frame_number),
                            _plug_float(camera, "mmd_camera_target_z", frame_number),
                        )
                    else:
                        position = (
                            _plug_float(camera, "translateX", frame_number),
                            _plug_float(camera, "translateY", frame_number),
                            -_plug_float(camera, "translateZ", frame_number),
                        )
                    if not uses_aim_roll_rig:
                        if uses_raw_mmd_attrs and all(
                            _has_attr(camera, attr)
                            for attr in ("mmd_camera_rotation_x", "mmd_camera_rotation_y", "mmd_camera_rotation_z")
                        ):
                            rotation = (
                                _plug_float(camera, "mmd_camera_rotation_x", frame_number),
                                _plug_float(camera, "mmd_camera_rotation_y", frame_number),
                                _plug_float(camera, "mmd_camera_rotation_z", frame_number),
                            )
                        else:
                            rotation = (
                                math.radians(_plug_float(camera, "rotateX", frame_number)),
                                math.radians(_plug_float(camera, "rotateY", frame_number)),
                                -math.radians(_plug_float(camera, "rotateZ", frame_number)),
                            )
                        distance = _plug_float(camera, "mmd_camera_distance", frame_number)
                        viewing_angle = int(round(_plug_float(camera, "mmd_camera_viewing_angle", frame_number)))
                        perspective = int(round(_plug_float(camera, "mmd_camera_perspective", frame_number)))
                    frames.append(
                        {
                            "frame_number": int(round(frame_number)),
                            "distance": distance,
                            "position": position,
                            "rotation": rotation,
                            "viewing_angle": viewing_angle,
                            "perspective": perspective,
                        }
                    )
        finally:
            if restore_time is not None:
                cmds.currentTime(restore_time, edit=True)
        frames.sort(key=lambda item: item["frame_number"])
        return frames

    def collect_light_frames(
        self,
        lights: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
    ) -> list[dict]:
        """Collect keyed MMD light controller frames."""
        frames = []
        for light in lights:
            keyed_frames = _filter_frame_range(
                _key_times(light, _LIGHT_COLOR_ATTRS + _LIGHT_ROTATE_ATTRS),
                start_frame,
                end_frame,
            )
            for frame_number in keyed_frames:
                frames.append(
                    {
                        "frame_number": int(round(frame_number)),
                        "color": tuple(_plug_float(light, attr, frame_number) for attr in _LIGHT_COLOR_ATTRS),
                        "position": _maya_light_rotation_to_vmd_direction(
                            _plug_float(light, "rotateX", frame_number),
                            _plug_float(light, "rotateY", frame_number),
                        ),
                    }
                )
        frames.sort(key=lambda item: item["frame_number"])
        return frames

    def _find_joints(self, target_model: Optional[str]) -> list[str]:
        if not target_model:
            return cmds.ls(type="joint") or []
        descendants = cmds.listRelatives(target_model, allDescendents=True, type="joint", fullPath=True) or []
        nodes = []
        if cmds.nodeType(target_model) == "joint":
            nodes.append(target_model)
        nodes.extend(descendants)
        return nodes

    def _find_blend_shapes(self) -> list[str]:
        return cmds.ls(type="blendShape") or []

    def _find_tagged_nodes(self, attr: str) -> list[str]:
        return cmds.ls(f"*.{attr}", objectsOnly=True) or []

    def _model_name(self, target_model: Optional[str]) -> str:
        if target_model and _has_attr(target_model, ATTR_MMD_MODEL_NAME):
            value = cmds.getAttr(f"{target_model}.{ATTR_MMD_MODEL_NAME}")
            if value:
                return str(value)
        return str(target_model or "")

    def _mmd_bone_name(self, joint: str) -> str:
        if _has_attr(joint, ATTR_MMD_BONE_NAME):
            value = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
            if value:
                return str(value)
        return _leaf_name(joint)

    def _blendshape_morph_names(self, blend_shape: str) -> dict[int, str]:
        stored = _read_blendshape_morph_names(blend_shape)
        weight_count = int(cmds.blendShape(blend_shape, query=True, weightCount=True) or 0)
        result = {}
        for weight_index in range(weight_count):
            alias = cmds.aliasAttr(f"{blend_shape}.weight[{weight_index}]", query=True)
            result[weight_index] = stored.get(weight_index) or alias or f"weight[{weight_index}]"
        return result


def _read_blendshape_morph_names(blend_shape: str) -> dict[int, str]:
    if not _has_attr(blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
        return {}
    try:
        raw = cmds.getAttr(f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parse_blendshape_morph_names(parsed)


def _key_times(node: str, attrs: Iterable[str]) -> list[float]:
    if not node:
        return []
    times = []
    for attr in attrs:
        plug = f"{node}.{attr}"
        try:
            values = cmds.keyframe(plug, query=True, timeChange=True) or []
        except Exception:
            values = []
        times.extend(float(value) for value in values)
    return sorted(set(times))


def _routed_key_times(
    joint: str,
    route: Mapping[str, tuple[str, str]],
) -> list[float]:
    times = []
    for attr in _BONE_EXPORT_ATTRS:
        node, target_attr = route.get(attr, (joint, attr))
        times.extend(_key_times(node, (target_attr,)))
    return sorted(set(times))


def _routed_plug_float(
    joint: str,
    attr: str,
    frame_number: float,
    route: Mapping[str, tuple[str, str]],
) -> float:
    node, target_attr = route.get(attr, (joint, attr))
    return _plug_float(node, target_attr, frame_number)


def _query_current_time() -> Optional[float]:
    try:
        return float(cmds.currentTime(query=True))
    except Exception:
        return None


def _filter_frame_range(
    frames: Iterable[float],
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> list[float]:
    result = []
    for frame in sorted(set(float(value) for value in frames)):
        if start_frame is not None and frame < start_frame:
            continue
        if end_frame is not None and frame > end_frame:
            continue
        result.append(frame)
    return result


def _plug_float(node: str, attr: str, frame: float) -> float:
    value = cmds.getAttr(f"{node}.{attr}", time=frame)
    if isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0][0]
        else:
            value = value[0]
    return float(value or 0.0)


def _camera_shape(camera: str) -> Optional[str]:
    shapes = cmds.listRelatives(camera, shapes=True, type="camera") or []
    return shapes[0] if shapes else None


def _camera_viewing_angle(camera: str, camera_shape: Optional[str], frame: float) -> int:
    if camera_shape:
        focal_length = _plug_float(camera_shape, "focalLength", frame)
        if abs(focal_length) > 1e-9:
            aperture_inch = _plug_float(camera_shape, "verticalFilmAperture", frame)
            aperture_mm = aperture_inch * 25.4
            return int(round(math.degrees(2.0 * math.atan(aperture_mm / (2.0 * focal_length)))))
    if _has_attr(camera, "mmd_camera_viewing_angle"):
        return int(round(_plug_float(camera, "mmd_camera_viewing_angle", frame)))
    return 45


def _camera_perspective_value(camera: str, camera_shape: Optional[str], frame: float) -> int:
    if camera_shape and _has_attr(camera_shape, "orthographic"):
        return int(round(_plug_float(camera_shape, "orthographic", frame)))
    if _has_attr(camera, "mmd_camera_perspective"):
        return int(round(_plug_float(camera, "mmd_camera_perspective", frame)))
    return 0


def _resolve_bind_pose(
    bind_poses: Mapping[str, Sequence[float]],
    bone_name: str,
    joint: str,
) -> tuple[float, float, float]:
    value = bind_poses.get(bone_name, bind_poses.get(joint, (0.0, 0.0, 0.0)))
    if len(value) != 3:
        raise ValueError("bone bind pose must contain 3 numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


def _maya_translate_to_vmd_position(
    translate: Sequence[float],
    bind_pose: Sequence[float],
    motion_scale: float,
) -> tuple[float, float, float]:
    if abs(float(motion_scale)) < 1e-12:
        raise ValueError("motion_scale must not be zero")
    tx, ty, tz = (float(translate[0]), float(translate[1]), float(translate[2]))
    bx, by, bz = (float(bind_pose[0]), float(bind_pose[1]), float(bind_pose[2]))
    scale = float(motion_scale)
    return ((tx - bx) / scale, (ty - by) / scale, -(tz - bz) / scale)


def _maya_joint_rotate_to_vmd_quaternion(
    joint: str,
    rx: float,
    ry: float,
    rz: float,
) -> tuple[float, float, float, float]:
    """Convert Maya XYZ joint.rotate degrees to a JO-aware VMD quaternion."""
    joint_orient = _joint_orient_values(joint)
    if joint_orient is not None:
        openmaya_result = _openmaya_joint_rotate_to_vmd_quaternion(rx, ry, rz, joint_orient)
        if openmaya_result is not None:
            return openmaya_result

    q_rotate = _euler_xyz_degrees_to_quaternion(rx, ry, rz)
    q_jo = _euler_xyz_degrees_to_quaternion(*joint_orient) if joint_orient is not None else None
    if q_jo is not None:
        q_maya = _quat_multiply(_quat_multiply(_quat_inverse(q_jo), q_rotate), q_jo)
    else:
        q_maya = q_rotate
    q_maya = _quat_normalize(q_maya)
    return (-q_maya[0], -q_maya[1], q_maya[2], q_maya[3])


def _joint_orient_values(joint: str) -> Optional[tuple[float, float, float]]:
    if not _has_attr(joint, "jointOrient"):
        return None
    value = _attr_tuple(joint, "jointOrient")
    if len(value) != 3 or not any(abs(item) > 1e-8 for item in value):
        return None
    return (float(value[0]), float(value[1]), float(value[2]))


def _openmaya_joint_rotate_to_vmd_quaternion(
    rx: float,
    ry: float,
    rz: float,
    joint_orient: Sequence[float],
) -> Optional[tuple[float, float, float, float]]:
    try:
        import maya.api.OpenMaya as om

        q_rotate = om.MEulerRotation(
            math.radians(rx),
            math.radians(ry),
            math.radians(rz),
        ).asQuaternion()
        q_jo = om.MEulerRotation(
            math.radians(joint_orient[0]),
            math.radians(joint_orient[1]),
            math.radians(joint_orient[2]),
        ).asQuaternion()
        q_maya = q_jo.inverse() * q_rotate * q_jo
        q_maya.normalizeIt()
        return (-q_maya.x, -q_maya.y, q_maya.z, q_maya.w)
    except Exception:
        return None


def _attr_tuple(node: str, attr: str) -> tuple[float, ...]:
    value = cmds.getAttr(f"{node}.{attr}")
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)):
        return (float(value),)
    return tuple(float(item) for item in value)


def _euler_xyz_degrees_to_quaternion(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    """Convert XYZ Euler degrees to a Maya quaternion tuple."""
    hx = math.radians(rx) * 0.5
    hy = math.radians(ry) * 0.5
    hz = math.radians(rz) * 0.5
    sx, cx = math.sin(hx), math.cos(hx)
    sy, cy = math.sin(hy), math.cos(hy)
    sz, cz = math.sin(hz), math.cos(hz)
    return (
        sx * cy * cz + cx * sy * sz,
        cx * sy * cz - sx * cy * sz,
        cx * cy * sz + sx * sy * cz,
        cx * cy * cz - sx * sy * sz,
    )


def _quat_multiply(
    left: Sequence[float],
    right: Sequence[float],
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _quat_inverse(quat: Sequence[float]) -> tuple[float, float, float, float]:
    x, y, z, w = quat
    norm_sq = x * x + y * y + z * z + w * w
    if norm_sq <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    return (-x / norm_sq, -y / norm_sq, -z / norm_sq, w / norm_sq)


def _quat_normalize(quat: Sequence[float]) -> tuple[float, float, float, float]:
    x, y, z, w = quat
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / length, y / length, z / length, w / length)


def _maya_light_rotation_to_vmd_direction(rx: float, ry: float) -> tuple[float, float, float]:
    """Invert VmdConverter._convert_light_animation's direction-to-Euler mapping."""
    rx_rad = math.radians(rx)
    ry_rad = math.radians(ry)
    cos_rx = math.cos(rx_rad)
    maya_x = -math.sin(ry_rad) * cos_rx
    maya_y = math.sin(rx_rad)
    maya_z = -math.cos(ry_rad) * cos_rx
    return (maya_x, maya_y, -maya_z)


def _has_attr(node: str, attr: str) -> bool:
    try:
        return bool(cmds.attributeQuery(attr, node=node, exists=True))
    except Exception:
        return False


def _uses_raw_mmd_camera_attrs(camera: str) -> bool:
    if not _has_attr(camera, _ATTR_MMD_CAMERA_RIG_TYPE):
        return False
    try:
        return cmds.getAttr(f"{camera}.{_ATTR_MMD_CAMERA_RIG_TYPE}") == "mmd"
    except Exception:
        return False


def _uses_aim_roll_camera(camera: str) -> bool:
    if not _has_attr(camera, _ATTR_MMD_CAMERA_RIG_TYPE):
        return False
    try:
        return cmds.getAttr(f"{camera}.{_ATTR_MMD_CAMERA_RIG_TYPE}") == _MMD_CAMERA_AIM_ROLL_RIG_TYPE
    except Exception:
        return False


def _camera_target_node(camera: str) -> Optional[str]:
    if not _has_attr(camera, ATTR_MMD_CAMERA_TARGET_NODE):
        return None
    targets = cmds.listConnections(
        f"{camera}.{ATTR_MMD_CAMERA_TARGET_NODE}",
        source=True,
        destination=False,
    ) or []
    return targets[0] if targets else None


def _camera_root_node(camera: str) -> Optional[str]:
    if not _has_attr(camera, ATTR_MMD_CAMERA_ROOT_NODE):
        return None
    roots = cmds.listConnections(
        f"{camera}.{ATTR_MMD_CAMERA_ROOT_NODE}",
        source=True,
        destination=False,
    ) or []
    return roots[0] if roots else None


def _camera_motion_scale(camera: str) -> float:
    if _has_attr(camera, MMD_CAMERA_EXPR_SCALE_ATTR):
        scale = _plug_float(camera, MMD_CAMERA_EXPR_SCALE_ATTR, _query_current_time())
        if abs(scale) > 1e-12:
            return scale
    return 1.0


def _signed_camera_distance(eye: om.MVector, target: om.MVector, forward: om.MVector) -> float:
    target_from_eye = target - eye
    distance = target_from_eye.length()
    if distance <= 1e-12:
        return 0.0
    forward_normal = om.MVector(forward.x, forward.y, forward.z)
    if forward_normal.length() <= 1e-12:
        return -distance
    forward_normal.normalize()
    return -distance if target_from_eye * forward_normal >= 0.0 else distance


def _leaf_name(node: str) -> str:
    leaf = node.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[-1]


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)
