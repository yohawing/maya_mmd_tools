"""Persistent Transform authoring proxy for redirected VMD XYZ channels."""

from __future__ import annotations

import json
import math
from typing import Dict, Mapping, Optional, Tuple

from maya import cmds


_MARKER = "mmd_vmd_authoring_proxy"
_TARGET = "mmd_vmd_authoring_target"
_TARGET_UUID = "mmd_vmd_authoring_target_uuid"
_TARGET_PATH = "mmd_vmd_authoring_target_path"
_DESTINATIONS = "mmd_vmd_authoring_destinations_json"
_SOURCE_OWNERS = "mmd_vmd_authoring_source_owners"
_CONVERSION_WRAPPERS = "mmd_vmd_authoring_conversion_wrappers"


def ensure_redirected_authoring_proxy(
    joint: str,
    attr_targets: Mapping[str, Tuple[str, str]],
) -> Dict[str, Tuple[str, str]]:
    """Create/reuse a proxy for complete XYZ groups on non-Transform owners."""
    eligible = _eligible_destinations(attr_targets)
    if not eligible:
        return {}
    existing, authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
    if claimed:
        if redirected_authority_matches(eligible, authority) and set(eligible) == set(existing):
            return dict(existing)
        raise RuntimeError("redirected VMD authoring proxy destination authority is stale")

    target_path = _canonical_path(joint)
    target_uuid = _single_uuid(target_path)
    if not target_path or not target_uuid:
        raise RuntimeError("redirected VMD authoring proxy target is ambiguous")
    destination_records = {}
    incoming: Dict[str, Optional[str]] = {}
    values = {}
    for channel, (node, attribute) in eligible.items():
        plug = f"{node}.{attribute}"
        sources = cmds.listConnections(
            plug, source=True, destination=False, plugs=True
        ) or []
        if len(sources) > 1:
            raise RuntimeError(f"redirected VMD destination is ambiguous: {plug}")
        incoming[channel] = str(sources[0]) if sources else None
        values[channel] = float(cmds.getAttr(plug))
        destination_records[channel] = {
            "plug": plug,
            "owner_uuid": _single_uuid(node),
        }
        if not destination_records[channel]["owner_uuid"]:
            raise RuntimeError(f"redirected VMD destination owner is ambiguous: {node}")

    proxy = cmds.createNode(
        "transform",
        name=f"{target_path.rsplit('|', 1)[-1]}_vmdAuthoring",
    )
    try:
        cmds.addAttr(proxy, longName=_MARKER, attributeType="bool")
        cmds.setAttr(f"{proxy}.{_MARKER}", True)
        cmds.addAttr(proxy, longName=_TARGET, attributeType="message")
        cmds.connectAttr(f"{target_path}.message", f"{proxy}.{_TARGET}")
        cmds.addAttr(
            proxy,
            longName=_SOURCE_OWNERS,
            attributeType="message",
            multi=True,
        )
        owner_nodes = sorted(
            {str(record["plug"]).partition(".")[0] for record in destination_records.values()}
        )
        for index, owner in enumerate(owner_nodes):
            cmds.connectAttr(
                f"{owner}.message",
                f"{proxy}.{_SOURCE_OWNERS}[{index}]",
            )
        for attribute, value in (
            (_TARGET_UUID, target_uuid),
            (_TARGET_PATH, target_path),
        ):
            cmds.addAttr(proxy, longName=attribute, dataType="string")
            cmds.setAttr(f"{proxy}.{attribute}", value, type="string")
        cmds.addAttr(proxy, longName=_DESTINATIONS, dataType="string")
        for attribute, value in (
            ("visibility", False),
            ("inheritsTransform", False),
            ("hiddenInOutliner", True),
        ):
            if cmds.objExists(f"{proxy}.{attribute}"):
                cmds.setAttr(f"{proxy}.{attribute}", value)
        if cmds.objExists(f"{target_path}.rotateOrder"):
            cmds.connectAttr(
                f"{target_path}.rotateOrder",
                f"{proxy}.rotateOrder",
                force=True,
            )
        conversion_nodes = []
        for channel, record in destination_records.items():
            proxy_plug = f"{proxy}.{channel}"
            destination = str(record["plug"])
            cmds.setAttr(proxy_plug, values[channel])
            source = incoming[channel]
            if source:
                cmds.disconnectAttr(source, destination)
                cmds.connectAttr(source, proxy_plug, force=True)
            cmds.connectAttr(proxy_plug, destination, force=True)
            conversion = _unit_conversion_authority(proxy_plug, destination)
            if conversion is not None:
                wrapper, wrapper_uuid, factor = conversion
                record["unit_conversion"] = {
                    "uuid": wrapper_uuid,
                    "factor": factor,
                }
                conversion_nodes.append(wrapper)
        if conversion_nodes:
            cmds.addAttr(
                proxy,
                longName=_CONVERSION_WRAPPERS,
                attributeType="message",
                multi=True,
            )
            for index, wrapper in enumerate(sorted(set(conversion_nodes))):
                cmds.connectAttr(
                    f"{wrapper}.message",
                    f"{proxy}.{_CONVERSION_WRAPPERS}[{index}]",
                )
        cmds.setAttr(
            f"{proxy}.{_DESTINATIONS}",
            json.dumps(destination_records, separators=(",", ":"), sort_keys=True),
            type="string",
        )
        resolved, authority, claimed = resolve_redirected_authoring_proxy_authority(
            target_path
        )
        if (
            not claimed
            or not redirected_authority_matches(eligible, authority)
            or set(resolved) != set(eligible)
        ):
            raise RuntimeError("redirected VMD authoring proxy validation failed")
        return dict(resolved)
    except Exception:
        try:
            if cmds.objExists(proxy):
                cmds.delete(proxy)
        finally:
            for channel, record in destination_records.items():
                destination = str(record["plug"])
                source = incoming[channel]
                if source:
                    if not cmds.isConnected(source, destination):
                        cmds.connectAttr(source, destination, force=True)
                else:
                    cmds.setAttr(destination, values[channel])
        raise


