"""Unit contracts for private verified Bake Timeline VMD stages."""

from array import array
from pathlib import Path
import unittest
from unittest.mock import patch

from mmd_tools.actions.prepared_vmd_artifact import (
    PreparedVmdArtifactError,
    PreparedVmdStageSession,
    _VmdPartsSink,
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
        return ExportValidationReport("vmd", (), mode=mode)


class PreparedVmdArtifactTests(unittest.TestCase):
    def test_parts_sink_preserves_raw_cp932_names_interpolation_and_aliases(self):
        bone_name = "センター".encode("cp932")
        morph_name = "笑い".encode("cp932")
        interpolation = bytes(range(64))
        sink = _VmdPartsSink("モデル")
        sink.write_frame(
            "bone",
            {
                "bone_name": bone_name,
                "frame": 4,
                "position": (1.0, 2.0, 3.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
                "interpolation": interpolation,
            },
        )
        sink.write_frame("morph", {"morph_name": morph_name, "frame": 8, "value": 0.25})
        sink.write_frame(
            "properties",
            {
                "frame": 8,
                "visible": 1,
                "ik_states": [{"bone_name": bone_name, "enabled": True}],
            },
        )

        with patch(
            "mmd_tools.actions.prepared_vmd_artifact.export_vmd_from_parts",
            return_value=b"native",
        ) as export:
            self.assertEqual(sink.finish(), b"native")

        args = export.call_args.args
        metadata = args[0]
        for values, typecode in zip(args[1:9], ("I", "I", "f", "f", "B", "I", "I", "f")):
            self.assertIsInstance(values, array)
            self.assertEqual(values.typecode, typecode)
        self.assertEqual(metadata["boneNames"][0]["nameBytes"], list(bone_name))
        self.assertEqual(metadata["morphNames"][0]["nameBytes"], list(morph_name))
        self.assertEqual(metadata["propertyFrames"][0]["ikStates"][0]["boneNameBytes"], list(bone_name))
        self.assertEqual(tuple(map(list, args[1:6])), ([0], [4], [1.0, 2.0, 3.0], [0.0, 0.0, 0.0, 1.0], list(interpolation)))
        self.assertEqual(tuple(map(list, args[6:9])), ([0], [8], [0.25]))

    def test_parts_sink_rejects_nul_and_invalid_cp932_raw_names(self):
        sink = _VmdPartsSink("model")
        with self.assertRaisesRegex(PreparedVmdArtifactError, "NUL"):
            sink.write_frame("bones", {"bone_name": b"a\x00b", "frame": 0})
        with self.assertRaisesRegex(PreparedVmdArtifactError, "CP932"):
            sink.write_frame("bones", {"bone_name": b"\x82", "frame": 0})

    def test_parts_sink_preserves_legacy_byte_boundary_name_truncation(self):
        sink = _VmdPartsSink("model")
        sink.write_frame("bones", {"bone_name": "あ" * 8, "frame": 0})

        with patch(
            "mmd_tools.actions.prepared_vmd_artifact.export_vmd_from_parts",
            return_value=b"native",
        ) as export:
            sink.finish()

        name = export.call_args.args[0]["boneNames"][0]
        self.assertEqual(name["name"], "あ" * 8)
        self.assertEqual(name["nameBytes"], [])

    def test_parts_sink_can_explicitly_close_empty_leading_bone_section(self):
        sink = _VmdPartsSink("model")
        sink.begin_section("bones")
        sink.end_section()
        sink.write_frame("morphs", {"morph_name": "笑い", "frame": 2, "value": 0.25})

        with patch(
            "mmd_tools.actions.prepared_vmd_artifact.export_vmd_from_parts",
            return_value=b"native",
        ) as export:
            sink.finish()

        self.assertEqual(sink.counts["bones"], 0)
        self.assertEqual(list(export.call_args.args[6]), [0])

    def test_incremental_session_promotes_stream_summary_without_vmd_data(self):
        verifier = _StreamingVerifier()
        with PreparedVmdStageSession("モデル", output_verifier=verifier) as session:
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
        self.assertFalse(receipt.output_validation_report.requires_warning_ack)
        self.assertEqual(verifier.calls[0][2]["expected_counts"]["bones"], 1)
        self.assertEqual(verifier.calls[0][2]["expected_size"], receipt.size)
        self.assertEqual(verifier.calls[0][2]["expected_sha256"], receipt.sha256)
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
