"""Maya-independent structural validation for VMD Mode A/C export."""

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..core.vmd_data import VmdData
from .export_validator import ExportValidationIssue, ExportValidationReport


VMD_MODE_A = "A"
VMD_MODE_C = "C"
VMD_MODES = frozenset({VMD_MODE_A, VMD_MODE_C})


def _issue(code: str, path: str, message: str) -> ExportValidationIssue:
    """Create a deterministic blocking VMD issue."""
    return ExportValidationIssue(code, "fatal", True, path, message)


def _finite_values(values: Iterable[Any], path: str, issues: List[ExportValidationIssue]) -> None:
    """Append one issue for the first non-finite numeric value."""
    try:
        for index, value in enumerate(values):
            if not math.isfinite(float(value)):
                issues.append(
                    _issue(
                        "VMD_NON_FINITE_NUMBER",
                        f"{path}[{index}]",
                        "VMD numeric value must be finite",
                    )
                )
                return
    except (TypeError, ValueError, OverflowError):
        issues.append(
            _issue(
                "VMD_NON_FINITE_NUMBER",
                path,
                "VMD numeric value must be a finite number",
            )
        )


def _frame_number(frame: Any, path: str, issues: List[ExportValidationIssue]) -> None:
    """Validate the unsigned VMD frame number contract."""
    try:
        value = int(frame.frame_number)
    except (AttributeError, TypeError, ValueError, OverflowError):
        issues.append(_issue("VMD_FRAME_NEGATIVE", path, "VMD frame number must be a non-negative integer"))
        return
    if value < 0:
        issues.append(_issue("VMD_FRAME_NEGATIVE", path, "VMD frame number must be non-negative"))


def _name(value: Any, path: str, issues: List[ExportValidationIssue]) -> None:
    """Reject nameless bone/morph entries before fixed-width encoding."""
    if not str(value or "").strip():
        issues.append(_issue("VMD_NAME_EMPTY", path, "VMD bone or morph name must not be empty"))


def _interpolation(value: Any, expected: int, path: str, issues: List[ExportValidationIssue]) -> None:
    """Validate fixed-size VMD interpolation bytes without rewriting them."""
    try:
        actual = len(value)
    except TypeError:
        actual = -1
    if actual != expected:
        issues.append(
            _issue(
                "VMD_BONE_INTERPOLATION_LENGTH"
                if expected == 64
                else "VMD_CAMERA_INTERPOLATION_LENGTH",
                path,
                f"VMD interpolation payload must contain {expected} bytes",
            )
        )


def _raw_bone_provenance_records(
    raw_provenance: Any,
    frame_range: Optional[Tuple[int, int]] = None,
) -> Tuple[bool, Optional[Dict[Tuple[str, int], bytes]]]:
    """Normalize complete raw bone records, scoped to the requested range."""
    if not isinstance(raw_provenance, Mapping) or "raw_bone_interpolation" not in raw_provenance:
        return False, None
    records = raw_provenance.get("raw_bone_interpolation")
    if not isinstance(records, list) or not raw_provenance.get("raw_bone_interpolation_complete"):
        return True, None
    try:
        expected_count = int(raw_provenance.get("raw_bone_key_count", len(records)))
    except (TypeError, ValueError, OverflowError):
        return True, None
    if expected_count != len(records):
        return True, None

    normalized: Dict[Tuple[str, int], bytes] = {}
    for record in records:
        if not isinstance(record, Mapping):
            return True, None
        name = str(record.get("bone_name") or "")
        try:
            frame_number = int(record.get("frame_number"))
            interpolation = bytes(record.get("interpolation", ()))
        except (TypeError, ValueError, OverflowError):
            return True, None
        key = (name, frame_number)
        if not name or frame_number < 0 or len(interpolation) != 64 or key in normalized:
            return True, None
        normalized[key] = interpolation
    if frame_range is not None:
        start, end = frame_range
        normalized = {
            key: interpolation
            for key, interpolation in normalized.items()
            if start <= key[1] <= end
        }
    return True, normalized


