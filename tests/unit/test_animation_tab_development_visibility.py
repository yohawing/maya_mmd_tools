"""Development Mode visibility contract for Animator Toolkit controls."""

import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub


install_maya_stub()
install_headless_ui_stubs()
open_maya_ui = ModuleType("maya.OpenMayaUI")
open_maya_ui.MQtUtil = Mock()
sys.modules["maya.OpenMayaUI"] = open_maya_ui

from mmd_tools.ui.tabs.animation_tab import AnimationTab  # noqa: E402


class _PickerTabs:
    def __init__(self, index=0):
        self.index = index

    def currentIndex(self):
        return self.index


class _VisibilityTarget:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = bool(visible)


def _view():
    return SimpleNamespace(
        TAB_BODY=AnimationTab.TAB_BODY,
        TAB_FINGER=AnimationTab.TAB_FINGER,
        picker_tabs=_PickerTabs(AnimationTab.TAB_BODY),
        tools_group=_VisibilityTarget(),
        control_rig_group=_VisibilityTarget(),
    )


class AnimationTabDevelopmentVisibilityTest(unittest.TestCase):
    """Keep unfinished MMD Control Rig actions private in release UI."""

    def test_mmd_control_rig_buttons_are_hidden_outside_development_mode(self):
        view = _view()

        with patch("mmd_tools.ui.tabs.animation_tab.SettingsService") as service:
            service.return_value.is_development_mode.return_value = False
            AnimationTab.refresh_development_mode_visibility(view)

        self.assertFalse(view.control_rig_group.visible)

    def test_mmd_control_rig_buttons_are_visible_only_in_development_mode(self):
        view = _view()

        with patch("mmd_tools.ui.tabs.animation_tab.SettingsService") as service:
            service.return_value.is_development_mode.return_value = True
            AnimationTab.refresh_development_mode_visibility(view)

        self.assertTrue(view.control_rig_group.visible)
