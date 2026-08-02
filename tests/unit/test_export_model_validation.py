"""Headless tests for PMX/PMD model-data preflight and its export gate."""

import json
import math
from pathlib import Path
import tempfile
import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.export_model_action import (  # noqa: E402
    ExportModelAction,
    ExportModelRequest,
)
from mmd_tools.core.pmd_data.bone import PmdBoneType  # noqa: E402
from mmd_tools.validation.export_validator import (  # noqa: E402
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
    PMD_MAX_BONE_COUNT,
    validate_model_data,
)
from mmd_tools.validation.snapshot import ExportValidationSnapshot, fingerprint_payload  # noqa: E402


def _valid_model_data():
    """Return collector-shaped data accepted by both model writers."""
    return {
        "model_name": "ValidationFixture",
        "vertices": [
            {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0], "bone_indices": [0]},
            {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0], "bone_indices": [0]},
            {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0], "bone_indices": [0]},
        ],
        "faces": [[0, 1, 2]],
        "materials": [{"name": "Default", "diffuse": [0.8, 0.8, 0.8, 1.0], "face_count": 3}],
        "bones": None,
    }


class _FakeExporter:
    def __init__(self):
        self.calls = []

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"fake pmx bytes")

    def export_pmd_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"fake pmd bytes")


class _RaisingExporter(_FakeExporter):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"partial pmx bytes")
        raise self.error

    def export_pmd_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"partial pmd bytes")
        raise self.error


class _SilentExporter(_FakeExporter):
    """Record the writer call without materializing an output file."""

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))

    def export_pmd_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))


