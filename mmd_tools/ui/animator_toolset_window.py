"""Standalone dockable window for the publicly available Animator Toolset."""

from maya import cmds
import maya.OpenMayaUI as mui

from .qt_compat import QVBoxLayout, QWidget, Qt, wrapInstance
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

    def __init__(self, parent=None):
        if parent is None:
            main_window_ptr = mui.MQtUtil.mainWindow()
            parent = wrapInstance(int(main_window_ptr), QWidget)

        super().__init__(parent)
        self.setObjectName(self.WINDOW_NAME)
        self.retranslateUi()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.app_state = ApplicationState()
        self.animation_tab = AnimationTab()
        self.animation_presenter = AnimationPresenter(
            self.animation_tab, self.app_state
        )
        layout.addWidget(self.animation_tab)
        self._cleanup_done = False
        self.animation_tab.destroyed.connect(
            self.animation_presenter.disconnect_signals
        )
        self.destroyed.connect(self._on_destroyed)

        self.app_state.refresh_model_list()

    def _cleanup(self, restore_motion=True):
        """Detach presenter listeners before Maya destroys docked Qt children."""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self.animation_presenter.disconnect_signals()
        if restore_motion:
            from ..actions.rest_pose_action import get_rest_pose_manager

            get_rest_pose_manager().return_to_motion()

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
        """Refresh Development Mode-only controls in the standalone window."""

        if hasattr(self, "animation_tab"):
            self.animation_tab.refresh_development_mode_visibility()

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
                initialWidth=420,
                initialHeight=700,
                widthProperty="preferred",
                retain=False,
                floating=True,
            )
            cmds.control(self.WINDOW_NAME, e=True, parent=ws)
            self.show()
            try:
                cmds.control(self.WINDOW_NAME, e=True, visible=True)
            except Exception:
                pass
            _raise_workspace_control(ws)
        else:
            self.setWindowFlags(Qt.Window)
            self.resize(420, 700)
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
