from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class DisplayPanePresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.connect_signals()

    def connect_signals(self):
        self.view.display_pane_list.currentItemChanged.connect(self.on_display_pane_selected)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_display_panes()

    def load_display_panes(self):
        self.view.display_pane_list.clear()
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        # This is a simplified example. Display panes are not directly represented in Maya.
        # We would need to store this information as attributes on the root node.
        display_panes = cmds.getAttr(f"{self.current_model_root}.mmd_display_panes")
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
