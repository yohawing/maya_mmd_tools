"""Unit tests for the HumanIK Maya menu action boundary."""

import unittest
from unittest.mock import MagicMock, patch

from mmd_tools.core.humanik_bake import HumanIkBakeResult
from mmd_tools.ui import humanik_menu_actions as actions


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.active_preview = None

    def inspect_model(self, root):
        self.calls.append(("inspect_model", root))
        return {
            "modelRoot": root,
            "assignmentCount": 25,
            "excludedFingerCount": 30,
            "missingMmdBones": [],
            "ambiguous": [],
        }

    def inspect_target_ownership(self, root):
        self.calls.append(("inspect_target_ownership", root))
        return {
            "modelRoot": root,
            "assignmentCount": 25,
            "excludedFingerCount": 30,
            "missingMmdBones": [],
            "ambiguous": [],
            "constraintCounts": {"mute_for_hik": 2, "keep_post": 1},
            "constraintRows": [],
            "blockers": [],
        }

    def setup_and_characterize(self, root, **kwargs):
        self.calls.append(("setup_and_characterize", root, kwargs))
        return {"modelRoot": root}

    def enter_source_mode(self, root):
        self.calls.append(("enter_source_mode", root))
        return root

    def enter_target_mode(self, root):
        self.calls.append(("enter_target_mode", root))
        return root

    def create_control_rig(self, root):
        self.calls.append(("create_control_rig", root))
        return True

    def bake_to_mmd_rig(self, start, end):
        self.calls.append(("bake_to_mmd_rig", start, end))
        return HumanIkBakeResult(start, end, 4, {}, 0.0, [])

    def restore_mmd_rig(self):
        self.calls.append(("restore_mmd_rig",))
        return True

    def diagnostics(self, root=None):
        self.calls.append(("diagnostics", root))
        return {"modelRoot": root, "preview": {"active": False}}


class _FakeModelService:
    models = ["|model_root"]
    selected_parent = {}

    def __init__(self, cmds_module=None):
        self.cmds = cmds_module

    def get_parent_mmd_root(self, node):
        return self.selected_parent.get(node)

    def object_exists(self, node):
        return bool(node)

    def list_mmd_models(self):
        return list(self.models)


