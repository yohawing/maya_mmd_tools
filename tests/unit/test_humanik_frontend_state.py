"""Unit tests for ``HumanIkFrontendSession.describe_frontend_state``.

These tests use the same Fake/mock patterns as ``test_humanik_frontend.py``
so the structured-state API stays testable without opening Maya.  In
addition to checking the reported mode/action matrix, several tests call
the mirrored method afterward to confirm the ``allowed``/``reasonCode``
prediction agrees with what the real guard does.
"""

import json
import unittest
from unittest.mock import ANY, patch

from mmd_tools.core.humanik_frontend import (
    FRONTEND_MODE_CONTROL_RIG,
    FRONTEND_MODE_NEUTRAL,
    FRONTEND_MODE_SOURCE,
    FRONTEND_MODE_TARGET_PREVIEW,
    HumanIkFrontendSession,
    REASON_MODEL_IS_SOURCE,
    REASON_MODEL_REQUIRED,
    REASON_NO_ACTIVE_PREVIEW,
    REASON_NO_SOURCE,
    REASON_NOTHING_TO_RESTORE,
    REASON_NOT_CHARACTERIZED,
    REASON_PREVIEW_ACTIVE,
)
from mmd_tools.core.humanik_resolver import HumanIkBoneAssignment, HumanIkResolveResult


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
        ),
        missing_mmd_bones=(),
        unindexed_mmd_bones=(),
        duplicate_assignments=(),
    )


class FakePreview:
    def __init__(self):
        self.active = True
        self.restore_state = object()


class FakeControlRigTransaction:
    def __init__(self, character="Character_source"):
        self.active = True
        self.character = character


class FakeStance:
    """Host-neutral transaction double, matching ``test_humanik_frontend.py``."""

    def __init__(self, model_root, assignments, **_kwargs):
        self.model_root = model_root
        self.assignments = tuple(assignments)
        self.stance_evidence = {"mode": "test-automatic-stance"}
        self.active = False
        self.prepared = False
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
        self.active = False
        return {"passed": True}

    def to_dict(self):
        return {
            "modelRoot": self.model_root,
            "active": self.active,
            "prepared": self.prepared,
            "stanceEvidence": dict(self.stance_evidence),
        }


def _session(cmds_module=None):
    return HumanIkFrontendSession(cmds_module=cmds_module, stance_transaction_factory=FakeStance)


class FakeSceneCmds:
    """Minimal ``cmds`` double for the orphaned-Control-Rig scan.

    Only the two calls ``_describe_orphaned_control_rigs`` makes:
    ``ls(type="HIKControlSetNode")`` and ``listConnections(node,
    type="HIKCharacterNode")``.
    """

    def __init__(self, control_set_nodes=(), character_by_node=None, raise_on_ls=False):
        self.control_set_nodes = list(control_set_nodes)
        self.character_by_node = dict(character_by_node or {})
        self.raise_on_ls = raise_on_ls

    def ls(self, type=None):
        if self.raise_on_ls:
            raise RuntimeError("HIK plugin not loaded")
        if type == "HIKControlSetNode":
            return list(self.control_set_nodes)
        return []

    def listConnections(self, node, type=None):
        if type == "HIKCharacterNode":
            character = self.character_by_node.get(node)
            return [character] if character else []
        return []


def _characterize(session, model_root, character):
    with patch(
        "mmd_tools.core.humanik_frontend.resolve_scene_humanik_assignments",
        return_value=_result(),
    ), patch(
        "mmd_tools.core.humanik_frontend.create_humanik_definition",
        return_value=character,
    ), patch("mmd_tools.core.humanik_frontend.lock_humanik_definition"):
        return session.setup_and_characterize(model_root)


