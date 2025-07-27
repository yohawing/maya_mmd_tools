from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QGroupBox,
    QTextEdit,
    QLabel,
    QComboBox,
    QPushButton,
    QHBoxLayout,
)
from ..base_tab import BaseTab


class InfoTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoTab")

        main_layout = QVBoxLayout(self)
        
        # モデル選択セクション
        model_select_layout = QHBoxLayout()
        self.current_model_label = QLabel(self.tr("current_model", "fields"))
        model_select_layout.addWidget(self.current_model_label)
        self.model_combo = QComboBox()
        self.refresh_button = QPushButton(self.tr("refresh", "buttons"))
        self.refresh_button.setMaximumWidth(100)
        model_select_layout.addWidget(self.model_combo)
        model_select_layout.addWidget(self.refresh_button)
        main_layout.addLayout(model_select_layout)

        # モデル情報セクション
        self.info_group = QGroupBox(self.tr("model_information", "groups"))
        info_layout = QFormLayout()

        self.model_name_jp_edit = QLineEdit()
        self.model_name_en_edit = QLineEdit()
        self.comment_jp_edit = QTextEdit()
        self.comment_en_edit = QTextEdit()

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
        self.info_label = QLabel(
            self.tr("no_model_loaded", "placeholders")
        )
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: gray; font-style: italic;")
        main_layout.addWidget(self.info_label)

        main_layout.addStretch()

        # 初期状態では編集不可
        self.set_fields_enabled(False)
        
        # モデルコンボボックスの初期状態
        self.model_combo.addItem(self.tr("no_mmd_models", "placeholders"))

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
        if hasattr(self, 'current_model_label'):
            self.current_model_label.setText(self.tr("current_model", "fields"))
        if hasattr(self, 'comment_jp_label'):
            self.comment_jp_label.setText(self.tr("comment_jp", "fields"))
        if hasattr(self, 'comment_en_label'):
            self.comment_en_label.setText(self.tr("comment_en", "fields"))
        
        # Buttons
        self.refresh_button.setText(self.tr("refresh", "buttons"))
        
        # GroupBox
        if hasattr(self, 'info_group'):
            self.info_group.setTitle(self.tr("model_information", "groups"))
        
        # FormLayout labels
        if hasattr(self, 'info_group'):
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
        
        # Refresh combo box placeholder text if no models
        if self.model_combo.count() == 1 and self.model_combo.currentData() is None:
            self.model_combo.setItemText(0, self.tr("no_mmd_models", "placeholders"))