def _raw_bone_payload_mismatch(
    expected: Mapping[Tuple[str, int], bytes],
    frames: Iterable[Any],
) -> Tuple[int, int, int, int]:
    """Return missing, extra, changed-payload, and duplicate raw key counts."""
    actual: Dict[Tuple[str, int], bytes] = {}
    duplicate_count = 0
    invalid_count = 0
    for frame in frames:
        try:
            key = (str(frame.bone_name), int(frame.frame_number))
            interpolation = bytes(frame.interpolation)
        except (AttributeError, TypeError, ValueError, OverflowError):
            invalid_count += 1
            continue
        if key in actual:
            duplicate_count += 1
        actual[key] = interpolation
    missing_count = len(set(expected).difference(actual))
    extra_count = len(set(actual).difference(expected))
    changed_count = sum(
        expected[key] != actual[key]
        for key in set(expected).intersection(actual)
    )
    return missing_count, extra_count, changed_count, duplicate_count + invalid_count


def _raw_bone_transform_records(
    raw_provenance: Any,
    frame_range: Optional[Tuple[int, int]] = None,
) -> Tuple[bool, Optional[Dict[Tuple[str, int], Tuple[Tuple[float, ...], Tuple[float, ...]]]]]:
    """Normalize complete raw bone position/rotation provenance."""
    if not isinstance(raw_provenance, Mapping) or "raw_bone_transform_complete" not in raw_provenance:
        return False, None
    records = raw_provenance.get("raw_bone_interpolation")
    if not isinstance(records, list) or not raw_provenance.get("raw_bone_transform_complete"):
        return True, None
    try:
        expected_count = int(raw_provenance.get("raw_bone_key_count", len(records)))
    except (TypeError, ValueError, OverflowError):
        return True, None
    if expected_count != len(records):
        return True, None

    normalized: Dict[Tuple[str, int], Tuple[Tuple[float, ...], Tuple[float, ...]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            return True, None
        name = str(record.get("bone_name") or "")
        try:
            frame_number = int(record.get("frame_number"))
            position = tuple(float(value) for value in record.get("position", ()))
            rotation = tuple(float(value) for value in record.get("rotation", ()))
        except (TypeError, ValueError, OverflowError):
            return True, None
        key = (name, frame_number)
        if (
            not name
            or frame_number < 0
            or len(position) != 3
            or len(rotation) != 4
            or not all(math.isfinite(value) for value in position + rotation)
            or key in normalized
        ):
            return True, None
        normalized[key] = (position, rotation)
    if frame_range is not None:
        start, end = frame_range
        normalized = {
            key: transform
            for key, transform in normalized.items()
            if start <= key[1] <= end
        }
    return True, normalized


def _same_raw_bone_transform(
    actual: Tuple[Tuple[float, ...], Tuple[float, ...]],
    expected: Tuple[Tuple[float, ...], Tuple[float, ...]],
) -> bool:
    """Compare position and quaternion with sign-equivalent rotation semantics."""
    actual_position, actual_rotation = actual
    expected_position, expected_rotation = expected
    if len(actual_position) != 3 or len(actual_rotation) != 4:
        return False
    if any(
        not math.isclose(float(value), float(source), rel_tol=0.0, abs_tol=1.0e-6)
        for value, source in zip(actual_position, expected_position)
    ):
        return False
    actual_norm = math.sqrt(sum(float(value) ** 2 for value in actual_rotation))
    expected_norm = math.sqrt(sum(float(value) ** 2 for value in expected_rotation))
    if actual_norm <= 1.0e-12 or expected_norm <= 1.0e-12:
        return False
    dot = abs(
        sum(float(value) * float(source) for value, source in zip(actual_rotation, expected_rotation))
        / (actual_norm * expected_norm)
    )
    return math.isclose(dot, 1.0, rel_tol=0.0, abs_tol=1.0e-6)


def _raw_bone_transform_mismatch(
    expected: Mapping[Tuple[str, int], Tuple[Tuple[float, ...], Tuple[float, ...]]],
    frames: Iterable[Any],
) -> Tuple[int, int, int, int]:
    """Return missing, extra, changed, and duplicate/invalid raw transform counts."""
    actual: Dict[Tuple[str, int], Tuple[Tuple[float, ...], Tuple[float, ...]]] = {}
    duplicate_count = 0
    invalid_count = 0
    for frame in frames:
        try:
            key = (str(frame.bone_name), int(frame.frame_number))
            position = tuple(float(value) for value in frame.position)
            rotation = tuple(float(value) for value in frame.rotation)
        except (AttributeError, TypeError, ValueError, OverflowError):
            invalid_count += 1
            continue
        if key in actual:
            duplicate_count += 1
        actual[key] = (position, rotation)
    missing_count = len(set(expected).difference(actual))
    extra_count = len(set(actual).difference(expected))
    changed_count = sum(
        not _same_raw_bone_transform(actual[key], expected[key])
        for key in set(expected).intersection(actual)
    )
    return missing_count, extra_count, changed_count, duplicate_count + invalid_count


def validate_vmd_data(
    vmd_data: VmdData,
    mode: str = VMD_MODE_C,
    *,
    raw_provenance: Optional[Any] = None,
    frame_range: Optional[Tuple[int, int]] = None,
    require_raw_provenance: bool = True,
) -> ExportValidationReport:
    """Validate a normalized VMD payload for Mode A or Mode C.

    Mode A requires caller-provided raw provenance so an imported motion is
    never silently presented as a raw round-trip.  Mode C validates evaluated
    frame payloads and does not require provenance.
    """
    mode = str(mode or "").upper()
    issues = []
    if mode not in VMD_MODES:
        issues.append(_issue("VMD_MODE_UNSUPPORTED", "mode", f"VMD mode {mode!r} is not supported"))
    if mode == VMD_MODE_A and require_raw_provenance and raw_provenance is None:
        issues.append(
            _issue(
                "VMD_RAW_PROVENANCE_MISSING",
                "raw_provenance",
                "VMD Mode A requires imported raw key/interpolation provenance",
            )
        )
    if not isinstance(vmd_data, VmdData):
        issues.append(_issue("OUTPUT_PARSE_FAILED", "animation_data", "VMD animation data must be VmdData"))
        return ExportValidationReport("vmd", tuple(issues), mode=mode)

    start = end = None
    if frame_range is not None:
        try:
            start, end = (int(frame_range[0]), int(frame_range[1]))
        except (IndexError, TypeError, ValueError, OverflowError):
            issues.append(_issue("VMD_FRAME_RANGE", "frame_range", "VMD frame range must contain two integers"))
        else:
            if start < 0 or end < start:
                issues.append(_issue("VMD_FRAME_RANGE", "frame_range", "VMD frame range must be non-negative and ordered"))

    for index, frame in enumerate(vmd_data.bone_frames):
        path = f"bone_frames[{index}]"
        _name(frame.bone_name, f"{path}.bone_name", issues)
        _frame_number(frame, f"{path}.frame_number", issues)
        _finite_values(frame.position, f"{path}.position", issues)
        _finite_values(frame.rotation, f"{path}.rotation", issues)
        _interpolation(frame.interpolation, 64, f"{path}.interpolation", issues)
        try:
            norm = math.sqrt(sum(float(value) * float(value) for value in frame.rotation))
        except (TypeError, ValueError, OverflowError):
            norm = 0.0
        if not math.isfinite(norm) or norm <= 1e-12:
            issues.append(_issue("VMD_QUATERNION_INVALID", f"{path}.rotation", "VMD quaternion must not be zero"))

    if mode == VMD_MODE_A:
        raw_frame_range = (start, end) if start is not None and end is not None else None
        has_raw_records, expected_raw_records = _raw_bone_provenance_records(
            raw_provenance,
            frame_range=raw_frame_range,
        )
        if has_raw_records:
            if expected_raw_records is None:
                issues.append(
                    _issue(
                        "VMD_RAW_PROVENANCE_MISMATCH",
                        "raw_provenance.raw_bone_interpolation",
                        "VMD Mode A raw bone provenance is incomplete or malformed",
                    )
                )
            else:
                missing, extra, changed, duplicate = _raw_bone_payload_mismatch(
                    expected_raw_records,
                    vmd_data.bone_frames,
                )
                if missing or extra or changed or duplicate:
                    issues.append(
                        _issue(
                            "VMD_RAW_PROVENANCE_MISMATCH",
                            "raw_provenance.raw_bone_interpolation",
                            "VMD Mode A raw bone key/interpolation mismatch: "
                            f"missing={missing}, extra={extra}, changed={changed}, duplicate={duplicate}",
                        )
                    )
        has_raw_transforms, expected_raw_transforms = _raw_bone_transform_records(
            raw_provenance,
            frame_range=raw_frame_range,
        )
        if has_raw_transforms:
            if expected_raw_transforms is None:
                issues.append(
                    _issue(
                        "VMD_RAW_PROVENANCE_MISMATCH",
                        "raw_provenance.raw_bone_interpolation",
                        "VMD Mode A raw bone transform provenance is incomplete or malformed",
                    )
                )
            else:
                missing, extra, changed, duplicate = _raw_bone_transform_mismatch(
                    expected_raw_transforms,
                    vmd_data.bone_frames,
                )
                if missing or extra or changed or duplicate:
                    issues.append(
                        _issue(
                            "VMD_RAW_PROVENANCE_MISMATCH",
                            "raw_provenance.raw_bone_interpolation",
                            "VMD Mode A raw bone position/rotation mismatch: "
                            f"missing={missing}, extra={extra}, changed={changed}, duplicate={duplicate}",
                        )
                    )

    for index, frame in enumerate(vmd_data.morph_frames):
        path = f"morph_frames[{index}]"
        _name(frame.morph_name, f"{path}.morph_name", issues)
        _frame_number(frame, f"{path}.frame_number", issues)
        _finite_values((frame.value,), f"{path}.value", issues)

    for index, frame in enumerate(vmd_data.camera_frames):
        path = f"camera_frames[{index}]"
        _frame_number(frame, f"{path}.frame_number", issues)
        _finite_values((frame.distance,), f"{path}.distance", issues)
        _finite_values(frame.position, f"{path}.position", issues)
        _finite_values(frame.rotation, f"{path}.rotation", issues)
        _interpolation(frame.interpolation, 24, f"{path}.interpolation", issues)
        _bounded_int(
            frame.perspective,
            (0, 1),
            "VMD_PERSPECTIVE_RANGE",
            f"{path}.perspective",
            "VMD perspective must be 0 or 1",
            issues,
        )

    for index, frame in enumerate(vmd_data.light_frames):
        path = f"light_frames[{index}]"
        _frame_number(frame, f"{path}.frame_number", issues)
        _finite_values(frame.color, f"{path}.color", issues)
        _finite_values(frame.position, f"{path}.position", issues)

    for index, frame in enumerate(vmd_data.shadow_frames):
        path = f"shadow_frames[{index}]"
        _frame_number(frame, f"{path}.frame_number", issues)
        _finite_values((frame.distance,), f"{path}.distance", issues)
        _bounded_int(
            frame.mode,
            (0, 1, 2),
            "VMD_SHADOW_MODE_RANGE",
            f"{path}.mode",
            "VMD shadow mode must be 0, 1, or 2",
            issues,
        )

    for index, frame in enumerate(vmd_data.ik_show_hide_frames):
        path = f"ik_show_hide_frames[{index}]"
        _frame_number(frame, f"{path}.frame_number", issues)
        _bounded_int(
            frame.visible,
            (0, 1),
            "VMD_IK_FLAG_RANGE",
            f"{path}.visible",
            "VMD visibility flag must be 0 or 1",
            issues,
        )
        try:
            ik_states = list(frame.ik_states)
        except TypeError:
            issues.append(
                _issue(
                    "VMD_IK_FLAG_RANGE",
                    f"{path}.ik_states",
                    "VMD IK states must be an iterable of name/flag pairs",
                )
            )
            ik_states = []
        for state_index, state in enumerate(ik_states):
            try:
                name, enabled = state
            except (TypeError, ValueError):
                issues.append(
                    _issue(
                        "VMD_IK_FLAG_RANGE",
                        f"{path}.ik_states[{state_index}]",
                        "VMD IK state must contain a name and a 0/1 flag",
                    )
                )
                continue
            _name(name, f"{path}.ik_states[{state_index}].name", issues)
            _bounded_int(
                enabled,
                (0, 1),
                "VMD_IK_FLAG_RANGE",
                f"{path}.ik_states[{state_index}].enabled",
                "VMD IK flag must be 0 or 1",
                issues,
            )

    if start is not None and end is not None:
        for section_name, frames in _frame_sections(vmd_data):
            for index, frame in enumerate(frames):
                try:
                    frame_value = int(frame.frame_number)
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue
                if not start <= frame_value <= end:
                    issues.append(
                        _issue(
                            "VMD_FRAME_RANGE",
                            f"{section_name}[{index}].frame_number",
                            f"VMD frame {frame_value} is outside requested range {start}..{end}",
                        )
                    )

    return ExportValidationReport("vmd", tuple(issues), mode=mode)


def _bounded_int(
    value: Any,
    allowed: Iterable[int],
    code: str,
    path: str,
    message: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate an integer flag without allowing malformed payloads to escape."""
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        issues.append(_issue(code, path, message))
        return
    if normalized not in allowed:
        issues.append(_issue(code, path, message))


def _frame_sections(vmd_data: VmdData) -> Iterable[Tuple[str, Iterable[Any]]]:
    """Return all VMD frame sections in stable report order."""
    return (
        ("bone_frames", vmd_data.bone_frames),
        ("morph_frames", vmd_data.morph_frames),
        ("camera_frames", vmd_data.camera_frames),
        ("light_frames", vmd_data.light_frames),
        ("shadow_frames", vmd_data.shadow_frames),
        ("ik_show_hide_frames", vmd_data.ik_show_hide_frames),
    )


def verify_vmd_output(
    file_path: str,
    mode: str = VMD_MODE_C,
    *,
    expected_counts: Optional[Mapping[str, int]] = None,
) -> ExportValidationReport:
    """Parse and structurally validate one temporary VMD output."""
    try:
        vmd_data = VmdData().parse_file(file_path)
    except Exception as exc:
        return ExportValidationReport(
            "vmd",
            (
                _issue(
                    "OUTPUT_PARSE_FAILED",
                    "output",
                    f"VMD output could not be parsed: {type(exc).__name__}",
                ),
            ),
            mode=str(mode or "").upper(),
        )
    report = validate_vmd_data(
        vmd_data,
        mode=mode,
        require_raw_provenance=False,
    )
    section_names = {
        "bone_frames": vmd_data.bone_frames,
        "morph_frames": vmd_data.morph_frames,
        "camera_frames": vmd_data.camera_frames,
        "light_frames": vmd_data.light_frames,
        "shadow_frames": vmd_data.shadow_frames,
        "ik_show_hide_frames": vmd_data.ik_show_hide_frames,
    }
    count_issues = list(report.issues)
    for section_name, expected in (expected_counts or {}).items():
        if section_name not in section_names:
            continue
        try:
            expected_value = int(expected)
        except (TypeError, ValueError, OverflowError):
            continue
        actual_value = len(section_names[section_name])
        if actual_value != expected_value:
            count_issues.append(
                _issue(
                    "VMD_FRAME_COUNT_MISMATCH",
                    f"output.{section_name}",
                    f"VMD {section_name} count {actual_value} does not match expected count {expected_value}",
                )
            )
    if len(count_issues) != len(report.issues):
        return ExportValidationReport("vmd", tuple(count_issues), mode=report.mode)
    return report


__all__ = [
    "VMD_MODE_A",
    "VMD_MODE_C",
    "VMD_MODES",
    "validate_vmd_data",
    "verify_vmd_output",
]
