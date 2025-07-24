from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class BonePresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.connect_signals()

    def connect_signals(self):
        self.view.bone_tree.currentItemChanged.connect(self.on_bone_selected)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_bones()

    def load_bones(self):
        self.view.bone_tree.clear()
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        joints = cmds.listRelatives(self.current_model_root, allDescendents=True, type="joint")
        if not joints:
            return

        # Create a dictionary to store tree items for quick lookup
        bone_items = {}

        for joint in joints:
            parent = cmds.listRelatives(joint, parent=True, type="joint")
            parent_name = parent[0] if parent else ""

            item = QTreeWidgetItem([joint, parent_name])
            bone_items[joint] = item

            if parent_name and parent_name in bone_items:
                bone_items[parent_name].addChild(item)
            else:
                self.view.bone_tree.addTopLevelItem(item)

    def on_bone_selected(self, current, previous):
        if not current:
            return
        bone_name = current.text(0)
        parent_name = current.text(1)
        logger.info(f"Selected bone: {bone_name}")

        self.view.bone_name_edit.setText(bone_name)
        self.view.parent_bone_edit.setText(parent_name)
