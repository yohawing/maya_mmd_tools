"""Contract tests for the stable schema-v2 validation report boundary."""

import json
import ast
from pathlib import Path
import tempfile
import unittest

from mmd_tools.validation.export_validator import (
    AGGREGATION_DISCRIMINATORS,
    EXPORT_VALIDATION_SCHEMA_VERSION,
    ExportValidationIssue,
    ExportValidationReport,
    structured_export_failure_report,
    validate_model_data,
)
from mmd_tools.validation.issue_catalog import STABLE_ISSUE_CODES, ISSUE_CATALOG
from mmd_tools.validation.report_artifacts import write_validation_report_artifacts
from tools.gates.export_report_consistency import validate_report_consistency


_LEGACY_CODES = frozenset(
    """
BONE_INDEX_OUT_OF_RANGE BONE_INDEX_TYPE BONE_INDICES_LENGTH BONE_NOT_MAPPING
BONE_REFERENCE_OUT_OF_RANGE BONE_REFERENCE_TYPE BONE_WEIGHTS_LENGTH BONES_EMPTY BONES_NOT_SEQUENCE
DISPLAY_ELEMENT_INDEX_MISSING DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE DISPLAY_ELEMENT_INDEX_TYPE
DISPLAY_ELEMENT_NOT_MAPPING DISPLAY_ELEMENT_TYPE_MISSING DISPLAY_ELEMENT_TYPE_TYPE
DISPLAY_ELEMENT_TYPE_UNSUPPORTED DISPLAY_ELEMENTS_NOT_SEQUENCE DISPLAY_FRAME_FIELD_RANGE
DISPLAY_FRAME_FIELD_TYPE DISPLAY_FRAME_NOT_MAPPING DISPLAY_FRAMES_NOT_SEQUENCE EXPORT_WORKFLOW_EXCEPTION
FACE_INDEX_OUT_OF_RANGE FACE_INDEX_TYPE FACE_NOT_SEQUENCE FACE_TOO_SHORT FACES_EMPTY FACES_NOT_SEQUENCE
FIELD_LENGTH FIELD_NOT_SEQUENCE JOINT_FIELD_RANGE JOINT_FIELD_TYPE JOINT_NOT_MAPPING
JOINT_RIGID_BODY_REFERENCE_OUT_OF_RANGE JOINT_RIGID_BODY_REFERENCE_TYPE JOINTS_NOT_SEQUENCE
JOINTS_REQUIRE_RIGID_BODIES MATERIAL_DRAW_FLAG_RANGE MATERIAL_DRAW_FLAG_TYPE
MATERIAL_FACE_COUNT_EXCEEDS_GEOMETRY MATERIAL_FACE_COUNT_RANGE MATERIAL_FACE_COUNT_TOTAL_MISMATCH
MATERIAL_FACE_COUNT_TYPE MATERIAL_NOT_MAPPING MATERIAL_SHARED_TOON_FLAG_RANGE
MATERIAL_SHARED_TOON_FLAG_TYPE MATERIAL_SEMANTIC_MISSING MATERIAL_SPHERE_MODE_RANGE
MATERIAL_SPHERE_MODE_TYPE MATERIAL_SPHERE_TEXTURE_INDEX_RANGE MATERIAL_SPHERE_TEXTURE_INDEX_TYPE
MATERIAL_TEXTURE_INDEX_RANGE MATERIAL_TEXTURE_INDEX_TYPE MATERIAL_TOON_TEXTURE_INDEX_MISSING
MATERIAL_TOON_TEXTURE_INDEX_RANGE MATERIAL_TOON_TEXTURE_INDEX_TYPE MATERIALS_NOT_SEQUENCE
MMD_ANIM_CLI_UNAVAILABLE MMD_ANIM_COMMAND_FAILED MMD_ANIM_COUNT_MISMATCH MMD_ANIM_DIAGNOSTICS
MMD_ANIM_BINDING_COUNT_MISMATCH MMD_ANIM_BINDING_INPUT_INVALID MMD_ANIM_BINDING_MATRIX_INVALID
MMD_ANIM_BINDING_RUNTIME_FAILED MMD_ANIM_BINDING_UNAVAILABLE MMD_ANIM_BINDING_WEIGHT_INVALID
MMD_ANIM_INSPECT_JSON_INVALID MMD_ANIM_ROUNDTRIP_FAILED MMD_ANIM_ROUNDTRIP_JSON_INVALID MMD_ANIM_TIMEOUT
MODEL_DATA_NOT_MAPPING MORPH_FIELD_RANGE MORPH_FIELD_TYPE MORPH_NOT_MAPPING MORPH_OFFSET_INDEX_MISSING
MORPH_OFFSET_INDEX_OUT_OF_RANGE MORPH_OFFSET_INDEX_TYPE MORPH_OFFSET_NOT_MAPPING MORPH_OFFSETS_NOT_SEQUENCE
MORPH_TYPE_UNSUPPORTED MORPHS_NOT_SEQUENCE NON_FINITE_NUMBER NUMERIC_VALUE_TYPE
OUTPUT_BONE_COUNT_MISMATCH OUTPUT_FACE_COUNT_MISMATCH OUTPUT_FILE_EMPTY OUTPUT_FILE_MISSING
OUTPUT_FORMAT_UNSUPPORTED OUTPUT_HEADER_INVALID OUTPUT_MATERIAL_COUNT_MISMATCH OUTPUT_PARSE_FAILED
OUTPUT_VERTEX_COUNT_MISMATCH OUTPUT_WRITE_FAILED PMX_ADDITIONAL_UV_UNSUPPORTED
PMX_BONE_IK_LINKS_NOT_SEQUENCE PMX_BONE_SEMANTIC_MISSING PMX_IK_DATA_UNSUPPORTED
PMX_SOFT_BODIES_UNSUPPORTED PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED
PMX_VERTEX_ADDITIONAL_UV_COUNT_MISMATCH PMX_VERTEX_SEMANTIC_MISSING PMX_VERTEX_SDEF_UNSUPPORTED
PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED RIGID_BODY_BONE_REFERENCE_OUT_OF_RANGE
RIGID_BODY_BONE_REFERENCE_TYPE RIGID_BODY_FIELD_RANGE RIGID_BODY_FIELD_TYPE RIGID_BODY_NOT_MAPPING
RIGID_BODIES_NOT_SEQUENCE SCENE_COLLECT_FAILED SCENE_FORMAT_UNSUPPORTED SCENE_FRAME_RANGE_INVALID
SCENE_FRAME_STEP_INVALID SCENE_OUTPUT_EXTENSION_MISMATCH SCENE_OUTPUT_PATH_INVALID SCENE_OUTPUT_SAME_AS_SOURCE
SCENE_OWNER_CONTROL_RIG SCENE_OWNER_HUMANIK SCENE_OWNER_QUERY_FAILED SCENE_SCALE_INVALID
SCENE_TARGET_MISSING SCENE_TARGET_STALE STALE_VALIDATION_SNAPSHOT TEXT_FIELD_TYPE TEXTURE_NOT_STRING
TEXTURES_NOT_SEQUENCE VERTEX_NOT_MAPPING VERTICES_EMPTY VERTICES_NOT_SEQUENCE
VMD_BONE_INTERPOLATION_LENGTH VMD_CONTROL_RIG_ROUTE_UNRESOLVED VMD_CAMERA_INTERPOLATION_LENGTH
VMD_FRAME_NEGATIVE VMD_FRAME_COUNT_MISMATCH VMD_FRAME_RANGE VMD_IK_FLAG_RANGE
VMD_IK_SCENE_REPRESENTATION_MISSING VMD_NAME_EMPTY VMD_NON_FINITE_NUMBER VMD_PERSPECTIVE_RANGE
VMD_QUATERNION_INVALID VMD_SHADOW_MODE_RANGE EXPORT_FORMAT_UNSUPPORTED AUTHORING_SPEC_INVALID
""".split()
)

