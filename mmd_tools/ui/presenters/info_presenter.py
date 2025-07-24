from maya import cmds
from ...core.logger import get_logger

logger = get_logger(__name__)

class InfoPresenter:
    def __init__(self, view):
        self.view = view
        self.current_model_root = None
        self.connect_signals()

    def connect_signals(self):
        self.view.model_name_jp_edit.textChanged.connect(self.update_model_info)
        self.view.model_name_en_edit.textChanged.connect(self.update_model_info)
        self.view.comment_jp_edit.textChanged.connect(self.update_model_info)
        self.view.comment_en_edit.textChanged.connect(self.update_model_info)

    def on_model_imported(self, root_node):
        self.current_model_root = root_node
        self.load_model_info()

    def load_model_info(self):
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            logger.warning("No model selected or model does not exist.")
            return

        try:
            # Disconnect signals to prevent feedback loops
            self.view.model_name_jp_edit.textChanged.disconnect(self.update_model_info)
            self.view.model_name_en_edit.textChanged.disconnect(self.update_model_info)
            self.view.comment_jp_edit.textChanged.disconnect(self.update_model_info)
            self.view.comment_en_edit.textChanged.disconnect(self.update_model_info)

            model_name_jp = cmds.getAttr(f"{self.current_model_root}.mmd_model_name_jp") or ""
            model_name_en = cmds.getAttr(f"{self.current_model_root}.mmd_model_name_en") or ""
            comment_jp = cmds.getAttr(f"{self.current_model_root}.mmd_comment_jp") or ""
            comment_en = cmds.getAttr(f"{self.current_model_root}.mmd_comment_en") or ""

            self.view.model_name_jp_edit.setText(model_name_jp)
            self.view.model_name_en_edit.setText(model_name_en)
            self.view.comment_jp_edit.setText(comment_jp)
            self.view.comment_en_edit.setText(comment_en)
        except Exception as e:
            logger.error(f"Failed to load model info: {e}", exc_info=True)
        finally:
            # Reconnect signals
            self.view.model_name_jp_edit.textChanged.connect(self.update_model_info)
            self.view.model_name_en_edit.textChanged.connect(self.update_model_info)
            self.view.comment_jp_edit.textChanged.connect(self.update_model_info)
            self.view.comment_en_edit.textChanged.connect(self.update_model_info)

    def update_model_info(self):
        if not self.current_model_root or not cmds.objExists(self.current_model_root):
            return

        try:
            cmds.setAttr(f"{self.current_model_root}.mmd_model_name_jp", self.view.model_name_jp_edit.text(), type="string")
            cmds.setAttr(f"{self.current_model_root}.mmd_model_name_en", self.view.model_name_en_edit.text(), type="string")
            cmds.setAttr(f"{self.current_model_root}.mmd_comment_jp", self.view.comment_jp_edit.text(), type="string")
            cmds.setAttr(f"{self.current_model_root}.mmd_comment_en", self.view.comment_en_edit.text(), type="string")
        except Exception as e:
            logger.error(f"Failed to update model info: {e}", exc_info=True)
