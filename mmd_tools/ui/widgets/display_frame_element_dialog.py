"""Modal selector for adding a PMX bone or morph to a display frame.

The dialog owns only selection state.  It never edits display-frame metadata;
the presenter receives the selected ``{type, index}`` identity after the
dialog is accepted and applies that value to its working copy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..qt_compat import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    Qt,
)
from ..translations.translator import UITranslator


class DisplayFrameElementDialog(QDialog):
    """Choose one display-frame element by type and PMX index.

    Args:
        candidates: Candidate mappings with ``type``, ``index`` and ``name``.
            A candidate may include ``disabled`` and ``disabled_reason`` to
            explain an existing duplicate without making it selectable.
        allowed_types: Element types available for this frame (0 = bone,
            1 = morph).  A one-item sequence locks the type selector, which
            is used for the Facial special frame.
        parent: Optional Qt parent widget.
    """

    def __init__(
        self,
        candidates: Iterable[Mapping[str, object]] = (),
        allowed_types: Iterable[int] = (0, 1),
        parent=None,
    ):
        super().__init__(parent)
        self._translator = UITranslator.instance()
        self._allowed_types = tuple(dict.fromkeys(int(value) for value in allowed_types))
        self._candidates = tuple(self._coerce_candidate(candidate) for candidate in candidates)
        self._selected_element: dict[str, int] | None = None

        self.setModal(True)
        self.setWindowTitle(self._tr("add_element", "buttons"))

        layout = QVBoxLayout(self)
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel(self._tr("element_type", "fields"), self))
        self.element_type_combo = QComboBox(self)
        self.type_combo = self.element_type_combo
        for element_type in self._allowed_types:
            self.element_type_combo.addItem(self._type_label(element_type), element_type)
        self.element_type_combo.setEnabled(len(self._allowed_types) > 1)
        self.element_type_combo.currentIndexChanged.connect(self._refresh_candidates)
        type_layout.addWidget(self.element_type_combo, 1)
        layout.addLayout(type_layout)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(self._tr("search", "fields"), self))
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(self._tr("search_display_element", "placeholders"))
        self.search_edit.textChanged.connect(self._refresh_candidates)
        search_layout.addWidget(self.search_edit, 1)
        layout.addLayout(search_layout)

        self.candidate_list = QListWidget(self)
        self.candidate_list.currentRowChanged.connect(self._on_candidate_selected)
        layout.addWidget(self.candidate_list, 1)

        self.pmx_index_label = QLabel(self._tr("pmx_index_unselected", "labels"), self)
        layout.addWidget(self.pmx_index_label)
        self.validation_label = QLabel(self)
        layout.addWidget(self.validation_label)

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton(self._tr("ok", "buttons"), self)
        self.cancel_button = QPushButton(self._tr("cancel", "buttons"), self)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._accept_selection)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self._refresh_candidates()

    @property
    def selected_element(self) -> dict[str, int] | None:
        """Return the selected ``type`` + PMX ``index`` identity."""

        return dict(self._selected_element) if self._selected_element is not None else None

    @property
    def selected_identity(self) -> dict[str, int] | None:
        """Compatibility alias for callers that use identity terminology."""

        return self.selected_element

    def exec_modal(self) -> bool:
        """Execute the modal dialog on either PySide6 or PySide2."""

        exec_method = getattr(self, "exec", None) or getattr(self, "exec_", None)
        return bool(exec_method()) if callable(exec_method) else False

    def _accept_selection(self) -> None:
        item = self.candidate_list.currentItem()
        if item is None or not item.isSelected() or not item.flags() & Qt.ItemIsEnabled:
            self.validation_label.setText(self._tr("select_display_element", "messages"))
            return
        value = item.data(Qt.UserRole)
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            self.validation_label.setText(self._tr("invalid_display_element", "messages"))
            return
        try:
            element_type, index = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            self.validation_label.setText(self._tr("invalid_display_element", "messages"))
            return
        if element_type not in self._allowed_types or index < 0:
            self.validation_label.setText(self._tr("invalid_display_element", "messages"))
            return
        self._selected_element = {"type": element_type, "index": index}
        self.accept()

    def _refresh_candidates(self, *_args) -> None:
        selected_type = self._selected_type()
        query = self.search_edit.text().strip().casefold()
        self.candidate_list.clear()
        for candidate in self._candidates:
            if candidate["type"] != selected_type:
                continue
            index_text = str(candidate["index"])
            if query and query not in candidate["name"].casefold() and query not in index_text:
                continue
            label = f'{candidate["name"]} [{candidate["index"]}]'
            if candidate["disabled"]:
                label += f' ({candidate["disabled_reason"]})'
            item = QListWidgetItem(label, self.candidate_list)
            item.setData(Qt.UserRole, (candidate["type"], candidate["index"]))
            if candidate["disabled"]:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
                item.setToolTip(candidate["disabled_reason"])
        self.validation_label.setText(
            "" if self.candidate_list.count() else self._tr("no_display_element_candidates", "messages")
        )
        self._on_candidate_selected(self.candidate_list.currentRow())

    def _on_candidate_selected(self, row: int) -> None:
        item = self.candidate_list.item(row) if row >= 0 else None
        value = item.data(Qt.UserRole) if item is not None else None
        enabled = bool(item is not None and item.flags() & Qt.ItemIsEnabled)
        if isinstance(value, (tuple, list)) and len(value) == 2 and enabled:
            self.pmx_index_label.setText(
                f'{self._tr("pmx_index", "labels")} {int(value[1])}'
            )
        else:
            self.pmx_index_label.setText(self._tr("pmx_index_unselected", "labels"))
        self.ok_button.setEnabled(enabled)

    def _selected_type(self) -> int:
        if not self._allowed_types:
            return -1
        value = self.element_type_combo.currentData()
        try:
            return int(value)
        except (TypeError, ValueError):
            return self._allowed_types[0]

    def _type_label(self, element_type: int) -> str:
        key = "display_element_type_bone" if element_type == 0 else "display_element_type_morph"
        return self._tr(key, "fields")

    def _tr(self, key: str, category: str) -> str:
        return self._translator.translate(key, category)

    @staticmethod
    def _coerce_candidate(candidate: Mapping[str, object]) -> dict[str, object]:
        try:
            element_type = int(candidate.get("type", -1))
            index = int(candidate.get("index", -1))
        except (TypeError, ValueError, AttributeError):
            element_type, index = -1, -1
        name = str(candidate.get("name", "")) if isinstance(candidate, Mapping) else ""
        return {
            "type": element_type,
            "index": index,
            "name": name,
            "disabled": bool(candidate.get("disabled", False)) if isinstance(candidate, Mapping) else True,
            "disabled_reason": str(candidate.get("disabled_reason", "")) if isinstance(candidate, Mapping) else "",
        }


__all__ = ["DisplayFrameElementDialog"]
