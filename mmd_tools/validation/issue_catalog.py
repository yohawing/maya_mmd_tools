"""Source-controlled wording and policy metadata for export validation issues.

The payload validator owns observed values and paths.  This module owns the
stable human-audit wording and policy metadata that can be rendered into a
canonical JSON or Markdown report without generating text at runtime.
"""

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, Mapping, Optional


class UnknownValidationIssueError(KeyError):
    """Raised when a canonical report contains an unregistered issue code."""


@dataclass(frozen=True)
class IssueCatalogEntry:
    """Fixed report metadata for one validation issue code."""

    code: str
    category: str
    title: str
    title_key: str
    action_key: str
    expected: str
    impact: str
    remediation: str
    loss_policy: str = "reject"


# Keep this list explicit.  The validator may add a code only after its
# human-facing policy has been registered here as well.
_KNOWN_ISSUE_CODES = (
    "BONE_INDEX_OUT_OF_RANGE",
    "BONE_INDEX_TYPE",
    "BONE_INDICES_LENGTH",
    "BONE_NOT_MAPPING",
    "BONE_REFERENCE_OUT_OF_RANGE",
    "BONE_REFERENCE_TYPE",
    "BONE_WEIGHTS_LENGTH",
    "BONES_EMPTY",
    "BONES_NOT_SEQUENCE",
    "DISPLAY_ELEMENT_INDEX_MISSING",
    "DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE",
    "DISPLAY_ELEMENT_INDEX_TYPE",
    "DISPLAY_ELEMENT_NOT_MAPPING",
    "DISPLAY_ELEMENT_TYPE_MISSING",
    "DISPLAY_ELEMENT_TYPE_TYPE",
    "DISPLAY_ELEMENT_TYPE_UNSUPPORTED",
    "DISPLAY_ELEMENTS_NOT_SEQUENCE",
    "DISPLAY_FRAME_FIELD_RANGE",
    "DISPLAY_FRAME_FIELD_TYPE",
    "DISPLAY_FRAME_NOT_MAPPING",
    "DISPLAY_FRAMES_NOT_SEQUENCE",
    "FACE_INDEX_OUT_OF_RANGE",
    "FACE_INDEX_TYPE",
    "FACE_NOT_SEQUENCE",
    "FACE_TOO_SHORT",
    "FACES_EMPTY",
    "FACES_NOT_SEQUENCE",
    "FIELD_LENGTH",
    "FIELD_NOT_SEQUENCE",
    "JOINT_FIELD_RANGE",
    "JOINT_FIELD_TYPE",
    "JOINT_NOT_MAPPING",
    "JOINT_RIGID_BODY_REFERENCE_OUT_OF_RANGE",
    "JOINT_RIGID_BODY_REFERENCE_TYPE",
    "JOINTS_NOT_SEQUENCE",
    "JOINTS_REQUIRE_RIGID_BODIES",
    "MATERIAL_DRAW_FLAG_RANGE",
    "MATERIAL_DRAW_FLAG_TYPE",
    "MATERIAL_EDGE_FLAG_RANGE",
    "MATERIAL_EDGE_FLAG_TYPE",
    "MATERIAL_FACE_COUNT_EXCEEDS_GEOMETRY",
    "MATERIAL_FACE_COUNT_RANGE",
    "MATERIAL_FACE_COUNT_TOTAL_MISMATCH",
    "MATERIAL_FACE_COUNT_TYPE",
    "MATERIAL_NOT_MAPPING",
    "MATERIAL_SHARED_TOON_FLAG_RANGE",
    "MATERIAL_SHARED_TOON_FLAG_TYPE",
    "MATERIAL_SPHERE_MODE_RANGE",
    "MATERIAL_SPHERE_MODE_TYPE",
    "MATERIAL_SPHERE_TEXTURE_INDEX_RANGE",
    "MATERIAL_SPHERE_TEXTURE_INDEX_TYPE",
    "MATERIAL_TEXTURE_INDEX_RANGE",
    "MATERIAL_TEXTURE_INDEX_TYPE",
    "MATERIAL_TOON_TEXTURE_INDEX_MISSING",
    "MATERIAL_TOON_TEXTURE_INDEX_RANGE",
    "MATERIAL_TOON_TEXTURE_INDEX_TYPE",
    "MATERIALS_NOT_SEQUENCE",
    "MMD_ANIM_CLI_UNAVAILABLE",
    "MMD_ANIM_COMMAND_FAILED",
    "MMD_ANIM_COUNT_MISMATCH",
    "MMD_ANIM_DIAGNOSTICS",
    "MMD_ANIM_INSPECT_JSON_INVALID",
    "MMD_ANIM_ROUNDTRIP_FAILED",
    "MMD_ANIM_ROUNDTRIP_JSON_INVALID",
    "MMD_ANIM_TIMEOUT",
    "MODEL_DATA_NOT_MAPPING",
    "MORPH_FIELD_RANGE",
    "MORPH_FIELD_TYPE",
    "MORPH_NOT_MAPPING",
    "MORPH_OFFSET_INDEX_MISSING",
    "MORPH_OFFSET_INDEX_OUT_OF_RANGE",
    "MORPH_OFFSET_INDEX_TYPE",
    "MORPH_OFFSET_NOT_MAPPING",
    "MORPH_OFFSETS_NOT_SEQUENCE",
    "MORPH_TYPE_UNSUPPORTED",
    "MORPHS_NOT_SEQUENCE",
    "NON_FINITE_NUMBER",
    "NUMERIC_VALUE_TYPE",
    "OUTPUT_BONE_COUNT_MISMATCH",
    "OUTPUT_FACE_COUNT_MISMATCH",
    "OUTPUT_FILE_EMPTY",
    "OUTPUT_FILE_MISSING",
    "OUTPUT_FORMAT_UNSUPPORTED",
    "OUTPUT_HEADER_INVALID",
    "OUTPUT_MATERIAL_COUNT_MISMATCH",
    "OUTPUT_PARSE_FAILED",
    "OUTPUT_VERTEX_COUNT_MISMATCH",
    "PMD_ADDITIONAL_UV_UNSUPPORTED",
    "PMD_BONE_LIMIT",
    "PMD_BONE_REFERENCE_OUT_OF_RANGE",
    "PMD_BONE_REFERENCE_TYPE",
    "PMD_BONE_TYPE",
    "PMD_BONE_WEIGHT_RANGE",
    "PMD_BONE_WEIGHT_TYPE",
    "PMD_DISPLAY_FRAMES_UNSUPPORTED",
    "PMD_EDGE_FLAG_RANGE",
    "PMD_EDGE_FLAG_TYPE",
    "PMD_IK_DATA_UNSUPPORTED",
    "PMD_JOINTS_UNSUPPORTED",
    "PMD_MORPHS_UNSUPPORTED",
    "PMD_RIGID_BODIES_UNSUPPORTED",
    "PMD_SOFT_BODIES_UNSUPPORTED",
    "PMD_TEXTURES_UNSUPPORTED",
    "PMD_VERTEX_LIMIT",
    "PMX_ADDITIONAL_UV_UNSUPPORTED",
    "PMX_BONE_IK_LINKS_NOT_SEQUENCE",
    "PMX_BONE_IK_LINKS_UNSUPPORTED",
    "PMX_IK_DATA_UNSUPPORTED",
    "PMX_SOFT_BODIES_UNSUPPORTED",
    "PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED",
    "PMX_VERTEX_SDEF_UNSUPPORTED",
    "PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED",
    "RIGID_BODY_BONE_REFERENCE_OUT_OF_RANGE",
    "RIGID_BODY_BONE_REFERENCE_TYPE",
    "RIGID_BODY_FIELD_RANGE",
    "RIGID_BODY_FIELD_TYPE",
    "RIGID_BODY_NOT_MAPPING",
    "RIGID_BODIES_NOT_SEQUENCE",
    "STALE_VALIDATION_SNAPSHOT",
    "TEXT_FIELD_TYPE",
    "TEXTURE_NOT_STRING",
    "VERTEX_NOT_MAPPING",
    "VERTICES_EMPTY",
    "VERTICES_NOT_SEQUENCE",
)


