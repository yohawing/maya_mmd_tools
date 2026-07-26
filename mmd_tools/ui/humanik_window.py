"""Standalone HumanIK (Experimental) editor window.

HUMANIK-FRONTEND-1 Phase B3: the HumanIK workflow used to live as a tab
inside ``MainWindow`` (the MMD Editor). It is now its own dockable window so
it can stay open independently of the MMD Editor. This module intentionally
does not touch the View (``mmd_tools.ui.humanik_view.HumanIkView``) or the
Presenter (``mmd_tools.ui.presenters.humanik_presenter.HumanIkPresenter``) --
both are hosted here unmodified, mirroring the dockable
``workspaceControl``/floating-window pattern already used by
``mmd_tools.ui.main_window.MainWindow`` and
``mmd_tools.ui.animator_toolset_window.AnimatorToolsetWindow``.

Lifecycle: ``MainWindow`` used to drive the presenter's
``on_tab_activated``/``on_tab_deactivated`` from tab-switch events (see
``MainWindow._on_main_tab_changed``). As a standalone window there is no tab
switch to hook, so this window instead drives the same two calls from its own
``showEvent``/``hideEvent``/``closeEvent`` -- Qt delivers these consistently
whether the window is floating or hosted inside a Maya ``workspaceControl``
(hiding/closing the workspaceControl hides/closes the widget it hosts), so
the presenter's scene scan and its subscription to
``humanik_control_rig_watch``'s warning callback stay tied to "is this window
actually visible" either way.
"""

from __future__ import annotations

# Maya modules are optional at import time: unit tests import (and patch)
# this module in a plain-Python process with no ``maya`` package available
# (see ``test_humanik_menu_actions``'s ``open_humanik_editor`` tests). Every
# code path that actually uses ``cmds``/``mui`` only runs inside Maya.
try:
    from maya import cmds
    import maya.OpenMayaUI as mui
except ImportError:  # non-Maya test process
    cmds = None  # type: ignore[assignment]
    mui = None  # type: ignore[assignment]

from .qt_compat import QVBoxLayout, QWidget, Qt, wrapInstance
from .application_state import ApplicationState
from .humanik_view import HumanIkView
from .presenters.humanik_presenter import HumanIkPresenter
from .translations import UITranslator
from ..core.logger import get_logger

logger = get_logger(__name__)


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


