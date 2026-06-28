"""Morph name mapping helpers for VMD animation conversion."""

import json
from typing import Dict, List, Tuple

import maya.cmds as cmds

from ..core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON


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


def read_blendshape_morph_names(blend_shape_node: str) -> Dict[int, str]:
    """Read stored weight index to raw PMX morph name mapping from a blendShape."""
    result: Dict[int, str] = {}
    if not cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=blend_shape_node, exists=True):
        return result
    try:
        raw = cmds.getAttr(f"{blend_shape_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return result
    if not isinstance(parsed, dict):
        return result
    for key, value in parsed.items():
        try:
            result[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return result


def build_morph_mappings(converter) -> None:
    """Build morph name mappings from scene blendShapes and metadata networks."""
    converter.morph_name_mapping = {}

    blend_shapes = cmds.ls(type="blendShape") or []
    for bs_node in blend_shapes:
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

    for morph_node in cmds.ls(type="network") or []:
        if not cmds.attributeQuery("mmd_morph_type", node=morph_node, exists=True):
            continue
        morph_type = cmds.getAttr(f"{morph_node}.mmd_morph_type")
        if morph_type not in {"bone", "group", "material"}:
            continue
        if not cmds.attributeQuery("weight", node=morph_node, exists=True):
            continue

        original_name = ""
        if cmds.attributeQuery("mmd_morph_name", node=morph_node, exists=True):
            original_name = cmds.getAttr(f"{morph_node}.mmd_morph_name") or ""
        if not original_name:
            continue

        mapping = (morph_node, "weight", original_name)
        register_morph_mapping(converter, original_name, mapping)
        safe_name = morph_node
        for suffix in ("_boneMorph", "_groupMorph", "_materialMorph"):
            if safe_name.endswith(suffix):
                safe_name = safe_name[: -len(suffix)]
                break
        register_morph_mapping(converter, safe_name, mapping)
        if cmds.attributeQuery("mmd_morph_name_en", node=morph_node, exists=True):
            english_name = cmds.getAttr(f"{morph_node}.mmd_morph_name_en") or ""
            if english_name:
                register_morph_mapping(converter, english_name, mapping)


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
