"""VMD current-scene Bake Timeline validation and atomic export contracts."""

import hashlib
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.vmd_stream_writer import VmdStreamWriter
from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from mmd_tools.validation.vmd_validator import (
    VMD_EXPORT_BAKE_TIMELINE,
    validate_vmd_data,
    verify_vmd_output,
    verify_vmd_output_streaming,
)


def _valid_bone_frame() -> VmdBoneFrame:
    """Return one finite bone frame suitable for a structural fixture."""
    frame = VmdBoneFrame()
    frame.bone_name = "センター"
    frame.rotation = (0.0, 0.0, 0.0, 1.0)
    return frame


def _write_full_stream_fixture(path: Path) -> bytes:
    """Write one record in every canonical stream section and return bytes."""
    writer = VmdStreamWriter(path, "モデル")
    writer.write_bone(
        {
            "bone_name": "センター",
            "frame": 4,
            "position": (0.0, 0.0, 0.0),
            "rotation": (0.0, 0.0, 0.0, 1.0),
        }
    )
    writer.write_morph({"morph_name": "笑い", "frame": 8, "value": 0.25})
    writer.write_camera({"frame": 12})
    writer.write_light({"frame": 16})
    writer.write_shadow({"frame": 20})
    writer.write_ik({"frame": 24, "visible": True, "ik_states": [("足IK", True)]})
    writer.finish()
    return path.read_bytes()


