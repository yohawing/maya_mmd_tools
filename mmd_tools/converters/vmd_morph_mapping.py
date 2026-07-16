"""Morph name mapping helpers for VMD animation conversion."""

from collections import defaultdict
from typing import List, Optional, Set, Tuple

import maya.cmds as cmds

from .morph_scene_metadata import iter_morph_network_metadata, read_blendshape_morph_names


def iter_morph_mappings(mapping_entry) -> List[Tuple[str, str, str]]:
    """Return normalized morph mappings from legacy tuple/list entries."""
    if isinstance(mapping_entry, list):
        mappings = mapping_entry
    elif mapping_entry:
        mappings = [mapping_entry]
    else:
        return []

    normalized_mappings = []
    for entry in mappings:
        if not isinstance(entry, tuple) or len(entry) != 3:
            continue

        morph_node, weight_ref, morph_name = entry
        if isinstance(weight_ref, int):
            weight_ref = f"weight[{weight_ref}]"
        normalized_mappings.append((morph_node, weight_ref, morph_name))

    return normalized_mappings


def register_morph_mapping(converter, morph_name: str, mapping: Tuple[str, str, str]) -> None:
    """Register one VMD morph name to one or more Maya weight targets."""
    existing = converter.morph_name_mapping.get(morph_name)
    if existing is None:
        converter.morph_name_mapping[morph_name] = [mapping]
        return

    if isinstance(existing, tuple):
        if existing == mapping:
            return
        converter.morph_name_mapping[morph_name] = [existing, mapping]
        return

    for existing_mapping in existing:
        if existing_mapping == mapping:
            return
    existing.append(mapping)


def _long_names(nodes) -> Set[str]:
    """Resolve Maya nodes to stable full DAG paths where possible."""
    result = set()
    for node in nodes or []:
        matches = cmds.ls(node, long=True) or []
        result.update(matches)
    return result


def _root_owned_dag_nodes(target_model: str) -> Set[str]:
    root_names = _long_names([target_model])
    descendants = cmds.listRelatives(target_model, allDescendents=True, fullPath=True) or []
    return root_names | _long_names(descendants)


def _blendshape_is_owned_by_root(blend_shape: str, target_model: str, owned_dag_nodes: Set[str]) -> bool:
    """Return True only when a blendShape has explicit or DAG-proven root ownership."""
    if cmds.attributeQuery("mmd_model_root", node=blend_shape, exists=True):
        connected_roots = cmds.listConnections(f"{blend_shape}.mmd_model_root") or []
        connected_root_names = _long_names(connected_roots)
        if connected_root_names and connected_root_names.issubset(_long_names([target_model])):
            return True

    geometry = cmds.blendShape(blend_shape, query=True, geometry=True) or []
    geometry_names = _long_names(geometry)
    # A deformer that resolves to geometry outside the target root has
    # ambiguous/shared ownership.  Skip it instead of cross-keying a model.
    return bool(geometry_names) and geometry_names.issubset(owned_dag_nodes)


def _network_is_owned_by_root(node: str, target_model: str) -> bool:
    """Network morph ownership is connection-based and intentionally fail-closed."""
    if not cmds.attributeQuery("mmd_model_root", node=node, exists=True):
        return False
    connected_roots = cmds.listConnections(f"{node}.mmd_model_root") or []
    connected_root_names = _long_names(connected_roots)
    return bool(connected_root_names) and connected_root_names.issubset(_long_names([target_model]))


def morph_node_is_owned_by_root(node: str, target_model: str) -> bool:
    """Prove blendShape/network morph ownership for destructive operations."""
    if not node or not target_model or not cmds.objExists(node) or not cmds.objExists(target_model):
        return False
    node_type = cmds.nodeType(node)
    if node_type == "blendShape":
        return _blendshape_is_owned_by_root(
            node,
            target_model,
            _root_owned_dag_nodes(target_model),
        )
    if node_type == "network":
        return _network_is_owned_by_root(node, target_model)
    return False


