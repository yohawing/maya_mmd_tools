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
                "INPUT_INVALID",
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
                "INPUT_INVALID",
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

    def test_append_merge_and_filter_use_all_source_occurrences(self):
        visible = tuple(
            ExportValidationIssue(
                "INPUT_INVALID",
                "info",
                False,
                f"groups.field{index}",
                "visible fixture",
            )
            for index in range(100)
        )
        hidden_warning = ExportValidationIssue(
            "ROUTE_UNRESOLVED",
            "warning",
            False,
            "hidden.warning",
            "hidden warning",
            details={"route": "dependency_bake"},
        )
        hidden_blocking = ExportValidationIssue(
            "OWNERSHIP_CONFLICT",
            "fatal",
            True,
            "hidden.control_rig",
            "hidden blocking",
            details={"owner": "control_rig"},
        )
        report = ExportValidationReport(
            "vmd", visible + (hidden_warning, hidden_blocking), mode="bake_timeline"
        )

        self.assertEqual(len(report.issues), 100)
        self.assertEqual(report.to_dict()["summary"], {"fatal": 1, "warning": 1, "info": 100})
        self.assertTrue(report.is_blocking)
        self.assertTrue(report.requires_warning_ack)
        self.assertEqual(len(report.warning_issues), 1)
        self.assertEqual(len(report.blocking_issues), 1)

        appended = report.with_appended_issues(
            (ExportValidationIssue("INPUT_INVALID", "info", False, "appended", "appended"),)
        )
        merged = appended.merged_with(
            ExportValidationReport(
                "vmd",
                (ExportValidationIssue("INPUT_INVALID", "info", False, "merged", "merged"),),
                mode="bake_timeline",
            )
        )
        filtered = merged.filtered(
            lambda issue: not (
                issue.code == "OWNERSHIP_CONFLICT"
                and issue.details.get("owner") == "control_rig"
            )
        )

        self.assertEqual(appended.issue_aggregation.total_occurrences, 103)
        self.assertEqual(merged.issue_aggregation.total_occurrences, 104)
        self.assertEqual(filtered.issue_aggregation.total_occurrences, 103)
        self.assertEqual(filtered.to_dict()["summary"], {"fatal": 0, "warning": 1, "info": 102})
        self.assertFalse(filtered.is_blocking)
        self.assertTrue(filtered.requires_warning_ack)


if __name__ == "__main__":
    unittest.main()