_REFERENCE_CODES = frozenset(
    """BONE_INDEX_OUT_OF_RANGE BONE_REFERENCE_OUT_OF_RANGE BONE_REFERENCE_TYPE
DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE FACE_INDEX_OUT_OF_RANGE JOINT_RIGID_BODY_REFERENCE_OUT_OF_RANGE
JOINT_RIGID_BODY_REFERENCE_TYPE MATERIAL_FACE_COUNT_EXCEEDS_GEOMETRY MATERIAL_FACE_COUNT_TOTAL_MISMATCH
MORPH_OFFSET_INDEX_OUT_OF_RANGE PMX_VERTEX_ADDITIONAL_UV_COUNT_MISMATCH
RIGID_BODY_BONE_REFERENCE_OUT_OF_RANGE RIGID_BODY_BONE_REFERENCE_TYPE""".split()
)
_UNSUPPORTED_CODES = frozenset(
    """DISPLAY_ELEMENT_TYPE_UNSUPPORTED MORPH_TYPE_UNSUPPORTED PMX_ADDITIONAL_UV_UNSUPPORTED
PMX_IK_DATA_UNSUPPORTED PMX_SOFT_BODIES_UNSUPPORTED PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED
PMX_VERTEX_SDEF_UNSUPPORTED PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED""".split()
)
_GROUPS = {
    "SCENE_INVALID": frozenset({"SCENE_TARGET_MISSING"}),
    "OWNERSHIP_CONFLICT": frozenset({"SCENE_OWNER_CONTROL_RIG", "SCENE_OWNER_HUMANIK"}),
    "ROUTE_UNRESOLVED": frozenset(
        {"VMD_CONTROL_RIG_ROUTE_UNRESOLVED", "VMD_IK_SCENE_REPRESENTATION_MISSING"}
    ),
    "EXPORT_OPTIONS_INVALID": frozenset(
        """EXPORT_FORMAT_UNSUPPORTED SCENE_FORMAT_UNSUPPORTED SCENE_FRAME_RANGE_INVALID
SCENE_FRAME_STEP_INVALID SCENE_OUTPUT_EXTENSION_MISMATCH SCENE_OUTPUT_PATH_INVALID
SCENE_OUTPUT_SAME_AS_SOURCE SCENE_SCALE_INVALID OUTPUT_FORMAT_UNSUPPORTED""".split()
    ),
    "COLLECTION_FAILED": frozenset(
        """MATERIAL_SEMANTIC_MISSING PMX_BONE_SEMANTIC_MISSING PMX_VERTEX_SEMANTIC_MISSING
SCENE_COLLECT_FAILED SCENE_OWNER_QUERY_FAILED""".split()
    ),
    "STALE_STATE": frozenset({"SCENE_TARGET_STALE", "STALE_VALIDATION_SNAPSHOT"}),
    "OUTPUT_WRITE_FAILED": frozenset({"OUTPUT_WRITE_FAILED"}),
    "OUTPUT_VERIFY_FAILED": frozenset(
        """OUTPUT_BONE_COUNT_MISMATCH OUTPUT_FACE_COUNT_MISMATCH OUTPUT_FILE_EMPTY OUTPUT_FILE_MISSING
OUTPUT_HEADER_INVALID OUTPUT_MATERIAL_COUNT_MISMATCH OUTPUT_PARSE_FAILED OUTPUT_VERTEX_COUNT_MISMATCH
VMD_FRAME_COUNT_MISMATCH""".split()
    ),
    "EXTERNAL_TOOL_FAILED": frozenset(code for code in _LEGACY_CODES if code.startswith("MMD_ANIM_")),
    "INTERNAL_ERROR": frozenset({"EXPORT_WORKFLOW_EXCEPTION"}),
}
_DISCRIMINATOR_BY_CODE = {
    "INPUT_INVALID": "payload_shape",
    "REFERENCE_INVALID": "reference",
    "UNSUPPORTED_FEATURE": "unsupported_feature",
    "SCENE_INVALID": "scene_target",
    "OWNERSHIP_CONFLICT": "ownership_control_rig",
    "ROUTE_UNRESOLVED": "route",
    "EXPORT_OPTIONS_INVALID": "export_option",
    "COLLECTION_FAILED": "collection",
    "STALE_STATE": "stale",
    "OUTPUT_WRITE_FAILED": "write",
    "OUTPUT_VERIFY_FAILED": "output_parse",
    "EXTERNAL_TOOL_FAILED": "external_tool",
    "INTERNAL_ERROR": "internal",
}
_REQUIRED_DETAILS_BY_CODE = {
    "INPUT_INVALID": ("field",),
    "REFERENCE_INVALID": ("reference_kind",),
    "UNSUPPORTED_FEATURE": ("feature",),
    "SCENE_INVALID": ("target",),
    "OWNERSHIP_CONFLICT": ("owner",),
    "ROUTE_UNRESOLVED": ("route",),
    "EXPORT_OPTIONS_INVALID": ("field",),
    "COLLECTION_FAILED": ("phase",),
    "STALE_STATE": ("snapshot_fingerprint",),
    "OUTPUT_WRITE_FAILED": ("phase", "exception_type"),
    "OUTPUT_VERIFY_FAILED": ("phase",),
    "EXTERNAL_TOOL_FAILED": ("tool", "phase"),
    "INTERNAL_ERROR": ("phase", "exception_type"),
}


