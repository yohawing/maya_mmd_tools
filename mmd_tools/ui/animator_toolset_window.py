"""Standalone dockable window for the publicly available Animator Toolset."""

from maya import cmds
import maya.OpenMayaUI as mui

from .qt_compat import QSettings, QTimer, QVBoxLayout, QWidget, Qt, wrapInstance
from .application_state import ApplicationState
from .tabs.animation_tab import AnimationTab
from .presenters.animation_presenter import AnimationPresenter
from .translations import UITranslator


def _raise_workspace_control(name: str) -> None:
    """Make an existing Maya workspaceControl visible and active when possible."""
    for kwargs in (
        {"visible": True},
        {"restore": True},
        {"raise": True},
    ):
        try:
            cmds.workspaceControl(name, e=True, **kwargs)
        except Exception:
            pass


class AnimatorToolsetWindow(QWidget):
    """Dockable Animator Toolset window, independent of the main MMD Tools window."""

    WINDOW_NAME = "MMDAnimatorToolsetWindow"
    WORKSPACE_CONTROL_NAME = "MMDAnimatorToolsetWorkspaceControl"
    MINIMUM_WIDTH = 150
    PREFERRED_WIDTH = 350
    DEFAULT_HEIGHT = 700
    SETTINGS_WIDTH_KEY = "animator_toolset/width"
    SETTINGS_HEIGHT_KEY = "animator_toolset/height"

    def __init__(self, parent=None):
        if parent is None:
            main_window_ptr = mui.MQtUtil.mainWindow()
            parent = wrapInstance(int(main_window_ptr), QWidget)

        super().__init__(parent)
        self._size_tracking_enabled = False
        self._settings = QSettings("yohawing", "maya_mmd_tools")
        self._window_width = self._setting_int(
            self.SETTINGS_WIDTH_KEY, self.PREFERRED_WIDTH, self.MINIMUM_WIDTH
        )
        self._window_height = self._setting_int(
            self.SETTINGS_HEIGHT_KEY, self.DEFAULT_HEIGHT, 1
        )
        self.setObjectName(self.WINDOW_NAME)
        self.retranslateUi()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.app_state = ApplicationState()
        self.animation_tab = AnimationTab()
        self.animation_presenter = AnimationPresenter(
            self.animation_tab, self.app_state
        )
        self.animation_tab.control_rig_manager_btn.clicked.connect(
            self.open_control_rig_manager
        )
        layout.addWidget(self.animation_tab)
        self._control_rig_manager = None
        self._control_rig_state_callback = None
        self._control_rig_manager_connected = False
        self._cleanup_done = False
        self.animation_tab.destroyed.connect(
            self.animation_presenter.disconnect_signals
        )
        self.destroyed.connect(self._on_destroyed)

        self.app_state.refresh_model_list()

    def open_control_rig_manager(self, *_args):
        """Open the modeless UUID-authoritative Control Rig Manager."""

        from mmd_tools.ui.control_rig_manager import open_control_rig_manager

        manager = open_control_rig_manager(
            app_state=self.app_state,
        )
        self._control_rig_manager = manager
        # Manager actions are scene transactions; refresh the picker state
        # after each explicit action while keeping the Animator itself read
        # only with respect to Control Rig ownership.
        if not getattr(self, "_control_rig_manager_connected", False):
            def state_callback(_root, _action):
                self.animation_presenter.refresh_for_scene_change()

            manager.state_changed.connect(state_callback)
            self._control_rig_state_callback = state_callback
            self._control_rig_manager_connected = True
        return manager

    def _setting_int(self, key: str, default: int, minimum: int) -> int:
        """Read a persisted integer while rejecting missing or invalid values."""

        try:
            return max(minimum, int(self._settings.value(key, default)))
        except (TypeError, ValueError):
            return default

    def _save_window_size(self) -> None:
        """Persist the most recent floating size for the next invocation."""

        self._settings.setValue(self.SETTINGS_WIDTH_KEY, self._window_width)
        self._settings.setValue(self.SETTINGS_HEIGHT_KEY, self._window_height)

    def _cleanup(self):
        """Detach presenter listeners before Maya destroys docked Qt children."""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self._save_window_size()
        manager = getattr(self, "_control_rig_manager", None)
        if manager is not None:
            state_callback = getattr(self, "_control_rig_state_callback", None)
            if state_callback is not None:
                try:
                    manager.state_changed.disconnect(state_callback)
                except (RuntimeError, TypeError):
                    pass
        self._control_rig_manager = None
        self._control_rig_state_callback = None
        self._control_rig_manager_connected = False
        self.animation_presenter.disconnect_signals()

    def _on_destroyed(self, *_args):
        """Cover workspaceControl teardown paths that skip ``closeEvent``."""
        self._cleanup()

    def retranslateUi(self):
        """Translate standalone window chrome and its tab in place."""
        translator = UITranslator.instance()
        self.setWindowTitle(translator.translate("window_title", "animation_toolset"))
        if hasattr(self, "animation_tab"):
            self.animation_tab.retranslateUi()
        if hasattr(self, "animation_presenter"):
            self.animation_presenter.retranslate_ui()

    def refresh_development_mode_visibility(self):
        """Refresh legacy Development Mode-only pose controls."""

        if hasattr(self, "animation_tab"):
            self.animation_tab.refresh_development_mode_visibility()

    def refresh_for_scene_change(self) -> None:
        """Refresh the presenter after Maya opens or creates a scene.

        Scene replacement invalidates the old model root and any UUID lookup;
        delegate to the presenter so ownership metadata is re-read from the
        new scene.  This is deliberately non-destructive and does not touch
        user animation curves.
        """

        if self._cleanup_done:
            return
        presenter = getattr(self, "animation_presenter", None)
        refresh = getattr(presenter, "refresh_for_scene_change", None)
        if callable(refresh):
            refresh()

    def refresh_for_language_change(self) -> None:
        """Rebuild presenter-owned labels after the application locale changes."""

        if self._cleanup_done:
            return
        presenter = getattr(self, "animation_presenter", None)
        refresh = getattr(presenter, "refresh_for_name_change", None)
        if callable(refresh):
            refresh()

    def _apply_floating_window_size(self) -> None:
        """Restore the saved size on Maya's floating Qt wrapper.

        ``workspaceControl(resizeWidth=...)`` does not resize the native
        floating wrapper on Maya 2026.  After reparenting, ``window()`` resolves
        that wrapper, so update it directly on the next Qt event-loop turn.
        """

        top_level = self.window()
        if top_level is None:
            return
        top_level.setMinimumWidth(self.MINIMUM_WIDTH)
        top_level.resize(self._window_width, self._window_height)
        self._size_tracking_enabled = True

    def show_window(self, dockable=True):
        """Show as a dockable Maya panel or a floating window."""
        if dockable:
            self.setWindowFlags(Qt.Widget)
            self.setAttribute(Qt.WA_DeleteOnClose, False)

            ws = self.WORKSPACE_CONTROL_NAME
            if cmds.workspaceControl(ws, exists=True):
                cmds.workspaceControl(ws, e=True, close=True)
                cmds.deleteUI(ws)

            self.show()

            cmds.workspaceControl(
                ws,
                label=self.windowTitle(),
                initialWidth=self._window_width,
                minimumWidth=self.MINIMUM_WIDTH,
                initialHeight=self._window_height,
                widthProperty="free",
                retain=False,
                floating=True,
            )
            cmds.control(self.WINDOW_NAME, e=True, parent=ws)
            self.show()
            # Maya can restore an older floating workspace width even after
            # deleteUI().  initialWidth does not override that restored state;
            # resizeWidth does.
            cmds.workspaceControl(
                ws,
                e=True,
                widthProperty="free",
                minimumWidth=self.MINIMUM_WIDTH,
                resizeWidth=self._window_width,
            )
            QTimer.singleShot(0, self._apply_floating_window_size)
            try:
                cmds.control(self.WINDOW_NAME, e=True, visible=True)
            except Exception:
                pass
            _raise_workspace_control(ws)
        else:
            self.setWindowFlags(Qt.Window)
            self.resize(self._window_width, self._window_height)
            self._size_tracking_enabled = True
            self.show()
            self.raise_()
            self.activateWindow()

    def close_window(self):
        """Close and clean up the workspace control."""
        self._cleanup()
        ws = self.WORKSPACE_CONTROL_NAME
        if cmds.workspaceControl(ws, exists=True):
            cmds.workspaceControl(ws, e=True, close=True)
            cmds.deleteUI(ws)
        self.close()
        self.setParent(None)
        self.deleteLater()

    def closeEvent(self, event):
        """Restore motion even when Maya closes the widget directly."""
        self._cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        """Remember user-driven size changes without writing settings per pixel."""

        super().resizeEvent(event)
        if not self._size_tracking_enabled:
            return
        top_level = self.window()
        if top_level is None:
            return
        self._window_width = max(self.MINIMUM_WIDTH, top_level.width())
        self._window_height = max(1, top_level.height())
