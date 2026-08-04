"""Tests for bounded export-validation issue reporting and artifact parity."""

import json
from pathlib import Path
import tempfile
import unittest

from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from mmd_tools.validation.report_artifacts import write_validation_report_artifacts
from tools.export_report_consistency import validate_report_consistency


class ExportIssueAggregationTests(unittest.TestCase):
    """Keep malformed large payloads bounded without hiding total counts."""

    def test_per_vertex_semantic_missing_is_folded_by_nearby_path(self):
        model_data = {
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [0.0, 0.0],
                    "bone_indices": [0],
                    "semantic_missing": ["additional_uvs_storage"],
                }
                for _ in range(150)
            ],
            "faces": [[0, 0, 0]],
        }

        report = validate_model_data(model_data, "pmx")

        self.assertEqual(len(report.issues), 1)
        self.assertTrue(report.is_blocking)
        self.assertEqual(report.to_dict()["summary"], {"fatal": 150, "warning": 0, "info": 0})
        self.assertEqual(
            report.issue_aggregation.to_dict(),
            {
                "max_display_issues": 100,
                "total_occurrences": 150,
                "shown_occurrences": 150,
                "omitted_occurrences": 0,
                "total_groups": 1,
                "shown_groups": 1,
                "omitted_groups": 0,
                "has_blocking": True,
                "requires_warning_ack": False,
            },
        )
        issue = report.to_dict()["issues"][0]
        self.assertEqual(issue["occurrence_count"], 150)
        self.assertEqual(issue["path_pattern"], "vertices[*].semantic_missing")
        self.assertEqual(len(issue["sample_paths"]), 3)

    def test_top_groups_are_bounded_and_omitted_occurrences_are_counted(self):
        issues = tuple(
            ExportValidationIssue(
                "FACE_TOO_SHORT",
                "fatal",
                True,
                f"faces[0].field{min(index, 2)}",
                "face has fewer than three indices",
            )
            for index in range(6)
        )

        report = ExportValidationReport("pmx", issues, max_display_issues=2)

        self.assertEqual(
            [issue.path for issue in report.issues],
            ["faces[0].field0", "faces[0].field1"],
        )
        self.assertEqual(report.issue_aggregation.shown_occurrences, 2)
        self.assertEqual(report.issue_aggregation.omitted_occurrences, 4)
        self.assertEqual(report.issue_aggregation.total_groups, 3)
        self.assertEqual(report.issue_aggregation.shown_groups, 2)
        self.assertEqual(report.issue_aggregation.omitted_groups, 1)
        self.assertEqual(report.to_dict()["issues"][0]["occurrence_count"], 1)

    def test_canonical_json_and_markdown_share_aggregation_metadata(self):
        issues = tuple(
            ExportValidationIssue(
                "FACE_TOO_SHORT",
                "fatal",
                True,
                f"faces[{index}]",
                "face has fewer than three indices",
            )
            for index in range(105)
        )
        report = ExportValidationReport("pmx", issues)

        with tempfile.TemporaryDirectory() as directory:
            paths = write_validation_report_artifacts(report, Path(directory) / "run")
            validate_report_consistency(paths.json_path, paths.markdown_path)
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["summary"], {"fatal": 105, "warning": 0, "info": 0})
        self.assertEqual(payload["issue_aggregation"]["omitted_occurrences"], 0)
        self.assertEqual(payload["issues"][0]["occurrence_count"], 105)


if __name__ == "__main__":
    unittest.main()
