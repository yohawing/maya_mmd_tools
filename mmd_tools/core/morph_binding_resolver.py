"""Pure policy for resolving one PMX vertex morph to Maya blendShapes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Set, Tuple

from mmd_tools.core.maya_name_utils import sanitize_text


_WEIGHT_PLUG_RE = re.compile(r"^(?:(?P<node>.+)\.)?(?:weight|w)\[(?P<index>\d+)\]$")


class MorphBindingResolutionError(ValueError):
    """Raised when observations cannot identify the requested morph safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__("{}: {}".format(code, message))
        self.code = code


@dataclass(frozen=True)
class MorphBindingRequest:
    """Identity of the single PMX morph/controller slot being resolved."""

    raw_pmx_name: str
    global_morph_index: int
    controller_identity: str
    controller_slot: int


@dataclass(frozen=True)
class MorphDestinationObservation:
    """One controller-output destination with its observed Maya node type."""

    node_identity: str
    node_type: str
    plug: str


@dataclass(frozen=True)
class MorphBinding:
    """Immutable canonical identity shared by morph readers and writers."""

    raw_pmx_name: str
    global_morph_index: int
    blend_shape_identity: str
    alias: str
    logical_target_index: int
    weight_plug: str
    controller_identity: str
    controller_slot: int


@dataclass(frozen=True)
class MorphBindingWarning:
    """Stable non-fatal diagnostic returned with a legacy resolution."""

    code: str
    message: str


@dataclass(frozen=True)
class MorphBindingResolution:
    """Resolved multi-mesh bindings and any explicit compatibility warnings."""

    bindings: Tuple[MorphBinding, ...]
    warnings: Tuple[MorphBindingWarning, ...]


def resolve_morph_binding(
    request: MorphBindingRequest,
    destinations: Iterable[MorphDestinationObservation],
    blend_shape_aliases: Mapping[str, Iterable[Tuple[str, str]]],
    raw_name_mappings: Mapping[str, Mapping[object, object]],
) -> MorphBindingResolution:
    """Resolve one requested morph from adapter-owned scene observations.

    Raw-name JSON is authoritative when present and must match both request
    name and global index.  Only legacy blendShapes with no raw-name mapping
    may use a unique sanitized-alias fallback, reported with a stable warning
    code.
    """
    _validate_request(request)
    aliases_by_node = _parse_aliases(blend_shape_aliases)
    raw_by_node = _parse_raw_mappings(raw_name_mappings)
    try:
        observations = tuple(destinations)
    except TypeError:
        _fail("malformed_destinations", "destinations must be iterable")
    if isinstance(destinations, (str, bytes, bytearray)):
        _fail("malformed_destinations", "destinations must not be a string")

    bindings = []
    warnings = []
    seen_nodes: Set[str] = set()
    for observation in observations:
        if not isinstance(observation, MorphDestinationObservation):
            _fail(
                "malformed_destination",
                "each destination must be a MorphDestinationObservation",
            )
        node = _required_text(observation.node_identity, "destination node identity")
        if observation.node_type != "blendShape":
            _fail(
                "wrong_node_type",
                "destination node {!r} has type {!r}, expected 'blendShape'".format(
                    node, observation.node_type
                ),
            )
        if node in seen_nodes:
            _fail(
                "duplicate_blendshape_candidate",
                "requested morph has multiple destinations on blendShape {!r}".format(node),
            )
        alias_by_index = aliases_by_node.get(node)
        if alias_by_index is None:
            _fail("missing_alias_observation", "blendShape {!r} has no alias observations".format(node))
        target_index = _resolve_destination_target(observation.plug, node, alias_by_index)
        canonical_plug = "{}.weight[{}]".format(node, target_index)
        seen_nodes.add(node)

        alias = alias_by_index.get(target_index)
        if alias is None:
            _fail(
                "missing_alias",
                "blendShape {!r}.weight[{}] has no alias".format(node, target_index),
            )
        node_raw_entries = raw_by_node.get(node)
        if node_raw_entries is None:
            _validate_legacy_alias(
                request.raw_pmx_name,
                node,
                alias,
                alias_by_index,
            )
            warnings.append(
                MorphBindingWarning(
                    code="legacy_sanitized_alias_fallback",
                    message=(
                        "{} has no authoritative raw-name entry for weight[{}]; "
                        "resolved unique sanitized alias {!r}"
                    ).format(node, target_index, alias),
                )
            )
        else:
            raw_entry = node_raw_entries.get(target_index)
            if raw_entry is None:
                _fail(
                    "stale_raw_name_mapping",
                    "{}.weight[{}] is missing from the existing raw-name mapping".format(
                        node, target_index
                    ),
                )
            raw_name, global_index = raw_entry
            if raw_name != request.raw_pmx_name or global_index != request.global_morph_index:
                _fail(
                    "stale_raw_name_mapping",
                    (
                        "{}.weight[{}] records name/index {!r}/{} but request is {!r}/{}"
                    ).format(
                        node,
                        target_index,
                        raw_name,
                        global_index,
                        request.raw_pmx_name,
                        request.global_morph_index,
                    ),
                )
        bindings.append(
            MorphBinding(
                raw_pmx_name=request.raw_pmx_name,
                global_morph_index=request.global_morph_index,
                blend_shape_identity=node,
                alias=alias,
                logical_target_index=target_index,
                weight_plug=canonical_plug,
                controller_identity=request.controller_identity,
                controller_slot=request.controller_slot,
            )
        )

    if not bindings:
        _fail("no_binding_candidate", "requested morph has no blendShape destination")
    return MorphBindingResolution(
        bindings=tuple(
            sorted(
                bindings,
                key=lambda item: (item.blend_shape_identity, item.logical_target_index),
            )
        ),
        warnings=tuple(warnings),
    )


