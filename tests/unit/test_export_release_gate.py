"""Focused contracts for the v0.7 export release-gate orchestrator."""

import ast
import copy
import inspect
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
    _gui_test_args,
    _capture_release_provenance,
    _validate_binding_gate_artifact,
    _validate_ffi_build_step,
    _validate_release_provenance,
    _validate_maya_probe_report,
)
from tools.export_release_maya_probe import _compare_scene_oracles, _run_vmd_case


def _clean_release_provenance(run_id=None):
    """Return a deterministic clean Git snapshot for isolated summary tests."""
    return {
        "run_id": run_id or "test-run-id",
        "timestamp": "2026-08-09T00:00:00+00:00",
        "branch": "Feature/v070-export",
        "head_sha": "a" * 40,
        "dirty": False,
        "git_capture": {"branch": True, "head_sha": True, "status": True},
    }


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

    def test_full_gui_args_use_windows_dx11_virtual_device_only_on_windows(self):
        """Full Windows GUI evidence fixes the VP2 backend deterministically."""
        log_path = Path("build/gui-2024.log")
        base = ["--maya_version", "2024", "--log_path", str(log_path)]
        with mock.patch.object(RELEASE_GATE.platform, "system", return_value="Windows"):
            self.assertEqual(
                _gui_test_args(version="2024", log_path=log_path, full_gui=True),
                [*base, "--vp2_device_override", "VirtualDeviceDx11"],
            )
            self.assertEqual(
                _gui_test_args(version="2024", log_path=log_path, full_gui=False),
                [
                    *base,
                    "--test_path",
                    "tests/gui",
                    "--test_filter",
                    "tests.gui.guitest_export_tab_gui",
                ],
            )
        with mock.patch.object(RELEASE_GATE.platform, "system", return_value="Linux"):
            self.assertEqual(
                _gui_test_args(version="2024", log_path=log_path, full_gui=True),
                base,
            )

    def test_maya_vmd_probe_explicitly_acknowledges_bake_timeline_export(self):
        """The Maya probe explicitly acknowledges the fixed Bake Timeline export."""
        function = ast.parse(inspect.getsource(_run_vmd_case))
        execute = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        )
        keyword_values = {keyword.arg: keyword.value for keyword in execute.keywords}
        self.assertIs(ast.literal_eval(keyword_values["acknowledge_warnings"]), True)

        request = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ExportWorkflowRequest"
        )
        options = request.args[1]
        self.assertIn(
            "bake_timeline",
            [
                ast.literal_eval(value)
                for key, value in zip(options.keys, options.values)
                if isinstance(key, ast.Constant) and key.value == "export_strategy"
            ],
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "prepare_vmd"
                for node in ast.walk(function)
            )
        )
        option_names = {
            key.value
            for key in options.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertTrue({"current_model_root", "target_model"}.issubset(option_names))

    def test_require_build_path_rejects_paths_outside_build(self):
        with self.assertRaises(ValueError):
            _require_build_path(Path(tempfile.gettempdir()) / "release-summary", "--out-dir")

    def test_not_run_is_explicit(self):
        result = _not_run("gui", "focused gate does not include full GUI")

        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["reason"], "focused gate does not include full GUI")

    def test_release_provenance_is_required_and_utc(self):
        valid = {
            "run_id": "20260809T000000000000Z-deadbeef",
            "timestamp": "2026-08-09T00:00:00+00:00",
            "branch": "Feature/v070-export",
            "head_sha": "a" * 40,
            "dirty": False,
            "git_capture": {"branch": True, "head_sha": True, "status": True},
        }
        self.assertEqual(_validate_release_provenance(valid), [])
        invalid = {**valid, "head_sha": "short", "dirty": "false"}
        failures = _validate_release_provenance(invalid)
        self.assertIn("provenance.head_sha must be a full SHA-1", failures)
        self.assertIn("provenance.dirty must be boolean", failures)

    def test_release_provenance_handles_detached_branch_and_independent_status(self):
        def git_result(command, **_kwargs):
            if "symbolic-ref" in command:
                return mock.Mock(returncode=1, stdout="", stderr="")
            if "rev-parse" in command:
                return mock.Mock(returncode=1, stdout="", stderr="head failed")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(RELEASE_GATE.subprocess, "run", side_effect=git_result):
            provenance = _capture_release_provenance()

        self.assertEqual(provenance["branch"], "DETACHED")
        self.assertFalse(provenance["dirty"])
        self.assertTrue(provenance["git_capture"]["branch"])
        self.assertFalse(provenance["git_capture"]["head_sha"])
        self.assertTrue(provenance["git_capture"]["status"])

    def test_release_provenance_reuses_one_run_id_for_end_snapshot(self):
        provenance = _capture_release_provenance(run_id="run-identity")

        self.assertEqual(provenance["run_id"], "run-identity")

    def test_binding_artifact_rejects_malformed_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "binding.json"
            runtime_path = Path(directory) / "mmd_runtime_ffi.test"
            runtime_path.write_bytes(b"runtime")
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "pass",
                        "model": str(RELEASE_GATE.ROOT / "tests/data/mmt_test_model.pmx"),
                        "motion": str(RELEASE_GATE.ROOT / "tests/data/mmt_test_model_test_motion.vmd"),
                        "runtime_library": str(runtime_path),
                                "report": {"schema_version": 1, "status": "blocked", "issues": [{}]},
                    }
                ),
                encoding="utf-8",
            )
            step = {"name": "mmd_anim_binding_gate", "status": "pass"}
            _validate_binding_gate_artifact(
                step,
                report_path,
                runtime_path=runtime_path,
                runtime_sha256=RELEASE_GATE._sha256(runtime_path),
            )

        self.assertEqual(step["status"], "fail")
        self.assertIn("report.valid/status", step["error"])

    def test_binding_artifact_rejects_binding_root_or_frame_mismatch(self):
        runtime_directory = tempfile.TemporaryDirectory()
        self.addCleanup(runtime_directory.cleanup)
        runtime_path = Path(runtime_directory.name) / "mmd_runtime_ffi.test"
        runtime_path.write_bytes(b"runtime")
        fake_root = Path(runtime_directory.name) / "root"
        (fake_root / "external/mmd-anim/bindings/python").mkdir(parents=True)
        (fake_root / "tests/data").mkdir(parents=True)
        (fake_root / "tests/data/mmt_test_model.pmx").write_bytes(b"model")
        (fake_root / "tests/data/mmt_test_model_test_motion.vmd").write_bytes(b"motion")
        base_report = {
            "schema_version": 1,
            "status": "pass",
            "binding_root": str(fake_root / "external/mmd-anim/bindings/python"),
            "frame": 0.0,
            "model": str(fake_root / "tests/data/mmt_test_model.pmx"),
            "motion": str(fake_root / "tests/data/mmt_test_model_test_motion.vmd"),
            "runtime_library": str(runtime_path),
            "report": {"schema_version": 1, "status": "ready", "issues": []},
        }
        for field, value, expected_error in (
            ("binding_root", str(RELEASE_GATE.ROOT / "external/mmd-anim"), "binding_root mismatch"),
            ("frame", 1.0, "frame must be 0.0"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                report_path = Path(directory) / "binding.json"
                report = {**base_report, field: value}
                report_path.write_text(json.dumps(report), encoding="utf-8")
                step = {"name": "mmd_anim_binding_gate", "status": "pass"}
                with mock.patch.object(RELEASE_GATE, "ROOT", fake_root):
                    _validate_binding_gate_artifact(
                        step,
                        report_path,
                        runtime_path=runtime_path,
                        runtime_sha256=RELEASE_GATE._sha256(runtime_path),
                    )
                self.assertEqual(step["status"], "fail")
                self.assertIn(expected_error, step["error"])

    def test_ffi_build_evidence_rejects_missing_runtime(self):
        step = {"name": "mmd_anim_ffi_build", "status": "pass"}
        with mock.patch.object(RELEASE_GATE, "_mmd_anim_runtime_path", return_value=None):
            runtime_path, runtime_sha, source_revision = _validate_ffi_build_step(step)
        self.assertIsNone(runtime_path)
        self.assertIsNone(runtime_sha)
        self.assertEqual(step["status"], "fail")
        self.assertIn("did not produce", step["error"])

    def test_ffi_runtime_candidates_ignore_foreign_host_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release_dir = root / "external" / "mmd-anim" / "target" / "release"
            release_dir.mkdir(parents=True)
            native = release_dir / "mmd_runtime_ffi.dll"
            foreign = release_dir / "libmmd_runtime_ffi.so"
            native.write_bytes(b"windows-runtime")
            foreign.write_bytes(b"foreign-runtime")
            with mock.patch.object(RELEASE_GATE, "ROOT", root), mock.patch.object(
                RELEASE_GATE.platform, "system", return_value="Windows"
            ):
                self.assertEqual(RELEASE_GATE._mmd_anim_runtime_candidates(), (native,))
                self.assertEqual(RELEASE_GATE._mmd_anim_runtime_path(), native)

    def test_fail_fixture_matrix_is_green_only_when_boundaries_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            result = _run_fail_fixture_matrix(Path(directory))

        self.assertEqual(result["status"], "pass")
        self.assertEqual(
            {fixture["name"] for fixture in result["fixtures"]},
            {
                "invalid_pmx",
                "invalid_vmd_quaternion",
                "warning_ack_boundary",
            },
        )
        for fixture in result["fixtures"]:
            self.assertEqual(fixture["status"], "pass")
        warning_fixture = next(
            fixture for fixture in result["fixtures"] if fixture["name"] == "warning_ack_boundary"
        )
        self.assertEqual(warning_fixture["first_issue_codes"], [])
        self.assertEqual(warning_fixture["first_issue_severities"], [])
        self.assertTrue(warning_fixture["first_succeeded"])
        self.assertFalse(warning_fixture["first_requires_warning_ack"])
        self.assertEqual(len(result["report_paths"]), 4)

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
                    for export_format in (
                        "pmx",
                        "pmd_import",
                        "pmx_morph",
                        "pmx_bone_semantics",
                        "pmx_physics",
                        "pmx_soft_body",
                        "pmx_sdef",
                        "pmx_impulse",
                        "pmx_flip",
                        "vmd",
                        "vmd_bake_timeline_model_tracks",
                        "vmd_bake_timeline_camera_light",
                    )
                ],
            }
            pmd_import_case = next(case for case in report["cases"] if case["format"] == "pmd_import")
            pmd_import_case.update(
                output=None,
                import_oracles={
                    "mesh_count": 1,
                    "vertex_count": 8,
                    "face_count": 12,
                    "material_count": 1,
                    "morph_count": 0,
                    "pose_joint_count": 3,
                    "pose_frame_count": 1,
                    "rigid_body_count": 0,
                    "joint_count": 0,
                    "metadata_field_count": 3,
                },
                collection={
                    "collector": "Maya PMD import pipeline",
                    "source_fresh_import": True,
                    "export_writer_called": False,
                },
            )
            physics_case = next(case for case in report["cases"] if case["format"] == "pmx_physics")
            physics_case.update(
                parsed_counts={"rigid_bodies": 16, "joints": 19},
                input_normalizations=[],
            )
            bone_case = next(case for case in report["cases"] if case["format"] == "pmx_bone_semantics")
            bone_fields = {
                "index": 0,
                "name": "root",
                "name_en": "root",
                "position": [0.0, 0.0, 0.0],
                "parent_index": -1,
                "transform_layer": 2,
                "bone_flag": 0x003E,
                "connect_bone_index": None,
                "connect_position_offset": [0.0, 1.0, 0.0],
                "grant_parent_bone_index": None,
                "grant_rate": None,
                "axis_direction": None,
                "x_axis_direction": None,
                "z_axis_direction": None,
                "key_value": None,
                "ik_target_bone_index": None,
                "ik_loop_count": None,
                "ik_limit_angle": None,
                "ik_links": None,
            }
            bone_payload = {"bones": [bone_fields]}
            bone_case.update(
                parsed_counts={"bones": 1},
                bone_semantics_coverage={
                    "verified_fields": list(RELEASE_GATE.BONE_SEMANTICS_FIELDS),
                    "source_oracle": "PMX parser payload",
                    "maya_oracle": "direct Maya bone metadata attributes",
                },
                bone_semantics={
                    "source": bone_payload,
                    "source_import": bone_payload,
                    "exported_file": bone_payload,
                    "fresh_import": bone_payload,
                    "comparison": {
                        "status": "pass",
                        "boundaries": list(RELEASE_GATE.BONE_SEMANTICS_COMPARISON_BOUNDARIES),
                    },
                },
            )
            morph_case = next(case for case in report["cases"] if case["format"] == "pmx_morph")
            morph_entries = [
                {"index": 0, "name": "vertex", "type": "vertex"},
                {
                    "index": 1,
                    "name": "bone",
                    "name_en": "bone",
                    "type": "bone",
                    "panel": 0,
                    "offsets": [{"bone_index": 0}],
                },
                {
                    "index": 2,
                    "name": "uv",
                    "name_en": "uv",
                    "type": "uv",
                    "panel": 0,
                    "offsets": [{"vertex_index": 0, "uv_offset": [0.1, 0.2, 0.3, 0.4]}],
                },
                {
                    "index": 3,
                    "name": "additional_uv1",
                    "name_en": "additional_uv1",
                    "type": "additional_uv1",
                    "panel": 0,
                    "offsets": [{"vertex_index": 0, "uv_offset": [0.2, 0.3, 0.4, 0.5]}],
                },
                {
                    "index": 4,
                    "name": "additional_uv2",
                    "name_en": "additional_uv2",
                    "type": "additional_uv2",
                    "panel": 0,
                    "offsets": [{"vertex_index": 0, "uv_offset": [0.3, 0.4, 0.5, 0.6]}],
                },
                {
                    "index": 5,
                    "name": "additional_uv3",
                    "name_en": "additional_uv3",
                    "type": "additional_uv3",
                    "panel": 0,
                    "offsets": [{"vertex_index": 0, "uv_offset": [0.4, 0.5, 0.6, 0.7]}],
                },
                {
                    "index": 6,
                    "name": "additional_uv4",
                    "name_en": "additional_uv4",
                    "type": "additional_uv4",
                    "panel": 0,
                    "offsets": [{"vertex_index": 0, "uv_offset": [0.5, 0.6, 0.7, 0.8]}],
                },
                {
                    "index": 7,
                    "name": "material",
                    "name_en": "material",
                    "type": "material",
                    "panel": 0,
                    "offsets": [{"material_index": 0}],
                },
                {
                    "index": 8,
                    "name": "group",
                    "name_en": "group",
                    "type": "group",
                    "panel": 0,
                    "offsets": [{"morph_index": 0, "morph_rate": 1.0}],
                },
            ]
            controller_outputs = {
                str(index): [1.0 if index == output else 0.0 for output in range(9)]
                for index in range(9)
            }
            additional_uvs = {
                "channel_count": 4,
                "vertices": [
                    [
                        [0.1, 0.2, 0.3, 0.4],
                        [0.2, 0.3, 0.4, 0.5],
                        [0.3, 0.4, 0.5, 0.6],
                        [0.4, 0.5, 0.6, 0.7],
                    ]
                ],
                "source_indices": [0],
            }
            morph_case.update(
                parsed_counts={"morphs": len(morph_entries)},
                morph_coverage={
                    "verified_types": list(RELEASE_GATE.MORPH_ORACLE_TYPES),
                    "verified_fields": {
                        name: list(fields) for name, fields in RELEASE_GATE.MORPH_ORACLE_FIELDS.items()
                    },
                    "excluded_boundaries": list(RELEASE_GATE.MORPH_ORACLE_EXCLUSIONS),
                    "source_oracle": "PMX parser payload",
                    "scene_oracle": "direct Maya DAG/network attributes and controller outputs",
                    "visual_parity_claimed": False,
                },
                morph_oracle={
                    "source": {
                        "morphs": morph_entries,
                        "additional_uvs": additional_uvs,
                        "vertex_offsets": {
                            "0": [{"vertex_index": 0, "object_space_delta": [0.0, 1.0, 0.0]}]
                        },
                        "controller_outputs": controller_outputs,
                        "unsupported_types": [],
                    },
                    "exported_file": {
                        "morphs": morph_entries,
                        "additional_uvs": additional_uvs,
                        "vertex_offsets": {
                            "0": [{"vertex_index": 0, "object_space_delta": [0.0, 1.0, 0.0]}]
                        },
                        "controller_outputs": controller_outputs,
                        "unsupported_types": [],
                    },
                    "fresh_import": {
                        "morphs": morph_entries,
                        "additional_uvs": additional_uvs,
                        "vertex_meshes": [{"vertex_count": 1, "source_vertex_indices": None}],
                        "vertex_runtime_deltas": {"0": [[0.0, 1.0, 0.0]]},
                        "controller_outputs": controller_outputs,
                        "unsupported_types": [],
                    },
                    "comparison": {
                        "status": "pass",
                        "checked_types": list(RELEASE_GATE.MORPH_ORACLE_TYPES),
                        "boundaries": list(RELEASE_GATE.MORPH_COMPARISON_BOUNDARIES),
                    },
                },
            )
            soft_body_case = next(
                case for case in report["cases"] if case["format"] == "pmx_soft_body"
            )
            vmd_bake_timeline_model_tracks_case = next(
                case for case in report["cases"] if case["format"] == "vmd_bake_timeline_model_tracks"
            )
            vmd_bake_timeline_camera_light_case = next(
                case for case in report["cases"] if case["format"] == "vmd_bake_timeline_camera_light"
            )
            vmd_bake_timeline_model_tracks_case.update(
                export_strategy="bake_timeline",
                parsed_counts={
                    "bone_frames": 2,
                    "morph_frames": 2,
                    "ik_show_hide_frames": 2,
                    "camera_frames": 0,
                    "light_frames": 0,
                    "shadow_frames": 0,
                },
                bake_timeline_warning_acknowledged=True,
                track_coverage={
                    "checked_frames": [0, 6, 10, 12, 20],
                    "tracks": list(RELEASE_GATE.VMD_BAKE_TIMELINE_MODEL_TRACKS),
                    "source_counts": {
                        "bone_frames": 1,
                        "morph_frames": 1,
                        "ik_show_hide_frames": 1,
                    },
                    "exported_counts": {
                        "bone_frames": 2,
                        "morph_frames": 2,
                        "ik_show_hide_frames": 2,
                    },
                    "bone_track_names": ["bone"],
                    "morph_track_names": ["morph"],
                    "ik_track_names": ["ik"],
                    "camera_light_shadow_claimed": False,
                    "visual_parity_claimed": False,
                },
                model_tracks={
                    "source": {
                        "bone_track_names": ["bone"],
                        "morph_track_names": ["morph"],
                        "bone_frame_count": 1,
                        "morph_frame_count": 1,
                        "ik_show_hide_frame_count": 1,
                        "bone_values": {"bone": {"0": {"position": [0.0, 0.0, 0.0]}}},
                        "morph_values": {"morph": {"0": 1.0}},
                        "ik_values": {"0": {"ik": 1}},
                    },
                    "source_import": {
                        "bone_values": {"bone": {"0": [0.0, 0.0, 0.0]}},
                        "morph_values": {"morph": {"0": 1.0, "6": 0.0}},
                        "ik_values": {"0": {"ik": 1}, "6": {"ik": 0}},
                    },
                    "exported_file": {
                        "bone_track_names": ["bone"],
                        "morph_track_names": ["morph"],
                        "bone_frame_count": 2,
                        "morph_frame_count": 2,
                        "ik_show_hide_frame_count": 2,
                        "bone_values": {"bone": {"0": {"position": [0.0, 0.0, 0.0]}}},
                        "morph_values": {"morph": {"0": 1.0}},
                        "ik_values": {"0": {"ik": 1}},
                    },
                    "fresh_import": {
                        "bone_values": {"bone": {"0": [0.0, 0.0, 0.0]}},
                        "morph_values": {"morph": {"0": 1.0, "6": 0.0}},
                        "ik_values": {"0": {"ik": 1}, "6": {"ik": 0}},
                    },
                    "comparison": {
                        "status": "pass",
                        "boundaries": list(
                            RELEASE_GATE.VMD_BAKE_TIMELINE_MODEL_TRACK_COMPARISON_BOUNDARIES
                        ),
                        "checked_frames": [0, 6, 10, 12, 20],
                        "raw_key_interpolation_preserved": False,
                    },
                },
            )
            camera_payload = {
                "camera": {
                    "0": {"distance": -45.0, "position": [0.0, 10.0, 0.0], "rotation": [0.0, 0.0, 0.0], "viewing_angle": 30, "perspective": 0, "interpolation": [20] * 24},
                    "30": {"distance": -60.0, "position": [5.0, 15.0, -3.0], "rotation": [0.2, 0.5, 0.0], "viewing_angle": 27, "perspective": 0, "interpolation": [20] * 24},
                    "60": {"distance": -45.0, "position": [0.0, 10.0, 0.0], "rotation": [0.0, 0.0, 0.0], "viewing_angle": 30, "perspective": 0, "interpolation": [20] * 24},
                },
                "light": {
                    "0": {"color": [0.6, 0.6, 0.6], "position": [-0.5, -1.0, 0.5]},
                    "30": {"color": [1.0, 0.8, 0.6], "position": [-0.3, -0.8, 0.7]},
                    "60": {"color": [0.6, 0.6, 0.6], "position": [-0.5, -1.0, 0.5]},
                },
            }
            for payload in camera_payload["light"].values():
                payload["direction"] = list(payload["position"])
            camera_scene_payload = {
                "camera": {
                    frame: {key: value for key, value in payload.items() if key != "interpolation"}
                    for frame, payload in camera_payload["camera"].items()
                },
                "light": camera_payload["light"],
            }
            dense_camera_payload = {
                str(frame): dict(camera_payload["camera"][str(frame if frame in (0, 30, 60) else 0)])
                for frame in (0, 15, 30, 45, 60)
            }
            dense_light_payload = {
                str(frame): dict(camera_payload["light"][str(frame if frame in (0, 30, 60) else 0)])
                for frame in (0, 15, 30, 45, 60)
            }
            dense_payload = {"camera": dense_camera_payload, "light": dense_light_payload}
            vmd_bake_timeline_camera_light_case.update(
                export_strategy="bake_timeline",
                parsed_counts={
                    "camera_frames": 3,
                    "light_frames": 3,
                    "bone_frames": 0,
                    "morph_frames": 0,
                    "ik_show_hide_frames": 0,
                    "shadow_frames": 0,
                },
                normalization={"excluded_shadow_frames": 1, "shadow_support_claimed": False},
                bake_timeline_warning_acknowledged=True,
                track_coverage={
                    "checked_frames": [0, 15, 30, 45, 60],
                    "tracks": list(RELEASE_GATE.VMD_BAKE_TIMELINE_CAMERA_LIGHT_TRACKS),
                    "source_counts": {"camera_frames": 3, "light_frames": 3},
                    "exported_counts": {"camera_frames": 61, "light_frames": 61},
                    "bone_frames": 0,
                    "morph_frames": 0,
                    "ik_show_hide_frames": 0,
                    "shadow_frames": 0,
                    "visual_parity_claimed": False,
                },
                camera_light={
                    "source": camera_payload,
                    "source_import": camera_scene_payload,
                    "exported_file": camera_payload,
                    "fresh_import": camera_scene_payload,
                    "interpolation": {
                        "source": {frame: payload["interpolation"] for frame, payload in camera_payload["camera"].items()},
                        "exported_file": {frame: [20] * 24 for frame in camera_payload["camera"]},
                        "bake_timeline_normalized": True,
                        "canonical_expected": [20] * 24,
                        "canonical_length": 24,
                        "canonical_exported": True,
                    },
                    "comparison": {
                        "status": "pass",
                        "boundaries": list(
                            RELEASE_GATE.VMD_BAKE_TIMELINE_CAMERA_LIGHT_COMPARISON_BOUNDARIES
                        ),
                        "checked_frames": [0, 30, 60],
                        "dense_checked_frames": [0, 15, 30, 45, 60],
                        "dense_status": "pass",
                    },
                    "dense": {
                        "checked_frames": [0, 15, 30, 45, 60],
                        "native_expected": copy.deepcopy(dense_payload),
                        "native_comparison_tracks": ["camera"],
                        "light_comparison": "source_import/fresh_import",
                        "source_import": copy.deepcopy(dense_payload),
                        "exported_file": copy.deepcopy(dense_payload),
                        "fresh_import": copy.deepcopy(dense_payload),
                    },
                },
            )
            soft_body_case.update(
                status="policy-reject",
                policy_code="PMX_SOFT_BODIES_UNSUPPORTED",
                import_oracles={"soft_body_count": 1},
                output=None,
                collection={
                    "source_fresh_import": True,
                    "export_writer_called": False,
                },
            )
            sdef_case = next(case for case in report["cases"] if case["format"] == "pmx_sdef")
            sdef_case.update(
                status="pass",
                output=str(root / "pmx_sdef" / "model.pmx"),
                import_oracles={
                    "source_sdef_vertex_count": 1,
                    "fresh_import_vertex_count": 8,
                    "fresh_import_skin_cluster_count": 1,
                    "fresh_import_influence_count": 2,
                    "fresh_import_weight_value_count": 16,
                    "fresh_import_finite_weight_value_count": 16,
                    "fresh_import_normalized_vertex_count": 8,
                    "fresh_import_weight_sum_min": 1.0,
                    "fresh_import_weight_sum_max": 1.0,
                    "exported_vertex_count": 8,
                    "exported_bdef4_vertex_count": 8,
                    "exported_non_bdef4_vertex_count": 0,
                },
                collection={
                    "source_fresh_import": True,
                    "export_writer_called": True,
                },
            )
            for export_format, policy_code, prefix in (
                ("pmx_impulse", "MORPH_TYPE_UNSUPPORTED", "impulse"),
                ("pmx_flip", "MORPH_TYPE_UNSUPPORTED", "flip"),
            ):
                policy_case = next(case for case in report["cases"] if case["format"] == export_format)
                policy_case.update(
                    status="policy-reject",
                    policy_code=policy_code,
                    import_oracles={
                        f"source_{prefix}_morph_count": 1,
                        f"fresh_import_{prefix}_morph_count": 1,
                        "provenance_offset_count": 1,
                        f"collected_{prefix}_morph_count": 1,
                    },
                    collection={
                        "source_fresh_import": True,
                        "export_writer_called": False,
                    },
                    output_safety={
                        "target_existed_before": True,
                        "target_exists_after": True,
                        "created": False,
                        "overwritten": False,
                        "preserved": True,
                        "writer_called": False,
                    },
                )
            report_path.write_text(json.dumps(report), encoding="utf-8")

            step = {"name": "maya_probe_2024", "status": "pass"}
            report_paths = _validate_maya_probe_report(step, report_path, "2024")

            self.assertEqual(step["status"], "pass")
            self.assertEqual(
                {path.parent.name for path in report_paths},
                {
                    "pmx",
                    "pmd_import",
                    "pmx_morph",
                    "pmx_bone_semantics",
                    "pmx_physics",
                    "pmx_soft_body",
                    "pmx_sdef",
                    "pmx_impulse",
                    "pmx_flip",
                    "vmd",
                    "vmd_bake_timeline_model_tracks",
                    "vmd_bake_timeline_camera_light",
                },
            )

            missing_bone_import = bone_case["bone_semantics"].pop("fresh_import")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("pmx_bone_semantics.bone_semantics.fresh_import_missing", step["error"])
            bone_case["bone_semantics"]["fresh_import"] = missing_bone_import

            exported_file = morph_case["morph_oracle"].pop("exported_file")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("exported_file_missing", step["error"])
            morph_case["morph_oracle"]["exported_file"] = exported_file

            morph_case["morph_coverage"]["visual_parity_claimed"] = True
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("visual_parity_claimed must be false", step["error"])
            morph_case["morph_coverage"]["visual_parity_claimed"] = False

            soft_body_case["collection"] = {
                "source_fresh_import": False,
                "export_writer_called": True,
            }
            soft_body_case["output"] = str(root / "pmx_soft_body" / "model.pmx")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("source_fresh_import must be true", step["error"])
            self.assertIn("export_writer_called must be false", step["error"])
            self.assertIn("output must be null", step["error"])

            soft_body_case["collection"] = {
                "source_fresh_import": True,
                "export_writer_called": False,
            }
            soft_body_case["output"] = None

            morph_case["morph_oracle"]["fresh_import"]["controller_outputs"] = {}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("controller_outputs", step["error"])
            morph_case["morph_oracle"]["fresh_import"]["controller_outputs"] = controller_outputs

            vmd_bake_timeline_model_tracks_case["model_tracks"]["fresh_import"]["morph_values"] = {}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_bake_timeline_model_tracks.model_tracks.fresh_import.morph_values_missing", step["error"])
            vmd_bake_timeline_model_tracks_case["model_tracks"]["fresh_import"]["morph_values"] = {
                "morph": {"0": 1.0, "6": 0.0}
            }
            vmd_bake_timeline_model_tracks_case["model_tracks"]["fresh_import"]["ik_values"] = {}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_bake_timeline_model_tracks.model_tracks.fresh_import.ik_values_missing", step["error"])
            vmd_bake_timeline_model_tracks_case["model_tracks"]["fresh_import"]["ik_values"] = {
                "0": {"ik": 1},
                "6": {"ik": 0},
            }

            vmd_bake_timeline_camera_light_case["parsed_counts"]["camera_frames"] = 0
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_bake_timeline_camera_light.parsed_counts.camera_frames must be positive", step["error"])
            vmd_bake_timeline_camera_light_case["parsed_counts"]["camera_frames"] = 61

            vmd_bake_timeline_camera_light_case["normalization"]["excluded_shadow_frames"] = 0
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("excluded_shadow_frames must be positive", step["error"])
            vmd_bake_timeline_camera_light_case["normalization"]["excluded_shadow_frames"] = 1

            vmd_bake_timeline_camera_light_case["camera_light"]["interpolation"]["exported_file"]["0"] = [20] * 23
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("must contain 24 bytes", step["error"])
            vmd_bake_timeline_camera_light_case["camera_light"]["interpolation"]["exported_file"]["0"] = [20] * 24

            vmd_bake_timeline_camera_light_case["camera_light"]["dense"]["source_import"]["camera"]["15"]["distance"] += 0.01
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("native_expected_vs_source_import.camera[15].distance mismatch", step["error"])
            vmd_bake_timeline_camera_light_case["camera_light"]["dense"]["source_import"]["camera"]["15"]["distance"] -= 0.01

            missing_direction = vmd_bake_timeline_camera_light_case["camera_light"]["dense"]["fresh_import"]["light"]["15"].pop("direction")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("fresh_import.light[15].direction missing", step["error"])
            vmd_bake_timeline_camera_light_case["camera_light"]["dense"]["fresh_import"]["light"]["15"]["direction"] = missing_direction

            report["cases"][-1]["status"] = "fail"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")

    def test_release_summary_keeps_cli_and_submodule_provenance_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "release"
            runtime_path = Path(directory) / "mmd_runtime_ffi.test"
            runtime_path.write_bytes(b"runtime")
            fake_root = Path(directory) / "root"
            (fake_root / "external/mmd-anim/bindings/python").mkdir(parents=True)
            (fake_root / "tests/data").mkdir(parents=True)
            (fake_root / "tests/data/mmt_test_model.pmx").write_bytes(b"model")
            (fake_root / "tests/data/mmt_test_model_test_motion.vmd").write_bytes(b"motion")

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
                if name == "mmd_anim_binding_gate":
                    report_path = Path(command[-1])
                    report_path.write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "status": "pass",
                                "binding_root": str(fake_root / "external/mmd-anim/bindings/python"),
                                "frame": 0.0,
                                "model": str(fake_root / "tests/data/mmt_test_model.pmx"),
                                "motion": str(fake_root / "tests/data/mmt_test_model_test_motion.vmd"),
                                "runtime_library": str(runtime_path),
                                "report": {"schema_version": 1, "status": "ready", "issues": []},
                            }
                        ),
                        encoding="utf-8",
                    )
                return {"name": name, "status": "pass", "returncode": 0}

            with mock.patch.object(
                RELEASE_GATE,
                "_capture_release_provenance",
                side_effect=_clean_release_provenance,
            ), mock.patch.object(
                RELEASE_GATE,
                "ROOT",
                fake_root,
            ), mock.patch.object(
                RELEASE_GATE,
                "_mmd_anim_runtime_path",
                return_value=runtime_path,
            ), mock.patch.object(
                RELEASE_GATE,
                "_mmd_anim_source_revision",
                return_value="a" * 40,
            ), mock.patch.object(
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
            self.assertIn("ui_headless_tests", summary["unexecuted"])
            self.assertEqual(summary["gui_scope"], "not_run")
            self.assertIsInstance(summary["provenance"]["start"]["dirty"], bool)
            self.assertIn("end", summary["provenance"])
            self.assertEqual(summary["run_id"], summary["provenance"]["start"]["run_id"])
            self.assertEqual(
                summary["provenance"]["start"]["run_id"],
                summary["provenance"]["end"]["run_id"],
            )
            self.assertEqual(summary["mmd_anim_provenance"]["binding"]["gate_status"], "pass")
            self.assertTrue(any(step["name"] == "mmd_anim_python_bindings" for step in summary["steps"]))
            self.assertTrue(any(step["name"] == "mmd_anim_binding_gate" for step in summary["steps"]))
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

    def test_release_summary_marks_targeted_gui_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "release"
            runtime_path = Path(directory) / "mmd_runtime_ffi.test"
            runtime_path.write_bytes(b"runtime")
            fake_root = Path(directory) / "root"
            (fake_root / "external/mmd-anim/bindings/python").mkdir(parents=True)
            (fake_root / "tests/data").mkdir(parents=True)
            (fake_root / "tests/data/mmt_test_model.pmx").write_bytes(b"model")
            (fake_root / "tests/data/mmt_test_model_test_motion.vmd").write_bytes(b"motion")

            def run_command(name, command, **_kwargs):
                if name == "mmd_anim_binding_gate":
                    Path(command[-1]).write_text(
                        json.dumps(
                            {
                                "schema_version": 1,
                                "status": "pass",
                                "binding_root": str(fake_root / "external/mmd-anim/bindings/python"),
                                "frame": 0.0,
                                "model": str(fake_root / "tests/data/mmt_test_model.pmx"),
                                "motion": str(fake_root / "tests/data/mmt_test_model_test_motion.vmd"),
                                "runtime_library": str(runtime_path),
                                "report": {"schema_version": 1, "status": "ready", "issues": []},
                            }
                        ),
                        encoding="utf-8",
                    )
                return {"name": name, "status": "pass", "returncode": 0}

            with mock.patch.object(
                RELEASE_GATE,
                "_capture_release_provenance",
                side_effect=_clean_release_provenance,
            ), mock.patch.object(
                RELEASE_GATE,
                "ROOT",
                fake_root,
            ), mock.patch.object(
                RELEASE_GATE,
                "_mmd_anim_runtime_path",
                return_value=runtime_path,
            ), mock.patch.object(
                RELEASE_GATE,
                "_mmd_anim_source_revision",
                return_value="a" * 40,
            ), mock.patch.object(
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
                    mmd_anim_cli=None,
                    skip_gui=False,
                    full_gui=False,
                    skip_focused_tests=True,
                )

            self.assertEqual(summary["gui_scope"], "targeted")
            self.assertIn("mmd_anim_validation", summary["unexecuted"])
            self.assertEqual(summary["mmd_anim_provenance"]["binding"]["gate_status"], "pass")

    def test_release_summary_fails_when_dirty_at_start(self):
        base = {
            "run_id": "run",
            "timestamp": "2026-08-09T00:00:00+00:00",
            "branch": "Feature/v070-export",
            "head_sha": "a" * 40,
            "git_capture": {"branch": True, "head_sha": True, "status": True},
        }
        start = {**base, "dirty": True}
        end = {**base, "dirty": False}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                RELEASE_GATE,
                "_capture_release_provenance",
                side_effect=[start, end],
            ), mock.patch.object(RELEASE_GATE, "_maya_path", return_value=Path("missing-mayapy")), mock.patch.object(
                RELEASE_GATE,
                "_run_fail_fixture_matrix",
                return_value={"status": "pass", "fixtures": [], "report_paths": []},
            ), mock.patch.object(
                RELEASE_GATE,
                "_run_command",
                side_effect=lambda name, _command, **_kwargs: {
                    "name": name,
                    "status": "pass",
                    "returncode": 0,
                },
            ), mock.patch.object(
                RELEASE_GATE,
                "_report_consistency_step",
                return_value={"name": "report_consistency", "status": "pass", "checked": [], "failures": []},
            ):
                summary = RELEASE_GATE.build_release_summary(
                    out_dir=Path(directory) / "release",
                    maya_versions=("2024",),
                    mmd_anim_cli=None,
                    skip_gui=True,
                    full_gui=False,
                    skip_focused_tests=True,
                )
        self.assertEqual(summary["status"], "fail")
        self.assertTrue(any(item["name"] == "release_provenance" for item in summary["blockers"]))

    def test_release_summary_fails_when_head_or_dirty_changes_mid_run(self):
        base = {
            "run_id": "run",
            "timestamp": "2026-08-09T00:00:00+00:00",
            "branch": "Feature/v070-export",
            "git_capture": {"branch": True, "head_sha": True, "status": True},
        }
        start = {**base, "head_sha": "a" * 40, "dirty": False}
        end = {**base, "head_sha": "b" * 40, "dirty": True}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                RELEASE_GATE,
                "_capture_release_provenance",
                side_effect=[start, end],
            ), mock.patch.object(RELEASE_GATE, "_maya_path", return_value=Path("missing-mayapy")), mock.patch.object(
                RELEASE_GATE,
                "_run_fail_fixture_matrix",
                return_value={"status": "pass", "fixtures": [], "report_paths": []},
            ), mock.patch.object(
                RELEASE_GATE,
                "_run_command",
                side_effect=lambda name, _command, **_kwargs: {
                    "name": name,
                    "status": "pass",
                    "returncode": 0,
                },
            ), mock.patch.object(
                RELEASE_GATE,
                "_report_consistency_step",
                return_value={"name": "report_consistency", "status": "pass", "checked": [], "failures": []},
            ):
                summary = RELEASE_GATE.build_release_summary(
                    out_dir=Path(directory) / "release",
                    maya_versions=("2024",),
                    mmd_anim_cli=None,
                    skip_gui=True,
                    full_gui=False,
                    skip_focused_tests=True,
                )
        self.assertEqual(summary["status"], "fail")
        self.assertTrue(any(item["name"] == "release_provenance" for item in summary["blockers"]))

    def test_release_summary_rejects_empty_maya_version_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                RELEASE_GATE,
                "_run_fail_fixture_matrix",
                return_value={"status": "pass", "fixtures": [], "report_paths": []},
            ), mock.patch.object(
                RELEASE_GATE,
                "_run_command",
                side_effect=lambda name, _command, **_kwargs: {
                    "name": name,
                    "status": "pass",
                    "returncode": 0,
                },
            ), mock.patch.object(
                RELEASE_GATE,
                "_report_consistency_step",
                return_value={"name": "report_consistency", "status": "pass", "checked": [], "failures": []},
            ):
                summary = RELEASE_GATE.build_release_summary(
                    out_dir=Path(directory) / "release",
                    maya_versions=(),
                    mmd_anim_cli=None,
                    skip_gui=True,
                    full_gui=False,
                    skip_focused_tests=True,
                )
        self.assertEqual(summary["status"], "fail")
        self.assertTrue(any(item["name"] == "maya_versions" for item in summary["blockers"]))

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

    def test_scene_oracle_applies_explicit_tolerance_only_to_pose(self):
        source = {
            "metadata": {},
            "pose": {
                "joint_count": 1,
                "frames": {"0": [{"name": "center", "translation": [0.0, 0.0, 0.0]}]},
            },
        }
        within_tolerance = {
            "metadata": {},
            "pose": {
                "joint_count": 1,
                "frames": {"0": [{"name": "center", "translation": [0.0049, 0.0, 0.0]}]},
            },
        }
        beyond_tolerance = {
            "metadata": {},
            "pose": {
                "joint_count": 1,
                "frames": {"0": [{"name": "center", "translation": [0.0051, 0.0, 0.0]}]},
            },
        }

        assert _compare_scene_oracles(
            source,
            within_tolerance,
            pose=True,
            mesh=False,
            materials=False,
        )
        assert _compare_scene_oracles(
            source,
            within_tolerance,
            pose=True,
            pose_tolerance=5e-3,
            mesh=False,
            materials=False,
        ) == []
        assert _compare_scene_oracles(
            source,
            beyond_tolerance,
            pose=True,
            pose_tolerance=5e-3,
            mesh=False,
            materials=False,
        )

    def test_scene_oracle_derives_optional_edge_flag_from_pmx_draw_flags(self):
        """A redundant Maya edge flag may be absent after a PMX fresh import."""
        material = {
            "index": 0,
            "draw_flags": 0x10,
            "edge_flag": 1,
            "diffuse": [],
            "specular": [],
            "ambient": [],
            "edge_color": [],
            "edge_size": None,
            "shininess": None,
        }
        source = {"materials": [material], "metadata": {}, "pose": {}}
        actual = {
            "materials": [{**material, "edge_flag": None}],
            "metadata": {},
            "pose": {},
        }

        assert _compare_scene_oracles(source, actual, pose=False, mesh=False) == []

    def test_scene_oracle_detects_physics_semantic_drift(self):
        source = {
            "metadata": {"mmd_file_type": "pmx", "mmd_model_name": "fixture"},
            "physics": {
                "rigid_bodies": [
                    {
                        "pmx_index": 0,
                        "name": "hair",
                        "name_en": "Hair",
                        "related_bone_index": 2,
                        "group": 1,
                        "collision_mask": 65534,
                        "shape_type": 0,
                        "physics_mode": 2,
                        "size": [0.2, 0.3, 0.4],
                        "position": [1.0, 2.0, 3.0],
                        "rotation": [0.0, 0.1, 0.2],
                        "mass": 0.5,
                        "velocity_attenuation": 0.1,
                        "rotation_attenuation": 0.2,
                        "elasticity": 0.3,
                        "friction": 0.4,
                    }
                ],
                "joints": [
                    {
                        "pmx_index": 0,
                        "name": "joint",
                        "name_en": "Joint",
                        "joint_type": 0,
                        "rigid_body_a_index": 0,
                        "rigid_body_b_index": 1,
                        "position": [0.0, 1.0, 2.0],
                        "rotation": [0.1, 0.2, 0.3],
                        "translation_limit_min": [-1.0, -1.0, -1.0],
                        "translation_limit_max": [1.0, 1.0, 1.0],
                        "rotation_limit_min": [-0.1, -0.2, -0.3],
                        "rotation_limit_max": [0.1, 0.2, 0.3],
                        "spring_translation": [0.0, 0.0, 0.0],
                        "spring_rotation": [0.0, 0.0, 0.0],
                    }
                ],
            },
        }
        actual = {
            **source,
            "physics": {
                **source["physics"],
                "rigid_bodies": [
                    {
                        **source["physics"]["rigid_bodies"][0],
                        "mass": 0.75,
                    }
                ],
            },
        }

        failures = _compare_scene_oracles(source, actual, pose=False, mesh=False, materials=False, physics=True)

        self.assertTrue(any("physics.rigid_bodies[0].mass" in failure for failure in failures))

    def test_scene_oracle_detects_morph_runtime_drift(self):
        source = {
            "metadata": {"mmd_file_type": "pmx", "mmd_model_name": "fixture"},
            "morphs": {
                "morphs": [{"index": 0, "name": "smile", "type": "vertex"}],
                "vertex_offsets": {
                    "0": [{"vertex_index": 0, "object_space_delta": [0.0, 1.0, 2.0]}]
                },
                "controller_outputs": {"0": [1.0]},
                "unsupported_types": [],
            },
        }
        actual = {
            **source,
            "morphs": {
                "morphs": [{"index": 0, "name": "smile", "type": "vertex"}],
                "vertex_meshes": [{"vertex_count": 1, "source_vertex_indices": None}],
                "vertex_runtime_deltas": {"0": [[0.0, 1.0, 2.25]]},
                "controller_outputs": {"0": [1.0]},
                "unsupported_types": [],
            },
        }

        failures = _compare_scene_oracles(source, actual, pose=False, mesh=False, materials=False, morphs=True)

        self.assertTrue(any("morphs[0] mesh[0] vertices max error" in failure for failure in failures))


    def test_main_defaults_to_release_complete_full_gui(self):
        summary = {"status": "pass", "unexecuted": []}
        with mock.patch.object(
            RELEASE_GATE, "build_release_summary", return_value=summary
        ) as build_summary, mock.patch.object(
            RELEASE_GATE, "_require_build_path", return_value=Path("build/release")
        ):
            self.assertEqual(
                RELEASE_GATE.main(["--out-dir", "build/release", "--mmd-anim-cli", "runtime"]),
                0,
            )

        self.assertTrue(build_summary.call_args.kwargs["full_gui"])

    def test_main_targeted_gui_is_explicitly_incomplete_scope(self):
        summary = {"status": "fail", "unexecuted": ["ui_coverage_gate"]}
        with mock.patch.object(
            RELEASE_GATE, "build_release_summary", return_value=summary
        ) as build_summary, mock.patch.object(
            RELEASE_GATE, "_require_build_path", return_value=Path("build/release")
        ):
            self.assertEqual(
                RELEASE_GATE.main(
                    [
                        "--out-dir",
                        "build/release",
                        "--mmd-anim-cli",
                        "runtime",
                        "--targeted-gui",
                    ]
                ),
                1,
            )

        self.assertFalse(build_summary.call_args.kwargs["full_gui"])


if __name__ == "__main__":
    unittest.main()
