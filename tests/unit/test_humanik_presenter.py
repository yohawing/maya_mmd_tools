"""Unit tests for the HumanIK tab presenter and the tab's static display maps.

The HumanIK tab is a thin status display over
``HumanIkFrontendSession.describe_frontend_state()`` plus a Character/Source
combo pair (HUMANIK-FRONTEND-1 Phase B4) and the remaining action
buttons, all dispatching to the already-tested ``humanik_menu_actions``
functions (see ``tests/unit/test_humanik_menu_actions.py``); this suite does
not re-verify menu action behavior, only that the presenter:

* wires the remaining buttons to dispatch + refresh correctly;
* resolves the Character combo's selection per the sticky/follow/override
  policy the tab's docstring documents;
* re-syncs the Source combo from backend truth every refresh, and dispatches
  a user change there to ``connect_retarget``/``disconnect_retarget``;
* renders state without scanning the scene while the tab is inactive.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from tests.common.mock_ui import attach_mocks

from mmd_tools.ui.presenters.humanik_presenter import HumanIkPresenter
from mmd_tools.ui.tabs.humanik_tab import HumanIkTab

_ACTION_BUTTON_ATTRS = (
    "create_control_rig_btn",
    "bake_btn",
    "restore_btn",
)


class _FakeSession:
    def __init__(self, state=None, error=None, external_candidates=None):
        self.state = state if state is not None else {}
        self.error = error
        self.describe_calls = []
        self.external_candidates = list(external_candidates) if external_candidates else []

    def describe_frontend_state(self, model_root=None):
        self.describe_calls.append(model_root)
        if self.error is not None:
            raise self.error
        return self.state

    def list_source_candidates(self):
        return list(self.external_candidates)


class _FakeActionsModule:
    """Stand-in for ``humanik_menu_actions`` with exactly the surface the presenter uses."""

    def __init__(self, session=None, display_model_root=None, scene_models=None):
        self.session = session or _FakeSession()
        self.display_model_root = display_model_root
        self.scene_models = list(scene_models) if scene_models is not None else []
        self.dispatch_calls = []
        self.bake_calls = []
        self.bake_control_rig_calls = []
        self.resolve_calls = 0
        self.connect_calls = []
        self.disconnect_calls = 0
        self.connect_result = "connected"
        self.disconnect_result = True
        self.connect_error = None
        self.disconnect_error = None

    def get_humanik_session(self):
        return self.session

    def dispatch_action(self, action_name):
        self.dispatch_calls.append(action_name)

    def bake_to_mmd_rig(self, start=None, end=None):
        self.bake_calls.append((start, end))

    def bake_to_control_rig(self, start=None, end=None):
        self.bake_control_rig_calls.append((start, end))

    def resolve_selected_model_root_for_display(self, *, cmds_module=None):
        self.resolve_calls += 1
        return self.display_model_root

    def list_scene_mmd_models(self, *, cmds_module=None):
        return list(self.scene_models)

    def connect_retarget(self, source_model_root, target_model_root):
        self.connect_calls.append((source_model_root, target_model_root))
        if self.connect_error is not None:
            raise self.connect_error
        return self.connect_result

    def disconnect_retarget(self):
        self.disconnect_calls += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error
        return self.disconnect_result


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
            "create_control_rig_btn",
            "bake_btn",
            "bake_execute_btn",
            "restore_btn",
            "character_combo",
            "source_combo",
        ],
    )
    view.bake_frame_range.return_value = (5, 25)
    view.bake_destination.return_value = "mmd_rig"
    view.tr.side_effect = lambda key, category=None: key
    return view


def _connected_handler(mock_signal_owner_attr):
    """Return the callback registered via ``.connect`` on a mock signal."""
    return mock_signal_owner_attr.connect.call_args[0][0]


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

    def test_scene_change_clears_combo_memory_and_refreshes_while_active(self):
        self.presenter._last_seen_selection = "|Old"
        self.presenter._character_override = "|Old"
        self.presenter._character_sticky = "|Old"
        self.presenter._character_root = "|Old"
        self.presenter._active = True

        self.presenter.on_scene_changed()

        self.assertIsNone(self.presenter._last_seen_selection)
        self.assertIsNone(self.presenter._character_override)
        self.assertIsNone(self.presenter._character_sticky)
        self.assertIsNone(self.presenter._character_root)
        self.assertEqual(len(self.session.describe_calls), 1)

    # -- (a) refresh reflects session state onto the view ----------------

    def test_refresh_passes_describe_frontend_state_result_to_view(self):
        self.session.state = {"mode": "source", "controlRigs": []}
        self.actions.display_model_root = "|Model"
        self.actions.scene_models = ["|Model"]

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

    # -- (d) remaining buttons dispatch to humanik_menu_actions, then refresh

    def test_each_action_button_dispatches_its_stable_action_name_and_refreshes(self):
        button_to_action = {
            "create_control_rig_btn": "create_control_rig",
            "restore_btn": "restore_mmd_rig",
        }
        for attr, action_name in button_to_action.items():
            with self.subTest(attr=attr):
                self.actions.dispatch_calls.clear()
                self.view.set_state.reset_mock()
                handler = _connected_handler(getattr(self.view, attr).clicked)
                handler()
                self.assertEqual(self.actions.dispatch_calls, [action_name])
                self.view.set_state.assert_called_once()

    def test_bake_button_passes_spinbox_range_without_touching_playback(self):
        handler = _connected_handler(self.view.bake_execute_btn.clicked)

        handler()

        self.assertEqual(self.cmds.edits, [])
        self.assertEqual(self.actions.bake_calls, [(5, 25)])
        self.assertEqual(self.actions.dispatch_calls, [])
        self.view.set_state.assert_called_once()

    def test_bake_button_dispatches_selected_control_rig_route(self):
        self.view.bake_destination.return_value = "control_rig"
        handler = _connected_handler(self.view.bake_execute_btn.clicked)

        handler()

        self.assertEqual(self.cmds.edits, [])
        self.assertEqual(self.actions.bake_control_rig_calls, [(5, 25)])
        self.assertEqual(self.actions.bake_calls, [])
        self.view.set_state.assert_called_once()

    def test_dispatch_exception_still_refreshes(self):
        def _raise(_name):
            raise RuntimeError("menu action failed")

        self.actions.dispatch_action = _raise
        handler = _connected_handler(self.view.restore_btn.clicked)

        handler()  # must not raise

        self.view.set_state.assert_called_once()

    def test_refresh_button_calls_refresh(self):
        handler = _connected_handler(self.view.refresh_btn.clicked)
        handler()
        self.view.set_state.assert_called_once()

class TestHumanIkPresenterCharacterCombo(unittest.TestCase):
    """Character combo selection policy: follow / sticky / override (Phase B4)."""

    def setUp(self):
        self.view = _make_mock_view()
        self.session = _FakeSession()
        self.actions = _FakeActionsModule(session=self.session)
        self.presenter = HumanIkPresenter(
            self.view,
            SimpleNamespace(),
            actions_module=self.actions,
            cmds_module=_FakeCmds(),
        )

    def _last_character_call(self):
        return self.view.set_character_options.call_args

    def test_single_scene_model_is_auto_adopted(self):
        self.actions.scene_models = ["|Only"]

        self.presenter.refresh()

        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|Only")
        self.assertEqual(self.session.describe_calls, ["|Only"])

    def test_maya_selection_wins_over_default(self):
        self.actions.scene_models = ["|A", "|B"]
        self.actions.display_model_root = "|B"

        self.presenter.refresh()

        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|B")

    def test_no_selection_sticks_to_last_resolved_model(self):
        self.actions.scene_models = ["|A", "|B"]
        self.actions.display_model_root = "|B"
        self.presenter.refresh()

        self.actions.display_model_root = None
        self.presenter.refresh()

        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|B")

    def test_manual_pick_wins_over_unchanged_selection(self):
        self.actions.scene_models = ["|A", "|B"]
        self.actions.display_model_root = "|A"
        self.presenter.refresh()

        handler = _connected_handler(self.view.character_combo.currentIndexChanged)
        self.view.character_combo.currentData.return_value = "|B"
        handler()

        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|B")
        # The Maya selection never changed away from |A during this pick.
        self.assertEqual(self.actions.display_model_root, "|A")

    def test_selection_change_clears_a_manual_override(self):
        self.actions.scene_models = ["|A", "|B"]
        self.actions.display_model_root = None
        self.presenter.refresh()

        handler = _connected_handler(self.view.character_combo.currentIndexChanged)
        self.view.character_combo.currentData.return_value = "|B"
        handler()
        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|B")

        # A genuinely new Maya selection now appears: it must win outright,
        # discarding the earlier manual override.
        self.actions.display_model_root = "|A"
        self.presenter.refresh()

        _options, selected = self._last_character_call().args
        self.assertEqual(selected, "|A")

    def test_character_combo_change_ignores_falsy_selection(self):
        self.actions.scene_models = ["|A"]
        self.presenter.refresh()
        calls_before = self.view.set_character_options.call_count

        handler = _connected_handler(self.view.character_combo.currentIndexChanged)
        self.view.character_combo.currentData.return_value = None
        handler()

        # No refresh should have been triggered by a falsy combo value.
        self.assertEqual(self.view.set_character_options.call_count, calls_before)


class TestHumanIkPresenterSourceCombo(unittest.TestCase):
    """Source combo: backend-truth sync + connect/disconnect dispatch (Phase B4)."""

    def setUp(self):
        self.view = _make_mock_view()
        self.session = _FakeSession()
        self.actions = _FakeActionsModule(session=self.session, scene_models=["|Char", "|Other"])
        self.presenter = HumanIkPresenter(
            self.view,
            SimpleNamespace(),
            actions_module=self.actions,
            cmds_module=_FakeCmds(),
        )

    def _last_source_call(self):
        return self.view.set_source_options.call_args

    def test_no_source_bound_shows_none(self):
        self.session.state = {"source": None}

        self.presenter.refresh()

        _options, selected = self._last_source_call().args
        self.assertIsNone(selected)

    def test_source_combo_excludes_the_character_model(self):
        self.session.state = {"source": None}

        self.presenter.refresh()

        options, _selected = self._last_source_call().args
        values = [value for _label, value in options]
        self.assertNotIn(self.presenter._character_root, values)
        self.assertIn(None, values)

    def test_source_combo_reflects_backend_truth_after_refresh(self):
        self.session.state = {"source": {"modelRoot": "|Other", "character": "OtherChar"}}

        self.presenter.refresh()

        _options, selected = self._last_source_call().args
        self.assertEqual(selected, ("mmd", "|Other"))

    def test_source_combo_mmd_item_data_is_a_kind_value_pair(self):
        self.session.state = {"source": None}

        self.presenter.refresh()

        options, _selected = self._last_source_call().args
        values = [value for _label, value in options]
        self.assertIn(("mmd", "|Other"), values)

    def test_picking_a_model_dispatches_connect_retarget(self):
        self.presenter.refresh()
        character_root = self.presenter._character_root

        handler = _connected_handler(self.view.source_combo.currentIndexChanged)
        self.view.source_combo.currentData.return_value = "|Other"
        handler()

        self.assertEqual(self.actions.connect_calls, [("|Other", character_root)])
        self.assertEqual(self.view.set_state.call_count, 2)  # initial refresh + post-pick refresh

    # -- HUMANIK-EXTERNAL-SOURCE-1 ES-3: external (non-MMD) HIK characters --

    def test_external_hik_characters_are_listed_and_labeled(self):
        self.session.external_candidates = [
            {"character": "MocapChar", "isMmd": False, "modelRoot": None, "locked": True},
            {"character": "|Other_hidden_mmd", "isMmd": True, "modelRoot": "|Other", "locked": True},
        ]
        self.session.state = {"source": None}

        self.presenter.refresh()

        options, _selected = self._last_source_call().args
        labels_and_values = {label: value for label, value in options}
        self.assertIn("MocapChar (HIK)", labels_and_values)
        self.assertEqual(labels_and_values["MocapChar (HIK)"], ("external", "MocapChar"))
        # The isMmd=True row must not be duplicated as an "(HIK)" entry --
        # it is already represented by the plain MMD model options above.
        self.assertNotIn("|Other_hidden_mmd (HIK)", labels_and_values)

    def test_external_source_binding_selects_the_external_combo_item(self):
        self.session.external_candidates = [
            {"character": "MocapChar", "isMmd": False, "modelRoot": None, "locked": True},
        ]
        self.session.state = {
            "source": {"modelRoot": None, "character": "MocapChar", "external": True}
        }

        self.presenter.refresh()

        _options, selected = self._last_source_call().args
        self.assertEqual(selected, ("external", "MocapChar"))

    def test_external_candidate_listing_failure_falls_back_to_no_external_items(self):
        def _raise():
            raise RuntimeError("boom")

        self.session.list_source_candidates = _raise
        self.session.state = {"source": None}

        self.presenter.refresh()  # must not raise

        options, _selected = self._last_source_call().args
        self.assertTrue(all(not str(label).endswith("(HIK)") for label, _value in options))

    def test_picking_an_external_character_dispatches_connect_retarget(self):
        self.session.external_candidates = [
            {"character": "MocapChar", "isMmd": False, "modelRoot": None, "locked": True},
        ]
        self.session.state = {"source": None}
        self.presenter.refresh()
        character_root = self.presenter._character_root

        handler = _connected_handler(self.view.source_combo.currentIndexChanged)
        self.view.source_combo.currentData.return_value = ("external", "MocapChar")
        handler()

        self.assertEqual(
            self.actions.connect_calls, [(("external", "MocapChar"), character_root)]
        )

    def test_picking_none_dispatches_disconnect_retarget(self):
        self.presenter.refresh()

        handler = _connected_handler(self.view.source_combo.currentIndexChanged)
        self.view.source_combo.currentData.return_value = None
        handler()

        self.assertEqual(self.actions.disconnect_calls, 1)
        self.assertEqual(self.actions.connect_calls, [])

    def test_connect_failure_still_refreshes_and_does_not_raise(self):
        self.presenter.refresh()
        self.actions.connect_error = RuntimeError("connect failed")

        handler = _connected_handler(self.view.source_combo.currentIndexChanged)
        self.view.source_combo.currentData.return_value = "|Other"
        handler()  # must not raise

        self.assertEqual(self.actions.connect_calls, [("|Other", self.presenter._character_root)])
        self.assertGreaterEqual(self.view.set_state.call_count, 2)


class TestHumanIkTabSetState(unittest.TestCase):
    """The compact view renders status but never duplicates backend guards."""

    def _make_fake_tab(self):
        fake = SimpleNamespace()
        fake.tr = lambda key, category=None: key
        fake.status_label = Mock()
        fake._action_buttons = {attr: Mock() for attr in _ACTION_BUTTON_ATTRS}
        fake._last_mode = "neutral"
        fake._last_control_rig_count = 0
        fake._mode_text = HumanIkTab._mode_text.__get__(fake)
        fake._status_text = HumanIkTab._status_text.__get__(fake)
        return fake

    def test_status_keeps_only_mode_and_control_rig_count(self):
        fake = self._make_fake_tab()
        state = {
            "mode": "target_preview",
            "controlRigs": [{"modelRoot": "|Target"}],
        }

        HumanIkTab.set_state(fake, state)

        fake.status_label.setText.assert_called_once_with(
            "humanik_mode_target_previewhumanik_status_control_rig_suffix"
        )

    def test_backend_blocked_actions_stay_clickable_without_inline_reason(self):
        fake = self._make_fake_tab()
        state = {
            "mode": "neutral",
            "actions": {
                "create_control_rig": {
                    "allowed": False,
                    "reasonCode": "not_characterized",
                    "reasonText": "details",
                },
            },
        }

        HumanIkTab.set_state(fake, state)

        for button in fake._action_buttons.values():
            button.setEnabled.assert_called_once_with(True)
            button.setToolTip.assert_not_called()

if __name__ == "__main__":
    unittest.main()
