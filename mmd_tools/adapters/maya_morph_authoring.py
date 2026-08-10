"""Transactional Maya bindings for immutable PMX morph authoring specs.

This module mutates registry-owned morph network nodes and the fixed-index
``mmdMorphController`` topology.  It deliberately does not open an undo chunk;
the scene metadata coordinator must call it inside the same transaction that
persists the resulting :class:`MmdModelAuthoringSpec`.

Vertex morph offsets remain coupled to imported blendShape targets.  Until a
blendShape target rebuilder is available, any change which would invalidate
that oracle is rejected before the first Maya write.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
import math
import re
from typing import Any

from mmd_tools.core import model_registry
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
    ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.core import maya_name_utils
from mmd_tools.core.model_authoring_spec import MmdModelAuthoringSpec, MmdMorphSpec
from mmd_tools.converters.morph_converter import pmx_vertex_offset_to_maya_tuple


REGISTRY_CATEGORY_MORPH = "morph"
_UNSUPPORTED_TYPES = {"flip", "impulse"}
_OFFSET_ATTRS = {
    "vertex": (ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON,),
    "bone": (ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON, "mmd_bone_morph_offsets_json"),
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
    model_scale_resolver: Any | None = None,
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
        model_scale_resolver,
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


_WEIGHT_DESTINATION = re.compile(r"^(?P<node>[^.]+)\.(?:weight|w)\[(?P<index>\d+)\]$")


def _vertex_target_plan(
    adapter: Any,
    root: str,
    old_by_binding: Mapping[str, MmdMorphSpec],
    new_by_binding: Mapping[str, MmdMorphSpec],
    created: list[MmdMorphSpec],
    controller_plan: Mapping[str, Any],
    model_scale_resolver: Any | None,
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
        destinations = tuple(controller_plan["outputs"].get(binding, ()))
        targets: list[tuple[str, int, str]] = []
        seen_nodes: set[str] = set()
        for destination in destinations:
            node, target_index = _resolve_vertex_weight_destination(adapter, str(destination), old.index)
            if _call(adapter, "node_type", node) != "blendShape":
                _fail(f"vertex morph {old.index} output is not a blendShape: {node!r}")
            if node in seen_nodes:
                _fail(f"vertex morph {old.index} has multiple targets on blendShape {node!r}")
            seen_nodes.add(node)
            plug = f"{node}.weight[{target_index}]"
            alias = _call(adapter, "alias_attr", plug, query=True)
            if not isinstance(alias, str) or not alias:
                _fail(f"vertex target {plug!r} has no unique alias")
            mapping = _read_vertex_name_mapping(adapter, node)
            entry = mapping.get(str(target_index))
            if not isinstance(entry, Mapping) or entry.get("name") != old.name or entry.get("index") != old.index:
                _fail(f"vertex target {plug!r} metadata does not match old_spec")
            targets.append((node, target_index, alias))
        if not targets:
            _fail(f"vertex morph {old.index} has no exact blendShape target binding")

        scale = _resolve_vertex_scale(root, model_scale_resolver) if offsets_changed else 1.0
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
                        points.append((*pmx_vertex_offset_to_maya_tuple(offset["position_offset"], scale), 1.0))
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
            model_scale_resolver,
        )
    )
    return tuple(plans)


def _resolve_vertex_weight_destination(
    adapter: Any,
    destination: str,
    morph_index: int,
) -> tuple[str, int]:
    match = _WEIGHT_DESTINATION.fullmatch(destination)
    if match is not None:
        return _canonical_node(adapter, match.group("node")), int(match.group("index"))
    if "." not in destination:
        _fail(f"vertex morph {morph_index} has invalid output {destination!r}")
    raw_node, alias = destination.rsplit(".", 1)
    node = _canonical_node(adapter, raw_node)
    if _call(adapter, "node_type", node) != "blendShape":
        _fail(f"vertex morph {morph_index} has non-blendShape output {destination!r}")
    flat = list(_call(adapter, "alias_attr", node, query=True) or ())
    matches = []
    for candidate_alias, plug in zip(flat[0::2], flat[1::2]):
        if str(candidate_alias) != alias:
            continue
        plug_match = re.fullmatch(r"(?:weight|w)\[(\d+)\]", str(plug))
        if plug_match is not None:
            matches.append(int(plug_match.group(1)))
    if len(matches) != 1:
        _fail(f"vertex morph {morph_index} alias output is ambiguous: {destination!r}")
    return node, matches[0]


def _new_vertex_target_plans(
    adapter: Any,
    root: str,
    created: list[MmdMorphSpec],
    model_scale_resolver: Any | None,
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
        scale = _resolve_vertex_scale(root, model_scale_resolver) if morph.offsets else 1.0
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
                points.append((*pmx_vertex_offset_to_maya_tuple(offset["position_offset"], scale), 1.0))
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


def _resolve_vertex_scale(root: str, resolver: Any | None) -> float:
    if not callable(resolver):
        _fail("vertex offset authoring requires an explicit model scale resolver")
    try:
        value = resolver(root)
    except Exception as exc:
        raise MayaMorphAuthoringError(f"vertex model scale resolution failed: {exc}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("vertex model scale must be a finite positive number")
    scale = float(value)
    if not math.isfinite(scale) or scale <= 0.0:
        _fail("vertex model scale must be a finite positive number")
    return scale


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
                _call(adapter, "alias_attr", plug, remove=True)
                mapping = _read_vertex_name_mapping(adapter, node)
                mapping.pop(str(target_index), None)
                _write_vertex_name_mapping(adapter, node, mapping)
                for geometry in plan["geometries"]:
                    if geometry["group"].startswith(f"{node}."):
                        _call(adapter, "remove_multi_instance", geometry["group"], b=True)
                _call(adapter, "remove_multi_instance", plug, b=True)
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
            item = geometry["item"]
            components = geometry["components"]
            points = geometry["points"]
            _call(
                adapter,
                "set_attr",
                f"{item}.inputComponentsTarget",
                len(components),
                *components,
                type="componentList",
            )
            _call(
                adapter,
                "set_attr",
                f"{item}.inputPointsTarget",
                len(points),
                *points,
                type="pointArray",
            )


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
        _call(
            adapter,
            "set_attr",
            f"{item}.inputComponentsTarget",
            len(components),
            *components,
            type="componentList",
        )
        _call(
            adapter,
            "set_attr",
            f"{item}.inputPointsTarget",
            len(points),
            *points,
            type="pointArray",
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
        _call(adapter, "alias_attr", f"morph_{morph.index}", input_plug)
        if morph.morph_type != "vertex":
            _call(adapter, "connect_attr", f"{controller}.outputWeight[{morph.index}]", f"{morph.binding_identity}.weight", force=True)

    topology = _group_topology(spec)
    _call(adapter, "set_attr", f"{controller}.topologyVersion", lock=False)
    _call(adapter, "set_attr", f"{controller}.topologyVersion", 1, lock=True)
    _call(adapter, "set_attr", f"{controller}.groupTopology", lock=False)
    _call(
        adapter,
        "set_attr",
        f"{controller}.groupTopology",
        json.dumps(topology, separators=(",", ":")),
        type="string",
        lock=True,
    )


def _binding_old_index(plan: Mapping[str, Any], binding: str) -> int:
    # The output destination itself is indexed only by the controller source;
    # preserve the old index alongside the plan without exposing it publicly.
    old_indices = plan.get("old_indices")
    if old_indices is not None:
        return int(old_indices[binding])
    _fail(f"missing old controller index for {binding!r}")


def _group_topology(spec: MmdModelAuthoringSpec) -> dict[str, list[list[float | int]]]:
    expanding = {
        morph.index: morph
        for morph in spec.morphs
        if morph.morph_type in {"group", "flip"}
    }
    rates: dict[int, dict[int, float]] = {}

    def expand(source: int, current: int, rate: float, path: set[int]) -> None:
        for offset in expanding[current].offsets:
            target = int(offset["morph_index"])
            next_rate = rate * float(offset.get("morph_rate", offset.get("flip_rate", 0.0)))
            if target in path:
                continue
            sources = rates.setdefault(target, {})
            sources[source] = sources.get(source, 0.0) + next_rate
            if target in expanding:
                expand(source, target, next_rate, path | {target})

    for index in expanding:
        expand(index, index, 1.0, {index})
    return {
        str(target): [[source, rate] for source, rate in sorted(sources.items())]
        for target, sources in sorted(rates.items())
    }


def _write_morph(adapter: Any, node: str, morph: MmdMorphSpec) -> None:
    values = {
        "mmd_morph_name": ("string", morph.name),
        "mmd_morph_name_en": ("string", morph.name_english),
        "mmd_morph_type": ("string", morph.morph_type),
        "mmd_morph_index": ("long", morph.index),
        "mmd_morph_panel": ("long", morph.panel),
    }
    payload = json.dumps(morph.to_mapping()["offsets"], ensure_ascii=False, separators=(",", ":"))
    for attr in _OFFSET_ATTRS[morph.morph_type]:
        values[attr] = ("string", payload)
    count_attr = _OFFSET_COUNT_ATTRS.get(morph.morph_type)
    if count_attr is not None:
        values[count_attr] = ("long", len(morph.offsets))
    for attr, (attr_type, value) in values.items():
        _set_typed(adapter, node, attr, attr_type, value)


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
    "apply_morph_spec_change",
    "maya_runtime_rebuilders",
]
