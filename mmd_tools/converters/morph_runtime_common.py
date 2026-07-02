"""Common helpers for PMX morph runtime graph builders."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from maya import cmds


def parse_morph_offsets_json(morph_node: str, attr_name: str) -> Optional[List[Dict[str, Any]]]:
    """Read a PMX morph offsets JSON attribute as a list of offset dicts.

    Args:
        morph_node: Maya network node containing PMX morph metadata.
        attr_name: String attribute containing JSON-encoded offsets.

    Returns:
        Parsed offsets, or None when the attribute does not contain a JSON list.
    """
    try:
        raw = cmds.getAttr(f"{morph_node}.{attr_name}") or "[]"
        offsets = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(offsets, list):
        return None
    return offsets


def get_morph_order(morph_node: str) -> int:
    """Return the PMX morph index used to keep runtime evaluation deterministic."""
    if cmds.attributeQuery("mmd_morph_index", node=morph_node, exists=True):
        try:
            return int(cmds.getAttr(f"{morph_node}.mmd_morph_index"))
        except Exception:
            pass
    return 0


def connect_if_needed(source: str, destination: str, force: bool = False) -> None:
    """Connect two plugs unless the destination already receives the source."""
    if is_connected(source, destination):
        return
    cmds.connectAttr(source, destination, force=force)


def is_connected(source: str, destination: str) -> bool:
    """Return whether destination already has source connected."""
    return any(same_source(conn, source) for conn in cmds.listConnections(destination, s=True, d=False, p=True) or [])


def same_source(left: str, right: str) -> bool:
    """Compare two plugs while tolerating short DAG names."""
    if left == right:
        return True
    left_long = cmds.ls(left, long=True) or []
    right_long = cmds.ls(right, long=True) or []
    return bool(left_long and right_long and left_long == right_long)