class TestDescribeFrontendStateNeutral(unittest.TestCase):
    def test_neutral_state_requires_model_and_reports_no_op_actions(self):
        session = _session()

        state = session.describe_frontend_state()

        self.assertEqual(state["mode"], FRONTEND_MODE_NEUTRAL)
        self.assertIsNone(state["source"])
        self.assertIsNone(state["target"])
        self.assertFalse(state["previewActive"])
        self.assertEqual(state["controlRigs"], [])
        self.assertNotIn("importLock", state)
        for action in ("setup_and_characterize", "enter_source_mode", "enter_target_mode", "create_control_rig"):
            self.assertFalse(state["actions"][action]["allowed"], action)
            self.assertEqual(state["actions"][action]["reasonCode"], REASON_MODEL_REQUIRED, action)
        self.assertFalse(state["actions"]["bake_to_mmd_rig"]["allowed"])
        self.assertEqual(state["actions"]["bake_to_mmd_rig"]["reasonCode"], REASON_NO_ACTIVE_PREVIEW)
        self.assertFalse(state["actions"]["restore_mmd_rig"]["allowed"])
        self.assertEqual(state["actions"]["restore_mmd_rig"]["reasonCode"], REASON_NOTHING_TO_RESTORE)
        self.assertTrue(state["actions"]["diagnostics"]["allowed"])

    def test_state_is_json_safe(self):
        session = _session()
        state = session.describe_frontend_state("|source")

        json.dumps(state)  # must not raise

    def test_enter_source_mode_uncharacterized_model_reports_not_characterized(self):
        session = _session()

        state = session.describe_frontend_state("|source")

        self.assertEqual(
            state["actions"]["enter_source_mode"]["reasonCode"], REASON_NOT_CHARACTERIZED
        )
        with self.assertRaisesRegex(RuntimeError, "not characterized"):
            session.enter_source_mode("|source")

    def test_enter_target_mode_without_source_reports_no_source(self):
        session = _session()
        _characterize(session, "|target", "Character_target")

        state = session.describe_frontend_state("|target")

        self.assertEqual(state["actions"]["enter_target_mode"]["reasonCode"], REASON_NO_SOURCE)
        with self.assertRaisesRegex(RuntimeError, "before target mode"):
            session.enter_target_mode("|target")


class TestDescribeFrontendStateSource(unittest.TestCase):
    def test_source_mode_reports_source_binding_and_allows_target_entry(self):
        session = _session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")

        state = session.describe_frontend_state("|target")

        self.assertEqual(state["mode"], FRONTEND_MODE_SOURCE)
        self.assertEqual(
            state["source"],
            {"modelRoot": "|source", "character": "Character_source", "external": False},
        )
        self.assertIsNone(state["target"])
        self.assertTrue(state["actions"]["enter_target_mode"]["allowed"])

    def test_create_control_rig_on_source_model_reports_model_is_source(self):
        session = _session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")

        state = session.describe_frontend_state("|source")

        self.assertEqual(
            state["actions"]["create_control_rig"]["reasonCode"], REASON_MODEL_IS_SOURCE
        )
        with self.assertRaisesRegex(RuntimeError, "active HumanIK SOURCE"):
            session.create_control_rig("|source")

    @patch("mmd_tools.core.humanik_frontend.begin_humanik_control_rig", return_value=FakeControlRigTransaction("Character_target"))
    def test_create_control_rig_on_target_model_is_allowed_and_succeeds(self, begin):
        session = _session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")

        state = session.describe_frontend_state("|target")
        self.assertTrue(state["actions"]["create_control_rig"]["allowed"])

        self.assertTrue(session.create_control_rig("|target"))
        begin.assert_called_once()


