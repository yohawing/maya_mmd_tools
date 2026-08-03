"""Test the Maya-independent ownership classification contract."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.core import model_ownership_audit  # noqa: E402


class TestModelOwnershipAudit(unittest.TestCase):
    """Keep root message classification and fail-closed reporting stable."""

    def test_classifies_standard_and_legacy_destinations(self):
        with mock.patch.object(
            model_ownership_audit,
            "_node_type",
            side_effect=lambda node: {"bindPose1": "bindPose", "solver": "mmdPhysicsSolver"}.get(node, "network"),
        ):
            self.assertEqual(
                model_ownership_audit._classify_root_message_destination("bindPose1", "members[0]"),
                ("maya_bind_pose", "standard"),
            )
            self.assertEqual(
                model_ownership_audit._classify_root_message_destination("morph", "mmd_model_root"),
                ("legacy_owner_link", "migration_required"),
            )
            self.assertEqual(
                model_ownership_audit._classify_root_message_destination("morph", "mmd_model_root[0]"),
                ("legacy_owner_link", "migration_required"),
            )
            self.assertEqual(
                model_ownership_audit._classify_root_message_destination("solver", "modelRoot"),
                ("legacy_physics_solver", "migration_required"),
            )

    def test_invalid_root_fails_closed_without_scene_queries(self):
        with mock.patch.object(model_ownership_audit, "_canonical_dag_root", return_value=None):
            report = model_ownership_audit.audit_model_root("not-a-root")

        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["findings"][0]["code"], "INVALID_MODEL_ROOT")

    def test_legacy_fanout_is_migration_required_but_not_unknown(self):
        with (
            mock.patch.object(model_ownership_audit, "_canonical_dag_root", return_value="|hero:Model_root"),
            mock.patch.object(
                model_ownership_audit,
                "_root_message_connections",
                return_value=[
                    {
                        "destination": "heroMorph.mmd_model_root",
                        "node": "heroMorph",
                        "attribute": "mmd_model_root",
                        "category": "legacy_owner_link",
                        "status": "migration_required",
                    }
                ],
            ),
            mock.patch.object(model_ownership_audit, "_legacy_owner_links", return_value=[]),
            mock.patch.object(model_ownership_audit, "get_model_registry", return_value=None),
        ):
            report = model_ownership_audit.audit_model_root("hero:Model_root")

        self.assertEqual(report["status"], "migration_required")
        self.assertEqual(report["root_message"]["legacy_fanout_count"], 1)
        self.assertNotIn("ROOT_UNKNOWN_MESSAGE_DESTINATION", {item["code"] for item in report["findings"]})

    def test_unknown_destination_fails_closed(self):
        with (
            mock.patch.object(model_ownership_audit, "_canonical_dag_root", return_value="|Model_root"),
            mock.patch.object(
                model_ownership_audit,
                "_root_message_connections",
                return_value=[
                    {
                        "destination": "foreign.message",
                        "node": "foreign",
                        "attribute": "message",
                        "category": "unknown",
                        "status": "unknown",
                    }
                ],
            ),
            mock.patch.object(model_ownership_audit, "_legacy_owner_links", return_value=[]),
            mock.patch.object(model_ownership_audit, "get_model_registry", return_value=None),
        ):
            report = model_ownership_audit.audit_model_root("Model_root")

        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["findings"][0]["code"], "ROOT_UNKNOWN_MESSAGE_DESTINATION")

    def test_scene_status_preserves_migration_required(self):
        with mock.patch.object(
            model_ownership_audit,
            "discover_model_roots",
            return_value=["|Model_root"],
        ), mock.patch.object(
            model_ownership_audit,
            "audit_model_root",
            return_value={"status": "migration_required"},
        ):
            report = model_ownership_audit.audit_scene_model_roots()

        self.assertEqual(report["status"], "migration_required")

    def test_invalid_explicit_root_is_fail_closed(self):
        self.assertEqual(
            model_ownership_audit.aggregate_model_audit_status([{"status": "invalid"}]),
            "fail",
        )

    def test_canonical_root_rejects_non_root_transform(self):
        with (
            mock.patch.object(model_ownership_audit.cmds, "objExists", return_value=True),
            mock.patch.object(model_ownership_audit.cmds, "ls", return_value=["|mesh"]),
        ):
            self.assertIsNone(model_ownership_audit._canonical_dag_root("mesh"))


if __name__ == "__main__":
    unittest.main()