class TestExportModelValidation(unittest.TestCase):
    """Verify deterministic findings and fail-closed writer dispatch."""

    def test_valid_pmx_and_pmd_data_passes(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format):
                report = validate_model_data(_valid_model_data(), export_format)
                self.assertTrue(report.valid)
                self.assertFalse(report.issues)

    def test_valid_writer_text_and_pmx_material_morph_scalars_pass(self):
        model_data = _valid_model_data()
        model_data.update(
            {
                "model_name_english": "Validation Fixture",
                "comment": "comment",
                "comment_english": "comment",
                "bones": [{"name": "root", "name_english": "root"}],
                "materials": [
                    {
                        "name": "Default",
                        "name_english": "Default",
                        "memo": "material",
                        "face_count": 3,
                    }
                ],
                "morphs": [
                    {
                        "name": "Material Morph",
                        "name_english": "Material Morph",
                        "panel": 255,
                        "type": "material",
                        "offsets": [
                            {
                                "material_index": 0,
                                "operation_type": 255,
                                "specular_coefficient": 1.5,
                                "edge_size": 0.25,
                            }
                        ],
                    }
                ],
                "display_frames": [
                    {
                        "name": "Root",
                        "name_english": "Root",
                        "special_flag": 1,
                        "elements": [{"type": 0, "index": 0}],
                    }
                ],
                "rigid_bodies": [
                    {"name": "Body", "name_english": "Body", "related_bone_index": 0}
                ],
                "joints": [
                    {
                        "name": "Joint",
                        "name_english": "Joint",
                        "rigid_body_a_index": 0,
                        "rigid_body_b_index": -1,
                    }
                ],
            }
        )

        report = validate_model_data(model_data, "pmx")

        self.assertTrue(report.valid)
        self.assertFalse(report.issues)

    def test_writer_facing_text_fields_and_pmd_texture_name_require_strings(self):
        cases = (
            (lambda data: data.update({"model_name": None}), "model_name", "pmx"),
            (lambda data: data.update({"bones": [{"name": 1}]}), "bones[0].name", "pmx"),
            (
                lambda data: data.update({"morphs": [{"name_english": None, "offsets": []}]}),
                "morphs[0].name_english",
                "pmx",
            ),
            (
                lambda data: data.update({"materials": [{"memo": 1}]}),
                "materials[0].memo",
                "pmx",
            ),
            (
                lambda data: data.update({"display_frames": [{"name": 1}]}),
                "display_frames[0].name",
                "pmx",
            ),
            (
                lambda data: data.update({"rigid_bodies": [{"name_english": 1}]}),
                "rigid_bodies[0].name_english",
                "pmx",
            ),
            (
                lambda data: data.update({"rigid_bodies": [{}], "joints": [{"name": 1}]}),
                "joints[0].name",
                "pmx",
            ),
            (
                lambda data: data.update({"materials": [{"texture_file_name": 1}]}),
                "materials[0].texture_file_name",
                "pmd",
            ),
        )
        for mutate, expected_path, export_format in cases:
            with self.subTest(expected_path=expected_path, export_format=export_format):
                model_data = _valid_model_data()
                mutate(model_data)

                report = validate_model_data(model_data, export_format)

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [("TEXT_FIELD_TYPE", expected_path)],
                )

    def test_pmx_material_morph_scalar_and_byte_fields_are_fail_closed(self):
        cases = (
            ("operation_type", "0", "MORPH_FIELD_TYPE"),
            ("operation_type", 256, "MORPH_FIELD_RANGE"),
            ("specular_coefficient", "1.0", "NUMERIC_VALUE_TYPE"),
            ("edge_size", None, "NUMERIC_VALUE_TYPE"),
        )
        for field_name, value, expected_code in cases:
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["morphs"] = [
                    {
                        "type": "material",
                        "offsets": [{"material_index": 0, field_name: value}],
                    }
                ]

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(expected_code, f"morphs[0].offsets[0].{field_name}")],
                )

    def test_pmx_morph_panel_and_display_special_flag_use_writer_boundaries(self):
        morph_data = _valid_model_data()
        morph_data["morphs"] = [{"panel": 256, "offsets": []}]
        report = validate_model_data(morph_data, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("MORPH_FIELD_RANGE", "morphs[0].panel")],
        )

        display_data = _valid_model_data()
        display_data["display_frames"] = [{"special_flag": 2, "elements": []}]
        report = validate_model_data(display_data, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("DISPLAY_FRAME_FIELD_RANGE", "display_frames[0].special_flag")],
        )

    def test_action_does_not_call_writer_when_explicit_text_is_invalid(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["comment"] = None

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])

    def test_normal_pmx_and_pmd_bdef_skin_payloads_pass(self):
        cases = (
            ("pmx", [0], [1.0]),
            ("pmx", [0, 0], [0.5, 0.5]),
            ("pmx", [0, 0, 0, 0], [0.25, 0.25, 0.25, 0.25]),
            ("pmd", [0], [1.0]),
            ("pmd", [0, 0], [0.5, 0.5]),
        )
        for export_format, bone_indices, bone_weights in cases:
            with self.subTest(export_format=export_format, bone_indices=bone_indices):
                model_data = _valid_model_data()
                model_data["vertices"][0].update(
                    {"bone_indices": bone_indices, "bone_weights": bone_weights}
                )

                report = validate_model_data(model_data, export_format)

                self.assertTrue(report.valid)

        pmx_edge_flag = _valid_model_data()
        pmx_edge_flag["vertices"][0]["edge_flag"] = 0x100
        self.assertTrue(validate_model_data(pmx_edge_flag, "pmx").valid)

    def test_pmx_vertex_payloads_not_retained_by_writer_are_blocking(self):
        cases = (
            ("additional_uvs", [[0.25, 0.5, 0.75, 1.0]], "PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED"),
            ("additional_uv", [[0.25, 0.5, 0.75, 1.0]], "PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED"),
            ("sdef_c", [0.0, 0.0, 0.0], "PMX_VERTEX_SDEF_UNSUPPORTED"),
            ("sdef_r0", [0.0, 0.0, 0.0], "PMX_VERTEX_SDEF_UNSUPPORTED"),
            ("sdef_r1", [0.0, 0.0, 0.0], "PMX_VERTEX_SDEF_UNSUPPORTED"),
            ("weight_transform_type", 3, "PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED"),
            ("weight_transform_type", 4, "PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED"),
            ("weight_transform_type", "0", "PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED"),
        )
        for field_name, value, expected_code in cases:
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["vertices"][0][field_name] = value

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(expected_code, f"vertices[0].{field_name}")],
                )

    def test_pmx_vertex_unsupported_payload_does_not_call_writer(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["vertices"][0]["sdef_c"] = [0.0, 0.0, 0.0]

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])
        self.assertEqual(result.validation_report.issues[0].code, "PMX_VERTEX_SDEF_UNSUPPORTED")

    def test_pmx_vertex_empty_unsupported_payloads_and_bdef_type_zero_pass(self):
        for field_name, value in (
            ("additional_uvs", None),
            ("additional_uvs", []),
            ("additional_uv", None),
            ("additional_uv", []),
            ("sdef_c", None),
            ("sdef_r0", None),
            ("sdef_r1", None),
            ("weight_transform_type", 0),
        ):
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["vertices"][0][field_name] = value
                self.assertTrue(validate_model_data(model_data, "pmx").valid)

    def test_pmd_does_not_apply_pmx_vertex_unsupported_policy(self):
        model_data = _valid_model_data()
        model_data["vertices"][0].update(
            {
                "additional_uvs": [[0.25, 0.5, 0.75, 1.0]],
                "sdef_c": [0.0, 0.0, 0.0],
                "weight_transform_type": 4,
            }
        )

        self.assertTrue(validate_model_data(model_data, "pmd").valid)

    def test_bone_weight_payloads_cannot_be_truncated_by_either_writer(self):
        for export_format, excessive_weights in (("pmx", [0.25] * 5), ("pmd", [0.5] * 3)):
            with self.subTest(export_format=export_format):
                model_data = _valid_model_data()
                model_data["vertices"][0]["bone_weights"] = excessive_weights

                report = validate_model_data(model_data, export_format)

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [("BONE_WEIGHTS_LENGTH", "vertices[0].bone_weights")],
                )

    def test_pmd_bone_indices_allow_one_or_two_values_only(self):
        for bone_indices in ([0], [0, 0]):
            with self.subTest(bone_indices=bone_indices):
                model_data = _valid_model_data()
                model_data["vertices"][0]["bone_indices"] = bone_indices
                self.assertTrue(validate_model_data(model_data, "pmd").valid)

        for bone_indices in ([], [0, 0, 0]):
            with self.subTest(bone_indices=bone_indices):
                model_data = _valid_model_data()
                model_data["vertices"][0]["bone_indices"] = bone_indices
                report = validate_model_data(model_data, "pmd")
                self.assertEqual(report.issues[0].code, "BONE_INDICES_LENGTH")

    def test_pmd_vertex_byte_payloads_use_integer_writer_boundaries(self):
        valid_data = _valid_model_data()
        valid_data["vertices"][0].update({"bone_weight": 0, "edge_flag": 0})
        valid_data["vertices"][1].update({"bone_weight": 100, "edge_flag": 255})
        self.assertTrue(validate_model_data(valid_data, "pmd").valid)

        for field_name, value, expected_code in (
            ("bone_weight", 50.0, "PMD_BONE_WEIGHT_TYPE"),
            ("bone_weight", -1, "PMD_BONE_WEIGHT_RANGE"),
            ("bone_weight", 101, "PMD_BONE_WEIGHT_RANGE"),
            ("edge_flag", 1.0, "PMD_EDGE_FLAG_TYPE"),
            ("edge_flag", -1, "PMD_EDGE_FLAG_RANGE"),
            ("edge_flag", 256, "PMD_EDGE_FLAG_RANGE"),
        ):
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["vertices"][0][field_name] = value
                report = validate_model_data(model_data, "pmd")
                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(expected_code, f"vertices[0].{field_name}")],
                )

    def test_pmd_bone_count_type_and_writer_sentinels_are_validated(self):
        model_data = _valid_model_data()
        model_data["bones"] = [{}] * (PMD_MAX_BONE_COUNT + 1)
        report = validate_model_data(model_data, "pmd")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMD_BONE_LIMIT", "bones")],
        )

        for bone_type in (PmdBoneType.ROTATE, 0, 9):
            with self.subTest(bone_type=bone_type):
                model_data = _valid_model_data()
                model_data["bones"] = [{"bone_type": bone_type}]
                self.assertTrue(validate_model_data(model_data, "pmd").valid)

        invalid_type = _valid_model_data()
        invalid_type["bones"] = [{"bone_type": 10}]
        report = validate_model_data(invalid_type, "pmd")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMD_BONE_TYPE", "bones[0].bone_type")],
        )

        valid_references = _valid_model_data()
        valid_references["bones"] = [
            {
                "parent_index": -1,
                "tail_pos_bone_index": 0xFFFF,
                "ik_parent_bone_index": 0,
            }
        ]
        self.assertTrue(validate_model_data(valid_references, "pmd").valid)

        equivalent_parent_sentinel = _valid_model_data()
        equivalent_parent_sentinel["bones"] = [{"parent_index": 0xFFFF}]
        self.assertTrue(validate_model_data(equivalent_parent_sentinel, "pmd").valid)

        for field_name, value in (
            ("parent_index", 1),
            ("tail_pos_bone_index", -1),
            ("ik_parent_bone_index", -1),
        ):
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["bones"] = [{field_name: value}]
                report = validate_model_data(model_data, "pmd")
                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [("PMD_BONE_REFERENCE_OUT_OF_RANGE", f"bones[0].{field_name}")],
                )

    def test_optional_vertex_fields_and_default_bone_are_allowed(self):
        report = validate_model_data(
            {"vertices": [{"bone_indices": [0]}], "faces": [[0, 0, 0]]},
            "pmx",
        )

        self.assertTrue(report.valid)

    def test_unspecified_material_face_count_is_allowed(self):
        model_data = _valid_model_data()
        model_data["materials"][0]["face_count"] = None

        report = validate_model_data(model_data, "pmx")

        self.assertTrue(report.valid)

    def test_material_face_count_total_mismatch_blocks_both_writers(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format):
                exporter = _FakeExporter()
                model_data = _valid_model_data()
                model_data["materials"][0]["face_count"] = 0

                result = ExportModelAction(
                    **{f"{export_format}_exporter": exporter, "collector": None}
                ).execute(
                    ExportModelRequest(
                        file_path=f"out.{export_format}",
                        options={"export_format": export_format, "model_data": model_data},
                    )
                )

                self.assertFalse(result.succeeded)
                self.assertEqual(exporter.calls, [])
                self.assertEqual(
                    [(issue.code, issue.path) for issue in result.validation_report.issues],
                    [("MATERIAL_FACE_COUNT_TOTAL_MISMATCH", "materials")],
                )

    def test_material_face_count_uses_fan_triangulated_index_count(self):
        model_data = _valid_model_data()
        model_data["vertices"].append(
            {
                "position": [1.0, 1.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [1.0, 1.0],
                "bone_indices": [0],
            }
        )
        model_data["faces"] = [[0, 1, 2, 3]]
        model_data["materials"][0]["face_count"] = 6

        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format):
                report = validate_model_data(model_data, export_format)
                self.assertTrue(report.valid)

    def test_partial_material_face_counts_allow_remaining_assignment_but_reject_overflow(self):
        model_data = _valid_model_data()
        model_data["materials"] = [
            {"name": "First", "face_count": 2},
            {"name": "Remaining", "face_count": None},
        ]

        self.assertTrue(validate_model_data(model_data, "pmx").valid)

        model_data["materials"][0]["face_count"] = 4
        report = validate_model_data(model_data, "pmd")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("MATERIAL_FACE_COUNT_EXCEEDS_GEOMETRY", "materials")],
        )

    def test_invalid_material_face_count_does_not_add_consistency_issue(self):
        for value, expected_code in (
            ("3", "MATERIAL_FACE_COUNT_TYPE"),
            (-1, "MATERIAL_FACE_COUNT_RANGE"),
        ):
            with self.subTest(value=value):
                model_data = _valid_model_data()
                model_data["materials"][0]["face_count"] = value

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(expected_code, "materials[0].face_count")],
                )

    def test_textures_and_pmx_material_references_are_validated(self):
        model_data = _valid_model_data()
        model_data["textures"] = ["diffuse.png", "sphere.png"]
        model_data["materials"][0].update(
            {
                "texture_index": 0,
                "sphere_texture_index": 1,
                "toon_texture_index": 0,
                "sphere_mode": 3,
                "shared_toon_flag": 0,
                "draw_flag": 0xFF,
            }
        )

        self.assertTrue(validate_model_data(model_data, "pmx").valid)

        cases = (
            ("texture_index", 2, "MATERIAL_TEXTURE_INDEX_RANGE"),
            ("sphere_texture_index", 2, "MATERIAL_SPHERE_TEXTURE_INDEX_RANGE"),
            ("toon_texture_index", 2, "MATERIAL_TOON_TEXTURE_INDEX_RANGE"),
            ("texture_index", "0", "MATERIAL_TEXTURE_INDEX_TYPE"),
            ("sphere_mode", 4, "MATERIAL_SPHERE_MODE_RANGE"),
            ("shared_toon_flag", 2, "MATERIAL_SHARED_TOON_FLAG_RANGE"),
            ("draw_flag", 256, "MATERIAL_DRAW_FLAG_RANGE"),
        )
        for field_name, value, expected_code in cases:
            with self.subTest(field_name=field_name, value=value):
                case_data = _valid_model_data()
                case_data["textures"] = ["texture.png"]
                case_data["materials"][0][field_name] = value
                report = validate_model_data(case_data, "pmx")
                self.assertEqual(report.issues[0].code, expected_code)

        shared_toon_data = _valid_model_data()
        shared_toon_data["materials"][0].update(
            {"shared_toon_flag": 1, "toon_texture_index": 9}
        )
        self.assertTrue(validate_model_data(shared_toon_data, "pmx").valid)
        shared_toon_data["materials"][0]["toon_texture_index"] = 10
        report = validate_model_data(shared_toon_data, "pmx")
        self.assertEqual(report.issues[0].code, "MATERIAL_TOON_TEXTURE_INDEX_RANGE")

        missing_shared_toon_index = _valid_model_data()
        missing_shared_toon_index["materials"][0]["shared_toon_flag"] = 1
        report = validate_model_data(missing_shared_toon_index, "pmx")
        self.assertEqual(report.issues[0].code, "MATERIAL_TOON_TEXTURE_INDEX_MISSING")

    def test_texture_table_shape_and_pmd_loss_are_blocking(self):
        for value in (None, [], ()):
            with self.subTest(value=value):
                model_data = _valid_model_data()
                model_data["textures"] = value
                self.assertTrue(validate_model_data(model_data, "pmx").valid)

        non_sequence = _valid_model_data()
        non_sequence["textures"] = {}
        report = validate_model_data(non_sequence, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("TEXTURES_NOT_SEQUENCE", "textures")],
        )

        non_string = _valid_model_data()
        non_string["textures"] = [None]
        report = validate_model_data(non_string, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("TEXTURE_NOT_STRING", "textures[0]")],
        )

        pmd_data = _valid_model_data()
        pmd_data["textures"] = ["ignored.png"]
        report = validate_model_data(pmd_data, "pmd")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMD_TEXTURES_UNSUPPORTED", "textures")],
        )

    def test_material_face_count_and_pmd_byte_boundaries_are_validated(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format):
                valid_data = _valid_model_data()
                valid_data["materials"][0]["face_count"] = 3
                self.assertTrue(validate_model_data(valid_data, export_format).valid)

                too_large = _valid_model_data()
                too_large["materials"][0]["face_count"] = 0x100000000
                report = validate_model_data(too_large, export_format)
                self.assertEqual(report.issues[0].code, "MATERIAL_FACE_COUNT_RANGE")

                negative = _valid_model_data()
                negative["materials"][0]["face_count"] = -1
                report = validate_model_data(negative, export_format)
                self.assertEqual(report.issues[0].code, "MATERIAL_FACE_COUNT_RANGE")

        for field_name, value, expected_code in (
            ("toon_texture_index", 256, "MATERIAL_TOON_TEXTURE_INDEX_RANGE"),
            ("edge_flag", 256, "MATERIAL_EDGE_FLAG_RANGE"),
            ("edge_flag", -1, "MATERIAL_EDGE_FLAG_RANGE"),
        ):
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data["materials"][0][field_name] = value
                report = validate_model_data(model_data, "pmd")
                self.assertEqual(report.issues[0].code, expected_code)

    def test_explicit_bone_parent_reference_uses_minus_one_sentinel_and_range(self):
        model_data = _valid_model_data()
        model_data["bones"] = [{"parent_index": -1}]

        self.assertTrue(validate_model_data(model_data, "pmx").valid)

        model_data["bones"][0]["parent_index"] = 1
        report = validate_model_data(model_data, "pmx")

        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("BONE_REFERENCE_OUT_OF_RANGE", "bones[0].parent_index")],
        )

    def test_pmx_bone_ik_links_are_rejected_only_when_present_and_non_empty(self):
        for value in (None, [], ()):
            with self.subTest(value=value):
                model_data = _valid_model_data()
                model_data["bones"] = [{"ik_links": value}]
                self.assertTrue(validate_model_data(model_data, "pmx").valid)

        unsupported = _valid_model_data()
        unsupported["bones"] = [{"ik_links": [{}]}]
        report = validate_model_data(unsupported, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMX_BONE_IK_LINKS_UNSUPPORTED", "bones[0].ik_links")],
        )

        malformed = _valid_model_data()
        malformed["bones"] = [{"ik_links": {}}]
        report = validate_model_data(malformed, "pmx")
        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMX_BONE_IK_LINKS_NOT_SEQUENCE", "bones[0].ik_links")],
        )

    def test_unretained_top_level_payloads_are_blocking_when_meaningful(self):
        for export_format in ("pmx", "pmd"):
            for field_name in ("ik_data", "soft_bodies", "additional_uv"):
                with self.subTest(export_format=export_format, field_name=field_name):
                    model_data = _valid_model_data()
                    model_data[field_name] = [1] if field_name != "additional_uv" else 1
                    report = validate_model_data(model_data, export_format)
                    self.assertEqual(
                        [(issue.code, issue.path) for issue in report.issues],
                        [(f"{export_format.upper()}_{field_name.upper()}_UNSUPPORTED", field_name)],
                    )

        for field_name, value in (
            ("ik_data", None),
            ("ik_data", []),
            ("soft_bodies", ()),
            ("additional_uv", 0),
        ):
            with self.subTest(field_name=field_name, value=value):
                model_data = _valid_model_data()
                model_data[field_name] = value
                self.assertTrue(validate_model_data(model_data, "pmx").valid)

    def test_supported_pmx_morph_types_include_numeric_enum_values(self):
        for morph_type in ("vertex", "bone", "material", 1, 2, 8):
            with self.subTest(morph_type=morph_type):
                model_data = _valid_model_data()
                model_data["morphs"] = [{"type": morph_type, "offsets": []}]

                report = validate_model_data(model_data, "pmx")

                self.assertTrue(report.valid)

    def test_pmx_morph_vertex_bone_and_material_references_are_range_checked(self):
        cases = (
            ("vertex", {"vertex_index": 3}, "morphs[0].offsets[0].vertex_index"),
            ("bone", {"bone_index": 1}, "morphs[0].offsets[0].bone_index"),
            ("material", {"material_index": 1}, "morphs[0].offsets[0].material_index"),
        )
        for morph_type, offset, expected_path in cases:
            with self.subTest(morph_type=morph_type):
                model_data = _valid_model_data()
                model_data["bones"] = [{}]
                model_data["morphs"] = [{"type": morph_type, "offsets": [offset]}]

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [("MORPH_OFFSET_INDEX_OUT_OF_RANGE", expected_path)],
                )

    def test_pmx_material_morph_minus_one_uses_default_material_count(self):
        model_data = _valid_model_data()
        model_data["materials"] = []
        model_data["morphs"] = [{"type": "material", "offsets": [{"material_index": -1}]}]

        report = validate_model_data(model_data, "pmx")

        self.assertTrue(report.valid)

    def test_unsupported_pmx_group_morph_is_blocking(self):
        model_data = _valid_model_data()
        model_data["morphs"] = [{"type": 0, "offsets": []}]

        report = validate_model_data(model_data, "pmx")

        self.assertEqual(
            [(issue.code, issue.path, issue.severity, issue.blocking) for issue in report.issues],
            [("MORPH_TYPE_UNSUPPORTED", "morphs[0].type", "fatal", True)],
        )

    def test_non_empty_pmd_morphs_are_blocked_as_unsupported(self):
        model_data = _valid_model_data()
        model_data["morphs"] = [{"type": "vertex", "offsets": []}]

        report = validate_model_data(model_data, "pmd")

        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("PMD_MORPHS_UNSUPPORTED", "morphs")],
        )

    def test_present_non_sequence_morphs_and_materials_are_blocking_even_when_empty(self):
        model_data = _valid_model_data()
        model_data["morphs"] = {}
        model_data["materials"] = {}

        report = validate_model_data(model_data, "pmx")

        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [
                ("MATERIALS_NOT_SEQUENCE", "materials"),
                ("MORPHS_NOT_SEQUENCE", "morphs"),
            ],
        )

    def test_empty_or_none_scene_physics_collections_are_allowed(self):
        for value in (None, [], ()):
            with self.subTest(value=value):
                model_data = _valid_model_data()
                model_data["display_frames"] = value
                model_data["rigid_bodies"] = value
                model_data["joints"] = value

                report = validate_model_data(model_data, "pmx")

                self.assertTrue(report.valid)

    def test_present_non_sequence_scene_physics_collections_are_blocking(self):
        for field_name in ("display_frames", "rigid_bodies", "joints"):
            with self.subTest(field_name=field_name):
                model_data = _valid_model_data()
                model_data[field_name] = {}

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(f"{field_name.upper()}_NOT_SEQUENCE", field_name)],
                )

    def test_pmx_display_frame_elements_and_references_are_validated(self):
        cases = (
            ({"elements": {}}, "DISPLAY_ELEMENTS_NOT_SEQUENCE"),
            ({"elements": [None]}, "DISPLAY_ELEMENT_NOT_MAPPING"),
            ({"elements": [{"type": 2, "index": 0}]}, "DISPLAY_ELEMENT_TYPE_UNSUPPORTED"),
            ({"elements": [{"type": 0, "index": "0"}]}, "DISPLAY_ELEMENT_INDEX_TYPE"),
            ({"elements": [{"type": 0, "index": 1}]}, "DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE"),
        )
        for frame, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                model_data = _valid_model_data()
                model_data["display_frames"] = [frame]

                report = validate_model_data(model_data, "pmx")

                self.assertEqual(report.issues[0].code, expected_code)

        model_data = _valid_model_data()
        model_data["morphs"] = [{"type": "vertex", "offsets": []}]
        model_data["display_frames"] = [{"elements": [{"type": 1, "index": 1}]}]
        report = validate_model_data(model_data, "pmx")
        self.assertEqual(report.issues[0].code, "DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE")

    def test_pmx_rigid_body_and_joint_payloads_pass(self):
        model_data = _valid_model_data()
        model_data["rigid_bodies"] = [
            {
                "related_bone_index": -1,
                "group": 1,
                "collision_mask": 0xFFFF,
                "shape_type": 2,
                "size": [1.0, 2.0, 3.0],
                "position": [0.0, 1.0, 2.0],
                "rotation": [0.1, 0.2, 0.3],
                "mass": 1.0,
                "velocity_attenuation": 0.5,
                "rotation_attenuation": 0.25,
                "elasticity": 0.1,
                "friction": 0.2,
                "physics_mode": 2,
            }
        ]
        model_data["joints"] = [
            {
                "joint_type": 0,
                "rigid_body_a_index": 0,
                "rigid_body_b_index": -1,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "translation_limit_min": [-1.0, -1.0, -1.0],
                "translation_limit_max": [1.0, 1.0, 1.0],
                "rotation_limit_min": [-0.5, -0.5, -0.5],
                "rotation_limit_max": [0.5, 0.5, 0.5],
                "spring_translation": [0.1, 0.2, 0.3],
                "spring_rotation": [0.4, 0.5, 0.6],
            }
        ]

        report = validate_model_data(model_data, "pmx")

        self.assertTrue(report.valid)

    def test_pmx_rigid_body_and_joint_references_and_shapes_are_blocking(self):
        rigid_model = _valid_model_data()
        rigid_model["rigid_bodies"] = [{"related_bone_index": 1}]
        report = validate_model_data(rigid_model, "pmx")
        self.assertEqual(report.issues[0].code, "RIGID_BODY_BONE_REFERENCE_OUT_OF_RANGE")

        rigid_model["rigid_bodies"] = [{"group": 256}]
        report = validate_model_data(rigid_model, "pmx")
        self.assertEqual(report.issues[0].code, "RIGID_BODY_FIELD_RANGE")

        rigid_model["rigid_bodies"] = [{"size": [0.0, 0.0]}]
        report = validate_model_data(rigid_model, "pmx")
        self.assertEqual(report.issues[0].code, "FIELD_LENGTH")

        joint_model = _valid_model_data()
        joint_model["rigid_bodies"] = [{}]
        joint_model["joints"] = [{"rigid_body_a_index": 1}]
        report = validate_model_data(joint_model, "pmx")
        self.assertEqual(report.issues[0].code, "JOINT_RIGID_BODY_REFERENCE_OUT_OF_RANGE")

        joint_model["joints"] = [{"joint_type": 256}]
        report = validate_model_data(joint_model, "pmx")
        self.assertEqual(report.issues[0].code, "JOINT_FIELD_RANGE")

    def test_pmx_joints_require_rigid_bodies(self):
        model_data = _valid_model_data()
        model_data["joints"] = [{}]

        report = validate_model_data(model_data, "pmx")

        self.assertEqual(
            [(issue.code, issue.path) for issue in report.issues],
            [("JOINTS_REQUIRE_RIGID_BODIES", "joints")],
        )

    def test_non_empty_pmd_scene_physics_collections_are_lossy_and_blocked(self):
        for field_name, expected_code in (
            ("display_frames", "PMD_DISPLAY_FRAMES_UNSUPPORTED"),
            ("rigid_bodies", "PMD_RIGID_BODIES_UNSUPPORTED"),
            ("joints", "PMD_JOINTS_UNSUPPORTED"),
        ):
            with self.subTest(field_name=field_name):
                model_data = _valid_model_data()
                model_data[field_name] = [{}]

                report = validate_model_data(model_data, "pmd")

                self.assertEqual(
                    [(issue.code, issue.path) for issue in report.issues],
                    [(expected_code, field_name)],
                )

    def test_action_does_not_call_writer_when_display_frame_preflight_fails(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["display_frames"] = [{"elements": [{"type": 2, "index": 0}]}]

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])

    def test_action_does_not_call_writer_when_texture_preflight_fails(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["textures"] = [None]

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])

    def test_morph_offsets_require_mappings_and_supported_vector_lengths(self):
        malformed_offsets = _valid_model_data()
        malformed_offsets["morphs"] = [{"type": "vertex", "offsets": {"vertex_index": 0}}]
        report = validate_model_data(malformed_offsets, "pmx")
        self.assertEqual(report.issues[0].code, "MORPH_OFFSETS_NOT_SEQUENCE")

        malformed_offset = _valid_model_data()
        malformed_offset["morphs"] = [
            {"type": "vertex", "offsets": [{"position_offset": [0.0, 0.0]}]}
        ]
        report = validate_model_data(malformed_offset, "pmx")
        self.assertEqual(
            [issue.code for issue in report.issues],
            ["MORPH_OFFSET_INDEX_MISSING", "FIELD_LENGTH"],
        )

        non_mapping_offset = _valid_model_data()
        non_mapping_offset["morphs"] = [{"type": "vertex", "offsets": [None]}]
        report = validate_model_data(non_mapping_offset, "pmx")
        self.assertEqual(report.issues[0].code, "MORPH_OFFSET_NOT_MAPPING")

    def test_recursive_finite_scan_covers_morph_offsets(self):
        model_data = _valid_model_data()
        model_data["morphs"] = [
            {
                "type": "vertex",
                "offsets": [{"vertex_index": 0, "position_offset": [math.nan, 0.0, 0.0]}],
            }
        ]

        report = validate_model_data(model_data, "pmx")

        self.assertIn("NON_FINITE_NUMBER", [issue.code for issue in report.issues])

    def test_action_does_not_call_writer_when_morph_preflight_fails(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["morphs"] = [{"type": "group", "offsets": []}]

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])

    def test_malformed_vertex_is_blocking(self):
        report = validate_model_data(
            {"vertices": [{"position": [0.0, 1.0]}], "faces": [[0, 0, 0]]},
            "pmx",
        )

        self.assertTrue(report.is_blocking)
        self.assertEqual(report.issues[0].code, "FIELD_LENGTH")
        self.assertEqual(report.issues[0].severity, "fatal")
        self.assertTrue(report.issues[0].blocking)
        self.assertEqual(report.issues[0].path, "vertices[0].position")

    def test_malformed_face_and_vertex_index_are_blocking(self):
        report = validate_model_data(
            {"vertices": [{}, {}, {}], "faces": [[0, 1], [0, 1, 3]]},
            "pmx",
        )

        codes = [issue.code for issue in report.issues]
        self.assertIn("FACE_TOO_SHORT", codes)
        self.assertIn("FACE_INDEX_OUT_OF_RANGE", codes)

    def test_non_finite_vertex_and_material_numbers_are_blocking(self):
        model_data = _valid_model_data()
        model_data["vertices"][0]["normal"][1] = math.inf
        model_data["materials"][0]["diffuse"][0] = math.nan

        report = validate_model_data(model_data, "pmx")

        self.assertEqual(
            [issue.code for issue in report.issues],
            ["NON_FINITE_NUMBER", "NON_FINITE_NUMBER"],
        )
        self.assertTrue(all(issue.blocking for issue in report.issues))

    def test_blocked_report_serializes_with_fatal_counts_and_stable_issue_keys(self):
        report = validate_model_data(
            {"vertices": [{"position": [0.0, 1.0]}], "faces": [[0, 0, 0]]},
            "pmx",
        )
        human_summary = report.summary

        payload = report.to_dict()

        self.assertEqual(
            list(payload),
            [
                "schema_version",
                "status",
                "requires_warning_ack",
                "format",
                "mode",
                "summary",
                "issues",
            ],
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["requires_warning_ack"])
        self.assertEqual(payload["format"], "pmx")
        self.assertEqual(payload["mode"], "model")
        self.assertEqual(payload["summary"], {"fatal": 1, "warning": 0, "info": 0})
        self.assertEqual(
            list(payload["issues"][0]),
            ["code", "severity", "blocking", "path", "message"],
        )
        self.assertEqual(payload["issues"][0]["severity"], "fatal")
        self.assertEqual(report.summary, human_summary)

    def test_ready_report_is_json_serializable(self):
        report = validate_model_data(_valid_model_data(), "pmd")

        encoded = json.dumps(report.to_dict(), sort_keys=True)
        decoded = json.loads(encoded)

        self.assertEqual(decoded["status"], "ready")
        self.assertFalse(decoded["requires_warning_ack"])
        self.assertEqual(decoded["summary"], {"fatal": 0, "warning": 0, "info": 0})
        self.assertEqual(decoded["issues"], [])

    def test_blocked_report_to_json_is_deterministic_and_preserves_schema(self):
        report = ExportValidationReport(
            "pmx",
            (ExportValidationIssue("invalid_payload", "fatal", True, "model_data", "日本語"),),
        )

        encoded = report.to_json()

        self.assertEqual(encoded, report.to_json())
        self.assertIn("日本語", encoded)
        self.assertEqual(json.loads(encoded), report.to_dict())
        self.assertEqual(json.loads(encoded)["status"], "blocked")

    def test_ready_report_write_json_has_utf8_newline_and_readback(self):
        report = validate_model_data(_valid_model_data(), "pmd")
        expected_text = report.to_json() + "\n"

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "validation-report.json"
            report.write_json(output_path)

            self.assertEqual(output_path.read_bytes(), expected_text.encode("utf-8"))
            self.assertEqual(output_path.read_text(encoding="utf-8"), expected_text)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), report.to_dict())

    def test_non_blocking_warning_report_has_warning_status_and_ack(self):
        report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue("partial_material", "warning", False, "materials[0]", "partial material"),
                ExportValidationIssue("informational", "info", False, "model_data", "informational note"),
            ),
        )

        payload = report.to_dict()

        self.assertEqual(payload["status"], "warning")
        self.assertTrue(payload["requires_warning_ack"])
        self.assertEqual(payload["summary"], {"fatal": 0, "warning": 1, "info": 1})
        self.assertEqual(payload["issues"][0]["code"], "partial_material")
        self.assertEqual(payload["issues"][1]["code"], "informational")

    def test_out_of_range_bone_index_and_empty_explicit_bones_are_blocking(self):
        model_data = _valid_model_data()
        model_data["bones"] = []
        model_data["vertices"][0]["bone_indices"] = [1]

        report = validate_model_data(model_data, "pmd")

        codes = [issue.code for issue in report.issues]
        self.assertIn("BONES_EMPTY", codes)
        self.assertIn("BONE_INDEX_OUT_OF_RANGE", codes)

    def test_action_does_not_call_writer_when_preflight_fails(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["faces"] = [[0, 1]]

        result = ExportModelAction(pmx_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertTrue(result.validation_report.is_blocking)
        self.assertEqual(exporter.calls, [])
        self.assertEqual(result.warnings, list(result.validation_report.issues))

    def test_action_does_not_call_writer_when_pmd_skin_preflight_fails(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["vertices"][0]["bone_weights"] = [0.5, 0.25, 0.25]

        result = ExportModelAction(pmd_exporter=exporter, collector=None).execute(
            ExportModelRequest(
                file_path="out.pmd",
                options={"export_format": "pmd", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(exporter.calls, [])
        self.assertEqual(result.validation_report.issues[0].code, "BONE_WEIGHTS_LENGTH")

    def test_action_passes_valid_data_to_both_writers(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / f"out.{export_format}"
                exporter = _FakeExporter()
                action_kwargs = {
                    f"{export_format}_exporter": exporter,
                    "collector": None,
                    "output_verifier": None,
                }
                result = ExportModelAction(**action_kwargs).execute(
                    ExportModelRequest(
                        file_path=str(output_path),
                        options={"export_format": export_format, "model_data": _valid_model_data()},
                    )
                )

                self.assertTrue(result.succeeded)
                self.assertEqual(len(exporter.calls), 1)
                writer_path = Path(exporter.calls[0][0])
                self.assertEqual(writer_path.parent, output_path.parent)
                self.assertEqual(writer_path.suffix, f".{export_format}")
                self.assertNotEqual(writer_path, output_path)
                self.assertFalse(writer_path.exists())
                self.assertEqual(output_path.read_bytes(), f"fake {export_format} bytes".encode())
                self.assertTrue(result.validation_report.valid)
                self.assertEqual(result.payload_fingerprint, fingerprint_payload(_valid_model_data()))

    def test_action_does_not_call_writer_or_modify_existing_file_when_preflight_fails(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / f"existing.{export_format}"
                original_bytes = b"existing export bytes"
                output_path.write_bytes(original_bytes)

                exporter = _FakeExporter()
                model_data = _valid_model_data()
                model_data["faces"] = [[0, 1]]
                result = ExportModelAction(
                    **{
                        f"{export_format}_exporter": exporter,
                        "collector": None,
                    }
                ).execute(
                    ExportModelRequest(
                        file_path=str(output_path),
                        options={"export_format": export_format, "model_data": model_data},
                    )
                )

                self.assertFalse(result.succeeded)
                self.assertIsInstance(result.error, ExportValidationError)
                self.assertEqual(exporter.calls, [])
                self.assertEqual(output_path.read_bytes(), original_bytes)

    def test_writer_exception_preserves_valid_report_and_original_error(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / f"existing.{export_format}"
                original_bytes = b"existing export bytes"
                output_path.write_bytes(original_bytes)
                writer_error = RuntimeError(f"{export_format} writer failed")
                exporter = _RaisingExporter(writer_error)
                result = ExportModelAction(
                    **{f"{export_format}_exporter": exporter, "collector": None}
                ).execute(
                    ExportModelRequest(
                        file_path=str(output_path),
                        options={"export_format": export_format, "model_data": _valid_model_data()},
                    )
                )

                self.assertFalse(result.succeeded)
                self.assertIs(result.error, writer_error)
                self.assertIsNotNone(result.validation_report)
                self.assertTrue(result.validation_report.valid)
                self.assertEqual(result.warnings, [])
                self.assertEqual(len(exporter.calls), 1)
                self.assertEqual(output_path.read_bytes(), original_bytes)
                self.assertFalse(Path(exporter.calls[0][0]).exists())

    def test_empty_temporary_output_fails_and_preserves_existing_file(self):
        for export_format in ("pmx", "pmd"):
            with self.subTest(export_format=export_format), tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / f"empty.{export_format}"
                original_bytes = b"existing export bytes"
                output_path.write_bytes(original_bytes)
                exporter = _SilentExporter()

                result = ExportModelAction(
                    **{f"{export_format}_exporter": exporter, "collector": None}
                ).execute(
                    ExportModelRequest(
                        file_path=str(output_path),
                        options={"export_format": export_format, "model_data": _valid_model_data()},
                    )
                )

                self.assertFalse(result.succeeded)
                self.assertIsInstance(result.error, ExportValidationError)
                self.assertEqual(result.validation_report.issues[0].code, "OUTPUT_FILE_EMPTY")
                self.assertEqual(output_path.read_bytes(), original_bytes)
                self.assertEqual(len(exporter.calls), 1)
                self.assertFalse(Path(exporter.calls[0][0]).exists())

    def test_output_verifier_failure_preserves_existing_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "verified.pmx"
            original_bytes = b"existing export bytes"
            output_path.write_bytes(original_bytes)
            exporter = _FakeExporter()

            def reject_output(file_path, export_format, model_data):
                return ExportValidationReport(
                    export_format,
                    (
                        ExportValidationIssue(
                            "OUTPUT_PARSE_FAILED",
                            "fatal",
                            True,
                            "output",
                            "parser rejected temporary output",
                        ),
                    ),
                )

            result = ExportModelAction(
                pmx_exporter=exporter,
                collector=None,
                output_verifier=reject_output,
            ).execute(
                ExportModelRequest(
                    file_path=str(output_path),
                    options={"export_format": "pmx", "model_data": _valid_model_data()},
                )
            )

            self.assertFalse(result.succeeded)
            self.assertIsInstance(result.error, ExportValidationError)
            self.assertEqual(result.validation_report.issues[0].code, "OUTPUT_PARSE_FAILED")
            self.assertEqual(output_path.read_bytes(), original_bytes)
            self.assertFalse(Path(exporter.calls[0][0]).exists())

    def test_expected_payload_fingerprint_mismatch_blocks_writer(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        result = ExportModelAction(
            pmx_exporter=exporter,
            collector=None,
            output_verifier=None,
        ).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={
                    "export_format": "pmx",
                    "model_data": model_data,
                    "expected_payload_fingerprint": "sha256:stale",
                },
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(result.validation_report.issues[0].code, "STALE_VALIDATION_SNAPSHOT")
        self.assertEqual(exporter.calls, [])
        self.assertEqual(result.payload_fingerprint, fingerprint_payload(model_data))

    def test_non_finite_payload_still_returns_validation_report(self):
        exporter = _FakeExporter()
        model_data = _valid_model_data()
        model_data["vertices"][0]["position"][0] = math.nan

        result = ExportModelAction(
            pmx_exporter=exporter,
            collector=None,
            output_verifier=None,
        ).execute(
            ExportModelRequest(
                file_path="out.pmx",
                options={"export_format": "pmx", "model_data": model_data},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertIn("NON_FINITE_NUMBER", [issue.code for issue in result.validation_report.issues])
        self.assertEqual(exporter.calls, [])

    def test_stale_validation_snapshot_blocks_writer_and_preserves_target(self):
        exporter = _FakeExporter()
        snapshot_data = _valid_model_data()
        snapshot = ExportValidationSnapshot.capture(snapshot_data, "pmx", scene_revision="12")
        current_data = _valid_model_data()
        current_data["model_name"] = "ChangedAfterValidation"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "stale.pmx"
            original_bytes = b"existing export bytes"
            output_path.write_bytes(original_bytes)
            result = ExportModelAction(
                pmx_exporter=exporter,
                collector=None,
                output_verifier=None,
            ).execute(
                ExportModelRequest(
                    file_path=str(output_path),
                    options={
                        "export_format": "pmx",
                        "model_data": current_data,
                        "validation_snapshot": snapshot,
                        "scene_revision": "12",
                    },
                )
            )
            self.assertEqual(output_path.read_bytes(), original_bytes)

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(result.validation_report.issues[0].code, "STALE_VALIDATION_SNAPSHOT")
        self.assertEqual(exporter.calls, [])

    def test_mmd_anim_opt_in_failure_preserves_existing_target(self):
        exporter = _FakeExporter()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "mmd-anim.pmx"
            original_bytes = b"existing export bytes"
            output_path.write_bytes(original_bytes)
            result = ExportModelAction(
                pmx_exporter=exporter,
                collector=None,
                output_verifier=None,
            ).execute(
                ExportModelRequest(
                    file_path=str(output_path),
                    options={
                        "export_format": "pmx",
                        "model_data": _valid_model_data(),
                        "verify_mmd_anim": True,
                        "mmd_anim_cli": "missing-mmd-anim-cli",
                    },
                )
            )
            self.assertEqual(output_path.read_bytes(), original_bytes)

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ExportValidationError)
        self.assertEqual(result.validation_report.issues[0].code, "MMD_ANIM_CLI_UNAVAILABLE")
        self.assertFalse(Path(exporter.calls[0][0]).exists())
