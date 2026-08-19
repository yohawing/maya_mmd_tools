"""Unit contracts for private verified Mode C VMD stages."""

from pathlib import Path
import unittest

from mmd_tools.actions.prepared_vmd_artifact import (
    PreparedVmdArtifactError,
    PreparedVmdStageSession,
)
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport


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
            session.finish_collection()
            receipt = session.promote()

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
            session.finish_collection()
            session.promote()

        self.assertFalse(stage_directory.exists())
        self.assertFalse(session.cleanup())

    def test_incremental_session_forwards_expected_frame_range(self):
        verifier = _StreamingVerifier()
        with PreparedVmdStageSession(
            output_verifier=verifier,
            expected_frame_range=(4, 8),
        ) as session:
            session.write_frame("morphs", {"morph_name": "笑い", "frame": 4, "value": 0.25})
            session.finish_collection()
            receipt = session.promote()

        self.assertEqual(verifier.calls[0][2]["expected_frame_range"], (4, 8))
        self.assertFalse(verifier.calls[0][2]["ack_warnings"])
        receipt.cleanup()

    def test_incremental_session_rejects_range_change_after_collection(self):
        session = PreparedVmdStageSession()
        session.finish_collection()

        with self.assertRaisesRegex(PreparedVmdArtifactError, "cannot change"):
            session.set_expected_frame_range((0, 1))

        session.cleanup()

    def test_incremental_session_frame_range_failure_removes_stage(self):
        session = PreparedVmdStageSession(expected_frame_range=(3, 3))
        stage_directory = Path(session.stage_directory)
        session.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})

        with self.assertRaisesRegex(PreparedVmdArtifactError, "verification blocked"):
            session.finish_collection()
            session.promote()

        self.assertFalse(stage_directory.exists())

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
            session.finish_collection()
            receipt = session.promote()

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
                session.finish_collection()
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
