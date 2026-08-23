"""Maya-independent structural verification for PMX export output.

The verifier runs after a writer has produced a temporary file.  It checks
the format header, parser acceptance, and the basic section counts that can be
compared with the validated model payload before an atomic target replace.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, List, Optional

from .export_validator import ExportValidationIssue, ExportValidationReport


_TEXT_SEQUENCE_TYPES = (str, bytes, bytearray)


def _is_sequence(value: Any) -> bool:
    """Return whether *value* is a non-text sequence."""
    return isinstance(value, Sequence) and not isinstance(value, _TEXT_SEQUENCE_TYPES)


def _issue(
    code: str,
    path: str,
    message: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
) -> ExportValidationIssue:
    """Build one blocking output-verifier issue."""
    issue_details = dict(details or {})
    issue_details.setdefault("field", path)
    if code == "OUTPUT_VERIFY_FAILED":
        issue_details.setdefault("phase", "verify")
    return ExportValidationIssue(code, "fatal", True, path, message, details=issue_details)


def _expected_sequence_count(
    model_data: Mapping,
    field_name: str,
    default: int,
    *,
    empty_uses_default: bool = False,
) -> Optional[int]:
    """Return an expected section count when the payload has that section."""
    if field_name not in model_data or model_data[field_name] is None:
        return default
    value = model_data[field_name]
    if not _is_sequence(value):
        return None
    if empty_uses_default and not value:
        return default
    return len(value)


def _expected_face_count(model_data: Mapping) -> Optional[int]:
    """Return the number of triangles the writers should serialize."""
    if "faces" not in model_data or model_data["faces"] is None:
        return None
    faces = model_data["faces"]
    if not _is_sequence(faces):
        return None
    triangles = 0
    for face in faces:
        if not _is_sequence(face) or len(face) < 3:
            return None
        triangles += 1 if len(face) == 3 else len(face) - 2
    return triangles


def _compare_count(
    issues: List[ExportValidationIssue],
    code: str,
    path: str,
    section_name: str,
    expected: Optional[int],
    actual: int,
) -> None:
    """Append a deterministic count mismatch issue when both counts exist."""
    if expected is None or expected == actual:
        return
    issues.append(
        _issue(
            code,
            path,
            f"{section_name} count {actual} does not match expected count {expected}",
            details={
                "section": section_name,
                "expected_count": expected,
                "actual_count": actual,
                "aggregation_discriminator": section_name,
            },
        )
    )


def _parse_output(file_path: Path, export_format: str):
    """Parse one output using the local format parser without native fallback."""
    from ..core.mmd_parser import parse_pmx_file

    return parse_pmx_file(str(file_path), use_native_pmx_parse=False)


def verify_model_output(
    file_path: str,
    export_format: str,
    model_data: Optional[Mapping] = None,
) -> ExportValidationReport:
    """Verify a temporary PMX output before it replaces a target.

    Args:
        file_path: Temporary output path written by the model exporter.
        export_format: ``"pmx"``.
        model_data: Optional validated payload used for section-count checks.

    Returns:
        A report whose blocking issues must prevent atomic target replacement.
    """
    normalized_format = (export_format or "").lower().lstrip(".")
    issues: List[ExportValidationIssue] = []
    if normalized_format != "pmx":
        issues.append(
            _issue(
                "EXPORT_OPTIONS_INVALID",
                "format",
                f"output verifier does not support format {normalized_format or 'empty'}",
                details={"format": normalized_format or "empty"},
            )
        )
        return ExportValidationReport(normalized_format or None, tuple(issues))

    output_path = Path(file_path)
    if not output_path.is_file():
        issues.append(
            _issue(
                "OUTPUT_VERIFY_FAILED",
                "output",
                "temporary output file does not exist",
                details={"phase": "presence", "aggregation_discriminator": "output_presence"},
            )
        )
        return ExportValidationReport(normalized_format, tuple(issues))
    if output_path.stat().st_size == 0:
        issues.append(
            _issue(
                "OUTPUT_VERIFY_FAILED",
                "output",
                "temporary output file is empty",
                details={"phase": "size", "aggregation_discriminator": "output_presence"},
            )
        )
        return ExportValidationReport(normalized_format, tuple(issues))

    expected_header = b"PMX "
    header_size = len(expected_header)
    with output_path.open("rb") as handle:
        actual_header = handle.read(header_size)
    if actual_header != expected_header:
        issues.append(
            _issue(
                "OUTPUT_VERIFY_FAILED",
                "output.header",
                f"{normalized_format.upper()} output header is not {expected_header!r}",
                details={
                    "expected_header": expected_header.decode("ascii"),
                    "actual_header": actual_header.hex(),
                    "aggregation_discriminator": "output_header",
                },
            )
        )
        return ExportValidationReport(normalized_format, tuple(issues))

    try:
        parsed = _parse_output(output_path, normalized_format)
    except Exception as exc:
        issues.append(
            _issue(
                "OUTPUT_VERIFY_FAILED",
                "output",
                f"{normalized_format.upper()} output parser raised {type(exc).__name__}",
                details={
                    "phase": "parse",
                    "exception_type": type(exc).__name__,
                    "aggregation_discriminator": "output_parse",
                },
            )
        )
        return ExportValidationReport(normalized_format, tuple(issues))

    if isinstance(model_data, Mapping):
        expected_vertices = _expected_sequence_count(model_data, "vertices", 0)
        expected_faces = _expected_face_count(model_data)
        expected_materials = _expected_sequence_count(
            model_data,
            "materials",
            1,
            empty_uses_default=True,
        )
        expected_bones = _expected_sequence_count(model_data, "bones", 1)
        _compare_count(
            issues,
            "OUTPUT_VERIFY_FAILED",
            "output.vertices",
            "vertex",
            expected_vertices,
            len(parsed.vertices),
        )
        _compare_count(
            issues,
            "OUTPUT_VERIFY_FAILED",
            "output.faces",
            "triangle",
            expected_faces,
            len(parsed.faces),
        )
        _compare_count(
            issues,
            "OUTPUT_VERIFY_FAILED",
            "output.materials",
            "material",
            expected_materials,
            len(parsed.materials),
        )
        _compare_count(
            issues,
            "OUTPUT_VERIFY_FAILED",
            "output.bones",
            "bone",
            expected_bones,
            len(parsed.bones),
        )

    return ExportValidationReport(normalized_format, tuple(issues))


__all__ = ["verify_model_output"]