_CATEGORY_TEXT = {
    "bones": {
        "expected": "Bone entries and references must form a valid exported bone table.",
        "impact": "The exported bone hierarchy or skin references would be invalid.",
        "remediation": "Repair the bone table or its references before exporting.",
    },
    "display": {
        "expected": "Display-frame entries must use supported bone or morph references.",
        "impact": "The display-frame section would contain an invalid or lossy reference.",
        "remediation": "Repair the display-frame entry or remove unsupported data.",
    },
    "geometry": {
        "expected": "Geometry fields must have supported shapes, finite values, and valid indices.",
        "impact": "The exported mesh would be malformed or would reference unavailable geometry.",
        "remediation": "Repair the vertex, face, or skin payload before exporting.",
    },
    "materials": {
        "expected": "Material and texture fields must match the selected format and geometry.",
        "impact": "Material assignment or texture references would be invalid or silently lost.",
        "remediation": "Repair the material/texture table or use a supported export format.",
    },
    "morphs": {
        "expected": "Morph entries must use supported types and valid target references.",
        "impact": "The exported morph section would be invalid or lose authored data.",
        "remediation": "Use a supported morph type and repair its target references.",
    },
    "model": {
        "expected": "The model payload must match the export contract and contain finite values.",
        "impact": "The exporter cannot produce a trustworthy model file from this payload.",
        "remediation": "Repair the model payload before exporting.",
    },
    "output": {
        "expected": "The temporary output must have a valid header, parse successfully, and preserve required counts.",
        "impact": "The generated file cannot be accepted as a trustworthy export artifact.",
        "remediation": "Inspect the writer output and fix the export path before replacing the target file.",
    },
    "physics": {
        "expected": "Rigid-body and joint entries must use valid supported references and fields.",
        "impact": "The exported physics section would be invalid or would lose authored data.",
        "remediation": "Repair the physics references/fields or remove unsupported physics data.",
    },
}


