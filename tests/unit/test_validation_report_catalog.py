"""Tests for catalog-backed export validation audit reports."""

import json
from pathlib import Path
import tempfile
import unittest

from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    validate_model_data,
)
from mmd_tools.validation.issue_catalog import (
    UnknownValidationIssueError,
    get_issue_catalog_entry,
)


def _valid_model_data():
    """Return the smallest model payload accepted by the model validator."""
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


class ValidationReportCatalogTests(unittest.TestCase):
    """Verify deterministic JSON/Markdown output and catalog fail-closed behavior."""

    def test_canonical_ready_report_contains_contract_metadata(self):
        report = validate_model_data(_valid_model_data(), "pmx")

        payload = report.to_canonical_dict(
            target_identity="modelRoot",
            snapshot_fingerprint="sha256:fixture",
            evidence={"fixture": "valid-model"},
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["target_identity"], "modelRoot")
        self.assertEqual(payload["snapshot_fingerprint"], "sha256:fixture")
        self.assertEqual(payload["evidence"], {"fixture": "valid-model"})
        self.assertEqual(payload["issues"], [])

    def test_blocking_issue_is_enriched_from_catalog(self):
        model_data = _valid_model_data()
        model_data["faces"] = [[0, 0]]
        report = validate_model_data(model_data, "pmx")

        payload = report.to_canonical_dict(
            snapshot_fingerprint="sha256:bad-face",
            evidence={"fixture": "face-too-short"},
        )
        issue = payload["issues"][0]

        self.assertEqual(issue["code"], "INPUT_INVALID")
        self.assertIn("face must contain", issue["reason"])
        self.assertTrue(issue["blocking"])
        self.assertIn("sha256:bad-face", issue["evidence"].values())

    def test_non_sequence_texture_payload_produces_cataloged_report(self):
        model_data = _valid_model_data()
        model_data["textures"] = {}

        report = validate_model_data(model_data, "pmx")

        payload = report.to_canonical_dict()

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["summary"]["fatal"], 1)
        issue = payload["issues"][0]
        self.assertEqual(issue["code"], "INPUT_INVALID")
        self.assertIn("textures", issue["reason"])

    def test_markdown_is_deterministic_and_keeps_audit_order(self):
        model_data = _valid_model_data()
        model_data["faces"] = [[0, 0]]
        report = validate_model_data(model_data, "pmx")

        markdown = report.to_markdown(
            target_identity="modelRoot",
            snapshot_fingerprint="sha256:bad-face",
            evidence={"fixture": "face-too-short"},
        )

        self.assertEqual(markdown, report.to_markdown(
            target_identity="modelRoot",
            snapshot_fingerprint="sha256:bad-face",
            evidence={"fixture": "face-too-short"},
        ))
        self.assertLess(markdown.index("- Reason:"), markdown.index("- Action:"))
        self.assertIn('"BLOCKED"', markdown)
        self.assertIn("`INPUT_INVALID`", markdown)
        self.assertIn('"fixture": "face-too-short"', markdown)

    def test_report_artifacts_are_utf8_and_have_final_newline(self):
        report = validate_model_data(_valid_model_data(), "pmx")

        with tempfile.TemporaryDirectory() as directory:
            json_path = Path(directory) / "report.json"
            markdown_path = Path(directory) / "report.md"
            report.write_canonical_json(json_path, target_identity="modelRoot")
            report.write_markdown(markdown_path, target_identity="modelRoot")

            json_text = json_path.read_text(encoding="utf-8")
            markdown_text = markdown_path.read_text(encoding="utf-8")
            self.assertTrue(json_text.endswith("\n"))
            self.assertTrue(markdown_text.endswith("\n"))
            self.assertEqual(json.loads(json_text), report.to_canonical_dict(target_identity="modelRoot"))
            self.assertIn("No validation issues.", markdown_text)

    def test_unknown_issue_code_fails_canonical_report_closed(self):
        with self.assertRaises(UnknownValidationIssueError):
            ExportValidationIssue("UNREGISTERED", "fatal", True, "model_data", "bad")

    def test_catalog_uses_reason_and_action_only(self):
        entry = get_issue_catalog_entry("UNSUPPORTED_FEATURE")

        self.assertEqual(entry.code, "UNSUPPORTED_FEATURE")
        self.assertTrue(entry.reason)
        self.assertTrue(entry.action)

if __name__ == "__main__":
    unittest.main()
