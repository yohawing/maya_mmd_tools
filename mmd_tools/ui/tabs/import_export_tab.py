from ..qt_compat import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QDoubleValidator,
)
from ..base_tab import BaseTab


class ImportExportTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ImportExportTab")

        main_layout = QVBoxLayout(self)

        # Import Group
        import_group = QGroupBox("Import")
        import_layout = QFormLayout()

        self.import_path_edit = QLineEdit()
        self.import_path_button = QPushButton("...")
        import_path_layout = QHBoxLayout()
        import_path_layout.addWidget(self.import_path_edit)
        import_path_layout.addWidget(self.import_path_button)
        import_layout.addRow("File Path:", import_path_layout)

        self.scale_edit = QLineEdit("1.0")
        self.scale_edit.setValidator(QDoubleValidator())
        import_layout.addRow("Scale:", self.scale_edit)

        self.import_button = QPushButton("Import")
        import_layout.addRow(self.import_button)

        import_group.setLayout(import_layout)
        main_layout.addWidget(import_group)

        # Export Group
        export_group = QGroupBox("Export")
        export_layout = QFormLayout()

        self.export_path_edit = QLineEdit()
        self.export_path_button = QPushButton("...")
        export_path_layout = QHBoxLayout()
        export_path_layout.addWidget(self.export_path_edit)
        export_path_layout.addWidget(self.export_path_button)
        export_layout.addRow("File Path:", export_path_layout)

        self.export_button = QPushButton("Export")
        export_layout.addRow(self.export_button)

        export_group.setLayout(export_layout)
        main_layout.addWidget(export_group)

        main_layout.addStretch()
