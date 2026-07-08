"""Shared helper for tab-local declarative translation registries."""

from typing import Any, Iterable, Tuple


TranslationEntry = Tuple[str, str, str, str]


def apply_translation_registry(tab: Any, registry: Iterable[TranslationEntry]) -> None:
    """Apply ``(attribute, setter, key, category)`` entries to widgets on a tab."""
    for attr_name, setter_name, key, category in registry:
        widget = getattr(tab, attr_name)
        setter = getattr(widget, setter_name)
        setter(tab.tr(key, category))
