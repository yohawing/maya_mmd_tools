"""Tests for canonical export report JSON/Markdown consistency checks."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport
from mmd_tools.validation.report_artifacts import write_validation_report_artifacts
from tools.gates.export_report_consistency import (
    ReportConsistencyError,
    main,
    validate_report_consistency,
)


def _report_with_multiple_issues() -> ExportValidationReport:
    """Return a catalog-backed report with fatal and warning issues."""
    return ExportValidationReport(
        "pmx",
        (
            ExportValidationIssue(
                "INPUT_INVALID",
                "fatal",
                True,
                "model_data.faces[0]",
                "face has fewer than three indices",
            ),
            ExportValidationIssue(
                "UNSUPPORTED_FEATURE",
                "warning",
                False,
                "model_data.vertices[0].additional_uv",
                "additional UV data is not supported",
            ),
        ),
        mode="model",
    )


def _write_report_pair(directory: Path):
    """Write a representative pair using the existing artifact writer."""
    return write_validation_report_artifacts(
        _report_with_multiple_issues(),
        directory,
        target_identity="modelRoot",
        snapshot_fingerprint="sha256:fixture",
        provenance="unit-test",
        evidence={"fixture": "consistency", "nested": {"frame": 1}},
    )


class ExportReportConsistencyTests(unittest.TestCase):
    """Verify consistency, catalog, provenance, and CLI failure behavior."""

    def test_report_artifacts_output_is_consistent_with_multiple_issues(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")

            validate_report_consistency(paths.json_path, paths.markdown_path)

            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [issue["code"] for issue in payload["issues"]],
                ["INPUT_INVALID", "UNSUPPORTED_FEATURE"],
            )
            self.assertIn("Report evidence:", paths.markdown_path.read_text(encoding="utf-8"))

    def test_omitted_issue_groups_preserve_full_report_status(self):
        """Markdown parity accepts aggregation facts that include hidden issues."""
        report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue(
                    "INPUT_INVALID", "warning", False, "", "visible warning"
                ),
                ExportValidationIssue(
                    "REFERENCE_INVALID", "fatal", True, "bones[0]", "hidden fatal"
                ),
            ),
            max_display_issues=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_validation_report_artifacts(report, Path(directory) / "run")

            validate_report_consistency(paths.json_path, paths.markdown_path)

            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["issue_aggregation"]["omitted_occurrences"], 1)

    def test_markdown_issue_order_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            lines = paths.markdown_path.read_text(encoding="utf-8").splitlines()
            heading_indices = [
                index for index, line in enumerate(lines) if line.startswith("### ")
            ]
            first_block = lines[heading_indices[0] : heading_indices[1]]
            second_block = lines[heading_indices[1] :]
            second_block[0] = re.sub(r"^### 2\.", "### 1.", second_block[0])
            first_block[0] = re.sub(r"^### 1\.", "### 2.", first_block[0])
            reordered = lines[: heading_indices[0]] + second_block + first_block
            paths.markdown_path.write_text("\n".join(reordered) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ReportConsistencyError, "projections differ"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_unknown_issue_code_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            payload["issues"][0]["code"] = "UNKNOWN_REPORT_CODE"
            paths.json_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReportConsistencyError, "unregistered issue code"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_ai_marker_in_formal_markdown_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            markdown = paths.markdown_path.read_text(encoding="utf-8")
            paths.markdown_path.write_text(
                markdown.replace(
                    "additional UV data is not supported",
                    "additional UV data was generated by AI",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReportConsistencyError, "AI-like marker"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_report_evidence_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            markdown = paths.markdown_path.read_text(encoding="utf-8")
            paths.markdown_path.write_text(
                markdown.replace('"fixture": "consistency"', '"fixture": "different"'),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReportConsistencyError, "projections differ"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_removed_issue_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            payload["issues"][0]["expected"] = "an unapproved expected value"
            paths.json_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReportConsistencyError, "removed legacy fields"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_markdown_reason_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_report_pair(Path(directory) / "run")
            markdown = paths.markdown_path.read_text(encoding="utf-8")
            paths.markdown_path.write_text(
                markdown.replace(
                    '- Reason: "face has fewer than three indices"',
                    '- Reason: "Unapproved reason"',
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ReportConsistencyError, "projections differ"):
                validate_report_consistency(paths.json_path, paths.markdown_path)

    def test_cli_returns_one_for_argument_and_read_failures(self):
        self.assertEqual(main([]), 1)
        self.assertEqual(main(["missing-report.json", "missing-report.md"]), 1)


if __name__ == "__main__":
    unittest.main()
