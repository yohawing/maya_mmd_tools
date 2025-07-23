from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class PhysicsPresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.connect_signals()

    def connect_signals(self):
        pass

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_physics()

    def load_physics(self):
        self.view.rigid_body_list.clear()
        self.view.joint_list.clear()
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        # This is a simplified example. Rigid bodies and joints are not directly represented in Maya.
        # We would need to store this information as attributes on the root node or use custom nodes.
        rigid_bodies = cmds.ls(type="nRigid")
        for rb in rigid_bodies:
            self.view.rigid_body_list.addItem(rb)

        joints = cmds.ls(type="constraint")
        for j in joints:
            self.view.joint_list.addItem(j)
