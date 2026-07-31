"""Safe, target-exclusive animation-layer ownership for Control Rig EDIT.

This module deliberately supports one narrow graph: a non-base ``animLayer``
whose members all belong to the target model and whose direct layer curves feed
the corresponding ``animBlendNode`` inputB.  The layer and its curves stay in
the scene; EDIT only moves that direct source edge through the controller.
Shared, foreign, nested, or otherwise unknown graphs fail closed.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional


class MmdControlRigAnimLayerError(RuntimeError):
    """Raised when an animation-layer graph is outside the safe contract."""


_BASE_LAYER_NAMES = frozenset({"BaseAnimation", "baseAnimation"})
_SETTING_FLAGS = (
    "weight",
    "mute",
    "solo",
    "passthrough",
    "override",
    "selected",
    "preferred",
    "lock",
)


def capture_mmd_control_rig_anim_layers(
    cmds,
    model_root: str,
    target_plugs: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Capture supported target-exclusive layers and their direct routes.

    The result is metadata-safe: every layer, curve, blend node, and plug has
    a UUID-backed reference in addition to its readable scene name.
    """
    root = _canonical_node(cmds, model_root)
    wanted = (
        None
        if target_plugs is None
        else {_canonical_plug(cmds, plug) for plug in target_plugs if plug}
    )
    layers = []
    routes: Dict[str, Dict[str, Any]] = {}
    for raw_layer in cmds.ls(type="animLayer") or []:
        layer = str(raw_layer)
        if layer in _BASE_LAYER_NAMES:
            continue
        attributes = _layer_attributes(cmds, layer)
        blend_nodes = [str(node) for node in (cmds.animLayer(layer, query=True, blendNodes=True) or [])]
        if not attributes and not blend_nodes:
            continue
        if not attributes:
            raise MmdControlRigAnimLayerError(
                f"animLayer has an unknown graph without membership: {layer}"
            )
        parent = _query_parent(cmds, layer)
        if parent and parent not in _BASE_LAYER_NAMES:
            raise MmdControlRigAnimLayerError(
                f"nested/shared animLayer is unsupported: {layer}"
            )
        if any(
            not _under_target_scope(cmds, str(plug).split(".", 1)[0], root)
            for plug in attributes
        ):
            raise MmdControlRigAnimLayerError(
                f"animLayer contains foreign/shared target attributes: {layer}"
            )
        layer_uuid = _node_uuid(cmds, layer)
        layer_row: Dict[str, Any] = {
            "name": layer,
            "uuid": layer_uuid,
            "parent": parent,
            "parentUuid": _node_uuid(cmds, parent) if parent else None,
            "attributes": list(attributes),
            "attributeRefs": [_plug_reference(cmds, plug) for plug in attributes],
            "settings": _layer_settings(cmds, layer),
            "blendNodes": [],
            "curves": [],
            "edges": [],
        }
        known_nodes = {layer, *blend_nodes}
        for blend_node in blend_nodes:
            if not str(cmds.nodeType(blend_node)).startswith("animBlendNode"):
                raise MmdControlRigAnimLayerError(
                    f"unknown animLayer blend node: {blend_node}"
                )
            layer_row["blendNodes"].append(
                {
                    "name": blend_node,
                    "uuid": _node_uuid(cmds, blend_node),
                    "edges": [],
                }
            )
            known_nodes.add(blend_node)
        route_targets = attributes if wanted is None else wanted.intersection(attributes)
        for target in sorted(route_targets):
            route = _capture_route(cmds, layer, layer_row, target, blend_nodes)
            routes[target] = route
            curve = route.get("curve")
            if curve:
                curve_node = curve.split(".", 1)[0]
                known_nodes.add(curve_node)
                if not any(row.get("uuid") == _node_uuid(cmds, curve_node) for row in layer_row["curves"]):
                    layer_row["curves"].append(_capture_curve(cmds, curve_node, curve))
        layer_row["edges"] = _capture_edges(cmds, known_nodes)
        for row in layer_row["blendNodes"]:
            row["edges"] = [edge for edge in layer_row["edges"] if edge["sourceNode"] == row["name"] or edge["targetNode"] == row["name"]]
        layers.append(layer_row)
    return {"layers": layers, "routes": routes}


