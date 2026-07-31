"""Unit tests for HumanIK/MMD-native Control Rig ownership interop."""

import unittest
from unittest.mock import patch

from mmd_tools.core.humanik_mmd_control_rig import (
    HumanIkMmdControlRigInterop,
    inspect_humanik_mmd_control_rig_interop,
    require_humanik_mmd_control_rig_interop,
)
from mmd_tools.core.humanik_control_rig import (
    HumanIkControlRigTransaction,
    begin_humanik_control_rig,
)
from mmd_tools.core.humanik_transaction import HumanIkRestoreState


class _Cmds:
    """Marker command module injected into the read-only interop helper."""

    def attributeQuery(self, _attribute, node=None, exists=False):
        return True

    def ls(self, value, uuid=False, long=False):
        if uuid:
            return ["model-uuid"]
        if long:
            return ["|model"]
        return [value]


class TestHumanIkMmdControlRigInterop(unittest.TestCase):
    def test_missing_native_rig_is_allowed_without_a_lease(self):
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=None,
        ):
            report = inspect_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())

        self.assertTrue(report.allowed)
        self.assertFalse(report.present)
        self.assertEqual(report.lease, "none")
        self.assertEqual(report.reason, "no_native_control_rig")

    def test_attached_mmd_owned_is_the_only_overlay_lease(self):
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value={
                "state": "ATTACHED",
                "owner": "MMD_OWNED",
                "modelRootUuid": "model-uuid",
            },
        ):
            report = require_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())

        self.assertTrue(report.allowed)
        self.assertEqual(report.lease, "overlay_isolation")
        self.assertEqual(report.to_dict()["owner"], "MMD_OWNED")

    def test_edit_control_owned_blocks_humanik_before_mutation(self):
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value={
                "state": "EDIT",
                "owner": "CONTROL_OWNED",
                "modelRootUuid": "model-uuid",
            },
        ):
            report = inspect_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())
            with self.assertRaisesRegex(RuntimeError, "ownership contract"):
                require_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())

        self.assertFalse(report.allowed)
        self.assertEqual(report.reason, "native_control_rig_owned")
        self.assertEqual(report.lease, "blocked")

    def test_baked_mmd_owned_still_blocks_overlay(self):
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value={
                "state": "BAKED",
                "owner": "MMD_OWNED",
                "modelRootUuid": "model-uuid",
            },
        ):
            report = inspect_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())

        self.assertFalse(report.allowed)
        self.assertEqual((report.state, report.owner), ("BAKED", "MMD_OWNED"))

    def test_metadata_read_failure_is_fail_closed_for_an_injected_scene(self):
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            side_effect=RuntimeError("stale UUID"),
        ):
            report = inspect_humanik_mmd_control_rig_interop("|model", cmds_module=_Cmds())

        self.assertFalse(report.allowed)
        self.assertTrue(report.present)
        self.assertEqual(report.reason, "metadata_invalid")
        self.assertIn("stale UUID", report.metadata_error)

    def test_snapshot_round_trip_rejects_inconsistent_allowed_state(self):
        report = HumanIkMmdControlRigInterop(
            "|model", True, True, "ATTACHED", "MMD_OWNED", "attached_mmd_owned",
            True, None, "model-uuid"
        )
        self.assertEqual(
            HumanIkMmdControlRigInterop.from_dict(report.to_dict()).to_dict(),
            report.to_dict(),
        )
        payload = report.to_dict()
        payload["owner"] = "CONTROL_OWNED"
        with self.assertRaisesRegex(ValueError, "allowed row"):
            HumanIkMmdControlRigInterop.from_dict(payload)

    def test_legacy_snapshot_without_model_root_uuid_is_rejected(self):
        payload = {
            "modelRoot": "|model",
            "present": True,
            "allowed": True,
            "state": "ATTACHED",
            "owner": "MMD_OWNED",
            "reason": "attached_mmd_owned",
            "lease": "overlay_isolation",
            "sceneAvailable": True,
            "metadataError": None,
        }
        with self.assertRaisesRegex(ValueError, "modelRootUuid is required"):
            HumanIkMmdControlRigInterop.from_dict(payload)

    def test_humanik_transaction_persists_the_native_rig_lease(self):
        lease = HumanIkMmdControlRigInterop(
            "|model", True, True, "ATTACHED", "MMD_OWNED", "attached_mmd_owned",
            True, None, "model-uuid"
        ).to_dict()
        restore_state = HumanIkRestoreState(
            "owner:rig", "Character", True, "", -1, [], [], "character-uuid"
        )
        transaction = HumanIkControlRigTransaction(
            ownership_id="owner:rig",
            character="Character",
            restore_state=restore_state,
            disconnected=[],
            retained_nodes=[],
            created_nodes=[],
            mmd_control_rig_interop=lease,
        )

        payload = transaction.to_scene_dict(
            "|model",
            model_root_uuid="model-uuid",
            character_uuid="character-uuid",
        )
        self.assertEqual(payload["mmdControlRigInterop"], lease)
        restored = HumanIkControlRigTransaction.from_scene_dict(payload)
        self.assertEqual(restored.mmd_control_rig_interop, lease)

    def test_blocked_lease_is_rejected_before_hik_scene_setup(self):
        lease = HumanIkMmdControlRigInterop(
            "|model", True, False, "EDIT", "CONTROL_OWNED", "native_control_rig_owned",
            True, None, "model-uuid"
        ).to_dict()
        with patch("mmd_tools.core.humanik_control_rig.ensure_humanik_mel_loaded") as ensure:
            with self.assertRaisesRegex(RuntimeError, "ownership contract"):
                begin_humanik_control_rig(
                    "owner:rig",
                    "Character",
                    {"|hips"},
                    object(),
                    object(),
                    mmd_control_rig_interop=lease,
                )
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
