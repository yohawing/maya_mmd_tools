"""Resolve PMX display-frame metadata into picker-ready groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .display_frame_metadata import display_frames_from_json


@dataclass(frozen=True)
class PickerItem:
    """A single selectable element within a picker group."""

    element_type: int
    index: int
    resolved_name: str


@dataclass(frozen=True)
class PickerGroup:
    """A named group of picker items derived from a PMX display frame."""

    name: str
    name_english: str
    special_flag: int
    items: tuple[PickerItem, ...]


def resolve_display_frames(
    display_frames_json: Optional[str],
    bone_index_map: Dict[int, str],
    morph_index_map: Optional[Dict[int, str]] = None,
) -> List[PickerGroup]:
    """Build picker groups from display-frame metadata.

    Args:
        display_frames_json: Raw JSON string from ``mmd_display_frames_json``.
        bone_index_map: PMX bone index -> Maya joint long name.
        morph_index_map: PMX morph index -> Maya morph node/alias name.

    Returns:
        Ordered list of :class:`PickerGroup`.  Falls back to a single flat
        group of all bones when no valid display-frame metadata exists.
    """
    frames = display_frames_from_json(display_frames_json)
    if not frames:
        return _fallback_flat_list(bone_index_map)

    morph_map: Dict[int, str] = morph_index_map or {}
    groups: List[PickerGroup] = []
    for frame in frames:
        items = _resolve_elements(frame.get("elements", []), bone_index_map, morph_map)
        groups.append(
            PickerGroup(
                name=frame.get("name", ""),
                name_english=frame.get("name_english", ""),
                special_flag=int(frame.get("special_flag", 0)),
                items=tuple(items),
            )
        )
    return groups


def resolve_bone_items(groups: Sequence[PickerGroup]) -> List[PickerItem]:
    """Collect all bone-type items across groups."""
    return [item for group in groups for item in group.items if item.element_type == 0]


def resolve_morph_items(groups: Sequence[PickerGroup]) -> List[PickerItem]:
    """Collect all morph-type items across groups."""
    return [item for group in groups for item in group.items if item.element_type == 1]


def _resolve_elements(
    elements: list,
    bone_map: Dict[int, str],
    morph_map: Dict[int, str],
) -> List[PickerItem]:
    items: List[PickerItem] = []
    for elem in elements:
        if not isinstance(elem, dict):
            continue
        elem_type = elem.get("type", -1)
        index = elem.get("index", -1)
        if elem_type == 0:
            resolved = bone_map.get(index, "")
        elif elem_type == 1:
            resolved = morph_map.get(index, "")
        else:
            continue
        items.append(PickerItem(element_type=elem_type, index=index, resolved_name=resolved))
    return items


def _fallback_flat_list(bone_index_map: Dict[int, str]) -> List[PickerGroup]:
    if not bone_index_map:
        return []
    items = tuple(
        PickerItem(element_type=0, index=idx, resolved_name=name)
        for idx, name in sorted(bone_index_map.items())
    )
    return [
        PickerGroup(
            name="All Bones",
            name_english="All Bones",
            special_flag=0,
            items=items,
        )
    ]
