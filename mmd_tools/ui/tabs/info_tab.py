from ..qt_compat import QWidget, QVBoxLayout, QFormLayout, QLineEdit, QGroupBox, QTextEdit, QLabel, QComboBox, QPushButton, QHBoxLayout
from ..base_tab import BaseTab

class InfoTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoTab")

        main_layout = QVBoxLayout(self)
        
        # モデル選択セクション
        model_select_group = QGroupBox("Model Selection")
        model_select_layout = QHBoxLayout()
        
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(200)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setMaximumWidth(100)
        
        model_select_layout.addWidget(QLabel("Current Model:"))
        model_select_layout.addWidget(self.model_combo)
        model_select_layout.addWidget(self.refresh_button)
        model_select_layout.addStretch()
        
        model_select_group.setLayout(model_select_layout)
        main_layout.addWidget(model_select_group)

        # モデル情報セクション
        info_group = QGroupBox("Model Information")
        info_layout = QFormLayout()

        self.model_name_jp_edit = QLineEdit()
        self.model_name_en_edit = QLineEdit()
        self.comment_jp_edit = QTextEdit()
        self.comment_en_edit = QTextEdit()
        
        # コメントフィールドの高さを制限
        self.comment_jp_edit.setMaximumHeight(100)
        self.comment_en_edit.setMaximumHeight(100)

        # モデル名
        info_layout.addRow("Model Name (JP):", self.model_name_jp_edit)
        info_layout.addRow("Model Name (EN):", self.model_name_en_edit)
        
        # コメントは縦に配置
        info_layout.addRow(QLabel("Comment (JP):"))
        info_layout.addRow(self.comment_jp_edit)
        info_layout.addRow(QLabel("Comment (EN):"))
        info_layout.addRow(self.comment_en_edit)

        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)
        
        # 初期状態のメッセージ
        self.info_label = QLabel("No model loaded. Import a PMX/PMD file to see model information.")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: gray; font-style: italic;")
        main_layout.addWidget(self.info_label)

        main_layout.addStretch()
        
        # 初期状態では編集不可
        self.set_fields_enabled(False)
    
    def set_fields_enabled(self, enabled):
        """フィールドの編集可否を設定"""
        self.model_name_jp_edit.setEnabled(enabled)
        self.model_name_en_edit.setEnabled(enabled)
        self.comment_jp_edit.setEnabled(enabled)
        self.comment_en_edit.setEnabled(enabled)
        
        # モデルがロードされたらメッセージを非表示
        self.info_label.setVisible(not enabled)
