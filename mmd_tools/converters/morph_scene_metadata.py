"""Scene metadata readers for PMX morph nodes and blendShape raw names."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Optional

import maya.cmds as cmds

from ..core import maya_attribute_utils
from ..core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON
from ..core.logger import get_logger
from ..core.morph_metadata_reader import parse_blendshape_morph_entries, parse_blendshape_morph_names


logger = get_logger(__name__)


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
    return parse_blendshape_morph_names(parsed)


def read_blendshape_morph_entries(
    blend_shape_node: str, *, ensure_attr: bool = False
) -> Dict[int, Dict[str, object]]:
    """Read lossless entries while accepting the legacy name-only schema."""
    if not maya_attribute_utils.attribute_exists(blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON):
        if ensure_attr:
            cmds.addAttr(blend_shape_node, longName=ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, dataType="string")
        return {}
    parsed = maya_attribute_utils.read_json_attr(
        blend_shape_node, ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, default={}
    )
    return parse_blendshape_morph_entries(parsed)


def read_blendshape_morph_entry_strings(
    blend_shape_node: str, *, ensure_attr: bool = False
) -> Dict[str, Dict[str, object]]:
    """Return lossless entries with JSON-compatible string weight keys."""
    return {
        str(index): entry
        for index, entry in read_blendshape_morph_entries(
            blend_shape_node, ensure_attr=ensure_attr
        ).items()
    }


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
) -> Iterator[MorphNetworkMetadata]:
    """Iterate PMX morph network nodes, failing closed under an explicit root."""
    allowed_types = set(morph_types) if morph_types is not None else None
    required_attr_tuple = tuple(required_attrs)
    requested_root = _canonical_dag_root(root_group) if root_group is not None else None
    warned_nodes = set()

    for node in cmds.ls(type="network") or []:
        if not _has_attr(node, "mmd_morph_type"):
            continue

        morph_type = _get_string_attr(node, "mmd_morph_type")
        if not morph_type or (allowed_types is not None and morph_type not in allowed_types):
            continue
        if any(not _has_attr(node, attr) for attr in required_attr_tuple):
            continue
        if root_group is not None:
            if not _has_attr(node, "mmd_model_root"):
                _warn_migration_required(node, root_group, "missing mmd_model_root", warned_nodes)
                continue
            connected_roots = cmds.listConnections(
                f"{node}.mmd_model_root",
                source=True,
                destination=False,
            ) or []
            if len(connected_roots) != 1:
                reason = "missing root connection" if not connected_roots else "multiple root connections"
                _warn_migration_required(node, root_group, reason, warned_nodes)
                continue
            connected_root = _canonical_dag_root(connected_roots[0])
            if requested_root is None or connected_root is None:
                _warn_migration_required(node, root_group, "invalid root connection", warned_nodes)
                continue
            if connected_root != requested_root:
                continue

        yield MorphNetworkMetadata(
            node=node,
            morph_type=morph_type,
            name=_get_string_attr(node, "mmd_morph_name"),
            name_english=_get_string_attr(node, "mmd_morph_name_en"),
            panel=_get_int_attr(node, "mmd_morph_panel", default=0),
            index=_get_optional_int_attr(node, "mmd_morph_index"),
        )


def _canonical_dag_root(node: Optional[str]) -> Optional[str]:
    if not node or not cmds.objExists(node):
        return None
    matches = cmds.ls(node, long=True) or []
    if len(matches) != 1 or not matches[0].startswith("|"):
        return None
    return matches[0]


def _warn_migration_required(
    node: str,
    requested_root: str,
    reason: str,
    warned_nodes: set,
) -> None:
    if node in warned_nodes:
        return
    warned_nodes.add(node)
    logger.warning(
        "Skipping legacy morph network %s for requested root %s: migration required (%s)",
        node,
        requested_root,
        reason,
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
