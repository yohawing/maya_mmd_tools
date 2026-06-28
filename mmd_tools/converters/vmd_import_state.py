"""Import state and cleanup helpers for VMD conversion."""

import json
from typing import Dict, Optional, Tuple

import maya.cmds as cmds

from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes

_ATTR_VMD_BIND_TRANSLATE = "mmd_vmd_bind_translate"


def restore_import_timeline_state(current_time: Optional[float]) -> None:
    """Keep VMD import from leaving Maya visibly playing or scrubbed ahead."""
    if current_time is not None:
        try:
            cmds.currentTime(current_time, edit=True)
        except Exception:
            pass
    try:
        cmds.play(state=False)
    except Exception:
        pass


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
        except Exception:
            pass
    return selection


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
        except Exception:
            pass


def clear_existing_motion(converter, layer_name: str, target_namespace: Optional[str] = None) -> None:
    """Delete existing VMD motion keys/layer for the target model."""
    cleared = 0

    mapped_joints = set(converter.bone_name_mapping.values())
    fallback_translates = _capture_fallback_rest_translates(mapped_joints)
    for joint in mapped_joints:
        cleared += cut_keyable_attrs(
            joint,
            ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
        )

    for target_joint, info in converter._collect_append_info().items():
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
    for mapping_entry in converter.morph_name_mapping.values():
        for morph_node, weight_attr, _morph_name in converter._iter_morph_mappings(mapping_entry):
            if node_matches_target_namespace(morph_node, target_namespace):
                cleared += cut_keyable_attrs(morph_node, (weight_attr,))
                morph_nodes.add(morph_node)

    if cmds.objExists(layer_name):
        try:
            cmds.delete(layer_name)
            cleared += 1
        except Exception as exc:
            converter.logger.debug(f"failed to delete existing animLayer {layer_name}: {exc}")

    # cutKey はアニメーション曲線を削除するが joint の attribute 値はポーズのまま残る。
    # 後続の _record_bind_poses が正しい rest position を取得できるよう dagPose で復元する。
    _restore_joints_to_rest(mapped_joints, fallback_translates, converter.logger)

    converter.logger.info(
        "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
        cleared,
        len(mapped_joints),
        len(morph_nodes),
    )


def _capture_fallback_rest_translates(joints) -> Dict[str, Tuple[float, float, float]]:
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
        except Exception:
            pass
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
        except Exception:
            pass
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
    except Exception:
        pass


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
    short_name = node.split("|")[-1]
    return short_name.startswith(f"{target_namespace}:")


def cut_keyable_attrs(node: str, attrs: Tuple[str, ...]) -> int:
    """Delete keys for existing attrs and return the number of attrs attempted."""
    if not node or not cmds.objExists(node):
        return 0

    cleared = 0
    for attr in attrs:
        attr_name = attr.split("[", 1)[0]
        if not cmds.attributeQuery(attr_name, node=node, exists=True):
            continue
        try:
            cmds.cutKey(node, attribute=attr)
            cleared += 1
        except Exception:
            pass
    return cleared
