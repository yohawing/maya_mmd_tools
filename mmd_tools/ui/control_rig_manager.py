"""Modeless manager for the MMD-native Control Rig.

The Animator picker deliberately contains no lifecycle operations.  This
window is the single owner of Setup, Bake, Restore, and Delete.
Reading the character list and refreshing the selected row only queries Maya;
scene mutation happens exclusively after an explicit action button click.
"""

from __future__ import annotations

from ..core.logger import get_logger
from ..services.scene_model_service import SceneModelService
from .combo_box_utils import add_combo_item_with_tooltip, configure_model_combo_width
from .qt_compat import (
    QLabel,
    QCheckBox,
    QLineEdit,
    QTimer,
    QScrollArea,
    QPushButton,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
    Qt,
    Signal,
    wrapInstance,
)
from .translations import UITranslator

logger = get_logger(__name__)


class ControlRigManagerWindow(QWidget):
    """Single-instance modeless Control Rig lifecycle manager."""

    WINDOW_NAME = "MMDControlRigManagerWindow"
    _instance: "ControlRigManagerWindow | None" = None
    state_changed = Signal(str, str)

    _ACTION_LABELS = (
        ("setup", "control_rig_setup"),
        ("bake_control", "control_rig_bake_control"),
        ("bake_mmd", "control_rig_bake_mmd"),
        ("restore", "control_rig_restore"),
        ("delete", "control_rig_delete"),
    )

    def __init__(
        self,
        parent=None,
        *,
        app_state=None,
        scene_model_service=None,
        cmds_module=None,
    ):
        if parent is None:
            parent = self._maya_main_window()
        super().__init__(parent)
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowFlags(Qt.Window)
        self.setWindowModality(Qt.NonModal)
        self._app_state = app_state
        self._cmds = cmds_module
        self._scene_model_service = scene_model_service or (
            getattr(app_state, "scene_model_service", None) or SceneModelService(cmds_module=cmds_module)
        )
        self._model_records: list[dict[str, str]] = []
        self._translator = UITranslator.instance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.character_combo = QComboBox()
        self.character_combo.setObjectName("ControlRigCharacterCombo")
        configure_model_combo_width(self.character_combo, minimum_width=300)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("ControlRigRefreshButton")
        header = QHBoxLayout()
        header.addWidget(QLabel())
        header.addWidget(self.character_combo, 1)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        actions_group = QGroupBox()
        actions_layout = QGridLayout(actions_group)
        self.action_buttons: dict[str, QPushButton] = {}
        for index, (action, translation_key) in enumerate(self._ACTION_LABELS):
            button = QPushButton()
            button.setObjectName(f"ControlRigAction_{action}")
            button.clicked.connect(lambda _checked=False, key=action: self.perform_action(key))
            self.action_buttons[action] = button
            actions_layout.addWidget(button, index // 2, index % 2)
        layout.addWidget(actions_group)

        self.secondary_group = QGroupBox()
        secondary_layout = QVBoxLayout(self.secondary_group)
        self.secondary_status = QLabel()
        secondary_layout.addWidget(self.secondary_status)
        self.secondary_buttons = {}
        secondary_actions = QGridLayout()
        for index, action in enumerate(("build", "remove", "bake")):
            button = QPushButton()
            button.setObjectName(f"SecondaryBoneAction_{action}")
            button.clicked.connect(lambda _checked=False, key=action: self.perform_secondary_action(key))
            self.secondary_buttons[action] = button
            secondary_actions.addWidget(button, 0, index)
        secondary_layout.addLayout(secondary_actions)
        self.secondary_visibility_label = QLabel()
        secondary_layout.addWidget(self.secondary_visibility_label)
        groups_widget = QWidget()
        self.secondary_groups_layout = QVBoxLayout(groups_widget)
        groups_scroll = QScrollArea()
        groups_scroll.setWidgetResizable(True)
        groups_scroll.setMaximumHeight(180)
        groups_scroll.setWidget(groups_widget)
        secondary_layout.addWidget(groups_scroll)
        self._secondary_group_state = ()
        layout.addWidget(self.secondary_group)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setObjectName("ControlRigMessage")
        layout.addWidget(self.message_label)
        layout.addStretch(1)

        self.refresh_btn.clicked.connect(self.refresh)
        self.character_combo.currentIndexChanged.connect(self._on_character_changed)
        self.destroyed.connect(self._on_destroyed)
        self.retranslateUi()
        self.refresh()
        self._secondary_timer = QTimer(self)
        self._secondary_timer.setInterval(1000)
        self._secondary_timer.timeout.connect(self._refresh_secondary_status)
        self._secondary_timer.start()

    @staticmethod
    def _maya_main_window():
        """Resolve Maya's main QWidget without making headless tests fail."""

        try:
            import maya.OpenMayaUI as mui

            pointer = mui.MQtUtil.mainWindow()
            if pointer:
                return wrapInstance(int(pointer), QWidget)
        except Exception:
            return None
        return None

    @classmethod
    def show_manager(cls, **kwargs) -> "ControlRigManagerWindow":
        """Show or raise the one process-local manager instance."""

        instance = cls._instance
        if instance is None:
            instance = cls(**kwargs)
            cls._instance = instance
        else:
            app_state = kwargs.get("app_state")
            if app_state is not None:
                instance._app_state = app_state
        instance.refresh()
        instance.show()
        instance.raise_()
        instance.activateWindow()
        return instance

    @classmethod
    def close_manager(cls) -> None:
        """Close and release the manager singleton (plugin teardown hook)."""

        instance = cls._instance
        cls._instance = None
        if instance is None:
            return
        try:
            instance.close()
            instance.deleteLater()
        except Exception:
            logger.debug("Control Rig Manager close failed", exc_info=True)

    def closeEvent(self, event):
        """Hide on user close while retaining singleton identity for reopen."""

        event.accept()

    def _on_destroyed(self, *_args) -> None:
        """Drop a stale singleton if Maya tears down the QWidget externally."""

        if type(self)._instance is self:
            type(self)._instance = None

    def retranslateUi(self) -> None:
        """Translate manager chrome in place without refreshing scene state."""

        def tr(key: str) -> str:
            return self._translator.translate(key, "control_rig_manager")
        self.setWindowTitle(tr("window_title"))
        self.refresh_btn.setText(tr("refresh"))
        self.message_label.setText("")
        self.secondary_group.setTitle(tr("secondary_title"))
        self.secondary_visibility_label.setText(tr("secondary_visibility"))
        self.secondary_group.setToolTip(tr("secondary_title_tooltip"))
        self.secondary_status.setToolTip(tr("secondary_status_tooltip"))
        self.secondary_visibility_label.setToolTip(tr("secondary_visibility_tooltip"))
        for action, button in self.secondary_buttons.items():
            button.setText(tr(f"secondary_{action}"))
            button.setToolTip(tr(f"secondary_{action}_tooltip"))
        for action, _translation_key in self._ACTION_LABELS:
            button = self.action_buttons[action]
            button.setText(tr(f"{action}"))
            button.setToolTip(tr(f"{action}_tooltip"))

    def refresh_for_language_change(self) -> None:
        """Rebuild locale-dependent model labels after a language switch."""

        self.refresh()

    def refresh(self) -> None:
        """Read model roots/UUIDs and metadata; never writes to the scene."""

        selected_uuid = self.selected_uuid()
        try:
            roots = self._scene_model_service.list_mmd_models() or []
        except Exception:
            logger.debug("Control Rig Manager model listing failed", exc_info=True)
            roots = []
        records = []
        for root in roots:
            uuid = self._node_uuid(root)
            if not uuid:
                continue
            try:
                display_name = self._scene_model_service.get_model_display_name(
                    root,
                    language=self._translator.get_language(),
                )
            except Exception:
                display_name = root
            records.append({"root": str(root), "uuid": uuid, "label": str(display_name or root)})
        self._model_records = records

        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for record in records:
            label = record["label"]
            if label == record["root"]:
                label = f"{label} [{record['uuid']}]"
            add_combo_item_with_tooltip(self.character_combo, label, record["uuid"])
        if selected_uuid:
            for index, record in enumerate(records):
                if record["uuid"] == selected_uuid:
                    self.character_combo.setCurrentIndex(index)
                    break
        self.character_combo.blockSignals(False)
        self._sync_selected_state()

    def selected_uuid(self) -> str | None:
        """Return the UUID authority held by the current combo item."""

        try:
            index = self.character_combo.currentIndex()
            if index < 0:
                return None
            value = self.character_combo.itemData(index, Qt.UserRole)
            if value:
                return str(value)
        except Exception:
            pass
        try:
            index = self.character_combo.currentIndex()
            return self._model_records[index]["uuid"]
        except (IndexError, TypeError, AttributeError):
            return None

    def selected_model_root(self) -> str | None:
        """Resolve the selected UUID back to a live model root."""

        uuid = self.selected_uuid()
        if not uuid:
            return None
        cmds = self._maya_cmds()
        try:
            matches = cmds.ls(uuid, long=True) or []
        except Exception:
            matches = []
        if len(matches) != 1:
            return None
        root = str(matches[0])
        if any(record["uuid"] == uuid and record["root"] == root for record in self._model_records):
            return root
        # Maya may return a canonical path while the service keeps a short
        # path.  Match by UUID, never by a mutable display name.
        for record in self._model_records:
            if record["uuid"] == uuid:
                return record["root"]
        return None

    def perform_action(self, action: str) -> None:
        """Execute one explicit lifecycle transaction for the selected UUID."""

        root = self.selected_model_root()
        if not root:
            self._set_status("status_no_character")
            return
        try:
            from ..core.mmd_control_rig_builder import build_mmd_control_rig
            from ..core.mmd_control_rig_motion import (
                bake_mmd_control_rig,
                enter_mmd_control_rig_edit,
                restore_and_remove_mmd_control_rig,
                restore_mmd_control_rig_attached,
            )

            if action == "setup":
                build_mmd_control_rig(root)
                metadata = enter_mmd_control_rig_edit(root)
            elif action == "bake_control":
                metadata = enter_mmd_control_rig_edit(root)
            elif action == "bake_mmd":
                metadata = bake_mmd_control_rig(root)
            elif action == "restore":
                metadata = restore_mmd_control_rig_attached(root)
            elif action == "delete":
                # Delete is a user-facing lifecycle action, so it must first
                # return EDIT/BAKED rigs to the MMD-owned graph.  The restore
                # transaction reinstates the exact pre-EDIT MMD connections
                # and channel values before the UUID-owned controls vanish.
                removed = restore_and_remove_mmd_control_rig(root)
                metadata = None
                self._set_status("status_deleted" if removed else "status_not_found")
            else:
                raise ValueError(f"unknown Control Rig action: {action}")

            if action != "delete":
                self._set_status(
                    "status_not_found" if not metadata else "status_transition",
                )
            self.state_changed.emit(root, action)
            self.refresh()
        except Exception as exc:
            logger.error("Control Rig Manager action failed", exc_info=True)
            self._set_status("status_error", error=exc)

    def _on_character_changed(self, _index: int) -> None:
        self.message_label.clear()
        self._sync_selected_state()

    def _sync_selected_state(self) -> None:
        """Clear stale messages when the selected character is unavailable."""

        if self.selected_model_root() is None:
            self.message_label.clear()
        self._refresh_secondary_status(rebuild_groups=True)

    def _refresh_secondary_status(self, *, rebuild_groups=False) -> None:
        """Query existing physics authority without changing the scene."""
        if not rebuild_groups and not self.isVisible():
            return
        root = self.selected_model_root()
        state = {"exists": False, "physics_enabled": False, "groups": []}
        try:
            if root:
                from ..core import secondary_bone_controls

                state = secondary_bone_controls.inspect(root, cmds_module=self._maya_cmds())
        except Exception as exc:
            self._set_status("status_error", error=exc)
            self.secondary_status.setText(str(exc))
            for button in self.secondary_buttons.values():
                button.setEnabled(False)
            return
        exists = bool(state["exists"])
        self.secondary_buttons["build"].setEnabled(bool(root) and not exists and bool(state.get("can_build")))
        self.secondary_buttons["remove"].setEnabled(exists)
        self.secondary_buttons["bake"].setEnabled(exists and bool(state["physics_enabled"]))
        status = ("secondary_absent" if state.get("can_build") else "secondary_requires_rig") if not exists else (
            "secondary_physics_on" if state["physics_enabled"] else "secondary_physics_off"
        )
        self.secondary_status.setText(self._translator.translate(status, "control_rig_manager"))
        group_state = tuple((group["id"], group["label"], group["visible"], group["count"]) for group in state["groups"])
        if rebuild_groups or group_state != getattr(self, "_secondary_group_state", ()):
            self._secondary_group_state = group_state
            while self.secondary_groups_layout.count():
                item = self.secondary_groups_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            for group in state["groups"]:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                visibility = QCheckBox(str(group["count"]))
                visibility.setChecked(group["visible"])
                label = QLineEdit(group["label"])
                visibility.setToolTip(self._translator.translate("secondary_visibility_tooltip", "control_rig_manager"))
                label.setToolTip(self._translator.translate("secondary_label_tooltip", "control_rig_manager"))
                group_id = group["id"]
                visibility.toggled.connect(
                    lambda visible, key=group_id, owner=root: self._edit_secondary_group(owner, key, visible=visible)
                )
                label.editingFinished.connect(
                    lambda field=label, key=group_id, owner=root: self._edit_secondary_group(owner, key, label=field.text())
                )
                row_layout.addWidget(visibility)
                row_layout.addWidget(label)
                self.secondary_groups_layout.addWidget(row)

    def _edit_secondary_group(self, root, group_id, *, visible=None, label=None) -> None:
        """Edit only the controller group's presentation metadata."""
        try:
            from ..core import secondary_bone_controls

            if visible is not None:
                secondary_bone_controls.set_group_visibility(root, group_id, visible, cmds_module=self._maya_cmds())
            if label is not None:
                secondary_bone_controls.rename_group(root, group_id, label, cmds_module=self._maya_cmds())
        except Exception as exc:
            self._set_status("status_error", error=exc)

    def perform_secondary_action(self, action: str) -> None:
        """Dispatch an explicit secondary-controller lifecycle operation."""
        root = self.selected_model_root()
        if not root:
            self._set_status("status_no_character")
            return
        try:
            from ..core import secondary_bone_controls

            cmds = self._maya_cmds()
            if action == "build":
                secondary_bone_controls.build(root, cmds_module=cmds)
            elif action == "remove":
                secondary_bone_controls.remove(root, cmds_module=cmds)
            elif action == "bake":
                start = cmds.playbackOptions(query=True, minTime=True)
                end = cmds.playbackOptions(query=True, maxTime=True)
                secondary_bone_controls.bake(root, start, end, cmds_module=cmds)
            else:
                raise ValueError(f"unknown secondary controller action: {action}")
            self._set_status("status_transition")
            self.state_changed.emit(root, f"secondary_{action}")
            self.refresh()
        except Exception as exc:
            logger.error("Secondary controller action failed", exc_info=True)
            self._set_status("status_error", error=exc)

    def _set_status(self, key: str, **values) -> None:
        message = self._translator.translate(key, "control_rig_manager")
        try:
            message = message.format(**values)
        except (KeyError, ValueError):
            pass
        self.message_label.setText(message)

    def _maya_cmds(self):
        if self._cmds is not None:
            return self._cmds
        try:
            from maya import cmds

            return cmds
        except Exception:
            return None

    def _node_uuid(self, root: str) -> str | None:
        cmds = self._maya_cmds()
        if cmds is None:
            return None
        try:
            values = cmds.ls(root, uuid=True) or []
            return str(values[0]) if values else None
        except Exception:
            return None


def open_control_rig_manager(**kwargs) -> ControlRigManagerWindow:
    """Public helper used by plugin_main and the Animator footer launcher."""

    return ControlRigManagerWindow.show_manager(**kwargs)