def resolve_mmd_control_rig_anim_layer_route(cmds, route: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve one persisted route by UUID without changing the scene."""
    resolved = dict(route)
    for key, description in (
        ("targetRef", "animLayer target"),
        ("curveRef", "animLayer curve"),
        ("blendRef", "animLayer layered plug"),
        ("blendOutputRef", "animLayer blend output"),
    ):
        reference = route.get(key)
        if not reference:
            resolved[key[:-3] if key.endswith("Ref") else key] = None
            continue
        resolved[key[:-3]] = _resolve_plug_reference(cmds, reference, description)
    return resolved


def apply_mmd_control_rig_anim_layer_route(
    cmds,
    route: Mapping[str, Any],
    control_plug: str,
    operations,
) -> None:
    """Move one layer source edge through a controller during EDIT."""
    current = resolve_mmd_control_rig_anim_layer_route(cmds, route)
    curve = current.get("curve")
    layered = current.get("blend")
    if not layered:
        raise MmdControlRigAnimLayerError("animLayer route layered plug is missing")
    if curve:
        if not cmds.isConnected(curve, layered):
            raise MmdControlRigAnimLayerError(
                f"animLayer source edge is not direct: {curve} -> {layered}"
            )
        cmds.disconnectAttr(curve, layered)
        operations.append(("connect", curve, layered))
        if not cmds.isConnected(curve, control_plug):
            cmds.connectAttr(curve, control_plug, force=False)
            operations.append(("disconnect", curve, control_plug))
    if not cmds.isConnected(control_plug, layered):
        cmds.connectAttr(control_plug, layered, force=False)
        operations.append(("disconnect", control_plug, layered))


def restore_mmd_control_rig_anim_layer_route(
    cmds,
    route: Mapping[str, Any],
    control_plug: str,
) -> None:
    """Return one controller-routed layer source to its original inputB."""
    current = resolve_mmd_control_rig_anim_layer_route(cmds, route)
    curve = current.get("curve")
    layered = current.get("blend")
    if not layered:
        raise MmdControlRigAnimLayerError("animLayer route layered plug is missing")
    incoming = [
        str(source)
        for source in (cmds.listConnections(control_plug, source=True, destination=False, plugs=True) or [])
    ]
    if incoming and (not curve or incoming != [curve]):
        raise MmdControlRigAnimLayerError(
            f"foreign controller source on animLayer route: {control_plug}"
        )
    if cmds.isConnected(control_plug, layered):
        cmds.disconnectAttr(control_plug, layered)
    if curve and cmds.isConnected(curve, control_plug):
        cmds.disconnectAttr(curve, control_plug)
    if curve and not cmds.isConnected(curve, layered):
        cmds.connectAttr(curve, layered, force=False)


def restore_mmd_control_rig_anim_layer_journal(cmds, journal: Mapping[str, Any]) -> None:
    """Restore layer settings and all captured internal edges exactly."""
    for layer_row in journal.get("layers", []) or []:
        layer = _resolve_uuid(cmds, layer_row.get("uuid"), "animLayer")
        settings = layer_row.get("settings") or {}
        for flag in _SETTING_FLAGS:
            if flag not in settings:
                continue
            cmds.animLayer(layer, edit=True, **{flag: settings[flag]})
        edges = [
            (_resolve_plug_reference(cmds, edge["sourceRef"], "animLayer source"),
             _resolve_plug_reference(cmds, edge["targetRef"], "animLayer target"))
            for edge in layer_row.get("edges", []) or []
        ]
        desired = {target: source for source, target in edges}
        for target, source in desired.items():
            for current_source in cmds.listConnections(target, source=True, destination=False, plugs=True) or []:
                if str(current_source) != source:
                    cmds.disconnectAttr(current_source, target)
            if not cmds.isConnected(source, target):
                cmds.connectAttr(source, target, force=False)


def _capture_route(cmds, layer, layer_row, target, blend_nodes):
    curves = cmds.animLayer(layer, query=True, findCurveForPlug=target) or []
    curve = str(curves[0]) + ".output" if curves else None
    layered = cmds.animLayer(layer, query=True, layeredPlug=target)
    if isinstance(layered, (list, tuple)):
        layered = layered[0] if layered else None
    layered = str(layered) if layered else None
    if not layered:
        raise MmdControlRigAnimLayerError(f"animLayer layered plug is missing: {target}")
    blend_node = layered.split(".", 1)[0]
    layered_attribute = str(layered).rsplit(".", 1)[-1]
    if blend_node not in blend_nodes or not (
        layered_attribute == "inputB"
        or layered_attribute.startswith("inputB")
        and layered_attribute[-1:] in "XYZ"
    ):
        raise MmdControlRigAnimLayerError(f"unsupported animLayer input route: {target}")
    suffix = layered_attribute[len("inputB") :]
    blend_output = f"{blend_node}.output{suffix}"
    target_sources = [str(value) for value in (cmds.listConnections(target, source=True, destination=False, plugs=True) or [])]
    if target_sources != [blend_output]:
        raise MmdControlRigAnimLayerError(f"animLayer target has an unknown writer: {target}")
    if curve and not cmds.isConnected(curve, layered):
        raise MmdControlRigAnimLayerError(f"animLayer curve is not connected directly: {curve}")
    return {
        "target": target,
        "targetRef": _plug_reference(cmds, target),
        "layerUuid": layer_row["uuid"],
        "layerName": layer_row["name"],
        "curve": curve,
        "curveRef": _plug_reference(cmds, curve) if curve else None,
        "blend": layered,
        "blendRef": _plug_reference(cmds, layered),
        "blendOutput": blend_output,
        "blendOutputRef": _plug_reference(cmds, blend_output),
    }


def _capture_curve(cmds, node, plug):
    times = [float(value) for value in (cmds.keyframe(node, query=True, timeChange=True) or [])]
    values = [float(value) for value in (cmds.keyframe(node, query=True, valueChange=True) or [])]
    keys = []
    for time, value in zip(times, values):
        payload = {"time": time, "value": value}
        for side in ("in", "out"):
            try:
                payload[f"{side}TangentType"] = (cmds.keyTangent(node, query=True, time=(time, time), **{f"{side}TangentType": True}) or [None])[0]
            except Exception:
                payload[f"{side}TangentType"] = None
        keys.append(payload)
    return {
        "name": node,
        "uuid": _node_uuid(cmds, node),
        "plug": plug,
        "plugRef": _plug_reference(cmds, plug),
        "nodeType": str(cmds.nodeType(node)),
        "keys": keys,
        "timeInput": [
            str(source)
            for source in (cmds.listConnections(f"{node}.input", source=True, destination=False, plugs=True) or [])
        ],
    }


def _capture_edges(cmds, nodes):
    result = []
    seen = set()
    for node in sorted(nodes):
        pairs = cmds.listConnections(node, connections=True, plugs=True) or []
        for index in range(0, len(pairs) - 1, 2):
            first, second = str(pairs[index]), str(pairs[index + 1])
            if cmds.isConnected(first, second):
                source, target = first, second
            elif cmds.isConnected(second, first):
                source, target = second, first
            else:
                continue
            key = (source, target)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "source": source,
                    "target": target,
                    "sourceRef": _plug_reference(cmds, source),
                    "targetRef": _plug_reference(cmds, target),
                    "sourceNode": source.split(".", 1)[0],
                    "targetNode": target.split(".", 1)[0],
                }
            )
    return result


def _layer_attributes(cmds, layer):
    result = []
    for raw in cmds.animLayer(layer, query=True, attribute=True) or []:
        result.append(_canonical_plug(cmds, str(raw)))
    return tuple(sorted(set(result)))


def _layer_settings(cmds, layer):
    settings = {}
    for flag in _SETTING_FLAGS:
        try:
            settings[flag] = cmds.animLayer(layer, query=True, **{flag: True})
        except Exception as exc:
            raise MmdControlRigAnimLayerError(f"could not inspect animLayer setting: {layer}.{flag}") from exc
    return settings


def _query_parent(cmds, layer):
    try:
        parent = cmds.animLayer(layer, query=True, parent=True)
    except Exception as exc:
        raise MmdControlRigAnimLayerError(f"could not inspect animLayer parent: {layer}") from exc
    if isinstance(parent, (list, tuple)):
        parent = parent[0] if parent else None
    return str(parent) if parent else None


def _canonical_node(cmds, node):
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise MmdControlRigAnimLayerError(f"expected one target model root: {node}")
    return str(matches[0])


def _canonical_plug(cmds, plug):
    node, separator, attribute = str(plug).partition(".")
    if not separator or not attribute:
        raise MmdControlRigAnimLayerError(f"invalid animLayer plug: {plug}")
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1:
        raise MmdControlRigAnimLayerError(f"ambiguous animLayer plug node: {plug}")
    return f"{matches[0]}.{attribute}"


def _under_root(node, root):
    return node == root or node.startswith(root + "|")


def _under_target_scope(cmds, node, root, visited=None):
    """Allow MMD helpers only when every output path ends under ``root``.

    A helper node may live outside the model hierarchy, but a target-exclusive
    layer cannot own a helper that also drives another model or an unrelated
    scene node.  Therefore all reachable terminal destinations must be inside
    the requested model root.  ``memo`` caches completed nodes while
    ``active`` detects cycles without rejecting a shared, target-contained DAG.
    """
    memo = visited if isinstance(visited, dict) else {}
    active = set()

    def check(current):
        matches = cmds.ls(current, long=True) or []
        if len(matches) == 1:
            current = str(matches[0])
        if _under_root(current, root):
            return True
        if current in memo:
            return memo[current]
        if current in active or not cmds.objExists(current):
            return False
        active.add(current)
        try:
            node_type = str(cmds.nodeType(current))
        except Exception:
            active.discard(current)
            memo[current] = False
            return False
        if not node_type.startswith("mmd"):
            active.discard(current)
            memo[current] = False
            return False
        destinations = cmds.listConnections(
            current,
            source=False,
            destination=True,
            plugs=True,
        ) or []
        # Maya records animLayer membership as message destinations on every
        # layered target/helper node (``layer.dagSetMembers[...]``).  Those
        # bookkeeping edges are not evaluated output routes and must not make
        # an otherwise target-exclusive helper look shared/foreign.
        destinations = [
            destination
            for destination in destinations
            if str(cmds.nodeType(str(destination).split(".", 1)[0])) != "animLayer"
        ]
        # An external helper with no output cannot be proven to belong to this
        # model.  Requiring every branch to resolve also rejects foreign
        # fan-out even when one branch reaches the target root.
        result = bool(destinations) and all(
            check(str(destination).split(".", 1)[0]) for destination in destinations
        )
        active.discard(current)
        memo[current] = result
        return result

    return check(node)


def _node_uuid(cmds, node):
    values = cmds.ls(node, uuid=True) or []
    if len(values) != 1:
        raise MmdControlRigAnimLayerError(f"could not resolve animLayer UUID: {node}")
    return str(values[0])


def _plug_reference(cmds, plug):
    node, separator, attribute = str(plug).partition(".")
    if not separator or not attribute:
        raise MmdControlRigAnimLayerError(f"invalid animLayer plug: {plug}")
    return {"nodeUuid": _node_uuid(cmds, node), "attribute": attribute}


def _resolve_uuid(cmds, uuid, description):
    if not uuid:
        raise MmdControlRigAnimLayerError(f"{description} UUID is missing")
    nodes = cmds.ls(str(uuid), long=True) or []
    if len(nodes) != 1:
        raise MmdControlRigAnimLayerError(f"{description} node is missing: {uuid}")
    return str(nodes[0])


def _resolve_plug_reference(cmds, reference, description):
    if not isinstance(reference, Mapping):
        raise MmdControlRigAnimLayerError(f"{description} reference is missing")
    node = _resolve_uuid(cmds, reference.get("nodeUuid"), description)
    attribute = reference.get("attribute")
    if not attribute:
        raise MmdControlRigAnimLayerError(f"{description} attribute is missing")
    plug = f"{node}.{attribute}"
    if not cmds.objExists(plug):
        raise MmdControlRigAnimLayerError(f"{description} plug is missing: {plug}")
    return plug
