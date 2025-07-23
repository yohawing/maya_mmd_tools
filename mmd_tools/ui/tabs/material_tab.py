from ..qt_compat import QWidget, QVBoxLayout, QListWidget
from ..base_tab import BaseTab

class MaterialTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MaterialTab")

        main_layout = QHBoxLayout(self)

        # Material List
        material_list_group = QGroupBox("Materials")
        material_list_layout = QVBoxLayout()
        self.material_list = QListWidget()
        material_list_layout.addWidget(self.material_list)
        material_list_group.setLayout(material_list_layout)
        main_layout.addWidget(material_list_group)

        # Material Details
        material_details_group = QGroupBox("Details")
        material_details_layout = QFormLayout()

        self.diffuse_color_edit = QLineEdit()
        self.specular_color_edit = QLineEdit()
        self.ambient_color_edit = QLineEdit()
        self.texture_path_edit = QLineEdit()
        self.sphere_map_path_edit = QLineEdit()

        material_details_layout.addRow("Diffuse:", self.diffuse_color_edit)
        material_details_layout.addRow("Specular:", self.specular_color_edit)
        material_details_layout.addRow("Ambient:", self.ambient_color_edit)
        material_details_layout.addRow("Texture:", self.texture_path_edit)
        material_details_layout.addRow("Sphere Map:", self.sphere_map_path_edit)

        material_details_group.setLayout(material_details_layout)
        main_layout.addWidget(material_details_group)
