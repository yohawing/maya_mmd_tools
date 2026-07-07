"""Standalone dockable window for the Animator Toolset (dev-mode only)."""

from maya import cmds
import maya.OpenMayaUI as mui

from .qt_compat import QVBoxLayout, QWidget, Qt, wrapInstance
from .application_state import ApplicationState
from .tabs.animation_tab import AnimationTab
from .presenters.animation_presenter import AnimationPresenter


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
        self.setWindowTitle("Animator Toolset")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.app_state = ApplicationState()
        self.animation_tab = AnimationTab()
        self.animation_presenter = AnimationPresenter(
            self.animation_tab, self.app_state
        )
        layout.addWidget(self.animation_tab)

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
                label="Animator Toolset",
                tabToControl=["AttributeEditor", -1],
                initialWidth=420,
                initialHeight=700,
                widthProperty="preferred",
                retain=False,
                floating=True,
            )
            cmds.control(self.WINDOW_NAME, e=True, parent=ws)
        else:
            self.setWindowFlags(Qt.Window)
            self.resize(420, 700)
            self.show()

    def close_window(self):
        """Close and clean up the workspace control."""
        ws = self.WORKSPACE_CONTROL_NAME
        if cmds.workspaceControl(ws, exists=True):
            cmds.workspaceControl(ws, e=True, close=True)
            cmds.deleteUI(ws)
        self.close()
        self.setParent(None)
        self.deleteLater()
