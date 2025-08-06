from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)


class DisplayPanePresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
        self.connect_signals()

    def connect_signals(self):
        # ApplicationStateのシグナル
        self.app_state.current_model_changed.connect(self.on_current_model_changed)

        # UIのシグナル
        self.view.display_pane_list.currentItemChanged.connect(self.on_display_pane_selected)

    def on_current_model_changed(self, model_root):
        """現在のモデルが変更されたときの処理"""
        self.load_display_panes()

    def load_display_panes(self):
        self.view.display_pane_list.clear()

        current_model_root = self.app_state.current_model_root
        if not current_model_root or not cmds.objExists(current_model_root):
            return

        # This is a simplified example. Display panes are not directly represented in Maya.
        # We would need to store this information as attributes on the root node.
        if cmds.attributeQuery("mmd_display_panes", node=current_model_root, exists=True):
            display_panes = cmds.getAttr(f"{current_model_root}.mmd_display_panes")
        else:
            display_panes = None
        if display_panes:
            for pane in display_panes:
                self.view.display_pane_list.addItem(pane)

    def on_display_pane_selected(self, current, previous):
        if not current:
            return
        pane_name = current.text()
        logger.info(f"Selected display pane: {pane_name}")

        self.view.contained_items_list.clear()
        # TODO: Load contained bones/morphs for the selected pane
