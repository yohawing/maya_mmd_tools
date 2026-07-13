"""PMX display-frame metadata serialization helpers."""

from __future__ import annotations

import json
from typing import Any, Iterable


def display_frame_to_dict(frame: Any) -> dict[str, Any]:
    """Convert a PmxDisplayFrame-like object to exporter metadata."""
    return normalize_display_frame_dict(
        {
            "name": getattr(frame, "name", ""),
            "name_english": getattr(frame, "name_english", ""),
            "special_flag": getattr(frame, "special_flag", 0),
            "elements": list(getattr(frame, "elements", []) or []),
        }
    )


def display_frames_to_dicts(frames: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert display-frame objects to JSON-safe dicts."""
    return [display_frame_to_dict(frame) for frame in frames or []]


def display_frames_to_json(frames: Iterable[Any]) -> str:
    """Serialize display-frame objects for storage on the Maya model root."""
    return json.dumps(display_frames_to_dicts(frames), ensure_ascii=False, separators=(",", ":"))


def display_frames_from_json(raw_value: str | None) -> list[dict[str, Any]]:
    """Parse root-node display-frame metadata, returning an empty list on invalid input."""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [normalize_display_frame_dict(frame) for frame in parsed if isinstance(frame, dict)]


def normalize_display_frame_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized display-frame dict accepted by PmxExporter."""
    elements = []
    for element in raw.get("elements", []) or []:
        if not isinstance(element, dict):
            continue
        try:
            element_type = int(element.get("type", 0))
            index = int(element.get("index", -1))
        except (TypeError, ValueError):
            continue
        if element_type not in (0, 1):
            continue
        elements.append({"type": element_type, "index": index})

    return {
        "name": str(raw.get("name", "")),
        "name_english": str(raw.get("name_english", raw.get("englishName", ""))),
        "special_flag": 1 if int(raw.get("special_flag", 1 if raw.get("special") else 0)) else 0,
        "elements": elements,
    }
