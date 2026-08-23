"""Tests for deterministic Export Validation artifact bundles."""

import json
from pathlib import Path
import tempfile
import unittest

from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from mmd_tools.validation.issue_catalog import UnknownValidationIssueError
from mmd_tools.validation.report_artifacts import write_validation_report_artifacts
from tools.export_report_consistency import validate_report_consistency


def _valid_model_data():
    """Return the smallest model payload accepted by the validator."""
    return {
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
                "bone_weights": [1.0],
            }
        ],
        "faces": [[0, 0, 0]],
        "materials": [{"face_count": 3}],
    }


class ValidationReportArtifactTests(unittest.TestCase):
    """Verify one canonical report produces matching JSON and Markdown."""

    def test_writes_both_artifacts_from_the_same_report(self):
        model_data = _valid_model_data()
        model_data["faces"] = [[0, 0]]
        report = validate_model_data(model_data, "pmx")

        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run-001"
            paths = write_validation_report_artifacts(
                report,
                run_directory,
                target_identity="modelRoot",
                snapshot_fingerprint="sha256:fixture",
                evidence={"fixture": "face-too-short"},
            )

            self.assertEqual(paths.run_directory, run_directory)
            self.assertEqual(paths.json_path, run_directory / "report.json")
            self.assertEqual(paths.markdown_path, run_directory / "report.md")
            json_text = paths.json_path.read_text(encoding="utf-8")
            markdown_text = paths.markdown_path.read_text(encoding="utf-8")
            self.assertTrue(json_text.endswith("\n"))
            self.assertTrue(markdown_text.endswith("\n"))
            self.assertEqual(
                json.loads(json_text),
                report.to_canonical_dict(
                    target_identity="modelRoot",
                    snapshot_fingerprint="sha256:fixture",
                    evidence={"fixture": "face-too-short"},
                ),
            )
            self.assertIn("`INPUT_INVALID`", markdown_text)
            self.assertIn('"fixture": "face-too-short"', markdown_text)

    def test_artifacts_are_byte_deterministic_for_equal_inputs(self):
        report = validate_model_data(_valid_model_data(), "pmx")

        with tempfile.TemporaryDirectory() as directory:
            first = write_validation_report_artifacts(
                report,
                Path(directory) / "first",
                target_identity="modelRoot",
                snapshot_fingerprint="sha256:same",
                evidence={"fixture": "valid"},
            )
            second = write_validation_report_artifacts(
                report,
                Path(directory) / "second",
                target_identity="modelRoot",
                snapshot_fingerprint="sha256:same",
                evidence={"fixture": "valid"},
            )

            self.assertEqual(first.json_path.read_bytes(), second.json_path.read_bytes())
            self.assertEqual(first.markdown_path.read_bytes(), second.markdown_path.read_bytes())

    def test_unknown_issue_code_fails_before_creating_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "should-not-exist"
            with self.assertRaises(UnknownValidationIssueError):
                ExportValidationIssue("UNREGISTERED", "fatal", True, "model_data", "bad")

            self.assertFalse(run_directory.exists())

    def test_markdown_roundtrips_multiline_text_and_nullable_header_sentinels(self):
        issue = ExportValidationIssue(
            "INPUT_INVALID",
            "fatal",
            True,
            "payload`field",
            "first line\nsecond `line`",
            action="repair\nthen retry with `care`",
        )
        cases = (
            ("unknown", "unspecified", "unspecified"),
            (None, None, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (export_format, target, snapshot) in enumerate(cases):
                with self.subTest(export_format=export_format, target=target, snapshot=snapshot):
                    paths = write_validation_report_artifacts(
                        ExportValidationReport(export_format, (issue,)),
                        Path(directory) / f"case-{index}",
                        target_identity=target,
                        snapshot_fingerprint=snapshot,
                        provenance="source\nwith `tick`",
                    )
                    validate_report_consistency(paths.json_path, paths.markdown_path)
                    markdown = paths.markdown_path.read_text(encoding="utf-8")
                    self.assertIn('"first line\\nsecond `line`"', markdown)
                    self.assertIn(f"- Format: {json.dumps(export_format)}", markdown)


if __name__ == "__main__":
    unittest.main()