class TestHumanIkMenuActions(unittest.TestCase):
    def setUp(self):
        self.session = _FakeSession()
        self.cmds = MagicMock()
        self.cmds.ls.return_value = []
        self.cmds.menu.return_value = False
        self.cmds.window.return_value = False
        self.cmds.menuItem.side_effect = lambda *args, **kwargs: (
            False if kwargs.get("exists") else args[0] if args else "menuItem"
        )
        actions.configure_humanik_actions(
            session=self.session,
            cmds_module=self.cmds,
            confirm_dialog=lambda **kwargs: "Continue",
            error_reporter=self._errors.append if hasattr(self, "_errors") else None,
        )
        self._errors = []
        actions._error_reporter = self._errors.append

    def tearDown(self):
        actions._session = None
        actions._cmds_module = None
        actions._mel_module = None
        actions._confirm_dialog = None
        actions._error_reporter = None

    def test_menu_contains_humanik_and_exact_seven_actions(self):
        submenu = actions.install_humanik_menu(parent="MMD", cmds_module=self.cmds)

        self.assertEqual(submenu, "MMDHumanIKMenu")
        labels = [
            call.kwargs.get("label")
            for call in self.cmds.menuItem.call_args_list
            if call.kwargs.get("parent") == submenu
        ]
        self.assertEqual(labels, [label for _action, label in actions.ACTION_LABELS])

    def test_menu_reinstall_removes_previous_submenu_before_recreating(self):
        existing = False

        def menu_item(*args, **kwargs):
            nonlocal existing
            if kwargs.get("exists"):
                return existing
            if kwargs.get("subMenu"):
                existing = True
            return args[0] if args else "menuItem"

        self.cmds.menuItem.side_effect = menu_item
        actions.install_humanik_menu(parent="MMD", cmds_module=self.cmds)
        actions.install_humanik_menu(parent="MMD", cmds_module=self.cmds)

        self.cmds.deleteUI.assert_called_once_with(actions.HUMANIK_MENU_NAME)

    def test_action_callback_dispatch_can_be_injected(self):
        calls = []
        actions.install_humanik_menu(
            parent="MMD",
            cmds_module=self.cmds,
            callback_dispatcher=lambda action: calls.append(action),
        )

        callbacks = [
            call.kwargs["command"]
            for call in self.cmds.menuItem.call_args_list
            if call.kwargs.get("parent") == "MMDHumanIKMenu"
        ]
        callbacks[0]("ignored")
        self.assertEqual(calls, ["setup_and_characterize"])

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_uses_selected_root_only(self):
        _FakeModelService.selected_parent = {"|selected|joint": "|selected_root"}
        self.cmds.ls.return_value = ["|selected|joint"]
        self.assertEqual(actions.resolve_model_root(cmds_module=self.cmds), "|selected_root")

        self.cmds.ls.return_value = []
        with self.assertRaisesRegex(ValueError, "Select an MMD model root"):
            actions.resolve_model_root(cmds_module=self.cmds)

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_rejects_ambiguous_selection_and_unresolved_selection(self):
        _FakeModelService.selected_parent = {
            "|a|joint": "|a_root",
            "|b|joint": "|b_root",
        }
        self.cmds.ls.return_value = ["|a|joint", "|b|joint"]
        with self.assertRaisesRegex(ValueError, "Multiple MMD model roots"):
            actions.resolve_model_root(cmds_module=self.cmds)

        self.cmds.ls.return_value = []
        with self.assertRaisesRegex(ValueError, "Select an MMD model root"):
            actions.resolve_model_root(cmds_module=self.cmds)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_cancel_does_not_mutate_and_continue_confirms_stance(self, resolve):
        actions._confirm_dialog = lambda **kwargs: "Cancel"
        self.assertIsNone(actions.setup_and_characterize())
        self.assertNotIn(("setup_and_characterize", "|model_root", {"stance_confirmed": True}), self.session.calls)

        actions._confirm_dialog = lambda **kwargs: "Continue"
        actions.setup_and_characterize()
        self.assertNotIn(("inspect_model", "|model_root"), self.session.calls)
        self.assertIn(
            ("setup_and_characterize", "|model_root", {"stance_confirmed": True}),
            self.session.calls,
        )

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_target_blocker_reports_error_without_starting_preview(self, resolve):
        self.session.inspect_target_ownership = lambda root: {
            "modelRoot": root,
            "constraintCounts": {"physics_blocker": 1},
            "blockers": [{"node": "physics", "classification": "physics_blocker"}],
        }

        self.assertIsNone(actions.enter_target_mode())
        self.assertTrue(any("blocked" in message for message in self._errors))
        self.assertNotIn(("enter_target_mode", "|model_root"), self.session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_target_cancel_does_not_start_preview(self, resolve):
        actions._confirm_dialog = lambda **kwargs: "Cancel"

        self.assertIsNone(actions.enter_target_mode())
        self.assertIn(("inspect_target_ownership", "|model_root"), self.session.calls)
        self.assertNotIn(("enter_target_mode", "|model_root"), self.session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_bake_uses_integer_playback_range(self, resolve):
        self.cmds.playbackOptions.side_effect = [1.9, 10.1]
        result = actions.bake_to_mmd_rig()

        self.assertEqual(result.start, 2)
        self.assertEqual(result.end, 10)
        self.assertIn(("bake_to_mmd_rig", 2, 10), self.session.calls)

    def test_bake_rejects_empty_integer_playback_range_before_confirmation(self):
        self.cmds.playbackOptions.side_effect = [5.2, 4.8]

        self.assertIsNone(actions.bake_to_mmd_rig())
        self.assertTrue(any("Playback range is empty" in message for message in self._errors))
        self.assertNotIn(("bake_to_mmd_rig", 6, 4), self.session.calls)

    def test_restore_does_not_require_model_resolution(self):
        self.cmds.ls.side_effect = RuntimeError("selection unavailable")
        self.assertTrue(actions.restore_mmd_rig())
        self.assertIn(("restore_mmd_rig",), self.session.calls)

    def test_reset_keeps_session_when_restore_fails(self):
        class FailingSession(_FakeSession):
            def restore_mmd_rig(self):
                raise RuntimeError("restore failed")

        failing = FailingSession()
        actions.set_humanik_session(failing)

        self.assertFalse(actions.reset_humanik_session())
        self.assertIs(actions.get_humanik_session(), failing)

    def test_reset_success_closes_diagnostics_window(self):
        self.cmds.window.return_value = True

        self.assertTrue(actions.reset_humanik_session())
        self.cmds.deleteUI.assert_called_once_with(actions.DIAGNOSTICS_WINDOW_NAME)
        self.assertIsNone(actions._session)

    def test_reset_without_session_is_success(self):
        actions._session = None

        self.assertTrue(actions.reset_humanik_session())

    def test_selection_free_model_actions_report_error_without_session_mutation(self):
        for function in (
            actions.setup_and_characterize,
            actions.enter_source_mode,
            actions.enter_target_mode,
            actions.create_control_rig,
        ):
            function()

        self.assertEqual(self.session.calls, [])
        self.assertEqual(len(self._errors), 4)

    @patch.object(actions, "resolve_model_root", side_effect=ValueError("no model"))
    def test_diagnostics_shows_session_report_without_selected_model(self, resolve):
        result = actions.diagnostics()

        self.assertIsNone(result["modelRoot"])
        self.assertEqual(self.cmds.window.call_count, 2)
        self.cmds.scrollField.assert_called_once()
        self.cmds.showWindow.assert_called_once_with(actions.DIAGNOSTICS_WINDOW_NAME)

    @patch.object(actions, "resolve_model_root", side_effect=ValueError("no model"))
    def test_diagnostics_reopen_deletes_existing_window(self, resolve):
        self.cmds.window.return_value = True

        actions.diagnostics()

        self.cmds.deleteUI.assert_called_once_with(actions.DIAGNOSTICS_WINDOW_NAME)


if __name__ == "__main__":
    unittest.main()
