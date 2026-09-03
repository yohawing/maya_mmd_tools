"""Locale-aware display policy for bilingual PMX names.

The Japanese PMX name remains authoritative metadata.  This module only
chooses display labels/naming candidates and never changes stored names or Maya node
identity.
"""

from __future__ import annotations


def _clean(value: object) -> str:
    return str(value or "").strip()


def _ascii_candidate(value: object) -> str:
    text = _clean(value)
    return text if text and text.isascii() else ""


def material_english_name(name_jp: object, name_en: object) -> str:
    """Replace PMX's literal 'en' placeholder for naming/display only."""
    english = _clean(name_en)
    japanese = _clean(name_jp)
    if english.casefold() == "en" and japanese:
        from .maya_name_utils import sanitize_text

        return sanitize_text(japanese)
    return english


def preferred_pmx_display_name(
    name_jp: object,
    name_en: object = "",
    *,
    fallback: object = "",
    language: str = "ja",
    unnamed: str = "(unnamed)",
) -> str:
    """Return a PMX label suitable for the active UI language.

    English UI deliberately does not fall back to non-ASCII Japanese text.
    An ASCII original name or Maya leaf remains useful when EnglishName is
    empty; otherwise a neutral placeholder keeps the UI readable. A callable
    fallback is evaluated only when neither stored name can be displayed.
    """

    japanese = _clean(name_jp)
    english = _clean(name_en)
    primary = (english or _ascii_candidate(japanese)) if language == "en" else (japanese or english)
    if primary:
        return primary
    fallback_text = _clean(fallback() if callable(fallback) else fallback)
    if language == "en":
        return _ascii_candidate(fallback_text) or unnamed
    return fallback_text or unnamed


def original_pmx_fields_visible(_language: str) -> bool:
    """Keep authoritative original PMX metadata editable in every UI language."""

    return True


def morph_name_fallback(name: object, index: object, *, include_index: bool = False) -> str:
    """Use the import sanitizer for unnamed-in-English morphs, retaining metadata."""
    from .unicode_converter import get_converter

    original = _clean(name)
    if not original:
        return f"Morph {index}"
    sanitized = get_converter().convert(original)
    return f"{sanitized} [{index}]" if include_index else sanitized


__all__ = ["material_english_name", "morph_name_fallback", "original_pmx_fields_visible", "preferred_pmx_display_name"]
