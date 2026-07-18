from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QListWidget,
    QSlider,
    Qt,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QComboBox,
    QDoubleSpinBox,
    QTabWidget,
    QSplitter,
    QCheckBox,
)
from ..base_tab import BaseTab
from .translation_registry import apply_translation_registry


class MorphTab(BaseTab):
    _TRANSLATION_REGISTRY = (
        ("morph_list_group", "setTitle", "morph_list", "groups"),
        ("preview_group", "setTitle", "preview", "groups"),
        ("advanced_group", "setTitle", "advanced_settings", "groups"),
        ("refresh_morphs_btn", "setText", "refresh", "buttons"),
        ("reset_slider_btn", "setText", "reset", "buttons"),
        ("reset_all_btn", "setText", "reset_all", "actions"),
        ("apply_btn", "setText", "apply", "buttons"),
        ("reset_btn", "setText", "reset", "buttons"),
        ("search_label", "setText", "search", "fields"),
        ("apply_rate_label", "setText", "apply_rate", "fields"),
        ("morph_name_jp_label", "setText", "morph_name_jp", "fields"),
        ("morph_name_en_label", "setText", "morph_name_en", "fields"),
        ("panel_label", "setText", "panel", "fields"),
        ("morph_type_label", "setText", "type", "fields"),
        ("multiplier_label", "setText", "multiplier", "fields"),
        ("invert_check", "setText", "invert_value", "checkboxes"),
        ("search_edit", "setPlaceholderText", "search_morph_name", "placeholders"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MorphTab")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スプリッターでリストと詳細を分割
        splitter = QSplitter(Qt.Horizontal)

        # 左側: モーフリスト
        center_widget = self._create_morph_list_section()
        splitter.addWidget(center_widget)

        # 右側: モーフ詳細
        right_widget = self._create_morph_details_section()
        splitter.addWidget(right_widget)

        # 初期のスプリッター比率
        splitter.setSizes([350, 650])

        main_layout.addWidget(splitter)

    def _create_morph_list_section(self):
        """モーフリストセクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # モーフリスト
        self.morph_list_group = QGroupBox(self.tr("morph_list", "groups"))
        morph_list_layout = QVBoxLayout()

        # ツールバー
        toolbar_layout = QHBoxLayout()
        self.refresh_morphs_btn = QPushButton(self.tr("refresh", "buttons"))
        self.refresh_morphs_btn.setMaximumWidth(60)
        toolbar_layout.addWidget(self.refresh_morphs_btn)
        toolbar_layout.addStretch()
        morph_list_layout.addLayout(toolbar_layout)

        # モーフリスト
        self.morph_list = QListWidget()
        self.morph_list.setAlternatingRowColors(True)
        morph_list_layout.addWidget(self.morph_list)

        # 検索
        search_layout = QHBoxLayout()
        self.search_label = QLabel(self.tr("search", "fields"))
        search_layout.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("search_morph_name", "placeholders"))
        search_layout.addWidget(self.search_edit)
        morph_list_layout.addLayout(search_layout)

        self.morph_list_group.setLayout(morph_list_layout)
        layout.addWidget(self.morph_list_group)

        return widget

    def _create_morph_details_section(self):
        """モーフ詳細セクションを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # タブウィジェット
        self.detail_tabs = QTabWidget()

        # 基本情報タブ
        self.detail_tabs.addTab(self._create_basic_info_tab(), self.tr("basic_information", "tabs"))

        layout.addWidget(self.detail_tabs)

        # プレビューセクション
        self.preview_group = QGroupBox(self.tr("preview", "groups"))
        preview_layout = QVBoxLayout()

        # スライダー
        slider_layout = QHBoxLayout()
        self.apply_rate_label = QLabel(self.tr("apply_rate", "fields"))
        slider_layout.addWidget(self.apply_rate_label)
        self.morph_slider = QSlider(Qt.Horizontal)
        self.morph_slider.setRange(0, 100)
        self.morph_slider.setTickPosition(QSlider.TicksBelow)
        self.morph_slider.setTickInterval(10)
        slider_layout.addWidget(self.morph_slider)

        self.morph_value_label = QLabel("0%")
        self.morph_value_label.setMinimumWidth(40)
        slider_layout.addWidget(self.morph_value_label)

        preview_layout.addLayout(slider_layout)

        # プレビュー値の補正
        self.advanced_group = QGroupBox(self.tr("advanced_settings", "groups"))
        advanced_layout = QFormLayout()
        self.invert_check = QCheckBox(self.tr("invert_value", "checkboxes"))
        advanced_layout.addRow("", self.invert_check)

        self.multiplier_spin = QDoubleSpinBox()
        self.multiplier_spin.setRange(-10.0, 10.0)
        self.multiplier_spin.setValue(1.0)
        self.multiplier_spin.setSingleStep(0.1)
        self.multiplier_label = QLabel(self.tr("multiplier", "fields"))
        advanced_layout.addRow(self.multiplier_label, self.multiplier_spin)
        self.advanced_group.setLayout(advanced_layout)
        preview_layout.addWidget(self.advanced_group)

        # リセットボタン
        reset_layout = QHBoxLayout()
        self.reset_slider_btn = QPushButton(self.tr("reset", "buttons"))
        self.reset_all_btn = QPushButton(self.tr("reset_all", "actions"))
        reset_layout.addStretch()
        reset_layout.addWidget(self.reset_slider_btn)
        reset_layout.addWidget(self.reset_all_btn)
        preview_layout.addLayout(reset_layout)

        self.preview_group.setLayout(preview_layout)
        layout.addWidget(self.preview_group)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.apply_btn = QPushButton(self.tr("apply", "buttons"))
        self.reset_btn = QPushButton(self.tr("reset", "buttons"))
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.reset_btn)
        layout.addLayout(button_layout)

        return widget

    def _create_basic_info_tab(self):
        """基本情報タブを作成"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # モーフ名
        self.morph_name_jp_edit = QLineEdit()
        self.morph_name_en_edit = QLineEdit()
        self.morph_name_jp_label = QLabel(self.tr("morph_name_jp", "fields"))
        self.morph_name_en_label = QLabel(self.tr("morph_name_en", "fields"))
        layout.addRow(self.morph_name_jp_label, self.morph_name_jp_edit)
        layout.addRow(self.morph_name_en_label, self.morph_name_en_edit)

        # パネル
        panel_layout = QHBoxLayout()
        self.panel_combo = QComboBox()
        self.panel_combo.addItems(
            [
                self.tr("none", "morph_panels"),
                self.tr("eyebrows_lower_left", "morph_panels"),
                self.tr("eyes_upper_left", "morph_panels"),
                self.tr("mouth_upper_right", "morph_panels"),
                self.tr("other_lower_right", "morph_panels"),
            ]
        )
        panel_layout.addWidget(self.panel_combo)
        self.panel_label = QLabel(self.tr("panel", "fields"))
        layout.addRow(self.panel_label, panel_layout)

        # モーフタイプ
        self.morph_type_combo = QComboBox()
        self.morph_type_combo.addItems(
            [
                self.tr("vertex", "morph_types"),
                "UV",
                "UV1",
                "UV2",
                "UV3",
                "UV4",
                self.tr("additional_uv1", "morph_types"),
                self.tr("additional_uv2", "morph_types"),
                self.tr("additional_uv3", "morph_types"),
                self.tr("additional_uv4", "morph_types"),
                self.tr("bone", "morph_types"),
                self.tr("material", "morph_types"),
                self.tr("group", "morph_types"),
                self.tr("flip", "morph_types"),
                self.tr("impulse", "morph_types"),
            ]
        )
        self.morph_type_label = QLabel(self.tr("type", "fields"))
        layout.addRow(self.morph_type_label, self.morph_type_combo)

        return widget

    def set_morph_details_enabled(self, enabled):
        """モーフ詳細セクションの有効/無効を設定"""
        self.detail_tabs.setEnabled(enabled)
        self.morph_slider.setEnabled(enabled)
        self.reset_slider_btn.setEnabled(enabled)
        self.invert_check.setEnabled(enabled)
        self.multiplier_spin.setEnabled(enabled)
        self.apply_btn.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)

    def set_morph_controls_enabled(self, enabled, tooltip=""):
        """Enable only runtime weight controls while keeping metadata browsable."""
        for widget in (
            self.morph_slider,
            self.reset_slider_btn,
            self.invert_check,
            self.multiplier_spin,
        ):
            widget.setEnabled(enabled)
            widget.setToolTip(tooltip)

    def retranslateUi(self):
        """言語切り替え時にUIを再翻訳"""
        apply_translation_registry(self, self._TRANSLATION_REGISTRY)

        # Tab widget texts
        if self.detail_tabs.count() >= 1:
            self.detail_tabs.setTabText(0, self.tr("basic_information", "tabs"))

        # ComboBox items - Panel
        self.panel_combo.clear()
        self.panel_combo.addItems(
            [
                self.tr("none", "morph_panels"),
                self.tr("eyebrows_lower_left", "morph_panels"),
                self.tr("eyes_upper_left", "morph_panels"),
                self.tr("mouth_upper_right", "morph_panels"),
                self.tr("other_lower_right", "morph_panels"),
            ]
        )

        # ComboBox items - Morph Type
        self.morph_type_combo.clear()
        self.morph_type_combo.addItems(
            [
                self.tr("vertex", "morph_types"),
                "UV",
                "UV1",
                "UV2",
                "UV3",
                "UV4",
                self.tr("additional_uv1", "morph_types"),
                self.tr("additional_uv2", "morph_types"),
                self.tr("additional_uv3", "morph_types"),
                self.tr("additional_uv4", "morph_types"),
                self.tr("bone", "morph_types"),
                self.tr("material", "morph_types"),
                self.tr("group", "morph_types"),
                self.tr("flip", "morph_types"),
                self.tr("impulse", "morph_types"),
            ]
        )
