"""PMX表示枠をモデル単位で編集するメインUIタブ。"""

from ..base_tab import BaseTab
from ..qt_compat import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    Qt,
)


class DisplayPaneTab(BaseTab):
    """表示枠と、そのボーン／モーフ要素を編集するView。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DisplayPaneTab")

        root_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        frame_widget = QWidget()
        frame_layout = QVBoxLayout(frame_widget)
        self.frames_group = QGroupBox()
        frames_group_layout = QVBoxLayout(self.frames_group)
        self.frame_list = QListWidget()
        frames_group_layout.addWidget(self.frame_list, 1)
        frame_toolbar = QHBoxLayout()
        self.add_frame_btn = QPushButton()
        self.delete_frame_btn = QPushButton()
        self.move_frame_up_btn = QPushButton()
        self.move_frame_down_btn = QPushButton()
        for button in (
            self.add_frame_btn,
            self.delete_frame_btn,
            self.move_frame_up_btn,
            self.move_frame_down_btn,
        ):
            frame_toolbar.addWidget(button)
        frames_group_layout.addLayout(frame_toolbar)
        frame_layout.addWidget(self.frames_group)
        splitter.addWidget(frame_widget)

        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)

        self.properties_group = QGroupBox()
        properties_layout = QFormLayout(self.properties_group)
        self.name_jp_edit = QLineEdit()
        self.name_en_edit = QLineEdit()
        self.special_frame_check = QCheckBox()
        self.name_jp_label = QLabel()
        self.name_en_label = QLabel()
        properties_layout.addRow(self.name_jp_label, self.name_jp_edit)
        properties_layout.addRow(self.name_en_label, self.name_en_edit)
        properties_layout.addRow(self.special_frame_check)
        editor_layout.addWidget(self.properties_group)

        self.items_group = QGroupBox()
        items_layout = QVBoxLayout(self.items_group)
        self.item_table = QTableWidget(0, 3)
        self.item_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.item_table.setSelectionMode(QTableWidget.SingleSelection)
        self.item_table.verticalHeader().setVisible(False)
        self.item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.item_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.item_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        items_layout.addWidget(self.item_table, 1)
        item_toolbar = QHBoxLayout()
        self.add_bone_btn = QPushButton()
        self.add_morph_btn = QPushButton()
        self.delete_item_btn = QPushButton()
        self.move_item_up_btn = QPushButton()
        self.move_item_down_btn = QPushButton()
        for button in (
            self.add_bone_btn,
            self.add_morph_btn,
            self.delete_item_btn,
            self.move_item_up_btn,
            self.move_item_down_btn,
        ):
            item_toolbar.addWidget(button)
        items_layout.addLayout(item_toolbar)
        editor_layout.addWidget(self.items_group, 1)
        splitter.addWidget(editor_widget)
        splitter.setSizes([260, 520])

        footer = QHBoxLayout()
        self.status_label = QLabel()
        footer.addWidget(self.status_label, 1)
        self.refresh_btn = QPushButton()
        self.apply_btn = QPushButton()
        self.reset_btn = QPushButton()
        footer.addWidget(self.refresh_btn)
        footer.addWidget(self.apply_btn)
        footer.addWidget(self.reset_btn)
        root_layout.addLayout(footer)

        self.retranslateUi()
        self.set_editor_enabled(False)

    def set_editor_enabled(self, enabled: bool) -> None:
        """モデル／枠の有無に応じて編集領域を切り替える。"""
        self.properties_group.setEnabled(enabled)
        self.items_group.setEnabled(enabled)
        self.delete_frame_btn.setEnabled(enabled)
        self.move_frame_up_btn.setEnabled(enabled)
        self.move_frame_down_btn.setEnabled(enabled)

    def retranslateUi(self):
        """現在の言語で表示テキストを更新する。"""
        self.frames_group.setTitle(self.tr("display_frames", "groups"))
        self.properties_group.setTitle(self.tr("display_frame_properties", "groups"))
        self.items_group.setTitle(self.tr("display_frame_items", "groups"))
        self.name_jp_label.setText(self.tr("display_frame_name_jp", "fields"))
        self.name_en_label.setText(self.tr("display_frame_name_en", "fields"))
        self.special_frame_check.setText(self.tr("special_display_frame", "fields"))
        self.item_table.setHorizontalHeaderLabels(
            [
                self.tr("element_type", "fields"),
                self.tr("element_name", "fields"),
                self.tr("element_index", "fields"),
            ]
        )
        self.add_frame_btn.setText(self.tr("add_frame", "buttons"))
        self.delete_frame_btn.setText(self.tr("delete_frame", "buttons"))
        self.move_frame_up_btn.setText(self.tr("up", "buttons"))
        self.move_frame_down_btn.setText(self.tr("down", "buttons"))
        self.add_bone_btn.setText(self.tr("add_bone", "buttons"))
        self.add_morph_btn.setText(self.tr("add_morph", "buttons"))
        self.delete_item_btn.setText(self.tr("delete_item", "buttons"))
        self.move_item_up_btn.setText(self.tr("up", "buttons"))
        self.move_item_down_btn.setText(self.tr("down", "buttons"))
        self.refresh_btn.setText(self.tr("refresh", "buttons"))
        self.apply_btn.setText(self.tr("apply", "buttons"))
        self.reset_btn.setText(self.tr("reset", "buttons"))
