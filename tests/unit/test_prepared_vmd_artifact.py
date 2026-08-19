"""Unit contracts for private verified Mode C VMD stages."""

from pathlib import Path
import unittest

from mmd_tools.actions.prepared_vmd_artifact import (
    PreparedVmdArtifactError,
    stage_vmd_artifact,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport


def _data() -> VmdData:
    data = VmdData()
    frame = VmdBoneFrame()
    frame.bone_name = "center"
    frame.frame_number = 4
    frame.rotation = (0.0, 0.0, 0.0, 1.0)
    data.bone_frames.append(frame)
    return data


class _Exporter:
    def __init__(self, fail=False):
        self.paths = []
        self.fail = fail

    def export_vmd_animation(self, file_path, vmd_data):
        self.paths.append(file_path)
        if self.fail:
            raise RuntimeError("writer failed")
        vmd_data.write_file(file_path)


class _Verifier:
    def __init__(self, blocking=False, warning=False):
        self.blocking = blocking
        self.warning = warning
        self.calls = []

    def __call__(self, file_path, mode, *, expected_counts):
        self.calls.append((file_path, mode, dict(expected_counts)))
        if self.blocking:
            issue = ExportValidationIssue("OUTPUT_PARSE_FAILED", "fatal", True, "output", "bad")
            return ExportValidationReport("vmd", (issue,), mode=mode)
        if self.warning:
            issue = ExportValidationIssue("OUTPUT_WARNING", "warning", False, "output", "review")
            return ExportValidationReport("vmd", (issue,), mode=mode)
        return ExportValidationReport("vmd", (), mode=mode)


class PreparedVmdArtifactTests(unittest.TestCase):
    def test_receipt_records_identity_and_cleanup(self):
        exporter = _Exporter()
        verifier = _Verifier()
        receipt = stage_vmd_artifact(
            _data(), exporter=exporter, output_verifier=verifier, mode="C"
        )

        self.assertTrue(receipt.validate_identity())
        self.assertEqual(receipt.section_counts["bone_frames"], 1)
        self.assertEqual(receipt.frame_bounds, (4, 4))
        self.assertEqual(len(receipt.sha256), 64)
        self.assertEqual(receipt.size, Path(receipt.file_path).stat().st_size)
        self.assertIsInstance(receipt.output_validation_report, ExportValidationReport)
        self.assertEqual(verifier.calls[0][2]["bone_frames"], 1)
        self.assertTrue(receipt.cleanup())
        self.assertFalse(Path(receipt.file_path).exists())

    def test_writer_and_verifier_failures_leave_no_stage(self):
        exporter = _Exporter(fail=True)
        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            stage_vmd_artifact(
                _data(), exporter=exporter, output_verifier=_Verifier(), mode="C"
            )
        self.assertFalse(Path(exporter.paths[0]).parent.exists())

        exporter = _Exporter()
        with self.assertRaises(PreparedVmdArtifactError):
            stage_vmd_artifact(
                _data(), exporter=exporter, output_verifier=_Verifier(blocking=True), mode="C"
            )
        self.assertFalse(Path(exporter.paths[0]).parent.exists())

    def test_warning_report_is_retained_on_verified_stage_without_acknowledgement(self):
        receipt = stage_vmd_artifact(
            _data(), exporter=_Exporter(), output_verifier=_Verifier(warning=True), mode="C"
        )

        self.assertTrue(receipt.output_validation_report.requires_warning_ack)
        self.assertEqual(
            [issue.code for issue in receipt.output_validation_report.issues],
            ["OUTPUT_WARNING"],
        )
        self.assertTrue(receipt.validate_identity())
        receipt.cleanup()

    def test_missing_or_tampered_stage_is_detected(self):
        receipt = stage_vmd_artifact(
            _data(), exporter=_Exporter(), output_verifier=_Verifier(), mode="C"
        )
        path = Path(receipt.file_path)
        path.write_bytes(path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(PreparedVmdArtifactError, "changed"):
            receipt.validate_identity()
        path.unlink()
        with self.assertRaisesRegex(PreparedVmdArtifactError, "missing"):
            receipt.validate_identity()
        self.assertFalse(receipt.cleanup())


if __name__ == "__main__":
    unittest.main()