def resolve_redirected_authoring_proxy(
    joint: str,
) -> Tuple[Dict[str, Tuple[str, str]], bool]:
    """Resolve one target-owned proxy; malformed claims return ``({}, True)``."""
    route, _authority, claimed = resolve_redirected_authoring_proxy_authority(joint)
    return route, claimed


def resolve_redirected_authoring_proxy_authority(
    joint: str,
) -> Tuple[
    Dict[str, Tuple[str, str]],
    Dict[str, Tuple[str, str]],
    bool,
]:
    """Resolve proxy route and its validated current destination authority."""
    target_path = _canonical_path(joint)
    target_uuid = _single_uuid(target_path)
    if not target_path or not target_uuid:
        return {}, {}, False
    destinations = cmds.listConnections(
        f"{target_path}.message",
        source=False,
        destination=True,
        plugs=True,
    ) or []
    candidates = []
    claimed = False
    for destination in destinations:
        proxy, separator, attribute = str(destination).partition(".")
        if not separator or attribute != _TARGET:
            continue
        claimed = True
        route, authority = _validated_proxy_route(proxy, target_path, target_uuid)
        if route:
            candidates.append((proxy, route, authority))
    if len(candidates) != 1:
        return {}, {}, claimed
    return candidates[0][1], candidates[0][2], True


def redirected_authority_matches(
    current: Mapping[str, Tuple[str, str]],
    authority: Mapping[str, Tuple[str, str]],
) -> bool:
    """Compare logical destinations by channel, owner UUID, and attribute."""
    if set(current) != set(authority):
        return False
    return all(
        current[channel][1] == authority[channel][1]
        and _single_uuid(current[channel][0]) == _single_uuid(authority[channel][0])
        for channel in current
    )


def _eligible_destinations(
    attr_targets: Mapping[str, Tuple[str, str]],
) -> Dict[str, Tuple[str, str]]:
    eligible = {}
    for kind in ("translate", "rotate"):
        channels = tuple(f"{kind}{axis}" for axis in "XYZ")
        if not all(channel in attr_targets for channel in channels):
            continue
        targets = [attr_targets[channel] for channel in channels]
        nodes = {node for node, _attribute in targets}
        attributes = tuple(attribute for _node, attribute in targets)
        if len(nodes) != 1 or not _xyz_siblings(attributes):
            continue
        node = targets[0][0]
        if cmds.nodeType(node) in {"transform", "joint"} and attributes == channels:
            continue
        eligible.update(zip(channels, targets))
    return eligible


