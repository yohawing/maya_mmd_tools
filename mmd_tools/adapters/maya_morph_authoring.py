"""Transactional Maya bindings for immutable PMX morph authoring specs.

This module mutates registry-owned morph network nodes and the fixed-index
``mmdMorphController`` topology.  It deliberately does not open an undo chunk;
the scene metadata coordinator must call it inside the same transaction that
persists the resulting :class:`MmdModelAuthoringSpec`.

Vertex morph offsets remain coupled to imported blendShape targets.  The
blendShape target is the sole value authority; registry nodes retain only the
stable PMX binding metadata needed to locate that target.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import json
import math
import re
from typing import Any

from mmd_tools.adapters.maya_morph_binding_query import (
    MayaMorphBindingQueryError,
    resolve_maya_morph_binding,
)
from mmd_tools.core import model_registry
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MODEL_REGISTRY,
)
from mmd_tools.core import maya_name_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec, MmdMorphSpec
from mmd_tools.core.morph_binding_resolver import (
    MorphBinding,
    MorphBindingRequest,
    MorphBindingResolutionError,
)
from mmd_tools.core.morph_authoring import MorphReindexResult, classify_morph_change
from mmd_tools.core.morph_topology import (
    TOPOLOGY_VERSION,
    compute_group_topology,
    serialize_group_topology,
)
from mmd_tools.converters.morph_converter import pmx_vertex_offset_to_maya_tuple


REGISTRY_CATEGORY_MORPH = "morph"
logger = get_logger(__name__)
_UNSUPPORTED_TYPES = {"flip", "impulse"}
_OFFSET_ATTRS = {
    # Vertex offsets live in the owned blendShape target.  The remaining
    # entries are metadata used by non-Vertex morph evaluators/export.
    "bone": ("mmd_bone_morph_offsets_json",),
    "group": ("mmd_group_morph_offsets_json",),
    "material": ("mmd_material_morph_offsets_json",),
    "uv": (ATTR_MMD_UV_MORPH_OFFSETS_JSON,),
    "additional_uv1": (ATTR_MMD_UV_MORPH_OFFSETS_JSON,),
    "additional_uv2": (ATTR_MMD_UV_MORPH_OFFSETS_JSON,),
    "additional_uv3": (ATTR_MMD_UV_MORPH_OFFSETS_JSON,),
    "additional_uv4": (ATTR_MMD_UV_MORPH_OFFSETS_JSON,),
    "flip": (ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,),
    "impulse": (ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,),
}
_OFFSET_COUNT_ATTRS = {
    "bone": "mmd_bone_morph_offset_count",
    "group": "mmd_group_morph_offset_count",
    "material": "mmd_material_morph_offset_count",
    "uv": "mmd_uv_morph_offset_count",
    "additional_uv1": "mmd_uv_morph_offset_count",
    "additional_uv2": "mmd_uv_morph_offset_count",
    "additional_uv3": "mmd_uv_morph_offset_count",
    "additional_uv4": "mmd_uv_morph_offset_count",
    "flip": "mmd_flip_morph_offset_count",
    "impulse": "mmd_impulse_morph_offset_count",
}


class MayaMorphAuthoringError(RuntimeError):
    """Raised when a morph binding edit cannot preserve Maya runtime parity."""


def apply_morph_value_patch(
    root: str,
    old_morph: MmdMorphSpec,
    new_morph: MmdMorphSpec,
    adapter: Any,
    registry_api: Any = model_registry,
) -> MmdMorphSpec:
    """Write one selected morph's patch-safe values in place.

    This path intentionally never touches the controller input/output arrays,
    registry membership, or another morph binding.  Bone/material runtime
    contribution constants are updated directly when their existing evaluator
    nodes expose a contribution driven by the selected ``binding.weight``.
    """
    if not isinstance(old_morph, MmdMorphSpec) or not isinstance(new_morph, MmdMorphSpec):
        _fail("old_morph and new_morph must be MmdMorphSpec values")
    binding = old_morph.binding_identity
    if not isinstance(binding, str) or not binding or new_morph.binding_identity != binding:
        _fail("morph binding identity cannot change in a value patch")
    if old_morph.index != new_morph.index:
        _fail("morph index cannot change in a value patch")
    route = classify_morph_change(old_morph, new_morph)
    if route == "noop":
        return new_morph
    if route != "value":
        _fail("morph value patch received structural fields")
    root = _require_root(adapter, root)
    try:
        owned = {
            _canonical_node(adapter, str(node))
            for node in registry_api.list_model_registry_members(root, REGISTRY_CATEGORY_MORPH)
        }
    except Exception as exc:
        raise MayaMorphAuthoringError(
            f"selected morph registry ownership read failed for {root!r}: {exc}"
        ) from exc
    canonical = _canonical_node(adapter, binding)
    if canonical not in owned:
        _fail(f"morph binding {canonical!r} is not registry-owned")
    if _call(adapter, "node_type", canonical) != "network":
        _fail(f"morph binding {canonical!r} must be a network node")
    if _read_int(adapter, canonical, "mmd_morph_index") != old_morph.index:
        _fail(f"morph binding {canonical!r} index does not match selected morph")
    if _read_string(adapter, canonical, "mmd_morph_type") != old_morph.morph_type:
        _fail(f"morph binding {canonical!r} type does not match selected morph")

    changed = {
        field
        for field in old_morph.to_mapping()
        if old_morph.to_mapping()[field] != new_morph.to_mapping()[field]
    }
    vertex_bindings: tuple[MorphBinding, ...] = ()
    controller = None
    if old_morph.morph_type == "vertex" and ({"name", "offsets"} & changed):
        controller = _resolve_controller(adapter, root, allow_missing=False)
        if controller is None:
            _fail("selected vertex morph patch requires an existing morph controller")
        vertex_bindings = _resolve_existing_vertex_bindings(
            adapter,
            controller,
            old_morph,
            controller_slot=old_morph.index,
        )
    _write_morph_values(adapter, canonical, old_morph, new_morph, changed)
    if "name" in changed:
        controller = controller or _resolve_controller(adapter, root, allow_missing=False)
        if controller is None:
            _fail("morph name patch requires an existing morph controller")
        _assign_controller_alias(adapter, controller, new_morph.index, new_morph.name)
    if "offsets" in changed or (old_morph.morph_type == "vertex" and "name" in changed):
        _update_selected_runtime_values(
            adapter,
            root,
            canonical,
            old_morph,
            new_morph,
            vertex_bindings=vertex_bindings,
        )
    return new_morph


def apply_morph_reindex(
    root: str,
    index: int,
    new_position: int,
    adapter: Any,
    registry_api: Any = model_registry,
) -> MorphReindexResult:
    """Swap two adjacent morph bindings without a model-wide transaction."""
    root = _require_root(adapter, root)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        _fail("morph index must be a non-negative integer")
    if isinstance(new_position, bool) or not isinstance(new_position, int) or new_position < 0:
        _fail("new_position must be a non-negative integer")
    if abs(index - new_position) != 1:
        _fail("morph reindex narrow path requires an adjacent swap")
    try:
        members = tuple(
            _canonical_node(adapter, str(node))
            for node in registry_api.list_model_registry_members(root, REGISTRY_CATEGORY_MORPH)
        )
    except Exception as exc:
        raise MayaMorphAuthoringError(f"morph registry preflight failed: {exc}") from exc
    if len(set(members)) != len(members):
        _fail("morph registry contains duplicate binding identities")
    records: list[dict[str, Any]] = []
    for binding in members:
        if _call(adapter, "node_type", binding) != "network":
            _fail(f"morph binding {binding!r} must be a network node")
        morph_index = _read_int(adapter, binding, "mmd_morph_index")
        morph_type = _read_string(adapter, binding, "mmd_morph_type")
        morph_name = (
            _read_string(adapter, binding, "mmd_morph_name")
            if morph_type == "vertex"
            else ""
        )
        records.append(
            {
                "binding": binding,
                "index": morph_index,
                "morph_type": morph_type,
                "name": morph_name,
            }
        )
    by_index = {record["index"]: record for record in records}
    if len(by_index) != len(records) or set(by_index) != set(range(len(records))):
        _fail("morph indices must be a contiguous registry-owned range")
    if index not in by_index or new_position not in by_index:
        _fail("morph adjacent swap index is not registry-owned")
    swap = {index: new_position, new_position: index}

    controller = _resolve_controller(adapter, root, allow_missing=False)
    if controller is None:
        _fail("morph reindex requires an existing morph controller")
    controller_state = _capture_controller_slots(adapter, controller, (index, new_position), records)
    topology = _capture_json_attr(adapter, controller, "groupTopology", required=False)
    display_frames = _capture_json_attr(adapter, root, ATTR_MMD_DISPLAY_FRAMES_JSON, required=False)
    morph_payloads = _capture_morph_reindex_payloads(adapter, records)
    remapped_payloads = _remap_morph_reindex_payloads(morph_payloads, swap)
    remapped_topology = (
        _remap_group_topology(topology, swap) if topology is not None else None
    )
    remapped_display = (
        _remap_display_frames_json(display_frames, swap)
        if display_frames is not None
        else None
    )
    vertex_state = _capture_vertex_reindex_state(adapter, records, controller, controller_state)
    runtime_state = _capture_runtime_reindex_state(adapter, records, swap)

    # Every preflight above completes before the first Maya write.
    _apply_controller_swap(adapter, controller, controller_state)
    for record in records:
        old_index = record["index"]
        new_index = swap.get(old_index, old_index)
        if new_index != old_index:
            _set_typed(adapter, record["binding"], "mmd_morph_index", "long", new_index)
    _write_morph_reindex_payloads(adapter, remapped_payloads)
    _apply_vertex_reindex_state(adapter, vertex_state, swap)
    if topology is not None:
        _call(adapter, "set_attr", f"{controller}.groupTopology", lock=False)
        _write_json_attr(adapter, controller, "groupTopology", remapped_topology)
        _call(adapter, "set_attr", f"{controller}.groupTopology", lock=True)
    if display_frames is not None:
        _write_json_attr(adapter, root, ATTR_MMD_DISPLAY_FRAMES_JSON, remapped_display)
    _apply_runtime_reindex_state(adapter, runtime_state, swap)

    bindings = tuple(
        sorted(
            (
                (swap[record["index"]], record["binding"])
                for record in records
                if record["index"] in swap
            ),
            key=lambda item: item[0],
        )
    )
    return MorphReindexResult(
        moved_index=index,
        new_position=new_position,
        swapped_indices=(index, new_position),
        bindings=bindings,
    )


def apply_morph_create(
    root: str,
    morph: MmdMorphSpec,
    adapter: Any,
    registry_api: Any = model_registry,
) -> MmdMorphSpec:
    """Create one empty-offset morph through a narrow Maya transaction.

    Offset authoring is intentionally a follow-up operation.  This keeps
    creation local to the registry, one network binding, and one controller
    slot; no complete model specification or runtime graph is rebuilt.
    """
    root = _require_root(adapter, root)
    if not isinstance(morph, MmdMorphSpec):
        _fail("morph must be an MmdMorphSpec")
    if morph.binding_identity is not None:
        _fail("new morph must not supply a binding identity")
    if morph.offsets:
        _fail("morph creation requires empty offsets; apply offsets separately")
    if morph.morph_type in _UNSUPPORTED_TYPES:
        _fail(f"{morph.morph_type} morph authoring is policy-rejected")
    try:
        members = tuple(
            _canonical_node(adapter, str(node))
            for node in registry_api.list_model_registry_members(root, REGISTRY_CATEGORY_MORPH)
        )
    except Exception as exc:
        raise MayaMorphAuthoringError(f"morph registry preflight failed: {exc}") from exc
    if len(set(members)) != len(members):
        _fail("morph registry contains duplicate binding identities")
    records: list[dict[str, Any]] = []
    for binding in members:
        if _call(adapter, "node_type", binding) != "network":
            _fail(f"morph binding {binding!r} must be a network node")
        records.append(
            {
                "binding": binding,
                "index": _read_int(adapter, binding, "mmd_morph_index"),
                "morph_type": _read_string(adapter, binding, "mmd_morph_type"),
            }
        )
    existing_indices = {record["index"] for record in records}
    if len(existing_indices) != len(records) or existing_indices != set(range(len(records))):
        _fail("morph indices must be a contiguous registry-owned range")
    new_index = len(records)
    candidate = replace(morph, index=new_index)
    controller = _resolve_controller(adapter, root, allow_missing=True)
    if controller is not None:
        _preflight_new_controller_slot(adapter, controller, new_index)
    elif "mmdMorphController" not in tuple(_call(adapter, "all_node_types") or ()):
        _fail("required node type 'mmdMorphController' is unavailable")
    registry = _resolve_registry_for_write(adapter, root)
    vertex_plan = ()
    if candidate.morph_type == "vertex":
        vertex_plan = tuple(
            _new_vertex_target_plans(adapter, root, [candidate])
        )
    node = _canonical_node(
        adapter,
        str(_call(adapter, "create_node", "network", name=f"mmdMorph_{new_index}")),
    )
    _ensure_attr(adapter, node, "weight", "double", default=0.0, keyable=True)
    bound = replace(candidate, binding_identity=node)
    _write_morph(adapter, node, bound)
    registry_api.register_model_members(registry, REGISTRY_CATEGORY_MORPH, [node])
    if controller is None:
        controller = _create_controller(adapter, root)
    input_plug = f"{controller}.inputWeight[{new_index}]"
    _call(adapter, "set_attr", input_plug, 0.0)
    _call(adapter, "set_attr", input_plug, keyable=True)
    _assign_controller_alias(adapter, controller, new_index, bound.name)
    if bound.morph_type != "vertex":
        _call(
            adapter,
            "connect_attr",
            f"{controller}.outputWeight[{new_index}]",
            f"{node}.weight",
            force=True,
        )
    if vertex_plan:
        _apply_new_vertex_targets(adapter, controller, vertex_plan[0])
    return bound


def _resolve_registry_for_write(adapter: Any, root: str) -> str:
    if not _has_attr(adapter, root, ATTR_MMD_MODEL_REGISTRY):
        _fail("morph creation requires an existing model registry")
    registries = tuple(
        _call(
            adapter,
            "list_connections",
            f"{root}.{ATTR_MMD_MODEL_REGISTRY}",
            source=True,
            destination=False,
        )
        or ()
    )
    if len(registries) != 1:
        _fail("model root must have exactly one registry connection")
    return _canonical_node(adapter, str(registries[0]))


def _preflight_new_controller_slot(adapter: Any, controller: str, index: int) -> None:
    input_plug = f"{controller}.inputWeight[{index}]"
    output_plug = f"{controller}.outputWeight[{index}]"
    incoming = tuple(
        _call(adapter, "list_connections", input_plug, source=True, destination=False, plugs=True)
        or ()
    )
    outgoing = tuple(
        _call(adapter, "list_connections", output_plug, source=False, destination=True, plugs=True)
        or ()
    )
    if incoming or outgoing:
        _fail(f"controller slot {index} is already occupied")
    aliases = list(_call(adapter, "alias_attr", controller, query=True) or ())
    if len(aliases) % 2:
        _fail("controller aliases must be alias/plug pairs")
    for alias, plug in zip(aliases[0::2], aliases[1::2]):
        plug_text = str(plug).rsplit(".", 1)[-1]
        if str(alias) == f"morph_{index}" or plug_text == f"inputWeight[{index}]":
            _fail(f"controller slot {index} alias is already occupied")


def _capture_controller_slots(
    adapter: Any,
    controller: str,
    indices: tuple[int, int],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    aliases = list(_call(adapter, "alias_attr", controller, query=True) or ())
    if len(aliases) % 2:
        _fail("controller aliases must be alias/plug pairs")
    alias_by_plug: dict[str, str] = {}
    for alias, plug in zip(aliases[0::2], aliases[1::2]):
        if not isinstance(alias, str) or not isinstance(plug, str):
            _fail("controller aliases must be strings")
        plug_text = str(plug)
        if plug_text.startswith("inputWeight["):
            if plug_text in alias_by_plug:
                _fail(f"controller input alias is ambiguous: {plug_text!r}")
            if str(alias) in alias_by_plug.values():
                _fail(f"controller input alias name is ambiguous: {alias!r}")
            alias_by_plug[plug_text] = str(alias)
    state: dict[int, dict[str, Any]] = {}
    records_by_index = {int(record["index"]): record for record in records}
    for index in indices:
        input_plug = f"{controller}.inputWeight[{index}]"
        output_plug = f"{controller}.outputWeight[{index}]"
        sources = tuple(
            str(item)
            for item in (_call(adapter, "list_connections", input_plug, source=True, destination=False, plugs=True) or ())
        )
        if len(sources) > 1:
            _fail(f"{input_plug} has ambiguous incoming connections")
        destinations = tuple(
            str(item)
            for item in (_call(adapter, "list_connections", output_plug, source=False, destination=True, plugs=True) or ())
        )
        record = records_by_index[index]
        binding = str(record["binding"])
        if record["morph_type"] != "vertex" and f"{binding}.weight" not in destinations:
            _fail(f"controller output {index} is not connected to {binding}.weight")
        if record["morph_type"] == "vertex" and not destinations:
            _fail(f"controller output {index} has no blendShape destination")
        value = _call(adapter, "get_attr", input_plug)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            _fail(f"{input_plug} has an invalid numeric value")
        value = float(value)
        alias = alias_by_plug.get(f"inputWeight[{index}]")
        state[index] = {
            "source": sources[0] if sources else None,
            "destinations": destinations,
            "value": value,
            "alias": alias,
        }
    return state


def _apply_controller_swap(adapter: Any, controller: str, state: Mapping[int, Mapping[str, Any]]) -> None:
    indices = tuple(sorted(state))
    if len(indices) != 2:
        _fail("controller swap requires exactly two preflighted slots")
    first, second = indices
    for index in indices:
        input_plug = f"{controller}.inputWeight[{index}]"
        source = state[index]["source"]
        if source:
            _call(adapter, "disconnect_attr", source, input_plug)
        for destination in state[index]["destinations"]:
            _call(adapter, "disconnect_attr", f"{controller}.outputWeight[{index}]", destination)
        alias = state[index].get("alias")
        if alias:
            _call(adapter, "alias_attr", input_plug, remove=True)
    for target, source_index in ((first, second), (second, first)):
        source = state[source_index]["source"]
        input_plug = f"{controller}.inputWeight[{target}]"
        if source:
            _call(adapter, "connect_attr", source, input_plug, force=True)
        else:
            _call(adapter, "set_attr", input_plug, state[source_index]["value"])
        alias = state[source_index].get("alias")
        if alias:
            _call(adapter, "alias_attr", alias, input_plug)
        output_plug = f"{controller}.outputWeight[{target}]"
        for destination in state[source_index]["destinations"]:
            _call(adapter, "connect_attr", output_plug, destination, force=True)


def _capture_morph_reindex_payloads(adapter: Any, records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    for record in records:
        morph_type = str(record["morph_type"])
        if morph_type not in {"group", "flip"}:
            continue
        attr = _OFFSET_ATTRS[morph_type][0]
        raw = _call(adapter, "get_attr", f"{record['binding']}.{attr}")
        if not isinstance(raw, str):
            _fail(f"{record['binding']}.{attr} must contain JSON text")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            _fail(f"{record['binding']}.{attr} contains invalid JSON: {exc}")
        if not isinstance(parsed, list):
            _fail(f"{record['binding']}.{attr} must contain a JSON list")
        payloads.append({"binding": record["binding"], "morph_type": morph_type, "attr": attr, "value": parsed})
    return tuple(payloads)


def _remap_morph_reindex_payloads(
    payloads: Sequence[Mapping[str, Any]],
    swap: Mapping[int, int],
) -> tuple[dict[str, Any], ...]:
    """Validate and remap Group/Flip payloads before any Maya write."""
    result: list[dict[str, Any]] = []
    for payload in payloads:
        value = json.loads(json.dumps(payload["value"]))
        changed = False
        for offset in value:
            if not isinstance(offset, Mapping) or "morph_index" not in offset:
                _fail(f"{payload['binding']} has an invalid group/flip offset")
            current = _strict_index(offset["morph_index"], f"{payload['binding']} morph_index")
            replacement = swap.get(current, current)
            if replacement != current:
                offset["morph_index"] = replacement
                changed = True
        result.append({**dict(payload), "value": value, "changed": changed})
    return tuple(result)


def _write_morph_reindex_payloads(
    adapter: Any, payloads: Sequence[Mapping[str, Any]]
) -> None:
    for payload in payloads:
        if payload.get("changed"):
            _write_json_attr(adapter, str(payload["binding"]), str(payload["attr"]), payload["value"])


def _capture_json_attr(adapter: Any, node: str, attr: str, *, required: bool) -> Any:
    if not _has_attr(adapter, node, attr):
        if required:
            _fail(f"{node}.{attr} is required")
        return None
    raw = _call(adapter, "get_attr", f"{node}.{attr}")
    if not isinstance(raw, str):
        _fail(f"{node}.{attr} must contain JSON text")
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        _fail(f"{node}.{attr} contains invalid JSON: {exc}")


def _write_json_attr(adapter: Any, node: str, attr: str, value: Any) -> None:
    _call(
        adapter,
        "set_attr",
        f"{node}.{attr}",
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        type="string",
    )


def _remap_group_topology(value: Any, swap: Mapping[int, int]) -> Any:
    if not isinstance(value, Mapping):
        _fail("controller groupTopology must contain a JSON object")
    result: dict[str, Any] = {}
    for target, sources in value.items():
        if isinstance(target, str) and re.fullmatch(r"(?:0|[1-9]\d*)", target):
            target_index = int(target)
        elif isinstance(target, int) and not isinstance(target, bool):
            target_index = _strict_index(target, "groupTopology target")
        else:
            _fail("groupTopology target must be a non-negative integer key")
        if not isinstance(sources, list):
            _fail("controller groupTopology entries must be lists")
        remapped = []
        for source in sources:
            if not isinstance(source, list) or len(source) != 2:
                _fail("controller groupTopology source entries must be [index, rate]")
            source_index = _strict_index(source[0], "groupTopology source")
            remapped.append([swap.get(source_index, source_index), source[1]])
        result[str(swap.get(target_index, target_index))] = remapped
    return result


def _remap_display_frames_json(value: Any, swap: Mapping[int, int]) -> Any:
    if not isinstance(value, list):
        _fail("display frame metadata must contain a JSON list")
    result = json.loads(json.dumps(value))
    for frame in result:
        if not isinstance(frame, Mapping):
            _fail("display frame entries must be mappings")
        elements = frame.get("elements", [])
        if not isinstance(elements, list):
            _fail("display frame elements must be a list")
        for element in elements:
            if not isinstance(element, Mapping):
                _fail("display frame elements must be mappings")
            element_type = _strict_index(element.get("type"), "display frame element type")
            if element_type not in {0, 1}:
                _fail("display frame element type must be 0 or 1")
            element_index = _strict_index(element.get("index"), "display frame element index")
            if element_type == 1:
                element["index"] = swap.get(element_index, element_index)
    return result


def _strict_index(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{field} must be a non-negative integer")
    return int(value)


def _capture_vertex_reindex_state(
    adapter: Any,
    records: Sequence[Mapping[str, Any]],
    controller: str,
    controller_state: Mapping[int, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    captured: list[dict[str, Any]] = []
    for record in records:
        if record["morph_type"] != "vertex":
            continue
        index = int(record["index"])
        if index not in controller_state:
            continue
        bindings = _resolve_existing_vertex_bindings(
            adapter,
            controller,
            MmdMorphSpec(
                name=str(record["name"]),
                index=index,
                morph_type="vertex",
                binding_identity=str(record["binding"]),
            ),
            controller_slot=index,
            destination_values=controller_state[index]["destinations"],
        )
        for binding in bindings:
            node = binding.blend_shape_identity
            target_index = binding.logical_target_index
            mapping = _read_vertex_name_mapping(adapter, node)
            captured.append({"node": node, "target_index": target_index, "mapping": mapping})
    return tuple(captured)


def _resolve_existing_vertex_bindings(
    adapter: Any,
    controller: str,
    morph: MmdMorphSpec,
    *,
    controller_slot: int,
    destination_values: Any = None,
) -> tuple[MorphBinding, ...]:
    """Resolve one existing vertex morph before any rename or mapping write."""
    request = MorphBindingRequest(
        raw_pmx_name=morph.name,
        global_morph_index=morph.index,
        controller_identity=controller,
        controller_slot=controller_slot,
    )
    try:
        resolution = resolve_maya_morph_binding(
            adapter,
            request,
            destination_values=destination_values,
        )
    except (MayaMorphBindingQueryError, MorphBindingResolutionError) as exc:
        raise MayaMorphAuthoringError(
            f"vertex morph {morph.index} target binding resolution failed: {exc}"
        ) from exc
    for warning in resolution.warnings:
        logger.warning("[%s] %s", warning.code, warning.message)
    return resolution.bindings


def _apply_vertex_reindex_state(
    adapter: Any,
    state: Sequence[Mapping[str, Any]],
    swap: Mapping[int, int],
) -> None:
    mappings: dict[str, dict[str, Any]] = {}
    for item in state:
        node = str(item["node"])
        mapping = mappings.setdefault(node, dict(item["mapping"]))
        key = str(int(item["target_index"]))
        entry = dict(mapping[key])
        current_index = entry.get("index")
        if isinstance(current_index, bool) or not isinstance(current_index, int):
            _fail(f"vertex target {node}.weight[{key}] index is not an integer")
        entry["index"] = swap.get(current_index, current_index)
        mapping[key] = entry
    for node, mapping in mappings.items():
        _write_vertex_name_mapping(adapter, node, mapping)


def _capture_runtime_reindex_state(
    adapter: Any,
    records: Sequence[Mapping[str, Any]],
    swap: Mapping[int, int],
) -> tuple[dict[str, Any], ...]:
    binding_by_node = {str(record["binding"]): int(record["index"]) for record in records}
    expected_counts: dict[str, tuple[str, int]] = {}
    for record in records:
        if record["morph_type"] not in {"bone", "material"}:
            continue
        morph_type = str(record["morph_type"])
        attr = _OFFSET_ATTRS[morph_type][0]
        raw = _call(adapter, "get_attr", f"{record['binding']}.{attr}")
        if not isinstance(raw, str):
            _fail(f"{record['binding']}.{attr} must contain JSON text")
        try:
            offsets = json.loads(raw)
        except (TypeError, ValueError) as exc:
            _fail(f"{record['binding']}.{attr} contains invalid JSON: {exc}")
        if not isinstance(offsets, list):
            _fail(f"{record['binding']}.{attr} must contain a JSON list")
        expected_counts[str(record["binding"])] = (morph_type, len(offsets))
    observed_counts: dict[str, int] = {}
    captured: list[dict[str, Any]] = []
    available_runtime_types: set[str] = set()
    for node_type in ("mmdBoneMorphAccum", "mmdMaterialMorphEval"):
        nodes = _runtime_nodes(adapter, node_type)
        if nodes:
            available_runtime_types.add(node_type)
        for node in nodes:
            indices = _call(adapter, "get_attr", f"{node}.contribution", multiIndices=True) or ()
            for raw_index in indices:
                if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
                    _fail(f"{node}.contribution multi-index must be a non-negative integer")
                slot = raw_index
                sources = tuple(
                    str(item)
                    for item in (
                        _call(
                            adapter,
                            "list_connections",
                            f"{node}.contribution[{slot}].weight",
                            source=True,
                            destination=False,
                            plugs=True,
                        )
                        or ()
                    )
                )
                if len(sources) > 1:
                    _fail(f"{node}.contribution[{slot}].weight has ambiguous sources")
                if not sources:
                    continue
                source_node = sources[0].split(".", 1)[0]
                if source_node not in binding_by_node:
                    continue
                old_index = binding_by_node[source_node]
                current_order = _call(
                    adapter,
                    "get_attr",
                    f"{node}.contribution[{slot}].morphOrder",
                )
                if isinstance(current_order, bool) or not isinstance(current_order, int):
                    _fail(f"{node}.contribution[{slot}].morphOrder must be an integer")
                if current_order != old_index:
                    _fail(
                        f"{node}.contribution[{slot}].morphOrder mismatch: "
                        f"expected {old_index}, got {current_order}"
                    )
                observed_counts[source_node] = observed_counts.get(source_node, 0) + 1
                captured.append(
                    {
                        "node": node,
                        "slot": slot,
                        "old_index": old_index,
                        "new_index": swap.get(old_index, old_index),
                    }
                )
    for binding, (morph_type, expected) in expected_counts.items():
        observed = observed_counts.get(binding, 0)
        runtime_node_type = (
            "mmdBoneMorphAccum" if morph_type == "bone" else "mmdMaterialMorphEval"
        )
        if expected and runtime_node_type in available_runtime_types and observed != expected:
            _fail(
                f"{morph_type} morph runtime contribution count mismatch for {binding!r}: "
                f"expected {expected}, got {observed}"
            )
    return tuple(captured)


def _apply_runtime_reindex_state(
    adapter: Any,
    state: Sequence[Mapping[str, Any]],
    swap: Mapping[int, int],
) -> None:
    del swap
    for item in state:
        if int(item["old_index"]) == int(item["new_index"]):
            continue
        _call(
            adapter,
            "set_attr",
            f"{item['node']}.contribution[{int(item['slot'])}].morphOrder",
            int(item["new_index"]),
        )


def maya_runtime_rebuilders() -> dict[str, Any]:
    """Return production rebuild callbacks for fixed bone/material preview DGs."""
    from mmd_tools.converters.bone_morph_runtime import build_bone_morph_graph
    from mmd_tools.converters.material_morph_runtime import build_material_morph_graph

    return {
        "bone": build_bone_morph_graph,
        "material": build_material_morph_graph,
    }


def apply_morph_spec_change(
    root: str,
    old_spec: MmdModelAuthoringSpec,
    new_spec: MmdModelAuthoringSpec,
    adapter: Any,
    registry_api: Any = model_registry,
    runtime_rebuilders: Mapping[str, Any] | None = None,
) -> MmdModelAuthoringSpec:
    """Apply one precomputed semantic morph change to Maya bindings.

    Existing morphs are matched by ``binding_identity`` rather than their PMX
    index.  New morphs must have no binding; returned specs contain the newly
    created canonical network-node identities.  The function validates the
    complete scene/controller plan before performing any writes.
    """
    root = _require_root(adapter, root)
    _require_model_spec(old_spec, "old_spec")
    _require_model_spec(new_spec, "new_spec")
    _validate_non_morph_sections(old_spec, new_spec)
    _reject_policy_edits(old_spec, new_spec)

    registry = registry_api.ensure_model_registry(root)
    owned = {_canonical_node(adapter, str(node)) for node in registry_api.list_model_registry_members(root, REGISTRY_CATEGORY_MORPH)}
    old_by_binding = _validate_old_bindings(adapter, old_spec, owned)
    old_bindings = set(old_by_binding)
    new_existing = [
        replace(morph, binding_identity=_canonical_node(adapter, str(morph.binding_identity)))
        for morph in new_spec.morphs
        if morph.binding_identity is not None
    ]
    if len({morph.binding_identity for morph in new_existing}) != len(new_existing):
        _fail("new_spec contains duplicate morph binding identities")
    unknown = {morph.binding_identity for morph in new_existing} - old_bindings
    if unknown:
        _fail(f"new_spec contains unknown morph bindings: {sorted(unknown)!r}")

    new_by_old_binding = {morph.binding_identity: morph for morph in new_existing}
    deleted = [old_by_binding[binding] for binding in old_bindings - set(new_by_old_binding)]
    created = [morph for morph in new_spec.morphs if morph.binding_identity is None]
    _validate_vertex_oracle(old_by_binding, new_by_old_binding, deleted, created)
    rebuild_types = _runtime_rebuild_types(old_by_binding, new_by_old_binding, deleted, created)
    rebuilders = _validate_runtime_rebuilders(rebuild_types, runtime_rebuilders)

    controller = _resolve_controller(adapter, root, allow_missing=not old_spec.morphs)
    controller_plan = (
        _controller_plan(adapter, controller, old_spec, new_spec, old_by_binding)
        if controller is not None
        else {"inputs": {}, "outputs": {}, "new_indices": {}, "old_indices": {}}
    )
    vertex_target_plan = _vertex_target_plan(
        adapter,
        root,
        old_by_binding,
        new_by_old_binding,
        created,
        controller_plan,
    )

    created_nodes: dict[int, str] = {}
    for morph in created:
        node = _canonical_node(adapter, str(_call(adapter, "create_node", "network", name=f"mmdMorph_{morph.index}")))
        _ensure_attr(adapter, node, "weight", "double", default=0.0, keyable=True)
        _write_morph(adapter, node, morph)
        created_nodes[morph.index] = node
    if created_nodes:
        registry_api.register_model_members(registry, REGISTRY_CATEGORY_MORPH, list(created_nodes.values()))

    if controller is None:
        controller = _create_controller(adapter, root)

    canonical_existing = {
        morph.index: morph for morph in new_existing
    }
    bound_morphs = tuple(
        replace(morph, binding_identity=created_nodes[morph.index])
        if morph.binding_identity is None
        else canonical_existing[morph.index]
        for morph in new_spec.morphs
    )
    bound_spec = replace(new_spec, morphs=bound_morphs)
    bound_by_identity = {morph.binding_identity: morph for morph in bound_morphs}

    for binding, morph in bound_by_identity.items():
        if binding is None or binding in created_nodes.values():
            continue
        _write_morph(adapter, binding, morph)

    _apply_controller_plan(adapter, controller, controller_plan, bound_spec, created_nodes)
    _apply_vertex_target_plan(adapter, controller, vertex_target_plan)

    deleted_nodes = [morph.binding_identity for morph in deleted if morph.binding_identity is not None]
    if deleted_nodes:
        registry_api.unregister_model_members(registry, REGISTRY_CATEGORY_MORPH, deleted_nodes)
        for node in deleted_nodes:
            _call(adapter, "delete", node)
    for morph_type in sorted(rebuild_types):
        try:
            result = rebuilders[morph_type](root)
        except Exception as exc:
            raise MayaMorphAuthoringError(
                f"{morph_type} live-preview graph rebuild failed: {exc}"
            ) from exc
        if isinstance(result, Mapping) and result.get("success") is False:
            _fail(f"{morph_type} live-preview graph rebuild reported failure: {result!r}")
    return bound_spec


def _validate_non_morph_sections(old: MmdModelAuthoringSpec, new: MmdModelAuthoringSpec) -> None:
    old_mapping = old.to_mapping()
    new_mapping = new.to_mapping()
    old_mapping.pop("morphs")
    new_mapping.pop("morphs")
    if old_mapping != new_mapping:
        _fail("apply_morph_spec_change accepts morph-only semantic changes")


def _reject_policy_edits(old: MmdModelAuthoringSpec, new: MmdModelAuthoringSpec) -> None:
    old_by_binding = {morph.binding_identity: morph for morph in old.morphs if morph.binding_identity}
    for morph in new.morphs:
        if morph.morph_type not in _UNSUPPORTED_TYPES:
            continue
        prior = old_by_binding.get(morph.binding_identity)
        if prior is None or prior.to_mapping() != morph.to_mapping():
            _fail(f"{morph.morph_type} morph authoring is policy-rejected")


def _validate_old_bindings(
    adapter: Any,
    spec: MmdModelAuthoringSpec,
    owned: set[str],
) -> dict[str, MmdMorphSpec]:
    result: dict[str, MmdMorphSpec] = {}
    for morph in spec.morphs:
        if not morph.binding_identity:
            _fail(f"old morph {morph.index} has no binding identity")
        binding = _canonical_node(adapter, morph.binding_identity)
        if binding not in owned:
            _fail(f"morph binding {binding!r} is not registry-owned")
        if binding in result:
            _fail(f"duplicate old morph binding: {binding!r}")
        if _read_int(adapter, binding, "mmd_morph_index") != morph.index:
            _fail(f"scene morph index does not match old_spec: {binding!r}")
        if _read_string(adapter, binding, "mmd_morph_type") != morph.morph_type:
            _fail(f"scene morph type does not match old_spec: {binding!r}")
        result[binding] = morph
    if set(result) != owned:
        _fail("registry morph membership does not exactly match old_spec")
    return result


def _validate_vertex_oracle(
    old_by_binding: Mapping[str, MmdMorphSpec],
    new_by_binding: Mapping[str, MmdMorphSpec],
    deleted: list[MmdMorphSpec],
    created: list[MmdMorphSpec],
) -> None:
    del deleted, created
    for binding, old in old_by_binding.items():
        if old.morph_type != "vertex":
            continue
        new = new_by_binding.get(binding)
        if new is not None and new.morph_type != "vertex":
            _fail("vertex morph type edits require explicit target conversion")


def _vertex_target_plan(
    adapter: Any,
    root: str,
    old_by_binding: Mapping[str, MmdMorphSpec],
    new_by_binding: Mapping[str, MmdMorphSpec],
    created: list[MmdMorphSpec],
    controller_plan: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Preflight exact blendShape targets for changed existing vertex morphs."""
    plans: list[dict[str, Any]] = []
    for binding, old in old_by_binding.items():
        if old.morph_type != "vertex":
            continue
        new = new_by_binding.get(binding)
        deleted = new is None
        name_changed = not deleted and new.name != old.name
        offsets_changed = not deleted and new.offsets != old.offsets
        index_changed = not deleted and new.index != old.index
        if not deleted and not name_changed and not offsets_changed and not index_changed:
            continue
        targets: list[tuple[str, int, str]] = []
        controller = controller_plan.get("controller")
        if not isinstance(controller, str) or not controller:
            _fail(f"vertex morph {old.index} has no controller identity")
        for resolved in _resolve_existing_vertex_bindings(
            adapter,
            controller,
            old,
            controller_slot=old.index,
            destination_values=controller_plan["outputs"].get(binding, ()),
        ):
            targets.append(
                (
                    resolved.blend_shape_identity,
                    resolved.logical_target_index,
                    resolved.alias,
                )
            )

        geometry_plans: list[dict[str, Any]] = []
        covered: set[int] = set()
        for node, target_index, _alias in targets:
            geometries = tuple(_call(adapter, "blend_shape", node, query=True, geometry=True) or ())
            geometry_indices = tuple(
                _call(adapter, "blend_shape", node, query=True, geometryIndices=True) or ()
            )
            if not geometries or len(geometries) != len(geometry_indices):
                _fail(f"blendShape {node!r} has ambiguous geometry indices")
            for geometry, geometry_index in zip(geometries, geometry_indices):
                geometry = _canonical_node(adapter, str(geometry))
                source_to_local = _source_vertex_map(adapter, geometry) if offsets_changed else {}
                components = []
                points = []
                if offsets_changed:
                    for offset in new.offsets:
                        source_index = int(offset["vertex_index"])
                        local_index = source_to_local.get(source_index)
                        if local_index is None:
                            continue
                        covered.add(source_index)
                        components.append(f"vtx[{local_index}]")
                        points.append((*pmx_vertex_offset_to_maya_tuple(offset["position_offset"]), 1.0))
                group = f"{node}.inputTarget[{int(geometry_index)}].inputTargetGroup[{target_index}]"
                item_indices = tuple(
                    _call(adapter, "get_attr", f"{group}.inputTargetItem", multiIndices=True) or ()
                )
                if 6000 not in {int(index) for index in item_indices}:
                    _fail(f"vertex target {group!r} has no full-weight inputTargetItem[6000]")
                geometry_plans.append(
                    {
                        "item": f"{group}.inputTargetItem[6000]",
                        "group": group,
                        "components": tuple(components),
                        "points": tuple(points),
                    }
                )
        expected = {int(offset["vertex_index"]) for offset in new.offsets} if offsets_changed else set()
        if offsets_changed and covered != expected:
            _fail(f"vertex offsets reference unmapped source indices: {sorted(expected - covered)!r}")
        plans.append(
            {
                "old": old,
                "new": new,
                "operation": "delete" if deleted else "update",
                "targets": tuple(targets),
                "geometries": tuple(geometry_plans) if offsets_changed or deleted else (),
            }
        )
    plans.extend(
        _new_vertex_target_plans(
            adapter,
            root,
            [morph for morph in created if morph.morph_type == "vertex"],
        )
    )
    return tuple(plans)


