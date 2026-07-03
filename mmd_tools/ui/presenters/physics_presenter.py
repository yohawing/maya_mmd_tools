from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core.logger import get_logger
from .list_presenter_helpers import reload_for_current_model_change

logger = get_logger(__name__)


class PhysicsPresenter:
    def __init__(self, view, app_state, maya_adapter=None):
        self.view = view
        self.app_state = app_state
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.connect_signals()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        reload_for_current_model_change(logger, "PhysicsPresenter", model_root, self.load_physics)

    def load_physics(self):
        self.view.rigid_body_list.clear()
        self.view.joint_list.clear()

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not self.maya_adapter.object_exists(current_model_root):
            return

        # This is a simplified example. Rigid bodies and joints are not directly represented in Maya.
        # We would need to store this information as attributes on the root node or use custom nodes.
        rigid_bodies = self.maya_adapter.ls(type="nRigid")
        for rb in rigid_bodies:
            self.view.rigid_body_list.addItem(rb)

        joints = self.maya_adapter.ls(type="constraint")
        for j in joints:
            self.view.joint_list.addItem(j)
