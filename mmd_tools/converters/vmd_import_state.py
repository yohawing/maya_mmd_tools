"""Import state and cleanup helpers for VMD conversion."""

import json
from typing import Any, Dict, Optional, Set, Tuple, Union

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from ..core.constants import ATTR_MMD_CAMERA, ATTR_MMD_LIGHT
from ..core.logger import get_logger
from ..core.namespace_utils import NamespaceUtils
from .vmd_context import VmdImportStateContext
from .vmd_ik_enabled_animation import ik_node_is_owned_by_root, root_owned_joints
from .vmd_morph_mapping import morph_node_is_owned_by_root
from .vmd_redirected_authoring_proxy import resolve_redirected_authoring_proxy
from .vmd_rotation_time_curve import delete_vmd_rotation_time_curves_for_controls
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes
from ..core.mmd_control_rig_builder import CONTROL_RIG_CONTROL_OWNED, read_mmd_control_rig_metadata
from ..core.mmd_control_rig_motion import (
    control_rig_edit_ik_enabled_plugs_for_model,
    control_rig_edit_routes_for_joints,
)
from .light_converter import (
    MMD_SELF_SHADOW_DISTANCE_ATTR,
    MMD_SELF_SHADOW_MODE_ATTR,
)

_ATTR_VMD_BIND_TRANSLATE = "mmd_vmd_bind_translate"
_CLEARABLE_PHYSICAL_ROUTE_TYPES = frozenset(
    {"mmdBoneMorphAccum", "mmdPhysicsBoneDriver"}
)
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


def collect_clearable_authoring_attrs(
    authored_routes: Optional[Dict[str, dict]],
    target_joints,
    logger,
) -> Dict[str, Set[str]]:
    """Resolve target-owned non-joint plugs used by VMD authoring."""
    owned_joint_paths = set()
    for joint in target_joints:
        matches = cmds.ls(joint, long=True) or []
        if len(matches) == 1:
            owned_joint_paths.add(str(matches[0]))

    routed_attrs: Dict[str, Set[str]] = {}
    for joint, route in (authored_routes or {}).items():
        matches = cmds.ls(joint, long=True) or []
        if len(matches) != 1 or str(matches[0]) not in owned_joint_paths:
            continue
        if not isinstance(route, dict):
            continue
        for channel, destination in (route.get("attr_targets") or {}).items():
            try:
                node, attribute = destination
                node_type = str(cmds.nodeType(node))
            except (TypeError, ValueError, RuntimeError):
                continue
            # Append, Control Rig, and IK routes have their own ownership
            # checks below.  Only the two route planners that already prove a
            # native node belongs to this joint are accepted here.
            if node_type not in _CLEARABLE_PHYSICAL_ROUTE_TYPES:
                continue
            if str(node) == str(joint) and str(attribute) == str(channel):
                continue
            routed_attrs.setdefault(str(node), set()).add(str(attribute))

    # Persisted proxies validate target UUID, destination-owner UUID, and the
    # live connection before returning a route, so a stale claim clears none.
    for joint in target_joints:
        try:
            proxy_route, claimed = resolve_redirected_authoring_proxy(joint)
        except Exception as exc:
            logger.debug("Failed to resolve redirected VMD route for %s: %s", joint, exc)
            continue
        if not claimed:
            continue
        for node, attribute in proxy_route.values():
            routed_attrs.setdefault(str(node), set()).add(str(attribute))
    return routed_attrs