class HumanIkWindow(QWidget):
    """Dockable HumanIK (Experimental) window, independent of the MMD Editor.

    Hosts the existing ``HumanIkView``/``HumanIkPresenter`` pair as-is; this
    class's own responsibility is window chrome (title, dockable show/hide)
    and mapping Qt visibility events onto the presenter's activate/deactivate
    lifecycle.
    """

    WINDOW_NAME = "MMDHumanIkWindow"
    WORKSPACE_CONTROL_NAME = "MMDHumanIkWorkspaceControl"
    MINIMUM_WIDTH = 120
    PREFERRED_WIDTH = 160

    def __init__(self, parent=None):
        if parent is None:
            main_window_ptr = mui.MQtUtil.mainWindow()
            parent = wrapInstance(int(main_window_ptr), QWidget)

        super().__init__(parent)
        self.setObjectName(self.WINDOW_NAME)
        self.setMinimumWidth(self.MINIMUM_WIDTH)
        self.setWindowTitle(self._window_title())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.app_state = ApplicationState()
        self.humanik_view = HumanIkView()
        self.humanik_presenter = HumanIkPresenter(self.humanik_view, self.app_state)
        layout.addWidget(self.humanik_view)

        # Tracks whether the presenter currently believes this window is
        # active, so a spurious extra showEvent/hideEvent (Qt can deliver
        # these more than once across reparenting) never double-registers or
        # double-deregisters the control rig watch callback.
        self._lifecycle_active = False

        self.app_state.refresh_model_list()

    @staticmethod
    def _window_title() -> str:
        return UITranslator.instance().translate("humanik", "tabs")

    # -- lifecycle: visibility drives the presenter's scan/watch -----------

    def showEvent(self, event):
        super().showEvent(event)
        if not self._lifecycle_active:
            self._lifecycle_active = True
            self.humanik_presenter.on_tab_activated()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._lifecycle_active:
            self._lifecycle_active = False
            self.humanik_presenter.on_tab_deactivated()

    def closeEvent(self, event):
        if self._lifecycle_active:
            self._lifecycle_active = False
            self.humanik_presenter.on_tab_deactivated()
        super().closeEvent(event)

    def retranslateUi(self):
        """Re-apply the window title and the hosted view's translations."""
        self.setWindowTitle(self._window_title())
        if hasattr(self.humanik_view, "retranslateUi"):
            self.humanik_view.retranslateUi()

    # -- show/close, mirroring MainWindow.show_window / AnimatorToolsetWindow --

    def show_window(self, dockable=True):
        """Show as an initially floating dockable panel or a plain Qt window."""
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
                label=self._window_title(),
                initialWidth=self.PREFERRED_WIDTH,
                minimumWidth=self.MINIMUM_WIDTH,
                initialHeight=640,
                widthProperty="free",
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
            self.show()
            # Apply the compact width after the first layout activation;
            # otherwise Qt can replace the pre-show resize with its wider
            # content sizeHint on a freshly constructed floating window.
            self.resize(self.PREFERRED_WIDTH, 280)
            self.raise_()
            self.activateWindow()

    def close_window(self):
        """Close and clean up the workspace control (if one hosted this window)."""
        ws = self.WORKSPACE_CONTROL_NAME
        if cmds.workspaceControl(ws, exists=True):
            cmds.workspaceControl(ws, e=True, close=True)
            # Closing a workspaceControl can already destroy it (observed on
            # Maya 2024), so re-check before deleteUI to avoid a "not found"
            # RuntimeError on the cleanup path.
            if cmds.workspaceControl(ws, exists=True):
                cmds.deleteUI(ws)
        self.close()
        self.setParent(None)
        self.deleteLater()


_window: "HumanIkWindow | None" = None


def show_humanik_window(dockable: bool = True) -> HumanIkWindow:
    """Show the singleton HumanIK Editor window, raising it if already open.

    Unlike ``AnimatorToolsetWindow`` (which is closed and rebuilt on every
    open), this window is a true singleton: repeated menu invocations must
    not re-create the presenter (which would re-subscribe to
    ``humanik_control_rig_watch``) or discard the user's in-progress state.
    Instead an existing live window is simply raised/focused.
    """
    global _window

    if _window is not None:
        try:
            already_visible = _window.isVisible()
        except RuntimeError:
            # The underlying C++ Qt object was already deleted (e.g. the
            # workspaceControl was torn down outside of close_window()).
            _window = None
        else:
            if already_visible:
                _window.raise_()
                _window.activateWindow()
                try:
                    if dockable and cmds.workspaceControl(
                        _window.WORKSPACE_CONTROL_NAME, exists=True
                    ):
                        _raise_workspace_control(_window.WORKSPACE_CONTROL_NAME)
                except Exception:
                    pass
                return _window
            # Not currently visible (e.g. hidden/closed): re-show the same
            # instance rather than constructing a second presenter.
            try:
                _window.show_window(dockable=dockable)
                return _window
            except Exception:
                logger.debug("HumanIK window re-show failed; recreating", exc_info=True)
                close_humanik_window()

    window = HumanIkWindow()
    window.show_window(dockable=dockable)
    _window = window
    return window


def close_humanik_window() -> None:
    """Close and drop the singleton HumanIK Editor window, if one exists."""
    global _window

    if _window is not None:
        try:
            _window.close_window()
        except Exception:
            logger.debug("HumanIK window close failed", exc_info=True)
        _window = None

    ws = HumanIkWindow.WORKSPACE_CONTROL_NAME
    try:
        if cmds.workspaceControl(ws, exists=True):
            cmds.deleteUI(ws)
    except Exception:
        pass


def refresh_humanik_window_for_scene_change() -> bool:
    """Refresh the singleton editor after Maya replaces the current scene."""
    if _window is None:
        return False
    try:
        _window.humanik_presenter.on_scene_changed()
    except Exception:
        logger.debug("HumanIK window scene-change refresh failed", exc_info=True)
        return False
    return True