class TestVmdValidator(unittest.TestCase):
    """VMD payload issue codes remain deterministic and fail closed."""

    def test_empty_bake_timeline_payload_is_ready(self):
        report = validate_vmd_data(VmdData(), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(report.valid)
        self.assertEqual(report.mode, VMD_EXPORT_BAKE_TIMELINE)
        self.assertEqual(report.to_dict()["status"], "ready")

    def test_invalid_bone_payload_reports_all_relevant_contracts(self):
        data = VmdData()
        frame = _valid_bone_frame()
        frame.frame_number = -1
        frame.rotation = (math.nan, 0.0, 0.0, 0.0)
        frame.interpolation = b"short"
        data.bone_frames.append(frame)

        report = validate_vmd_data(data, VMD_EXPORT_BAKE_TIMELINE, frame_range=(0, 10))
        codes = [issue.code for issue in report.issues]

        self.assertEqual(
            codes,
            [
                "VMD_FRAME_NEGATIVE",
                "VMD_NON_FINITE_NUMBER",
                "VMD_BONE_INTERPOLATION_LENGTH",
                "VMD_QUATERNION_INVALID",
                "VMD_FRAME_RANGE",
            ],
        )
        self.assertEqual(report.issues[0].path, "bone_frames[0].frame_number")

    def test_verify_output_parses_vmd_written_by_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            data = VmdData()
            data.bone_frames.append(_valid_bone_frame())
            data.write_file(path)

            report = verify_vmd_output(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(report.valid)
        self.assertEqual(report.export_format, "vmd")

    def test_verify_output_rejects_section_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            VmdData().write_file(path)

            report = verify_vmd_output(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts={"bone_frames": 1},
            )

        self.assertEqual(report.issues[0].code, "VMD_FRAME_COUNT_MISMATCH")

    def test_streaming_verifier_matches_stream_writer_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path, "モデル")
            writer.write_bone(
                {
                    "bone_name": "センター",
                    "frame": 4,
                    "position": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            )
            writer.write_morph({"morph_name": "笑い", "frame": 8, "value": 0.25})
            summary = writer.finish()

            streaming = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=summary.counts,
                expected_bounds=summary.frame_bounds,
                expected_sha256=summary.sha256,
                expected_size=summary.size,
            )
            legacy = verify_vmd_output(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertTrue(streaming.valid, streaming.issues)
        self.assertEqual(streaming.issues, legacy.issues)

    def test_streaming_verifier_rejects_noncanonical_global_frame_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            writer.write_morph({"morph_name": "笑い", "frame": 8, "value": 0.25})
            writer.finish()

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_bounds=(8, 8),
            )

        self.assertFalse(report.valid)
        self.assertIn("VMD_FRAME_RANGE", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_malformed_canonical_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            summary = writer.finish()
            incomplete_counts = dict(summary.counts)
            incomplete_counts.pop("ik")
            boolean_counts = dict(summary.counts)
            boolean_counts["bones"] = True
            mapping_bounds = dict(summary.frame_bounds)
            mapping_bounds["bones"] = {"minimum": None, "maximum": None}

            malformed = (
                {"expected_counts": incomplete_counts},
                {"expected_counts": boolean_counts},
                {"expected_counts": {"bone_frames": 0}},
                {"expected_bounds": mapping_bounds},
            )
            for metadata in malformed:
                with self.subTest(metadata=metadata):
                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        **metadata,
                    )
                    self.assertFalse(report.valid)

    def test_streaming_verifier_enforces_inclusive_frame_range_for_all_sections(self):
        section_payloads = {
            "bones": {
                "bone_name": "センター",
                "frame": 10,
                "position": (0.0, 0.0, 0.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            },
            "morphs": {"morph_name": "笑い", "frame": 10, "value": 0.25},
            "cameras": {"frame": 10},
            "lights": {"frame": 10},
            "shadows": {"frame": 10},
            "ik": {"frame": 10, "visible": True, "ik_states": [("足IK", True)]},
        }
        for section in section_payloads:
            for frame in (9, 11):
                with self.subTest(section=section, frame=frame), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "stream.vmd"
                    writer = VmdStreamWriter(path)
                    for current_section, payload in section_payloads.items():
                        current = dict(payload)
                        if current_section == section:
                            current["frame"] = frame
                        writer.write_frame(current_section, current)
                    writer.finish()

                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        expected_frame_range=(10, 10),
                    )

                self.assertFalse(report.valid)
                range_issues = [issue for issue in report.issues if issue.code == "VMD_FRAME_RANGE"]
                self.assertEqual(len(range_issues), 1)
                self.assertIn("frame_number", range_issues[0].path)

    def test_streaming_verifier_accepts_inclusive_frame_range_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            writer.write_morph({"morph_name": "笑い", "frame": 10, "value": 0.25})
            writer.finish()

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_frame_range=(10, 10),
            )

        self.assertTrue(report.valid, report.issues)

    def test_streaming_verifier_rejects_malformed_expected_frame_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            writer.finish()
            for frame_range in (
                (),
                (1,),
                (1, 2, 3),
                (2, 1),
                (-1, 1),
                (True, 1),
                (1.5, 2),
                ("1", 2),
                ("bad", 1),
                {0: 1, 1: 2},
                (0, 0x1_0000_0000),
            ):
                with self.subTest(frame_range=frame_range):
                    report = verify_vmd_output_streaming(
                        str(path),
                        VMD_EXPORT_BAKE_TIMELINE,
                        expected_frame_range=frame_range,
                    )
                    self.assertFalse(report.valid)
                    self.assertIn("VMD_FRAME_RANGE", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_truncation_in_each_section(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_full_stream_fixture(path)
            # Header/model (50), then count + record for each fixed section;
            # IK has a 9-byte fixed prefix and one 21-byte state.
            record_ends = (164, 191, 256, 288, 301, 335)
            for index, end in enumerate(record_ends):
                candidate = Path(directory) / "truncated-{}.vmd".format(index)
                candidate.write_bytes(source[:end])
                report = verify_vmd_output_streaming(str(candidate), VMD_EXPORT_BAKE_TIMELINE)
                self.assertFalse(report.valid)
                self.assertIn("OUTPUT_PARSE_FAILED", [issue.code for issue in report.issues])

    def test_streaming_verifier_requires_declared_empty_tail_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            summary = writer.finish()
            # Preserve the required bone and morph counts, but remove the four
            # empty tail counts emitted by the canonical stream writer.
            path.write_bytes(path.read_bytes()[:58])

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=summary.counts,
            )

        self.assertFalse(report.valid)
        self.assertIn("OUTPUT_PARSE_FAILED", [issue.code for issue in report.issues])

    def test_streaming_verifier_rejects_nonfinite_values_and_wire_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            source = _write_full_stream_fixture(path)
            # Offsets are stable for the one-record fixture above.
            corruptions = (
                (54 + 31, struct.pack("<f", float("nan")), "VMD_NON_FINITE_NUMBER"),
                (169 + 19, struct.pack("<f", float("inf")), "VMD_NON_FINITE_NUMBER"),
                (196 + 60, b"\x02", "VMD_PERSPECTIVE_RANGE"),
                (293 + 4, b"\x09", "VMD_SHADOW_MODE_RANGE"),
                (306 + 4, b"\x02", "VMD_IK_FLAG_RANGE"),
                (306 + 9 + 20, b"\x02", "VMD_IK_FLAG_RANGE"),
                (54, b"\x81 ", "OUTPUT_PARSE_FAILED"),
            )
            for index, (offset, payload, code) in enumerate(corruptions):
                candidate = Path(directory) / "corrupt-{}.vmd".format(index)
                data = bytearray(source)
                data[offset : offset + len(payload)] = payload
                candidate.write_bytes(data)
                report = verify_vmd_output_streaming(str(candidate), VMD_EXPORT_BAKE_TIMELINE)
                self.assertFalse(report.valid, "corruption {} produced {}".format(index, report.issues))
                self.assertIn(code, [issue.code for issue in report.issues])

    def test_streaming_verifier_reports_trailing_bytes_and_metadata_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stream.vmd"
            writer = VmdStreamWriter(path)
            summary = writer.finish()
            path.write_bytes(path.read_bytes() + b"trailing")
            expected_counts = dict(summary.counts)
            expected_counts["bones"] = 1

            report = verify_vmd_output_streaming(
                str(path),
                VMD_EXPORT_BAKE_TIMELINE,
                expected_counts=expected_counts,
                expected_sha256="0" * 64,
                expected_size=0,
            )

        codes = [issue.code for issue in report.issues]
        self.assertIn("OUTPUT_PARSE_FAILED", codes)
        self.assertIn("VMD_FRAME_COUNT_MISMATCH", codes)
        self.assertFalse(report.valid)

    def test_streaming_verifier_bounds_issue_memory_for_many_invalid_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many-invalid.vmd"
            writer = VmdStreamWriter(path)
            for frame in range(130):
                writer.write_morph({"morph_name": "", "frame": frame, "value": 0.0})
            writer.finish()

            report = verify_vmd_output_streaming(str(path), VMD_EXPORT_BAKE_TIMELINE)

        self.assertFalse(report.valid)
        self.assertLessEqual(len(report.issues), 100)


class _WritingVmdExporter:
    """Small writer spy that emits a parseable empty VMD file."""

    def __init__(self, *, invalid=False):
        self.calls = []
        self.invalid = invalid

    def to_vmd_data(self, animation_data):
        return animation_data

    def export_vmd_animation(self, file_path, animation_data):
        self.calls.append((file_path, animation_data))
        if self.invalid:
            Path(file_path).write_bytes(b"not a vmd")
        else:
            VmdData().write_file(file_path)


class _TransformingVmdExporter(_WritingVmdExporter):
    """Return a distinct normalized snapshot so the writer input is observable."""

    def to_vmd_data(self, animation_data):
        transformed = VmdData()
        transformed.header.model_name = f"validated:{animation_data['model_name']}"
        self.transformed = transformed
        return transformed


class TestExportVmdValidationGate(unittest.TestCase):
    """The action protects the writer boundary and existing target file."""

    def test_bake_timeline_validator_receives_vmd_range_converted_from_maya_time(self):
        observed = []

        def validator(_data, export_strategy, *, frame_range=None):
            observed.append((export_strategy, frame_range))
            return ExportValidationReport("vmd", (), mode=export_strategy)

        action = ExportVmdAction(validator=validator)
        with mock.patch(
            "mmd_tools.actions.export_vmd_action._scene_maya_time_to_vmd_frame",
            return_value=lambda value: float(value) * 30.0 / 24.0,
        ) as converter:
            action._validate(VmdData(), VMD_EXPORT_BAKE_TIMELINE, {"frame_range": (0, 10)})
            action._validate(VmdData(), VMD_EXPORT_BAKE_TIMELINE, {"frame_range": (-1.0, 10.5)})

        self.assertEqual(observed[0], (VMD_EXPORT_BAKE_TIMELINE, (0, 12)))
        self.assertEqual(observed[1], (VMD_EXPORT_BAKE_TIMELINE, (-1, 13)))
        self.assertEqual(converter.call_count, 2)

    def test_legacy_strategy_is_normalized_and_ntsc_range_is_unchanged(self):
        observed = []

        def validator(_data, export_strategy, *, frame_range=None):
            observed.append((export_strategy, frame_range))
            return ExportValidationReport("vmd", (), mode=export_strategy)

        action = ExportVmdAction(validator=validator)
        with mock.patch(
            "mmd_tools.actions.export_vmd_action._scene_maya_time_to_vmd_frame",
            return_value=lambda value: float(value),
        ) as converter:
            action._validate(VmdData(), "preserve_keys", {"frame_range": (0, 10)})

        self.assertEqual(observed, [(VMD_EXPORT_BAKE_TIMELINE, (0, 10))])
        self.assertEqual(converter.call_count, 1)

    def test_fatal_payload_does_not_call_writer_or_change_target(self):
        exporter = _WritingVmdExporter()
        data = VmdData()
        invalid_frame = _valid_bone_frame()
        invalid_frame.rotation = (0.0, 0.0, 0.0, 0.0)
        data.bone_frames.append(invalid_frame)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"keep this target")
            before = hashlib.sha256(target.read_bytes()).hexdigest()
            result = ExportVmdAction(exporter=exporter).execute(
                ExportVmdRequest(str(target), {}, animation_data=data)
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(result.validation_report.issues[0].code, "VMD_QUATERNION_INVALID")
            self.assertEqual(exporter.calls, [])
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)

    def test_writer_receives_the_validated_vmd_snapshot(self):
        exporter = _TransformingVmdExporter()
        validated = []

        def validator(data, export_strategy, **_kwargs):
            validated.append(data)
            return ExportValidationReport("vmd", (), mode=export_strategy)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            source = {"model_name": "source", "bone_frames": []}
            result = ExportVmdAction(
                exporter=exporter,
                output_verifier=None,
                validator=validator,
            ).execute(ExportVmdRequest(str(target), {}, animation_data=source))

        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(len(validated), 1)
        self.assertIs(exporter.calls[0][1], validated[0])
        self.assertIs(exporter.calls[0][1], exporter.transformed)
        self.assertIsNot(exporter.calls[0][1], source)
        self.assertEqual(exporter.calls[0][1].header.model_name, "validated:source")

    def test_output_verifier_failure_keeps_existing_target(self):
        exporter = _WritingVmdExporter(invalid=True)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"old output")
            before = target.read_bytes()
            result = ExportVmdAction(exporter=exporter).execute(
                ExportVmdRequest(str(target), {}, animation_data=VmdData())
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(result.validation_report.issues[-1].code, "OUTPUT_PARSE_FAILED")
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(exporter.calls)

    def test_warning_requires_ack_before_writer_and_ack_allows_export(self):
        exporter = _WritingVmdExporter()

        def warning_validator(data, export_strategy, **_kwargs):
            return ExportValidationReport(
                "vmd",
                (ExportValidationIssue("VMD_FRAME_RANGE", "warning", False, "frame_range", "ack me"),),
                mode=export_strategy,
            )

        with tempfile.TemporaryDirectory() as directory:
            first_target = Path(directory) / "first.vmd"
            first = ExportVmdAction(exporter=exporter, validator=warning_validator).execute(
                ExportVmdRequest(str(first_target), {}, animation_data=VmdData())
            )
            self.assertFalse(first.succeeded)
            self.assertEqual(len(exporter.calls), 0)

            second_target = Path(directory) / "second.vmd"
            second = ExportVmdAction(exporter=exporter, validator=warning_validator).execute(
                ExportVmdRequest(
                    str(second_target),
                    {"ack_warnings": True},
                    animation_data=VmdData(),
                )
            )

        self.assertTrue(second.succeeded)
        self.assertEqual(len(exporter.calls), 1)


if __name__ == "__main__":
    unittest.main()
