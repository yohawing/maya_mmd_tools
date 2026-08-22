"""Canonical Validation Console rendering contracts."""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.validation_console import (  # noqa: E402
    _console_requires_red,
    render_validation_console_text,
)
from mmd_tools.ui.translations import UITranslator  # noqa: E402
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport  # noqa: E402


class TestValidationConsoleRendering(unittest.TestCase):
    """Console text must be derived from one fixed English renderer."""

    def test_clean_report_has_exact_success_line(self):
        report = ExportValidationReport("vmd", (), mode="bake_timeline")

        self.assertEqual(
            render_validation_console_text(
                report,
                {"target_identity": "model_ROOT", "payload_fingerprint": "sha256:test"},
            ),
            "[INFO] Validation passed: no errors or warnings were found.",
        )

    def test_issue_reason_and_action_precede_debug_fields(self):
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "EXPORT_OPTIONS_INVALID",
                    "fatal",
                    True,
                    "frame_range",
                    "current scene frame range is invalid",
                    "Choose a valid start and end frame, then retry.",
                    {"start": 42, "end": 12},
                ),
            ),
            mode="bake_timeline",
        )

        rendered = render_validation_console_text(report, {"fixture": "range"})

        self.assertIn("[BLOCKED] Validation report", rendered)
        self.assertLess(rendered.index("Reason:"), rendered.index("Action:"))
        self.assertLess(rendered.index("Action:"), rendered.index("Code:"))
        self.assertIn("[FATAL] BLOCKED", rendered)
        self.assertIn("Path: frame_range", rendered)
        self.assertIn('Details: {"end": 12, "start": 42}', rendered)

    def test_info_only_report_starts_clean_and_keeps_info_details(self):
        report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue(
                    "UNSUPPORTED_FEATURE",
                    "info",
                    False,
                    "feature",
                    "optional feature was not present",
                    "No action is required.",
                ),
            ),
        )

        rendered = render_validation_console_text(report)

        self.assertTrue(
            rendered.startswith("[INFO] Validation passed: no errors or warnings were found.")
        )
        self.assertIn("Reason: optional feature was not present", rendered)
        self.assertIn("Code: UNSUPPORTED_FEATURE", rendered)

    def test_oversized_report_preserves_aggregation(self):
        report = ExportValidationReport(
            "pmx",
            tuple(
                ExportValidationIssue(
                    "INPUT_INVALID",
                    "fatal",
                    True,
                    f"faces[{index}]",
                    "face has fewer than three indices",
                    "Repair the test payload and retry export.",
                )
                for index in range(105)
            ),
        )

        rendered = render_validation_console_text(report)

        self.assertIn("Issue occurrences: shown=105 omitted=0", rendered)
        self.assertIn("Occurrences: 105", rendered)
        self.assertIn("Path pattern: faces[*]", rendered)

    def test_red_style_uses_full_source_summary_beyond_display_limit(self):
        report = ExportValidationReport(
            "pmx",
            tuple(
                ExportValidationIssue(
                    "INPUT_INVALID",
                    "warning",
                    False,
                    f"field_{index:03d}",
                    "field needs review",
                    "Review the field and retry export.",
                )
                for index in range(100)
            )
            + (
                ExportValidationIssue(
                    "INPUT_INVALID",
                    "fatal",
                    False,
                    "field_100",
                    "field is invalid",
                    "Repair the field and retry export.",
                ),
            ),
        )

        self.assertFalse(report.is_blocking)
        self.assertNotIn("field_100", tuple(issue.path for issue in report.issues))
        self.assertIsNotNone(report.issue_aggregation)
        self.assertEqual(report.issue_aggregation.omitted_groups, 1)
        self.assertTrue(_console_requires_red(report))

    def test_console_text_is_english_for_every_ui_locale(self):
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "EXPORT_OPTIONS_INVALID",
                    "fatal",
                    True,
                    "frame_range",
                    "current scene frame range is invalid",
                ),
            ),
            mode="bake_timeline",
        )
        try:
            translator.set_language("ja")
            japanese = render_validation_console_text(report)
            translator.set_language("zh-CN")
            simplified = render_validation_console_text(report)
            translator.set_language("zh-TW")
            traditional = render_validation_console_text(report)
        finally:
            translator.set_language(previous_language)

        self.assertEqual(japanese, simplified)
        self.assertEqual(simplified, traditional)
        self.assertIn("Reason: current scene frame range is invalid", japanese)
        self.assertNotIn("理由:", japanese)


if __name__ == "__main__":
    unittest.main()
