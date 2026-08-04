"""Focused contracts for the v0.7 export release-gate orchestrator."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import tools.export_release_gate as RELEASE_GATE
from tools.export_release_gate import (
    _not_run,
    _require_build_path,
    _run_fail_fixture_matrix,
    _maya_path,
    _validate_maya_probe_report,
)
from tools.export_release_maya_probe import _compare_scene_oracles


class ExportReleaseGateTests(unittest.TestCase):
    """The release summary must expose omissions and fail-closed fixtures."""

    def test_maya_path_uses_shared_mayapy_resolver(self):
        """Release probes honor the shared environment-aware Maya resolver."""
        with mock.patch.object(
            RELEASE_GATE,
            "resolve_mayapy",
            return_value=Path("custom-maya/mayapy"),
        ) as resolver:
            self.assertEqual(_maya_path("2024"), Path("custom-maya/mayapy"))
        resolver.assert_called_once_with("2024")

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

    def test_release_summary_keeps_cli_and_submodule_provenance_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "release"

            def run_command(name, command, **_kwargs):
                if name == "mmd_anim_validation":
                    report_path = Path(command[-1])
                    report_path.write_text(
                        json.dumps(
                            {
                                "status": "pass",
                                "cli": "C:/downloads/mmd-anim.exe",
                                "cli_version": "mmd-anim 0.2.0",
                                "expected_cli_version": "mmd-anim 0.2.0",
                                "version_match": True,
                                "submodule_revision": "v0.3.3",
                            }
                        ),
                        encoding="utf-8",
                    )
                return {"name": name, "status": "pass", "returncode": 0}

            with mock.patch.object(
                RELEASE_GATE,
                "_run_fail_fixture_matrix",
                return_value={"status": "pass", "fixtures": [], "report_paths": []},
            ), mock.patch.object(
                RELEASE_GATE,
                "_run_command",
                side_effect=run_command,
            ), mock.patch.object(
                RELEASE_GATE,
                "_report_consistency_step",
                return_value={"name": "report_consistency", "status": "pass", "checked": [], "failures": []},
            ):
                summary = RELEASE_GATE.build_release_summary(
                    out_dir=out_dir,
                    maya_versions=(),
                    mmd_anim_cli="C:/downloads/mmd-anim.exe",
                    skip_gui=True,
                    full_gui=False,
                    skip_focused_tests=True,
            )

            provenance = summary["mmd_anim_provenance"]
            self.assertEqual(summary["status"], "fail")
            self.assertIn("focused_tests", summary["unexecuted"])
            self.assertEqual(provenance["cli_version"], "mmd-anim 0.2.0")
            self.assertEqual(provenance["expected_cli_version"], "mmd-anim 0.2.0")
            self.assertTrue(provenance["version_match"])
            self.assertEqual(provenance["submodule_revision"], "v0.3.3")
            self.assertEqual(
                provenance["relationship"]["cli_submodule_direct_comparison"],
                "not_applicable",
            )
            markdown = (out_dir / "release-summary.md").read_text(encoding="utf-8")
            self.assertIn("## MMD-Anim Provenance", markdown)
            self.assertIn("Observed CLI version: `mmd-anim 0.2.0`", markdown)
            self.assertIn("Checked-out submodule revision: `v0.3.3`", markdown)

    def test_release_summary_does_not_reuse_stale_mmd_anim_report(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "release"
            out_dir.mkdir(parents=True)
            stale_report = out_dir / "mmd-anim-validation.json"
            stale_report.write_text(
                json.dumps(
                    {
                        "status": "pass",
                        "cli_version": "mmd-anim 0.1.9",
                        "expected_cli_version": "mmd-anim 0.1.9",
                        "version_match": True,
                        "submodule_revision": "stale",
                    }
                ),
                encoding="utf-8",
            )

            def run_command(name, _command, **_kwargs):
                if name == "mmd_anim_validation":
                    return {
                        "name": name,
                        "status": "fail",
                        "returncode": 1,
                        "stderr": "timeout",
                    }
                return {"name": name, "status": "pass", "returncode": 0}

            with mock.patch.object(
                RELEASE_GATE,
                "_run_fail_fixture_matrix",
                return_value={"status": "pass", "fixtures": [], "report_paths": []},
            ), mock.patch.object(
                RELEASE_GATE,
                "_run_command",
                side_effect=run_command,
            ), mock.patch.object(
                RELEASE_GATE,
                "_report_consistency_step",
                return_value={"name": "report_consistency", "status": "pass", "checked": [], "failures": []},
            ):
                summary = RELEASE_GATE.build_release_summary(
                    out_dir=out_dir,
                    maya_versions=(),
                    mmd_anim_cli="C:/downloads/mmd-anim.exe",
                    skip_gui=True,
                    full_gui=False,
                    skip_focused_tests=True,
                )

            provenance = summary["mmd_anim_provenance"]
            self.assertEqual(summary["status"], "fail")
            self.assertFalse(stale_report.exists())
            self.assertEqual(provenance["evidence_status"], "unavailable")
            self.assertEqual(provenance["validation_status"], None)
            self.assertIn("not written", provenance["reason"])

    def test_scene_oracle_detects_material_semantic_drift(self):
        source = {
            "materials": [
                {
                    "index": 0,
                    "name": "face",
                    "name_en": "Face",
                    "diffuse": [1.0, 0.5, 0.25, 1.0],
                    "specular": [0.2, 0.2, 0.2],
                    "ambient": [0.1, 0.1, 0.1],
                    "edge_color": [0.0, 0.0, 0.0, 1.0],
                    "edge_size": 1.0,
                    "shininess": 0.99,
                    "memo": "",
                    "texture_path": None,
                    "sphere_texture_path": "",
                    "draw_flags": 14,
                    "edge_flag": None,
                    "sphere_mode": 0,
                    "sphere_texture_index": -1,
                    "texture_index": -1,
                    "toon_texture_index": -1,
                    "shared_toon_flag": 0,
                }
            ],
            "metadata": {"mmd_file_type": "pmx", "mmd_model_name": "fixture"},
            "pose": {"joint_count": 0, "frames": {}},
        }
        actual = {
            **source,
            "materials": [
                {
                    **source["materials"][0],
                    "shininess": 0.5,
                    "edge_size": 0.5,
                    "memo": "drift",
                    "sphere_texture_path": None,
                }
            ],
        }

        failures = _compare_scene_oracles(source, actual, pose=False, mesh=False)

        self.assertTrue(any("material[0].shininess" in failure for failure in failures))
        self.assertTrue(any("material[0].edge_size" in failure for failure in failures))
        self.assertTrue(any("material[0].memo" in failure for failure in failures))
        self.assertFalse(any("material[0].sphere_texture_path" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
