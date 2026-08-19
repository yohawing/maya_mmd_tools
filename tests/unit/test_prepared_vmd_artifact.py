"""Unit contracts for private verified Mode C VMD stages."""

from pathlib import Path
import unittest

from mmd_tools.actions.prepared_vmd_artifact import (
    PreparedVmdArtifactError,
    PreparedVmdStageSession,
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


class _StreamingVerifier:
    def __init__(self, blocking=False):
        self.blocking = blocking
        self.calls = []

    def __call__(self, file_path, mode, **kwargs):
        self.calls.append((file_path, mode, kwargs))
        if self.blocking:
            issue = ExportValidationIssue("OUTPUT_PARSE_FAILED", "fatal", True, "output", "bad")
            return ExportValidationReport("vmd", (issue,), mode=mode)
        if kwargs.get("raw_loss_warning_required") and not kwargs.get("ack_warnings"):
            issue = ExportValidationIssue(
                "VMD_MODE_C_RAW_LOSS", "warning", False, "mode", "review"
            )
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

    def test_incremental_session_promotes_stream_summary_without_vmd_data(self):
        verifier = _StreamingVerifier()
        with PreparedVmdStageSession(
            "モデル", output_verifier=verifier, raw_loss_warning_required=True
        ) as session:
            session.write_frame(
                "bones",
                {
                    "bone_name": "センター",
                    "frame": 4,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            )
            session.write_frame("morphs", {"morph_name": "笑い", "frame": 8, "value": 0.25})
            receipt = session.finish()

        self.assertTrue(receipt.validate_identity())
        self.assertEqual(receipt.section_counts["bone_frames"], 1)
        self.assertEqual(receipt.section_counts["morph_frames"], 1)
        self.assertEqual(receipt.frame_bounds, (4, 8))
        self.assertTrue(receipt.output_validation_report.requires_warning_ack)
        self.assertEqual(verifier.calls[0][2]["expected_counts"]["bones"], 1)
        self.assertEqual(verifier.calls[0][2]["expected_size"], receipt.size)
        self.assertEqual(verifier.calls[0][2]["expected_sha256"], receipt.sha256)
        self.assertFalse(verifier.calls[0][2]["ack_warnings"])
        stage_directory = Path(receipt.stage_directory)
        self.assertTrue(stage_directory.is_dir())
        self.assertTrue(receipt.cleanup())
        self.assertFalse(stage_directory.exists())
        self.assertFalse(receipt.cleanup())

    def test_incremental_session_verification_failure_removes_stage(self):
        verifier = _StreamingVerifier(blocking=True)
        session = PreparedVmdStageSession(output_verifier=verifier)
        stage_directory = Path(session.stage_directory)
        session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})

        with self.assertRaisesRegex(PreparedVmdArtifactError, "verification blocked"):
            session.finish()

        self.assertFalse(stage_directory.exists())
        self.assertFalse(session.cleanup())

    def test_incremental_session_default_verifier_parses_semantics_and_identity(self):
        with PreparedVmdStageSession("モデル") as session:
            session.write_frame(
                "bones",
                {
                    "bone_name": "センター",
                    "frame": 4,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            )
            receipt = session.finish()

        self.assertTrue(receipt.output_validation_report.valid)
        self.assertTrue(receipt.validate_identity())
        self.assertEqual(receipt.section_counts["bone_frames"], 1)
        receipt.cleanup()

    def test_incremental_session_keyboard_interrupt_during_write_is_preserved(self):
        session = PreparedVmdStageSession()
        stage_directory = Path(session.stage_directory)
        writer = session._writer
        original = writer.write_frame

        def cancel(*args, **kwargs):
            raise KeyboardInterrupt()

        writer.write_frame = cancel
        try:
            with self.assertRaises(KeyboardInterrupt):
                session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})
        finally:
            writer.write_frame = original
        self.assertFalse(stage_directory.exists())

    def test_incremental_session_keyboard_interrupt_during_finish_is_preserved(self):
        session = PreparedVmdStageSession()
        stage_directory = Path(session.stage_directory)
        writer = session._writer
        original = writer.finish

        def cancel(*args, **kwargs):
            raise KeyboardInterrupt()

        writer.finish = cancel
        try:
            with self.assertRaises(KeyboardInterrupt):
                session.finish()
        finally:
            writer.finish = original
        self.assertFalse(stage_directory.exists())

    def test_incremental_session_unfinished_context_removes_stage(self):
        stage_directory = None
        with PreparedVmdStageSession() as session:
            stage_directory = Path(session.stage_directory)
            session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})
            self.assertTrue(stage_directory.exists())
        self.assertFalse(stage_directory.exists())

    def test_incremental_session_promotion_verifier_error_removes_stage(self):
        def verifier_error(*_args, **_kwargs):
            raise RuntimeError("verification failed")

        session = PreparedVmdStageSession(output_verifier=verifier_error)
        stage_directory = Path(session.stage_directory)
        session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})
        session.finish_collection()

        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            session.promote()

        self.assertFalse(stage_directory.exists())

    def test_incremental_session_tamper_before_promotion_removes_stage(self):
        session = PreparedVmdStageSession()
        stage_directory = Path(session.stage_directory)
        session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})
        session.finish_collection()
        path = Path(session.file_path)
        path.write_bytes(path.read_bytes() + b"tamper")

        with self.assertRaisesRegex(PreparedVmdArtifactError, "changed"):
            session.promote()

        self.assertFalse(stage_directory.exists())


if __name__ == "__main__":
    unittest.main()
