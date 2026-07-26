"""Development Mode visibility contract for Animator Toolkit controls.

The MMD-native Control Rig ships as a Development Mode-only, unsupported
surface (``MMD-CONTROL-RIG-RELEASE-DISPOSITION-1``).  These tests fix the two
release conditions for that disposition: normal mode cannot reach Create,
Attach/Edit, Bake, Restore, Delete or Diagnostics, and no control rig
transaction state outlives a scene new/open.
"""

import ast
import pathlib
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


_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CORE = _PROJECT_ROOT / "mmd_tools" / "core"
_ANIMATION_TAB = _PROJECT_ROOT / "mmd_tools" / "ui" / "tabs" / "animation_tab.py"
_ANIMATION_PRESENTER = (
    _PROJECT_ROOT / "mmd_tools" / "ui" / "presenters" / "animation_presenter.py"
)
_PLUGIN_MAIN = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"

# Every staged MMD Control Rig action the animator tab exposes. Adding an
# action without listing it here fails the reachability test below.
_CONTROL_RIG_ACTIONS = frozenset(
    {"create", "edit", "bake_mmd", "restore", "delete", "diagnostics"}
)


class _PickerTabs:
    def __init__(self, index=0):
        self.index = index

    def currentIndex(self):
        return self.index


class _VisibilityTarget:
    def __init__(self):
        self.visible = None
        self.enabled = None

    def setVisible(self, visible):
        self.visible = bool(visible)

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


def _view():
    control_rig_visibility = _VisibilityTarget()
    control_rig_visibility._control_rig_available = True
    return SimpleNamespace(
        TAB_BODY=AnimationTab.TAB_BODY,
        TAB_FINGER=AnimationTab.TAB_FINGER,
        picker_tabs=_PickerTabs(AnimationTab.TAB_BODY),
        tools_group=_VisibilityTarget(),
        control_rig_group=_VisibilityTarget(),
        vis_checkboxes={"control_rig": control_rig_visibility},
    )


def _refresh(view, development_mode):
    with patch("mmd_tools.ui.tabs.animation_tab.SettingsService") as service:
        service.return_value.is_development_mode.return_value = development_mode
        AnimationTab.refresh_development_mode_visibility(view)


def _declared_control_rig_actions():
    """Read the action keys wired into ``self.control_rig_buttons``.

    The keys live in the literal tuple driving the button-building loop, so
    parsing the source keeps the contract honest without constructing Qt
    widgets in a headless test run.
    """
    tree = ast.parse(_ANIMATION_TAB.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        assigns_buttons = any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "control_rig_buttons"
            for statement in ast.walk(node)
            if isinstance(statement, ast.Assign)
            for target in statement.targets
        )
        if not assigns_buttons:
            continue
        pairs = ast.walk(node.iter)
        return {
            element.elts[0].value
            for element in pairs
            if isinstance(element, ast.Tuple)
            and len(element.elts) == 2
            and isinstance(element.elts[0], ast.Constant)
            and isinstance(element.elts[0].value, str)
        }
    return set()


class AnimationTabDevelopmentVisibilityTest(unittest.TestCase):
    """Keep unfinished MMD Control Rig actions private in release UI."""

    def test_mmd_control_rig_buttons_are_hidden_outside_development_mode(self):
        view = _view()

        _refresh(view, development_mode=False)

        self.assertFalse(view.control_rig_group.visible)

    def test_mmd_control_rig_buttons_are_disabled_outside_development_mode(self):
        """Hiding alone still leaves buttons clickable from a scripted view."""
        view = _view()

        _refresh(view, development_mode=False)

        self.assertFalse(view.control_rig_group.enabled)

    def test_mmd_control_rig_buttons_are_visible_only_in_development_mode(self):
        view = _view()

        _refresh(view, development_mode=True)

        self.assertTrue(view.control_rig_group.visible)
        self.assertTrue(view.control_rig_group.enabled)

    def test_control_rig_visibility_button_is_development_mode_only(self):
        view = _view()

        _refresh(view, development_mode=False)
        self.assertFalse(view.vis_checkboxes["control_rig"].visible)
        self.assertFalse(view.vis_checkboxes["control_rig"].enabled)

        _refresh(view, development_mode=True)
        self.assertTrue(view.vis_checkboxes["control_rig"].visible)
        self.assertTrue(view.vis_checkboxes["control_rig"].enabled)


class MmdControlRigReleaseIsolationTest(unittest.TestCase):
    """Fix the release disposition contract for the unsupported rig surface."""

    def test_every_control_rig_action_lives_in_the_gated_group(self):
        """No staged action may escape ``control_rig_group`` into normal UI."""
        self.assertEqual(_CONTROL_RIG_ACTIONS, _declared_control_rig_actions())

    def test_control_rig_actions_are_dispatched_only_from_the_gated_map(self):
        """The presenter must not route an action the gated group cannot raise."""
        presenter_source = _ANIMATION_PRESENTER.read_text(encoding="utf-8")
        self.assertIn(
            "control_rig_buttons",
            presenter_source,
            "control rig clicks are no longer wired from the gated button map",
        )

        tree = ast.parse(presenter_source)
        dispatch = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_on_control_rig_clicked"
        )
        compared = {
            operand.value
            for node in ast.walk(dispatch)
            if isinstance(node, ast.Compare)
            for operand in node.comparators
            if isinstance(operand, ast.Constant) and isinstance(operand.value, str)
        }

        self.assertTrue(compared, "dispatch no longer branches on the action key")
        self.assertLessEqual(compared, _CONTROL_RIG_ACTIONS)

    def test_no_mmd_control_rig_entry_point_on_the_maya_menu(self):
        """The MMD menu must not offer the unsupported rig in normal mode."""
        plugin_main = _PLUGIN_MAIN.read_text(encoding="utf-8")

        self.assertNotIn("mmd_control_rig", plugin_main)

    def test_control_rig_modules_hold_no_process_owned_transaction_state(self):
        """Scene new/open cannot strand a transaction that lives in metadata."""
        for name in (
            "mmd_control_rig_motion",
            "mmd_control_rig_builder",
            "mmd_control_rig_analyzer",
        ):
            with self.subTest(module=name):
                tree = ast.parse((_CORE / f"{name}.py").read_text(encoding="utf-8"))

                rebinds = [
                    node for node in ast.walk(tree) if isinstance(node, ast.Global)
                ]
                self.assertEqual(
                    [],
                    rebinds,
                    f"{name} rebinds module state; a transaction could outlive a scene",
                )

                for node in tree.body:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if not isinstance(target, ast.Name):
                            continue
                        if not isinstance(node.value, (ast.Dict, ast.List, ast.Set)):
                            continue
                        # UPPER_SNAKE_CASE names are read-only lookup tables.
                        if target.id.lstrip("_").isupper():
                            continue
                        self.fail(
                            f"{name}.{target.id} is module-level mutable state; "
                            "control rig transactions must live in scene metadata"
                        )
