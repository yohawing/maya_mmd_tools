"""Dependency-bake warning contracts for the one-shot VMD action."""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.bake_timeline_vmd_export_action import (  # noqa: E402
    _augment_dependency_bake_report,
)
from mmd_tools.ui.validation_console import render_validation_console_text  # noqa: E402
from mmd_tools.validation.export_validator import (  # noqa: E402
    ExportValidationReport,
)


class DependencyBakeWarningTests(unittest.TestCase):
    def test_reason_is_renderer_ready_and_structured_details_are_retained(self):
        report = _augment_dependency_bake_report(
            ExportValidationReport("vmd", ()),
            {
                "diagnostics": {
                    "control_rig_direct_export": {
                        "dependency_baked": [
                            {
                                "bone": "EyeCtrl",
                                "frame_range": [0, 120],
                                "generated_key_count": 121,
                            }
                        ]
                    }
                }
            },
        )

        issue = report.issues[0]
        reason = "This bone has no dedicated Control Rig mapping, so its evaluated motion was baked."
        self.assertEqual(issue.reason, reason)
        self.assertTrue(issue.action)
        self.assertEqual(issue.details["bone"], "EyeCtrl")
        self.assertEqual(issue.details["frame_range"], [0, 120])
        self.assertEqual(issue.details["generated_key_count"], 121)

        rendered = render_validation_console_text(report)
        self.assertEqual(rendered.count(f"Reason: {reason}"), 1)
        self.assertIn('"bone": "EyeCtrl"', rendered)
        self.assertIn('"frame_range": [0, 120]', rendered)
        self.assertIn('"generated_key_count": 121', rendered)

    def test_unencodable_morph_omission_requires_warning_acknowledgement(self):
        report = _augment_dependency_bake_report(
            ExportValidationReport("vmd", ()),
            {
                "diagnostics": {
                    "omitted_unencodable_morphs": {
                        "track_count": 2,
                        "frame_count": 6,
                        "nonzero_frame_count": 1,
                        "names": ["-﹏-|||", "腹显"],
                    }
                }
            },
        )

        self.assertTrue(report.requires_warning_ack)
        issue = report.issues[0]
        self.assertEqual(issue.code, "UNSUPPORTED_FEATURE")
        self.assertEqual(issue.severity, "warning")
        self.assertEqual(issue.details["encoding"], "cp932")
        self.assertEqual(issue.details["names"], ["-﹏-|||", "腹显"])
        self.assertEqual(issue.details["nonzero_frame_count"], 1)


if __name__ == "__main__":
    unittest.main()
