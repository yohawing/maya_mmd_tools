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
    _validate_maya_probe_report,
)
from tools.export_release_maya_probe import _compare_scene_oracles, _run_vmd_case


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

    def test_maya_vmd_probe_explicitly_acknowledges_mode_c_warning(self):
        """The Maya probe accepts the explicit Mode C raw-provenance warning."""
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
            "C",
            [
                ast.literal_eval(value)
                for key, value in zip(options.keys, options.values)
                if isinstance(key, ast.Constant) and key.value == "vmd_mode"
            ],
        )

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
        warning_fixture = next(
            fixture for fixture in result["fixtures"] if fixture["name"] == "warning_ack_boundary"
        )
        self.assertEqual(warning_fixture["first_issue_codes"], ["VMD_MODE_C_RAW_LOSS"])
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
                    for export_format in (
                        "pmx",
                        "pmx_morph",
                        "pmx_bone_semantics",
                        "pmx_physics",
                        "pmx_soft_body",
                        "pmx_sdef",
                        "pmx_impulse",
                        "pmx_flip",
                        "pmd",
                        "vmd",
                        "vmd_model_tracks",
                        "vmd_camera_light",
                    )
                ],
            }
            pmd_case = next(case for case in report["cases"] if case["format"] == "pmd")
            pmd_case.update(
                status="policy-reject",
                policy_code="PMD_EXPORT_POLICY_REJECT",
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
            vmd_model_tracks_case = next(
                case for case in report["cases"] if case["format"] == "vmd_model_tracks"
            )
            vmd_camera_light_case = next(
                case for case in report["cases"] if case["format"] == "vmd_camera_light"
            )
            vmd_model_tracks_case.update(
                parsed_counts={
                    "bone_frames": 2,
                    "morph_frames": 2,
                    "ik_show_hide_frames": 2,
                    "camera_frames": 0,
                    "light_frames": 0,
                    "shadow_frames": 0,
                },
                mode_c_warning_acknowledged=True,
                track_coverage={
                    "checked_frames": [0, 6, 10, 12, 20],
                    "tracks": list(RELEASE_GATE.VMD_MODEL_TRACKS),
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
                        "boundaries": list(RELEASE_GATE.VMD_MODEL_TRACK_COMPARISON_BOUNDARIES),
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
            vmd_camera_light_case.update(
                parsed_counts={
                    "camera_frames": 3,
                    "light_frames": 3,
                    "bone_frames": 0,
                    "morph_frames": 0,
                    "ik_show_hide_frames": 0,
                    "shadow_frames": 0,
                },
                normalization={"excluded_shadow_frames": 1, "shadow_support_claimed": False},
                mode_c_warning_acknowledged=True,
                track_coverage={
                    "checked_frames": [0, 15, 30, 45, 60],
                    "tracks": list(RELEASE_GATE.VMD_CAMERA_LIGHT_TRACKS),
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
                        "raw_preserved": False,
                        "mode_c_normalized": True,
                        "canonical_expected": [20] * 24,
                        "canonical_length": 24,
                        "canonical_exported": True,
                    },
                    "comparison": {
                        "status": "pass",
                        "boundaries": list(RELEASE_GATE.VMD_CAMERA_LIGHT_COMPARISON_BOUNDARIES),
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
            for export_format, policy_code, prefix in (
                ("pmx_sdef", "PMX_VERTEX_SDEF_UNSUPPORTED", "sdef"),
                ("pmx_impulse", "MORPH_TYPE_UNSUPPORTED", "impulse"),
                ("pmx_flip", "MORPH_TYPE_UNSUPPORTED", "flip"),
            ):
                policy_case = next(case for case in report["cases"] if case["format"] == export_format)
                policy_case.update(
                    status="policy-reject",
                    policy_code=policy_code,
                    import_oracles={
                        f"source_{prefix}_{'vertex' if prefix == 'sdef' else 'morph'}_count": 1,
                        f"fresh_import_{prefix}_{'vertex' if prefix == 'sdef' else 'morph'}_count": 1,
                        "provenance_vertex_count" if prefix == "sdef" else "provenance_offset_count": 1,
                        f"collected_{prefix}_{'vertex' if prefix == 'sdef' else 'morph'}_count": 1,
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
                    "pmx_morph",
                    "pmx_bone_semantics",
                    "pmx_physics",
                    "pmx_soft_body",
                    "pmx_sdef",
                    "pmx_impulse",
                    "pmx_flip",
                    "pmd",
                    "vmd",
                    "vmd_model_tracks",
                    "vmd_camera_light",
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

            vmd_model_tracks_case["model_tracks"]["fresh_import"]["morph_values"] = {}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_model_tracks.model_tracks.fresh_import.morph_values_missing", step["error"])
            vmd_model_tracks_case["model_tracks"]["fresh_import"]["morph_values"] = {
                "morph": {"0": 1.0, "6": 0.0}
            }
            vmd_model_tracks_case["model_tracks"]["fresh_import"]["ik_values"] = {}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_model_tracks.model_tracks.fresh_import.ik_values_missing", step["error"])
            vmd_model_tracks_case["model_tracks"]["fresh_import"]["ik_values"] = {
                "0": {"ik": 1},
                "6": {"ik": 0},
            }

            vmd_camera_light_case["parsed_counts"]["camera_frames"] = 0
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("vmd_camera_light.parsed_counts.camera_frames must be positive", step["error"])
            vmd_camera_light_case["parsed_counts"]["camera_frames"] = 61

            vmd_camera_light_case["normalization"]["excluded_shadow_frames"] = 0
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("excluded_shadow_frames must be positive", step["error"])
            vmd_camera_light_case["normalization"]["excluded_shadow_frames"] = 1

            vmd_camera_light_case["camera_light"]["interpolation"]["exported_file"]["0"] = [20] * 23
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("must contain 24 bytes", step["error"])
            vmd_camera_light_case["camera_light"]["interpolation"]["exported_file"]["0"] = [20] * 24

            vmd_camera_light_case["camera_light"]["dense"]["source_import"]["camera"]["15"]["distance"] += 0.01
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("native_expected_vs_source_import.camera[15].distance mismatch", step["error"])
            vmd_camera_light_case["camera_light"]["dense"]["source_import"]["camera"]["15"]["distance"] -= 0.01

            missing_direction = vmd_camera_light_case["camera_light"]["dense"]["fresh_import"]["light"]["15"].pop("direction")
            report_path.write_text(json.dumps(report), encoding="utf-8")
            step = {"name": "maya_probe_2024", "status": "pass"}
            self.assertEqual(_validate_maya_probe_report(step, report_path, "2024"), [])
            self.assertEqual(step["status"], "fail")
            self.assertIn("fresh_import.light[15].direction missing", step["error"])
            vmd_camera_light_case["camera_light"]["dense"]["fresh_import"]["light"]["15"]["direction"] = missing_direction

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


if __name__ == "__main__":
    unittest.main()
