from ..qt_compat import (
    QVBoxLayout,
    QGroupBox,
    QFormLayout,
    QCheckBox,
    QComboBox,
    QLineEdit
)
from ..base_tab import BaseTab

class SettingsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsTab")

        main_layout = QVBoxLayout(self)

        # General Settings
        general_group = QGroupBox("General")
        general_layout = QFormLayout()
        self.language_combo = QComboBox()
        self.language_combo.addItems(["English", "Japanese"])
        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        general_layout.addRow("UI Language:", self.language_combo)
        general_layout.addRow("Log Level:", self.log_level_combo)
        general_group.setLayout(general_layout)
        main_layout.addWidget(general_group)

        # Import Settings
        import_group = QGroupBox("Import")
        import_layout = QFormLayout()
        self.import_physics_check = QCheckBox("Import Physics")
        import_layout.addRow(self.import_physics_check)
        import_group.setLayout(import_layout)
        main_layout.addWidget(import_group)

        main_layout.addStretch()