def _validated_proxy_route(
    proxy: str,
    target_path: str,
    target_uuid: str,
) -> Tuple[Dict[str, Tuple[str, str]], Dict[str, Tuple[str, str]]]:
    try:
        if (
            cmds.nodeType(proxy) != "transform"
            or not cmds.attributeQuery(_MARKER, node=proxy, exists=True)
            or not bool(cmds.getAttr(f"{proxy}.{_MARKER}"))
            or str(cmds.getAttr(f"{proxy}.{_TARGET_UUID}") or "") != target_uuid
            or str(cmds.getAttr(f"{proxy}.{_TARGET_PATH}") or "") != target_path
        ):
            return {}, {}
        records = json.loads(cmds.getAttr(f"{proxy}.{_DESTINATIONS}") or "{}")
        if not isinstance(records, dict) or not records:
            return {}, {}
        connected_owner_uuids = {
            _single_uuid(str(owner))
            for owner in (
                cmds.listConnections(
                    f"{proxy}.{_SOURCE_OWNERS}",
                    source=True,
                    destination=False,
                )
                or []
            )
        }
        conversion_authorities = [
            record.get("unit_conversion")
            for record in records.values()
            if isinstance(record, dict) and record.get("unit_conversion") is not None
        ]
        connected_conversion_uuids = set()
        if conversion_authorities:
            if not cmds.attributeQuery(
                _CONVERSION_WRAPPERS, node=proxy, exists=True
            ):
                return {}, {}
            connected_conversion_uuids = {
                _single_uuid(str(wrapper))
                for wrapper in (
                    cmds.listConnections(
                        f"{proxy}.{_CONVERSION_WRAPPERS}",
                        source=True,
                        destination=False,
                    )
                    or []
                )
            }
        route = {}
        authority = {}
        for channel, record in records.items():
            if channel not in {
                f"{kind}{axis}" for kind in ("translate", "rotate") for axis in "XYZ"
            } or not isinstance(record, dict):
                return {}, {}
            destination = str(record.get("plug") or "")
            owner = destination.partition(".")[0]
            if (
                not destination
                or _single_uuid(owner) != str(record.get("owner_uuid") or "")
                or str(record.get("owner_uuid") or "") not in connected_owner_uuids
                or not _proxy_drives_destination(
                    f"{proxy}.{channel}",
                    destination,
                    record.get("unit_conversion"),
                    connected_conversion_uuids,
                )
            ):
                return {}, {}
            route[channel] = (proxy, channel)
            authority[channel] = (owner, destination.partition(".")[2])
        return route, authority
    except Exception:
        return {}, {}


def _unit_conversion_authority(
    proxy_plug: str,
    destination: str,
) -> Optional[Tuple[str, str, float]]:
    """Return Maya's exact single conversion wrapper, or ``None`` for a direct edge."""

    if cmds.isConnected(proxy_plug, destination):
        return None
    incoming = cmds.listConnections(
        destination,
        source=True,
        destination=False,
        plugs=True,
        skipConversionNodes=False,
    ) or []
    if isinstance(incoming, (str, bytes)) or len(incoming) != 1:
        raise RuntimeError("redirected VMD destination is not driven by one source")
    wrapper_output = str(incoming[0])
    wrapper, separator, output_attr = wrapper_output.partition(".")
    if (
        not separator
        or output_attr != "output"
        or str(cmds.nodeType(wrapper) or "") != "unitConversion"
        or not cmds.isConnected(wrapper_output, destination)
    ):
        raise RuntimeError("redirected VMD destination has an unexpected conversion wrapper")
    wrapper_input = f"{wrapper}.input"
    sources = cmds.listConnections(
        wrapper_input,
        source=True,
        destination=False,
        plugs=True,
        skipConversionNodes=False,
    ) or []
    if (
        isinstance(sources, (str, bytes))
        or len(sources) != 1
        or str(sources[0]) != proxy_plug
        or not cmds.isConnected(proxy_plug, wrapper_input)
    ):
        raise RuntimeError("redirected VMD conversion input authority is invalid")
    wrapper_uuid = _single_uuid(wrapper)
    factor = float(cmds.getAttr(f"{wrapper}.conversionFactor"))
    if not wrapper_uuid or not math.isfinite(factor):
        raise RuntimeError("redirected VMD conversion authority is invalid")
    return wrapper, wrapper_uuid, factor


def _proxy_drives_destination(
    proxy_plug: str,
    destination: str,
    expected_conversion: object,
    owned_conversion_uuids: set[str],
) -> bool:
    """Validate the recorded identity and factor of an owned conversion edge."""

    try:
        actual = _unit_conversion_authority(proxy_plug, destination)
    except Exception:
        return False
    if expected_conversion is None:
        return actual is None
    if not isinstance(expected_conversion, Mapping) or actual is None:
        return False
    _wrapper, actual_uuid, actual_factor = actual
    try:
        expected_uuid = str(expected_conversion["uuid"])
        expected_factor = float(expected_conversion["factor"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        expected_uuid == actual_uuid
        and actual_uuid in owned_conversion_uuids
        and math.isfinite(expected_factor)
        and math.isclose(
            actual_factor,
            expected_factor,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    )


def _canonical_path(node: str) -> Optional[str]:
    matches = cmds.ls(node, long=True) or []
    return str(matches[0]) if len(matches) == 1 else None


def _single_uuid(node: Optional[str]) -> str:
    if not node:
        return ""
    values = cmds.ls(node, uuid=True) or []
    return str(values[0]) if len(values) == 1 else ""


def _xyz_siblings(attributes: Tuple[str, ...]) -> bool:
    return len(attributes) == 3 and all(
        attribute.endswith(axis) and attribute[:-1] == attributes[0][:-1]
        for attribute, axis in zip(attributes, "XYZ")
    )
