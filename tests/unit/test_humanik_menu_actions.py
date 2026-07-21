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

    def enter_external_source_mode(self, character):
        self.calls.append(("enter_external_source_mode", character))
        return {"character": character, "external": True, "locked": True}

    def list_source_candidates(self):
        self.calls.append(("list_source_candidates",))
        return []

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
        self.cmds.listConnections.return_value = []
        self.cmds.objExists.return_value = True
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

    def test_action_labels_lead_with_open_humanik_editor_plus_seven_staged_actions(self):
        self.assertEqual(actions.ACTION_LABELS[0], ("open_humanik_editor", "HumanIK Editor..."))
        self.assertEqual(len(actions.ACTION_LABELS), 8)

    def test_open_humanik_editor_dispatches_to_the_standalone_window(self):
        with patch("mmd_tools.ui.humanik_window.show_humanik_window") as show_window:
            actions.open_humanik_editor()

        show_window.assert_called_once_with(dockable=True)

    def test_dispatch_action_routes_open_humanik_editor(self):
        with patch("mmd_tools.ui.humanik_window.show_humanik_window") as show_window:
            actions.dispatch_action("open_humanik_editor")

        show_window.assert_called_once_with(dockable=True)

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

    def test_menu_submenu_label_marks_humanik_as_experimental(self):
        actions.install_humanik_menu(parent="MMD", cmds_module=self.cmds)

        submenu_calls = [
            call
            for call in self.cmds.menuItem.call_args_list
            if call.kwargs.get("subMenu")
        ]
        self.assertEqual(len(submenu_calls), 1)
        self.assertEqual(submenu_calls[0].kwargs.get("label"), "HumanIK (Experimental)")
        # The menu id/name stays stable for backward compatibility even though
        # the displayed label now flags the feature as experimental.
        self.assertEqual(submenu_calls[0].args[0], actions.HUMANIK_MENU_NAME)

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
        self.assertEqual(calls, ["open_humanik_editor"])

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
                {"profile": "full", "include_fingers": True},
            ),
            session.calls,
        )
        self.assertIn(("enter_source_mode", "|model_root"), session.calls)
        display_info.assert_called_once()
        self.assertIn("auto-characterizing", display_info.call_args.args[0])
        self.assertIn("Full", display_info.call_args.args[0])

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_enter_source_mode_skips_auto_characterize_when_already_characterized(self, resolve):
        with patch.object(actions, "_display_info") as display_info:
            actions.enter_source_mode()

        self.assertNotIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "full", "include_fingers": True},
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
                {"profile": "full", "include_fingers": True},
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
                {"profile": "full", "include_fingers": True},
            ),
            session.calls,
        )
        self.assertIn(("create_control_rig", "|model_root"), session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_runs_immediately_with_the_full_default_profile_no_dialog(self, resolve):
        # HUMANIK-FRONTEND-1 Phase B6: the "Body only / Body + fingers /
        # Cancel" picker dialog is gone; Setup / Characterize always runs
        # immediately with the full (body + fingers) default profile.
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown for Setup / Characterize")
        )

        result = actions.setup_and_characterize()

        self.assertTrue(result["success"])
        self.assertEqual(result["profile"], "full")
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "full", "include_fingers": True},
            ),
            self.session.calls,
        )
        self.assertIn(
            (
                "inspect_target_ownership",
                "|model_root",
                {"profile": "full", "include_fingers": True},
            ),
            self.session.calls,
        )

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_setup_preserves_an_existing_body_only_binding_instead_of_recharacterizing(self, resolve):
        # HUMANIK-FRONTEND-1 Phase B6 compatibility: a model already
        # characterized body-only (by an older session, or before this
        # change) must not be silently forced to full.
        self.session.diagnostics = lambda root=None: {
            "modelRoot": root,
            "character": "MMDFrontend_model_root",
            "profile": "body-only",
            "preview": {"active": False},
        }
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown for Setup / Characterize")
        )

        result = actions.setup_and_characterize()

        self.assertTrue(result["success"])
        self.assertEqual(result["profile"], "body-only")
        self.assertIn(
            (
                "setup_and_characterize",
                "|model_root",
                {"profile": "body-only", "include_fingers": False},
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

        self.assertEqual(len(message.splitlines()), 5)
        self.assertIn("experimental", message.splitlines()[0].lower())
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
    def test_target_mode_starts_immediately_without_a_confirmation_dialog(self, resolve):
        # HUMANIK-FRONTEND-1 Phase B6: the "Continue/Cancel" confirmation is
        # gone; an unblocked, profile-matched Enter Target Mode call starts
        # the preview immediately.
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown for Enter Target Mode")
        )

        self.assertEqual(actions.enter_target_mode(), "|model_root")
        self.assertIn(("inspect_target_ownership", "|model_root"), self.session.calls)
        self.assertIn(("enter_target_mode", "|model_root"), self.session.calls)

    @patch.object(actions, "resolve_model_root", return_value="|model_root")
    def test_bake_uses_integer_playback_range(self, resolve):
        self.cmds.playbackOptions.side_effect = [1.9, 10.1]
        result = actions.bake_to_mmd_rig()

        self.assertEqual(result.start, 2)
        self.assertEqual(result.end, 10)
        self.assertIn(("bake_to_mmd_rig", 2, 10), self.session.calls)

    def test_bake_runs_immediately_without_a_confirmation_dialog(self):
        # HUMANIK-FRONTEND-1 Phase B6: Bake to MMD Rig has no configurable
        # options left to confirm, so it bakes immediately.
        self.cmds.playbackOptions.side_effect = [1.9, 10.1]
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown for Bake to MMD Rig")
        )

        result = actions.bake_to_mmd_rig()

        self.assertEqual(result.start, 2)
        self.assertEqual(result.end, 10)
        self.assertIn(("bake_to_mmd_rig", 2, 10), self.session.calls)

    def test_bake_rejects_empty_integer_playback_range_before_confirmation(self):
        self.cmds.playbackOptions.side_effect = [5.2, 4.8]

        with patch.object(actions.logger, "error") as log_error:
            self.assertIsNone(actions.bake_to_mmd_rig())

        self.assertTrue(any("Bake frame range is empty" in message for message in self._errors))
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
        window_call = next(
            call for call in self.cmds.window.call_args_list if "title" in call.kwargs
        )
        self.assertIn("Experimental", window_call.kwargs["title"])

    @patch.object(actions, "resolve_model_root", side_effect=ValueError("no model"))
    def test_diagnostics_reopen_deletes_existing_window(self, resolve):
        self.cmds.window.return_value = True

        actions.diagnostics()

        self.cmds.deleteUI.assert_called_once_with(actions.DIAGNOSTICS_WINDOW_NAME)

    # -- HUMANIK-FRONTEND-1 Phase B4: explicit model_root arguments ---------

    def test_model_root_argument_skips_selection_resolution(self):
        with patch.object(actions, "resolve_model_root") as resolve:
            resolve.side_effect = AssertionError("resolve_model_root must not be called")
            self.assertTrue(actions.create_control_rig(model_root="|explicit_root"))
            actions.enter_source_mode(model_root="|explicit_root")
            actions.enter_target_mode(model_root="|explicit_root")
            actions.setup_and_characterize(model_root="|explicit_root")
        resolve.assert_not_called()
        self.assertIn(("create_control_rig", "|explicit_root"), self.session.calls)
        self.assertIn(("enter_source_mode", "|explicit_root"), self.session.calls)
        self.assertIn(("enter_target_mode", "|explicit_root"), self.session.calls)
        self.assertIn(
            (
                "setup_and_characterize",
                "|explicit_root",
                {"profile": "full", "include_fingers": True},
            ),
            self.session.calls,
        )

    def test_model_root_none_falls_back_to_selection_resolution_as_before(self):
        with patch.object(actions, "resolve_model_root", return_value="|selected_root") as resolve:
            actions.create_control_rig()
        resolve.assert_called_once()
        self.assertIn(("create_control_rig", "|selected_root"), self.session.calls)

    # -- HUMANIK-FRONTEND-1 Phase B4: list_scene_mmd_models ------------------

    @patch.object(actions, "SceneModelService", _FakeModelService)
    def test_list_scene_mmd_models_returns_sorted_scene_roots(self):
        _FakeModelService.models = ["|b_root", "|a_root"]
        self.assertEqual(
            actions.list_scene_mmd_models(cmds_module=self.cmds), ["|a_root", "|b_root"]
        )

    def test_list_scene_mmd_models_fails_soft_to_empty_list(self):
        cmds = MagicMock()
        with patch.object(actions, "SceneModelService", side_effect=RuntimeError("boom")):
            self.assertEqual(actions.list_scene_mmd_models(cmds_module=cmds), [])

    # -- HUMANIK-FRONTEND-1 Phase B4: connect_retarget / disconnect_retarget -

    def test_connect_retarget_runs_source_then_target_in_order(self):
        actions._confirm_dialog = lambda **kwargs: "Continue"

        result = actions.connect_retarget("|source_root", "|target_root")

        self.assertEqual(result, "|target_root")
        source_index = self.session.calls.index(("enter_source_mode", "|source_root"))
        target_index = self.session.calls.index(("enter_target_mode", "|target_root"))
        self.assertLess(source_index, target_index)

    def test_connect_retarget_rejects_identical_source_and_target(self):
        actions.connect_retarget("|same_root", "|same_root")

        self.assertTrue(any("must differ" in message for message in self._errors))
        self.assertNotIn(("enter_source_mode", "|same_root"), self.session.calls)

    def test_connect_retarget_rejects_missing_models(self):
        actions.connect_retarget("", "|target_root")

        self.assertTrue(any("requires both" in message for message in self._errors))
        self.assertEqual(self.session.calls, [])

    def test_connect_retarget_stops_before_target_when_source_step_fails(self):
        class FailingSourceSession(_FakeSession):
            def enter_source_mode(self, root):
                raise RuntimeError("source failed")

        actions.set_humanik_session(FailingSourceSession())
        actions._confirm_dialog = lambda **kwargs: "Continue"

        result = actions.connect_retarget("|source_root", "|target_root")

        self.assertIsNone(result)
        self.assertTrue(any("Enter Source Mode failed" in message for message in self._errors))
        self.assertNotIn(("enter_target_mode", "|target_root"), self.session.calls)

    # -- HUMANIK-EXTERNAL-SOURCE-1 ES-3: external (non-MMD) HIK source ------

    def test_connect_retarget_accepts_mmd_kind_value_pairs(self):
        actions._confirm_dialog = lambda **kwargs: "Continue"

        result = actions.connect_retarget(("mmd", "|source_root"), "|target_root")

        self.assertEqual(result, "|target_root")
        self.assertIn(("enter_source_mode", "|source_root"), self.session.calls)
        self.assertNotIn(("enter_external_source_mode", "|source_root"), self.session.calls)

    def test_connect_retarget_external_skips_auto_characterize_of_the_source(self):
        self.cmds.listConnections.return_value = []

        result = actions.connect_retarget(("external", "MocapChar"), "|target_root")

        self.assertEqual(result, "|target_root")
        self.assertIn(("enter_external_source_mode", "MocapChar"), self.session.calls)
        self.assertNotIn(("enter_source_mode", "MocapChar"), self.session.calls)
        # No dialog is shown when there is no existing animation to warn about.
        source_index = self.session.calls.index(("enter_external_source_mode", "MocapChar"))
        target_index = self.session.calls.index(("enter_target_mode", "|target_root"))
        self.assertLess(source_index, target_index)

    def test_connect_retarget_external_rejects_missing_character(self):
        result = actions.connect_retarget(("external", ""), "|target_root")

        self.assertIsNone(result)
        self.assertTrue(any("requires both" in message for message in self._errors))
        self.assertEqual(self.session.calls, [])

    def test_connect_retarget_external_warns_and_clears_existing_target_animation(self):
        class SessionWithJoints(_FakeSession):
            def inspect_model(self, root, **kwargs):
                return {"modelRoot": root, "assignments": [{"joint": "|target|Hips"}]}

        session = SessionWithJoints()
        actions.set_humanik_session(session)
        self.cmds.listConnections.return_value = ["curveA", "curveB"]
        dialog = {}

        def choose(**kwargs):
            dialog.update(kwargs)
            return "Clear and connect"

        actions._confirm_dialog = choose

        result = actions.connect_retarget(("external", "MocapChar"), "|target_root")

        self.assertEqual(result, "|target_root")
        self.assertIn("2", dialog["message"])
        self.cmds.undoInfo.assert_any_call(openChunk=True)
        self.cmds.delete.assert_called_once_with(["curveA", "curveB"])
        self.cmds.undoInfo.assert_any_call(closeChunk=True)
        self.assertIn(("enter_external_source_mode", "MocapChar"), session.calls)

    def test_connect_retarget_external_connect_anyway_keeps_existing_animation(self):
        class SessionWithJoints(_FakeSession):
            def inspect_model(self, root, **kwargs):
                return {"modelRoot": root, "assignments": [{"joint": "|target|Hips"}]}

        session = SessionWithJoints()
        actions.set_humanik_session(session)
        self.cmds.listConnections.return_value = ["curveA"]
        actions._confirm_dialog = lambda **kwargs: "Connect anyway"

        result = actions.connect_retarget(("external", "MocapChar"), "|target_root")

        self.assertEqual(result, "|target_root")
        self.cmds.delete.assert_not_called()
        self.assertIn(("enter_external_source_mode", "MocapChar"), session.calls)

    def test_connect_retarget_external_cancel_on_existing_animation_stops(self):
        class SessionWithJoints(_FakeSession):
            def inspect_model(self, root, **kwargs):
                return {"modelRoot": root, "assignments": [{"joint": "|target|Hips"}]}

        session = SessionWithJoints()
        actions.set_humanik_session(session)
        self.cmds.listConnections.return_value = ["curveA"]
        actions._confirm_dialog = lambda **kwargs: "Cancel"

        result = actions.connect_retarget(("external", "MocapChar"), "|target_root")

        self.assertIsNone(result)
        self.cmds.delete.assert_not_called()
        self.assertNotIn(("enter_external_source_mode", "MocapChar"), session.calls)

    def test_connect_retarget_mmd_source_never_scans_for_existing_animation(self):
        # ES-3: "MMD source connection keeps current behavior" -- the
        # animation-clear check only runs on the external-source branch.
        actions._confirm_dialog = lambda **kwargs: "Continue"
        self.cmds.listConnections.return_value = ["curveA"]

        result = actions.connect_retarget("|source_root", "|target_root")

        self.assertEqual(result, "|target_root")
        self.cmds.delete.assert_not_called()

    def test_enter_external_source_mode_dispatches_to_the_session(self):
        result = actions.enter_external_source_mode("MocapChar")

        self.assertEqual(result, {"character": "MocapChar", "external": True, "locked": True})
        self.assertIn(("enter_external_source_mode", "MocapChar"), self.session.calls)

    def test_enter_external_source_mode_requires_a_character(self):
        result = actions.enter_external_source_mode("")

        self.assertIsNone(result)
        self.assertTrue(any("requires a character" in message for message in self._errors))

    def test_disconnect_retarget_runs_immediately_when_no_control_rig_is_active(self):
        # HUMANIK-FRONTEND-1 Phase B6: confirmation is shown only when a
        # Control Rig transaction is currently active.
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown without an active Control Rig")
        )

        self.assertTrue(actions.disconnect_retarget())
        self.assertIn(("restore_mmd_rig",), self.session.calls)

    def test_disconnect_retarget_confirms_before_restoring_an_active_control_rig(self):
        self.session.describe_frontend_state = lambda model_root=None: {
            "controlRigs": [{"modelRoot": "|model_root", "character": "Char"}]
        }
        dialog = {}

        def choose(**kwargs):
            dialog.update(kwargs)
            return "Continue"

        actions._confirm_dialog = choose

        self.assertTrue(actions.disconnect_retarget())
        self.assertIn("Control Rig", dialog["message"])
        self.assertIn(("restore_mmd_rig",), self.session.calls)

    def test_disconnect_retarget_cancel_with_active_control_rig_does_not_restore(self):
        self.session.describe_frontend_state = lambda model_root=None: {
            "controlRigs": [{"modelRoot": "|model_root", "character": "Char"}]
        }
        actions._confirm_dialog = lambda **kwargs: "Cancel"

        self.assertIsNone(actions.disconnect_retarget())
        self.assertNotIn(("restore_mmd_rig",), self.session.calls)

    def test_restore_mmd_rig_runs_immediately_when_no_control_rig_is_active(self):
        actions._confirm_dialog = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("no dialog should be shown without an active Control Rig")
        )

        self.assertTrue(actions.restore_mmd_rig())
        self.assertIn(("restore_mmd_rig",), self.session.calls)

    def test_restore_mmd_rig_confirms_before_deleting_an_active_control_rig(self):
        self.session.describe_frontend_state = lambda model_root=None: {
            "controlRigs": [{"modelRoot": "|model_root", "character": "Char"}]
        }
        actions._confirm_dialog = lambda **kwargs: "Cancel"

        self.assertIsNone(actions.restore_mmd_rig())
        self.assertNotIn(("restore_mmd_rig",), self.session.calls)


if __name__ == "__main__":
    unittest.main()