def _stable_code(old_code):
    if old_code in _REFERENCE_CODES:
        return "REFERENCE_INVALID"
    if old_code in _UNSUPPORTED_CODES:
        return "UNSUPPORTED_FEATURE"
    for stable_code, old_codes in _GROUPS.items():
        if old_code in old_codes:
            return stable_code
    return "INPUT_INVALID"


def _fixture_emitter(stable_code):
    return {
        "INPUT_INVALID": "export_validator.validate_model_data",
        "REFERENCE_INVALID": "export_validator.validate_model_data",
        "UNSUPPORTED_FEATURE": "export_validator.validate_model_data",
        "SCENE_INVALID": "scene_preflight.run",
        "OWNERSHIP_CONFLICT": "scene_preflight.run",
        "ROUTE_UNRESOLVED": "vmd_scene_collector",
        "EXPORT_OPTIONS_INVALID": "scene_preflight.run",
        "COLLECTION_FAILED": "export_workflow_service",
        "STALE_STATE": "export_model_action",
        "OUTPUT_WRITE_FAILED": "export_workflow_service",
        "OUTPUT_VERIFY_FAILED": "output_verifier",
        "EXTERNAL_TOOL_FAILED": "mmd_anim_verifier",
        "INTERNAL_ERROR": "export_presenter",
    }[stable_code]