def _category_for_code(code: str) -> str:
    """Return the fixed report category for a registered issue code."""
    if code.startswith("OUTPUT_") or code.startswith("MMD_ANIM_"):
        return "output"
    if code.startswith("BONE") or code.startswith("PMD_BONE") or code.startswith("PMX_BONE"):
        return "bones"
    if code.startswith("DISPLAY") or code.startswith("PMD_DISPLAY"):
        return "display"
    if code.startswith("FACE") or code.startswith("VERTEX") or code.startswith("VERTICES"):
        return "geometry"
    if (
        code.startswith("MATERIAL")
        or code.startswith("TEXTURE")
        or code.startswith("PMD_TEXTURE")
        or code.startswith("PMD_EDGE")
    ):
        return "materials"
    if code.startswith("MORPH") or code.startswith("PMD_MORPH"):
        return "morphs"
    if (
        code.startswith("JOINT")
        or code.startswith("RIGID")
        or code.startswith("PMD_JOINT")
        or code.startswith("PMD_RIGID")
    ):
        return "physics"
    if (
        code.startswith("PMD_VERTEX")
        or code.startswith("PMX_VERTEX")
        or code.endswith("_ADDITIONAL_UV_UNSUPPORTED")
    ):
        return "geometry"
    if code in {"FIELD_LENGTH", "FIELD_NOT_SEQUENCE", "NON_FINITE_NUMBER", "NUMERIC_VALUE_TYPE"}:
        return "model"
    return "model"


def _humanize_code(code: str) -> str:
    """Convert a stable issue code into a readable catalog title."""
    return code.replace("_", " ").title()


def _build_catalog_entry(code: str) -> IssueCatalogEntry:
    category = _category_for_code(code)
    wording = _CATEGORY_TEXT[category]
    key = code.lower()
    return IssueCatalogEntry(
        code=code,
        category=category,
        title=_humanize_code(code),
        title_key=f"validation.{key}.title",
        action_key=f"validation.{key}.action",
        expected=wording["expected"],
        impact=wording["impact"],
        remediation=wording["remediation"],
    )


ISSUE_CATALOG = {code: _build_catalog_entry(code) for code in _KNOWN_ISSUE_CODES}


