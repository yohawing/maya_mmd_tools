"""Import state and cleanup helpers for VMD conversion."""

import json
from typing import Any, Dict, Optional, Tuple, Union

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from ..core.constants import (
    ATTR_MMD_CAMERA,
    ATTR_MMD_LIGHT,
    ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
)
from ..core.exceptions import MMDImportException
from ..core.logger import get_logger
from ..core.namespace_utils import NamespaceUtils
from .vmd_context import VmdImportStateContext
from .vmd_ik_enabled_animation import ik_node_is_owned_by_root, root_owned_joints
from .vmd_morph_mapping import morph_node_is_owned_by_root
from .bone_morph_runtime import resolve_owned_bone_morph_base_routes
from .vmd_redirected_authoring_proxy import (
    resolve_redirected_authoring_proxy_authority,
)
from .vmd_rotation_time_curve import delete_vmd_rotation_time_curves_for_controls
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes
from ..core.mmd_control_rig_builder import CONTROL_RIG_CONTROL_OWNED, read_mmd_control_rig_metadata
from ..core.mmd_control_rig_motion import (
    control_rig_edit_ik_enabled_plugs_for_model,
    control_rig_edit_routes_for_joints,
)

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


def _canonical_node_path(node: Optional[str]) -> str:
    """Resolve one Maya node to a stable long name when possible."""
    if not node:
        return ""
    try:
        matches = cmds.ls(str(node), long=True) or []
    except Exception:
        matches = []
    return str(matches[0]) if len(matches) == 1 else str(node)


def _curve_uuid(curve: str) -> str:
    """Return an animCurve UUID for diagnostics, without making queries fatal."""
    try:
        values = cmds.ls(curve, uuid=True) or []
    except Exception:
        values = []
    return str(values[0]) if len(values) == 1 else ""


def _key_count_for_plug(plug: str) -> int:
    """Return the current key count for a plug or curve node."""
    try:
        values = cmds.keyframe(plug, query=True, timeChange=True) or []
    except Exception:
        values = []
    return len(values)


def _curve_records_for_plug(node: str, attribute: str) -> list:
    """Describe every animCurve currently driving one logical channel."""
    plug = f"{node}.{attribute}"
    try:
        direct = cmds.listConnections(
            plug,
            source=True,
            destination=False,
            type="animCurve",
        ) or []
    except Exception:
        direct = []
    curves = _animation_curve_nodes_for_plug(node, attribute, direct_curves=direct)
    records = []
    for curve in curves:
        records.append(
            {
                "name": str(curve),
                "uuid": _curve_uuid(str(curve)),
                "key_count": _key_count_for_plug(str(curve)),
            }
        )
    return records


def _read_vmd_clear_scope(target_model: Optional[str]) -> dict:
    """Read the small curve-identity journal stored on a model root."""
    if not target_model:
        return {}
    try:
        plug = f"{target_model}.{ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON}"
        if not cmds.objExists(plug):
            return {}
        raw = cmds.getAttr(plug)
        payload = json.loads(raw) if raw else {}
    except Exception:
        return {}
    scope = payload.get("clear_scope") if isinstance(payload, dict) else None
    if not isinstance(scope, dict) or scope.get("schema") != 1:
        return {}
    try:
        root_uuids = cmds.ls(target_model, uuid=True) or []
    except Exception:
        root_uuids = []
    if len(root_uuids) != 1 or str(scope.get("target_model_uuid") or "") != str(root_uuids[0]):
        return {}
    return dict(scope)


def _append_clear_route(routes: list, seen: set, node: str, attribute: str, source: str) -> None:
    """Append a unique logical clear route."""
    if not node or not attribute:
        return
    key = (_canonical_node_path(node), str(attribute))
    if key in seen:
        return
    seen.add(key)
    routes.append(
        {
            "source": str(source),
            "node": key[0],
            "attribute": key[1],
            "plug": f"{key[0]}.{key[1]}",
            "curves": _curve_records_for_plug(key[0], key[1]),
        }
    )


