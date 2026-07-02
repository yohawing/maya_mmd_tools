"""Shared helpers for list/detail presenters."""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence

from ..translations import UITranslator


def _normalise_terms(terms: Iterable[object]) -> list[str]:
    return [str(term).lower() for term in terms if term not in (None, "")]


def text_matches_query(query: str, terms: Iterable[object]) -> bool:
    """Return whether any search term contains the query."""
    query = (query or "").lower()
    if not query:
        return True
    return any(query in term for term in _normalise_terms(terms))


def apply_list_filter(
    items: Iterable[object],
    query: str,
    terms_for_item: Callable[[object], Iterable[object]],
    always_hidden: Optional[Callable[[object], bool]] = None,
) -> None:
    """Set each list item's hidden state from a search query."""
    for item in items:
        if always_hidden is not None and always_hidden(item):
            item.setHidden(True)
            continue
        item.setHidden(not text_matches_query(query, terms_for_item(item)))


def select_existing_user_role_nodes(
    list_widget,
    maya_adapter,
    role,
    exists: Callable[[str], bool],
    logger=None,
    label: str = "nodes",
) -> Sequence[str]:
    """Select existing Maya nodes stored in selected list items' user data."""
    selected_items = list_widget.selectedItems()
    if not selected_items:
        return []

    nodes = []
    for item in selected_items:
        node = item.data(role)
        if node and exists(node):
            nodes.append(node)

    if not nodes:
        return []

    try:
        maya_adapter.select(nodes, replace=True)
        if logger is not None:
            logger.debug("Selected %s in Maya: %s", label, nodes)
    except Exception as exc:
        if logger is not None:
            logger.warning("Could not select %s in Maya: %s", label, exc)
        return []
    return nodes


def reload_for_current_model_change(logger, presenter_name: str, model_root, reload_callback: Callable[[], None]) -> None:
    """Log a model change and run the presenter's reload callback."""
    logger.info("%s: Current model changed to %s", presenter_name, model_root)
    reload_callback()


def tr_message(key: str) -> str:
    """Translate a presenter message key."""
    return UITranslator.instance().translate(key, "messages")


def tr_message_format(key: str, **kwargs) -> str:
    """Translate and format a presenter message template."""
    return tr_message(key).format(**kwargs)