def _validate_request(request: MorphBindingRequest) -> None:
    if not isinstance(request, MorphBindingRequest):
        _fail("malformed_request", "request must be a MorphBindingRequest")
    _required_text(request.raw_pmx_name, "raw PMX morph name")
    _non_negative_int(request.global_morph_index, "global morph index")
    _required_text(request.controller_identity, "controller identity")
    _non_negative_int(request.controller_slot, "controller slot")


def _parse_aliases(
    observed: Mapping[str, Iterable[Tuple[str, str]]]
) -> Dict[str, Dict[int, str]]:
    if not isinstance(observed, Mapping):
        _fail("malformed_aliases", "blendShape aliases must be a mapping")
    result: Dict[str, Dict[int, str]] = {}
    for node_value, pairs in observed.items():
        node = _required_text(node_value, "blendShape identity")
        if isinstance(pairs, (str, bytes, bytearray)):
            _fail("malformed_aliases", "alias observations for {!r} must be pairs".format(node))
        try:
            pair_items = tuple(pairs)
        except TypeError:
            _fail("malformed_aliases", "alias observations for {!r} must be iterable".format(node))
        by_index: Dict[int, str] = {}
        seen_aliases: Set[str] = set()
        for pair in pair_items:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                _fail(
                    "malformed_alias_pair",
                    "alias observation for {!r} must be an (alias, plug) pair".format(node),
                )
            alias = _required_text(pair[0], "blendShape alias")
            plug_node, target_index = _parse_weight_plug(pair[1], node, "alias plug")
            if plug_node != node:
                _fail(
                    "cross_node_alias",
                    "alias {!r} for {!r} points to {!r}".format(alias, node, plug_node),
                )
            if target_index in by_index or alias in seen_aliases:
                _fail("alias_ambiguity", "ambiguous alias {!r} on {!r}".format(alias, node))
            by_index[target_index] = alias
            seen_aliases.add(alias)
        result[node] = by_index
    return result


def _parse_raw_mappings(
    observed: Mapping[str, Mapping[object, object]]
) -> Dict[str, Dict[int, Tuple[str, int]]]:
    if not isinstance(observed, Mapping):
        _fail("malformed_raw_mapping", "raw-name JSON mappings must be a mapping")
    result: Dict[str, Dict[int, Tuple[str, int]]] = {}
    for node_value, entries in observed.items():
        node = _required_text(node_value, "blendShape identity")
        if not isinstance(entries, Mapping):
            _fail("malformed_raw_mapping", "raw-name mapping for {!r} must be an object".format(node))
        by_index: Dict[int, Tuple[str, int]] = {}
        for target_value, entry in entries.items():
            target_index = _non_negative_int(target_value, "logical target index")
            if target_index in by_index:
                _fail(
                    "duplicate_raw_entry",
                    "duplicate raw-name entry for {!r}.weight[{}]".format(node, target_index),
                )
            if not isinstance(entry, Mapping) or "name" not in entry or "index" not in entry:
                _fail(
                    "malformed_raw_entry",
                    "raw-name entry for {!r}.weight[{}] requires name and index".format(
                        node, target_index
                    ),
                )
            by_index[target_index] = (
                _required_text(entry.get("name"), "raw PMX morph name"),
                _non_negative_int(entry.get("index"), "global morph index"),
            )
        result[node] = by_index
    return result


def _resolve_destination_target(
    plug_value: object, node: str, aliases: Mapping[int, str]
) -> int:
    plug = _required_text(plug_value, "destination plug")
    match = _WEIGHT_PLUG_RE.fullmatch(plug)
    if match is not None:
        plug_node = match.group("node") or node
        if plug_node != node:
            _fail(
                "cross_node_destination",
                "destination {!r} does not belong to {!r}".format(plug, node),
            )
        return int(match.group("index"))

    prefix = node + "."
    alias = plug[len(prefix) :] if plug.startswith(prefix) else plug
    if not alias or "." in alias or "[" in alias or "]" in alias:
        _fail("malformed_destination", "cannot resolve destination plug {!r}".format(plug))
    matches = [index for index, candidate in aliases.items() if candidate == alias]
    if len(matches) != 1:
        _fail(
            "alias_destination_ambiguity",
            "destination alias {!r} on {!r} matched {} targets".format(alias, node, len(matches)),
        )
    return matches[0]


def _validate_legacy_alias(
    raw_name: str,
    node: str,
    alias: str,
    aliases: Mapping[int, str],
) -> None:
    sanitized = sanitize_text(raw_name)
    matches = [candidate for candidate in aliases.values() if candidate == sanitized]
    if alias != sanitized or len(matches) != 1:
        _fail(
            "legacy_alias_ambiguity",
            "legacy alias {!r} on {!r} is not the unique sanitized form {!r}".format(
                alias, node, sanitized
            ),
        )


def _parse_weight_plug(value: object, default_node: str, label: str) -> Tuple[str, int]:
    text = _required_text(value, label)
    match = _WEIGHT_PLUG_RE.fullmatch(text)
    if match is None:
        _fail("malformed_weight_plug", "malformed {} {!r}".format(label, text))
    return match.group("node") or default_node, int(match.group("index"))


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("malformed_text", "{} must be a non-empty string".format(label))
    return value


def _non_negative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        _fail("malformed_index", "{} must be a non-negative integer".format(label))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _fail("malformed_index", "{} must be a non-negative integer".format(label))
    if parsed < 0 or str(value).strip() != str(parsed):
        _fail("malformed_index", "{} must be a non-negative integer".format(label))
    return parsed


def _fail(code: str, message: str):
    raise MorphBindingResolutionError(code, message)