def build_motion_clear_inventory(
    converter_or_context: Union[Any, VmdImportStateContext],
    layer_name: str,
    target_namespace: Optional[str] = None,
    target_model: Optional[str] = None,
    *,
    legacy_routes: Optional[Dict[str, dict]] = None,
) -> dict:
    """Collect the physical authoring routes in one target-scoped report.

    The result is deliberately JSON-friendly.  It is used both before and
    after a clear operation, so a caller can distinguish an attempted
    attribute from actual keys removed and can report an unresolved ownership
    claim before Maya is mutated.
    """
    context = _resolve_import_state_context(converter_or_context)
    owned_joints = root_owned_joints(target_model) if target_model else set()
    target_joints = sorted(
        owned_joints if target_model else set(context.bone_name_mapping.values())
    )
    routes = []
    seen = set()
    for joint in target_joints:
        for attribute in (
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ):
            _append_clear_route(routes, seen, joint, attribute, "joint")

    blockers = []
    route_nodes = set(target_joints)
    route_map = legacy_routes or {}
    for joint, route in route_map.items():
        if not isinstance(route, dict):
            continue
        blocked_channels = route.get("blocked_channels") or ()
        if blocked_channels:
            blockers.append(
                {
                    "code": "route_ownership_blocked",
                    "reason": str(route.get("block_reason") or "route_ownership_unknown"),
                    "node": _canonical_node_path(joint),
                    "channels": sorted(str(channel) for channel in blocked_channels),
                    "source": "legacy_route_inventory",
                }
            )
        for channel, destination in (route.get("attr_targets") or {}).items():
            try:
                node, attribute = destination
            except (TypeError, ValueError):
                blockers.append(
                    {
                        "code": "route_ownership_blocked",
                        "reason": "malformed_authoring_route",
                        "node": _canonical_node_path(joint),
                        "channels": [str(channel)],
                        "source": "legacy_route_inventory",
                    }
                )
                continue
            route_nodes.add(_canonical_node_path(node))
            _append_clear_route(routes, seen, node, attribute, "legacy_route")

    if target_model:
        try:
            accumulator = resolve_owned_bone_morph_base_routes(target_joints)
        except Exception as exc:
            accumulator = None
            blockers.append(
                {
                    "code": "route_ownership_blocked",
                    "reason": "bone_morph_inventory_failed",
                    "detail": str(exc),
                    "source": "bone_morph_accumulator",
                }
            )
        if accumulator is not None:
            for joint, route in accumulator.routes.items():
                for channel, destination in route.items():
                    node, attribute = destination
                    route_nodes.add(_canonical_node_path(node))
                    _append_clear_route(routes, seen, node, attribute, "bone_morph_accumulator")
            for joint, (channels, reason) in accumulator.blocked.items():
                blockers.append(
                    {
                        "code": "route_ownership_blocked",
                        "reason": str(reason),
                        "node": _canonical_node_path(joint),
                        "channels": sorted(str(channel) for channel in channels),
                        "source": "bone_morph_accumulator",
                    }
                )

    for target_joint, info in context.collect_append_info().items():
        append_node = info.get("node") if isinstance(info, dict) else None
        if not append_node:
            continue
        owned = (
            _append_node_is_owned_by_root(
                append_node,
                target_joint,
                target_model,
                owned_joints,
                source_joint=info.get("source_joint") if isinstance(info, dict) else None,
            )
            if target_model
            else node_matches_target_namespace(target_joint, target_namespace)
            or node_matches_target_namespace(append_node, target_namespace)
        )
        if not owned:
            if not target_model or _canonical_node_path(target_joint) in owned_joints:
                blockers.append(
                    {
                        "code": "route_ownership_blocked",
                        "reason": "append_route_shared_or_unowned",
                        "node": _canonical_node_path(append_node),
                        "source": "append",
                    }
                )
            continue
        route_nodes.add(_canonical_node_path(append_node))
        for attribute in (
            "baseTranslateX",
            "baseTranslateY",
            "baseTranslateZ",
            "baseRotateX",
            "baseRotateY",
            "baseRotateZ",
        ):
            _append_clear_route(routes, seen, append_node, attribute, "append")

    for ik_node in _ls_mmd_ccd_ik_nodes():
        owned = (
            ik_node_is_owned_by_root(ik_node, target_model, owned_joints)
            if target_model
            else node_matches_target_namespace(ik_node, target_namespace)
        )
        if not owned:
            continue
        route_nodes.add(_canonical_node_path(ik_node))
        _append_clear_route(routes, seen, ik_node, "enabled", "ik")
        _append_clear_route(routes, seen, ik_node, "inputRotate", "ik")

    for mapping_entry in context.morph_name_mapping.values():
        for morph_node, weight_attr, _morph_name in context.iter_morph_mappings(mapping_entry):
            owned = (
                morph_node_is_owned_by_root(morph_node, target_model)
                if target_model
                else node_matches_target_namespace(morph_node, target_namespace)
            )
            if owned:
                route_nodes.add(_canonical_node_path(morph_node))
                _append_clear_route(routes, seen, morph_node, weight_attr, "morph")

    if target_model:
        control_metadata = read_mmd_control_rig_metadata(target_model)
        if control_metadata and control_metadata.get("owner") == CONTROL_RIG_CONTROL_OWNED:
            try:
                control_routes = control_rig_edit_routes_for_joints(target_joints)
                for route in control_routes.values():
                    for node, attribute in route.values():
                        route_nodes.add(_canonical_node_path(node))
                        _append_clear_route(routes, seen, node, attribute, "control_rig")
                for plug in control_rig_edit_ik_enabled_plugs_for_model(target_model):
                    node, attribute = str(plug).rsplit(".", 1)
                    route_nodes.add(_canonical_node_path(node))
                    _append_clear_route(routes, seen, node, attribute, "control_rig_ik")
            except Exception as exc:
                blockers.append(
                    {
                        "code": "route_ownership_blocked",
                        "reason": "control_rig_inventory_failed",
                        "detail": str(exc),
                        "source": "control_rig",
                    }
                )

    for joint in target_joints:
        proxy_route, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
        if not claimed:
            continue
        if not proxy_route:
            blockers.append(
                {
                    "code": "route_ownership_blocked",
                    "reason": "redirected_proxy_authority_stale_or_ambiguous",
                    "node": _canonical_node_path(joint),
                    "source": "redirected_proxy",
                }
            )
            continue
        for _channel, (node, attribute) in proxy_route.items():
            route_nodes.add(_canonical_node_path(node))
            _append_clear_route(routes, seen, node, attribute, "redirected_proxy")

    layer_attributes = []
    try:
        layer_attributes = cmds.animLayer(layer_name, query=True, attribute=True) or []
    except Exception:
        layer_attributes = []
    for attribute in layer_attributes:
        node = str(attribute).split(".", 1)[0]
        if target_model and not _nodes_are_exclusively_owned([node], set(route_nodes)):
            blockers.append(
                {
                    "code": "shared_animation_layer",
                    "reason": "animation_layer_contains_foreign_attribute",
                    "node": _canonical_node_path(node),
                    "layer": str(layer_name),
                    "source": "anim_layer",
                }
            )

    scope = _read_vmd_clear_scope(target_model)
    known_curve_uuids = {
        str(value)
        for value in (scope.get("curve_uuids") or [])
        if value
    }
    for route in routes:
        for curve in route["curves"]:
            if curve["key_count"] <= 0:
                continue
            if curve["uuid"] and curve["uuid"] in known_curve_uuids:
                curve["ownership"] = "vmd"
            else:
                curve["ownership"] = "unknown"
                blockers.append(
                    {
                        "code": "unknown_curve_ownership",
                        "reason": "curve_has_keys_without_vmd_provenance",
                        "node": route["node"],
                        "attribute": route["attribute"],
                        "curve_uuid": curve["uuid"],
                        "source": route["source"],
                    }
                )

    return {
        "schema": 1,
        "target_model": _canonical_node_path(target_model),
        "target_namespace": str(target_namespace or ""),
        "layer_name": str(layer_name),
        "routes": routes,
        "route_count": len(routes),
        "key_count": sum(
            int(curve.get("key_count", 0))
            for route in routes
            for curve in route.get("curves", [])
        ),
        "curve_uuids": sorted(
            {
                curve["uuid"]
                for route in routes
                for curve in route.get("curves", [])
                if curve.get("uuid")
            }
        ),
        "known_curve_uuids": sorted(known_curve_uuids),
        "blockers": blockers,
    }


