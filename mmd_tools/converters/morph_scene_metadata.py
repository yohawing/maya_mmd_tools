"""Scene metadata readers for PMX morph nodes and blendShape raw names."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Optional

import maya.cmds as cmds

from ..core import maya_attribute_utils
from ..core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from ..core.namespace_utils import NamespaceUtils


@dataclass(frozen=True)
class MorphNetworkMetadata:
    """Read-only metadata stored on PMX morph network nodes."""

    node: str
    morph_type: str
    name: str
    name_english: str
    panel: int
    index: Optional[int]


def read_blendshape_morph_names(blend_shape_node: str, *, ensure_attr: bool = False) -> Dict[int, str]:
    """Read blendShape weight-index to raw PMX morph-name mapping."""
    if not maya_attribute_utils.attribute_exists(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
        if ensure_attr:
            cmds.addAttr(blend_shape_node, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
        return {}

    parsed = maya_attribute_utils.read_json_attr(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, default={})
    if not isinstance(parsed, dict):
        return {}

    result: Dict[int, str] = {}
    for key, value in parsed.items():
        try:
            result[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return result


def read_blendshape_morph_name_strings(
    blend_shape_node: str, *, ensure_attr: bool = False
) -> Dict[str, str]:
    """Read blendShape raw morph names with string weight indices for JSON updates."""
    return {
        str(index): raw_name
        for index, raw_name in read_blendshape_morph_names(
            blend_shape_node,
            ensure_attr=ensure_attr,
        ).items()
    }


def iter_morph_network_metadata(
    *,
    root_group: Optional[str] = None,
    morph_types: Optional[Iterable[str]] = None,
    required_attrs: Iterable[str] = (),
    enforce_model_root_scope: bool = True,
) -> Iterator[MorphNetworkMetadata]:
    """Iterate PMX morph network nodes with optional type and scope filters."""
    allowed_types = set(morph_types) if morph_types is not None else None
    required_attr_tuple = tuple(required_attrs)
    namespace = NamespaceUtils.get_namespace_from_node(root_group) if root_group else None

    for node in cmds.ls(type="network") or []:
        if namespace is not None and NamespaceUtils.get_namespace_from_node(node) != namespace:
            continue
        if not _has_attr(node, "mmd_morph_type"):
            continue

        morph_type = _get_string_attr(node, "mmd_morph_type")
        if not morph_type or (allowed_types is not None and morph_type not in allowed_types):
            continue
        if any(not _has_attr(node, attr) for attr in required_attr_tuple):
            continue
        if enforce_model_root_scope and root_group and _has_attr(node, "mmd_model_root"):
            connected_roots = cmds.listConnections(f"{node}.mmd_model_root") or []
            if root_group not in connected_roots:
                continue

        yield MorphNetworkMetadata(
            node=node,
            morph_type=morph_type,
            name=_get_string_attr(node, "mmd_morph_name"),
            name_english=_get_string_attr(node, "mmd_morph_name_en"),
            panel=_get_int_attr(node, "mmd_morph_panel", default=0),
            index=_get_optional_int_attr(node, "mmd_morph_index"),
        )


def _has_attr(node: str, attr: str) -> bool:
    return maya_attribute_utils.attribute_exists(node, attr)


def _get_string_attr(node: str, attr: str) -> str:
    if not _has_attr(node, attr):
        return ""
    return maya_attribute_utils.get_attr_safe(node, attr, default="") or ""


def _get_int_attr(node: str, attr: str, *, default: int = 0) -> int:
    value = _get_optional_int_attr(node, attr)
    return default if value is None else value


def _get_optional_int_attr(node: str, attr: str) -> Optional[int]:
    if not _has_attr(node, attr):
        return None
    return maya_attribute_utils.get_attr_safe(node, attr, default=None, cast=int)