def clear_existing_motion(
    converter_or_context: Union[Any, VmdImportStateContext],
    layer_name: str,
    target_namespace: Optional[str] = None,
    target_model: Optional[str] = None,
    *,
    preserve_curve_nodes: bool = False,
    detached_curve_nodes=None,
    authored_routes: Optional[Dict[str, dict]] = None,
) -> bool:
    """Delete all existing character motion keys for the target model.

    When ``target_model`` is explicit, every joint below that model root is
    cleared.  The clear scope must not depend on the incoming VMD's bone-name
    mapping: an omitted or unsupported bone in the new motion must not leave
    stale keys on the character.  Root ownership checks still isolate other
    models and scene-level camera/light animation.

    ``preserve_curve_nodes`` is used by the Control Rig preflight transaction
    to retain legacy animCurve identity for exact rollback.  Such curves are
    detached after their keys are removed and are deleted on successful commit.

    Returns:
        True only when an exclusively target-owned morph-controller layer was
        retained for the immediate replacement import.
    """
    context = _resolve_import_state_context(converter_or_context)
    cleared = 0
    owned_motion_nodes = set()

    owned_joints = root_owned_joints(target_model) if target_model else None
    target_joints = (
        set(owned_joints)
        if target_model
        else set(context.bone_name_mapping.values())
    )
    fallback_translates = _capture_fallback_rest_translates(target_joints, context.logger)
    if not preserve_curve_nodes:
        cleared += len(delete_vmd_rotation_time_curves_for_controls(target_joints))
    for joint in target_joints:
        owned_motion_nodes.add(joint)
        cleared += cut_keyable_attrs(
            joint,
            ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"),
            preserve_curve_nodes=preserve_curve_nodes,
            detached_curve_nodes=detached_curve_nodes,
        )

    # Clear the validated physical destinations selected by the keying route
    # planner so an omitted track cannot survive upstream of its joint.
    for node, attrs in collect_clearable_authoring_attrs(
        authored_routes,
        target_joints,
        context.logger,
    ).items():
        owned_motion_nodes.add(node)
        cleared += cut_keyable_attrs(
            node,
            tuple(sorted(attrs)),
            preserve_curve_nodes=True,
        )

    # In CONTROL_OWNED/EDIT, authored VMD channels are redirected to the
    # controller curves.  Clear those curves as part of the same root-scoped
    # operation; otherwise Clear Existing Motion would leave old controller
    # keys driving the freshly imported motion.
    control_routes = {}
    control_metadata = None
    if target_model:
        control_metadata = read_mmd_control_rig_metadata(target_model)
        if control_metadata and control_metadata.get("owner") == CONTROL_RIG_CONTROL_OWNED:
            control_routes = control_rig_edit_routes_for_joints(target_joints)
    for route in control_routes.values():
        by_node = {}
        for target_node, target_attr in route.values():
            by_node.setdefault(target_node, set()).add(target_attr)
        for node, attrs in by_node.items():
            owned_motion_nodes.add(node)
            cleared += cut_keyable_attrs(
                node,
                tuple(sorted(attrs)),
                preserve_curve_nodes=True,
            )

    # IK visibility animation is authored on the Control Rig controller rather
    # than on the legacy solver.  Resolve only UUID-backed controls owned by
    # this model root, then clear their existing animCurve keys in place.
    if control_metadata and control_metadata.get("owner") == CONTROL_RIG_CONTROL_OWNED:
        for plug in control_rig_edit_ik_enabled_plugs_for_model(
            target_model,
        ):
            control, attribute = plug.rsplit(".", 1)
            owned_motion_nodes.add(control)
            cleared += cut_keyable_attrs(
                control,
                (attribute,),
                preserve_curve_nodes=True,
            )

    for target_joint, info in context.collect_append_info().items():
        append_node = info.get("node")
        if append_node and (
            (
                target_model
                and _append_node_is_owned_by_root(
                    append_node,
                    target_joint,
                    target_model,
                    owned_joints,
                    source_joint=info.get("source_joint"),
                )
            )
            or (
                not target_model
                and (
                    node_matches_target_namespace(target_joint, target_namespace)
                    or node_matches_target_namespace(append_node, target_namespace)
                )
            )
        ):
            owned_motion_nodes.add(append_node)
            # Append nodes are part of the authored rig graph.  Clear only
            # the key payload in the existing animCurves so the append node,
            # curve identity, and direct joint/root connections survive a
            # root-scoped re-import.
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
                preserve_curve_nodes=True,
            )

    for ik_node in _ls_mmd_ccd_ik_nodes():
        if (
            ik_node_is_owned_by_root(ik_node, target_model, owned_joints)
            if target_model
            else node_matches_target_namespace(ik_node, target_namespace)
        ):
            owned_motion_nodes.add(ik_node)
            # ``cutKey`` on the custom compound array can tear down the
            # solver node when it removes the last inputRotate curve.  Remove
            # keys through the existing animCurve nodes instead so solver
            # graph connections remain intact during a root-scoped clear.
            cleared += cut_keyable_attrs(
                ik_node,
                ("enabled", "inputRotate"),
                preserve_curve_nodes=True,
            )

    morph_nodes = set()
    for mapping_entry in context.morph_name_mapping.values():
        for morph_node, weight_attr, _morph_name in context.iter_morph_mappings(mapping_entry):
            if (
                morph_node_is_owned_by_root(morph_node, target_model)
                if target_model
                else node_matches_target_namespace(morph_node, target_namespace)
            ):
                owned_motion_nodes.add(morph_node)
                # Remove only the key payload.  Bone morph weights feed the
                # accumulator contribution graph; using ``cutKey`` here can
                # delete an otherwise still-connected animCurve when it is
                # the last key on the network node.  Keep the curve node and
                # its downstream accumulator wiring intact for re-import.
                cleared += cut_keyable_attrs(
                    morph_node,
                    (weight_attr,),
                    preserve_curve_nodes=True,
                )
                morph_nodes.add(morph_node)

    can_delete_layer = not target_model or _anim_layer_is_exclusively_owned_by(
        layer_name,
        owned_motion_nodes,
    )
    retains_morph_controller = bool(
        target_model and can_delete_layer and _anim_layer_targets_morph_controller(layer_name)
    )
    if cmds.objExists(layer_name) and can_delete_layer and not retains_morph_controller:
        try:
            cmds.delete(layer_name)
            cleared += 1
        except Exception as exc:
            context.logger.debug(f"failed to delete existing animLayer {layer_name}: {exc}")
    elif cmds.objExists(layer_name) and not can_delete_layer:
        context.logger.warning(
            "Preserving shared/unowned animation layer during root-scoped VMD clear: %s",
            layer_name,
        )

    # cutKey はアニメーション曲線を削除するが joint の attribute 値はポーズのまま残る。
    # 後続の _record_bind_poses が正しい rest position を取得できるよう dagPose で復元する。
    _restore_joints_to_rest(target_joints, fallback_translates, context.logger)

    context.logger.info(
        "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
        cleared,
        len(target_joints),
        len(morph_nodes),
    )
    return retains_morph_controller


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