def build_morph_mappings(converter, target_model: Optional[str] = None) -> None:
    """Build morph name mappings from scene blendShapes and metadata networks."""
    converter.morph_name_mapping = {}
    converter.morph_bindings = {}
    converter.morph_binding_diagnostics = []

    owned_dag_nodes = (
        _root_owned_dag_nodes(target_model)
        if target_model and cmds.objExists(target_model)
        else set()
    )
    blend_shapes = cmds.ls(type="blendShape") or []
    for bs_node in blend_shapes:
        if target_model and not _blendshape_is_owned_by_root(bs_node, target_model, owned_dag_nodes):
            continue
        stored_names = read_blendshape_morph_names(bs_node)
        weight_count = cmds.blendShape(bs_node, query=True, weightCount=True) or 0
        for i in range(weight_count):
            alias = cmds.aliasAttr(f"{bs_node}.weight[{i}]", query=True)
            mapping = (bs_node, f"weight[{i}]", alias or f"weight[{i}]")
            if alias:
                register_morph_mapping(converter, alias, mapping)

            original_name = stored_names.get(i)
            if original_name:
                register_morph_mapping(converter, original_name, mapping)
            elif alias:
                for candidate in get_original_morph_name_candidates(alias):
                    register_morph_mapping(converter, candidate, mapping)

    network_metadata = list(iter_morph_network_metadata(
        root_group=target_model if target_model else None,
        morph_types={"bone", "group", "material"},
        required_attrs=("weight",),
    ))
    providers_by_index = defaultdict(list)
    providers_by_name = defaultdict(list)
    for metadata in network_metadata:
        if metadata.index is not None:
            providers_by_index[metadata.index].append(metadata.node)
        if metadata.name:
            providers_by_name[metadata.name].append(metadata.node)
    ambiguous_nodes = set()
    for index, nodes in providers_by_index.items():
        if len(nodes) > 1:
            ambiguous_nodes.update(nodes)
            converter.morph_binding_diagnostics.append(
                f"duplicate_morph_provider:{index}:{','.join(sorted(nodes))}"
            )
    for name, nodes in providers_by_name.items():
        if len(nodes) > 1:
            ambiguous_nodes.update(nodes)
            converter.morph_binding_diagnostics.append(
                f"duplicate_named_provider:{name}:{','.join(sorted(nodes))}"
            )

    for metadata in network_metadata:
        morph_node = metadata.node
        if target_model and not _network_is_owned_by_root(morph_node, target_model):
            continue
        if morph_node in ambiguous_nodes:
            continue
        original_name = metadata.name
        if not original_name:
            continue

        final_plugs = sorted(set(cmds.listConnections(
            f"{morph_node}.weight",
            source=False,
            destination=True,
            plugs=True,
        ) or []))
        if target_model and not final_plugs:
            converter.morph_binding_diagnostics.append(f"disconnected_morph_provider:{morph_node}")

        mapping = (morph_node, "weight", original_name)
        register_morph_mapping(converter, original_name, mapping)
        safe_name = morph_node
        for suffix in ("_boneMorph", "_groupMorph", "_materialMorph"):
            if safe_name.endswith(suffix):
                safe_name = safe_name[: -len(suffix)]
                break
        register_morph_mapping(converter, safe_name, mapping)
        if metadata.name_english:
            register_morph_mapping(converter, metadata.name_english, mapping)
        converter.morph_bindings.setdefault(original_name, []).append({
            "source_plug": f"{morph_node}.weight",
            "morph_type": metadata.morph_type,
            "morph_index": metadata.index,
            "final_input_plugs": tuple(final_plugs),
        })


def get_original_morph_name_candidates(alias: str) -> List[str]:
    """Return original VMD/PMX morph name candidates for a Maya alias."""
    candidates = []
    if not alias:
        return candidates

    try:
        from mmd_tools.core.unicode_converter import get_converter

        converter = get_converter()
        for source_map in (converter.unicode_to_ascii, converter.exact_match):
            for original_name, converted_name in source_map.items():
                if converted_name == alias:
                    candidates.append(original_name)
    except Exception:
        pass

    unique_candidates = []
    for candidate in candidates:
        if candidate and candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates
