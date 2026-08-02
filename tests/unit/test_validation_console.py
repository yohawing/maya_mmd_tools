"""Canonical Validation Console rendering contracts."""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.validation_console import render_validation_console_text  # noqa: E402
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport  # noqa: E402


class TestValidationConsoleRendering(unittest.TestCase):
    """Console text must be derived from the same report/catalog as artifacts."""

    def test_ready_report_has_stable_summary(self):
        report = ExportValidationReport("vmd", (), mode="C")

        rendered = render_validation_console_text(
            report,
            {"target_identity": "model_ROOT", "payload_fingerprint": "sha256:test"},
        )

        self.assertIn("Status: READY", rendered)
        self.assertIn("Format: vmd", rendered)
        self.assertIn("Mode: C", rendered)
        self.assertIn("Snapshot: sha256:test", rendered)
        self.assertIn("No validation issues.", rendered)

    def test_issue_detail_uses_catalog_wording_and_observed_fact(self):
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "VMD_RAW_PROVENANCE_MISSING",
                    "fatal",
                    True,
                    "raw_provenance",
                    "imported raw key provenance was not supplied",
                ),
            ),
            mode="A",
        )

        rendered = render_validation_console_text(report, {"fixture": "mode_a_missing_raw"})

        self.assertIn("[FATAL] VMD_RAW_PROVENANCE_MISSING", rendered)
        self.assertIn("imported raw key provenance was not supplied", rendered)
        self.assertIn("Remediation:", rendered)
        self.assertIn("mode_a_missing_raw", rendered)


if __name__ == "__main__":
    unittest.main()
