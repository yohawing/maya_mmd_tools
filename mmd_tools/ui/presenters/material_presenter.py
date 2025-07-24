from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class MaterialPresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.connect_signals()

    def connect_signals(self):
        self.view.material_list.currentItemChanged.connect(self.on_material_selected)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_materials()

    def load_materials(self):
        self.view.material_list.clear()
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        shapes = cmds.listRelatives(self.current_model_root, allDescendents=True, type="mesh")
        if not shapes:
            return

        shading_groups = cmds.listConnections(shapes, type='shadingEngine')
        if not shading_groups:
            return

        # Get unique shading groups
        shading_groups = list(set(shading_groups))

        for sg in shading_groups:
            materials = cmds.ls(cmds.listConnections(sg), materials=True)
            for mat in materials:
                self.view.material_list.addItem(mat)

    def on_material_selected(self, current, previous):
        if not current:
            return
        material_name = current.text()
        logger.info(f"Selected material: {material_name}")

        try:
            diffuse_color = cmds.getAttr(f"{material_name}.color")[0]
            specular_color = cmds.getAttr(f"{material_name}.specularColor")[0]
            ambient_color = cmds.getAttr(f"{material_name}.ambientColor")[0]

            self.view.diffuse_color_edit.setText(str(diffuse_color))
            self.view.specular_color_edit.setText(str(specular_color))
            self.view.ambient_color_edit.setText(str(ambient_color))

            # Get texture path
            file_node = cmds.listConnections(f"{material_name}.color", type="file")
            if file_node:
                texture_path = cmds.getAttr(f"{file_node[0]}.fileTextureName")
                self.view.texture_path_edit.setText(texture_path)
            else:
                self.view.texture_path_edit.clear()

            # Get sphere map path
            # This is a simplified example. Sphere map could be connected to other attributes.
            sphere_map_node = cmds.listConnections(f"{material_name}.reflectedColor", type="file")
            if sphere_map_node:
                sphere_map_path = cmds.getAttr(f"{sphere_map_node[0]}.fileTextureName")
                self.view.sphere_map_path_edit.setText(sphere_map_path)
            else:
                self.view.sphere_map_path_edit.clear()

        except Exception as e:
            logger.error(f"Failed to load material details for {material_name}: {e}", exc_info=True)
