"""Collect Maya observations and resolve one vertex-morph binding."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from mmd_tools.core.morph_binding_resolver import (
    MorphBindingRequest,
    MorphBindingResolution,
    MorphDestinationObservation,
    resolve_morph_binding,
)


class MayaMorphBindingQueryError(RuntimeError):
    """Raised when Maya cannot provide well-formed binding observations."""


def resolve_maya_morph_binding(
    adapter: Any,
    request: MorphBindingRequest,
    destination_values: Optional[Iterable[str]] = None,
) -> MorphBindingResolution:
    """Resolve one controller slot from narrowly queried Maya scene state."""
    output_plug = "{}.outputWeight[{}]".format(
        request.controller_identity,
        request.controller_slot,
    )
    destinations = destination_values
    if destinations is None:
        destinations = _call(
            adapter,
            "list_connections",
            output_plug,
            source=False,
            destination=True,
            plugs=True,
        ) or ()
    if isinstance(destinations, (str, bytes, bytearray)):
        raise MayaMorphBindingQueryError(
            "list_connections({!r}) returned a scalar".format(output_plug)
        )

    observations: List[MorphDestinationObservation] = []
    aliases: Dict[str, Tuple[Tuple[str, str], ...]] = {}
    raw_mappings: Dict[str, Mapping[object, object]] = {}
    for destination_value in destinations:
        destination = str(destination_value)
        if "." not in destination:
            raise MayaMorphBindingQueryError(
                "controller destination is not a node plug: {!r}".format(destination)
            )
        raw_node, plug = destination.rsplit(".", 1)
        node = _canonical_identity(adapter, raw_node)
        node_type = _call(adapter, "node_type", node)
        observations.append(
            MorphDestinationObservation(
                node_identity=node,
                node_type=node_type if isinstance(node_type, str) else "",
                plug="{}.{}".format(node, plug),
            )
        )
        if node in aliases:
            continue
        aliases[node] = _alias_pairs(adapter, node)
        if _call(
            adapter,
            "attribute_exists",
            ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
            node,
        ):
            raw_mappings[node] = _raw_mapping(adapter, node)

    return resolve_morph_binding(request, observations, aliases, raw_mappings)


def _canonical_identity(adapter: Any, node: str) -> str:
    if not node:
        raise MayaMorphBindingQueryError("destination node identity is empty")
    if node.startswith("|"):
        return node
    names = _call(adapter, "ls", node, long=True) or ()
    if isinstance(names, (str, bytes, bytearray)):
        raise MayaMorphBindingQueryError("ls({!r}) returned a scalar".format(node))
    names = tuple(names)
    if len(names) == 1 and isinstance(names[0], str) and names[0]:
        return names[0]
    raise MayaMorphBindingQueryError(
        "node {!r} has no unique canonical identity".format(node)
    )


def _alias_pairs(adapter: Any, node: str) -> Tuple[Tuple[str, str], ...]:
    flat = _call(adapter, "alias_attr", node, query=True) or ()
    if isinstance(flat, (str, bytes, bytearray)):
        raise MayaMorphBindingQueryError(
            "alias_attr({!r}) returned a scalar".format(node)
        )
    flat = tuple(flat)
    if len(flat) % 2:
        raise MayaMorphBindingQueryError(
            "alias_attr({!r}) must return alias/plug pairs".format(node)
        )
    return tuple((flat[index], flat[index + 1]) for index in range(0, len(flat), 2))


def _raw_mapping(adapter: Any, node: str) -> Mapping[object, object]:
    path = "{}.{}".format(node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON)
    raw = _call(adapter, "get_attr", path)
    if not isinstance(raw, str):
        raise MayaMorphBindingQueryError("{} must contain JSON text".format(path))
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except MayaMorphBindingQueryError as exc:
        raise MayaMorphBindingQueryError(
            "{} must contain strict JSON: {}".format(path, exc)
        ) from exc
    except (TypeError, ValueError) as exc:
        raise MayaMorphBindingQueryError("{} must contain JSON".format(path)) from exc
    if not isinstance(value, Mapping):
        raise MayaMorphBindingQueryError("{} must contain an object".format(path))
    return value


def _unique_json_object(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    value: Dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise MayaMorphBindingQueryError(
                "duplicate object key {!r}".format(key)
            )
        value[key] = item
    return value


def _call(adapter: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    try:
        return getattr(adapter, method)(*args, **kwargs)
    except MayaMorphBindingQueryError:
        raise
    except AttributeError as exc:
        raise MayaMorphBindingQueryError(
            "injected adapter is missing {}()".format(method)
        ) from exc
    except Exception as exc:
        raise MayaMorphBindingQueryError(
            "adapter {}() failed: {}".format(method, exc)
        ) from exc
