"""Unit tests for the UI-neutral HumanIK frontend session."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_bake import HumanIkBakeResult
from mmd_tools.core.humanik_builder import HumanIkCharacterCreationError
from mmd_tools.core.humanik_frontend import (
    HumanIkFrontendSession,
    _split_body_assignments,
    filter_humanik_body_assignments,
)
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
        self.journal = object()


class TestHumanIkFrontend(unittest.TestCase):
    def test_synthetic_profile_has_25_body_roll_and_30_finger_assignments(self):
        result = _synthetic_55_result()

        body, excluded = _split_body_assignments(result)
        self.assertEqual(len(body.assignments), 25)
        self.assertEqual(len(excluded), 30)
        self.assertEqual(sum("Roll" in item.hik_bone for item in body.assignments), 4)

    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition")
    def test_stance_must_be_confirmed_before_scene_mutation(self, create, resolve):
        session = HumanIkFrontendSession()

        with self.assertRaisesRegex(ValueError, "stance_confirmed"):
            session.setup_and_characterize("|source")
        resolve.assert_not_called()
        create.assert_not_called()

    def test_body_filter_excludes_finger_and_keeps_roll(self):
        result = filter_humanik_body_assignments(_result())

        self.assertEqual([item.hik_bone for item in result.assignments], ["Hips", "LeftArmRoll"])

    @patch("mmd_tools.core.humanik_frontend.collect_humanik_constraint_facts", return_value=[])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_read_only_inspect_reports_assignments_without_character_creation(self, resolve, collect):
        session = HumanIkFrontendSession()

        model_report = session.inspect_model("|source")
        ownership_report = session.inspect_target_ownership("|source")

        self.assertEqual(model_report["assignmentCount"], 2)
        self.assertEqual(model_report["excludedFingerCount"], 1)
        self.assertEqual(ownership_report["constraintCounts"], {})
        self.assertEqual(ownership_report["constraintRows"], [])
        self.assertEqual(resolve.call_count, 2)
        collect.assert_called_once()

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_setup_is_idempotent_and_reports_excluded_fingers(self, resolve, create, lock):
        session = HumanIkFrontendSession()

        first = session.setup_and_characterize("|source", stance_confirmed=True)
        second = session.setup_and_characterize("|source", stance_confirmed=True)

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
        session = HumanIkFrontendSession()
        session.setup_and_characterize("|source", stance_confirmed=True)
        session.enter_source_mode("|source")

        with self.assertRaisesRegex(ValueError, "must differ"):
            session.enter_target_mode("|source")

        with patch(
            "mmd_tools.core.humanik_frontend.classify_humanik_constraints",
            return_value={"rows": [{"node": "physics", "classification": "physics_blocker"}], "counts": {}},
        ), patch("mmd_tools.core.humanik_frontend.collect_humanik_constraint_facts", return_value=[]), patch(
            "mmd_tools.core.humanik_frontend.begin_humanik_target_preview"
        ) as begin:
            session.setup_and_characterize("|target", stance_confirmed=True)
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                session.enter_target_mode("|target")
            begin.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_target_preview")
    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview", return_value=FakePreview())
    @patch("mmd_tools.core.humanik_frontend.collect_humanik_constraint_facts", return_value=[])
    @patch("mmd_tools.core.humanik_frontend.classify_humanik_constraints", return_value={"rows": [], "counts": {}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: "Character_" + kwargs["name_hint"].split("_")[-1])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_preview_bake_and_restore_lifecycle(self, resolve, create, lock, classify, collect, begin, stop):
        session = HumanIkFrontendSession()
        session.setup_and_characterize("|source", stance_confirmed=True)
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target", stance_confirmed=True)
        preview = session.enter_target_mode("|target")

        self.assertIs(session.active_preview, preview)
        fake_bake = HumanIkBakeResult(0, 1, 2, {}, 0.0, [])
        with patch("mmd_tools.core.humanik_frontend.bake_humanik_target_preview", return_value=fake_bake):
            result = session.bake_to_mmd_rig(0, 1)
        self.assertIs(result, fake_bake)
        self.assertIsNone(session.active_preview)
        self.assertFalse(session.restore_mmd_rig())

    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_diagnostics_are_json_safe_and_include_quality_route(self, resolve, create, lock):
        session = HumanIkFrontendSession()
        session.setup_and_characterize("|source", stance_confirmed=True)

        diagnostics = session.diagnostics("|source")

        self.assertEqual(diagnostics["assignments"]["excludedFingerCount"], 1)
        self.assertEqual(diagnostics["quality"]["status"], "experimental")
        self.assertEqual(diagnostics["quality"]["referenceS5bBodyMatrixResidual"], 0.0298786502441323)
        self.assertEqual(diagnostics["quality"]["fingerStatus"], "deferred")
        self.assertEqual(diagnostics["preview"], {"active": False, "journalAvailable": False})
        self.assertEqual(diagnostics["assignments"]["required"]["genericLockMinimumAssignmentCount"], 1)

    @patch("mmd_tools.core.humanik_frontend.delete_humanik_character")
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition", side_effect=RuntimeError("lock failed"))
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Pending")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_lock_failure_cleans_up_created_character(self, resolve, create, lock, delete):
        session = HumanIkFrontendSession()

        with self.assertRaisesRegex(RuntimeError, "lock failed"):
            session.setup_and_characterize("|source", stance_confirmed=True)
        delete.assert_called_once_with("Pending", mel_module=None)
        self.assertFalse(session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.delete_humanik_character", side_effect=RuntimeError("delete failed"))
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition", side_effect=RuntimeError("lock failed"))
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Pending")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_failed_cleanup_is_pending_and_restore_retries(self, resolve, create, lock, delete):
        session = HumanIkFrontendSession()

        with self.assertRaisesRegex(RuntimeError, "cleanup also failed"):
            session.setup_and_characterize("|source", stance_confirmed=True)
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
        session = HumanIkFrontendSession()

        with self.assertRaises(HumanIkCharacterCreationError):
            session.setup_and_characterize("|source", stance_confirmed=True)
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
        session = HumanIkFrontendSession()

        with self.assertRaises(HumanIkCharacterCreationError):
            session.setup_and_characterize("|source", stance_confirmed=True)
        self.assertEqual(session._pending_characters, {"Orphaned": "|source"})

        with self.assertRaisesRegex(RuntimeError, "retry failed"):
            session.restore_mmd_rig()
        self.assertIn("Orphaned", session._pending_characters)
        self.assertTrue(session.restore_mmd_rig())
        self.assertFalse(session._pending_characters)

    @patch("mmd_tools.core.humanik_frontend.create_humanik_control_rig", return_value=True)
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", return_value="Character_source")
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_active_preview_rejects_session_mutations(self, resolve, create, lock, control_rig):
        session = HumanIkFrontendSession()
        session.setup_and_characterize("|source", stance_confirmed=True)
        session._preview = FakePreview()

        with self.assertRaisesRegex(RuntimeError, "active"):
            session.setup_and_characterize("|other", stance_confirmed=True)
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.enter_source_mode("|source")
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.create_control_rig("|source")
        control_rig.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_target_preview")
    @patch("mmd_tools.core.humanik_frontend.collect_humanik_constraint_facts", return_value=[])
    @patch("mmd_tools.core.humanik_frontend.classify_humanik_constraints", return_value={"rows": [{"node": "physics", "classification": "physics_blocker"}], "counts": {"physics_blocker": 1}})
    @patch("mmd_tools.core.humanik_frontend.lock_humanik_definition")
    @patch("mmd_tools.core.humanik_frontend.create_humanik_definition", side_effect=lambda result, **kwargs: kwargs["name_hint"])
    @patch("mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments", return_value=_result())
    def test_blocker_report_is_retained_without_preview(self, resolve, create, lock, classify, collect, begin):
        session = HumanIkFrontendSession()
        session.setup_and_characterize("|source", stance_confirmed=True)
        session.enter_source_mode("|source")
        session.setup_and_characterize("|target", stance_confirmed=True)

        with self.assertRaisesRegex(RuntimeError, "blocked"):
            session.enter_target_mode("|target")
        diagnostics = session.diagnostics()
        self.assertEqual(diagnostics["target"]["modelRoot"], "|target")
        self.assertEqual(diagnostics["ownership"]["blockers"][0]["classification"], "physics_blocker")
        begin.assert_not_called()

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_target_preview")
    def test_restore_failure_keeps_preview_for_retry(self, stop):
        session = HumanIkFrontendSession()
        preview = FakePreview()
        session._preview = preview
        def restore_then_clear(*args, **kwargs):
            preview.active = False

        stop.side_effect = [RuntimeError("restore failed"), restore_then_clear]

        with self.assertRaisesRegex(RuntimeError, "restore failed"):
            session.restore_mmd_rig()
        self.assertIs(session._preview, preview)
        self.assertTrue(session.restore_mmd_rig())

    def test_bake_failure_retains_active_preview_but_clears_inactive_preview(self):
        session = HumanIkFrontendSession()
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


if __name__ == "__main__":
    unittest.main()
