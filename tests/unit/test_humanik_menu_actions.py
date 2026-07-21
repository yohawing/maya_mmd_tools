"""Unit tests for the HumanIK Maya menu action boundary."""

import unittest
from unittest.mock import MagicMock, patch

from mmd_tools.core.humanik_bake import HumanIkBakeResult
from mmd_tools.core.humanik_frontend import REASON_NOT_CHARACTERIZED
from mmd_tools.ui import humanik_menu_actions as actions


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.active_preview = None

    def inspect_model(self, root, **kwargs):
        self.calls.append(("inspect_model", root, kwargs) if kwargs else ("inspect_model", root))
        full = kwargs.get("profile") == "full" or kwargs.get("include_fingers") is True
        return {
            "modelRoot": root,
            "assignmentCount": 55 if full else 25,
            "bodyAssignments": [{}] * 25,
            "assignments": [{}] * (55 if full else 25),
            "excludedFingerCount": 0 if full else 30,
            "missingMmdBones": [],
            "ambiguous": [],
        }

    def inspect_target_ownership(self, root, **kwargs):
        self.calls.append(
            ("inspect_target_ownership", root, kwargs)
            if kwargs
            else ("inspect_target_ownership", root)
        )
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

    def describe_frontend_state(self, model_root=None):
        self.calls.append(("describe_frontend_state", model_root))
        return {
            "actions": {
                "enter_source_mode": {"reasonCode": None},
                "enter_target_mode": {"reasonCode": None},
                "create_control_rig": {"reasonCode": None},
            }
        }


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
        with patch.object(actions, "install_maya_script_editor_handler") as install_handler:
            submenu = actions.install_humanik_menu(parent="MMD", cmds_module=self.cmds)

        install_handler.assert_called_once_with()

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
        _FakeModelService.models = ["|selected_root"]
        _FakeModelService.selected_parent = {"|selected|joint": "|selected_root"}
        self.cmds.ls.return_value = ["|selected|joint"]
        self.assertEqual(actions.resolve_model_root(cmds_module=self.cmds), "|selected_root")

        _FakeModelService.models = []
        self.cmds.ls.return_value = []
        with self.assertRaisesRegex(ValueError, "Select an MMD model root"):
            actions.resolve_model_root(cmds_module=self.cmds)

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_rejects_ambiguous_selection_and_unresolved_selection(self):
        _FakeModelService.models = ["|a_root", "|b_root"]
        _FakeModelService.selected_parent = {
            "|a|joint": "|a_root",
            "|b|joint": "|b_root",
        }
        self.cmds.ls.return_value = ["|a|joint", "|b|joint"]
        with self.assertRaisesRegex(ValueError, "Multiple MMD model roots"):
            actions.resolve_model_root(cmds_module=self.cmds)

        _FakeModelService.models = []
        self.cmds.ls.return_value = []
        with self.assertRaisesRegex(ValueError, "Select an MMD model root"):
            actions.resolve_model_root(cmds_module=self.cmds)

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_auto_adopts_the_single_scene_model_without_a_dialog(self):
        _FakeModelService.models = ["|model_root"]
        _FakeModelService.selected_parent = {}
        self.cmds.ls.return_value = []
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown for a single scene model")
        )

        self.assertEqual(actions.resolve_model_root(cmds_module=self.cmds), "|model_root")

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_shows_picker_dialog_for_multiple_scene_models(self):
        _FakeModelService.models = ["|model_a", "|model_b"]
        _FakeModelService.selected_parent = {}
        self.cmds.ls.return_value = []
        dialog = {}

        def choose(**kwargs):
            dialog.update(kwargs)
            return "model_b"

        actions._confirm_dialog = choose

        self.assertEqual(actions.resolve_model_root(cmds_module=self.cmds), "|model_b")
        self.assertEqual(dialog["button"], ["model_a", "model_b", "Cancel"])

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_model_resolution_picker_cancel_returns_none_without_error(self):
        _FakeModelService.models = ["|model_a", "|model_b"]
        _FakeModelService.selected_parent = {}
        self.cmds.ls.return_value = []
        actions._confirm_dialog = lambda **kwargs: "Cancel"

        with patch.object(actions, "_display_info") as display_info:
            self.assertIsNone(actions.resolve_model_root(cmds_module=self.cmds))

        display_info.assert_called_once()
        self.assertIn("cancelled", display_info.call_args.args[0])

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_enter_source_mode_auto_characterizes_when_not_characterized(self, resolve):
        class UncharacterizedSession(_FakeSession):
            def describe_frontend_state(self, model_root=None):
                self.calls.append(("describe_frontend_state", model_root))
                return {"actions": {"enter_source_mode": {"reasonCode": REASON_NOT_CHARACTERIZED}}}

        session = UncharacterizedSession()
        actions.set_humanik_session(session)

        with patch.object(actions, "_display_info") as display_info:
            result = actions.enter_source_mode()

        self.assertEqual(result, "|model_root")
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
            ),
            session.calls,
        )
        self.assertIn(("enter_source_mode", "|model_root"), session.calls)
        display_info.assert_called_once()
        self.assertIn("auto-characterizing", display_info.call_args.args[0])

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_enter_source_mode_skips_auto_characterize_when_already_characterized(self, resolve):
        with patch.object(actions, "_display_info") as display_info:
            actions.enter_source_mode()

        self.assertNotIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
            ),
            self.session.calls,
        )
        display_info.assert_not_called()

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_enter_target_mode_auto_characterizes_when_not_characterized(self, resolve):
        class UncharacterizedSession(_FakeSession):
            def describe_frontend_state(self, model_root=None):
                return {"actions": {"enter_target_mode": {"reasonCode": REASON_NOT_CHARACTERIZED}}}

        session = UncharacterizedSession()
        actions.set_humanik_session(session)

        result = actions.enter_target_mode()

        self.assertEqual(result, "|model_root")
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
            ),
            session.calls,
        )
        self.assertIn(("enter_target_mode", "|model_root"), session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_create_control_rig_auto_characterizes_when_not_characterized(self, resolve):
        class UncharacterizedSession(_FakeSession):
            def describe_frontend_state(self, model_root=None):
                return {"actions": {"create_control_rig": {"reasonCode": REASON_NOT_CHARACTERIZED}}}

        session = UncharacterizedSession()
        actions.set_humanik_session(session)

        result = actions.create_control_rig()

        self.assertTrue(result)
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
            ),
            session.calls,
        )
        self.assertIn(("create_control_rig", "|model_root"), session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_cancel_does_not_mutate_and_continue_runs_automatic_stance(self, resolve):
        actions._confirm_dialog = lambda **kwargs: "Cancel"
        self.assertIsNone(actions.setup_and_characterize())
        self.assertNotIn(("setup_and_characterize", "|model_root", {}), self.session.calls)

        actions._confirm_dialog = lambda **kwargs: "Continue"
        result = actions.setup_and_characterize()
        self.assertTrue(result["success"])
        self.assertNotIn(("inspect_model", "|model_root"), self.session.calls)
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
            ),
            self.session.calls,
        )

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_profile_buttons_can_opt_into_full_assignments(self, resolve):
        dialog = {}

        def choose(**kwargs):
            dialog.update(kwargs)
            return "Body + fingers"

        actions._confirm_dialog = choose

        result = actions.setup_and_characterize()

        self.assertTrue(result["success"])
        self.assertEqual(result["profile"], "full")
        self.assertEqual(dialog["button"], ["Body only", "Body + fingers", "Cancel"])
        self.assertIn(
            (
                "inspect_target_ownership",
                "|model_root",
                {"profile": "full", "include_fingers": True},
            ),
            self.session.calls,
        )
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "full", "include_fingers": True},
            ),
            self.session.calls,
        )

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_warns_but_succeeds_for_usable_stance_residual(self, resolve):
        class WarningSession(_FakeSession):
            def setup_and_characterize(self, root, **kwargs):
                super().setup_and_characterize(root, **kwargs)
                return {
                    "modelRoot": root,
                    "stance": {
                        "pose": {
                            "passed": True,
                            "strictPassed": False,
                            "warning": True,
                            "warningRows": ["LeftArm"],
                        }
                    },
                }

        actions.set_humanik_session(WarningSession())
        with patch.object(actions, "_display_warning") as display_warning:
            result = actions.setup_and_characterize()

        self.assertTrue(result["success"])
        display_warning.assert_called_once()
        self.assertIn("LeftArm", display_warning.call_args.args[0])
        self.assertIn("Characterization continued", display_warning.call_args.args[0])

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_confirmation_summarizes_edges_and_blockers(self, resolve):
        long_node = "|" + "very_long_constraint_node_" * 30
        long_edge = long_node + ".outputRotate" + "X" * 100
        report = {
            "assignmentCount": 25,
            "excludedFingerCount": 30,
            "missingMmdBones": [],
            "ambiguous": [],
            "automaticStance": {
                "directionStrategy": "current-world-direction-horizontal-projection",
                "targets": {"LeftArm": {"joint": long_node, "child": long_edge}},
                "missingSlots": [],
            },
            "constraintCounts": {"mute_for_hik": 12, "keep_post": 9},
            "constraintRows": [
                {"node": long_node, "classification": "mute_for_hik", "writes": [long_edge]},
            ],
            "blockers": [
                {"node": long_node, "classification": "physics_blocker"},
                {"node": long_node, "classification": "physics_blocker"},
            ],
        }

        message = actions._setup_confirmation_message("|" + "deep_model_path_" * 30, report, report)

        self.assertIn("Set up HumanIK for", message)
        self.assertIn("Body only: 25 bones (default)", message)
        self.assertIn("Body + fingers: 55 bones (30 finger bones)", message)
        self.assertIn("Issues: unresolved 0, ambiguous 0, blockers 2", message)
        self.assertNotIn(long_node, message)
        self.assertNotIn("nodes/edges", message)
        self.assertNotIn("directionStrategy", message)
        self.assertNotIn("reference residual", message)
        self.assertLessEqual(len(message), 320)

        target_message = actions._target_confirmation_message("|model_root", report)
        self.assertIn("blocker summary: physics_blocker (2)", target_message)
        self.assertNotIn(long_node, target_message)
        self.assertNotIn("nodes/edges", target_message)

    def test_setup_confirmation_is_four_plain_lines_when_preflight_is_clean(self):
        report = {
            "assignmentCount": 25,
            "excludedFingerCount": 30,
            "missingMmdBones": [],
            "ambiguous": [],
            "constraintCounts": {"mute_for_hik": 4, "keep_post": 12},
            "constraintRows": [{"node": "technicalNode", "writes": ["long.edge"]}],
            "blockers": [],
        }

        message = actions._setup_confirmation_message("|Base:Base_root", report, report)

        self.assertEqual(len(message.splitlines()), 4)
        self.assertIn("Set up HumanIK for Base:Base_root?", message)
        self.assertNotIn("mute_for_hik", message)
        self.assertNotIn("journal", message)
        self.assertNotIn("residual", message)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_failure_logs_traceback_and_returns_short_failure(self, resolve):
        class FailingSession(_FakeSession):
            def setup_and_characterize(self, root, **kwargs):
                raise RuntimeError("failure detail " + ("x" * 500))

        actions.set_humanik_session(FailingSession())
        with patch.object(actions.logger, "error") as log_error:
            result = actions.setup_and_characterize()

        self.assertFalse(result["success"])
        self.assertLessEqual(len(result["error"]), 180)
        self.assertTrue(any("See the Maya Script Editor" in message for message in self._errors))
        self.assertLessEqual(len(self._errors[-1]), 180)
        log_error.assert_called_once()
        self.assertTrue(log_error.call_args.kwargs["exc_info"])

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
    def test_target_profile_mismatch_is_rejected_before_confirmation(self, resolve):
        self.session.diagnostics = lambda root=None: {
            "source": {"profile": "body-only"},
            "preview": {"active": False},
        }
        self.session.inspect_target_ownership = lambda root: {
            "profile": "full",
            "constraintCounts": {},
            "blockers": [],
        }
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("confirmation should not be shown")
        )

        self.assertIsNone(actions.enter_target_mode())
        self.assertTrue(any("profile mismatch" in message for message in self._errors))
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

    def test_bake_confirmation_uses_active_profile_without_quality_residual(self):
        self.cmds.playbackOptions.side_effect = [1.9, 10.1]
        self.session.diagnostics = lambda root=None: {"profile": "full"}
        dialog = {}

        def choose(**kwargs):
            dialog.update(kwargs)
            return "Continue"

        actions._confirm_dialog = choose
        actions.bake_to_mmd_rig()

        self.assertIn("Profile: full", dialog["message"])
        self.assertIn("included experimentally", dialog["message"])
        self.assertNotIn("residual", dialog["message"])

    def test_bake_rejects_empty_integer_playback_range_before_confirmation(self):
        self.cmds.playbackOptions.side_effect = [5.2, 4.8]

        with patch.object(actions.logger, "error") as log_error:
            self.assertIsNone(actions.bake_to_mmd_rig())

        self.assertTrue(any("Playback range is empty" in message for message in self._errors))
        self.assertTrue(any("Maya Script Editor" in message for message in self._errors))
        self.assertTrue(log_error.call_args.kwargs["exc_info"])
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
