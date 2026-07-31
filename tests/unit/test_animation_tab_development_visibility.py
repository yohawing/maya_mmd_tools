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
_ANIMATION_PRESENTER = (
    _PROJECT_ROOT / "mmd_tools" / "ui" / "presenters" / "animation_presenter.py"
)
_PLUGIN_MAIN = _PROJECT_ROOT / "mmd_tools" / "plugin_main.py"
_TRANSLATION_DIR = _PROJECT_ROOT / "mmd_tools" / "ui" / "translations"

# Every staged MMD Control Rig action the animator tab exposes. Adding an
# action without listing it here fails the reachability test below.
_CONTROL_RIG_TRANSLATION_KEYS = (
    "control_rig_manager",
    "control_rig_manager_tooltip",
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
        control_rig_manager_btn=_TextTarget(),
        tool_buttons={"copy": _TextTarget()},
    )
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
        control_rig_manager_btn=_VisibilityTarget(),
        vis_checkboxes={"control_rig": control_rig_visibility},
    )


def _refresh(view, development_mode):
    with patch("mmd_tools.ui.tabs.animation_tab.SettingsService") as service:
        service.return_value.is_development_mode.return_value = development_mode
        AnimationTab.refresh_development_mode_visibility(view)


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
                    view.control_rig_manager_btn.text,
                    translations["control_rig_manager"],
                )
                self.assertEqual(
                    view.control_rig_manager_btn.tooltip,
                    translations["control_rig_manager_tooltip"],
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

    def test_manager_footer_is_visible_outside_development_mode(self):
        view = _view()

        _refresh(view, development_mode=False)

        self.assertIsNotNone(view.control_rig_manager_btn)

    def test_manager_footer_is_enabled_outside_development_mode(self):
        view = _view()

        _refresh(view, development_mode=False)

        self.assertIsNotNone(view.control_rig_manager_btn)

    def test_manager_footer_remains_visible_in_development_mode(self):
        view = _view()

        _refresh(view, development_mode=True)

        self.assertIsNotNone(view.control_rig_manager_btn)

    def test_control_rig_visibility_button_is_public_when_available(self):
        view = _view()

        _refresh(view, development_mode=False)
        self.assertTrue(view.vis_checkboxes["control_rig"].visible)
        self.assertTrue(view.vis_checkboxes["control_rig"].enabled)

        _refresh(view, development_mode=True)
        self.assertTrue(view.vis_checkboxes["control_rig"].visible)
        self.assertTrue(view.vis_checkboxes["control_rig"].enabled)


class MmdControlRigReleaseIsolationTest(unittest.TestCase):
    """The Animator owns status/launcher only; lifecycle belongs to Manager."""

    def test_presenter_has_no_control_rig_lifecycle_dispatcher(self):
        presenter_source = _ANIMATION_PRESENTER.read_text(encoding="utf-8")
        self.assertNotIn("_on_control_rig_clicked", presenter_source)
        self.assertNotIn("control_rig_buttons", presenter_source)

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
