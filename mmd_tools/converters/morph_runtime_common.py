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