def _fixture_discriminator(old_code, stable_code):
    if old_code == "SCENE_OWNER_HUMANIK":
        return "ownership_humanik"
    if old_code.endswith("COUNT_MISMATCH"):
        return "output_count" if stable_code == "OUTPUT_VERIFY_FAILED" else "external_tool"
    if old_code in {"OUTPUT_FILE_EMPTY", "OUTPUT_FILE_MISSING"}:
        return "output_presence"
    if old_code == "OUTPUT_HEADER_INVALID":
        return "output_header"
    return _DISCRIMINATOR_BY_CODE[stable_code]


_BASE_MAPPING_FIXTURE = tuple(
    {
        "old_code": old_code,
        "emitter": _fixture_emitter(_stable_code(old_code)),
        "path_pattern": "legacy." + old_code.lower(),
        "stable_code": _stable_code(old_code),
        "aggregation_discriminator": _fixture_discriminator(old_code, _stable_code(old_code)),
        "required_details": _REQUIRED_DETAILS_BY_CODE[_stable_code(old_code)],
    }
    for old_code in sorted(_LEGACY_CODES)
)

_SENSITIVE_MAPPING_FIXTURE = (
    {
        "old_code": "OUTPUT_PARSE_FAILED",
        "emitter": "vmd_validator.validate_vmd_data",
        "path_pattern": "animation_data",
        "stable_code": "INPUT_INVALID",
        "aggregation_discriminator": "payload_shape",
        "required_details": ("field", "expected_type", "actual_type"),
    },
    {
        "old_code": "VMD_FRAME_COUNT_MISMATCH",
        "emitter": "vmd_validator.verify_vmd_output_streaming",
        "path_pattern": "expected_counts",
        "stable_code": "INPUT_INVALID",
        "aggregation_discriminator": "payload_shape",
        "required_details": ("expected_count_contract",),
    },
    {
        "old_code": "VMD_FRAME_RANGE",
        "emitter": "vmd_validator.validate_vmd_data",
        "path_pattern": "frame_range",
        "stable_code": "EXPORT_OPTIONS_INVALID",
        "aggregation_discriminator": "export_option",
        "required_details": ("frame_range",),
    },
    {
        "old_code": "VMD_FRAME_RANGE",
        "emitter": "vmd_validator.verify_vmd_output_streaming",
        "path_pattern": "output.*.frame_number",
        "stable_code": "OUTPUT_VERIFY_FAILED",
        "aggregation_discriminator": "output_range",
        "required_details": ("section", "expected_bounds", "actual_bounds"),
    },
    {
        "old_code": "MMD_ANIM_BINDING_INPUT_INVALID",
        "emitter": "mmd_anim_binding_verifier",
        "path_pattern": "binding.input",
        "stable_code": "INPUT_INVALID",
        "aggregation_discriminator": "payload_shape",
        "required_details": ("input_kind", "path"),
    },
)
_MAPPING_FIXTURE = _BASE_MAPPING_FIXTURE + _SENSITIVE_MAPPING_FIXTURE


