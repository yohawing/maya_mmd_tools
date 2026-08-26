"""Focused tests for current-pose VPD export and its atomic boundary."""

from pathlib import Path
import tempfile
import unittest

from mmd_tools.actions.export_vpd_action import (
    ExportVpdAction,
    ExportVpdRequest,
)
from mmd_tools.core.vpd_data import VpdData
from mmd_tools.core.vpd_data.bone_pose import BonePose


def _pose(name="センター", index=0):
    pose = BonePose()
    pose.bone_name = name
    pose.bone_index = index
    pose.position = [1.0, 2.0, -3.0]
    pose.quaternion = [0.0, 0.0, 0.0, 1.0]
    return pose


class TestExportVpdAction(unittest.TestCase):
    """Verify VPD write/parse/replace and fail-closed payload checks."""

    def test_empty_pose_is_blocked_without_touching_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.vpd"
            target.write_bytes(b"keep me")
            result = ExportVpdAction(collector=lambda _options: VpdData()).execute(
                ExportVpdRequest(str(target), {"export_format": "vpd"})
            )

            self.assertFalse(result.succeeded)
            self.assertTrue(result.validation_report.is_blocking)
            self.assertIn(
                "INPUT_INVALID",
                [issue.code for issue in result.validation_report.issues],
            )
            self.assertEqual(target.read_bytes(), b"keep me")

    def test_current_pose_roundtrip_preserves_mmd_name_values(self):
        data = VpdData()
        data.bone_poses.append(_pose())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pose.vpd"
            result = ExportVpdAction(collector=lambda _options: data).execute(
                ExportVpdRequest(str(target), {"export_format": "vpd"})
            )

            self.assertTrue(result.succeeded)
            parsed = VpdData()
            parsed.parse_file(str(target))
            self.assertEqual(parsed.bone_poses[0].bone_name, "センター")
            self.assertEqual(parsed.bone_poses[0].position, [1.0, 2.0, -3.0])
            self.assertEqual(parsed.bone_poses[0].quaternion, [0.0, 0.0, 0.0, 1.0])

    def test_unrepresentable_name_blocks_without_touching_existing_target(self):
        data = VpdData()
        data.bone_poses.append(_pose("😀"))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.vpd"
            target.write_bytes(b"keep me")
            result = ExportVpdAction(collector=lambda _options: data).execute(
                ExportVpdRequest(str(target), {"export_format": "vpd"})
            )

            self.assertFalse(result.succeeded)
            self.assertTrue(result.validation_report.is_blocking)
            self.assertEqual(target.read_bytes(), b"keep me")

    def test_writer_failure_keeps_existing_target_and_cleans_temporary_file(self):
        class FailingData(VpdData):
            def write_file(self, _path):
                raise IOError("writer failed")

        target_data = FailingData()
        target_data.bone_poses.append(_pose())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.vpd"
            target.write_bytes(b"keep me")
            result = ExportVpdAction(collector=lambda _options: target_data).execute(
                ExportVpdRequest(str(target), {"export_format": "vpd"})
            )

            self.assertFalse(result.succeeded)
            self.assertIn(
                "OUTPUT_WRITE_FAILED",
                [issue.code for issue in result.validation_report.issues],
            )
            self.assertEqual(target.read_bytes(), b"keep me")
            self.assertEqual(list(Path(directory).glob(".*.vpd")), [])

    def test_phase_callback_reports_collection_writer_and_cleanup_boundaries(self):
        phases = []
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pose.vpd"
            data = VpdData()
            data.bone_poses.append(_pose())
            result = ExportVpdAction(collector=lambda _options: data).execute(
                ExportVpdRequest(
                    str(target),
                    {"export_format": "vpd", "_phase_callback": lambda *value: phases.append(value)},
                )
            )

        self.assertTrue(result.succeeded)
        self.assertIn(("collect", True), phases)
        self.assertIn(("encode", True), phases)
        self.assertIn(("flush", True), phases)
        self.assertIn(("output_verify", True), phases)
        self.assertIn(("replace", True), phases)
        self.assertNotIn(("cleanup", True), phases)

    def test_cancel_before_writer_preserves_target_and_cleans_stage(self):
        checks = 0

        def cancel_requested():
            nonlocal checks
            checks += 1
            return checks >= 3

        data = VpdData()
        data.bone_poses.append(_pose())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "existing.vpd"
            target.write_bytes(b"keep me")
            result = ExportVpdAction(collector=lambda _options: data).execute(
                ExportVpdRequest(
                    str(target),
                    {
                        "export_format": "vpd",
                        "_cancel_requested": cancel_requested,
                    },
                )
            )

            self.assertFalse(result.succeeded)
            self.assertTrue(result.cancelled)
            self.assertIsNone(result.error)
            self.assertEqual(target.read_bytes(), b"keep me")
            self.assertEqual(list(Path(directory).glob(".*.vpd")), [])


if __name__ == "__main__":
    unittest.main()
