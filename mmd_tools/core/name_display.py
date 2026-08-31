"""Locale-aware display policy for bilingual PMX names.

The Japanese PMX name remains authoritative metadata.  This module only
chooses a user-facing label and never changes the stored names or Maya node
identity.
"""

from __future__ import annotations


def _clean(value: object) -> str:
    return str(value or "").strip()


def _ascii_candidate(value: object) -> str:
    text = _clean(value)
    return text if text and text.isascii() else ""


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
    empty; otherwise a neutral placeholder keeps the UI readable.
    """

    japanese = _clean(name_jp)
    english = _clean(name_en)
    fallback_text = _clean(fallback)
    if language == "en":
        return english or _ascii_candidate(japanese) or _ascii_candidate(fallback_text) or unnamed
    return japanese or english or fallback_text or unnamed


def original_pmx_fields_visible(_language: str) -> bool:
    """Keep authoritative original PMX metadata editable in every UI language."""

    return True


__all__ = ["original_pmx_fields_visible", "preferred_pmx_display_name"]