class ValidationReportV2Tests(unittest.TestCase):
    """Keep taxonomy reduction and artifact parity fail-closed."""

    def test_catalog_is_exactly_the_stable_taxonomy(self):
        self.assertEqual(len(STABLE_ISSUE_CODES), 13)
        self.assertEqual(EXPORT_VALIDATION_SCHEMA_VERSION, 2)
        self.assertEqual(tuple(ISSUE_CATALOG), STABLE_ISSUE_CODES)

    def test_legacy_mapping_fixture_covers_145_emitters_without_collisions(self):
        self.assertEqual(len(_LEGACY_CODES), 145)
        self.assertEqual({row["old_code"] for row in _MAPPING_FIXTURE}, _LEGACY_CODES)
        self.assertTrue(
            all(row["stable_code"] in STABLE_ISSUE_CODES for row in _MAPPING_FIXTURE)
        )
        self.assertTrue(
            all(
                row["aggregation_discriminator"] in AGGREGATION_DISCRIMINATORS
                for row in _MAPPING_FIXTURE
            )
        )
        keys = {
            (
                row["stable_code"],
                "fatal",
                True,
                row["path_pattern"],
                row["aggregation_discriminator"],
            )
            for row in _MAPPING_FIXTURE
        }
        self.assertEqual(len(keys), len(_MAPPING_FIXTURE))
        self.assertTrue(all(row["required_details"] for row in _MAPPING_FIXTURE))

    def test_owned_production_emitters_use_all_and_only_stable_codes(self):
        root = Path(__file__).resolve().parents[2]
        sources = list((root / "mmd_tools" / "validation").glob("*.py"))
        sources.extend((root / "mmd_tools" / "actions").glob("*.py"))
        sources.extend(
            (
                root / "mmd_tools" / "services" / "export_workflow_service.py",
                root / "mmd_tools" / "converters" / "authoring_export_bridge.py",
                root / "mmd_tools" / "ui" / "presenters" / "export_presenter.py",
            )
        )
        literals = set()
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            literals.update(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            )
        removed_legacy_codes = _LEGACY_CODES.difference(STABLE_ISSUE_CODES)
        self.assertFalse(removed_legacy_codes.intersection(literals))
        self.assertEqual(set(STABLE_ISSUE_CODES).intersection(literals), set(STABLE_ISSUE_CODES))

    def test_more_than_100_mixed_causes_preserve_every_stable_family(self):
        issues = []
        for stable_code in STABLE_ISSUE_CODES:
            discriminator = _DISCRIMINATOR_BY_CODE[stable_code]
            for index in range(10):
                issues.append(
                    ExportValidationIssue(
                        stable_code,
                        "fatal",
                        True,
                        f"mixed.{stable_code.lower()}[{index}]",
                        f"{stable_code} fixture",
                        details={"aggregation_discriminator": discriminator},
                    )
                )
        report = ExportValidationReport("pmx", tuple(issues))
        payload = report.to_canonical_dict()
        markdown = report.to_markdown()
        self.assertEqual(report.issue_aggregation.total_occurrences, 130)
        self.assertEqual({issue["code"] for issue in payload["issues"]}, set(STABLE_ISSUE_CODES))
        self.assertTrue(all(f"`{code}`" in markdown for code in STABLE_ISSUE_CODES))

    def test_issue_rejects_unbounded_discriminator_and_micro_code_aliases(self):
        with self.assertRaises(ValueError):
            ExportValidationIssue(
                "INPUT_INVALID",
                "fatal",
                True,
                "payload",
                "bad",
                details={"aggregation_discriminator": "free-form reason"},
            )
        for forbidden in ("reason_id", "subcode"):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                ExportValidationIssue(
                    "INPUT_INVALID",
                    "fatal",
                    True,
                    "payload",
                    "bad",
                    details={forbidden: "old-code"},
                )

    def test_exception_adapter_accepts_only_registered_stable_codes(self):
        class RegisteredFailure(RuntimeError):
            validation_issue_code = "ROUTE_UNRESOLVED"
            validation_issue_path = "route.control_rig"

        report = structured_export_failure_report(RegisteredFailure("no route"), "vmd")
        self.assertEqual(report.issues[0].code, "ROUTE_UNRESOLVED")
        self.assertEqual(report.issues[0].details["route"], "exception_adapter")

        class UnknownFailure(RuntimeError):
            validation_issue_code = "VMD_UNKNOWN_ROUTE"

        with self.assertRaises(KeyError):
            structured_export_failure_report(UnknownFailure("unknown"), "vmd")

    def test_payload_issue_uses_stable_code_and_v2_fields(self):
        report = validate_model_data(
            {"vertices": [{"position": "bad"}], "faces": [[0, 0, 0]]},
            "pmx",
        )
        issue = report.to_canonical_dict()["issues"][0]
        self.assertEqual(issue["code"], "INPUT_INVALID")
        self.assertEqual(
            set(issue),
            {"code", "severity", "blocking", "path", "reason", "action", "details", "evidence", "provenance"},
        )
        self.assertFalse(
            {"message", "category", "observed", "expected", "impact", "remediation"}.intersection(issue)
        )

    def test_report_rejects_unregistered_detector_code(self):
        with self.assertRaises(KeyError):
            ExportValidationIssue("FACE_INDEX_OUT_OF_RANGE", "fatal", True, "faces[0][0]", "bad reference")

    def test_report_persists_direct_stable_codes(self):
        report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue("REFERENCE_INVALID", "fatal", True, "faces[0][0]", "bad reference"),
                ExportValidationIssue("UNSUPPORTED_FEATURE", "fatal", True, "vertices[0]", "unsupported"),
            ),
        )
        codes = [issue["code"] for issue in report.to_canonical_dict()["issues"]]
        self.assertEqual(codes, ["REFERENCE_INVALID", "UNSUPPORTED_FEATURE"])

    def test_artifact_pair_is_schema_v2_and_consistent(self):
        report = ExportValidationReport(
            "vmd",
            (ExportValidationIssue("EXPORT_OPTIONS_INVALID", "warning", False, "frame_range", "review range"),),
            mode="bake_timeline",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = write_validation_report_artifacts(report, Path(directory) / "run")
            payload = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            validate_report_consistency(paths.json_path, paths.markdown_path)


if __name__ == "__main__":
    unittest.main()
