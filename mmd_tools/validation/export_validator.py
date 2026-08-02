"""Maya-independent preflight validation for PMX/PMD model export.

The validator intentionally covers the exporter's structural input boundary,
not the complete PMX/PMD authoring schema.  It returns deterministic issue
objects so an action or a later report adapter can present the same findings.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from numbers import Integral, Number, Real
import json
import math
from typing import Any, Dict, List, Optional, Set, Tuple


# PMD vertex indices are unsigned 16-bit values, so the count is one larger
# than the maximum representable index.
PMD_MAX_VERTEX_COUNT = 0xFFFF + 1
PMD_MAX_BONE_COUNT = 0xFFFF
PMD_MAX_BONE_WEIGHT = 100
PMD_MAX_EDGE_FLAG = 0xFF
UINT32_MAX = 0xFFFFFFFF

_SEQUENCE_TYPES = (str, bytes, bytearray)
_BONE_REFERENCE_FIELDS = (
    "parent_index",
    "tail_pos_bone_index",
    "ik_parent_bone_index",
    "connect_bone_index",
    "grant_parent_bone_index",
    "ik_target_bone_index",
)
_PMX_MORPH_TYPE_BY_ENUM = {1: "vertex", 2: "bone", 8: "material"}
_PMX_MORPH_TYPES = frozenset(_PMX_MORPH_TYPE_BY_ENUM.values())
_PMD_BONE_TYPE_VALUES = frozenset(range(10))


@dataclass(frozen=True)
class ExportValidationIssue:
    """One deterministic finding produced by model-data preflight."""

    code: str
    severity: str
    blocking: bool
    path: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Return the stable machine-readable issue representation."""
        return {
            "code": self.code,
            "severity": self.severity,
            "blocking": self.blocking,
            "path": self.path,
            "message": self.message,
        }

    def __str__(self) -> str:
        """Return a compact human-readable representation."""
        location = f" at {self.path}" if self.path else ""
        return f"[{self.code}]{location}: {self.message}"


