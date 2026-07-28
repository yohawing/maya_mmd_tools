"""Visibility contract for public Animator Toolkit Control Rig controls."""

import ast
import json
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
_TRANSLATION_DIR = _PROJECT_ROOT / "mmd_tools" / "ui" / "translations"

# Every staged MMD Control Rig action the animator tab exposes. Adding an
# action without listing it here fails the reachability test below.
_CONTROL_RIG_ACTIONS = frozenset(
    {"create", "bake_control", "bake_mmd", "restore", "delete", "diagnostics"}
)
_CONTROL_RIG_TRANSLATION_KEYS = (
    "control_rig_group_title",
    "control_rig_create",
    "control_rig_create_tooltip",
    "control_rig_bake_control",
    "control_rig_bake_control_tooltip",
    "control_rig_bake_mmd",
    "control_rig_bake_mmd_tooltip",
    "control_rig_restore",
    "control_rig_restore_tooltip",
    "control_rig_delete",
    "control_rig_delete_tooltip",
    "control_rig_diagnostics",
    "control_rig_diagnostics_tooltip",
)
_VISIBILITY_TRANSLATION_KEYS = (
    "visibility_state_visible",
    "visibility_state_reference",
    "visibility_state_hidden",
    "visibility_unavailable",
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


class _TextTarget:
    def __init__(self):
        self.text = None
        self.tooltip = None
        self.title = None
        self.visibility_labels = None
        self.visibility_unavailable = None

    def setText(self, text):
        self.text = text

    def setToolTip(self, text):
        self.tooltip = text

    def setVisibilityLabels(self, labels, unavailable_label=None):
        self.visibility_labels = dict(labels)
        self.visibility_unavailable = unavailable_label

    def setTitle(self, text):
        self.title = text


class _TabTarget:
    def __init__(self):
        self.tabs = {}

    def setTabText(self, index, text):
        self.tabs[index] = text


class _PickerTarget:
    def update_region_texts(self, **kwargs):
        pass


def _retranslate_view(data):
    """Build the smallest view double needed by ``AnimationTab.retranslateUi``."""
    view = SimpleNamespace(
        refresh_btn=_TextTarget(),
        picker_tabs=_TabTarget(),
        select_all_btn=_TextTarget(),
        clear_btn=_TextTarget(),
        visibility_label=_TextTarget(),
        body_picker=_PickerTarget(),
        finger_picker=_PickerTarget(),
        vis_checkboxes={key: _TextTarget() for key in ("mesh", "joints", "colliders", "control_rig")},
        tools_group=_TextTarget(),
        control_rig_group=_TextTarget(),
        control_rig_buttons={
            key: _TextTarget() for key in _CONTROL_RIG_ACTIONS
        },
        tool_buttons={"copy": _TextTarget()},
    )
    for key, button in view.control_rig_buttons.items():
        button._control_rig_translation_key = f"control_rig_{key}"

    def translate(key, category=None):
        if category is None:
            return data.get(key, key)
        return data[category][key]

    view.tr = translate
    return view


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
    """Keep the experimental rig public while pose helpers stay gated."""

    def test_control_rig_labels_and_tooltips_retranslate_for_every_locale(self):
        for locale in ("en", "ja", "zh_cn", "zh_tw"):
            with self.subTest(locale=locale):
                translations = json.loads(
                    (_TRANSLATION_DIR / f"{locale}.json").read_text(encoding="utf-8")
                )["animation_toolset"]
                self.assertTrue(
                    set(_CONTROL_RIG_TRANSLATION_KEYS).issubset(translations),
                    locale,
                )
                self.assertTrue(
                    set(_VISIBILITY_TRANSLATION_KEYS).issubset(translations),
                    locale,
                )
                view = _retranslate_view({"animation_toolset": translations, **translations})
                AnimationTab.retranslateUi(view)

                self.assertEqual(
                    view.control_rig_group.title,
                    translations["control_rig_group_title"],
                )
                for key, button in view.control_rig_buttons.items():
                    translation_key = f"control_rig_{key}"
                    self.assertEqual(button.text, translations[translation_key])
                    self.assertEqual(
                        button.tooltip,
                        translations[f"{translation_key}_tooltip"],
                    )
                for button in view.vis_checkboxes.values():
                    self.assertEqual(
                        button.visibility_labels["reference"],
                        translations["visibility_state_reference"],
                    )
                    self.assertEqual(
                        button.visibility_unavailable,
                        translations["visibility_unavailable"],
                    )

    def test_mmd_control_rig_buttons_are_visible_outside_development_mode(self):
        view = _view()

        _refresh(view, development_mode=False)

        self.assertTrue(view.control_rig_group.visible)

    def test_mmd_control_rig_buttons_are_enabled_outside_development_mode(self):
        view = _view()

        _refresh(view, development_mode=False)

        self.assertTrue(view.control_rig_group.enabled)

    def test_mmd_control_rig_buttons_remain_visible_in_development_mode(self):
        view = _view()

        _refresh(view, development_mode=True)

        self.assertTrue(view.control_rig_group.visible)
        self.assertTrue(view.control_rig_group.enabled)

    def test_control_rig_visibility_button_is_public_when_available(self):
        view = _view()

        _refresh(view, development_mode=False)
        self.assertTrue(view.vis_checkboxes["control_rig"].visible)
        self.assertTrue(view.vis_checkboxes["control_rig"].enabled)

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
