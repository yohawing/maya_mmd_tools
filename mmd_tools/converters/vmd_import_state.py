"""Import state and cleanup helpers for VMD conversion."""

import json
from typing import Any, Dict, Optional, Tuple, Union

import maya.cmds as cmds

from ..core.constants import ATTR_MMD_CAMERA, ATTR_MMD_LIGHT
from ..core.logger import get_logger
from ..core.namespace_utils import NamespaceUtils
from .vmd_context import VmdImportStateContext
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes

_ATTR_VMD_BIND_TRANSLATE = "mmd_vmd_bind_translate"
_LOGGER = get_logger(__name__)


def _resolve_import_state_context(converter_or_context: Union[Any, VmdImportStateContext]) -> VmdImportStateContext:
    if isinstance(converter_or_context, VmdImportStateContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_import_state_context", None)
    if callable(factory):
        return factory()
    return VmdImportStateContext(
        logger=converter_or_context.logger,
        bone_name_mapping=converter_or_context.bone_name_mapping,
        bone_bind_poses=converter_or_context._bone_bind_poses,
        morph_name_mapping=converter_or_context.morph_name_mapping,
        collect_append_info=converter_or_context._collect_append_info,
        iter_morph_mappings=converter_or_context._iter_morph_mappings,
        set_refresh_suspended=lambda value: setattr(converter_or_context, "_vmd_import_refresh_suspended", value),
    )


def restore_import_timeline_state(current_time: Optional[float], logger: Optional[Any] = None) -> None:
    """Keep VMD import from leaving Maya visibly playing or scrubbed ahead."""
    if current_time is not None:
        try:
            cmds.currentTime(current_time, edit=True)
        except Exception as exc:
            if logger is not None:
                logger.debug("Failed to restore VMD import current time: %s", exc)
    try:
        cmds.play(state=False)
    except Exception as exc:
        if logger is not None:
            logger.debug("Failed to stop Maya playback after VMD import: %s", exc)


def suspend_import_scene_updates(converter_or_context: Union[Any, VmdImportStateContext]) -> Tuple[bool, bool]:
    """Suppress Maya undo recording and viewport refresh during VMD import."""
    context = _resolve_import_state_context(converter_or_context)
    undo_was_enabled = True
    refresh_suspended = False
    try:
        undo_was_enabled = bool(cmds.undoInfo(q=True, state=True))
    except Exception as exc:
        context.logger.debug("Failed to query Maya undo state before VMD import: %s", exc)
        undo_was_enabled = True
    try:
        cmds.undoInfo(stateWithoutFlush=False)
    except Exception as exc:
        context.logger.debug("Failed to disable Maya undo during VMD import: %s", exc)
    try:
        cmds.refresh(suspend=True)
        refresh_suspended = True
        context.set_refresh_suspended(True)
    except Exception as exc:
        context.logger.debug("Failed to suspend Maya refresh during VMD import: %s", exc)
        refresh_suspended = False
        context.set_refresh_suspended(False)
    return undo_was_enabled, refresh_suspended


def restore_import_scene_updates(
    converter_or_context: Union[Any, VmdImportStateContext],
    undo_was_enabled: bool,
    refresh_suspended: bool,
) -> None:
    """Restore viewport refresh and undo state after VMD import."""
    context = _resolve_import_state_context(converter_or_context)
    if refresh_suspended:
        try:
            cmds.refresh(suspend=False)
        except Exception as exc:
            context.logger.debug("Failed to restore Maya refresh after VMD import: %s", exc)
    context.set_refresh_suspended(False)
    if undo_was_enabled:
        try:
            cmds.undoInfo(stateWithoutFlush=True)
        except Exception as exc:
            context.logger.debug("Failed to restore Maya undo after VMD import: %s", exc)


def capture_anim_layer_selection() -> Dict[str, bool]:
    """Capture animLayer selected states before VMD import mutates them."""
    try:
        layers = cmds.ls(type="animLayer") or []
    except Exception:
        return {}

    selection = {}
    for layer in layers:
        try:
            selection[layer] = bool(cmds.animLayer(layer, query=True, selected=True))
        except Exception as exc:
            _LOGGER.debug("Failed to capture animLayer selection for %s: %s", layer, exc)
    return selection


def record_bind_poses(converter_or_context: Union[Any, VmdImportStateContext]) -> None:
    """Record current joint translates as VMD bind-pose fallback metadata."""
    context = _resolve_import_state_context(converter_or_context)
    context.logger.debug("Recording initial bone positions")

    for vmd_bone_name, maya_joint in context.bone_name_mapping.items():
        try:
            translate = cmds.getAttr(f"{maya_joint}.translate")[0]
            context.bone_bind_poses[vmd_bone_name] = translate
            store_bind_translate(maya_joint, translate)
        except Exception as exc:
            context.logger.warning("Failed to get bind pose for %s: %s", vmd_bone_name, exc)


def restore_anim_layer_selection(selection: Optional[Dict[str, bool]]) -> None:
    """Restore animLayer selected states changed during VMD import."""
    if selection is None:
        return
    try:
        layers = cmds.ls(type="animLayer") or []
    except Exception:
        return

    for layer in layers:
        try:
            cmds.animLayer(layer, edit=True, selected=selection.get(layer, False))
        except Exception as exc:
            _LOGGER.debug("Failed to restore animLayer selection for %s: %s", layer, exc)


def clear_existing_motion(
    converter_or_context: Union[Any, VmdImportStateContext],
    layer_name: str,
    target_namespace: Optional[str] = None,
) -> None:
    """Delete existing VMD motion keys/layer for the target model."""
    context = _resolve_import_state_context(converter_or_context)
    cleared = 0

    mapped_joints = set(context.bone_name_mapping.values())
    fallback_translates = _capture_fallback_rest_translates(mapped_joints, context.logger)
    for joint in mapped_joints:
        cleared += cut_keyable_attrs(
            joint,
            ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
        )

    for target_joint, info in context.collect_append_info().items():
        append_node = info.get("node")
        if append_node and (
            node_matches_target_namespace(target_joint, target_namespace)
            or node_matches_target_namespace(append_node, target_namespace)
        ):
            cleared += cut_keyable_attrs(
                append_node,
                (
                    "baseTranslateX",
                    "baseTranslateY",
                    "baseTranslateZ",
                    "baseRotateX",
                    "baseRotateY",
                    "baseRotateZ",
                ),
            )

    for ik_node in _ls_mmd_ccd_ik_nodes():
        if node_matches_target_namespace(ik_node, target_namespace):
            cleared += cut_keyable_attrs(ik_node, ("enabled", "inputRotate"))

    morph_nodes = set()
    for mapping_entry in context.morph_name_mapping.values():
        for morph_node, weight_attr, _morph_name in context.iter_morph_mappings(mapping_entry):
            if node_matches_target_namespace(morph_node, target_namespace):
                cleared += cut_keyable_attrs(morph_node, (weight_attr,))
                morph_nodes.add(morph_node)

    if cmds.objExists(layer_name):
        try:
            cmds.delete(layer_name)
            cleared += 1
        except Exception as exc:
            context.logger.debug(f"failed to delete existing animLayer {layer_name}: {exc}")

    # cutKey はアニメーション曲線を削除するが joint の attribute 値はポーズのまま残る。
    # 後続の _record_bind_poses が正しい rest position を取得できるよう dagPose で復元する。
    _restore_joints_to_rest(mapped_joints, fallback_translates, context.logger)

    context.logger.info(
        "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
        cleared,
        len(mapped_joints),
        len(morph_nodes),
    )


def _capture_fallback_rest_translates(joints, logger) -> Dict[str, Tuple[float, float, float]]:
    """Capture translate fallback values before motion keys are removed."""
    fallback_translates = {}
    for joint in joints:
        if not cmds.objExists(joint):
            continue
        stored = get_stored_bind_translate(joint)
        if stored is not None:
            fallback_translates[joint] = stored
            continue
        try:
            fallback_translates[joint] = tuple(cmds.getAttr(f"{joint}.translate")[0])
        except Exception as exc:
            logger.debug("Failed to capture fallback rest translate for %s: %s", joint, exc)
    return fallback_translates


def _restore_joints_to_rest(joints, fallback_translates: Dict[str, Tuple[float, float, float]], logger) -> None:
    """Restore joints to their bind pose after animation keys have been removed.

    Uses dagPose -restore -bindPose first; if that fails (no bind pose node),
    falls back to the translate values captured before keys were removed.
    """
    if not joints:
        return

    # Try dagPose restore on the first joint to find and apply the bind pose
    dag_pose_restored = False
    for joint in joints:
        if not cmds.objExists(joint):
            continue
        bind_poses = cmds.dagPose(joint, query=True, bindPose=True) or []
        if bind_poses:
            try:
                cmds.dagPose(bind_poses[0], restore=True)
                dag_pose_restored = True
            except Exception as exc:
                logger.debug(f"dagPose restore failed: {exc}")
            break

    if dag_pose_restored:
        return

    # Fallback: zero out rotate and restore stored bind translate when available.
    restored = 0
    for joint in joints:
        if not cmds.objExists(joint):
            continue
        try:
            cmds.setAttr(f"{joint}.rotate", 0.0, 0.0, 0.0)
            translate = fallback_translates.get(joint)
            if translate is not None:
                cmds.setAttr(f"{joint}.translate", translate[0], translate[1], translate[2])
            restored += 1
        except Exception as exc:
            logger.debug("Failed to restore joint fallback rest pose for %s: %s", joint, exc)
    if restored:
        logger.debug(f"Fallback: restored {restored} joints from snapshots (no dagPose)")


def store_bind_translate(joint: str, translate: Tuple[float, float, float]) -> None:
    """Persist a joint bind translate for future clear/reimport fallback."""
    if not joint or not cmds.objExists(joint):
        return
    try:
        if not cmds.attributeQuery(_ATTR_VMD_BIND_TRANSLATE, node=joint, exists=True):
            cmds.addAttr(joint, longName=_ATTR_VMD_BIND_TRANSLATE, dataType="string")
        cmds.setAttr(
            f"{joint}.{_ATTR_VMD_BIND_TRANSLATE}",
            json.dumps([float(translate[0]), float(translate[1]), float(translate[2])]),
            type="string",
        )
    except Exception as exc:
        _LOGGER.debug("Failed to store VMD bind translate on %s: %s", joint, exc)


def get_stored_bind_translate(joint: str) -> Optional[Tuple[float, float, float]]:
    """Read a persisted VMD bind translate from a joint when available."""
    if not joint or not cmds.objExists(joint):
        return None
    try:
        if not cmds.attributeQuery(_ATTR_VMD_BIND_TRANSLATE, node=joint, exists=True):
            return None
        raw_value = cmds.getAttr(f"{joint}.{_ATTR_VMD_BIND_TRANSLATE}")
        values = json.loads(raw_value)
        if not isinstance(values, list) or len(values) != 3:
            return None
        return (float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        return None


def node_matches_target_namespace(node: str, target_namespace: Optional[str]) -> bool:
    """Return whether a node belongs to target_namespace when one is specified."""
    if not target_namespace:
        return True
    return NamespaceUtils.get_namespace_from_node(node) == target_namespace


def cut_keyable_attrs(node: str, attrs: Tuple[str, ...]) -> int:
    """Delete keys for existing attrs and return the number of attrs attempted."""
    if not node or not cmds.objExists(node):
        return 0

    cleared = 0
    for attr in attrs:
        attr_name = attr.split("[", 1)[0]
        if not cmds.attributeQuery(attr_name, node=node, exists=True) and not cmds.objExists(f"{node}.{attr}"):
            continue
        try:
            for target_attr in _key_cut_attrs(node, attr):
                cmds.cutKey(node, attribute=target_attr)
            cleared += 1
        except Exception as exc:
            _LOGGER.debug("Failed to cut key %s.%s: %s", node, attr, exc)
    return cleared


def _key_cut_attrs(node: str, attr: str) -> Tuple[str, ...]:
    """Return attribute names that may carry keys for an attr or its alias."""
    attrs = [attr]
    aliases = cmds.aliasAttr(node, query=True) or []
    for alias, plug in zip(aliases[0::2], aliases[1::2]):
        if plug == attr and alias not in attrs:
            attrs.append(alias)
    return tuple(attrs)


_CAMERA_TRANSFORM_ATTRS = ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
_CAMERA_SHAPE_ATTRS = ("focalLength", "orthographicWidth", "orthographic")
_LIGHT_ROTATE_ATTRS = ("rotateX", "rotateY", "rotateZ")
_LIGHT_COLOR_ATTRS = ("colorR", "colorG", "colorB")
_LIGHT_MMD_COLOR_ATTRS = ("mmd_light_colorR", "mmd_light_colorG", "mmd_light_colorB")


def clear_existing_camera_motion(logger: Any = None) -> int:
    """Delete existing MMD camera animation keys.

    Finds the MMD camera by the mmd_camera marker attribute and clears keys on
    the camera transform, its shape, camera target, and camera root nodes.
    Does NOT touch character bone/morph keys.

    Returns:
        Number of attribute channels cleared.
    """
    from .vmd_camera_animation import ATTR_MMD_CAMERA_TARGET_NODE, ATTR_MMD_CAMERA_ROOT_NODE

    cameras = cmds.ls(f"*.{ATTR_MMD_CAMERA}", objectsOnly=True) or []
    if not cameras:
        if logger:
            logger.debug("No MMD camera found; nothing to clear")
        return 0

    cleared = 0
    for camera_transform in cameras:
        cleared += cut_keyable_attrs(camera_transform, _CAMERA_TRANSFORM_ATTRS)

        camera_shapes = cmds.listRelatives(camera_transform, shapes=True, type="camera") or []
        for shape in camera_shapes:
            cleared += cut_keyable_attrs(shape, _CAMERA_SHAPE_ATTRS)

        if cmds.attributeQuery(ATTR_MMD_CAMERA_TARGET_NODE, node=camera_transform, exists=True):
            targets = cmds.listConnections(f"{camera_transform}.{ATTR_MMD_CAMERA_TARGET_NODE}", source=True) or []
            for target in targets:
                cleared += cut_keyable_attrs(target, _CAMERA_TRANSFORM_ATTRS)

        if cmds.attributeQuery(ATTR_MMD_CAMERA_ROOT_NODE, node=camera_transform, exists=True):
            roots = cmds.listConnections(f"{camera_transform}.{ATTR_MMD_CAMERA_ROOT_NODE}", source=True) or []
            for root in roots:
                cleared += cut_keyable_attrs(root, _CAMERA_TRANSFORM_ATTRS)

    if logger:
        logger.info("Cleared existing camera motion: %d attribute channels from %d camera(s)", cleared, len(cameras))
    return cleared


def clear_existing_light_motion(logger: Any = None) -> int:
    """Delete existing MMD light animation keys.

    Finds the MMD light by the mmd_light marker attribute and clears keys on
    the light transform rotation, controller color attrs, and directionalLight
    shape color attrs. Does NOT touch character bone/morph keys.

    Returns:
        Number of attribute channels cleared.
    """
    lights = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True) or []
    if not lights:
        if logger:
            logger.debug("No MMD light found; nothing to clear")
        return 0

    cleared = 0
    for light_transform in lights:
        cleared += cut_keyable_attrs(light_transform, _LIGHT_ROTATE_ATTRS)

        if cmds.attributeQuery("mmd_light_color", node=light_transform, exists=True):
            cleared += cut_keyable_attrs(light_transform, _LIGHT_MMD_COLOR_ATTRS)
        light_shapes = cmds.listRelatives(light_transform, shapes=True, type="directionalLight") or []
        for shape in light_shapes:
            cleared += cut_keyable_attrs(shape, _LIGHT_COLOR_ATTRS)

    if logger:
        logger.info("Cleared existing light motion: %d attribute channels from %d light(s)", cleared, len(lights))
    return cleared
