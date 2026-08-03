"""Focused contracts for the v0.7 export release-gate orchestrator."""

import json
from pathlib import Path
import tempfile
import unittest

from tools.export_release_gate import (
    _not_run,
    _require_build_path,
    _run_fail_fixture_matrix,
    _validate_maya_probe_report,
)


class ExportReleaseGateTests(unittest.TestCase):
    """The release summary must expose omissions and fail-closed fixtures."""

    def test_require_build_path_rejects_paths_outside_build(self):
        with self.assertRaises(ValueError):
            _require_build_path(Path(tempfile.gettempdir()) / "release-summary", "--out-dir")

    def test_not_run_is_explicit(self):
        result = _not_run("gui", "focused gate does not include full GUI")

        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["reason"], "focused gate does not include full GUI")

    def test_fail_fixture_matrix_is_green_only_when_boundaries_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _run_fail_fixture_matrix(Path(directory))

        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            {fixture["name"] for fixture in result["fixtures"]},
            {
                "invalid_pmx",
                "invalid_pmd",
                "invalid_vmd_quaternion",
                "warning_ack_boundary",
            },
        )
        for fixture in result["fixtures"]:
            self.assertEqual(fixture["status"], "pass")
        self.assertEqual(len(result["report_paths"]), 5)

    def test_maya_probe_report_is_required_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "maya-probe.json"
            report = {
                "gate": "V070-EXPORT-RELEASE-GATE-1",
                "maya_version": "2024",
                "status": "pass",
                "cases": [
                    {
                        "format": export_format,
                        "status": "pass",
                        "report_json": str(root / export_format / "report.json"),
                        "report_md": str(root / export_format / "report.md"),
                    }
                    for export_format in ("pmx", "pmd", "vmd")
                ],
            }
            report["cases"][1].update(
                status="policy-reject",
                policy_code="PMD_EXPORT_POLICY_REJECT",
            )
            report_path.write_text(json.dumps(report), encoding="utf-8")

            step = {"name": "maya_probe_2024", "status": "pass"}
            report_paths = _validate_maya_probe_report(step, report_path, "2024")

            self.assertEqual(step["status"], "pass")
            self.assertEqual({path.parent.name for path in report_paths}, {"pmx", "pmd", "vmd"})

            report["cases"][-1]["status"] = "fail"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")


if __name__ == "__main__":
    unittest.main()
