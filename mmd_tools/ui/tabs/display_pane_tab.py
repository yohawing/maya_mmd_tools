"""PMX表示枠をモデル単位で編集するメインUIタブ。"""

from ..base_tab import BaseTab
from ...core.name_display import original_pmx_fields_visible
from ..components.authoring_toolbar import AuthoringToolbar
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
        root_layout.setContentsMargins(5, 5, 5, 5)
        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        frame_widget = QWidget()
        frame_layout = QVBoxLayout(frame_widget)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        self.frames_group = QGroupBox()
        frames_group_layout = QVBoxLayout(self.frames_group)
        self.frame_list = QListWidget()
        self.frame_list.setObjectName("displayFrameList")
        frame_toolbar = QHBoxLayout()
        self.frame_authoring_toolbar = AuthoringToolbar(
            actions=("create", "delete", "move_up", "move_down"),
            labels={
                "create": self.tr("add_frame", "buttons"),
                "delete": self.tr("delete_frame", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            parent=self,
        )
        self.frame_authoring_toolbar.setObjectName("displayFrameAuthoringToolbar")
        self.add_frame_btn = self.frame_authoring_toolbar.button("create")
        self.delete_frame_btn = self.frame_authoring_toolbar.button("delete")
        self.move_frame_up_btn = self.frame_authoring_toolbar.button("move_up")
        self.move_frame_down_btn = self.frame_authoring_toolbar.button("move_down")
        self.add_frame_btn.setObjectName("displayAddFrameButton")
        self.delete_frame_btn.setObjectName("displayDeleteFrameButton")
        self.move_frame_up_btn.setObjectName("displayMoveFrameUpButton")
        self.move_frame_down_btn.setObjectName("displayMoveFrameDownButton")
        frame_toolbar.addWidget(self.frame_authoring_toolbar)
        self.refresh_toolbar = AuthoringToolbar(
            actions=("refresh",),
            labels={"refresh": self.tr("refresh", "buttons")},
            parent=self,
        )
        self.refresh_toolbar.setObjectName("displayRefreshToolbar")
        self.footer_toolbar = self.refresh_toolbar
        self.refresh_btn = self.refresh_toolbar.button("refresh")
        self.refresh_btn.setObjectName("displayRefreshButton")
        frame_toolbar.addWidget(self.refresh_toolbar)
        frame_toolbar.addStretch(1)
        frames_group_layout.addLayout(frame_toolbar)
        frames_group_layout.addWidget(self.frame_list, 1)
        frame_layout.addWidget(self.frames_group)
        splitter.addWidget(frame_widget)

        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)

        self.properties_group = QGroupBox()
        properties_layout = QFormLayout(self.properties_group)
        self.name_jp_edit = QLineEdit()
        self.name_jp_edit.setObjectName("displayFrameNameJpEdit")
        self.name_en_edit = QLineEdit()
        self.name_en_edit.setObjectName("displayFrameNameEnEdit")
        self.special_frame_check = QCheckBox()
        self.special_frame_check.setObjectName("displaySpecialFrameCheck")
        self.name_jp_label = QLabel()
        self.name_en_label = QLabel()
        properties_layout.addRow(self.name_jp_label, self.name_jp_edit)
        properties_layout.addRow(self.name_en_label, self.name_en_edit)
        properties_layout.addRow(self.special_frame_check)
        editor_layout.addWidget(self.properties_group)

        self.items_group = QGroupBox()
        items_layout = QVBoxLayout(self.items_group)
        self.item_table = QTableWidget(0, 3)
        self.item_table.setObjectName("displayItemTable")
        self.item_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.item_table.setSelectionMode(QTableWidget.SingleSelection)
        self.item_table.verticalHeader().setVisible(False)
        self.item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.item_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.item_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        item_toolbar = QHBoxLayout()
        self.item_element_toolbar = AuthoringToolbar(
            actions=("create",),
            labels={"create": self.tr("add_element", "buttons")},
            parent=self,
        )
        self.item_element_toolbar.setObjectName("displayItemElementToolbar")
        self.item_authoring_toolbar = AuthoringToolbar(
            actions=("delete", "move_up", "move_down"),
            labels={
                "delete": self.tr("delete_item", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            parent=self,
        )
        self.item_authoring_toolbar.setObjectName("displayItemAuthoringToolbar")
        self.add_element_btn = self.item_element_toolbar.button("create")
        self.delete_item_btn = self.item_authoring_toolbar.button("delete")
        self.move_item_up_btn = self.item_authoring_toolbar.button("move_up")
        self.move_item_down_btn = self.item_authoring_toolbar.button("move_down")
        self.add_element_btn.setObjectName("displayAddElementButton")
        self.delete_item_btn.setObjectName("displayDeleteItemButton")
        self.move_item_up_btn.setObjectName("displayMoveItemUpButton")
        self.move_item_down_btn.setObjectName("displayMoveItemDownButton")
        item_toolbar.addWidget(self.item_element_toolbar)
        item_toolbar.addWidget(self.item_authoring_toolbar)
        item_toolbar.addStretch(1)
        items_layout.addLayout(item_toolbar)
        items_layout.addWidget(self.item_table, 1)
        editor_layout.addWidget(self.items_group, 1)
        splitter.addWidget(editor_widget)
        splitter.setSizes([260, 520])

        footer = QHBoxLayout()
        self.status_label = QLabel()
        footer.addWidget(self.status_label, 1)
        self.apply_btn = QPushButton()
        self.reset_btn = QPushButton()
        self.apply_btn.setObjectName("displayApplyButton")
        self.reset_btn.setObjectName("displayResetButton")
        footer.addWidget(self.apply_btn)
        footer.addWidget(self.reset_btn)
        root_layout.addLayout(footer)

        self.retranslateUi()
        self.set_editor_enabled(False)

    def set_editor_enabled(self, enabled: bool) -> None:
        """モデル／枠の有無に応じて編集領域を切り替える。"""
        self.frame_list.setEnabled(enabled)
        self.properties_group.setEnabled(enabled)
        self.items_group.setEnabled(enabled)
        reason = "" if enabled else self.tr("authoring_selection_required", "tooltips")
        reason_key = "" if enabled else "authoring_selection_required"
        self.frame_authoring_toolbar.set_action_enabled("create", enabled, reason, reason_key)
        self.frame_authoring_toolbar.set_action_enabled(
            "delete", enabled, reason, reason_key
        )
        self.frame_authoring_toolbar.set_action_enabled("move_up", enabled, reason, reason_key)
        self.frame_authoring_toolbar.set_action_enabled("move_down", enabled, reason, reason_key)
        for action in ("delete", "move_up", "move_down"):
            self.item_authoring_toolbar.set_action_enabled(action, enabled, reason, reason_key)
        self.item_element_toolbar.set_action_enabled("create", enabled, reason, reason_key)

    def retranslateUi(self):
        """現在の言語で表示テキストを更新する。"""
        self.frames_group.setTitle(self.tr("display_frames", "groups"))
        self.properties_group.setTitle(self.tr("display_frame_properties", "groups"))
        self.items_group.setTitle(self.tr("display_frame_items", "groups"))
        self.name_jp_label.setText(self.tr("display_frame_name_jp", "fields"))
        self.name_en_label.setText(self.tr("display_frame_name_en", "fields"))
        original_visible = original_pmx_fields_visible(self._translator.get_language())
        self.name_jp_label.setVisible(original_visible)
        self.name_jp_edit.setVisible(original_visible)
        self.special_frame_check.setText(self.tr("special_display_frame", "fields"))
        self.item_table.setHorizontalHeaderLabels(
            [
                self.tr("element_type", "fields"),
                self.tr("element_name", "fields"),
                self.tr("element_index", "fields"),
            ]
        )
        self.frame_authoring_toolbar.retranslate(
            {
                "create": self.tr("add_frame", "buttons"),
                "delete": self.tr("delete_frame", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )
        self.item_element_toolbar.retranslate(
            {"create": self.tr("add_element", "buttons")},
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )
        self.item_authoring_toolbar.retranslate(
            {
                "delete": self.tr("delete_item", "buttons"),
                "move_up": self.tr("up", "buttons"),
                "move_down": self.tr("down", "buttons"),
            },
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )
        self.refresh_toolbar.retranslate(
            {"refresh": self.tr("refresh", "buttons")},
            reason_resolver=lambda key: self.tr(key, "tooltips"),
        )
        self.apply_btn.setText(self.tr("apply", "buttons"))
        self.reset_btn.setText(self.tr("reset", "buttons"))