def store_bind_translate(
    joint: str,
    translate: Tuple[float, float, float],
    cmds_module=None,
) -> None:
    """Persist an immutable joint bind translate for clear/reimport fallback.

    ``cmds_module`` is injectable for import paths that already own a Maya
    command facade (notably the C++ fast importer).  The normal VMD path keeps
    the historical module-level ``maya.cmds`` default.  Bone import owns the
    first write; later VMD imports must not replace bind authority with the
    currently evaluated animation pose.
    """
    maya_cmds = cmds if cmds_module is None else cmds_module
    if not joint or not maya_cmds.objExists(joint):
        return
    try:
        if maya_cmds.attributeQuery(_ATTR_VMD_BIND_TRANSLATE, node=joint, exists=True):
            return
        maya_cmds.addAttr(joint, longName=_ATTR_VMD_BIND_TRANSLATE, dataType="string")
        maya_cmds.setAttr(
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


def _long_names(nodes) -> set:
    result = set()
    for node in nodes or []:
        result.update(cmds.ls(node, long=True) or [])
    return result


def _nodes_are_exclusively_owned(nodes, owned_nodes) -> bool:
    resolved = _long_names(nodes)
    return bool(resolved) and resolved.issubset(owned_nodes or set())


def _append_node_is_owned_by_root(
    append_node: str,
    target_joint: str,
    target_model: str,
    owned_joints,
    source_joint: Optional[str] = None,
) -> bool:
    """Prove every direct append joint/root connection belongs to target_model."""
    if not append_node or not cmds.objExists(append_node):
        return False
    if not _nodes_are_exclusively_owned([target_joint], owned_joints):
        return False
    if source_joint and not _nodes_are_exclusively_owned([source_joint], owned_joints):
        return False

    connected_joints = cmds.listConnections(
        append_node,
        source=True,
        destination=True,
        type="joint",
    ) or []
    if not _nodes_are_exclusively_owned(connected_joints, owned_joints):
        return False

    if cmds.attributeQuery("mmd_model_root", node=append_node, exists=True):
        connected_roots = cmds.listConnections(f"{append_node}.mmd_model_root") or []
        target_roots = _long_names([target_model])
        if not _nodes_are_exclusively_owned(connected_roots, target_roots):
            return False
    return True


def _anim_layer_is_exclusively_owned_by(layer_name: str, owned_nodes) -> bool:
    """Fail closed when a layer contains attributes outside the target model."""
    if not cmds.objExists(layer_name):
        return False
    attributes = cmds.animLayer(layer_name, query=True, attribute=True) or []
    if not attributes:
        return True
    owned_long_names = _long_names(owned_nodes)
    for attribute in attributes:
        node = attribute.split(".", 1)[0]
        if not _nodes_are_exclusively_owned([node], owned_long_names):
            return False
    return True


def _anim_layer_targets_morph_controller(layer_name: str) -> bool:
    """Return whether deleting a layer would delete an MMD morph controller."""
    if not cmds.objExists(layer_name):
        return False
    for attribute in cmds.animLayer(layer_name, query=True, attribute=True) or []:
        node = str(attribute).split(".", 1)[0]
        try:
            if cmds.nodeType(node) == "mmdMorphController":
                return True
        except Exception:
            continue
    return False


def cut_keyable_attrs(
    node: str,
    attrs: Tuple[str, ...],
    *,
    preserve_curve_nodes: bool = False,
    detached_curve_nodes=None,
) -> int:
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
                plug = f"{node}.{target_attr}"
                curves = (
                    cmds.listConnections(
                        plug,
                        source=True,
                        destination=False,
                        type="animCurve",
                    )
                    or []
                )
                if preserve_curve_nodes:
                    # Compound-array parents (for example inputRotate) can
                    # expose several directly connected child curves.  Clear
                    # every unique curve in place; falling back to cutKey here
                    # can tear down custom solver nodes and their graph.
                    for curve in _animation_curve_nodes_for_plug(
                        node,
                        target_attr,
                        direct_curves=curves,
                    ):
                        try:
                            selection = om.MSelectionList()
                            selection.add(curve)
                            curve_fn = oma.MFnAnimCurve(selection.getDependNode(0))
                        except Exception as exc:
                            _LOGGER.debug("Failed to resolve animation curve %s: %s", curve, exc)
                            continue
                        for index in reversed(range(curve_fn.numKeys)):
                            curve_fn.remove(index)
                    if detached_curve_nodes is not None:
                        for source in cmds.listConnections(
                            plug,
                            source=True,
                            destination=False,
                            plugs=True,
                        ) or []:
                            source_node = str(source).split(".", 1)[0]
                            try:
                                if not str(cmds.nodeType(source_node)).startswith("animCurve"):
                                    continue
                            except Exception:
                                continue
                            try:
                                cmds.disconnectAttr(source, plug)
                            except Exception as exc:
                                _LOGGER.debug(
                                    "Failed to detach animation curve %s from %s: %s",
                                    source,
                                    plug,
                                    exc,
                                )
                                continue
                            detached_curve_nodes.append(source_node)
                    continue
                cmds.cutKey(node, attribute=target_attr)
            cleared += 1
        except Exception as exc:
            _LOGGER.debug("Failed to cut key %s.%s: %s", node, attr, exc)
    return cleared


def _animation_curve_nodes_for_plug(
    node: str,
    attribute: str,
    *,
    direct_curves=(),
) -> Tuple[str, ...]:
    """Return all animCurves in a plug's keyset, including animation layers.

    A layered attribute is often driven through an animBlend node, so a direct
    ``listConnections(type=\"animCurve\")`` query can be empty even though the
    plug still owns keyed curves.  Maya's keyset ``name`` query walks that
    blend/layer graph; union it with direct connections for compound inputs and
    de-duplicate before callers clear each curve in place.
    """
    curves = list(direct_curves or ())
    plug = f"{node}.{attribute}"
    try:
        curves.extend(cmds.keyframe(plug, query=True, name=True) or ())
    except Exception as exc:
        _LOGGER.debug("Failed to query animation curves for %s: %s", plug, exc)
    return tuple(dict.fromkeys(str(curve) for curve in curves if curve))


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
_LIGHT_SELF_SHADOW_ATTRS = (MMD_SELF_SHADOW_MODE_ATTR, MMD_SELF_SHADOW_DISTANCE_ATTR)


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
        cleared += cut_keyable_attrs(light_transform, _LIGHT_SELF_SHADOW_ATTRS)

        if cmds.attributeQuery("mmd_light_color", node=light_transform, exists=True):
            cleared += cut_keyable_attrs(light_transform, _LIGHT_MMD_COLOR_ATTRS)
        light_shapes = cmds.listRelatives(light_transform, shapes=True, type="directionalLight") or []
        for shape in light_shapes:
            cleared += cut_keyable_attrs(shape, _LIGHT_COLOR_ATTRS)

    if logger:
        logger.info("Cleared existing light motion: %d attribute channels from %d light(s)", cleared, len(lights))
    return cleared


def clear_existing_shadow_motion(logger: Any = None) -> int:
    """Clear only VMD self-shadow keys, preserving light direction and color."""
    lights = cmds.ls(f"*.{ATTR_MMD_LIGHT}", objectsOnly=True) or []
    if not lights:
        if logger:
            logger.debug("No MMD light found; nothing to clear")
        return 0

    cleared = sum(
        cut_keyable_attrs(light_transform, _LIGHT_SELF_SHADOW_ATTRS)
        for light_transform in lights
    )
    if logger:
        logger.info(
            "Cleared self-shadow motion: %d attribute channels from %d light(s)",
            cleared,
            len(lights),
        )
    return cleared