@dataclass(frozen=True)
class ExportValidationReport:
    """Structured result of PMX/PMD model-data preflight."""

    export_format: Optional[str]
    issues: Tuple[ExportValidationIssue, ...]

    @property
    def is_blocking(self) -> bool:
        """Return whether any issue must prevent the writer from running."""
        return any(issue.blocking for issue in self.issues)

    @property
    def valid(self) -> bool:
        """Return whether the model data passed the blocking checks."""
        return not self.is_blocking

    @property
    def blocking_issues(self) -> Tuple[ExportValidationIssue, ...]:
        """Return only issues that prevent export."""
        return tuple(issue for issue in self.issues if issue.blocking)

    def has_blocking_issues(self) -> bool:
        """Compatibility-friendly method form of :attr:`is_blocking`."""
        return self.is_blocking

    @property
    def summary(self) -> str:
        """Return a stable human-readable summary of the report."""
        if not self.issues:
            return "model data passed export validation"
        return "; ".join(str(issue) for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        """Return a deterministic, JSON-serializable report representation."""
        counts = {
            "fatal": sum(issue.severity == "fatal" for issue in self.issues),
            "warning": sum(issue.severity == "warning" for issue in self.issues),
            "info": sum(issue.severity == "info" for issue in self.issues),
        }
        has_non_blocking_warning = any(
            issue.severity == "warning" and not issue.blocking for issue in self.issues
        )
        if self.is_blocking:
            status = "blocked"
        elif has_non_blocking_warning:
            status = "warning"
        else:
            status = "ready"
        return {
            "schema_version": 1,
            "status": status,
            "requires_warning_ack": has_non_blocking_warning,
            "format": self.export_format,
            "mode": "model",
            "summary": counts,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def to_canonical_dict(
        self,
        *,
        target_identity: Optional[str] = None,
        snapshot_fingerprint: Optional[str] = None,
        provenance: str = "PayloadValidator",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return the catalog-backed report used by audit artifacts.

        ``to_dict`` remains the small compatibility representation used by
        existing callers.  This method is the v0.7 contract representation:
        every issue is enriched from the source-controlled issue catalog and
        an unregistered issue code fails closed.
        """
        from .issue_catalog import canonical_issue_dict

        return {
            "schema_version": 1,
            "status": self.to_dict()["status"],
            "requires_warning_ack": self.to_dict()["requires_warning_ack"],
            "format": self.export_format,
            "mode": "model",
            "target_identity": target_identity,
            "snapshot_fingerprint": snapshot_fingerprint,
            "summary": self.to_dict()["summary"],
            "issues": [
                canonical_issue_dict(
                    issue,
                    provenance=provenance,
                    snapshot_fingerprint=snapshot_fingerprint,
                    evidence=evidence,
                )
                for issue in self.issues
            ],
        }

    def to_canonical_json(
        self,
        *,
        target_identity: Optional[str] = None,
        snapshot_fingerprint: Optional[str] = None,
        provenance: str = "PayloadValidator",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return deterministic JSON for the catalog-backed audit report."""
        return json.dumps(
            self.to_canonical_dict(
                target_identity=target_identity,
                snapshot_fingerprint=snapshot_fingerprint,
                provenance=provenance,
                evidence=evidence,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def write_canonical_json(
        self,
        file_path,
        *,
        target_identity: Optional[str] = None,
        snapshot_fingerprint: Optional[str] = None,
        provenance: str = "PayloadValidator",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write the catalog-backed audit JSON with one final newline."""
        with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                self.to_canonical_json(
                    target_identity=target_identity,
                    snapshot_fingerprint=snapshot_fingerprint,
                    provenance=provenance,
                    evidence=evidence,
                )
            )
            handle.write("\n")

    def to_markdown(
        self,
        *,
        target_identity: Optional[str] = None,
        snapshot_fingerprint: Optional[str] = None,
        provenance: str = "PayloadValidator",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return the deterministic human-audit Markdown report."""
        from .issue_catalog import render_validation_report_markdown

        return render_validation_report_markdown(
            self,
            target_identity=target_identity,
            snapshot_fingerprint=snapshot_fingerprint,
            provenance=provenance,
            evidence=evidence,
        )

    def write_markdown(
        self,
        file_path,
        *,
        target_identity: Optional[str] = None,
        snapshot_fingerprint: Optional[str] = None,
        provenance: str = "PayloadValidator",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write the human-audit Markdown report with one final newline."""
        with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                self.to_markdown(
                    target_identity=target_identity,
                    snapshot_fingerprint=snapshot_fingerprint,
                    provenance=provenance,
                    evidence=evidence,
                )
            )

    def to_json(self) -> str:
        """Return the canonical JSON representation of this report."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def write_json(self, file_path) -> None:
        """Write the canonical report JSON to *file_path* with one final newline."""
        with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.to_json())
            handle.write("\n")


class ExportValidationError(ValueError):
    """Error carrying the blocking report returned by model-data preflight."""

    def __init__(self, report: ExportValidationReport):
        self.report = report
        format_name = (report.export_format or "model").upper()
        super().__init__(f"{format_name} export validation failed: {report.summary}")


def _is_sequence(value: Any) -> bool:
    """Return whether *value* is a non-text sequence accepted by exporters."""
    return isinstance(value, Sequence) and not isinstance(value, _SEQUENCE_TYPES)


def _is_real(value: Any) -> bool:
    """Return whether *value* is a finite-checkable real number type."""
    return isinstance(value, Real) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    """Return whether *value* is an integer rather than a bool masquerading as one."""
    return isinstance(value, Integral) and not isinstance(value, bool)


def _is_non_finite_numeric(value: Any) -> bool:
    """Return whether the recursive numeric scan reports *value* as invalid."""
    if isinstance(value, bool) or not isinstance(value, Number):
        return False
    try:
        return not math.isfinite(value)
    except (TypeError, ValueError):
        return True


def _path_for_key(path: str, key: Any) -> str:
    """Build a deterministic path for a mapping member."""
    if isinstance(key, str) and key.isidentifier():
        return f"{path}.{key}" if path else key
    return f"{path}[{key!r}]" if path else repr(key)


def _path_for_index(path: str, index: int) -> str:
    """Build a deterministic path for a sequence member."""
    return f"{path}[{index}]" if path else f"[{index}]"


def _issue(
    issues: List[ExportValidationIssue],
    code: str,
    path: str,
    message: str,
) -> None:
    """Append one blocking error issue."""
    issues.append(
        ExportValidationIssue(
            code=code,
            severity="fatal",
            blocking=True,
            path=path,
            message=message,
        )
    )


def _scan_non_finite_numbers(
    value: Any,
    path: str,
    issues: List[ExportValidationIssue],
    active: Set[int],
) -> None:
    """Find non-finite numeric payloads without assuming a full schema.

    Only mappings and sequences are traversed.  This keeps the validator
    independent from Maya/native DTO classes while still covering supported
    nested vertex, bone, material, morph, and physics payloads represented by
    ordinary collector-shaped dictionaries and lists.
    """
    if isinstance(value, bool) or value is None or isinstance(value, _SEQUENCE_TYPES):
        return

    if isinstance(value, Number):
        try:
            finite = math.isfinite(value)
        except (TypeError, ValueError):
            _issue(issues, "NUMERIC_VALUE_TYPE", path, "numeric payload must be a real number")
        else:
            if not finite:
                _issue(issues, "NON_FINITE_NUMBER", path, "numeric payload must be finite")
        return

    if not isinstance(value, (Mapping, Sequence)):
        return

    value_id = id(value)
    if value_id in active:
        return
    active.add(value_id)
    try:
        if isinstance(value, Mapping):
            for key, child in value.items():
                _scan_non_finite_numbers(child, _path_for_key(path, key), issues, active)
        else:
            for index, child in enumerate(value):
                _scan_non_finite_numbers(child, _path_for_index(path, index), issues, active)
    finally:
        active.remove(value_id)


def _validate_real(value: Any, path: str, issues: List[ExportValidationIssue]) -> None:
    """Validate one real-valued field without coercing it."""
    if not _is_real(value):
        _issue(issues, "NUMERIC_VALUE_TYPE", path, "value must be a real number")


def _validate_text_fields(
    mapping: Mapping,
    field_names: Sequence[str],
    path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate optional writer-facing text fields without applying defaults."""
    for field_name in field_names:
        if field_name not in mapping:
            continue
        if not isinstance(mapping[field_name], str):
            _issue(
                issues,
                "TEXT_FIELD_TYPE",
                _path_for_key(path, field_name),
                "field must be a string",
            )


def _validate_vector_field(
    mapping: Mapping,
    field_name: str,
    expected_length: int,
    path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate an optional fixed-size real-valued vector field."""
    if field_name not in mapping:
        return
    value = mapping[field_name]
    field_path = _path_for_key(path, field_name)
    if not _is_sequence(value):
        _issue(issues, "FIELD_NOT_SEQUENCE", field_path, "field must be a numeric sequence")
        return
    if len(value) != expected_length:
        _issue(
            issues,
            "FIELD_LENGTH",
            field_path,
            f"field must contain exactly {expected_length} values",
        )
        return
    for index, item in enumerate(value):
        _validate_real(item, _path_for_index(field_path, index), issues)


def _validate_numeric_sequence(
    mapping: Mapping,
    field_name: str,
    path: str,
    issues: List[ExportValidationIssue],
    *,
    integer: bool = False,
) -> None:
    """Validate an optional variable-length numeric sequence field."""
    if field_name not in mapping:
        return
    value = mapping[field_name]
    field_path = _path_for_key(path, field_name)
    if not _is_sequence(value):
        _issue(issues, "FIELD_NOT_SEQUENCE", field_path, "field must be a numeric sequence")
        return
    checker = _is_integer if integer else _is_real
    message = "value must be an integer" if integer else "value must be a real number"
    for index, item in enumerate(value):
        if not checker(item):
            _issue(issues, "NUMERIC_VALUE_TYPE", _path_for_index(field_path, index), message)


def _validate_sequence_max_length(
    mapping: Mapping,
    field_name: str,
    path: str,
    maximum: int,
    issues: List[ExportValidationIssue],
    *,
    label: str,
) -> None:
    """Reject sequence values that the selected writer would truncate."""
    if field_name not in mapping or not _is_sequence(mapping[field_name]):
        return
    value = mapping[field_name]
    if len(value) > maximum:
        _issue(
            issues,
            "BONE_WEIGHTS_LENGTH",
            _path_for_key(path, field_name),
            f"{label} must contain at most {maximum} values",
        )


def _validate_numeric_fields(
    mapping: Mapping,
    field_names: Sequence[str],
    path: str,
    issues: List[ExportValidationIssue],
    *,
    integer_fields: Sequence[str] = (),
    allow_none_fields: Sequence[str] = (),
) -> None:
    """Validate optional scalar numeric fields used by the current writers."""
    integer_names = set(integer_fields)
    none_names = set(allow_none_fields)
    for field_name in field_names:
        if field_name not in mapping:
            continue
        if mapping[field_name] is None and field_name in none_names:
            continue
        field_path = _path_for_key(path, field_name)
        if field_name in integer_names:
            if not _is_integer(mapping[field_name]):
                _issue(issues, "NUMERIC_VALUE_TYPE", field_path, "value must be an integer")
        else:
            _validate_real(mapping[field_name], field_path, issues)


def _validate_vertices(
    vertices: Sequence,
    bone_count: int,
    export_format: Optional[str],
    issues: List[ExportValidationIssue],
) -> None:
    """Validate vertex shape, optional numeric fields, and bone references."""
    for vertex_index, vertex in enumerate(vertices):
        vertex_path = _path_for_index("vertices", vertex_index)
        if not isinstance(vertex, Mapping):
            _issue(issues, "VERTEX_NOT_MAPPING", vertex_path, "vertex must be a mapping")
            continue

        if export_format == "pmx":
            _validate_pmx_vertex_unsupported_fields(vertex, vertex_path, issues)
        _validate_vector_field(vertex, "position", 3, vertex_path, issues)
        _validate_vector_field(vertex, "normal", 3, vertex_path, issues)
        _validate_vector_field(vertex, "uv", 2, vertex_path, issues)
        _validate_numeric_sequence(vertex, "bone_weights", vertex_path, issues)
        numeric_fields = ("edge_magnification",)
        if export_format != "pmd":
            numeric_fields += ("bone_weight", "edge_flag")
        _validate_numeric_fields(
            vertex,
            numeric_fields,
            vertex_path,
            issues,
            integer_fields=("edge_flag",) if export_format != "pmd" else (),
        )
        if export_format == "pmd":
            _validate_integer_range_field(
                vertex,
                "bone_weight",
                vertex_path,
                0,
                PMD_MAX_BONE_WEIGHT,
                issues,
                type_code="PMD_BONE_WEIGHT_TYPE",
                range_code="PMD_BONE_WEIGHT_RANGE",
            )
            _validate_integer_range_field(
                vertex,
                "edge_flag",
                vertex_path,
                0,
                PMD_MAX_EDGE_FLAG,
                issues,
                type_code="PMD_EDGE_FLAG_TYPE",
                range_code="PMD_EDGE_FLAG_RANGE",
            )
        if export_format in ("pmx", "pmd"):
            _validate_sequence_max_length(
                vertex,
                "bone_weights",
                vertex_path,
                2 if export_format == "pmd" else 4,
                issues,
                label="PMD bone_weights" if export_format == "pmd" else "PMX bone_weights",
            )

        if "bone_indices" not in vertex:
            bone_indices = (0,)
        else:
            bone_indices = vertex["bone_indices"]
            field_path = _path_for_key(vertex_path, "bone_indices")
            if not _is_sequence(bone_indices):
                _issue(issues, "FIELD_NOT_SEQUENCE", field_path, "bone_indices must be an integer sequence")
                continue
            if export_format == "pmx" and len(bone_indices) not in (1, 2, 4):
                _issue(
                    issues,
                    "BONE_INDICES_LENGTH",
                    field_path,
                    "PMX bone_indices must contain 1, 2, or 4 values",
                )
            elif export_format == "pmd" and len(bone_indices) not in (1, 2):
                _issue(
                    issues,
                    "BONE_INDICES_LENGTH",
                    field_path,
                    "PMD bone_indices must contain 1 or 2 values",
                )

        for index, bone_index in enumerate(bone_indices):
            index_path = _path_for_index(_path_for_key(vertex_path, "bone_indices"), index)
            if not _is_integer(bone_index):
                _issue(issues, "BONE_INDEX_TYPE", index_path, "bone index must be an integer")
                continue
            if bone_index < 0 or bone_index >= bone_count:
                _issue(
                    issues,
                    "BONE_INDEX_OUT_OF_RANGE",
                    index_path,
                    f"bone index {bone_index} is outside effective bone count {bone_count}",
                )


def _validate_faces(faces: Sequence, vertex_count: int, issues: List[ExportValidationIssue]) -> None:
    """Validate polygon index sequences before exporter triangulation."""
    for face_index, face in enumerate(faces):
        face_path = _path_for_index("faces", face_index)
        if not _is_sequence(face):
            _issue(issues, "FACE_NOT_SEQUENCE", face_path, "face must be an integer sequence")
            continue
        if len(face) < 3:
            _issue(issues, "FACE_TOO_SHORT", face_path, "face must contain at least 3 vertex indices")
        for vertex_index, index in enumerate(face):
            index_path = _path_for_index(face_path, vertex_index)
            if not _is_integer(index):
                _issue(issues, "FACE_INDEX_TYPE", index_path, "face vertex index must be an integer")
                continue
            if index < 0 or index >= vertex_count:
                _issue(
                    issues,
                    "FACE_INDEX_OUT_OF_RANGE",
                    index_path,
                    f"face vertex index {index} is outside vertex count {vertex_count}",
                )


def _expected_index_count(faces: Any) -> Optional[int]:
    """Return the writer's fan-triangulated index count for valid face shapes."""
    if not _is_sequence(faces) or not faces:
        return None

    index_count = 0
    for face in faces:
        if not _is_sequence(face) or len(face) < 3:
            return None
        index_count += (len(face) - 2) * 3
    return index_count


def _validate_bone_references(
    bone: Mapping,
    bone_path: str,
    bone_count: int,
    issues: List[ExportValidationIssue],
    export_format: Optional[str],
) -> None:
    """Validate explicit bone references without adding cycle semantics."""
    for field_name in _BONE_REFERENCE_FIELDS:
        if field_name not in bone:
            continue
        field_path = _path_for_key(bone_path, field_name)
        value = bone[field_name]
        if export_format == "pmd" and field_name in {
            "parent_index",
            "tail_pos_bone_index",
            "ik_parent_bone_index",
        }:
            if not _is_integer(value):
                if not _is_non_finite_numeric(value):
                    _issue(
                        issues,
                        "PMD_BONE_REFERENCE_TYPE",
                        field_path,
                        "PMD bone reference must be an integer",
                    )
                continue
            if field_name == "parent_index":
                is_sentinel = value in (-1, 0xFFFF)
            else:
                is_sentinel = value == 0xFFFF
            if not is_sentinel and not 0 <= value < bone_count:
                _issue(
                    issues,
                    "PMD_BONE_REFERENCE_OUT_OF_RANGE",
                    field_path,
                    f"PMD bone reference {value} is outside effective bone count {bone_count}",
                )
            continue
        if not _is_integer(value):
            # NaN/Inf/complex values are already reported by the recursive
            # scan; avoid adding a duplicate type issue for those values.
            if not _is_non_finite_numeric(value):
                _issue(issues, "BONE_REFERENCE_TYPE", field_path, "bone reference must be an integer")
            continue
        if value != -1 and not 0 <= value < bone_count:
            _issue(
                issues,
                "BONE_REFERENCE_OUT_OF_RANGE",
                field_path,
                f"bone reference {value} is outside effective bone count {bone_count}",
            )


def _validate_bone_ik_links(
    bone: Mapping,
    bone_path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Reject PMX bone IK links that the model-data writer does not retain."""
    if "ik_links" not in bone:
        return
    value = bone["ik_links"]
    field_path = _path_for_key(bone_path, "ik_links")
    if value is None:
        return
    if not _is_sequence(value):
        _issue(
            issues,
            "PMX_BONE_IK_LINKS_NOT_SEQUENCE",
            field_path,
            "bone ik_links must be a sequence",
        )
    elif value:
        _issue(
            issues,
            "PMX_BONE_IK_LINKS_UNSUPPORTED",
            field_path,
            "PMX model-data export does not retain bone ik_links",
        )


def _is_pmd_bone_type(value: Any) -> bool:
    """Return whether *value* is a PMD bone type accepted by the writer."""
    if _is_integer(value):
        return int(value) in _PMD_BONE_TYPE_VALUES
    if isinstance(value, Enum) and value.__class__.__name__ == "PmdBoneType":
        return _is_integer(value.value) and int(value.value) in _PMD_BONE_TYPE_VALUES
    return False


def _validate_pmd_bone_type(
    bone: Mapping,
    bone_path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate the PMD writer's one-byte bone type enum boundary."""
    if "bone_type" not in bone:
        return
    if not _is_pmd_bone_type(bone["bone_type"]):
        _issue(
            issues,
            "PMD_BONE_TYPE",
            _path_for_key(bone_path, "bone_type"),
            "PMD bone_type must be PmdBoneType or an integer in range [0, 9]",
        )


def _validate_bones(
    bones: Sequence,
    bone_count: int,
    export_format: Optional[str],
    issues: List[ExportValidationIssue],
) -> None:
    """Validate explicit bone entries and their supported numeric payloads."""
    for bone_index, bone in enumerate(bones):
        bone_path = _path_for_index("bones", bone_index)
        if not isinstance(bone, Mapping):
            _issue(issues, "BONE_NOT_MAPPING", bone_path, "bone must be a mapping")
            continue
        _validate_text_fields(bone, ("name", "name_english"), bone_path, issues)
        _validate_vector_field(bone, "position", 3, bone_path, issues)
        _validate_vector_field(bone, "connect_position_offset", 3, bone_path, issues)
        _validate_vector_field(bone, "axis_direction", 3, bone_path, issues)
        _validate_vector_field(bone, "x_axis_direction", 3, bone_path, issues)
        _validate_vector_field(bone, "z_axis_direction", 3, bone_path, issues)
        _validate_bone_references(bone, bone_path, bone_count, issues, export_format)
        if export_format == "pmd":
            _validate_pmd_bone_type(bone, bone_path, issues)
        if export_format != "pmd":
            _validate_bone_ik_links(bone, bone_path, issues)
        _validate_numeric_fields(
            bone,
            (
                "transform_layer",
                "bone_flag",
                "grant_rate",
                "key_value",
                "ik_loop_count",
                "ik_limit_angle",
            ),
            bone_path,
            issues,
            integer_fields=(
                "transform_layer",
                "bone_flag",
                "key_value",
                "ik_loop_count",
            ),
        )


def _normalize_pmx_morph_type(value: Any) -> Optional[str]:
    """Return the current PMX writer's normalized supported morph type."""
    if isinstance(value, str):
        normalized = value.lower()
        return normalized if normalized in _PMX_MORPH_TYPES else None
    if _is_integer(value):
        return _PMX_MORPH_TYPE_BY_ENUM.get(int(value))
    return None


def _validate_morph_offset_index(
    offset: Mapping,
    field_name: str,
    offset_path: str,
    effective_count: int,
    issues: List[ExportValidationIssue],
    *,
    allow_minus_one: bool = False,
) -> None:
    """Validate one required morph offset index and its effective range."""
    field_path = _path_for_key(offset_path, field_name)
    if field_name not in offset:
        _issue(
            issues,
            "MORPH_OFFSET_INDEX_MISSING",
            field_path,
            f"morph offset requires {field_name}",
        )
        return
    value = offset[field_name]
    if not _is_integer(value):
        if not _is_non_finite_numeric(value):
            _issue(issues, "MORPH_OFFSET_INDEX_TYPE", field_path, "morph offset index must be an integer")
        return
    if allow_minus_one and value == -1:
        return
    if value < 0 or value >= effective_count:
        _issue(
            issues,
            "MORPH_OFFSET_INDEX_OUT_OF_RANGE",
            field_path,
            f"morph offset index {value} is outside effective count {effective_count}",
        )


def _validate_pmx_morph_offsets(
    morph_type: str,
    offsets: Sequence,
    morph_path: str,
    vertex_count: int,
    bone_count: int,
    material_count: int,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate supported PMX morph offset mappings and writer fields."""
    for offset_index, offset in enumerate(offsets):
        offset_path = _path_for_index(_path_for_key(morph_path, "offsets"), offset_index)
        if not isinstance(offset, Mapping):
            _issue(issues, "MORPH_OFFSET_NOT_MAPPING", offset_path, "morph offset must be a mapping")
            continue

        if morph_type == "vertex":
            _validate_morph_offset_index(offset, "vertex_index", offset_path, vertex_count, issues)
            _validate_vector_field(offset, "position_offset", 3, offset_path, issues)
        elif morph_type == "bone":
            _validate_morph_offset_index(offset, "bone_index", offset_path, bone_count, issues)
            _validate_vector_field(offset, "translation", 3, offset_path, issues)
            _validate_vector_field(offset, "rotation", 4, offset_path, issues)
        else:
            _validate_morph_offset_index(
                offset,
                "material_index",
                offset_path,
                material_count,
                issues,
                allow_minus_one=True,
            )
            for field_name, length in (
                ("diffuse", 4),
                ("specular", 3),
                ("ambient", 3),
                ("edge_color", 4),
                ("texture_factor", 4),
                ("sphere_texture_factor", 4),
                ("toon_texture_factor", 4),
            ):
                _validate_vector_field(offset, field_name, length, offset_path, issues)
            _validate_integer_range_field(
                offset,
                "operation_type",
                offset_path,
                0,
                0xFF,
                issues,
                type_code="MORPH_FIELD_TYPE",
                range_code="MORPH_FIELD_RANGE",
            )
            _validate_numeric_fields(
                offset,
                ("specular_coefficient", "edge_size"),
                offset_path,
                issues,
            )


def _validate_morphs(
    morphs: Any,
    export_format: Optional[str],
    vertex_count: int,
    bone_count: int,
    material_count: int,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate PMX morph input or report PMD's unsupported/lossy path."""
    if morphs is None:
        return
    if not _is_sequence(morphs):
        _issue(issues, "MORPHS_NOT_SEQUENCE", "morphs", "morphs must be a sequence")
        return
    if not morphs:
        return
    if export_format == "pmd":
        _issue(
            issues,
            "PMD_MORPHS_UNSUPPORTED",
            "morphs",
            "PMD export does not serialize morphs",
        )
        return

    for morph_index, morph in enumerate(morphs):
        morph_path = _path_for_index("morphs", morph_index)
        if not isinstance(morph, Mapping):
            _issue(issues, "MORPH_NOT_MAPPING", morph_path, "morph must be a mapping")
            continue

        if "type" in morph:
            type_field = "type"
        elif "morph_type" in morph:
            type_field = "morph_type"
        else:
            type_field = "type"
        raw_type = morph.get(type_field, "vertex")
        normalized_type = _normalize_pmx_morph_type(raw_type)
        if normalized_type is None:
            _issue(
                issues,
                "MORPH_TYPE_UNSUPPORTED",
                _path_for_key(morph_path, type_field),
                f"PMX morph type {raw_type!r} is unsupported",
            )
            continue

        _validate_text_fields(morph, ("name", "name_english"), morph_path, issues)
        _validate_integer_range_field(
            morph,
            "panel",
            morph_path,
            0,
            0xFF,
            issues,
            type_code="MORPH_FIELD_TYPE",
            range_code="MORPH_FIELD_RANGE",
        )

        offsets = morph.get("offsets", ())
        offsets_path = _path_for_key(morph_path, "offsets")
        if not _is_sequence(offsets):
            _issue(issues, "MORPH_OFFSETS_NOT_SEQUENCE", offsets_path, "morph offsets must be a sequence")
            continue
        _validate_pmx_morph_offsets(
            normalized_type,
            offsets,
            morph_path,
            vertex_count,
            bone_count,
            material_count,
            issues,
        )


def _validate_optional_reference(
    mapping: Mapping,
    field_name: str,
    path: str,
    effective_count: int,
    issues: List[ExportValidationIssue],
    *,
    type_code: str,
    range_code: str,
    label: str,
) -> None:
    """Validate an optional -1-or-in-range integer reference."""
    if field_name not in mapping:
        return
    field_path = _path_for_key(path, field_name)
    value = mapping[field_name]
    if not _is_integer(value):
        if not _is_non_finite_numeric(value):
            _issue(issues, type_code, field_path, f"{label} must be an integer")
        return
    if value != -1 and not 0 <= value < effective_count:
        _issue(
            issues,
            range_code,
            field_path,
            f"{label} {value} is outside effective count {effective_count}",
        )


def _validate_integer_range_field(
    mapping: Mapping,
    field_name: str,
    path: str,
    minimum: int,
    maximum: int,
    issues: List[ExportValidationIssue],
    *,
    type_code: str,
    range_code: str,
    allow_none: bool = False,
) -> None:
    """Validate an optional integer field and its inclusive numeric range."""
    if field_name not in mapping:
        return
    field_path = _path_for_key(path, field_name)
    value = mapping[field_name]
    if value is None and allow_none:
        return
    if not _is_integer(value):
        if not _is_non_finite_numeric(value):
            _issue(issues, type_code, field_path, "field must be an integer")
        return
    if value < minimum or value > maximum:
        _issue(
            issues,
            range_code,
            field_path,
            f"field value {value} must be in range [{minimum}, {maximum}]",
        )


def _optional_sequence(
    value: Any,
    field_name: str,
    issues: List[ExportValidationIssue],
) -> Optional[Sequence]:
    """Validate a top-level optional collection while preserving None/empty defaults."""
    if value is None:
        return None
    if not _is_sequence(value):
        _issue(issues, f"{field_name.upper()}_NOT_SEQUENCE", field_name, f"{field_name} must be a sequence")
        return None
    return value


def _is_meaningful_payload(value: Any) -> bool:
    """Return whether an optional ignored payload contains exportable data."""
    if value is None or _is_non_finite_numeric(value):
        return False
    if isinstance(value, Mapping) or _is_sequence(value):
        return bool(value)
    if isinstance(value, _SEQUENCE_TYPES):
        return bool(value)
    if isinstance(value, Number):
        return value != 0
    return True


def _validate_pmx_vertex_unsupported_fields(
    vertex: Mapping,
    vertex_path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Reject PMX vertex payloads that the model-data writer does not retain."""
    for field_name in ("additional_uvs", "additional_uv"):
        if field_name not in vertex or not _is_meaningful_payload(vertex[field_name]):
            continue
        _issue(
            issues,
            "PMX_VERTEX_ADDITIONAL_UV_UNSUPPORTED",
            _path_for_key(vertex_path, field_name),
            "PMX model-data export does not retain vertex additional UVs",
        )

    for field_name in ("sdef_c", "sdef_r0", "sdef_r1"):
        if field_name not in vertex or vertex[field_name] is None:
            continue
        _issue(
            issues,
            "PMX_VERTEX_SDEF_UNSUPPORTED",
            _path_for_key(vertex_path, field_name),
            "PMX model-data export does not retain vertex SDEF payload",
        )

    if "weight_transform_type" not in vertex:
        return
    value = vertex["weight_transform_type"]
    if _is_non_finite_numeric(value) or (isinstance(value, Number) and value == 0):
        return
    _issue(
        issues,
        "PMX_VERTEX_SKINNING_TYPE_UNSUPPORTED",
        _path_for_key(vertex_path, "weight_transform_type"),
        "PMX model-data export only retains BDEF weight transform type 0",
    )


def _validate_textures(
    value: Any,
    export_format: Optional[str],
    issues: List[ExportValidationIssue],
) -> Optional[Sequence]:
    """Validate the optional texture table and PMD's lossy texture path."""
    textures = _optional_sequence(value, "textures", issues)
    if textures is None:
        return None
    if export_format == "pmd":
        if textures:
            _issue(
                issues,
                "PMD_TEXTURES_UNSUPPORTED",
                "textures",
                "PMD export does not serialize the top-level texture table",
            )
        return textures

    for texture_index, texture in enumerate(textures):
        texture_path = _path_for_index("textures", texture_index)
        if not isinstance(texture, str) and not _is_non_finite_numeric(texture):
            _issue(
                issues,
                "TEXTURE_NOT_STRING",
                texture_path,
                "PMX texture path must be a string",
            )
    return textures


def _validate_unsupported_top_level_payloads(
    model_data: Mapping,
    export_format: Optional[str],
    issues: List[ExportValidationIssue],
) -> None:
    """Reject meaningful top-level payloads ignored by the selected writer."""
    format_name = "PMD" if export_format == "pmd" else "PMX"
    for field_name in ("ik_data", "soft_bodies", "additional_uv"):
        if field_name not in model_data or not _is_meaningful_payload(model_data[field_name]):
            continue
        _issue(
            issues,
            f"{format_name}_{field_name.upper()}_UNSUPPORTED",
            field_name,
            f"{format_name} export does not retain top-level {field_name}",
        )


def _validate_display_frames(
    display_frames: Sequence,
    bone_count: int,
    morph_count: int,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate PMX display-frame mappings before normalization can drop data."""
    for frame_index, frame in enumerate(display_frames):
        frame_path = _path_for_index("display_frames", frame_index)
        if not isinstance(frame, Mapping):
            _issue(issues, "DISPLAY_FRAME_NOT_MAPPING", frame_path, "display frame must be a mapping")
            continue
        _validate_text_fields(frame, ("name", "name_english"), frame_path, issues)
        _validate_integer_range_field(
            frame,
            "special_flag",
            frame_path,
            0,
            1,
            issues,
            type_code="DISPLAY_FRAME_FIELD_TYPE",
            range_code="DISPLAY_FRAME_FIELD_RANGE",
        )
        if "elements" not in frame:
            continue
        elements_path = _path_for_key(frame_path, "elements")
        elements = frame["elements"]
        if not _is_sequence(elements):
            _issue(issues, "DISPLAY_ELEMENTS_NOT_SEQUENCE", elements_path, "display frame elements must be a sequence")
            continue
        for element_index, element in enumerate(elements):
            element_path = _path_for_index(elements_path, element_index)
            if not isinstance(element, Mapping):
                _issue(issues, "DISPLAY_ELEMENT_NOT_MAPPING", element_path, "display frame element must be a mapping")
                continue

            type_path = _path_for_key(element_path, "type")
            if "type" not in element:
                _issue(issues, "DISPLAY_ELEMENT_TYPE_MISSING", type_path, "display frame element requires type")
                element_type = None
            elif not _is_integer(element["type"]):
                if not _is_non_finite_numeric(element["type"]):
                    _issue(issues, "DISPLAY_ELEMENT_TYPE_TYPE", type_path, "display frame element type must be an integer")
                element_type = None
            elif element["type"] not in (0, 1):
                _issue(
                    issues,
                    "DISPLAY_ELEMENT_TYPE_UNSUPPORTED",
                    type_path,
                    "display frame element type must be 0 (bone) or 1 (morph)",
                )
                element_type = None
            else:
                element_type = element["type"]

            index_path = _path_for_key(element_path, "index")
            if "index" not in element:
                _issue(issues, "DISPLAY_ELEMENT_INDEX_MISSING", index_path, "display frame element requires index")
                continue
            index = element["index"]
            if not _is_integer(index):
                if not _is_non_finite_numeric(index):
                    _issue(issues, "DISPLAY_ELEMENT_INDEX_TYPE", index_path, "display frame element index must be an integer")
                continue
            if element_type == 0:
                effective_count = bone_count
                label = "display frame bone index"
            elif element_type == 1:
                effective_count = morph_count
                label = "display frame morph index"
            else:
                continue
            if index < 0 or index >= effective_count:
                _issue(
                    issues,
                    "DISPLAY_ELEMENT_INDEX_OUT_OF_RANGE",
                    index_path,
                    f"{label} {index} is outside effective count {effective_count}",
                )


def _validate_rigid_bodies(
    rigid_bodies: Sequence,
    bone_count: int,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate PMX rigid-body references, byte fields, vectors, and scalars."""
    for rigid_body_index, rigid_body in enumerate(rigid_bodies):
        rigid_body_path = _path_for_index("rigid_bodies", rigid_body_index)
        if not isinstance(rigid_body, Mapping):
            _issue(issues, "RIGID_BODY_NOT_MAPPING", rigid_body_path, "rigid body must be a mapping")
            continue
        _validate_text_fields(rigid_body, ("name", "name_english"), rigid_body_path, issues)
        _validate_optional_reference(
            rigid_body,
            "related_bone_index",
            rigid_body_path,
            bone_count,
            issues,
            type_code="RIGID_BODY_BONE_REFERENCE_TYPE",
            range_code="RIGID_BODY_BONE_REFERENCE_OUT_OF_RANGE",
            label="rigid body related bone index",
        )
        for field_name, minimum, maximum in (
            ("group", 0, 0xFF),
            ("collision_mask", 0, 0xFFFF),
            ("shape_type", 0, 0xFF),
            ("physics_mode", 0, 0xFF),
        ):
            _validate_integer_range_field(
                rigid_body,
                field_name,
                rigid_body_path,
                minimum,
                maximum,
                issues,
                type_code="RIGID_BODY_FIELD_TYPE",
                range_code="RIGID_BODY_FIELD_RANGE",
            )
        for field_name in ("size", "position", "rotation"):
            _validate_vector_field(rigid_body, field_name, 3, rigid_body_path, issues)
        _validate_numeric_fields(
            rigid_body,
            (
                "mass",
                "velocity_attenuation",
                "rotation_attenuation",
                "elasticity",
                "friction",
            ),
            rigid_body_path,
            issues,
        )


def _validate_joints(
    joints: Sequence,
    rigid_body_count: int,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate PMX joint references, byte fields, and vector payloads."""
    for joint_index, joint in enumerate(joints):
        joint_path = _path_for_index("joints", joint_index)
        if not isinstance(joint, Mapping):
            _issue(issues, "JOINT_NOT_MAPPING", joint_path, "joint must be a mapping")
            continue
        _validate_text_fields(joint, ("name", "name_english"), joint_path, issues)
        _validate_integer_range_field(
            joint,
            "joint_type",
            joint_path,
            0,
            0xFF,
            issues,
            type_code="JOINT_FIELD_TYPE",
            range_code="JOINT_FIELD_RANGE",
        )
        for field_name in ("rigid_body_a_index", "rigid_body_b_index"):
            _validate_optional_reference(
                joint,
                field_name,
                joint_path,
                rigid_body_count,
                issues,
                type_code="JOINT_RIGID_BODY_REFERENCE_TYPE",
                range_code="JOINT_RIGID_BODY_REFERENCE_OUT_OF_RANGE",
                label=f"joint {field_name}",
            )
        for field_name in (
            "position",
            "rotation",
            "translation_limit_min",
            "translation_limit_max",
            "rotation_limit_min",
            "rotation_limit_max",
            "spring_translation",
            "spring_rotation",
        ):
            _validate_vector_field(joint, field_name, 3, joint_path, issues)


def _validate_materials(
    materials: Any,
    export_format: Optional[str],
    texture_count: int,
    issues: List[ExportValidationIssue],
    expected_index_count: Optional[int] = None,
) -> None:
    """Validate material entries and writer-facing index/range boundaries.

    Writers assign omitted ``face_count`` values from the remaining geometry,
    then correct the final material if the explicit counts do not add up.  A
    preflight report must reject only the explicit cases that would trigger
    that correction.
    """
    if materials is None:
        return
    if not _is_sequence(materials):
        _issue(issues, "MATERIALS_NOT_SEQUENCE", "materials", "materials must be a sequence")
        return
    if not materials:
        return
    specified_total = 0
    all_face_counts_specified = True
    has_invalid_face_count = False
    for material_index, material in enumerate(materials):
        material_path = _path_for_index("materials", material_index)
        if not isinstance(material, Mapping):
            _issue(issues, "MATERIAL_NOT_MAPPING", material_path, "material must be a mapping")
            all_face_counts_specified = False
            continue
        if export_format == "pmd":
            _validate_text_fields(material, ("texture_file_name",), material_path, issues)
        else:
            _validate_text_fields(material, ("name", "name_english", "memo"), material_path, issues)
        _validate_vector_field(material, "diffuse", 4, material_path, issues)
        _validate_vector_field(material, "specular", 3, material_path, issues)
        _validate_vector_field(material, "ambient", 3, material_path, issues)
        _validate_vector_field(material, "edge_color", 4, material_path, issues)
        numeric_fields = ["specular_power", "specular_coefficient", "edge_size"]
        integer_fields = []
        if export_format != "pmd":
            numeric_fields.append("edge_flag")
            integer_fields.append("edge_flag")
        _validate_numeric_fields(
            material,
            numeric_fields,
            material_path,
            issues,
            integer_fields=integer_fields,
        )
        _validate_integer_range_field(
            material,
            "face_count",
            material_path,
            0,
            UINT32_MAX,
            issues,
            type_code="MATERIAL_FACE_COUNT_TYPE",
            range_code="MATERIAL_FACE_COUNT_RANGE",
            allow_none=True,
        )
        face_count = material.get("face_count")
        if "face_count" not in material or face_count is None:
            all_face_counts_specified = False
        elif not _is_integer(face_count) or not 0 <= face_count <= UINT32_MAX:
            has_invalid_face_count = True
        else:
            specified_total += face_count

        if export_format == "pmd":
            _validate_integer_range_field(
                material,
                "toon_texture_index",
                material_path,
                0,
                0xFF,
                issues,
                type_code="MATERIAL_TOON_TEXTURE_INDEX_TYPE",
                range_code="MATERIAL_TOON_TEXTURE_INDEX_RANGE",
            )
            _validate_integer_range_field(
                material,
                "edge_flag",
                material_path,
                0,
                0xFF,
                issues,
                type_code="MATERIAL_EDGE_FLAG_TYPE",
                range_code="MATERIAL_EDGE_FLAG_RANGE",
            )
            _validate_numeric_fields(
                material,
                (
                    "texture_index",
                    "sphere_texture_index",
                    "sphere_mode",
                    "shared_toon_flag",
                    "draw_flag",
                ),
                material_path,
                issues,
                integer_fields=(
                    "texture_index",
                    "sphere_texture_index",
                    "sphere_mode",
                    "shared_toon_flag",
                    "draw_flag",
                ),
            )
            continue

        _validate_optional_reference(
            material,
            "texture_index",
            material_path,
            texture_count,
            issues,
            type_code="MATERIAL_TEXTURE_INDEX_TYPE",
            range_code="MATERIAL_TEXTURE_INDEX_RANGE",
            label="material texture index",
        )
        _validate_optional_reference(
            material,
            "sphere_texture_index",
            material_path,
            texture_count,
            issues,
            type_code="MATERIAL_SPHERE_TEXTURE_INDEX_TYPE",
            range_code="MATERIAL_SPHERE_TEXTURE_INDEX_RANGE",
            label="material sphere texture index",
        )
        _validate_integer_range_field(
            material,
            "sphere_mode",
            material_path,
            0,
            3,
            issues,
            type_code="MATERIAL_SPHERE_MODE_TYPE",
            range_code="MATERIAL_SPHERE_MODE_RANGE",
        )
        _validate_integer_range_field(
            material,
            "shared_toon_flag",
            material_path,
            0,
            1,
            issues,
            type_code="MATERIAL_SHARED_TOON_FLAG_TYPE",
            range_code="MATERIAL_SHARED_TOON_FLAG_RANGE",
        )
        _validate_integer_range_field(
            material,
            "draw_flag",
            material_path,
            0,
            0xFF,
            issues,
            type_code="MATERIAL_DRAW_FLAG_TYPE",
            range_code="MATERIAL_DRAW_FLAG_RANGE",
        )

        shared_toon_flag = material.get("shared_toon_flag", 0)
        if _is_integer(shared_toon_flag) and shared_toon_flag in (0, 1):
            if shared_toon_flag == 0:
                _validate_optional_reference(
                    material,
                    "toon_texture_index",
                    material_path,
                    texture_count,
                    issues,
                    type_code="MATERIAL_TOON_TEXTURE_INDEX_TYPE",
                    range_code="MATERIAL_TOON_TEXTURE_INDEX_RANGE",
                    label="material toon texture index",
                )
            else:
                if "toon_texture_index" not in material:
                    _issue(
                        issues,
                        "MATERIAL_TOON_TEXTURE_INDEX_MISSING",
                        _path_for_key(material_path, "toon_texture_index"),
                        "shared toon materials require toon_texture_index",
                    )
                else:
                    _validate_integer_range_field(
                        material,
                        "toon_texture_index",
                        material_path,
                        0,
                        9,
                        issues,
                        type_code="MATERIAL_TOON_TEXTURE_INDEX_TYPE",
                        range_code="MATERIAL_TOON_TEXTURE_INDEX_RANGE",
                    )

    if expected_index_count is None or has_invalid_face_count:
        return

    if specified_total > expected_index_count:
        _issue(
            issues,
            "MATERIAL_FACE_COUNT_EXCEEDS_GEOMETRY",
            "materials",
            "specified material face_count total "
            f"{specified_total} exceeds triangulated geometry index count "
            f"{expected_index_count}",
        )
        return

    if all_face_counts_specified and specified_total != expected_index_count:
        _issue(
            issues,
            "MATERIAL_FACE_COUNT_TOTAL_MISMATCH",
            "materials",
            "material face_count total "
            f"{specified_total} does not match triangulated geometry index count "
            f"{expected_index_count}",
        )


def _effective_material_count(materials: Any) -> int:
    """Return the material count used for morph references by the writers."""
    if _is_sequence(materials) and materials:
        return len(materials)
    return 1


def validate_model_data(
    model_data: Any,
    export_format: Optional[str] = None,
) -> ExportValidationReport:
    """Validate collector-shaped PMX/PMD model data.

    Args:
        model_data: Mapping containing ``vertices`` and ``faces``.
        export_format: Optional ``"pmx"`` or ``"pmd"`` format selector.  PMD
            applies its existing 16-bit vertex-count limit when selected.

    Returns:
        A deterministic report.  A report with blocking issues must not be
        passed to a PMX/PMD writer.
    """
    normalized_format = export_format.lower().lstrip(".") if isinstance(export_format, str) else None
    issues: List[ExportValidationIssue] = []

    if not isinstance(model_data, Mapping):
        _issue(issues, "MODEL_DATA_NOT_MAPPING", "model_data", "model data must be a mapping")
        return ExportValidationReport(normalized_format, tuple(issues))

    _scan_non_finite_numbers(model_data, "", issues, set())
    _validate_text_fields(
        model_data,
        ("model_name", "model_name_english", "comment", "comment_english"),
        "",
        issues,
    )

    vertices = model_data.get("vertices")
    if not _is_sequence(vertices):
        _issue(issues, "VERTICES_NOT_SEQUENCE", "vertices", "vertices must be a non-empty sequence")
        vertices = None
    elif not vertices:
        _issue(issues, "VERTICES_EMPTY", "vertices", "vertices must not be empty")

    faces = model_data.get("faces")
    if not _is_sequence(faces):
        _issue(issues, "FACES_NOT_SEQUENCE", "faces", "faces must be a non-empty sequence")
        faces = None
    elif not faces:
        _issue(issues, "FACES_EMPTY", "faces", "faces must not be empty")

    bones_value = model_data.get("bones")
    bone_count = 1
    bones = None
    if bones_value is not None:
        if not _is_sequence(bones_value):
            _issue(issues, "BONES_NOT_SEQUENCE", "bones", "explicit bones must be a non-empty sequence")
            bone_count = 0
        else:
            bones = bones_value
            bone_count = len(bones)
            if not bones:
                _issue(issues, "BONES_EMPTY", "bones", "explicit bones must not be empty")

    if vertices is not None:
        if normalized_format == "pmd" and len(vertices) > PMD_MAX_VERTEX_COUNT:
            _issue(
                issues,
                "PMD_VERTEX_LIMIT",
                "vertices",
                f"PMD supports at most {PMD_MAX_VERTEX_COUNT} vertices, got {len(vertices)}",
            )
        _validate_vertices(vertices, bone_count, normalized_format, issues)
    if faces is not None and vertices is not None:
        _validate_faces(faces, len(vertices), issues)
    if bones is not None:
        if normalized_format == "pmd" and len(bones) > PMD_MAX_BONE_COUNT:
            _issue(
                issues,
                "PMD_BONE_LIMIT",
                "bones",
                f"PMD supports at most {PMD_MAX_BONE_COUNT} bones, got {len(bones)}",
            )
        _validate_bones(bones, bone_count, normalized_format, issues)
    textures = _validate_textures(model_data.get("textures"), normalized_format, issues)
    texture_count = len(textures) if textures is not None else 0
    materials = model_data.get("materials")
    _validate_materials(
        materials,
        normalized_format,
        texture_count,
        issues,
        _expected_index_count(faces),
    )
    _validate_morphs(
        model_data.get("morphs"),
        normalized_format,
        len(vertices) if vertices is not None else 0,
        bone_count,
        _effective_material_count(materials),
        issues,
    )
    _validate_unsupported_top_level_payloads(model_data, normalized_format, issues)

    display_frames = _optional_sequence(model_data.get("display_frames"), "display_frames", issues)
    rigid_bodies = _optional_sequence(model_data.get("rigid_bodies"), "rigid_bodies", issues)
    joints = _optional_sequence(model_data.get("joints"), "joints", issues)
    rigid_body_count = len(rigid_bodies) if rigid_bodies is not None else 0
    if normalized_format == "pmd":
        for value, field_name, message in (
            (display_frames, "display_frames", "PMD export does not retain display frames"),
            (rigid_bodies, "rigid_bodies", "PMD export does not retain rigid bodies"),
            (joints, "joints", "PMD export does not retain joints"),
        ):
            if value:
                _issue(issues, f"PMD_{field_name.upper()}_UNSUPPORTED", field_name, message)
    else:
        morphs = model_data.get("morphs")
        morph_count = len(morphs) if _is_sequence(morphs) else 0
        if display_frames:
            _validate_display_frames(display_frames, bone_count, morph_count, issues)
        if rigid_bodies:
            _validate_rigid_bodies(rigid_bodies, bone_count, issues)
        if joints:
            if rigid_body_count == 0:
                _issue(
                    issues,
                    "JOINTS_REQUIRE_RIGID_BODIES",
                    "joints",
                    "joints require at least one rigid body",
                )
            _validate_joints(joints, rigid_body_count, issues)

    return ExportValidationReport(normalized_format, tuple(issues))


# Explicit aliases keep the small module easy to discover for callers that
# use either the model-data or export-validation vocabulary.
validate_export_model = validate_model_data
ModelValidationIssue = ExportValidationIssue
ModelValidationReport = ExportValidationReport


__all__ = [
    "ExportValidationError",
    "ExportValidationIssue",
    "ExportValidationReport",
    "ModelValidationIssue",
    "ModelValidationReport",
    "PMD_MAX_BONE_COUNT",
    "PMD_MAX_BONE_WEIGHT",
    "PMD_MAX_EDGE_FLAG",
    "PMD_MAX_VERTEX_COUNT",
    "validate_export_model",
    "validate_model_data",
]
