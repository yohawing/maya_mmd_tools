"""VMD Mode A/C validation and atomic export fail-path contracts."""

import hashlib
import math
from pathlib import Path
import tempfile
import unittest

from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.vmd_exporter import VmdExporter
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport
from mmd_tools.validation.vmd_validator import (
    VMD_MODE_A,
    VMD_MODE_C,
    validate_vmd_data,
    verify_vmd_output,
)


def _valid_bone_frame() -> VmdBoneFrame:
    """Return one finite bone frame suitable for a structural fixture."""
    frame = VmdBoneFrame()
    frame.bone_name = "センター"
    frame.rotation = (0.0, 0.0, 0.0, 1.0)
    return frame


class TestVmdValidator(unittest.TestCase):
    """VMD payload issue codes remain deterministic and fail closed."""

    def test_empty_mode_c_payload_is_ready(self):
        report = validate_vmd_data(VmdData(), VMD_MODE_C)

        self.assertTrue(report.valid)
        self.assertEqual(report.mode, VMD_MODE_C)
        self.assertEqual(report.to_dict()["status"], "ready")

    def test_mode_a_requires_raw_provenance(self):
        report = validate_vmd_data(VmdData(), VMD_MODE_A)

        self.assertTrue(report.is_blocking)
        self.assertEqual([issue.code for issue in report.issues], ["VMD_RAW_PROVENANCE_MISSING"])

    def test_mode_a_rejects_raw_key_set_mismatch(self):
        data = VmdData()
        data.bone_frames.append(_valid_bone_frame())
        report = validate_vmd_data(
            data,
            VMD_MODE_A,
            raw_provenance={
                "raw_bone_interpolation_complete": True,
                "raw_bone_key_count": 1,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 10,
                        "interpolation": [20] * 64,
                    }
                ],
            },
        )

        self.assertIn("VMD_RAW_PROVENANCE_MISMATCH", [issue.code for issue in report.issues])

    def test_mode_a_rejects_raw_interpolation_payload_change(self):
        data = VmdData()
        data.bone_frames.append(_valid_bone_frame())
        report = validate_vmd_data(
            data,
            VMD_MODE_A,
            raw_provenance={
                "raw_bone_interpolation_complete": True,
                "raw_bone_key_count": 1,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "interpolation": [7] * 64,
                    }
                ],
            },
        )

        self.assertIn("VMD_RAW_PROVENANCE_MISMATCH", [issue.code for issue in report.issues])

    def test_mode_a_rejects_raw_position_or_rotation_payload_change(self):
        data = VmdData()
        data.bone_frames.append(_valid_bone_frame())
        report = validate_vmd_data(
            data,
            VMD_MODE_A,
            raw_provenance={
                "raw_bone_interpolation_complete": True,
                "raw_bone_transform_complete": True,
                "raw_bone_key_count": 1,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "position": [1.0, 2.0, 3.0],
                        "rotation": [0.0, 0.0, 0.0, 1.0],
                        "interpolation": [20] * 64,
                    }
                ],
            },
        )

        self.assertIn("VMD_RAW_PROVENANCE_MISMATCH", [issue.code for issue in report.issues])

    def test_mode_a_scopes_raw_comparison_to_requested_frame_range(self):
        data = VmdData()
        frame = _valid_bone_frame()
        frame.frame_number = 10
        data.bone_frames.append(frame)
        report = validate_vmd_data(
            data,
            VMD_MODE_A,
            frame_range=(10, 10),
            raw_provenance={
                "raw_bone_interpolation_complete": True,
                "raw_bone_key_count": 2,
                "raw_bone_interpolation": [
                    {
                        "bone_name": "センター",
                        "frame_number": 0,
                        "interpolation": [20] * 64,
                    },
                    {
                        "bone_name": "センター",
                        "frame_number": 10,
                        "interpolation": [20] * 64,
                    },
                ],
            },
        )

        self.assertTrue(report.valid)

    def test_invalid_bone_payload_reports_all_relevant_contracts(self):
        data = VmdData()
        frame = _valid_bone_frame()
        frame.frame_number = -1
        frame.rotation = (math.nan, 0.0, 0.0, 0.0)
        frame.interpolation = b"short"
        data.bone_frames.append(frame)

        report = validate_vmd_data(data, VMD_MODE_C, frame_range=(0, 10))
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

    def test_unsupported_mode_is_blocking(self):
        report = validate_vmd_data(VmdData(), "B")

        self.assertTrue(report.is_blocking)
        self.assertEqual(report.issues[0].code, "VMD_MODE_UNSUPPORTED")

    def test_verify_output_parses_vmd_written_by_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            data = VmdData()
            data.bone_frames.append(_valid_bone_frame())
            data.write_file(path)

            report = verify_vmd_output(str(path), VMD_MODE_C)

        self.assertTrue(report.valid)
        self.assertEqual(report.export_format, "vmd")

    def test_verify_output_rejects_section_count_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.vmd"
            VmdData().write_file(path)

            report = verify_vmd_output(
                str(path),
                VMD_MODE_C,
                expected_counts={"bone_frames": 1},
            )

        self.assertEqual(report.issues[0].code, "VMD_FRAME_COUNT_MISMATCH")


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

        def validator(data, mode, **_kwargs):
            validated.append(data)
            return ExportValidationReport("vmd", (), mode=mode)

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

    def test_mode_a_with_raw_provenance_can_export(self):
        exporter = _WritingVmdExporter()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            result = ExportVmdAction(exporter=exporter).execute(
                ExportVmdRequest(
                    str(target),
                    {"vmd_mode": VMD_MODE_A, "raw_provenance": {"source": "import"}},
                    animation_data=VmdData(),
                )
            )

            self.assertTrue(result.succeeded)
        self.assertEqual(result.validation_report.mode, VMD_MODE_A)
        self.assertIsNotNone(result.payload_fingerprint)

    def test_collector_raw_provenance_flows_into_mode_a_validation(self):
        def collector(_options):
            return {
                "model_name": "ImportedMotion",
                "raw_provenance": {"source": "import", "raw_bone_key_count": 0},
                "bone_frames": [],
            }

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            result = ExportVmdAction(
                exporter=VmdExporter(native_exporter=None),
                collector=collector,
            ).execute(ExportVmdRequest(str(target), {"vmd_mode": VMD_MODE_A}))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.validation_report.mode, VMD_MODE_A)

    def test_reusing_request_does_not_retain_collector_provenance(self):
        payloads = iter(
            (
                {
                    "model_name": "ImportedMotion",
                    "raw_provenance": {"source": "first"},
                    "bone_frames": [],
                },
                {"model_name": "EditedMotion", "bone_frames": []},
            )
        )
        action = ExportVmdAction(
            exporter=VmdExporter(native_exporter=None),
            collector=lambda _options: next(payloads),
        )

        with tempfile.TemporaryDirectory() as directory:
            request = ExportVmdRequest(
                str(Path(directory) / "motion.vmd"),
                {"vmd_mode": VMD_MODE_A},
            )
            first = action.execute(request)
            second = action.execute(request)

        self.assertTrue(first.succeeded)
        self.assertFalse(second.succeeded)
        self.assertEqual(second.validation_report.issues[0].code, "VMD_RAW_PROVENANCE_MISSING")
        self.assertNotIn("raw_provenance", request.options)

    def test_warning_requires_ack_before_writer_and_ack_allows_export(self):
        exporter = _WritingVmdExporter()

        def warning_validator(data, mode, **_kwargs):
            return ExportValidationReport(
                "vmd",
                (ExportValidationIssue("VMD_FRAME_RANGE", "warning", False, "frame_range", "ack me"),),
                mode=mode,
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
