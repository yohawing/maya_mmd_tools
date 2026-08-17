from ..qt_compat import (
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QLabel,
    QScrollArea,
    QSpinBox,
)
from ..base_tab import BaseTab


class SettingsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsTab")

        # デバッグ用
        from ...core.logger import get_logger

        logger = get_logger(__name__)
        logger.debug("SettingsTab initialization started")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # スクロールエリアを作成
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        # スクロール内のウィジェット
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        # タブウィジェット
        self.settings_tabs = QTabWidget()

        # 各設定タブを追加
        self.settings_tabs.addTab(self._create_general_tab(), self.tr("general_settings", "tabs"))

        scroll_layout.addWidget(self.settings_tabs)

        # ボタンバー
        button_layout = QHBoxLayout()
        self.save_settings_btn = QPushButton(self.tr("save_settings", "actions"))
        self.reset_settings_btn = QPushButton(self.tr("reset_to_default", "actions"))
        self.export_settings_btn = QPushButton(self.tr("export_settings", "actions"))
        self.import_settings_btn = QPushButton(self.tr("import_settings", "actions"))
        self.save_settings_btn.setObjectName("settingsSaveButton")
        self.reset_settings_btn.setObjectName("settingsResetButton")
        self.export_settings_btn.setObjectName("settingsExportButton")
        self.import_settings_btn.setObjectName("settingsImportButton")

        button_layout.addStretch()
        button_layout.addWidget(self.save_settings_btn)
        button_layout.addWidget(self.reset_settings_btn)
        button_layout.addWidget(self.export_settings_btn)
        button_layout.addWidget(self.import_settings_btn)

        scroll_layout.addLayout(button_layout)

        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)

        # デバッグ：初期化完了
        logger.debug("SettingsTab initialization completed")
        logger.debug(f"SettingsTab attributes: {list(self.__dict__.keys())}")

    def _create_general_tab(self):
        """全般設定タブを作成"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # UI設定
        self.ui_group = QGroupBox(self.tr("ui_settings", "groups"))
        ui_layout = QFormLayout()

        self.development_mode_check = QCheckBox(self.tr("development_mode", "checkboxes"))
        self.development_mode_check.setObjectName("settingsDevelopmentModeCheck")
        ui_layout.addRow("", self.development_mode_check)

        # 言語選択
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("settingsLanguageCombo")
        # UITranslatorから言語リストを取得
        from ...ui.translations import UITranslator

        translator = UITranslator.instance()
        languages = translator.get_supported_languages()
        for code, name in languages.items():
            self.language_combo.addItem(name, code)
        self.language_label = QLabel(self.tr("language", "fields"))
        ui_layout.addRow(self.language_label, self.language_combo)

        self.file_history_limit_spin = QSpinBox()
        self.file_history_limit_spin.setObjectName("settingsFileHistoryLimitSpin")
        self.file_history_limit_spin.setRange(1, 100)
        self.file_history_limit_spin.setValue(20)
        self.file_history_limit_label = QLabel(self.tr("file_history_limit", "fields"))
        ui_layout.addRow(self.file_history_limit_label, self.file_history_limit_spin)

        self.ui_group.setLayout(ui_layout)
        layout.addWidget(self.ui_group)

        # Development tools. Hidden unless Development Mode is enabled.
        self.dev_tools_group = QGroupBox(self.tr("dev_tools", "groups"))
        dev_tools_layout = QFormLayout()

        command_port_layout = QHBoxLayout()
        self.command_port_spin = QSpinBox()
        self.command_port_spin.setObjectName("settingsCommandPortSpin")
        self.command_port_spin.setRange(1, 65535)
        self.command_port_spin.setValue(3939)
        self.open_command_port_btn = QPushButton(self.tr("open_command_port", "buttons"))
        self.open_command_port_btn.setObjectName("settingsOpenCommandPortButton")
        command_port_layout.addWidget(self.command_port_spin)
        command_port_layout.addWidget(self.open_command_port_btn)
        self.command_port_label = QLabel(self.tr("command_port", "fields"))
        dev_tools_layout.addRow(self.command_port_label, command_port_layout)

        self.dev_tools_group.setLayout(dev_tools_layout)
        layout.addWidget(self.dev_tools_group)

        # ログ設定
        self.log_group = QGroupBox(self.tr("log_settings", "groups"))
        log_layout = QFormLayout()

        self.logging_enabled_check = QCheckBox(self.tr("enable_logging", "checkboxes"))
        self.logging_enabled_check.setObjectName("settingsLoggingEnabledCheck")
        log_layout.addRow("", self.logging_enabled_check)

        self.log_level_combo = QComboBox()
        self.log_level_combo.setObjectName("settingsLogLevelCombo")
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.log_level_label = QLabel(self.tr("log_level", "fields"))
        log_layout.addRow(self.log_level_label, self.log_level_combo)

        log_file_layout = QHBoxLayout()
        self.log_file_path_edit = QLineEdit()
        self.log_file_path_edit.setObjectName("settingsLogFilePathEdit")
        self.log_file_browse_btn = QPushButton(self.tr("browse", "buttons"))
        self.log_file_browse_btn.setObjectName("settingsLogFileBrowseButton")
        self.log_file_browse_btn.setMaximumWidth(60)
        log_file_layout.addWidget(self.log_file_path_edit)
        log_file_layout.addWidget(self.log_file_browse_btn)
        self.log_file_label = QLabel(self.tr("log_file", "fields"))
        log_layout.addRow(self.log_file_label, log_file_layout)

        self.log_group.setLayout(log_layout)
        layout.addWidget(self.log_group)

        layout.addStretch()
        return widget

    def retranslateUi(self):
        """UIテキストを再翻訳"""
        # タブテキスト
        if hasattr(self, "settings_tabs"):
            if self.settings_tabs.count() >= 1:
                self.settings_tabs.setTabText(0, self.tr("general_settings", "tabs"))

        # ボタン
        if hasattr(self, "save_settings_btn"):
            self.save_settings_btn.setText(self.tr("save_settings", "actions"))
        if hasattr(self, "reset_settings_btn"):
            self.reset_settings_btn.setText(self.tr("reset_to_default", "actions"))
        if hasattr(self, "export_settings_btn"):
            self.export_settings_btn.setText(self.tr("export_settings", "actions"))
        if hasattr(self, "import_settings_btn"):
            self.import_settings_btn.setText(self.tr("import_settings", "actions"))
        if hasattr(self, "log_file_browse_btn"):
            self.log_file_browse_btn.setText(self.tr("browse", "buttons"))
        if hasattr(self, "open_command_port_btn"):
            self.open_command_port_btn.setText(self.tr("open_command_port", "buttons"))

        # GroupBoxes
        if hasattr(self, "ui_group"):
            self.ui_group.setTitle(self.tr("ui_settings", "groups"))
        if hasattr(self, "dev_tools_group"):
            self.dev_tools_group.setTitle(self.tr("dev_tools", "groups"))
        if hasattr(self, "log_group"):
            self.log_group.setTitle(self.tr("log_settings", "groups"))

        # Labels
        if hasattr(self, "language_label"):
            self.language_label.setText(self.tr("language", "fields"))
        if hasattr(self, "file_history_limit_label"):
            self.file_history_limit_label.setText(self.tr("file_history_limit", "fields"))
        if hasattr(self, "log_level_label"):
            self.log_level_label.setText(self.tr("log_level", "fields"))
        if hasattr(self, "log_file_label"):
            self.log_file_label.setText(self.tr("log_file", "fields"))
        if hasattr(self, "command_port_label"):
            self.command_port_label.setText(self.tr("command_port", "fields"))

        # CheckBoxes
        if hasattr(self, "development_mode_check"):
            self.development_mode_check.setText(self.tr("development_mode", "checkboxes"))
        if hasattr(self, "logging_enabled_check"):
            self.logging_enabled_check.setText(self.tr("enable_logging", "checkboxes"))