class TestDescribeFrontendStatePreview(unittest.TestCase):
    def _enter_preview(self, session):
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")
        with patch(
            "mmd_tools.core.humanik_frontend.collect_hik_ownership_report",
            return_value={"rows": [], "counts": {}},
        ), patch(
            "mmd_tools.core.humanik_frontend.begin_humanik_target_preview",
            return_value=FakePreview(),
        ):
            session.enter_target_mode("|target")

    def test_preview_active_blocks_mutating_actions_with_preview_active_reason(self):
        session = _session()
        self._enter_preview(session)

        state = session.describe_frontend_state("|other")

        self.assertEqual(state["mode"], FRONTEND_MODE_TARGET_PREVIEW)
        self.assertTrue(state["previewActive"])
        self.assertEqual(state["target"], {"modelRoot": "|target", "character": "Character_target"})
        for action in ("setup_and_characterize", "enter_source_mode", "create_control_rig"):
            self.assertFalse(state["actions"][action]["allowed"], action)
            self.assertEqual(state["actions"][action]["reasonCode"], REASON_PREVIEW_ACTIVE, action)
        self.assertTrue(state["actions"]["bake_to_mmd_rig"]["allowed"])
        self.assertTrue(state["actions"]["restore_mmd_rig"]["allowed"])

        with self.assertRaisesRegex(RuntimeError, "active"):
            session.setup_and_characterize("|other")
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.enter_source_mode("|source")
        with self.assertRaisesRegex(RuntimeError, "active"):
            session.create_control_rig("|source")

    def test_preview_active_for_different_model_blocks_enter_target_mode(self):
        session = _session()
        _characterize(session, "|other", "Character_other")
        self._enter_preview(session)

        state = session.describe_frontend_state("|other")

        self.assertEqual(state["actions"]["enter_target_mode"]["reasonCode"], REASON_PREVIEW_ACTIVE)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            session.enter_target_mode("|other")

    def test_bake_allowed_prediction_agrees_with_real_bake(self):
        session = _session()
        self._enter_preview(session)

        state = session.describe_frontend_state()
        self.assertTrue(state["actions"]["bake_to_mmd_rig"]["allowed"])

        from mmd_tools.core.humanik_bake import HumanIkBakeResult

        fake_bake = HumanIkBakeResult(0, 1, 2, {}, 0.0, [])
        with patch(
            "mmd_tools.core.humanik_frontend.bake_humanik_target_preview",
            return_value=fake_bake,
        ):
            session.bake_to_mmd_rig(0, 1)  # must not raise


class TestDescribeFrontendStateControlRig(unittest.TestCase):
    @patch("mmd_tools.core.humanik_frontend.begin_humanik_control_rig", return_value=FakeControlRigTransaction())
    def test_control_rig_mode_reported_when_no_preview_is_active(self, begin):
        session = _session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")
        session.create_control_rig("|target")

        state = session.describe_frontend_state()

        self.assertEqual(state["mode"], FRONTEND_MODE_CONTROL_RIG)
        self.assertEqual(
            state["controlRigs"], [{"modelRoot": "|target", "character": "Character_target"}]
        )
        self.assertTrue(state["actions"]["restore_mmd_rig"]["allowed"])

    @patch("mmd_tools.core.humanik_frontend.stop_humanik_control_rig")
    @patch("mmd_tools.core.humanik_frontend.begin_humanik_control_rig", return_value=FakeControlRigTransaction())
    def test_restore_allowed_prediction_agrees_with_real_restore(self, begin, stop):
        session = _session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")
        session.create_control_rig("|target")
        transaction = session._control_rig_transactions["|target"]

        def deactivate(*_args, **_kwargs):
            transaction.active = False

        stop.side_effect = deactivate

        state = session.describe_frontend_state()
        self.assertTrue(state["actions"]["restore_mmd_rig"]["allowed"])

        self.assertTrue(session.restore_mmd_rig())  # must not raise, and reports work done

        # SOURCE mode selection (enter_source_mode) is independent of the
        # control rig transaction that was just restored; the session
        # remains in SOURCE mode until the caller leaves it explicitly.
        state_after = session.describe_frontend_state()
        self.assertEqual(state_after["mode"], FRONTEND_MODE_SOURCE)
        self.assertFalse(state_after["actions"]["restore_mmd_rig"]["allowed"])
        self.assertFalse(session.restore_mmd_rig())