def refresh_motion_clear_inventory(inventory: dict) -> dict:
    """Recount curves on an already validated physical route plan."""
    routes = []
    for route in inventory.get("routes", []) or []:
        refreshed = dict(route)
        refreshed["curves"] = _curve_records_for_plug(
            str(route.get("node") or ""),
            str(route.get("attribute") or ""),
        )
        routes.append(refreshed)
    result = dict(inventory)
    result["routes"] = routes
    result["route_count"] = len(routes)
    result["key_count"] = sum(
        int(curve.get("key_count", 0))
        for route in routes
        for curve in route.get("curves", [])
    )
    result["curve_uuids"] = sorted(
        {
            curve["uuid"]
            for route in routes
            for curve in route.get("curves", [])
            if curve.get("uuid")
        }
    )
    result["blockers"] = []
    return result


def clear_existing_motion(
    converter_or_context: Union[Any, VmdImportStateContext],
    layer_name: str,
    target_namespace: Optional[str] = None,
    target_model: Optional[str] = None,
    *,
    preserve_curve_nodes: bool = False,
    detached_curve_nodes=None,
    profile: Optional[Dict[str, Any]] = None,
    strict: bool = False,
    legacy_routes: Optional[Dict[str, dict]] = None,
) -> dict:
    """Delete all existing character motion keys for the target model.

    When ``target_model`` is explicit, every joint below that model root is
    cleared.  The clear scope must not depend on the incoming VMD's bone-name
    mapping: an omitted or unsupported bone in the new motion must not leave
    stale keys on the character.  Root ownership checks still isolate other
    models and scene-level camera/light animation.

    ``preserve_curve_nodes`` is used by the Control Rig preflight transaction
    to retain legacy animCurve identity for exact rollback.  Such curves are
    detached after their keys are removed and are deleted on successful commit.
    """
    context = _resolve_import_state_context(converter_or_context)
    before = build_motion_clear_inventory(
        context,
        layer_name,
        target_namespace,
        target_model,
        legacy_routes=legacy_routes,
    )
    clear_profile = {
        "schema": 1,
        "requested": {
            "clear_existing_motion": True,
            "target_model": before.get("target_model", ""),
            "target_namespace": before.get("target_namespace", ""),
            "layer_name": str(layer_name),
            "strict_ownership": bool(strict),
        },
        "effective": {
            "target_model": before.get("target_model", ""),
            "route_count": before.get("route_count", 0),
            "known_curve_count": len(before.get("known_curve_uuids", [])),
        },
        "before": before,
        "status": "pending",
    }
    if isinstance(profile, dict):
        profile["motion_clear"] = clear_profile
    if strict and before.get("blockers"):
        clear_profile["status"] = "blocked"
        clear_profile["failure"] = {
            "code": "vmd_clear_ownership_blocked",
            "reasons": sorted(
                {
                    str(item.get("reason") or item.get("code") or "unknown")
                    for item in before["blockers"]
                }
            ),
        }
        raise MMDImportException(
            "Existing VMD motion clear blocked by unresolved ownership: "
            + "; ".join(clear_profile["failure"]["reasons"]),
            reason_code="vmd_clear_ownership_blocked",
        )
    cleared = 0
    owned_motion_nodes = {
        str(route.get("node") or "")
        for route in before.get("routes", [])
        if route.get("node")
    }
    target_joints = (
        set(root_owned_joints(target_model))
        if target_model
        else set(context.bone_name_mapping.values())
    )
    fallback_translates = _capture_fallback_rest_translates(target_joints, context.logger)
    if not preserve_curve_nodes:
        cleared += len(delete_vmd_rotation_time_curves_for_controls(target_joints))

    for route in before.get("routes", []) or []:
        source = str(route.get("source") or "")
        keep_curve = preserve_curve_nodes or source != "joint"
        cleared += cut_keyable_attrs(
            str(route.get("node") or ""),
            (str(route.get("attribute") or ""),),
            preserve_curve_nodes=keep_curve,
            detached_curve_nodes=(
                detached_curve_nodes if preserve_curve_nodes else None
            ),
        )

    morph_nodes = {
        str(route.get("node") or "")
        for route in before.get("routes", [])
        if route.get("source") == "morph"
    }

    can_delete_layer = not target_model or _anim_layer_is_exclusively_owned_by(
        layer_name,
        owned_motion_nodes,
    )
    if cmds.objExists(layer_name) and can_delete_layer and not preserve_curve_nodes:
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

    after = refresh_motion_clear_inventory(before)
    clear_profile["after"] = after
    clear_profile["effective"]["cleared_key_count"] = max(
        int(before.get("key_count", 0)) - int(after.get("key_count", 0)),
        0,
    )
    residuals = [
        {
            "plug": route.get("plug", ""),
            "curves": [
                curve
                for curve in route.get("curves", [])
                if int(curve.get("key_count", 0)) > 0
            ],
        }
        for route in after.get("routes", [])
        if any(int(curve.get("key_count", 0)) > 0 for curve in route.get("curves", []))
    ]
    if residuals:
        clear_profile["status"] = "residual"
        clear_profile["failure"] = {
            "code": "vmd_clear_residual_keys",
            "residuals": residuals,
        }
        if strict:
            raise MMDImportException(
                "Existing VMD motion clear left residual keys",
                reason_code="vmd_clear_residual_keys",
            )
    else:
        clear_profile["status"] = "success"

    context.logger.info(
        "Cleared existing VMD motion: keys_or_layers=%d joints=%d morph_nodes=%d",
        cleared,
        len(target_joints),
        len(morph_nodes),
    )
    return clear_profile


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


def cut_keyable_attrs(
    node: str,
    attrs: Tuple[str, ...],
    *,
    preserve_curve_nodes: bool = False,
    detached_curve_nodes=None,
) -> int:
    """Delete keys for existing attrs and return the number of keys removed."""
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
                        removed = curve_fn.numKeys
                        for index in reversed(range(curve_fn.numKeys)):
                            curve_fn.remove(index)
                        cleared += removed
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
                before = _key_count_for_plug(plug)
                cmds.cutKey(node, attribute=target_attr)
                after = _key_count_for_plug(plug)
                cleared += max(before - after, 0)
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
