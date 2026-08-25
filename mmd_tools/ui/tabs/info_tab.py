from ..qt_compat import (
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QGroupBox,
    QTextEdit,
    QLabel,
    QObject,
    Signal,
)
from ..base_tab import BaseTab


class _InfoEditEventFilter(QObject):
    """Emit deterministic edit-session boundaries for both text widgets.

    ``QLineEdit`` and ``QTextEdit`` expose different editing signals, so the
    presenter must not infer an undo transaction from ``textChanged`` or use a
    timer.  Focus transitions are common to both widgets and provide the
    smallest stable seam for an Info metadata edit session.
    """

    # QEvent.FocusIn / FocusOut are 8 / 9 in both Qt 5 and Qt 6.  Keeping the
    # numeric values here avoids importing QEvent, which is intentionally not
    # part of the lightweight headless Qt compatibility stubs.
    _FOCUS_IN = 8
    _FOCUS_OUT = 9

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def eventFilter(self, watched, event):  # noqa: N802 - Qt virtual method
        try:
            event_type = int(event.type())
        except (AttributeError, TypeError, ValueError):
            event_type = None

        if event_type == self._FOCUS_IN:
            self.owner.edit_started.emit(watched)
        elif event_type == self._FOCUS_OUT:
            self.owner.edit_finished.emit(watched)

        try:
            return super().eventFilter(watched, event)
        except AttributeError:
            # The pure-Python Qt stubs do not implement QObject.eventFilter.
            return False


class InfoTab(BaseTab):
    """Model metadata editor with shared focus-based edit-session signals."""

    edit_started = Signal(object)
    edit_finished = Signal(object)
    teardown = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoTab")

        main_layout = QVBoxLayout(self)

        # モデル情報セクション
        self.info_group = QGroupBox(self.tr("model_information", "groups"))
        info_layout = QFormLayout()

        self.model_name_jp_edit = QLineEdit()
        self.model_name_en_edit = QLineEdit()
        self.comment_jp_edit = QTextEdit()
        self.comment_en_edit = QTextEdit()

        self.editable_fields = (
            self.model_name_jp_edit,
            self.model_name_en_edit,
            self.comment_jp_edit,
            self.comment_en_edit,
        )
        self._edit_event_filter = _InfoEditEventFilter(self)
        for widget in self.editable_fields:
            widget.installEventFilter(self._edit_event_filter)

        # コメントフィールドの高さを制限
        self.comment_jp_edit.setMaximumHeight(100)
        self.comment_en_edit.setMaximumHeight(100)

        # モデル名
        info_layout.addRow(self.tr("model_name_jp", "fields"), self.model_name_jp_edit)
        info_layout.addRow(self.tr("model_name_en", "fields"), self.model_name_en_edit)

        # コメントは縦に配置
        self.comment_jp_label = QLabel(self.tr("comment_jp", "fields"))
        info_layout.addRow(self.comment_jp_label)
        info_layout.addRow(self.comment_jp_edit)
        self.comment_en_label = QLabel(self.tr("comment_en", "fields"))
        info_layout.addRow(self.comment_en_label)
        info_layout.addRow(self.comment_en_edit)

        self.info_group.setLayout(info_layout)
        main_layout.addWidget(self.info_group)

        # 初期状態のメッセージ
        self.info_label = QLabel(self.tr("no_model_loaded", "placeholders"))
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: gray; font-style: italic;")
        main_layout.addWidget(self.info_label)

        main_layout.addStretch()

        # 初期状態では編集不可
        self.set_fields_enabled(False)

    def closeEvent(self, event):  # noqa: N802 - Qt virtual method
        """Notify the presenter before the tab is torn down."""
        self.teardown.emit()
        try:
            super().closeEvent(event)
        except AttributeError:
            if hasattr(event, "accept"):
                event.accept()

    def set_fields_enabled(self, enabled):
        """フィールドの編集可否を設定"""
        self.model_name_jp_edit.setEnabled(enabled)
        self.model_name_en_edit.setEnabled(enabled)
        self.comment_jp_edit.setEnabled(enabled)
        self.comment_en_edit.setEnabled(enabled)

        # モデルがロードされたらメッセージを非表示
        self.info_label.setVisible(not enabled)

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        # Labels
        if hasattr(self, "comment_jp_label"):
            self.comment_jp_label.setText(self.tr("comment_jp", "fields"))
        if hasattr(self, "comment_en_label"):
            self.comment_en_label.setText(self.tr("comment_en", "fields"))

        # GroupBox
        if hasattr(self, "info_group"):
            self.info_group.setTitle(self.tr("model_information", "groups"))

        # FormLayout labels
        if hasattr(self, "info_group"):
            info_layout = self.info_group.layout()
            if info_layout:
                label = info_layout.labelForField(self.model_name_jp_edit)
                if label:
                    label.setText(self.tr("model_name_jp", "fields"))
                label = info_layout.labelForField(self.model_name_en_edit)
                if label:
                    label.setText(self.tr("model_name_en", "fields"))

        # Info label
        self.info_label.setText(self.tr("no_model_loaded", "placeholders"))