class TestDescribeFrontendStateImportLock(unittest.TestCase):
    def test_import_lock_omitted_without_model_root(self):
        session = _session()

        state = session.describe_frontend_state()

        self.assertNotIn("importLock", state)

    def test_import_lock_maps_target_preview_block(self):
        session = _session()

        class Lock:
            blocked = "target_preview"
            character = "Character_target"
            has_control_rig = False

        with patch(
            "mmd_tools.core.humanik_frontend.describe_humanik_import_lock", return_value=Lock()
        ):
            state = session.describe_frontend_state("|target")

        self.assertEqual(
            state["importLock"],
            {
                "blocked": True,
                "reasonCode": "import_blocked_target_preview",
                "character": "Character_target",
                "hasControlRig": False,
            },
        )

    def test_import_lock_maps_control_rig_block(self):
        session = _session()

        class Lock:
            blocked = "control_rig"
            character = "Character_target"
            has_control_rig = True

        with patch(
            "mmd_tools.core.humanik_frontend.describe_humanik_import_lock", return_value=Lock()
        ):
            state = session.describe_frontend_state("|target")

        self.assertEqual(state["importLock"]["reasonCode"], "import_blocked_control_rig")
        self.assertTrue(state["importLock"]["hasControlRig"])

    def test_import_lock_query_failure_reports_unblocked_default(self):
        session = _session()

        with patch(
            "mmd_tools.core.humanik_frontend.describe_humanik_import_lock",
            side_effect=RuntimeError("no maya"),
        ):
            state = session.describe_frontend_state("|target")

        self.assertEqual(
            state["importLock"],
            {"blocked": False, "reasonCode": None, "character": None, "hasControlRig": False},
        )


class TestDescribeFrontendStateOrphanedControlRigs(unittest.TestCase):
    """HUMANIK-RESTORE-GAPS-1 fix 1b: surface Control Rigs this session
    cannot tear down (scene reopen, or created outside ``create_control_rig``)
    instead of ``restore_mmd_rig`` silently no-oping for them."""

    def test_no_control_set_nodes_reports_empty_list(self):
        session = _session(cmds_module=FakeSceneCmds(control_set_nodes=[]))

        state = session.describe_frontend_state()

        self.assertEqual(state["restoreHint"]["orphanedControlRigs"], [])

    def test_untracked_control_set_node_is_reported_as_orphaned(self):
        cmds = FakeSceneCmds(
            control_set_nodes=["Stray_ControlRig"],
            character_by_node={"Stray_ControlRig": "Character_target"},
        )
        session = _session(cmds_module=cmds)
        _characterize(session, "|target", "Character_target")

        state = session.describe_frontend_state()

        self.assertEqual(
            state["restoreHint"]["orphanedControlRigs"],
            [
                {
                    "controlSetNode": "Stray_ControlRig",
                    "character": "Character_target",
                    "modelRoot": "|target",
                }
            ],
        )

    def test_orphaned_node_with_unknown_character_reports_none_model_root(self):
        cmds = FakeSceneCmds(control_set_nodes=["Stray_ControlRig"], character_by_node={})
        session = _session(cmds_module=cmds)

        state = session.describe_frontend_state()

        self.assertEqual(
            state["restoreHint"]["orphanedControlRigs"],
            [{"controlSetNode": "Stray_ControlRig", "character": None, "modelRoot": None}],
        )

    @patch(
        "mmd_tools.core.humanik_frontend.begin_humanik_control_rig",
        return_value=FakeControlRigTransaction("Character_target"),
    )
    def test_control_set_node_owned_by_active_transaction_is_excluded(self, begin):
        cmds = FakeSceneCmds(
            control_set_nodes=["Tracked_ControlRig"],
            character_by_node={"Tracked_ControlRig": "Character_target"},
        )
        # The real HumanIkControlRigTransaction records the nodes
        # hikCreateControlRig() created; simulate that here so the tracked
        # transaction "owns" the scene node the fake scan reports.
        FakeControlRigTransaction.created_nodes = ["Tracked_ControlRig"]
        try:
            session = _session(cmds_module=cmds)
            _characterize(session, "|source", "Character_source")
            session.enter_source_mode("|source")
            _characterize(session, "|target", "Character_target")
            session.create_control_rig("|target")

            state = session.describe_frontend_state()

            self.assertEqual(state["restoreHint"]["orphanedControlRigs"], [])
        finally:
            del FakeControlRigTransaction.created_nodes

    def test_query_failure_fails_soft_to_empty_list(self):
        session = _session(cmds_module=FakeSceneCmds(raise_on_ls=True))

        state = session.describe_frontend_state()

        self.assertEqual(state["restoreHint"]["orphanedControlRigs"], [])