def _new_vertex_target_plans(
    adapter: Any,
    root: str,
    created: list[MmdMorphSpec],
) -> list[dict[str, Any]]:
    if not created:
        return []
    shapes = tuple(
        shape
        for shape in (
            _canonical_node(adapter, str(candidate))
            for candidate in (
                _call(adapter, "list_relatives", root, allDescendents=True, type="mesh", fullPath=True) or ()
            )
        )
        if not bool(_call(adapter, "get_attr", f"{shape}.intermediateObject"))
    )
    if not shapes:
        _fail("vertex target creation requires at least one owned mesh shape")
    plans: list[dict[str, Any]] = []
    for morph in created:
        targets = []
        covered: set[int] = set()
        seen_blend_shapes: set[str] = set()
        for shape in shapes:
            history = tuple(_call(adapter, "list_history", shape) or ())
            blend_shapes = tuple(
                _canonical_node(adapter, str(node))
                for node in (_call(adapter, "ls", history, type="blendShape") or ())
            )
            if len(blend_shapes) > 1:
                _fail(f"mesh {shape!r} has multiple blendShape nodes")
            blend_shape = blend_shapes[0] if blend_shapes else None
            used_indices: set[int] = set()
            mapping: dict[str, Any] = {}
            geometry_index: int | None = None
            if blend_shape is not None:
                if blend_shape in seen_blend_shapes:
                    _fail(f"blendShape {blend_shape!r} owns multiple renderable mesh shapes")
                seen_blend_shapes.add(blend_shape)
                used_indices = {
                    int(index)
                    for index in (
                        _call(adapter, "get_attr", f"{blend_shape}.weight", multiIndices=True) or ()
                    )
                }
                if _has_attr(adapter, blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
                    mapping = _read_vertex_name_mapping(adapter, blend_shape)
                if any(
                    isinstance(entry, Mapping) and entry.get("index") == morph.index
                    for entry in mapping.values()
                ):
                    _fail(f"blendShape {blend_shape!r} already maps global morph index {morph.index}")
                geometries = tuple(_call(adapter, "blend_shape", blend_shape, query=True, geometry=True) or ())
                geometry_indices = tuple(
                    _call(adapter, "blend_shape", blend_shape, query=True, geometryIndices=True) or ()
                )
                matches = [
                    int(index)
                    for geometry, index in zip(geometries, geometry_indices)
                    if _canonical_node(adapter, str(geometry)) == shape
                ]
                if len(matches) != 1:
                    _fail(f"blendShape {blend_shape!r} does not uniquely own mesh {shape!r}")
                geometry_index = matches[0]
            target_index = morph.index
            while target_index in used_indices:
                target_index += 1
            source_to_local = _source_vertex_map(adapter, shape)
            components = []
            points = []
            for offset in morph.offsets:
                source_index = int(offset["vertex_index"])
                local_index = source_to_local.get(source_index)
                if local_index is None:
                    continue
                covered.add(source_index)
                components.append(f"vtx[{local_index}]")
                points.append((*pmx_vertex_offset_to_maya_tuple(offset["position_offset"]), 1.0))
            targets.append(
                {
                    "blend_shape": blend_shape,
                    "shape": shape,
                    "geometry_index": geometry_index,
                    "target_index": target_index,
                    "mapping": mapping,
                    "components": tuple(components),
                    "points": tuple(points),
                }
            )
        expected = {int(offset["vertex_index"]) for offset in morph.offsets}
        if covered != expected:
            _fail(f"vertex offsets reference unmapped source indices: {sorted(expected - covered)!r}")
        plans.append({"operation": "create", "new": morph, "targets": tuple(targets), "geometries": ()})
    return plans


def _read_vertex_name_mapping(adapter: Any, blend_shape: str) -> dict[str, Any]:
    if not _has_attr(adapter, blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
        _fail(f"blendShape {blend_shape!r} has no vertex morph name mapping")
    raw = _call(adapter, "get_attr", f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}")
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise MayaMorphAuthoringError(f"blendShape {blend_shape!r} has invalid morph name JSON") from exc
    if not isinstance(parsed, dict):
        _fail(f"blendShape {blend_shape!r} morph name mapping must be an object")
    return parsed


def _source_vertex_map(adapter: Any, geometry: str) -> dict[int, int]:
    owner = geometry
    if not _has_attr(adapter, owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
        parents = tuple(_call(adapter, "list_relatives", geometry, parent=True, fullPath=True) or ())
        if len(parents) > 1:
            _fail(f"geometry {geometry!r} has ambiguous parents")
        if parents:
            owner = _canonical_node(adapter, str(parents[0]))
    vertex_count = int(_call(adapter, "poly_evaluate", geometry, vertex=True))
    if vertex_count < 0:
        _fail(f"geometry {geometry!r} returned an invalid vertex count")
    if not _has_attr(adapter, owner, ATTR_MMD_SOURCE_VERTEX_INDICES):
        return {index: index for index in range(vertex_count)}
    raw = _call(adapter, "get_attr", f"{owner}.{ATTR_MMD_SOURCE_VERTEX_INDICES}")
    if isinstance(raw, tuple) and len(raw) == 1 and isinstance(raw[0], (list, tuple)):
        raw = raw[0]
    if not isinstance(raw, (list, tuple)) or len(raw) != vertex_count:
        _fail(f"geometry {geometry!r} has invalid source vertex mapping")
    result: dict[int, int] = {}
    for local_index, source_index in enumerate(raw):
        if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
            _fail(f"geometry {geometry!r} has invalid source vertex index")
        if source_index in result:
            _fail(f"geometry {geometry!r} maps source vertex {source_index} more than once")
        result[source_index] = local_index
    return result


def _apply_vertex_target_plan(
    adapter: Any,
    controller: str,
    plans: tuple[dict[str, Any], ...],
) -> None:
    for plan in plans:
        if plan["operation"] == "create":
            _apply_new_vertex_targets(adapter, controller, plan)
            continue
        old = plan["old"]
        new = plan["new"]
        for node, target_index, alias in plan["targets"]:
            plug = f"{node}.weight[{target_index}]"
            if plan["operation"] == "delete":
                # Keep the blendShape target payload in place.  Maya 2024 can
                # undo the controller disconnect and metadata edits, but it
                # does not reliably restore inputTargetItem[6000] after a
                # removeMultiInstance call.  Logical deletion (disconnect,
                # unmap, and zero the retained weight) preserves the target
                # payload so Undo/Redo can restore the exact vertex morph.
                _call(adapter, "alias_attr", plug, remove=True)
                mapping = _read_vertex_name_mapping(adapter, node)
                mapping.pop(str(target_index), None)
                _write_vertex_name_mapping(adapter, node, mapping)
                _call(adapter, "set_attr", plug, 0.0)
                continue
            if new.name != old.name:
                flat = list(_call(adapter, "alias_attr", node, query=True) or ())
                used = {str(value) for value in flat[0::2] if str(value) != alias}
                replacement = maya_name_utils.sanitize_unique_name(
                    new.name,
                    used,
                    fallback=f"morph_{new.index}",
                )
                _call(adapter, "alias_attr", plug, remove=True)
                _call(adapter, "alias_attr", replacement, plug)
            if new.name != old.name or new.index != old.index:
                mapping = _read_vertex_name_mapping(adapter, node)
                mapping[str(target_index)] = {"name": new.name, "index": new.index}
                _write_vertex_name_mapping(adapter, node, mapping)
        for geometry in plan["geometries"]:
            _write_vertex_target_deltas(
                adapter,
                geometry["item"],
                geometry["components"],
                geometry["points"],
            )


def _write_vertex_target_deltas(
    adapter: Any,
    item: str,
    components: Sequence[str],
    points: Sequence[tuple[float, float, float, float]],
) -> None:
    """Write blendShape deltas without creating Maya's malformed empty arrays."""
    if len(components) != len(points):
        _fail(f"vertex target {item!r} points/components lengths differ")
    component_values = (len(components), *components) if components else ()
    point_values = (len(points), *points) if points else ()
    _call(
        adapter,
        "set_attr",
        f"{item}.inputComponentsTarget",
        *component_values,
        type="componentList",
    )
    _call(
        adapter,
        "set_attr",
        f"{item}.inputPointsTarget",
        *point_values,
        type="pointArray",
    )


def _initialize_empty_vertex_target(
    adapter: Any,
    blend_shape: str,
    shape: str,
    geometry_index: int,
    target_index: int,
) -> None:
    """Register an editable empty target through Maya's blendShape command."""
    parents = tuple(_call(adapter, "list_relatives", shape, parent=True, fullPath=True) or ())
    if len(parents) != 1:
        _fail(f"vertex target shape {shape!r} has no unique parent transform")
    base = _canonical_node(adapter, str(parents[0]))
    _call(
        adapter,
        "blend_shape",
        blend_shape,
        edit=True,
        target=(base, target_index, base, 1.0),
    )
    item = (
        f"{blend_shape}.inputTarget[{geometry_index}].inputTargetGroup[{target_index}]"
        ".inputTargetItem[6000]"
    )
    geometry_plug = f"{item}.inputGeomTarget"
    sources = tuple(
        _call(
            adapter,
            "list_connections",
            geometry_plug,
            source=True,
            destination=False,
            plugs=True,
        )
        or ()
    )
    if len(sources) != 1:
        _fail(f"empty vertex target {item!r} has no unique temporary geometry source")
    _call(adapter, "disconnect_attr", str(sources[0]), geometry_plug)
    plug = f"{blend_shape}.weight[{target_index}]"
    if _call(adapter, "alias_attr", plug, query=True):
        _call(adapter, "alias_attr", plug, remove=True)


def _apply_new_vertex_targets(adapter: Any, controller: str, plan: Mapping[str, Any]) -> None:
    morph = plan["new"]
    for target in plan["targets"]:
        blend_shape = target["blend_shape"]
        geometry_index = target["geometry_index"]
        if blend_shape is None:
            result = tuple(
                _call(
                    adapter,
                    "blend_shape",
                    target["shape"],
                    name=f"mmdVertexMorph_{morph.index}_blendShape",
                )
                or ()
            )
            if len(result) != 1:
                _fail(f"could not create one blendShape for {target['shape']!r}")
            blend_shape = _canonical_node(adapter, str(result[0]))
            geometry_indices = tuple(
                _call(adapter, "blend_shape", blend_shape, query=True, geometryIndices=True) or ()
            )
            if len(geometry_indices) != 1:
                _fail(f"new blendShape {blend_shape!r} has ambiguous geometry index")
            geometry_index = int(geometry_indices[0])
        target_index = int(target["target_index"])
        item = (
            f"{blend_shape}.inputTarget[{geometry_index}].inputTargetGroup[{target_index}]"
            ".inputTargetItem[6000]"
        )
        components = target["components"]
        points = target["points"]
        if components or points:
            _write_vertex_target_deltas(adapter, item, components, points)
        else:
            _initialize_empty_vertex_target(
                adapter,
                blend_shape,
                target["shape"],
                int(geometry_index),
                target_index,
            )
        plug = f"{blend_shape}.weight[{target_index}]"
        flat = list(_call(adapter, "alias_attr", blend_shape, query=True) or ())
        alias = maya_name_utils.sanitize_unique_name(
            morph.name,
            {str(value) for value in flat[0::2]},
            fallback=f"morph_{morph.index}",
        )
        _call(adapter, "alias_attr", alias, plug)
        _ensure_attr(adapter, blend_shape, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, "string")
        mapping = dict(target["mapping"])
        mapping[str(target_index)] = {"name": morph.name, "index": morph.index}
        _write_vertex_name_mapping(adapter, blend_shape, mapping)
        _call(
            adapter,
            "connect_attr",
            f"{controller}.outputWeight[{morph.index}]",
            plug,
            force=True,
        )


def _write_vertex_name_mapping(adapter: Any, blend_shape: str, mapping: Mapping[str, Any]) -> None:
    _call(
        adapter,
        "set_attr",
        f"{blend_shape}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}",
        json.dumps(mapping, ensure_ascii=False, separators=(",", ":")),
        type="string",
    )


def _runtime_rebuild_types(
    old_by_binding: Mapping[str, MmdMorphSpec],
    new_by_binding: Mapping[str, MmdMorphSpec],
    deleted: list[MmdMorphSpec],
    created: list[MmdMorphSpec],
) -> set[str]:
    """Return fixed runtime graphs which must be refreshed for this edit."""
    affected: set[str] = set()
    for morph in (*deleted, *created):
        if morph.morph_type in {"bone", "material"} and morph.offsets:
            affected.add(morph.morph_type)
    for binding, old in old_by_binding.items():
        new = new_by_binding.get(binding)
        if new is None or old.morph_type not in {"bone", "material"}:
            continue
        if old.index != new.index or old.offsets != new.offsets:
            if old.offsets or new.offsets:
                affected.add(old.morph_type)
    return affected


def _validate_runtime_rebuilders(
    affected: set[str],
    rebuilders: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if not affected:
        return {}
    if not isinstance(rebuilders, Mapping):
        _fail(
            "bone/material live-preview edits require explicit transactional runtime rebuilders"
        )
    missing = sorted(kind for kind in affected if not callable(rebuilders.get(kind)))
    if missing:
        _fail(f"missing live-preview runtime rebuilders: {missing!r}")
    return rebuilders


def _resolve_controller(adapter: Any, root: str, *, allow_missing: bool) -> str | None:
    if not _has_attr(adapter, root, "mmd_morph_controller"):
        if allow_missing:
            _require_controller_node_type(adapter)
            return None
        _fail(f"model root {root!r} has no morph controller")
    controllers = list(_call(adapter, "list_connections", f"{root}.mmd_morph_controller", source=True, destination=False) or [])
    if not controllers and allow_missing:
        _require_controller_node_type(adapter)
        return None
    if len(controllers) != 1:
        _fail(f"model root {root!r} must have exactly one morph controller")
    controller = _canonical_node(adapter, str(controllers[0]))
    if _call(adapter, "node_type", controller) != "mmdMorphController":
        _fail(f"invalid morph controller type: {controller!r}")
    return controller


def _require_controller_node_type(adapter: Any) -> None:
    available = list(_call(adapter, "all_node_types") or [])
    if "mmdMorphController" not in available:
        _fail("required node type 'mmdMorphController' is unavailable")


def _create_controller(adapter: Any, root: str) -> str:
    controller = _canonical_node(
        adapter,
        str(_call(adapter, "create_node", "mmdMorphController", name="mmdMorphController")),
    )
    _ensure_attr(adapter, root, "mmd_morph_controller", "message")
    _call(adapter, "connect_attr", f"{controller}.message", f"{root}.mmd_morph_controller")
    return controller


def _controller_plan(
    adapter: Any,
    controller: str,
    old_spec: MmdModelAuthoringSpec,
    new_spec: MmdModelAuthoringSpec,
    old_by_binding: Mapping[str, MmdMorphSpec],
) -> dict[str, Any]:
    new_index_by_binding = {
        morph.binding_identity: morph.index for morph in new_spec.morphs if morph.binding_identity is not None
    }
    inputs: dict[str, tuple[str | None, float]] = {}
    outputs: dict[str, tuple[str, ...]] = {}
    for binding, old in old_by_binding.items():
        input_plug = f"{controller}.inputWeight[{old.index}]"
        sources = tuple(_call(adapter, "list_connections", input_plug, source=True, destination=False, plugs=True) or ())
        if len(sources) > 1:
            _fail(f"{input_plug} has ambiguous incoming connections")
        value = float(_call(adapter, "get_attr", input_plug) or 0.0)
        inputs[binding] = (str(sources[0]) if sources else None, value)
        output_plug = f"{controller}.outputWeight[{old.index}]"
        destinations = tuple(
            str(item)
            for item in (_call(adapter, "list_connections", output_plug, source=False, destination=True, plugs=True) or ())
        )
        outputs[binding] = destinations
        if old.morph_type != "vertex" and f"{binding}.weight" not in destinations:
            _fail(f"controller output {old.index} is not connected to {binding}.weight")
    return {
        "controller": controller,
        "inputs": inputs,
        "outputs": outputs,
        "new_indices": new_index_by_binding,
        "old_indices": {binding: morph.index for binding, morph in old_by_binding.items()},
    }


def _apply_controller_plan(
    adapter: Any,
    controller: str,
    plan: Mapping[str, Any],
    spec: MmdModelAuthoringSpec,
    created_nodes: Mapping[int, str],
) -> None:
    new_indices: Mapping[str, int] = plan["new_indices"]
    for binding, (source, _value) in plan["inputs"].items():
        old_index = _binding_old_index(plan, binding)
        if source:
            _call(adapter, "disconnect_attr", source, f"{controller}.inputWeight[{old_index}]")
        for destination in plan["outputs"][binding]:
            _call(adapter, "disconnect_attr", f"{controller}.outputWeight[{old_index}]", destination)

    aliases = list(_call(adapter, "alias_attr", controller, query=True) or [])
    for index in range(1, len(aliases), 2):
        plug = str(aliases[index])
        if plug.startswith("inputWeight["):
            _call(adapter, "alias_attr", f"{controller}.{plug}", remove=True)

    for binding, (source, value) in plan["inputs"].items():
        if binding not in new_indices:
            continue
        index = new_indices[binding]
        plug = f"{controller}.inputWeight[{index}]"
        if source:
            _call(adapter, "connect_attr", source, plug, force=True)
        else:
            _call(adapter, "set_attr", plug, value)
        for destination in plan["outputs"][binding]:
            # Network weight destinations are reconnected below from the
            # canonical binding; all other destinations (e.g. blendShape) are
            # preserved verbatim.
            if destination != f"{binding}.weight":
                _call(adapter, "connect_attr", f"{controller}.outputWeight[{index}]", destination, force=True)

    for morph in spec.morphs:
        input_plug = f"{controller}.inputWeight[{morph.index}]"
        if morph.binding_identity in created_nodes.values():
            _call(adapter, "set_attr", input_plug, 0.0)
        _call(adapter, "set_attr", input_plug, keyable=True)
        _assign_controller_alias(adapter, controller, morph.index, morph.name)
        if morph.morph_type != "vertex":
            _call(adapter, "connect_attr", f"{controller}.outputWeight[{morph.index}]", f"{morph.binding_identity}.weight", force=True)

    topology = compute_group_topology(spec.morphs)
    _call(adapter, "set_attr", f"{controller}.topologyVersion", lock=False)
    _call(adapter, "set_attr", f"{controller}.topologyVersion", TOPOLOGY_VERSION, lock=True)
    _call(adapter, "set_attr", f"{controller}.groupTopology", lock=False)
    _call(
        adapter,
        "set_attr",
        f"{controller}.groupTopology",
        serialize_group_topology(topology),
        type="string",
        lock=True,
    )


def _assign_controller_alias(
    adapter: Any,
    controller: str,
    index: int,
    display_name: str,
) -> str:
    """Expose one safe, unique editable morph name as a controller alias."""
    input_plug = f"{controller}.inputWeight[{index}]"
    aliases = list(_call(adapter, "alias_attr", controller, query=True) or ())
    if len(aliases) % 2:
        _fail("controller aliases must be alias/plug pairs")
    current_alias = None
    used = {str(alias) for alias in aliases[0::2]}
    for alias, plug in zip(aliases[0::2], aliases[1::2]):
        if str(plug).rsplit(".", 1)[-1] == f"inputWeight[{index}]":
            current_alias = str(alias)
            break
    list_attr = getattr(adapter, "list_attr", None)
    if callable(list_attr):
        used.update(str(value) for value in (list_attr(controller) or ()))
        used.update(
            str(value) for value in (list_attr(controller, shortNames=True) or ())
        )
    if current_alias:
        used.discard(current_alias)
    alias = maya_name_utils.sanitize_unique_name(
        display_name,
        used,
        fallback=f"morph_{index}",
    )
    if current_alias == alias:
        return alias
    if current_alias:
        _call(adapter, "alias_attr", input_plug, remove=True)
    _call(adapter, "alias_attr", alias, input_plug)
    return alias


def _binding_old_index(plan: Mapping[str, Any], binding: str) -> int:
    # The output destination itself is indexed only by the controller source;
    # preserve the old index alongside the plan without exposing it publicly.
    old_indices = plan.get("old_indices")
    if old_indices is not None:
        return int(old_indices[binding])
    _fail(f"missing old controller index for {binding!r}")


def _write_morph(adapter: Any, node: str, morph: MmdMorphSpec) -> None:
    values = {
        "mmd_morph_name": ("string", morph.name),
        "mmd_morph_name_en": ("string", morph.name_english),
        "mmd_morph_type": ("string", morph.morph_type),
        "mmd_morph_index": ("long", morph.index),
        "mmd_morph_panel": ("long", morph.panel),
    }
    if morph.morph_type != "vertex":
        payload = json.dumps(morph.to_mapping()["offsets"], ensure_ascii=False, separators=(",", ":"))
        for attr in _OFFSET_ATTRS[morph.morph_type]:
            values[attr] = ("string", payload)
    count_attr = _OFFSET_COUNT_ATTRS.get(morph.morph_type)
    if count_attr is not None:
        values[count_attr] = ("long", len(morph.offsets))
    for attr, (attr_type, value) in values.items():
        _set_typed(adapter, node, attr, attr_type, value)


def _write_morph_values(
    adapter: Any,
    node: str,
    old: MmdMorphSpec,
    new: MmdMorphSpec,
    changed: set[str],
) -> None:
    """Write only selected-node attrs which changed in the patch-safe route."""
    if "name" in changed:
        _set_typed(adapter, node, "mmd_morph_name", "string", new.name)
    if "name_english" in changed:
        _set_typed(adapter, node, "mmd_morph_name_en", "string", new.name_english)
    if "panel" in changed:
        _set_typed(adapter, node, "mmd_morph_panel", "long", new.panel)
    if "offsets" in changed:
        if old.morph_type == "vertex":
            # Vertex values are written to blendShape inputTarget data by the
            # runtime patch below; never recreate a persisted JSON shadow.
            return
        payload = json.dumps(new.to_mapping()["offsets"], ensure_ascii=False, separators=(",", ":"))
        for attr in _OFFSET_ATTRS[old.morph_type]:
            _set_typed(adapter, node, attr, "string", payload)


def _update_selected_runtime_values(
    adapter: Any,
    root: str,
    binding: str,
    old: MmdMorphSpec,
    new: MmdMorphSpec,
    *,
    vertex_bindings: tuple[MorphBinding, ...] = (),
) -> None:
    """Refresh existing evaluator contribution constants for one morph.

    Runtime nodes are discovered by type and matched through their incoming
    ``contribution[*].weight`` connection.  No controller or unrelated
    contribution is rebuilt.  Morph types without a selected-only runtime
    contract are classified structural before this function is reached.
    """
    if old.morph_type == "bone":
        _patch_bone_runtime_values(adapter, binding, old, new)
    elif old.morph_type == "material":
        _patch_material_runtime_values(adapter, binding, new)
    elif old.morph_type == "vertex":
        _patch_vertex_runtime_values(
            adapter,
            root,
            binding,
            old,
            new,
            vertex_bindings=vertex_bindings,
        )


def _runtime_nodes(adapter: Any, node_type: str) -> tuple[str, ...]:
    node_types = getattr(adapter, "all_node_types", None)
    if callable(node_types):
        try:
            available = tuple(str(value) for value in (node_types() or ()))
        except Exception as exc:
            _fail(f"failed to inspect available Maya node types: {exc}")
        if node_type not in available:
            return ()
    lister = getattr(adapter, "ls", None)
    if not callable(lister):
        return ()
    try:
        return tuple(str(node) for node in (lister(type=node_type, long=True) or ()))
    except Exception as exc:
        _fail(f"failed to inspect {node_type} runtime nodes: {exc}")
    return ()


def _selected_contribution_slots(adapter: Any, node: str, binding: str) -> list[int]:
    try:
        indices = _call(adapter, "get_attr", f"{node}.contribution", multiIndices=True) or ()
    except Exception as exc:
        _fail(f"failed to inspect runtime contributions on {node!r}: {exc}")
    selected: list[int] = []
    for raw_index in indices:
        try:
            index = int(raw_index)
            sources = _call(
                adapter,
                "list_connections",
                f"{node}.contribution[{index}].weight",
                source=True,
                destination=False,
                plugs=True,
            ) or ()
        except Exception as exc:
            _fail(f"failed to inspect runtime contribution {node}[{raw_index}]: {exc}")
        if any(str(source).split(".", 1)[0] == binding for source in sources):
            selected.append(index)
    return selected


def _patch_bone_runtime_values(adapter: Any, binding: str, old: MmdMorphSpec, new: MmdMorphSpec) -> None:
    del old
    slots_written = 0
    for node in _runtime_nodes(adapter, "mmdBoneMorphAccum"):
        slots = _selected_contribution_slots(adapter, node, binding)
        if not slots:
            continue
        target_joint = _call(adapter, "get_attr", f"{node}.mmd_target_joint")
        target_index = _runtime_target_index(adapter, str(target_joint), "mmd_bone_index")
        offsets = tuple(
            offset for offset in new.offsets if int(offset["bone_index"]) == target_index
        )
        if len(slots) != len(offsets):
            _fail(
                f"selected bone morph {binding!r} runtime contribution count mismatch: "
                f"slots={len(slots)} offsets={len(offsets)}"
            )
        for slot, offset in zip(slots, offsets):
            prefix = f"{node}.contribution[{slot}]"
            translation = offset["translation"]
            rotation = offset["rotation"]
            from mmd_tools.converters.bone_morph_runtime import pmx_bone_offset_to_runtime_values

            translated, converted_rotation = pmx_bone_offset_to_runtime_values(
                tuple(translation), tuple(rotation), str(target_joint)
            )
            _call(
                adapter,
                "set_attr",
                f"{prefix}.translateOffset",
                *translated,
                type="double3",
            )
            _call(
                adapter,
                "set_attr",
                f"{prefix}.rotateOffsetQuat",
                *converted_rotation,
                type="double4",
            )
            slots_written += 1
    if new.offsets and slots_written == 0 and _runtime_nodes(adapter, "mmdBoneMorphAccum"):
        _fail(f"selected bone morph {binding!r} has no runtime contribution binding")


def _patch_material_runtime_values(adapter: Any, binding: str, new: MmdMorphSpec) -> None:
    slots_written = 0
    for node in _runtime_nodes(adapter, "mmdMaterialMorphEval"):
        slots = _selected_contribution_slots(adapter, node, binding)
        if not slots:
            continue
        target_shader = _call(adapter, "get_attr", f"{node}.mmd_target_shader")
        target_index = _runtime_target_index(adapter, str(target_shader), "mmd_material_index")
        offsets = tuple(
            offset
            for offset in new.offsets
            if int(offset["material_index"]) in {target_index, -1}
        )
        if len(slots) != len(offsets):
            _fail(
                f"selected material morph {binding!r} runtime contribution count mismatch: "
                f"slots={len(slots)} offsets={len(offsets)}"
            )
        for slot, offset in zip(slots, offsets):
            prefix = f"{node}.contribution[{slot}]"
            vectors = {
                "diffuseOffset": tuple(offset["diffuse"]),
                "specularOffset": tuple(offset["specular"]),
                "specularCoefficientOffset": (float(offset["specular_coefficient"]),),
                "ambientOffset": tuple(offset["ambient"]),
                "edgeColorOffset": tuple(offset["edge_color"]),
                "edgeSizeOffset": (float(offset["edge_size"]),),
                "textureOffset": tuple(offset["texture_factor"]),
                "sphereTextureOffset": tuple(offset["sphere_texture_factor"]),
                "toonTextureOffset": tuple(offset["toon_texture_factor"]),
            }
            for attr, values in vectors.items():
                if len(values) == 1:
                    _call(adapter, "set_attr", f"{prefix}.{attr}", values[0])
                elif len(values) == 3:
                    _call(adapter, "set_attr", f"{prefix}.{attr}", *values, type="double3")
                else:
                    for axis, value in zip("RGBA", values):
                        _call(adapter, "set_attr", f"{prefix}.{attr}{axis}", value)
            slots_written += 1
    if new.offsets and slots_written == 0 and _runtime_nodes(adapter, "mmdMaterialMorphEval"):
        _fail(f"selected material morph {binding!r} has no runtime contribution binding")


def _runtime_target_index(adapter: Any, node: str, attr: str) -> int:
    if not node or not _has_attr(adapter, node, attr):
        _fail(f"runtime target {node!r} is missing {attr}")
    return _read_int(adapter, node, attr)


def _patch_vertex_runtime_values(
    adapter: Any,
    root: str,
    binding: str,
    old: MmdMorphSpec,
    new: MmdMorphSpec,
    *,
    vertex_bindings: tuple[MorphBinding, ...],
) -> None:
    """Update selected imported blendShape target point arrays in place."""
    covered: set[int] = set()
    target_count = 0
    for resolved in vertex_bindings:
        node = resolved.blend_shape_identity
        target_index = resolved.logical_target_index
        geometries = tuple(_call(adapter, "blend_shape", node, query=True, geometry=True) or ())
        geometry_indices = tuple(
            _call(adapter, "blend_shape", node, query=True, geometryIndices=True) or ()
        )
        if len(geometries) != len(geometry_indices) or not geometries:
            _fail(f"blendShape {node!r} has ambiguous geometry indices")
        mapping = _read_vertex_name_mapping(adapter, node)
        if new.name != old.name:
            aliases = list(_call(adapter, "alias_attr", node, query=True) or ())
            old_alias = None
            for alias, plug in zip(aliases[0::2], aliases[1::2]):
                if str(plug) in {f"weight[{target_index}]", f"w[{target_index}]"}:
                    old_alias = str(alias)
                    break
            replacement = maya_name_utils.sanitize_unique_name(
                new.name,
                {str(value) for value in aliases[0::2] if str(value) != old_alias},
                fallback=f"morph_{new.index}",
            )
            if old_alias:
                _call(adapter, "alias_attr", f"{node}.weight[{target_index}]", remove=True)
            _call(adapter, "alias_attr", replacement, f"{node}.weight[{target_index}]")
        entry = mapping.get(str(target_index))
        if not isinstance(entry, Mapping) or entry.get("name") != new.name or entry.get("index") != new.index:
            # Name edits are persisted on the network binding; update the
            # selected blendShape annotation before writing point values.
            mapping[str(target_index)] = {"name": new.name, "index": new.index}
            _write_vertex_name_mapping(adapter, node, mapping)
        for geometry, geometry_index in zip(geometries, geometry_indices):
            geometry = _canonical_node(adapter, str(geometry))
            source_to_local = _source_vertex_map(adapter, geometry)
            components: list[str] = []
            points: list[tuple[float, float, float, float]] = []
            for offset in new.offsets:
                source_index = int(offset["vertex_index"])
                local_index = source_to_local.get(source_index)
                if local_index is None:
                    _fail(f"vertex morph {new.index} references unmapped source index {source_index}")
                covered.add(source_index)
                components.append(f"vtx[{local_index}]")
                points.append((*pmx_vertex_offset_to_maya_tuple(offset["position_offset"]), 1.0))
            item = (
                f"{node}.inputTarget[{int(geometry_index)}].inputTargetGroup[{target_index}]"
                ".inputTargetItem[6000]"
            )
            _write_vertex_target_deltas(adapter, item, components, points)
            target_count += 1
    expected = {int(offset["vertex_index"]) for offset in new.offsets}
    if target_count == 0 or covered != expected:
        _fail(f"selected vertex morph {binding!r} has no exact blendShape target binding")


def _set_typed(adapter: Any, node: str, attr: str, attr_type: str, value: Any) -> None:
    _ensure_attr(adapter, node, attr, attr_type)
    if attr_type == "string":
        _call(adapter, "set_attr", f"{node}.{attr}", value, type="string")
    else:
        _call(adapter, "set_attr", f"{node}.{attr}", value)


def _ensure_attr(adapter: Any, node: str, attr: str, attr_type: str, **kwargs: Any) -> None:
    if _has_attr(adapter, node, attr):
        return
    options: dict[str, Any] = {"longName": attr, "attributeType": attr_type}
    if attr_type == "string":
        options = {"longName": attr, "dataType": "string"}
    if "default" in kwargs:
        options["defaultValue"] = kwargs["default"]
    if "keyable" in kwargs:
        options["keyable"] = kwargs["keyable"]
    _call(adapter, "add_attr", node, **options)


def _require_root(adapter: Any, root: str) -> str:
    if not isinstance(root, str) or not root.strip() or not _call(adapter, "object_exists", root):
        _fail(f"invalid model root: {root!r}")
    paths = list(_call(adapter, "ls", root, long=True) or [])
    if len(paths) != 1 or not str(paths[0]).startswith("|"):
        _fail(f"model root is not a unique DAG path: {root!r}")
    return str(paths[0])


def _canonical_node(adapter: Any, node: str) -> str:
    paths = list(_call(adapter, "ls", node, long=True) or [])
    if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0]:
        _fail(f"node is not a unique Maya identity: {node!r}")
    return paths[0]


def _require_model_spec(value: Any, field: str) -> None:
    if not isinstance(value, MmdModelAuthoringSpec):
        _fail(f"{field} must be an MmdModelAuthoringSpec")


def _has_attr(adapter: Any, node: str, attr: str) -> bool:
    return bool(_call(adapter, "attribute_exists", attr, node))


def _read_int(adapter: Any, node: str, attr: str) -> int:
    value = _call(adapter, "get_attr", f"{node}.{attr}")
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{node}.{attr} must be an integer")
    return value


def _read_string(adapter: Any, node: str, attr: str) -> str:
    value = _call(adapter, "get_attr", f"{node}.{attr}")
    if not isinstance(value, str):
        _fail(f"{node}.{attr} must be a string")
    return value


def _call(adapter: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    try:
        return getattr(adapter, method)(*args, **kwargs)
    except MayaMorphAuthoringError:
        raise
    except Exception as exc:
        raise MayaMorphAuthoringError(f"Maya adapter call {method} failed: {exc}") from exc


def _fail(message: str) -> None:
    raise MayaMorphAuthoringError(message)


__all__ = [
    "MayaMorphAuthoringError",
    "apply_morph_create",
    "apply_morph_value_patch",
    "apply_morph_spec_change",
    "maya_runtime_rebuilders",
]
