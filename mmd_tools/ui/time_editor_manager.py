"""Model-scoped entry point to Maya's standard Time Editor."""
from ..core.logger import get_logger
from ..services.scene_model_service import SceneModelService
from .combo_box_utils import add_combo_item_with_tooltip, configure_model_combo_width
from .qt_compat import QLabel, QPushButton, QComboBox, QHBoxLayout, QVBoxLayout, QWidget, Qt, wrapInstance
from .translations import UITranslator

logger = get_logger(__name__)


class TimeEditorManagerWindow(QWidget):
    """Create or remove one bone clip; editing remains in Maya's Time Editor."""

    _instance = None

    def __init__(self, parent=None, *, cmds_module=None, scene_model_service=None):
        if parent is None:
            import maya.OpenMayaUI as mui
            pointer = mui.MQtUtil.mainWindow()
            parent = wrapInstance(int(pointer), QWidget) if pointer else None
        super().__init__(parent)
        from maya import cmds
        self._cmds = cmds_module or cmds
        self._service = scene_model_service or SceneModelService(cmds_module=self._cmds)
        self._translator = UITranslator.instance()
        self.setObjectName("MMDTimeEditorManagerWindow")
        self.setWindowFlags(Qt.Window)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        self.character_combo = QComboBox()
        self.character_combo.setObjectName("TimeEditorCharacterCombo")
        configure_model_combo_width(self.character_combo, minimum_width=300)
        self.refresh_btn = QPushButton()
        header = QHBoxLayout()
        header.addWidget(self.character_combo, 1)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)
        self.scope_label = QLabel()
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.buttons = {}
        actions = QHBoxLayout()
        for action in ("create", "remove", "open"):
            button = QPushButton()
            button.setObjectName("TimeEditor_" + action)
            button.clicked.connect(lambda _checked=False, key=action: self.perform_action(key))
            self.buttons[action] = button
            actions.addWidget(button)
        layout.addLayout(actions)
        self.state_label = QLabel()
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        layout.addWidget(self.message_label)
        self.refresh_btn.clicked.connect(self.refresh)
        self.character_combo.currentIndexChanged.connect(self._refresh_status)
        self.destroyed.connect(self._on_destroyed)
        self.retranslateUi()
        self.refresh()

    def _tr(self, key):
        return self._translator.translate(key, "time_editor")

    def retranslateUi(self):
        self.setWindowTitle(self._tr("title"))
        self.scope_label.setText(self._tr("scope"))
        self.refresh_btn.setText(self._tr("refresh"))
        for action, button in self.buttons.items():
            button.setText(self._tr(action))
            button.setToolTip(self._tr(action + "_tooltip"))

    def refresh(self):
        """Populate model UUIDs without creating sources or changing animation."""
        selected = self.character_combo.currentData(Qt.UserRole)
        self.character_combo.blockSignals(True)
        try:
            self.character_combo.clear()
            for root in self._service.list_mmd_models() or []:
                uuids = self._cmds.ls(root, uuid=True) or []
                if len(uuids) != 1:
                    continue
                label = self._service.get_model_display_name(root, language=self._translator.get_language())
                add_combo_item_with_tooltip(self.character_combo, label or root, uuids[0])
                if uuids[0] == selected:
                    self.character_combo.setCurrentIndex(self.character_combo.count() - 1)
        except Exception as exc:
            logger.error("Time Editor model refresh failed", exc_info=True)
            self.message_label.setText(str(exc))
        finally:
            self.character_combo.blockSignals(False)
        self._refresh_status()

    def selected_model_root(self):
        """Resolve the current UUID, including models renamed after refresh."""
        uuid = self.character_combo.currentData(Qt.UserRole)
        nodes = self._cmds.ls(uuid, long=True) or [] if uuid else []
        return str(nodes[0]) if len(nodes) == 1 else None

    def _refresh_status(self, *_args):
        from ..core.mmd_time_editor import inspect_model_clip
        root = self.selected_model_root()
        active = False
        valid = bool(root)
        try:
            active = bool(root and inspect_model_clip(root).get("active"))
            self.state_label.setText(self._tr("active" if active else "inactive"))
        except Exception as exc:
            valid = False
            self.state_label.setText(self._tr("invalid"))
            self.message_label.setText(str(exc))
        self.buttons["create"].setEnabled(valid and not active)
        self.buttons["remove"].setEnabled(valid and active)

    def perform_action(self, action):
        """Only explicit actions may create or remove a model's clip."""
        try:
            if action == "open":
                self._cmds.TimeEditorWindow()
                return
            from ..core.mmd_time_editor import create_model_clip, remove_model_clip
            root = self.selected_model_root()
            if not root:
                self.message_label.setText(self._tr("no_model"))
                return
            if action == "create":
                create_model_clip(root)
            elif action == "remove":
                remove_model_clip(root)
            else:
                raise ValueError("unknown Time Editor action: " + action)
            self.refresh()
            self.message_label.setText(self._tr("created" if action == "create" else "removed"))
        except Exception as exc:
            logger.error("Time Editor action failed", exc_info=True)
            self.message_label.setText(str(exc))

    @classmethod
    def show_manager(cls, **kwargs):
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        cls._instance.retranslateUi()
        cls._instance.refresh()
        cls._instance.show()
        cls._instance.raise_()
        cls._instance.activateWindow()
        return cls._instance

    @classmethod
    def close_manager(cls):
        instance, cls._instance = cls._instance, None
        if instance is not None:
            instance.close()
            instance.deleteLater()

    def _on_destroyed(self, *_args):
        if type(self)._instance is self:
            type(self)._instance = None