def get_issue_catalog_entry(code: str) -> IssueCatalogEntry:
    """Return catalog metadata for *code* or raise for an unregistered code."""
    try:
        return ISSUE_CATALOG[code]
    except KeyError as exc:
        raise UnknownValidationIssueError(
            f"Validation issue code is not registered in the issue catalog: {code}"
        ) from exc


def validate_issue_catalog(codes: Iterable[str]) -> None:
    """Fail if any code in *codes* is absent from the source-controlled catalog."""
    unknown = sorted(set(codes).difference(ISSUE_CATALOG))
    if unknown:
        joined = ", ".join(unknown)
        raise UnknownValidationIssueError(f"Unregistered validation issue codes: {joined}")


def canonical_issue_dict(
    issue: Any,
    *,
    provenance: str = "PayloadValidator",
    snapshot_fingerprint: Optional[str] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Render one validator issue using fixed catalog wording."""
    entry = get_issue_catalog_entry(issue.code)
    issue_evidence = dict(evidence or {})
    if snapshot_fingerprint is not None:
        issue_evidence.setdefault("snapshot_fingerprint", snapshot_fingerprint)
    return {
        "code": issue.code,
        "severity": issue.severity,
        "blocking": issue.blocking,
        "loss_policy": entry.loss_policy,
        "category": entry.category,
        "path": issue.path,
        "subject": None,
        "title_key": entry.title_key,
        "observed": issue.message,
        "expected": entry.expected,
        "impact": entry.impact,
        "action_key": entry.action_key,
        "message": issue.message,
        "remediation": entry.remediation,
        "provenance": provenance,
        "evidence": issue_evidence,
    }


def render_validation_report_markdown(
    report: Any,
    *,
    target_identity: Optional[str] = None,
    snapshot_fingerprint: Optional[str] = None,
    provenance: str = "PayloadValidator",
    evidence: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render a deterministic human-audit Markdown report from one report."""
    payload = report.to_canonical_dict(
        target_identity=target_identity,
        snapshot_fingerprint=snapshot_fingerprint,
        provenance=provenance,
        evidence=evidence,
    )
    summary = payload["summary"]
    lines = [
        "# Export Validation Report",
        "",
        f"- Status: `{payload['status'].upper()}`",
        f"- Format: `{payload['format'] or 'unknown'}`",
        f"- Mode: `{payload['mode']}`",
        f"- Target: `{payload['target_identity'] or 'unspecified'}`",
        f"- Snapshot fingerprint: `{payload['snapshot_fingerprint'] or 'unspecified'}`",
        "",
        "## Summary",
        "",
        f"- Fatal: {summary['fatal']}",
        f"- Warning: {summary['warning']}",
        f"- Info: {summary['info']}",
        f"- Warning acknowledgement required: `{str(payload['requires_warning_ack']).lower()}`",
        "",
        "## Issues",
        "",
    ]
    if not payload["issues"]:
        lines.append("No validation issues.")
    else:
        for index, issue in enumerate(payload["issues"], start=1):
            entry = get_issue_catalog_entry(issue["code"])
            lines.extend(
                [
                    f"### {index}. [{issue['severity'].upper()}] `{issue['code']}`",
                    "",
                    f"- Category: `{issue['category']}`",
                    f"- Path: `{issue['path'] or 'model_data'}`",
                    f"- Decision: `{'BLOCK' if issue['blocking'] else 'ALLOW'}`",
                    f"- Title: {entry.title}",
                    f"- Observed: {issue['observed']}",
                    f"- Expected: {issue['expected']}",
                    f"- Impact: {issue['impact']}",
                    f"- Remediation: {issue['remediation']}",
                    f"- Provenance: `{issue['provenance']}`",
                    f"- Evidence: `{json.dumps(issue['evidence'], ensure_ascii=False, sort_keys=True)}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ISSUE_CATALOG",
    "IssueCatalogEntry",
    "UnknownValidationIssueError",
    "canonical_issue_dict",
    "get_issue_catalog_entry",
    "render_validation_report_markdown",
    "validate_issue_catalog",
]
