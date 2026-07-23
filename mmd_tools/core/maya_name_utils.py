"""Maya-safe name normalization and deterministic allocation helpers."""

import re
from typing import Optional, Set

from . import utils


_MAYA_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_text(name):
    """
    Maya用に名前をサニタイズする。
    日本語などのマルチバイト文字をASCII文字に変換し、Maya互換の名前にする。
    """
    if not name:
        return "unnamed"

    converted_name = utils.convert_utf8_to_ascii(str(name))
    if not converted_name:
        return "default_name"

    # ``convert_utf8_to_ascii`` already performs dictionary-specific Maya
    # replacements.  Keep a final conservative pass here so callers cannot
    # accidentally create a namespace (``:``), DAG path, or punctuation name
    # when a custom dictionary entry contains an unsafe character.
    converted_name = re.sub(r"[^A-Za-z0-9_]", "_", str(converted_name))
    if not converted_name:
        return "default_name"
    if converted_name[0].isdigit():
        converted_name = f"name_{converted_name}"
    return converted_name if _MAYA_NAME_PATTERN.fullmatch(converted_name) else "default_name"


def sanitize_unique_name(name, used_names: Optional[Set[str]], fallback: str = "unnamed") -> str:
    """Return a safe name that is unique within a caller-owned name set.

    ``sanitize_text`` is intentionally context-free and therefore cannot
    distinguish two source names that map to the same ASCII spelling.  This
    helper keeps uniqueness policy at the caller boundary while preserving a
    deterministic ``_1``, ``_2`` suffix sequence.  The supplied set is
    updated with the returned name when it is mutable.

    Args:
        name: Raw source name to normalize.
        used_names: Names already allocated by the caller. ``None`` creates a
            private set and is useful for one-off safe-name conversion.
        fallback: Safe base used for empty or unusable source names.

    Returns:
        A Maya-safe ASCII identifier matching ``^[A-Za-z_][A-Za-z0-9_]*$``.
    """
    names = used_names if used_names is not None else set()
    base = sanitize_text(name)
    if base in {"unnamed", "default_name"} and name not in ("unnamed", "default_name"):
        fallback_name = sanitize_text(fallback)
        if fallback_name:
            base = fallback_name

    candidate = base
    index = 1
    while candidate in names:
        candidate = f"{base}_{index}"
        index += 1
    names.add(candidate)
    return candidate


def sanitize_bone_name(name):
    """Maya用にMMD/PMXボーン名をサニタイズする。"""
    if not name:
        return "unnamed"

    from .mmd_bone_names import convert_mmd_bone_name_to_ascii

    converted_name = convert_mmd_bone_name_to_ascii(name)
    if converted_name and converted_name[0].isdigit():
        return f"bone_{converted_name}"
    return converted_name or "default_name"
