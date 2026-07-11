"""Physics main-tab activation refresh wiring tests."""

import sys
from types import ModuleType
from unittest.mock import Mock

from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub

install_maya_stub()
install_headless_ui_stubs()
open_maya_ui = ModuleType("maya.OpenMayaUI")
open_maya_ui.MQtUtil = Mock()
sys.modules["maya.OpenMayaUI"] = open_maya_ui

from mmd_tools.ui.main_window import MainWindow  # noqa: E402


class _Tabs:
    def __init__(self, widgets):
        self.widgets = widgets

    def widget(self, index):
        return self.widgets[index]


def test_physics_refreshes_only_when_its_main_tab_activates():
    physics_tab = object()
    other_tab = object()
    presenter = Mock()
    window = type(
        "Window",
        (),
        {
            "physics_tab": physics_tab,
            "physics_presenter": presenter,
            "tab_widget": _Tabs([other_tab, physics_tab]),
        },
    )()

    MainWindow._on_main_tab_changed(window, 0)
    presenter.refresh_physics.assert_not_called()

    MainWindow._on_main_tab_changed(window, 1)
    presenter.refresh_physics.assert_called_once_with()
