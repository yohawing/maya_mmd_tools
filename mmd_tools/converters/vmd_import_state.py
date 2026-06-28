"""Import state and cleanup helpers for VMD conversion."""

from typing import Dict, Optional, Tuple

import maya.cmds as cmds

from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


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

    for joint in set(converter.bone_name_mapping.values()):
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

    converter.logger.info(
        "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
        cleared,
        len(set(converter.bone_name_mapping.values())),
        len(morph_nodes),
    )


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
