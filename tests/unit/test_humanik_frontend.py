"""Unit tests for the UI-neutral HumanIK frontend session."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_bake import HumanIkBakeResult
from mmd_tools.core.humanik_builder import HumanIkCharacterCreationError
from mmd_tools.core.humanik_frontend import (
    FULL_ASSIGNMENT_PROFILE,
    HumanIkFrontendSession,
    _split_body_assignments,
    filter_humanik_body_assignments,
)
from mmd_tools.core.humanik_transaction import HumanIkRestoreState
from mmd_tools.core.humanik_resolver import (
    HumanIkBoneAssignment,
    HumanIkResolveResult,
)


def _assignment(hik_bone, hik_index, joint):
    return HumanIkBoneAssignment(
        joint=joint,
        mmd_bone=hik_bone,
        hik_bone=hik_bone,
        hik_index=hik_index,
        source="test",
    )


def _result():
    return HumanIkResolveResult(
        assignments=(
            _assignment("Hips", 1, "|source|hips"),
            _assignment("LeftArmRoll", 45, "|source|left_arm_roll"),
            _assignment("LeftHandIndex1", 54, "|source|left_index"),
        ),
        missing_mmd_bones=("head",),
        unindexed_mmd_bones=(),
        duplicate_assignments=(),
    )


def _synthetic_55_result():
    body_names = (
        "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
        "LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand",
        "RightShoulder", "RightArm", "RightForeArm", "RightHand",
        "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg",
        "RightFoot", "LeftToeBase",
    )
    body = [_assignment(name, index, "|model|%s" % name.lower()) for index, name in enumerate(body_names)]
    body.extend(
        _assignment(name, 30 + index, "|model|%s" % name.lower())
        for index, name in enumerate(("LeftArmRoll", "RightArmRoll", "LeftLegRoll", "RightLegRoll"))
    )
    fingers = [
        _assignment("LeftHandIndex%d" % index, 100 + index, "|model|left_index%d" % index)
        for index in range(1, 31)
    ]
    return HumanIkResolveResult(
        assignments=tuple(body + fingers),
        missing_mmd_bones=(),
        unindexed_mmd_bones=(),
        duplicate_assignments=(),
    )


class FakePreview:
    def __init__(self):
        self.active = True
        self.restore_state = object()


class FakeControlRigTransaction:
    def __init__(self, ownership_id="owner:control-rig", character="Character_source"):
        self.active = True
        self.ownership_id = ownership_id
        self.character = character


class FakeStance:
    """Host-neutral transaction double for frontend lifecycle tests."""

    def __init__(self, model_root, assignments, **_kwargs):
        self.model_root = model_root
        self.assignments = tuple(assignments)
        self.stance_evidence = {"mode": "test-automatic-stance"}
        self.active = False
        self.prepared = False
        self.restores = 0
        self.character = None

    def prepare(self):
        self.prepared = True
        return self

    def enter(self):
        self.active = True
        return self

    def attach_character(self, character):
        self.character = character

    def restore(self):
        self.restores += 1
        self.active = False
        return {"passed": True}

    def to_dict(self):
        return {
            "modelRoot": self.model_root,
            "active": self.active,
            "prepared": self.prepared,
            "stanceEvidence": dict(self.stance_evidence),
        }


def _session():
    return HumanIkFrontendSession(stance_transaction_factory=FakeStance)


class TestHumanIkFrontend(unittest.TestCase):
    @patch("mmd_tools.core.humanik_frontend._find_mmd_model_root_for_character", return_value="|target")
    @patch("mmd_tools.core.humanik_frontend.persist_humanik_restore_state")
    @patch("mmd_tools.core.humanik_frontend.load_humanik_restore_state")
    def test_new_session_rebuilds_persisted_control_rig_transaction(
        self, load_state, persist_state, find_model
    ):
        restore_state = HumanIkRestoreState(
            "mmd-tools:frontend:control-rig:|target",
            "Character_target",
            True,
            "",
            -1,
            [],
            [],
        )
        load_state.return_value = [{
            "modelRoot": "|target",
            "ownershipId": restore_state.ownership_id,
            "character": restore_state.character,
            "restore_state": restore_state.to_dict(),
            "disconnected": [],
            "retainedNodes": [],
            "createdNodes": ["HIKControlSetNode1"],
            "preCycleBaseline": [],
            "postCyclePlugs": [],
            "active": True,
        }]

        class Cmds:
            def objExists(self, name):
                return name in {"|target", "Character_target"}

        session = HumanIkFrontendSession(cmds_module=Cmds())

        self.assertIn("|target", session._control_rig_transactions)
        transaction = session._control_rig_transactions["|target"]
        self.assertTrue(transaction.active)
        self.assertEqual(transaction.restore_state.input_source, "")
        find_model.assert_called_once_with("Character_target", session._cmds or find_model.call_args.args[1])
        persist_state.assert_not_called()

    def test_synthetic_profile_has_25_body_roll_and_30_finger_assignments(self):
        result = _synthetic_55_result()

        body, excluded = _split_body_assignments(result)
        self.assertEqual(len(body.assignments), 25)
        self.assertEqual(len(excluded), 30)
        self.assertEqual(sum("Roll" in item.hik_bone for item in body.assignments), 4)

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_setup_automatically_runs_stance_without_manual_confirmation(self, resolve, create, lock):
        session = _session()

        binding = session.setup_and_characterize("|source")

        self.assertEqual(binding.stance["mode"], "test-automatic-stance")
        resolve.assert_called_once()
        create.assert_called_once()
        lock.assert_called_once()

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_synthetic_55_result())
    def test_setup_full_profile_keeps_finger_assignments(self, resolve, create, lock):
        session = _session()

        binding = session.setup_and_characterize(
            "|source",
            profile=FULL_ASSIGNMENT_PROFILE,
            include_fingers=True,
        )

        self.assertEqual(binding.profile, FULL_ASSIGNMENT_PROFILE)
        self.assertEqual(len(binding.assignments), 55)
        self.assertEqual(binding.to_dict()["assignmentCount"], 55)
        self.assertEqual(binding.to_dict()["excludedFingerCount"], 0)
        self.assertTrue(binding.to_dict()["includeFingers"])
        self.assertEqual(len(session._pending_stances), 0)
        self.assertEqual(len(create.call_args.args[0].assignments), 55)
        self.assertEqual(len(session._bindings["|source"].assignments), 55)
        session.enter_source_mode("|source")
        diagnostics = session.diagnostics("|source")
        self.assertEqual(diagnostics["profile"], FULL_ASSIGNMENT_PROFILE)
        self.assertEqual(diagnostics["source"]["profile"], FULL_ASSIGNMENT_PROFILE)
        self.assertEqual(diagnostics["assignments"]["excludedFingerCount"], 0)
        self.assertEqual(diagnostics["profileCoverage"]["expectedAssignmentCount"], 55)
        self.assertEqual(diagnostics["quality"]["status"], "experimental")
        self.assertEqual(diagnostics["quality"]["fingerStatus"], "included-experimental")
        with patch(
            "mmd_tools.core.humanik_frontend.collect_hik_ownership_report",
            return_value={"rows": [], "counts": {}},
        ):
            inferred_report = session.inspect_target_ownership("|source")
        self.assertEqual(inferred_report["profile"], FULL_ASSIGNMENT_PROFILE)
        self.assertEqual(inferred_report["assignmentCount"], 55)

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_setup_same_root_rejects_profile_change(self, resolve, create, lock):
        session = _session()
        session.setup_and_characterize("|source")

        with self.assertRaisesRegex(ValueError, "different assignment profile"):
            session.setup_and_characterize(
                "|source",
                profile=FULL_ASSIGNMENT_PROFILE,
                include_fingers=True,
            )

        create.assert_called_once()
        resolve.assert_called_once()

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_synthetic_55_result())
    def test_include_fingers_flag_selects_full_profile_without_profile_argument(self, resolve, create, lock):
        binding = _session().setup_and_characterize("|source", include_fingers=True)

        self.assertEqual(binding.profile, FULL_ASSIGNMENT_PROFILE)
        self.assertEqual(len(binding.assignments), 55)

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_stance_captures_character_state_after_lock(self, resolve, create, lock):
        events = []

        class OrderedStance(FakeStance):
            def attach_character(self, character):
                events.append("attach")
                super().attach_character(character)

        def factory(*args, **kwargs):
            return OrderedStance(*args, **kwargs)

        lock.side_effect = lambda *args, **kwargs: events.append("lock")
        session = HumanIkFrontendSession(stance_transaction_factory=factory)

        session.setup_and_characterize("|source")

        self.assertEqual(events, ["lock", "attach"])

    def test_body_filter_excludes_finger_and_keeps_roll(self):
        result = filter_humanik_body_assignments(_result())

        self.assertEqual([item.hik_bone for item in result.assignments], ["Hips", "LeftArmRoll"])

    @patch("mmd_tools.core.humanik_frontend.collect_hik_ownership_report", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_read_only_inspect_reports_assignments_without_character_creation(self, resolve, ownership):
        session = _session()

        model_report = session.inspect_model("|source")
        ownership_report = session.inspect_target_ownership("|source")

        self.assertEqual(model_report["assignmentCount"], 2)
        self.assertEqual(model_report["excludedFingerCount"], 1)
        self.assertEqual(ownership_report["constraintCounts"], {})
        self.assertEqual(ownership_report["constraintRows"], [])
        self.assertFalse(model_report["automaticStance"]["ready"])
        self.assertEqual(ownership_report["automaticStance"]["ownership"]["disconnect"], [])
        self.assertEqual(ownership_report["automaticStance"]["ownership"]["retain"], [])
        self.assertEqual(resolve.call_count, 2)
        ownership.assert_called_once()

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_setup_is_idempotent_and_reports_excluded_fingers(self, resolve, create, lock):
        session = _session()

        first = session.setup_and_characterize("|source")
        second = session.setup_and_characterize("|source")

        self.assertIs(first, second)
        self.assertEqual(first.character, "Character_source")
        self.assertEqual(len(first.assignments), 2)
        self.assertEqual(first.to_dict()["excludedFingerCount"], 1)
        create.assert_called_once()
        lock.assert_called_once_with("Character_source", mel_module=None)

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: "Character_" + kwargs["name_hint"].split("_")[-1])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_target_rejects_source_root_and_blocker_before_preview_mutation(self, resolve, create, lock):
        session = _session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")

        with self.assertRaisesRegex(ValueError, "must differ"):
            session.enter_target_mode("|source")

        with patch(
            "mmd_tools.core.humanik_frontend.collect_hik_ownership_report",
            return_value={"rows": [{"node": "physics", "classification": "physics_blocker"}], "counts": {}},
        ), patch(
            "mmd_tools.core.humanik_frontend.begin_humanik_target_preview"
        ) as begin:
            session.setup_and_characterize("|target")
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                session.enter_target_mode("|target")
            begin.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_target_preview")
    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview", return_value=FakePreview())
    @patch("mmd_tools.core.humanik_frontend.collect_hik_ownership_report", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: "Character_" + kwargs["name_hint"].split("_")[-1])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_preview_bake_and_restore_lifecycle(self, resolve, create, lock, ownership, begin, stop):
        session = _session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target")
        preview = session.enter_target_mode("|target")

        self.assertIs(session.active_preview, preview)
        fake_bake = HumanIkBakeResult(0, 1, 2, {}, 0.0, [])
        with patch("mmd_tools.core.humanik_frontend.bake_humanik_target_preview", return_value=fake_bake):
            result = session.bake_to_mmd_rig(0, 1)
        self.assertIs(result, fake_bake)
        self.assertIsNone(session.active_preview)
        self.assertFalse(session.restore_mmd_rig())

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview", return_value=FakePreview())
    @patch("mmd_tools.core.humanik_frontend.collect_hik_ownership_report", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: "Character_" + kwargs["name_hint"].split("_")[-1])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_synthetic_55_result())
    def test_full_profile_preview_and_bake_receive_all_assignments(
        self, resolve, create, lock, ownership, begin
    ):
        session = _session()
        session.setup_and_characterize("|source", profile=FULL_ASSIGNMENT_PROFILE, include_fingers=True)
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target", profile=FULL_ASSIGNMENT_PROFILE, include_fingers=True)
        session.enter_target_mode("|target")

        preview_joints = tuple(begin.call_args.args[4])
        self.assertEqual(len(preview_joints), 55)
        fake_bake = HumanIkBakeResult(0, 1, 55, {}, 0.0, [])
        with patch(
            "mmd_tools.core.humanik_frontend.bake_humanik_target_preview",
            return_value=fake_bake,
        ) as bake:
            self.assertIs(session.bake_to_mmd_rig(0, 1), fake_bake)
        self.assertEqual(len(tuple(bake.call_args.args[1])), 55)

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview")
    @patch("mmd_tools.core.humanik_frontend.collect_hik_ownership_report", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: "Character_" + kwargs["name_hint"].split("_")[-1])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_target_rejects_source_target_profile_mismatch_before_preview(
        self, resolve, create, lock, ownership, begin
    ):
        session = _session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target", profile=FULL_ASSIGNMENT_PROFILE, include_fingers=True)

        with self.assertRaisesRegex(ValueError, "source/target assignment profile mismatch"):
            session.enter_target_mode("|target")

        ownership.assert_not_called()
        begin.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_diagnostics_are_json_safe_and_include_quality_route(self, resolve, create, lock):
        session = _session()
        session.setup_and_characterize("|source")

        diagnostics = session.diagnostics("|source")

        self.assertEqual(diagnostics["assignments"]["excludedFingerCount"], 1)
        self.assertEqual(diagnostics["quality"]["status"], "experimental")
        self.assertEqual(diagnostics["quality"]["referenceS5bBodyMatrixResidual"], 0.0298786502441323)
        self.assertEqual(diagnostics["quality"]["fingerStatus"], "deferred")
        self.assertEqual(diagnostics["preview"], {"active": False, "restoreStateAvailable": False})
        self.assertEqual(diagnostics["assignments"]["required"]["genericLockMinimumAssignmentCount"], 1)

    @patch("mmd_tools.core.humanik_frontend.delete_humanik_character")
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition", side_effect=RuntimeError("lock failed"))
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Pending")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_lock_failure_cleans_up_created_character(self, resolve, create, lock, delete):
        session = _session()

        with self.assertRaisesRegex(RuntimeError, "lock failed"):
            session.setup_and_characterize("|source")
        delete.assert_called_once_with("Pending", mel_module=None)
        self.assertFalse(session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.delete_humanik_character", side_effect=RuntimeError("delete failed"))
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition", side_effect=RuntimeError("lock failed"))
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Pending")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_failed_cleanup_is_pending_and_restore_retries(self, resolve, create, lock, delete):
        session = _session()

        with self.assertRaisesRegex(RuntimeError, "cleanup also failed"):
            session.setup_and_characterize("|source")
        self.assertIn("Pending", session._pending_characters)
        delete.side_effect = [RuntimeError("delete failed"), None]
        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            session.restore_mmd_rig()
        self.assertIn("Pending", session._pending_characters)
        self.assertTrue(session.restore_mmd_rig())
        self.assertNotIn("Pending", session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_create_cleanup_success_does_not_leave_frontend_pending(self, resolve, create):
        creation_error = HumanIkCharacterCreationError(
            "Cleaned",
            RuntimeError("assignment failed"),
        )
        create.side_effect = creation_error
        session = _session()

        with self.assertRaises(HumanIkCharacterCreationError):
            session.setup_and_characterize("|source")
        self.assertFalse(session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.delete_humanik_character")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_create_cleanup_failure_is_pending_and_restore_retries(self, resolve, create, delete):
        creation_error = HumanIkCharacterCreationError(
            "Orphaned",
            RuntimeError("assignment failed"),
            cleanup_error=RuntimeError("cleanup failed"),
        )
        create.side_effect = creation_error
        delete.side_effect = [RuntimeError("retry failed"), None]
        session = _session()

        with self.assertRaises(HumanIkCharacterCreationError):
            session.setup_and_characterize("|source")
        self.assertEqual(session._pending_characters, {"Orphaned": "|source"})

        with self.assertRaisesRegex(RuntimeError, "retry failed"):
            session.restore_mmd_rig()
        self.assertIn("Orphaned", session._pending_characters)
        self.assertTrue(session.restore_mmd_rig())
        self.assertFalse(session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_control_rig", return_value=object())
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_active_preview_rejects_session_mutations(self, resolve, create, lock, control_rig):
        session = _session()
        session.setup_and_characterize("|source")
        session._preview = FakePreview()

        with self.assertRaisesRegex(RuntimeError, "active"):
            session.setup_and_characterize("|other")
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.enter_source_mode("|source")
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.create_control_rig("|source")
        control_rig.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview")
    @patch(
        "mmd_tools.core.humanik_frontend.collect_hik_ownership_report",
        return_value={"rows": [{"node": "physics", "classification": "physics_blocker"}], "counts": {"physics_blocker": 1}},
    )
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: kwargs["name_hint"])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_blocker_report_is_retained_without_preview(self, resolve, create, lock, ownership, begin):
        session = _session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target")

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            session.enter_target_mode("|target")
        diagnostics = session.diagnostics()
        self.assertEqual(diagnostics["target"]["modelRoot"], "|target")
        self.assertEqual(diagnostics["ownership"]["blockers"][0]["classification"], "physics_blocker")
        begin.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_target_preview")
    def test_restore_failure_keeps_preview_for_retry(self, stop):
        session = _session()
        preview = FakePreview()
        session._preview = preview
        def restore_then_clear(*args, **kwargs):
            preview.active = False

        stop.side_effect = [RuntimeError("restore failed"), restore_then_clear]

        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            session.restore_mmd_rig()
        self.assertIs(session._preview, preview)
        self.assertTrue(session.restore_mmd_rig())

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_control_rig")
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_create_control_rig_rejects_active_source_before_mutation(
        self, resolve, create, lock, begin
    ):
        session = _session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")

        with self.assertRaisesRegex(RuntimeError, "active HumanIK SOURCE"):
            session.create_control_rig("|source")
        begin.assert_not_called()

    @patch(
        "mmd_tools.core.humanik_frontend.begin_humanik_control_rig",
        return_value=FakeControlRigTransaction(),
    )
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_create_control_rig_uses_transaction_and_is_idempotent(
        self, resolve, create, lock, begin
    ):
        session = _session()
        binding = session.setup_and_characterize("|source")

        created = session.create_control_rig("|source")
        self.assertTrue(created)
        begin.assert_called_once()
        self.assertTrue(binding.control_rig_created)
        self.assertIn("|source", session._control_rig_transactions)

        # A second call is a no-op while the transaction is still active.
        self.assertTrue(session.create_control_rig("|source"))
        begin.assert_called_once()

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_control_rig")
    @patch(
        "mmd_tools.core.humanik_frontend.begin_humanik_control_rig",
        return_value=FakeControlRigTransaction(),
    )
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_restore_mmd_rig_tears_down_control_rig_transaction(
        self, resolve, create, lock, begin, stop
    ):
        session = _session()
        binding = session.setup_and_characterize("|source")
        session.create_control_rig("|source")
        transaction = session._control_rig_transactions["|source"]

        def deactivate(*_args, **_kwargs):
            transaction.active = False

        stop.side_effect = deactivate

        self.assertTrue(session.restore_mmd_rig())

        stop.assert_called_once()
        self.assertNotIn("|source", session._control_rig_transactions)
        self.assertFalse(binding.control_rig_created)

    def test_bake_failure_retains_active_preview_but_clears_inactive_preview(self):
        session = _session()
        target = _result()
        session._bindings["|target"] = type("Binding", (), {"assignments": target.assignments})()
        session._target_model_root = "|target"
        active = FakePreview()
        session._preview = active
        with patch(
            "mmd_tools.core.humanik_frontend.bake_humanik_target_preview",
            side_effect=RuntimeError("bake failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "bake failed"):
                session.bake_to_mmd_rig(0, 1)
        self.assertIs(session._preview, active)

        inactive = FakePreview()
        session._preview = inactive

        def fail_after_inactive(*args, **kwargs):
            inactive.active = False
            raise RuntimeError("bake failed")

        with patch(
            "mmd_tools.core.humanik_frontend.bake_humanik_target_preview",
            side_effect=fail_after_inactive,
        ):
            with self.assertRaisesRegex(RuntimeError, "bake failed"):
                session.bake_to_mmd_rig(0, 1)
        self.assertIsNone(session._preview)


class FakeHikSceneCmds:
    """Minimal ``cmds`` double for external-source scene scans.

    Only the one call ``enter_external_source_mode`` makes directly:
    ``ls(type="HIKCharacterNode")``.
    """

    def __init__(self, characters=()):
        self.characters = list(characters)

    def ls(self, type=None):
        if type == "HIKCharacterNode":
            return list(self.characters)
        return []


class TestExternalSourceMode(unittest.TestCase):
    """HUMANIK-EXTERNAL-SOURCE-1 ES-1: a non-MMD, already-locked scene HIK
    character selected as SOURCE via ``enter_external_source_mode``."""

    @staticmethod
    def _session(characters=("Character_mocap",)):
        return HumanIkFrontendSession(
            cmds_module=FakeHikSceneCmds(characters),
            stance_transaction_factory=FakeStance,
        )

    def test_rejects_empty_character(self):
        session = self._session()

        with self.assertRaises(ValueError):
            session.enter_external_source_mode("   ")

    def test_rejects_character_not_in_scene(self):
        session = self._session(characters=())

        with self.assertRaisesRegex(RuntimeError, "not found in scene"):
            session.enter_external_source_mode("Character_mocap")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=False)
    def test_rejects_unlocked_character(self, lock_state):
        session = self._session()

        with self.assertRaisesRegex(RuntimeError, "locked"):
            session.enter_external_source_mode("Character_mocap")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_success_sets_external_source_state(self, lock_state):
        session = self._session()

        result = session.enter_external_source_mode("Character_mocap")

        self.assertEqual(
            result, {"character": "Character_mocap", "external": True, "locked": True}
        )
        self.assertEqual(session._external_source_character, "Character_mocap")
        self.assertIsNone(session._source_model_root)

    def test_active_preview_rejects_mutation(self):
        session = self._session()
        session._preview = FakePreview()

        with self.assertRaisesRegex(RuntimeError, "active"):
            session.enter_external_source_mode("Character_mocap")

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_entering_mmd_source_clears_external_source(self, lock_state, resolve, create, lock):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")
        session.setup_and_characterize("|source")

        session.enter_source_mode("|source")

        self.assertIsNone(session._external_source_character)
        self.assertEqual(session._source_model_root, "|source")

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_entering_external_source_clears_mmd_source(self, lock_state, resolve, create, lock):
        session = self._session()
        session.setup_and_characterize("|source")
        session.enter_source_mode("|source")

        session.enter_external_source_mode("Character_mocap")

        self.assertIsNone(session._source_model_root)
        self.assertEqual(session._external_source_character, "Character_mocap")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview", return_value=FakePreview())
    @patch("mmd_tools.core.humanik_frontend.collect_hik_ownership_report", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_target")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_enter_target_mode_connects_external_source_without_profile_check(
        self, resolve, create, lock, ownership, begin, lock_state
    ):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")
        session.setup_and_characterize("|target", profile=FULL_ASSIGNMENT_PROFILE, include_fingers=True)

        preview = session.enter_target_mode("|target")

        self.assertIs(session.active_preview, preview)
        begin.assert_called_once()
        self.assertEqual(begin.call_args.args[1], "Character_target")
        self.assertEqual(begin.call_args.args[2], "Character_mocap")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_mocap")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_enter_target_mode_rejects_target_matching_external_source_character(
        self, resolve, create, lock, lock_state
    ):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")
        session.setup_and_characterize("|target")

        with self.assertRaisesRegex(ValueError, "must differ"):
            session.enter_target_mode("|target")

    def test_no_source_before_external_or_mmd_selection_raises(self):
        session = self._session()

        with self.assertRaisesRegex(RuntimeError, "before target mode"):
            session.enter_target_mode("|target")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_restore_mmd_rig_clears_external_source_selection(self, lock_state):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")

        self.assertTrue(session.restore_mmd_rig())

        self.assertIsNone(session._external_source_character)
        self.assertFalse(session.restore_mmd_rig())


if __name__ == "__main__":
    unittest.main()
