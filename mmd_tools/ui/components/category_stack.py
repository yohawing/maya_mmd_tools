"""Shared tab presentation component for categorized pages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..qt_compat import (
    QTabWidget,
    QWidget,
)


class CategoryStack(QTabWidget):
    """Expose the shared category pages as a tab widget.

    ``CategoryStack`` keeps the small keyed-page API used by the import and
    export tabs while delegating navigation and page ownership directly to
    ``QTabWidget``.  The old button/``QStackedWidget`` presentation was never
    used by production callers and is intentionally no longer supported.
    """

    def __init__(
        self,
        categories: Iterable[str],
        labels: Mapping[str, str],
        object_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._categories = tuple(str(category) for category in categories)
        if not self._categories or len(set(self._categories)) != len(self._categories):
            raise ValueError("CategoryStack requires unique categories")
        if any(category not in labels for category in self._categories):
            raise ValueError("CategoryStack labels must cover every category")
        self._labels = {category: str(labels[category]) for category in self._categories}
        self._indices = {}
        self._active_category = self._categories[0]
        self.currentChanged.connect(self._on_stack_index_changed)

    @property
    def categories(self) -> tuple[str, ...]:
        return self._categories

    @property
    def current_category(self) -> str:
        return self._active_category

    def set_category_visible(self, category: str, visible: bool) -> None:
        key = str(category)
        index = self._indices.get(key)
        if index is None:
            raise ValueError(f"Category has no page: {key}")
        if not visible and key == self._active_category:
            fallback = next(
                (candidate for candidate in self._categories if candidate != key),
                None,
            )
            if fallback is not None:
                self.set_current_category(fallback)
        tab_bar = self.tabBar()
        set_tab_visible = getattr(tab_bar, "setTabVisible", None)
        if callable(set_tab_visible):
            set_tab_visible(index, bool(visible))
        else:
            self.setTabEnabled(index, bool(visible))

    def add_page(self, category: str, page: QWidget) -> int:
        key = str(category)
        if key not in self._categories or key in self._indices:
            raise ValueError(f"Unknown or duplicate category: {key}")
        index = self.addTab(page, self._labels[key])
        self._indices[key] = index
        if len(self._indices) == 1:
            self.setCurrentIndex(index)
        return index

    def set_current_category(self, category: str) -> None:
        key = str(category)
        if key not in self._indices:
            raise ValueError(f"Category has no page: {key}")
        self.setCurrentIndex(self._indices[key])

    def retranslate(self, labels: Mapping[str, str]) -> None:
        for index, category in enumerate(self._categories):
            if category not in labels:
                continue
            label = str(labels[category])
            self._labels[category] = label
            if index < self.count():
                self.setTabText(index, label)

    def _on_stack_index_changed(self, index: int) -> None:
        for category, category_index in self._indices.items():
            if category_index == index:
                self._active_category = category
                return


__all__ = ["CategoryStack"]
