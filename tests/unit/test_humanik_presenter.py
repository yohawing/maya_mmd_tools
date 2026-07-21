"""Unit tests for the HumanIK tab presenter and the tab's static display maps.

The HumanIK tab is a thin status display over
``HumanIkFrontendSession.describe_frontend_state()`` plus buttons that
dispatch to the already-tested ``humanik_menu_actions`` functions (see
``tests/unit/test_humanik_menu_actions.py``); this suite does not re-verify
menu action behavior, only that the presenter wires buttons to dispatch +
refresh correctly and renders state without scanning the scene while the tab
is inactive.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from tests.common.mock_ui import attach_mocks

from mmd_tools.core.humanik_frontend import (
    REASON_ALREADY_CHARACTERIZED_OTHER_PROFILE,
    REASON_MODEL_IS_SOURCE,
    REASON_MODEL_REQUIRED,
    REASON_NO_ACTIVE_PREVIEW,
    REASON_NO_SOURCE,
    REASON_NOT_CHARACTERIZED,
    REASON_NOTHING_TO_RESTORE,
    REASON_PREVIEW_ACTIVE,
    REASON_PROFILE_MISMATCH,
    REASON_TARGET_IS_SOURCE,
)
from mmd_tools.ui.presenters.humanik_presenter import HumanIkPresenter
from mmd_tools.ui.tabs.humanik_tab import (
    ACTION_KEY_TO_BUTTON,
    HumanIkTab,
    REASON_CODE_TRANSLATION_KEYS,
)
from mmd_tools.ui.translations import UITranslator

_ALL_REASON_CODES = {
    REASON_PREVIEW_ACTIVE,
    REASON_NOT_CHARACTERIZED,
    REASON_NO_SOURCE,
    REASON_TARGET_IS_SOURCE,
    REASON_PROFILE_MISMATCH,
    REASON_MODEL_IS_SOURCE,
    REASON_NO_ACTIVE_PREVIEW,
    REASON_ALREADY_CHARACTERIZED_OTHER_PROFILE,
    REASON_NOTHING_TO_RESTORE,
    REASON_MODEL_REQUIRED,
}


def _action_allowed():
    return {"allowed": True, "reasonCode": None, "reasonText": None}


def _action_blocked(reason_code, reason_text="blocked"):
    return {"allowed": False, "reasonCode": reason_code, "reasonText": reason_text}


class _FakeSession:
    def __init__(self, state=None, error=None):
        self.state = state if state is not None else {}
        self.error = error
        self.describe_calls = []

    def describe_frontend_state(self, model_root=None):
        self.describe_calls.append(model_root)
        if self.error is not None:
            raise self.error
        return self.state


class _FakeActionsModule:
    """Stand-in for ``humanik_menu_actions`` with exactly the surface the presenter uses."""

    def __init__(self, session=None, display_model_root=None):
        self.session = session or _FakeSession()
        self.display_model_root = display_model_root
        self.dispatch_calls = []
        self.bake_calls = []
        self.resolve_calls = 0

    def get_humanik_session(self):
        return self.session

    def dispatch_action(self, action_name):
        self.dispatch_calls.append(action_name)

    def bake_to_mmd_rig(self, start=None, end=None):
        self.bake_calls.append((start, end))

    def resolve_selected_model_root_for_display(self, *, cmds_module=None):
        self.resolve_calls += 1
        return self.display_model_root


class _FakeCmds:
    """Minimal ``cmds.playbackOptions`` double."""

    def __init__(self, min_time=1.0, max_time=30.0):
        self.min_time = min_time
        self.max_time = max_time
        self.edits = []

    def playbackOptions(self, query=False, edit=False, minTime=None, maxTime=None):
        if edit:
            self.edits.append((minTime, maxTime))
            if minTime is not None:
                self.min_time = minTime
            if maxTime is not None:
                self.max_time = maxTime
            return None
        if query:
            if minTime:
                return self.min_time
            if maxTime:
                return self.max_time
        return None


def _make_mock_view():
    view = MagicMock(spec=HumanIkTab)
    attach_mocks(
        view,
        [
            "refresh_btn",
            "setup_characterize_btn",
            "enter_source_btn",
            "enter_target_btn",
            "create_control_rig_btn",
            "bake_btn",
            "restore_btn",
            "diagnostics_btn",
        ],
    )
    view.bake_frame_range.return_value = (5, 25)
    return view


def _connected_handler(mock_button):
    """Return the callback the presenter registered via ``.clicked.connect``."""
    return mock_button.clicked.connect.call_args[0][0]


class TestHumanIkPresenter(unittest.TestCase):
    def setUp(self):
        self.view = _make_mock_view()
        self.session = _FakeSession()
        self.actions = _FakeActionsModule(session=self.session)
        self.cmds = _FakeCmds(min_time=1.0, max_time=42.0)
        self.app_state = SimpleNamespace()
        self.presenter = HumanIkPresenter(
            self.view,
            self.app_state,
            actions_module=self.actions,
            cmds_module=self.cmds,
        )

    # -- (e) inactive tabs do not scan the scene ------------------------

    def test_construction_does_not_scan_the_scene(self):
        self.assertEqual(self.session.describe_calls, [])
        self.assertEqual(self.actions.resolve_calls, 0)
        self.view.set_state.assert_not_called()

    def test_on_tab_activated_triggers_a_single_refresh(self):
        self.presenter.on_tab_activated()
        self.assertEqual(len(self.session.describe_calls), 1)
        self.view.set_state.assert_called_once()

    def test_on_tab_deactivated_does_not_itself_scan(self):
        self.presenter.on_tab_activated()
        self.session.describe_calls.clear()
        self.presenter.on_tab_deactivated()
        self.assertEqual(self.session.describe_calls, [])

    # -- (a) refresh reflects session state onto the view ----------------

    def test_refresh_passes_describe_frontend_state_result_to_view(self):
        self.session.state = {"mode": "source", "controlRigs": []}
        self.actions.display_model_root = "|Model"

        self.presenter.refresh()

        self.assertEqual(self.session.describe_calls, ["|Model"])
        self.view.set_state.assert_called_once_with(self.session.state)

    def test_refresh_syncs_bake_frame_range_from_playback_options(self):
        self.presenter.refresh()

        self.view.set_bake_frame_range.assert_called_once_with(1, 42)

    def test_refresh_survives_a_describe_frontend_state_exception(self):
        self.session.error = RuntimeError("boom")

        self.presenter.refresh()

        self.view.set_state.assert_called_once_with({})

    def test_refresh_resolves_display_model_root_without_a_dialog(self):
        # resolve_selected_model_root_for_display is the display-only helper;
        # the presenter must not call resolve_model_root (which can show a
        # picker dialog or raise) for a passive status refresh.
        self.presenter.refresh()
        self.assertEqual(self.actions.resolve_calls, 1)

    # -- (d) buttons dispatch to humanik_menu_actions, then refresh -------

    def test_each_action_button_dispatches_its_stable_action_name_and_refreshes(self):
        button_to_action = {
            "setup_characterize_btn": "setup_and_characterize",
            "enter_source_btn": "enter_source_mode",
            "enter_target_btn": "enter_target_mode",
            "create_control_rig_btn": "create_control_rig",
            "restore_btn": "restore_mmd_rig",
            "diagnostics_btn": "diagnostics",
        }
        for attr, action_name in button_to_action.items():
            with self.subTest(attr=attr):
                self.actions.dispatch_calls.clear()
                self.view.set_state.reset_mock()
                handler = _connected_handler(getattr(self.view, attr))
                handler()
                self.assertEqual(self.actions.dispatch_calls, [action_name])
                self.view.set_state.assert_called_once()

    def test_bake_button_passes_spinbox_range_without_touching_playback(self):
        handler = _connected_handler(self.view.bake_btn)

        handler()

        self.assertEqual(self.cmds.edits, [])
        self.assertEqual(self.actions.bake_calls, [(5, 25)])
        self.assertEqual(self.actions.dispatch_calls, [])
        self.view.set_state.assert_called_once()

    def test_dispatch_exception_still_refreshes(self):
        def _raise(_name):
            raise RuntimeError("menu action failed")

        self.actions.dispatch_action = _raise
        handler = _connected_handler(self.view.restore_btn)

        handler()  # must not raise

        self.view.set_state.assert_called_once()

    def test_refresh_button_calls_refresh(self):
        handler = _connected_handler(self.view.refresh_btn)
        handler()
        self.view.set_state.assert_called_once()


class TestHumanIkReasonCodeTranslation(unittest.TestCase):
    """(b) Every backend reasonCode maps to a real, non-fallback display string."""

    def test_reason_code_map_covers_every_describe_frontend_state_reason(self):
        self.assertEqual(set(REASON_CODE_TRANSLATION_KEYS), _ALL_REASON_CODES)

    def test_each_mapped_key_translates_in_english_and_japanese(self):
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        try:
            for language in ("en", "ja"):
                translator.set_language(language)
                for reason_code, key in REASON_CODE_TRANSLATION_KEYS.items():
                    with self.subTest(language=language, reason_code=reason_code):
                        text = translator.translate(key, "messages")
                        # A translation miss falls back to returning the key
                        # itself; that would silently show a raw enum string
                        # in the UI, so it must never happen for a known key.
                        self.assertNotEqual(text, key)
        finally:
            translator.set_language(previous_language)

    def test_action_key_to_button_covers_every_describe_frontend_state_action(self):
        expected_actions = {
            "setup_and_characterize",
            "enter_source_mode",
            "enter_target_mode",
            "create_control_rig",
            "bake_to_mmd_rig",
            "restore_mmd_rig",
            "diagnostics",
        }
        self.assertEqual(set(ACTION_KEY_TO_BUTTON), expected_actions)


class TestHumanIkTabSetState(unittest.TestCase):
    """View-logic checks that avoid constructing a real QWidget.

    ``HumanIkTab.set_state``/``reason_text`` only touch attributes set on
    ``self`` and call ``self.tr`` -- calling them unbound against a plain
    stand-in object exercises the exact same branches as a real tab without
    needing a running QApplication.
    """

    def _make_fake_tab(self):
        fake = SimpleNamespace()
        fake.tr = lambda key, category=None: UITranslator.instance().translate(key, category)
        fake.mode_value_label = Mock()
        fake.source_value_label = Mock()
        fake.target_value_label = Mock()
        fake.control_rigs_value_label = Mock()
        fake.orphaned_warning_label = Mock()
        fake._action_buttons = {attr: Mock() for attr in ACTION_KEY_TO_BUTTON.values()}
        fake._reason_labels = {attr: Mock() for attr in ACTION_KEY_TO_BUTTON.values()}
        fake._mode_text = HumanIkTab._mode_text.__get__(fake)
        fake.reason_text = HumanIkTab.reason_text.__get__(fake)
        fake._format_binding = HumanIkTab._format_binding
        return fake

    def test_orphaned_control_rigs_show_a_warning(self):
        fake = self._make_fake_tab()
        state = {
            "mode": "neutral",
            "actions": {},
            "restoreHint": {
                "orphanedControlRigs": [{"modelRoot": "|Orphan", "character": "OrphanChar"}],
            },
        }

        HumanIkTab.set_state(fake, state)

        fake.orphaned_warning_label.show.assert_called_once()
        fake.orphaned_warning_label.setText.assert_called_once()
        (warning_text,), _ = fake.orphaned_warning_label.setText.call_args
        self.assertIn("Orphan", warning_text)

    def test_no_orphaned_control_rigs_hides_the_warning(self):
        fake = self._make_fake_tab()
        state = {"mode": "neutral", "actions": {}, "restoreHint": {"orphanedControlRigs": []}}

        HumanIkTab.set_state(fake, state)

        fake.orphaned_warning_label.hide.assert_called_once()
        fake.orphaned_warning_label.setText.assert_not_called()

    def test_blocked_action_disables_its_button_and_shows_the_reason(self):
        fake = self._make_fake_tab()
        state = {
            "mode": "neutral",
            "actions": {
                "enter_source_mode": _action_blocked(REASON_NOT_CHARACTERIZED),
                "enter_target_mode": _action_allowed(),
            },
            "restoreHint": {"orphanedControlRigs": []},
        }

        HumanIkTab.set_state(fake, state)

        enter_source_button = fake._action_buttons["enter_source_btn"]
        enter_source_button.setEnabled.assert_called_once_with(False)
        expected_text = UITranslator.instance().translate(
            "humanik_reason_not_characterized", "messages"
        )
        enter_source_button.setToolTip.assert_called_once_with(expected_text)

        enter_target_button = fake._action_buttons["enter_target_btn"]
        enter_target_button.setEnabled.assert_called_once_with(True)
        enter_target_button.setToolTip.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
