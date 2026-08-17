"""Shared category selector and stacked-page presentation component."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..qt_compat import QHBoxLayout, QPushButton, QStackedWidget, Qt, QVBoxLayout, QWidget, Signal


class CategoryStack(QWidget):
    """Expose a small, keyboard-friendly category selector over one stack.

    The component owns only category identity, button state, and the stacked
    index.  Page contents and their settings/action ownership remain with the
    Import or Export tab that creates them.
    """

    category_changed = Signal(str)

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
        self._indices = {}
        self._buttons = {}
        self._active_category = self._categories[0]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        selector = QWidget(self)
        selector.setObjectName(f"{object_name}Selector")
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(4)
        for category in self._categories:
            button = QPushButton(str(labels[category]), selector)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setObjectName(f"{object_name}{_pascal_case(category)}CategoryButton")
            button.setProperty("category", category)
            button.setAccessibleName(str(labels[category]))
            button.clicked.connect(
                lambda _checked=False, selected=category: self.set_current_category(selected)
            )
            selector_layout.addWidget(button, 1)
            self._buttons[category] = button
        layout.addWidget(selector)

        self.stacked_widget = QStackedWidget(self)
        self.stacked_widget.setObjectName(f"{object_name}Pages")
        self.stacked_widget.currentChanged.connect(self._on_stack_index_changed)
        # Small QTabWidget-like read-only/index helpers keep existing signal
        # probes and presenter integrations source-compatible while the
        # visible control remains a button selector over QStackedWidget.
        self.currentChanged = self.stacked_widget.currentChanged
        layout.addWidget(self.stacked_widget, 1)

    @property
    def categories(self) -> tuple[str, ...]:
        """Return the immutable category order used by the stack."""

        return self._categories

    @property
    def current_category(self) -> str:
        """Return the selected category identifier."""

        return self._active_category

    def button(self, category: str) -> QPushButton:
        """Return the selector button for a category."""

        return self._buttons[str(category)]

    def count(self) -> int:
        """Return the number of category pages."""

        return len(self._categories)

    def currentIndex(self) -> int:
        """Return the active stack index."""

        return self.stacked_widget.currentIndex()

    def setCurrentIndex(self, index: int) -> None:
        """Select a page by index for compatibility with bounded UI probes."""

        self.set_current_category(self._categories[int(index)])

    def tabText(self, index: int) -> str:
        """Return the selector label at an index."""

        return self.button(self._categories[int(index)]).text()

    def setTabText(self, index: int, text: str) -> None:
        """Update one selector label."""

        self.button(self._categories[int(index)]).setText(str(text))

    def add_page(self, category: str, page: QWidget) -> int:
        """Add a page and return its stack index."""

        key = str(category)
        if key not in self._categories or key in self._indices:
            raise ValueError(f"Unknown or duplicate category: {key}")
        index = self.stacked_widget.addWidget(page)
        self._indices[key] = index
        if len(self._indices) == 1:
            self.stacked_widget.setCurrentIndex(index)
            self._set_button_state(key)
        return index

    def set_current_category(self, category: str) -> None:
        """Select a category by identifier and emit exactly once on change."""

        key = str(category)
        if key not in self._indices:
            raise ValueError(f"Category has no page: {key}")
        if key == self._active_category and self.stacked_widget.currentIndex() == self._indices[key]:
            self._set_button_state(key)
            return
        self._active_category = key
        self.stacked_widget.setCurrentIndex(self._indices[key])
        self._set_button_state(key)
        self.category_changed.emit(key)

    def retranslate(self, labels: Mapping[str, str]) -> None:
        """Update button labels without changing the selected page."""

        for category, button in self._buttons.items():
            if category in labels:
                label = str(labels[category])
                button.setText(label)
                button.setAccessibleName(label)

    def _on_stack_index_changed(self, index: int) -> None:
        for category, category_index in self._indices.items():
            if category_index == index:
                self._active_category = category
                self._set_button_state(category)
                return

    def _set_button_state(self, active_category: str) -> None:
        for category, button in self._buttons.items():
            button.setChecked(category == active_category)
            button.setProperty("activeCategory", category == active_category)


def _pascal_case(value: str) -> str:
    """Convert a stable category id into an object-name segment."""

    return "".join(part[:1].upper() + part[1:] for part in value.split("_") if part)


__all__ = ["CategoryStack"]
