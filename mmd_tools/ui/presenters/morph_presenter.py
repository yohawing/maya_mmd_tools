from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class MorphPresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.blend_shape_node = None
        self.connect_signals()

    def connect_signals(self):
        self.view.morph_list.currentItemChanged.connect(self.on_morph_selected)
        self.view.morph_slider.valueChanged.connect(self.on_morph_slider_changed)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_morphs()

    def load_morphs(self):
        self.view.morph_list.clear()
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        shapes = cmds.listRelatives(self.current_model_root, allDescendents=True, type="mesh")
        if not shapes:
            return

        history = cmds.listHistory(shapes[0])
        blend_shape_nodes = cmds.ls(history, type="blendShape")
        if not blend_shape_nodes:
            return

        self.blend_shape_node = blend_shape_nodes[0]
        aliases = cmds.aliasAttr(self.blend_shape_node, query=True)
        for i in range(0, len(aliases), 2):
            self.view.morph_list.addItem(aliases[i])

    def on_morph_selected(self, current, previous):
        if not current or not self.blend_shape_node:
            return
        morph_name = current.text()
        logger.info(f"Selected morph: {morph_name}")

        self.view.morph_name_edit.setText(morph_name)
        weight = cmds.getAttr(f"{self.blend_shape_node}.{morph_name}")
        self.view.morph_slider.setValue(int(weight * 100))

    def on_morph_slider_changed(self, value):
        if not self.blend_shape_node:
            return
        morph_name = self.view.morph_name_edit.text()
        if not morph_name:
            return

        weight = value / 100.0
        cmds.setAttr(f"{self.blend_shape_node}.{morph_name}", weight)
