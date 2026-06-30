"""Minimum Maya scene collector for VMD export.

This collector gathers keyed joint transforms and blendShape weights into the
dict contract consumed by ``VmdExporter``. Bone translation can be converted
back to VMD offsets when a bind-pose map is supplied, and XYZ joint rotations
are converted back to VMD quaternions with jointOrient compensation.
"""

import json
import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_MODEL_NAME,
)


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

        return {
            "model_name": str(options.get("model_name") or self._model_name(target_model)),
            "bone_frames": self.collect_bone_frames(
                joints,
                start_frame,
                end_frame,
                motion_scale=motion_scale,
                bone_bind_poses=bone_bind_poses,
            ),
            "morph_frames": self.collect_morph_frames(blend_shapes, start_frame, end_frame),
            "camera_frames": self.collect_camera_frames(cameras, start_frame, end_frame),
            "light_frames": self.collect_light_frames(lights, start_frame, end_frame),
        }

    def collect_bone_frames(
        self,
        joints: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        motion_scale: float = 1.0,
        bone_bind_poses: Optional[Mapping[str, Sequence[float]]] = None,
    ) -> list[dict]:
        """Collect keyed local joint transform frames."""
        bone_bind_poses = bone_bind_poses or {}
        frames = []
        for joint in joints:
            bone_name = self._mmd_bone_name(joint)
            bind_pose = _resolve_bind_pose(bone_bind_poses, bone_name, joint)
            keyed_frames = _filter_frame_range(
                _key_times(joint, _BONE_EXPORT_ATTRS),
                start_frame,
                end_frame,
            )
            for frame_number in keyed_frames:
                rotation = _maya_joint_rotate_to_vmd_quaternion(
                    joint,
                    _plug_float(joint, "rotateX", frame_number),
                    _plug_float(joint, "rotateY", frame_number),
                    _plug_float(joint, "rotateZ", frame_number),
                )
                frames.append(
                    {
                        "bone_name": bone_name,
                        "frame_number": int(round(frame_number)),
                        "position": _maya_translate_to_vmd_position(
                            (
                                _plug_float(joint, "translateX", frame_number),
                                _plug_float(joint, "translateY", frame_number),
                                _plug_float(joint, "translateZ", frame_number),
                            ),
                            bind_pose,
                            motion_scale,
                        ),
                        "rotation": rotation,
                    }
                )
        frames.sort(key=lambda item: (item["bone_name"], item["frame_number"]))
        return frames

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
        for camera in cameras:
            keyed_frames = _filter_frame_range(
                _key_times(camera, _CAMERA_EXPORT_ATTRS),
                start_frame,
                end_frame,
            )
            for frame_number in keyed_frames:
                uses_raw_mmd_attrs = _uses_raw_mmd_camera_attrs(camera)
                if uses_raw_mmd_attrs and all(
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
                if uses_raw_mmd_attrs and all(
                    _has_attr(camera, attr) for attr in ("mmd_camera_rotation_x", "mmd_camera_rotation_y", "mmd_camera_rotation_z")
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
                frames.append(
                    {
                        "frame_number": int(round(frame_number)),
                        "distance": _plug_float(camera, "mmd_camera_distance", frame_number),
                        "position": position,
                        "rotation": rotation,
                        "viewing_angle": int(round(_plug_float(camera, "mmd_camera_viewing_angle", frame_number))),
                        "perspective": int(round(_plug_float(camera, "mmd_camera_perspective", frame_number))),
                    }
                )
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
    if not isinstance(parsed, dict):
        return {}
    result = {}
    for key, value in parsed.items():
        try:
            result[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return result


def _key_times(node: str, attrs: Iterable[str]) -> list[float]:
    times = []
    for attr in attrs:
        plug = f"{node}.{attr}"
        try:
            values = cmds.keyframe(plug, query=True, timeChange=True) or []
        except Exception:
            values = []
        times.extend(float(value) for value in values)
    return sorted(set(times))


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


def _leaf_name(node: str) -> str:
    leaf = node.rsplit("|", 1)[-1]
    return leaf.rsplit(":", 1)[-1]


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)