class TestRestoreMmdRigOrphanRecovery(unittest.TestCase):
    """HUMANIK-RESTORE-GAPS-1 slice 1c: ``restore_mmd_rig``'s best-effort
    scene-facts recovery pass for a Control Rig this session has no
    ``HumanIkControlRigTransaction`` for (scene reopen, or a raw
    ``hikCreateControlRig()``/Maya standard UI Control Rig)."""

    @staticmethod
    def _session(cmds_module):
        return HumanIkFrontendSession(
            cmds_module=cmds_module,
            mel_module=object(),
            stance_transaction_factory=FakeStance,
        )

    @patch("mmd_tools.core.humanik_frontend.delete_orphaned_control_rig")
    @patch("mmd_tools.core.humanik_frontend.find_humanik_character_for_model")
    @patch("mmd_tools.core.humanik_frontend.SceneModelService")
    def test_mmd_driven_orphan_is_recovered(self, scene_service_cls, find_character, delete_rig):
        scene_service_cls.return_value.list_mmd_models.return_value = ["|target"]
        find_character.side_effect = (
            lambda model_root, cmds_module=None: "Character_target" if model_root == "|target" else None
        )
        cmds = FakeSceneCmds(
            control_set_nodes=["Stray_ControlRig"],
            character_by_node={"Stray_ControlRig": "Character_target"},
        )
        session = self._session(cmds)

        restored = session.restore_mmd_rig()

        self.assertTrue(restored)
        delete_rig.assert_called_once_with("Character_target", cmds_module=cmds, mel_module=ANY)
        report = session.describe_last_orphan_recovery()
        self.assertEqual(report["skipped"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual(len(report["recovered"]), 1)
        recovered = report["recovered"][0]
        self.assertEqual(recovered["controlSetNode"], "Stray_ControlRig")
        self.assertEqual(recovered["character"], "Character_target")
        self.assertEqual(recovered["modelRoot"], "|target")
        self.assertTrue(recovered["unrecoverableWarnings"])
        self.assertTrue(
            any("restore_state_unavailable" in warning for warning in recovered["unrecoverableWarnings"])
        )
        # Same report is surfaced through describe_frontend_state for UI consumption.
        state = session.describe_frontend_state()
        self.assertEqual(state["restoreHint"]["lastOrphanRecovery"], report)

    @patch("mmd_tools.core.humanik_frontend.delete_orphaned_control_rig")
    @patch("mmd_tools.core.humanik_frontend.find_humanik_character_for_model")
    @patch("mmd_tools.core.humanik_frontend.SceneModelService")
    def test_non_mmd_character_is_never_deleted(self, scene_service_cls, find_character, delete_rig):
        # No MMD model root in the scene resolves to this character: it is
        # not MMD-driven, so the Control Rig must be left completely alone.
        scene_service_cls.return_value.list_mmd_models.return_value = ["|target"]
        find_character.return_value = None
        cmds = FakeSceneCmds(
            control_set_nodes=["Unrelated_ControlRig"],
            character_by_node={"Unrelated_ControlRig": "Character_unrelated"},
        )
        session = self._session(cmds)

        restored = session.restore_mmd_rig()

        self.assertFalse(restored)
        delete_rig.assert_not_called()
        report = session.describe_last_orphan_recovery()
        self.assertEqual(report["recovered"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual(len(report["skipped"]), 1)
        skipped = report["skipped"][0]
        self.assertEqual(skipped["controlSetNode"], "Unrelated_ControlRig")
        self.assertEqual(skipped["character"], "Character_unrelated")
        self.assertEqual(skipped["skippedReason"], "not_mmd_driven")

    def test_orphan_with_unknown_character_is_skipped_without_scene_scan(self):
        cmds = FakeSceneCmds(control_set_nodes=["Stray_ControlRig"], character_by_node={})
        session = self._session(cmds)

        with patch("mmd_tools.core.humanik_frontend.SceneModelService") as scene_service_cls, patch(
            "mmd_tools.core.humanik_frontend.delete_orphaned_control_rig"
        ) as delete_rig:
            restored = session.restore_mmd_rig()

        self.assertFalse(restored)
        delete_rig.assert_not_called()
        scene_service_cls.assert_not_called()
        report = session.describe_last_orphan_recovery()
        self.assertEqual(report["skipped"], [{"controlSetNode": "Stray_ControlRig", "character": None, "skippedReason": "unknown_character"}])

    @patch(
        "mmd_tools.core.humanik_frontend.delete_orphaned_control_rig",
        side_effect=RuntimeError("HIKCharacterControlsTool not available in batch mode"),
    )
    @patch("mmd_tools.core.humanik_frontend.find_humanik_character_for_model")
    @patch("mmd_tools.core.humanik_frontend.SceneModelService")
    def test_mel_delete_failure_is_fail_soft_not_raised(self, scene_service_cls, find_character, delete_rig):
        scene_service_cls.return_value.list_mmd_models.return_value = ["|target"]
        find_character.return_value = "Character_target"
        cmds = FakeSceneCmds(
            control_set_nodes=["Stray_ControlRig"],
            character_by_node={"Stray_ControlRig": "Character_target"},
        )
        session = self._session(cmds)

        restored = session.restore_mmd_rig()  # must not raise

        self.assertFalse(restored)
        report = session.describe_last_orphan_recovery()
        self.assertEqual(report["recovered"], [])
        self.assertEqual(len(report["failed"]), 1)
        failed = report["failed"][0]
        self.assertEqual(failed["controlSetNode"], "Stray_ControlRig")
        self.assertEqual(failed["modelRoot"], "|target")
        self.assertIn("batch mode", failed["error"])

    def test_no_orphans_leaves_tracked_teardown_return_value_unchanged(self):
        """Existing behavior (HUMANIK-RESTORE-GAPS-1 1a/1b): nothing to
        restore still returns False, and the orphan pass is a pure no-op."""
        cmds = FakeSceneCmds(control_set_nodes=[])
        session = self._session(cmds)

        self.assertFalse(session.restore_mmd_rig())
        self.assertEqual(
            session.describe_last_orphan_recovery(),
            {"recovered": [], "skipped": [], "failed": []},
        )

    @patch(
        "mmd_tools.core.humanik_frontend.begin_humanik_control_rig",
        return_value=FakeControlRigTransaction("Character_target"),
    )
    def test_tracked_transaction_teardown_still_wins_over_orphan_pass(self, begin):
        """A Control Rig this session *does* track must still be torn down
        through the normal ``stop_humanik_control_rig`` path exactly once,
        with the new orphan-recovery pass never also deleting it -- even
        though ``stop_humanik_control_rig`` is stubbed here (so the fake
        scene's ``HIKControlSetNode`` is not actually removed) and the
        transaction is popped as soon as the stub "succeeds", which would
        otherwise make the node look newly-unowned to
        ``_describe_orphaned_control_rigs`` on the very same
        ``restore_mmd_rig`` call.
        """
        FakeControlRigTransaction.created_nodes = ["Tracked_ControlRig"]
        try:
            cmds = FakeSceneCmds(
                control_set_nodes=["Tracked_ControlRig"],
                character_by_node={"Tracked_ControlRig": "Character_target"},
            )
            session = self._session(cmds)
            _characterize(session, "|source", "Character_source")
            session.enter_source_mode("|source")
            _characterize(session, "|target", "Character_target")
            session.create_control_rig("|target")

            with patch(
                "mmd_tools.core.humanik_frontend.stop_humanik_control_rig"
            ) as stop_control_rig, patch(
                "mmd_tools.core.humanik_frontend.delete_orphaned_control_rig"
            ) as delete_rig, patch(
                "mmd_tools.core.humanik_frontend.SceneModelService"
            ) as scene_service_cls:
                scene_service_cls.return_value.list_mmd_models.return_value = []
                restored = session.restore_mmd_rig()

            self.assertTrue(restored)
            stop_control_rig.assert_called_once()
            delete_rig.assert_not_called()
            self.assertEqual(
                session.describe_last_orphan_recovery()["skipped"],
                [
                    {
                        "controlSetNode": "Tracked_ControlRig",
                        "character": "Character_target",
                        "skippedReason": "not_mmd_driven",
                    }
                ],
            )
        finally:
            del FakeControlRigTransaction.created_nodes


class FakeHikSceneCmds(FakeSceneCmds):
    """Extends the orphaned-Control-Rig scene double with the
    ``ls(type="HIKCharacterNode")`` call ``enter_external_source_mode`` makes.
    """

    def __init__(self, hik_characters=(), **kwargs):
        super().__init__(**kwargs)
        self.hik_characters = list(hik_characters)

    def ls(self, type=None):
        if type == "HIKCharacterNode":
            return list(self.hik_characters)
        return super().ls(type=type)


class TestDescribeFrontendStateExternalSource(unittest.TestCase):
    """HUMANIK-EXTERNAL-SOURCE-1 ES-1: ``describe_frontend_state`` reporting
    for a SOURCE selected via ``enter_external_source_mode`` rather than a
    characterized MMD binding."""

    @staticmethod
    def _session(characters=("Character_mocap",)):
        return HumanIkFrontendSession(
            cmds_module=FakeHikSceneCmds(hik_characters=characters),
            stance_transaction_factory=FakeStance,
        )

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_external_source_reports_source_mode_with_external_flag(self, lock_state):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")

        state = session.describe_frontend_state()

        self.assertEqual(state["mode"], FRONTEND_MODE_SOURCE)
        self.assertEqual(
            state["source"],
            {"modelRoot": None, "character": "Character_mocap", "external": True},
        )

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_external_source_allows_enter_target_mode_without_no_source_reason(self, lock_state):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")
        _characterize(session, "|target", "Character_target")

        state = session.describe_frontend_state("|target")

        self.assertTrue(state["actions"]["enter_target_mode"]["allowed"])
        self.assertNotEqual(
            state["actions"]["enter_target_mode"]["reasonCode"], REASON_NO_SOURCE
        )

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_enter_target_mode_blocked_when_target_character_matches_external_source(
        self, lock_state
    ):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")
        _characterize(session, "|target", "Character_mocap")

        state = session.describe_frontend_state("|target")

        self.assertFalse(state["actions"]["enter_target_mode"]["allowed"])
        with self.assertRaisesRegex(ValueError, "must differ"):
            session.enter_target_mode("|target")

    @patch("mmd_tools.core.humanik_frontend.get_humanik_definition_lock_state", return_value=True)
    def test_restore_mmd_rig_allowed_and_clears_external_source(self, lock_state):
        session = self._session()
        session.enter_external_source_mode("Character_mocap")

        state = session.describe_frontend_state()
        self.assertTrue(state["actions"]["restore_mmd_rig"]["allowed"])

        self.assertTrue(session.restore_mmd_rig())

        state_after = session.describe_frontend_state()
        self.assertEqual(state_after["mode"], FRONTEND_MODE_NEUTRAL)
        self.assertIsNone(state_after["source"])
        self.assertFalse(state_after["actions"]["restore_mmd_rig"]["allowed"])

    def test_active_preview_blocks_enter_external_source_mode(self):
        session = self._session()
        _characterize(session, "|source", "Character_source")
        session.enter_source_mode("|source")
        _characterize(session, "|target", "Character_target")
        with patch(
            "mmd_tools.core.humanik_frontend.collect_hik_ownership_report",
            return_value={"rows": [], "counts": {}},
        ), patch(
            "mmd_tools.core.humanik_frontend.begin_humanik_target_preview",
            return_value=FakePreview(),
        ):
            session.enter_target_mode("|target")

        state = session.describe_frontend_state()

        self.assertFalse(state["actions"]["enter_external_source_mode"]["allowed"])
        self.assertEqual(
            state["actions"]["enter_external_source_mode"]["reasonCode"], REASON_PREVIEW_ACTIVE
        )


if __name__ == "__main__":
    unittest.main()
