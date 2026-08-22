"""Canonical Validation Console rendering contracts."""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui.validation_console import render_validation_console_text  # noqa: E402
from mmd_tools.ui.translations import UITranslator  # noqa: E402
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport  # noqa: E402


class TestValidationConsoleRendering(unittest.TestCase):
    """Console text must be derived from the same report/catalog as artifacts."""

    def test_ready_report_has_stable_summary(self):
        report = ExportValidationReport("vmd", (), mode="bake_timeline")

        rendered = render_validation_console_text(
            report,
            {"target_identity": "model_ROOT", "payload_fingerprint": "sha256:test"},
        )

        self.assertIn("Status: READY", rendered)
        self.assertIn("Format: vmd", rendered)
        self.assertIn("Export strategy: bake_timeline", rendered)
        self.assertIn("Snapshot: sha256:test", rendered)
        self.assertIn("No validation issues.", rendered)

    def test_issue_detail_uses_catalog_wording_and_observed_fact(self):
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "VMD_FRAME_RANGE",
                    "fatal",
                    True,
                    "frame_range",
                    "current scene frame range is invalid",
                ),
            ),
            mode="bake_timeline",
        )

        rendered = render_validation_console_text(report, {"fixture": "current_scene_invalid_range"})

        self.assertIn("[FATAL] VMD_FRAME_RANGE", rendered)
        self.assertIn("current scene frame range is invalid", rendered)
        self.assertIn("Remediation:", rendered)
        self.assertIn("current_scene_invalid_range", rendered)

    def test_oversized_report_shows_folded_occurrence_counts(self):
        report = ExportValidationReport(
            "pmx",
            tuple(
                ExportValidationIssue(
                    "FACE_TOO_SHORT",
                    "fatal",
                    True,
                    f"faces[{index}]",
                    "face has fewer than three indices",
                )
                for index in range(105)
            ),
        )

        rendered = render_validation_console_text(report)

        self.assertIn("Issue occurrences: shown=105 omitted=0", rendered)
        self.assertIn("Occurrences: 105", rendered)
        self.assertIn("Path pattern: faces[*]", rendered)

    def test_localized_console_resolves_labels_and_catalog_wording(self):
        translator = UITranslator.instance()
        previous_language = translator.get_language()
        translator.set_language("ja")
        try:
            report = ExportValidationReport(
                "vmd",
                (
                    ExportValidationIssue(
                        "VMD_FRAME_RANGE",
                        "fatal",
                        True,
                        "frame_range",
                        "current scene frame range is invalid",
                    ),
                ),
                mode="bake_timeline",
            )

            rendered = render_validation_console_text(report, localize=True)

            self.assertIn("エクスポート検証コンソール", rendered)
            self.assertIn("書き出し方式: 現在のタイムラインをVMD化", rendered)
            self.assertIn("タイトル:", rendered)
            self.assertIn("影響:", rendered)
            self.assertIn("対処方法:", rendered)
        finally:
            translator.set_language(previous_language)


if __name__ == "__main__":
    unittest.main()
