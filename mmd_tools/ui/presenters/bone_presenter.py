from maya import cmds
from ...core.logger import get_logger
from ..qt_compat import QTreeWidgetItem

logger = get_logger(__name__)

class BonePresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.connect_signals()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)
        
        # UIのシグナル
        self.view.bone_tree.currentItemChanged.connect(self.on_bone_selected)
    
    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        self.load_bones()

    def load_bones(self):
        self.view.bone_tree.clear()
        
        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            return

        joints = cmds.listRelatives(current_model_root, allDescendents=True, type="joint")
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
