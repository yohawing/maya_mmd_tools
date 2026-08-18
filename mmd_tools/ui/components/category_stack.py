"""Shared category selector and stacked-page presentation component."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..qt_compat import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    Qt,
    QVBoxLayout,
    QWidget,
    Signal,
)


class CategoryStack(QWidget):
    """Expose a category selector over one stack.

    ``buttons`` preserves the compact selector used by Export. ``tabs`` uses a
    conventional QTabWidget navigation for screens where the button selector
    is too prominent. Page contents and settings/action ownership remain with
    the Import or Export tab that creates them.
    """

    category_changed = Signal(str)

    def __init__(
        self,
        categories: Iterable[str],
        labels: Mapping[str, str],
        object_name: str,
        parent=None,
        navigation: str = "buttons",
    ):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._categories = tuple(str(category) for category in categories)
        if not self._categories or len(set(self._categories)) != len(self._categories):
            raise ValueError("CategoryStack requires unique categories")
        if any(category not in labels for category in self._categories):
            raise ValueError("CategoryStack labels must cover every category")
        self._navigation = str(navigation)
        if self._navigation not in {"buttons", "tabs"}:
            raise ValueError("CategoryStack navigation must be buttons or tabs")
        self._labels = {category: str(labels[category]) for category in self._categories}
        self._indices = {}
        self._buttons = {}
        self._active_category = self._categories[0]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if self._navigation == "buttons":
            selector = QWidget(self)
            selector.setObjectName(f"{object_name}Selector")
            selector_layout = QHBoxLayout(selector)
            selector_layout.setContentsMargins(0, 0, 0, 0)
            selector_layout.setSpacing(4)
            for category in self._categories:
                button = QPushButton(self._labels[category], selector)
                button.setCheckable(True)
                button.setFocusPolicy(Qt.StrongFocus)
                button.setObjectName(f"{object_name}{_pascal_case(category)}CategoryButton")
                button.setProperty("category", category)
                button.setAccessibleName(self._labels[category])
                button.clicked.connect(
                    lambda _checked=False, selected=category: self.set_current_category(selected)
                )
                selector_layout.addWidget(button, 1)
                self._buttons[category] = button
            layout.addWidget(selector)

        self.stacked_widget = (
            QTabWidget(self) if self._navigation == "tabs" else QStackedWidget(self)
        )
        self.stacked_widget.setObjectName(f"{object_name}Pages")
        self.stacked_widget.currentChanged.connect(self._on_stack_index_changed)
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

    def set_category_visible(self, category: str, visible: bool) -> None:
        """Show or hide one category in either navigation mode."""

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
        if self._navigation == "tabs":
            tab_bar = self.stacked_widget.tabBar()
            set_tab_visible = getattr(tab_bar, "setTabVisible", None)
            if callable(set_tab_visible):
                set_tab_visible(index, bool(visible))
            else:
                self.stacked_widget.setTabEnabled(index, bool(visible))
        else:
            self._buttons[key].setVisible(bool(visible))

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

        if self._navigation == "tabs":
            return self.stacked_widget.tabText(int(index))
        return self.button(self._categories[int(index)]).text()

    def setTabText(self, index: int, text: str) -> None:
        """Update one selector label."""

        if self._navigation == "tabs":
            self.stacked_widget.setTabText(int(index), str(text))
        else:
            self.button(self._categories[int(index)]).setText(str(text))

    def add_page(self, category: str, page: QWidget) -> int:
        """Add a page and return its stack index."""

        key = str(category)
        if key not in self._categories or key in self._indices:
            raise ValueError(f"Unknown or duplicate category: {key}")
        if self._navigation == "tabs":
            index = self.stacked_widget.addTab(page, self._labels[key])
        else:
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
        """Update navigation labels without changing the selected page."""

        for index, category in enumerate(self._categories):
            if category not in labels:
                continue
            label = str(labels[category])
            self._labels[category] = label
            if self._navigation == "tabs":
                self.stacked_widget.setTabText(index, label)
            else:
                button = self._buttons[category]
                button.setText(label)
                button.setAccessibleName(label)

    def _on_stack_index_changed(self, index: int) -> None:
        for category, category_index in self._indices.items():
            if category_index == index:
                self._active_category = category
                self._set_button_state(category)
                return

    def _set_button_state(self, active_category: str) -> None:
        if self._navigation != "buttons":
            return
        for category, button in self._buttons.items():
            button.setChecked(category == active_category)
            button.setProperty("activeCategory", category == active_category)


def _pascal_case(value: str) -> str:
    """Convert a stable category id into an object-name segment."""

    return "".join(part[:1].upper() + part[1:] for part in value.split("_") if part)


__all__ = ["CategoryStack"]
