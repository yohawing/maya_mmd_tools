"""Minimum Maya scene collector for VMD export.

This collector gathers keyed joint transforms, blendShape weights, and
model-scoped PMX network morph controller weights into the dict contract
consumed by ``VmdExporter``. Bone translation can be converted back to VMD
offsets when a bind-pose map is supplied, and XYZ joint rotations are
converted back to VMD quaternions with jointOrient compensation.
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
    resolve_mmd_control_rig_binding_authored_plugs,
    resolve_mmd_control_rig_binding_joint,
)
from mmd_tools.core.mmd_control_rig_analyzer import (
    INPUT_BONE_MORPH_BASE,
    INPUT_IK_CONTROLLER,
)
from mmd_tools.core.morph_metadata_reader import parse_blendshape_morph_names
from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata
from mmd_tools.converters.vmd_camera_animation import (
    ATTR_MMD_CAMERA_ROOT_NODE,
    ATTR_MMD_CAMERA_TARGET_NODE,
    MMD_CAMERA_EXPR_SCALE_ATTR,
    mmd_camera_rotation_from_maya_forward_up,
)
from mmd_tools.converters.vmd_append_decomposition import collect_append_info
from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name
from mmd_tools.converters.vmd_ik_passthrough import collect_mmd_ik_passthrough_info
from mmd_tools.converters.vmd_import_state import get_stored_bind_translate
from mmd_tools.converters.vmd_runtime_sampling import (
    maya_time_to_vmd_frame as _maya_time_to_vmd_frame_at_fps,
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
_MMD_CAMERA_AIM_ROLL_RIG_TYPE = "mmd_aim_roll"
_TRANSFORM_EXPORT_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CAMERA_SHAPE_EXPORT_ATTRS = ("focalLength", "orthographic", "orthographicWidth")
_MAYA_TIME_UNIT_FPS = {
    "game": 15.0,
    "film": 24.0,
    "pal": 25.0,
    "ntsc": 30.0,
    "show": 48.0,
    "palf": 50.0,
    "ntscf": 60.0,
}


def _canonical_dag_path(node: str) -> Optional[str]:
    """Resolve one Maya node to an unambiguous long DAG path."""
    try:
        matches = cmds.ls(node, long=True) or []
    except Exception:
        return None
    if len(matches) != 1:
        return None
    return str(matches[0])


def _dag_path_is_under_root(node: str, root_path: str) -> bool:
    """Return whether a DAG node is the root or a descendant of it."""
    node_path = _canonical_dag_path(node)
    if not node_path:
        return False
    return node_path == root_path or node_path.startswith(f"{root_path}|")


class VmdSceneCollector:
    """Collect minimum VMD-compatible animation data from a Maya scene."""

    def collect(self, options: Optional[Mapping[str, Any]] = None) -> dict:
        """Collect VMD exporter input from the current Maya scene.

        Args:
            options: Optional mapping. Supported keys are ``target_model`` /
                ``model_root``, ``joints``, ``blend_shapes``, ``cameras``,
                ``lights``, ``start_frame``, ``end_frame``, ``model_name``,
                ``motion_scale``, and ``bone_bind_poses``. Automatic DAG
                discovery is scoped to the selected model root; explicit node
                lists remain authoritative for scene-level callers.
        """
        options = options or {}
        target_model = options.get("target_model") or options.get("model_root")
        joints = list(options.get("joints") or self._find_joints(target_model))
        blend_shapes = list(
            options.get("blend_shapes") or self._find_blend_shapes(target_model)
        )
        cameras = list(
            options.get("cameras")
            or self._find_tagged_nodes(ATTR_MMD_CAMERA, target_model)
        )
        lights = list(
            options.get("lights")
            or self._find_tagged_nodes(ATTR_MMD_LIGHT, target_model)
        )
        start_frame = _optional_float(options.get("start_frame"))
        end_frame = _optional_float(options.get("end_frame"))
        motion_scale = float(options.get("motion_scale", 1.0) or 1.0)
        bone_bind_poses = options.get("bone_bind_poses") or {}
        maya_time_to_vmd = _scene_maya_time_to_vmd_frame()
        dense_control_rig_export = self._control_rig_dense_export(target_model)
        rotation_interpolation = self._rotation_time_curve_interpolation(target_model)
        authored_routes = self._scene_authored_input_routes(joints, target_model)

        return {
            "model_name": str(options.get("model_name") or self._model_name(target_model)),
            "bone_frames": self.collect_bone_frames(
                joints,
                start_frame,
                end_frame,
                motion_scale=motion_scale,
                bone_bind_poses=bone_bind_poses,
                input_routes=authored_routes,
                dense_sample=dense_control_rig_export,
                time_converter=maya_time_to_vmd,
                rotation_interpolation=rotation_interpolation,
            ),
            "morph_frames": self.collect_morph_frames(
                blend_shapes,
                start_frame,
                end_frame,
                time_converter=maya_time_to_vmd,
                target_model=target_model,
            ),
            "camera_frames": self.collect_camera_frames(
                cameras,
                start_frame,
                end_frame,
                time_converter=maya_time_to_vmd,
            ),
            "light_frames": self.collect_light_frames(
                lights,
                start_frame,
                end_frame,
                time_converter=maya_time_to_vmd,
            ),
            "ik_show_hide_frames": self.collect_ik_show_hide_frames(
                target_model,
                start_frame,
                end_frame,
                time_converter=maya_time_to_vmd,
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
        dense_sample: bool = False,
        time_converter=None,
        rotation_interpolation: Optional[Mapping[str, Mapping[int, bytes]]] = None,
    ) -> list[dict]:
        """Collect keyed local joint transform frames."""
        bone_bind_poses = bone_bind_poses or {}
        input_routes = input_routes or {}
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        rotation_context = _build_rotation_export_context(joints)
        rotation_interpolation = rotation_interpolation or {}
        frames = []
        dense_frames = None
        if dense_sample:
            all_keyed = []
            for joint in joints:
                long_names = cmds.ls(joint, long=True) or [joint]
                route = input_routes.get(str(long_names[0]), {})
                all_keyed.extend(_routed_key_times(joint, route))
            ranged = _filter_frame_range(all_keyed, start_frame, end_frame)
            if ranged:
                dense_frames = list(
                    range(int(math.floor(min(ranged))), int(math.ceil(max(ranged))) + 1)
                )
        for joint in joints:
            bone_name = self._mmd_bone_name(joint)
            bind_pose = _resolve_bind_pose(bone_bind_poses, bone_name, joint)
            long_names = cmds.ls(joint, long=True) or [joint]
            route = input_routes.get(str(long_names[0]), {})
            sparse_frames = _filter_frame_range(
                _routed_key_times(joint, route),
                start_frame,
                end_frame,
            )
            preserve_sparse_rotation = bone_name in rotation_interpolation
            keyed_frames = (
                dense_frames
                if dense_frames is not None
                and sparse_frames
                and not preserve_sparse_rotation
                else sparse_frames
            )
            for frame_number in keyed_frames:
                rotation = _maya_joint_rotate_to_vmd_quaternion(
                    joint,
                    _routed_plug_float(joint, "rotateX", frame_number, route),
                    _routed_plug_float(joint, "rotateY", frame_number, route),
                    _routed_plug_float(joint, "rotateZ", frame_number, route),
                    rotation_context.get(str(long_names[0])),
                )
                vmd_frame = _vmd_frame_number(frame_number, time_converter)
                payload = {
                        "bone_name": bone_name,
                        "frame_number": vmd_frame,
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
                interpolation = rotation_interpolation.get(bone_name, {}).get(vmd_frame)
                if interpolation is not None:
                    payload["interpolation"] = interpolation
                frames.append(payload)
        return _deduplicate_frames(frames, ("bone_name", "frame_number"))

    def collect_ik_show_hide_frames(
        self,
        target_model: Optional[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
    ) -> list[dict]:
        """Collect keyed owned ``mmdCcdIk.enabled`` values as VMD properties."""
        if not target_model:
            return []
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
        nodes_by_name = collect_ik_nodes_by_bone_name(target_model=target_model)
        all_keyed_frames = sorted(
            {
                frame
                for node in nodes_by_name.values()
                for frame in _key_times(node, ("enabled",))
            }
        )
        keyed_frames = _filter_frame_range(
            all_keyed_frames,
            start_frame,
            end_frame,
        )
        frames = []
        baseline_time = _ik_baseline_time(start_frame, end_frame)
        if nodes_by_name and baseline_time is not None and baseline_time not in all_keyed_frames:
            baseline_frame = _vmd_frame_number(baseline_time, time_converter)
            if baseline_frame >= 0:
                frames.append(
                    {
                        "frame_number": baseline_frame,
                        "visible": True,
                        "ik_states": [
                            (name, bool(_plug_float(node, "enabled", baseline_time)))
                            for name, node in sorted(nodes_by_name.items())
                        ],
                    }
                )
        for frame in keyed_frames:
            vmd_frame = _vmd_frame_number(frame, time_converter)
            if vmd_frame < 0:
                continue
            frames.append(
                {
                    "frame_number": vmd_frame,
                    "visible": True,
                    "ik_states": [
                        (name, bool(_plug_float(node, "enabled", frame)))
                        for name, node in sorted(nodes_by_name.items())
                    ],
                }
            )
        return _deduplicate_frames(frames, ("frame_number",))

    def _control_rig_dense_export(
        self,
        target_model: Optional[str],
    ) -> bool:
        """Reject EDIT and request dense sampling for an attached control rig."""
        if not target_model:
            return False
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return False
        if metadata["state"] == CONTROL_RIG_EDIT:
            raise ValueError("Bake the MMD control rig before VMD export")
        return True

    @staticmethod
    def _rotation_time_curve_interpolation(
        target_model: Optional[str],
    ) -> dict[str, dict[int, bytes]]:
        """Resolve Experimental rotation interpolation for sparse export."""
        if not target_model:
            return {}
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return {}
        from mmd_tools.converters.vmd_rotation_time_curve import (
            rotation_time_curve_interpolation_by_bone,
        )

        return rotation_time_curve_interpolation_by_bone(metadata)

    def _scene_authored_input_routes(
        self,
        joints: Sequence[str],
        target_model: Optional[str] = None,
    ) -> dict[str, dict[str, tuple[str, str]]]:
        """Resolve authored channels that bypass the visible joint transform.

        Control-rig EDIT rewires ``bone_morph_base`` and ``ik_controller``
        bindings through an ``mmdBoneMorphAccum`` node.  Once the rig is
        baked, the joint itself has no keys, so the VMD collector must sample
        the accumulator's base channels instead.  UUID-backed metadata is
        authoritative; malformed or stale rows are skipped so unrelated
        joints remain exportable.
        """
        routes = {}
        append_info = collect_append_info()
        ik_info = collect_mmd_ik_passthrough_info()
        for joint_name in joints:
            long_names = cmds.ls(joint_name, long=True) or [joint_name]
            joint = str(long_names[0])
            append = append_info.get(joint)
            if append:
                node = str(append["node"])
                for source_attr, target_attr in append.get("attr_map", {}).items():
                    routes.setdefault(joint, {})[source_attr] = (node, target_attr)
            ik = ik_info.get(joint)
            if ik:
                node = str(ik["node"])
                slot = int(ik["input_slot"])
                for axis in "XYZ":
                    routes.setdefault(joint, {})[f"rotate{axis}"] = (
                        node,
                        f"inputRotate[{slot}].inputRotateElement{axis}",
                    )

        if not target_model:
            return routes
        metadata = read_mmd_control_rig_metadata(target_model)
        if not metadata:
            return routes
        joints_by_path = {
            str((cmds.ls(joint, long=True) or [joint])[0]): str(joint)
            for joint in joints
        }
        for binding in (metadata.get("bindings", {}) or {}).values():
            if not isinstance(binding, Mapping) or binding.get("inputKind") not in {
                INPUT_BONE_MORPH_BASE,
                INPUT_IK_CONTROLLER,
            }:
                continue
            try:
                joint = str(resolve_mmd_control_rig_binding_joint(cmds, binding))
            except Exception:
                joint = str(binding.get("joint") or "")
            joint = str((cmds.ls(joint, long=True) or [joint])[0]) if joint else ""
            if joint not in joints_by_path:
                continue
            try:
                authored_plugs = resolve_mmd_control_rig_binding_authored_plugs(cmds, binding)
            except Exception:
                authored_plugs = tuple(str(plug) for plug in (binding.get("authoredPlugs") or ()))
            route = routes.setdefault(joint, {})
            for authored in authored_plugs:
                node, separator, attribute = str(authored).rpartition(".")
                if not separator or attribute not in {"baseTranslate", "baseRotate"}:
                    continue
                channel = "translate" if attribute == "baseTranslate" else "rotate"
                for axis in "XYZ":
                    route[f"{channel}{axis}"] = (node, f"{attribute}{axis}")
        return routes

    def collect_morph_frames(
        self,
        blend_shapes: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
        target_model: Optional[str] = None,
    ) -> list[dict]:
        """Collect keyed blendShape and model-owned network morph frames.

        Vertex morphs are represented by the existing blendShape targets.  The
        PMX controller also owns non-vertex morphs (bone, group, and material),
        whose weights are keyed on ``inputWeight[index]``.  Network metadata is
        used as the authoritative name/index mapping and is only consulted when
        a model root is supplied, so unrelated model controllers cannot leak
        into an export.
        """
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
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
                            "frame_number": _vmd_frame_number(frame_number, time_converter),
                            "weight": _plug_float(blend_shape, attr, frame_number),
                        }
                    )

        # Non-vertex morphs are driven by the model-scoped mmdMorphController.
        # Keep the existing blendShape rows first so a malformed scene that
        # reuses a morph name remains deterministic and does not duplicate a
        # VMD name/frame pair.
        if target_model:
            controller = _morph_controller_for_model(target_model)
            if controller:
                metadata = list(
                    iter_morph_network_metadata(root_group=target_model)
                )
                metadata_by_index = {}
                metadata_by_name = {}
                for entry in metadata:
                    morph_type = str(entry.morph_type or "")
                    if morph_type == "vertex" or not entry.name or entry.index is None:
                        continue
                    index = int(entry.index)
                    name = str(entry.name)
                    # Duplicate index/name providers are ambiguous.  Skip all
                    # contenders instead of guessing which network is active.
                    metadata_by_index.setdefault(index, []).append(entry)
                    metadata_by_name.setdefault(name, []).append(entry)

                for index, entries in sorted(metadata_by_index.items()):
                    if len(entries) != 1:
                        continue
                    entry = entries[0]
                    if len(metadata_by_name.get(str(entry.name), ())) != 1:
                        continue
                    attr = f"inputWeight[{index}]"
                    keyed_frames = _filter_frame_range(
                        _key_times(controller, (attr,)),
                        start_frame,
                        end_frame,
                    )
                    for frame_number in keyed_frames:
                        frames.append(
                            {
                                "morph_name": str(entry.name),
                                "frame_number": _vmd_frame_number(frame_number, time_converter),
                                "weight": _plug_float(controller, attr, frame_number),
                            }
                        )
        return _deduplicate_frames(frames, ("morph_name", "frame_number"))

    def collect_camera_frames(
        self,
        cameras: Sequence[str],
        start_frame: Optional[float] = None,
        end_frame: Optional[float] = None,
        time_converter=None,
    ) -> list[dict]:
        """Collect keyed MMD camera controller frames."""
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
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
                            "frame_number": _vmd_frame_number(frame_number, time_converter),
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
        time_converter=None,
    ) -> list[dict]:
        """Collect keyed MMD light controller frames."""
        time_converter = time_converter or _scene_maya_time_to_vmd_frame()
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
                        "frame_number": _vmd_frame_number(frame_number, time_converter),
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

    def _find_blend_shapes(self, target_model: Optional[str] = None) -> list[str]:
        """Find blendShapes on mesh history below the selected model root.

        A global ``cmds.ls(type="blendShape")`` query is safe only when no
        model target exists.  With a target, history is resolved from its mesh
        shapes so another namespaced model cannot contribute vertex morph keys.
        """
        if not target_model:
            return cmds.ls(type="blendShape") or []

        try:
            target_type = cmds.nodeType(target_model)
        except Exception:
            target_type = None
        if target_type == "mesh":
            shapes = [target_model]
        else:
            shapes = list(
                cmds.listRelatives(
                    target_model,
                    shapes=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )
            shapes.extend(
                cmds.listRelatives(
                    target_model,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )

        result = []
        seen = set()
        for shape in shapes:
            try:
                history = cmds.listHistory(shape, pruneDagObjects=True) or []
            except Exception:
                continue
            for node in history:
                if node in seen:
                    continue
                try:
                    is_blend_shape = cmds.nodeType(node) == "blendShape"
                except Exception:
                    is_blend_shape = False
                if is_blend_shape:
                    seen.add(node)
                    result.append(node)
        return result

    def _find_tagged_nodes(
        self,
        attr: str,
        target_model: Optional[str] = None,
    ) -> list[str]:
        """Find tagged DAG nodes, restricting automatic discovery to a root."""
        nodes = cmds.ls(f"*.{attr}", objectsOnly=True, long=True) or []
        if not target_model:
            return nodes

        root_path = _canonical_dag_path(target_model)
        if not root_path:
            return []
        return [
            node
            for node in nodes
            if _dag_path_is_under_root(node, root_path)
        ]

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


def _morph_controller_for_model(target_model: str) -> Optional[str]:
    """Resolve the single morph controller connected to a model root.

    The root message connection is the ownership boundary for morph export.
    Missing, malformed, or ambiguous connections fail closed so a VMD export
    cannot accidentally include another model's controller weights.
    """
    if not target_model or not _has_attr(target_model, "mmd_morph_controller"):
        return None
    try:
        controllers = cmds.listConnections(
            f"{target_model}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or []
    except Exception:
        return None
    if len(controllers) != 1:
        return None
    controller = str(controllers[0])
    try:
        if cmds.nodeType(controller) != "mmdMorphController":
            return None
    except Exception:
        return None
    return controller


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
        for source_node in _upstream_anim_curves(plug):
            try:
                values = cmds.keyframe(source_node, query=True, timeChange=True) or []
                times.extend(float(value) for value in values)
            except Exception:
                continue
    return sorted(set(times))


def _upstream_anim_curves(plug: str) -> set[str]:
    curves = set()
    try:
        queue = list(
            cmds.listConnections(
                plug, source=True, destination=False, plugs=True
            )
            or []
        )
    except (RuntimeError, ValueError):
        return curves
    visited = set()
    while queue:
        source = str(queue.pop())
        node = source.split(".", 1)[0]
        if node in visited:
            continue
        visited.add(node)
        node_type = str(cmds.nodeType(node))
        if node_type.startswith("animCurve"):
            curves.add(node)
            continue
        if not (
            node_type in {"pairBlend", "blendWeighted", "unitConversion"}
            or node_type.startswith("animBlendNode")
        ):
            continue
        queue.extend(
            cmds.listConnections(
                node, source=True, destination=False, plugs=True
            )
            or []
        )
    return curves


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


def _scene_maya_fps() -> float:
    """Return the current Maya UI FPS used to evaluate scene key times."""
    try:
        unit = cmds.currentUnit(query=True, time=True)
    except Exception:
        return 30.0
    if isinstance(unit, (int, float)):
        return float(unit) if float(unit) > 0.0 else 30.0
    unit_name = str(unit).strip().lower()
    fps = _MAYA_TIME_UNIT_FPS.get(unit_name)
    if fps is not None:
        return fps
    if unit_name.endswith("fps"):
        try:
            parsed = float(unit_name[:-3])
        except ValueError:
            parsed = 0.0
        if parsed > 0.0:
            return parsed
    return 30.0


def _scene_maya_time_to_vmd_frame():
    """Build a converter from current Maya time values to fixed-30fps VMD frames."""
    fps = _scene_maya_fps()
    return lambda maya_time: _maya_time_to_vmd_frame_at_fps(maya_time, fps)


def _vmd_frame_number(maya_time: float, time_converter) -> int:
    """Convert a Maya time to one integer VMD frame number."""
    return int(round(float(time_converter(maya_time))))


def _deduplicate_frames(frames: Iterable[dict], key_fields: Sequence[str]) -> list[dict]:
    """Keep the first Maya sample for each VMD output key."""
    unique = {}
    for frame in frames:
        key = tuple(frame[field] for field in key_fields)
        unique.setdefault(key, frame)
    return sorted(unique.values(), key=lambda item: tuple(item[field] for field in key_fields))


def _ik_baseline_time(
    start_frame: Optional[float],
    end_frame: Optional[float],
) -> Optional[float]:
    """Return an in-range Maya time for an unkeyed IK baseline property."""
    if start_frame is not None:
        candidate = float(start_frame)
    else:
        candidate = 0.0
    if candidate < 0.0:
        return None
    if end_frame is not None and candidate > float(end_frame):
        return None
    return candidate


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
    value = bind_poses.get(bone_name, bind_poses.get(joint))
    if value is None:
        value = get_stored_bind_translate(joint) or (0.0, 0.0, 0.0)
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


def _build_rotation_export_context(
    joints: Sequence[str],
) -> dict[str, dict[str, Any]]:
    canonical = {
        int(cmds.getAttr(f"{joint}.mmd_bone_index")): str(
            (cmds.ls(joint, long=True) or [joint])[0]
        )
        for joint in joints
        if _has_attr(joint, "mmd_bone_index")
    }
    index_by_joint = {joint: index for index, joint in canonical.items()}
    parent_by_index = {}
    for index, joint in canonical.items():
        parent = (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0]
        parent_by_index[index] = index_by_joint.get(parent)

    bind_worlds = {}

    def bind_world(index: int) -> om.MMatrix:
        if index in bind_worlds:
            return bind_worlds[index]
        joint = canonical[index]
        translate = get_stored_bind_translate(joint)
        if translate is None:
            translate = cmds.getAttr(f"{joint}.translate")[0]
        transform = om.MTransformationMatrix()
        transform.setTranslation(om.MVector(*translate), om.MSpace.kTransform)
        orient = _joint_orient_quaternion(joint)
        transform.setRotation(orient)
        local = transform.asMatrix()
        parent_index = parent_by_index[index]
        result = local * bind_world(parent_index) if parent_index is not None else local
        bind_worlds[index] = result
        return result

    for index in canonical:
        bind_world(index)

    no_orient = {}
    for index, matrix in bind_worlds.items():
        value = om.MMatrix()
        value[12], value[13], value[14] = matrix[12], matrix[13], matrix[14]
        no_orient[index] = value

    result = {}
    for index, joint in canonical.items():
        parent_index = parent_by_index[index]
        parent_bind = bind_worlds[parent_index] if parent_index is not None else om.MMatrix()
        parent_no_orient = no_orient[parent_index] if parent_index is not None else om.MMatrix()
        result[joint] = {
            "jointOrient": _joint_orient_quaternion(joint),
            "rotateOrder": int(cmds.getAttr(f"{joint}.rotateOrder")),
            "bindCorrection": om.MTransformationMatrix(
                bind_worlds[index] * no_orient[index].inverse()
            ).rotation(asQuaternion=True),
            "parentCorrection": om.MTransformationMatrix(
                parent_no_orient * parent_bind.inverse()
            ).rotation(asQuaternion=True),
        }
    return result


def _joint_orient_quaternion(joint: str) -> om.MQuaternion:
    values = cmds.getAttr(f"{joint}.jointOrient")[0]
    return om.MEulerRotation(
        math.radians(float(values[0])),
        math.radians(float(values[1])),
        math.radians(float(values[2])),
    ).asQuaternion()


def _maya_joint_rotate_to_vmd_quaternion(
    joint: str,
    rx: float,
    ry: float,
    rz: float,
    export_context: Optional[Mapping[str, Any]] = None,
) -> tuple[float, float, float, float]:
    """Convert Maya XYZ joint.rotate degrees to a JO-aware VMD quaternion."""
    if export_context:
        order = int(export_context["rotateOrder"])
        order_map = (
            om.MEulerRotation.kXYZ,
            om.MEulerRotation.kYZX,
            om.MEulerRotation.kZXY,
            om.MEulerRotation.kXZY,
            om.MEulerRotation.kYXZ,
            om.MEulerRotation.kZYX,
        )
        euler = om.MEulerRotation(
            math.radians(rx),
            math.radians(ry),
            math.radians(rz),
            order_map[order] if 0 <= order < len(order_map) else order_map[0],
        )
        q_rotate = euler.asQuaternion()
        q_total = q_rotate * export_context["jointOrient"]
        q_maya = (
            export_context["bindCorrection"].inverse()
            * q_total
            * export_context["parentCorrection"].inverse()
        )
        q_maya.normalizeIt()
        return (-q_maya.x, -q_maya.y, q_maya.z, q_maya.w)
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
