"""Modal form for starting a new MMD model from a packaged template.

The dialog deliberately receives already validated template options from its
presenter.  It does not load template files or construct any skeleton data;
the selected opaque template identifier is returned to the action boundary.
"""

from __future__ import annotations

from typing import Iterable

from ..actions.create_model_action import CreateModelRequest
from ..core.model_template import ModelTemplateOption
from .qt_compat import QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from .translations.translator import UITranslator


class CreateModelDialog(QDialog):
    """Collect a packaged template and bilingual model names."""

    def __init__(self, templates: Iterable[ModelTemplateOption], parent=None):
        super().__init__(parent)
        translator = UITranslator.instance()
        self._translator = translator
        self.setWindowTitle(translator.translate("new_mmd_model", "actions"))
        self.setModal(True)

        form_layout = QFormLayout()
        self.template_combo = QComboBox(self)
        for template in tuple(templates or ()):
            template_id = getattr(template, "template_id", None)
            label = getattr(template, "label", None)
            if isinstance(template_id, str) and template_id and isinstance(label, str):
                self.template_combo.addItem(label, template_id)
        form_layout.addRow(
            QLabel(translator.translate("model_template", "fields"), self),
            self.template_combo,
        )

        self.model_name_jp_edit = QLineEdit(self)
        form_layout.addRow(
            QLabel(translator.translate("model_name_jp", "fields"), self),
            self.model_name_jp_edit,
        )

        self.model_name_en_edit = QLineEdit(self)
        form_layout.addRow(
            QLabel(translator.translate("model_name_en", "fields"), self),
            self.model_name_en_edit,
        )

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton(translator.translate("ok", "buttons"), self)
        self.cancel_button = QPushButton(translator.translate("cancel", "buttons"), self)
        self.ok_button.setEnabled(self.template_combo.count() > 0)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addLayout(button_layout)

    @property
    def selected_template_id(self) -> str | None:
        """Return the opaque packaged template identifier selected by the user."""
        value = self.template_combo.currentData()
        return value if isinstance(value, str) and value else None

    @property
    def model_name(self) -> str:
        """Return the Japanese model name entered by the user."""
        return self.model_name_jp_edit.text().strip()

    @property
    def model_name_english(self) -> str:
        """Return the English model name entered by the user."""
        return self.model_name_en_edit.text().strip()

    def exec_modal(self) -> bool:
        """Execute the modal using the available Qt binding."""
        exec_method = getattr(self, "exec", None) or getattr(self, "exec_", None)
        return bool(exec_method()) if callable(exec_method) else False

    def get_request(self) -> CreateModelRequest | None:
        """Return a validated action request, or ``None`` without a selection."""
        template_id = self.selected_template_id
        if not template_id:
            return None
        return CreateModelRequest(
            template_id=template_id,
            model_name=self.model_name,
            model_name_english=self.model_name_english,
        )


__all__ = ["CreateModelDialog"]
