"""Maya-independent structural validation for VMD export strategies."""

import hashlib
import math
from pathlib import Path
import struct
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..core.vmd_data import VmdData
from .export_validator import ExportValidationIssue, ExportValidationReport


VMD_EXPORT_BAKE_TIMELINE = "bake_timeline"
VMD_EXPORT_PRESERVE_KEYS = "preserve_keys"
VMD_EXPORT_STRATEGIES = frozenset(
    {VMD_EXPORT_BAKE_TIMELINE, VMD_EXPORT_PRESERVE_KEYS}
)


# The stream verifier intentionally never retains one issue per record.  A
# malformed multi-million-frame file must not turn validation itself into a
# second unbounded allocation.
_STREAM_MAX_ISSUES = 100
_STREAM_SECTIONS = (
    ("bone_frames", 111),
    ("morph_frames", 23),
    ("camera_frames", 61),
    ("light_frames", 28),
    ("shadow_frames", 9),
    ("ik_show_hide_frames", 9),
)
_STREAM_METADATA_NAMES = {
    "bone_frames": "bones",
    "morph_frames": "morphs",
    "camera_frames": "cameras",
    "light_frames": "lights",
    "shadow_frames": "shadows",
    "ik_show_hide_frames": "ik",
}
_VMD_SIGNATURE = b"Vocaloid Motion Data"
_VMD_SIGNATURE_V2 = b"Vocaloid Motion Data 0002"


def _issue(
    code: str,
    path: str,
    message: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
) -> ExportValidationIssue:
    """Create a deterministic blocking VMD issue."""
    issue_details = dict(details or {})
    issue_details.setdefault("field", path)
    if code == "OUTPUT_VERIFY_FAILED":
        issue_details.setdefault("phase", "verify")
    return ExportValidationIssue(
        code,
        "fatal",
        True,
        path,
        message,
        details=issue_details,
    )


def _normalize_frame_range(
    frame_range: Optional[Tuple[int, int]],
) -> Tuple[Optional[Tuple[int, int]], Optional[ExportValidationIssue]]:
    """Normalize a requested frame range using the VMD validation contract."""

    if frame_range is None:
        return None, None
    if not isinstance(frame_range, (tuple, list)) or len(frame_range) != 2:
        return None, _issue(
            "EXPORT_OPTIONS_INVALID",
            "frame_range",
            "VMD frame range must contain two integers",
            details={"frame_range": None, "actual_type": type(frame_range).__name__},
        )
    start, end = frame_range
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
    ):
        return None, _issue(
            "EXPORT_OPTIONS_INVALID",
            "frame_range",
            "VMD frame range must contain two integers",
            details={"frame_range": [repr(start), repr(end)]},
        )
    if start < 0 or end < start or end > 0xFFFFFFFF:
        return None, _issue(
            "EXPORT_OPTIONS_INVALID",
            "frame_range",
            "VMD frame range must be ordered unsigned 32-bit integers",
            details={"frame_range": [start, end]},
        )
    return (start, end), None


def _finite_values(values: Iterable[Any], path: str, issues: List[ExportValidationIssue]) -> None:
    """Append one issue for the first non-finite numeric value."""
    try:
        for index, value in enumerate(values):
            if not math.isfinite(float(value)):
                issues.append(
                    _issue(
                        "INPUT_INVALID",
                        f"{path}[{index}]",
                        "VMD numeric value must be finite",
                    )
                )
                return
    except (TypeError, ValueError, OverflowError):
        issues.append(
            _issue(
                "INPUT_INVALID",
                path,
                "VMD numeric value must be a finite number",
            )
        )


def _frame_number(frame: Any, path: str, issues: List[ExportValidationIssue]) -> None:
    """Validate the unsigned VMD frame number contract."""
    try:
        value = int(frame.frame_number)
    except (AttributeError, TypeError, ValueError, OverflowError):
        issues.append(_issue("INPUT_INVALID", path, "VMD frame number must be a non-negative integer"))
        return
    if value < 0:
        issues.append(_issue("INPUT_INVALID", path, "VMD frame number must be non-negative"))


def _name(value: Any, path: str, issues: List[ExportValidationIssue]) -> None:
    """Reject nameless bone/morph entries before fixed-width encoding."""
    if not str(value or "").strip():
        issues.append(_issue("INPUT_INVALID", path, "VMD bone or morph name must not be empty"))


def _interpolation(value: Any, expected: int, path: str, issues: List[ExportValidationIssue]) -> None:
    """Validate fixed-size VMD interpolation bytes without rewriting them."""
    try:
        actual = len(value)
    except TypeError:
        actual = -1
    if actual != expected:
        issues.append(
            _issue(
                "INPUT_INVALID",
                path,
                f"VMD interpolation payload must contain {expected} bytes",
            )
        )


def validate_vmd_data(
    vmd_data: VmdData,
    export_strategy: str = VMD_EXPORT_BAKE_TIMELINE,
    *,
    frame_range: Optional[Tuple[int, int]] = None,
) -> ExportValidationReport:
    """Validate a normalized current-scene VMD payload."""
    export_strategy = str(export_strategy or VMD_EXPORT_BAKE_TIMELINE).lower()
    if export_strategy not in VMD_EXPORT_STRATEGIES:
        export_strategy = VMD_EXPORT_BAKE_TIMELINE
    issues = []
    if not isinstance(vmd_data, VmdData):
        issues.append(
            _issue(
                "INPUT_INVALID",
                "animation_data",
                "VMD animation data must be VmdData",
                details={
                    "field": "animation_data",
                    "expected_type": "VmdData",
                    "actual_type": type(vmd_data).__name__,
                },
            )
        )
        return ExportValidationReport("vmd", tuple(issues), mode=export_strategy)


    start = end = None
    if frame_range is not None:
        try:
            start, end = (int(frame_range[0]), int(frame_range[1]))
        except (IndexError, TypeError, ValueError, OverflowError):
            issues.append(
                _issue(
                    "EXPORT_OPTIONS_INVALID",
                    "frame_range",
                    "VMD frame range must contain two integers",
                    details={"frame_range": None, "actual_type": type(frame_range).__name__},
                )
            )
        else:
            if start < 0 or end < start:
                issues.append(
                    _issue(
                        "EXPORT_OPTIONS_INVALID",
                        "frame_range",
                        "VMD frame range must be non-negative and ordered",
                        details={"frame_range": [start, end]},
                    )
                )

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
            issues.append(_issue("INPUT_INVALID", f"{path}.rotation", "VMD quaternion must not be zero"))


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
            "INPUT_INVALID",
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
            "INPUT_INVALID",
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
            "INPUT_INVALID",
            f"{path}.visible",
            "VMD visibility flag must be 0 or 1",
            issues,
        )
        try:
            ik_states = list(frame.ik_states)
        except TypeError:
            issues.append(
                _issue(
                    "INPUT_INVALID",
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
                        "INPUT_INVALID",
                        f"{path}.ik_states[{state_index}]",
                        "VMD IK state must contain a name and a 0/1 flag",
                    )
                )
                continue
            _name(name, f"{path}.ik_states[{state_index}].name", issues)
            _bounded_int(
                enabled,
                (0, 1),
                "INPUT_INVALID",
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
                            "INPUT_INVALID",
                            f"{section_name}[{index}].frame_number",
                            f"VMD frame {frame_value} is outside requested range {start}..{end}",
                            details={
                                "frame_range": [start, end],
                                "actual_frame": frame_value,
                                "section": section_name,
                            },
                        )
                    )

    return ExportValidationReport("vmd", tuple(issues), mode=export_strategy)


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
    export_strategy: str = VMD_EXPORT_BAKE_TIMELINE,
    *,
    expected_counts: Optional[Mapping[str, int]] = None,
) -> ExportValidationReport:
    """Parse and structurally validate one temporary VMD output."""
    normalized_strategy = str(export_strategy or VMD_EXPORT_BAKE_TIMELINE).lower()
    if normalized_strategy not in VMD_EXPORT_STRATEGIES:
        normalized_strategy = VMD_EXPORT_BAKE_TIMELINE
    try:
        vmd_data = VmdData().parse_file(file_path)
    except Exception as exc:
        return ExportValidationReport(
            "vmd",
            (
                _issue(
                    "OUTPUT_VERIFY_FAILED",
                    "output",
                    f"VMD output could not be parsed: {type(exc).__name__}",
                ),
            ),
            mode=normalized_strategy,
        )
    report = validate_vmd_data(
        vmd_data,
        export_strategy=export_strategy,
    )
    section_names = {
        "bone_frames": vmd_data.bone_frames,
        "morph_frames": vmd_data.morph_frames,
        "camera_frames": vmd_data.camera_frames,
        "light_frames": vmd_data.light_frames,
        "shadow_frames": vmd_data.shadow_frames,
        "ik_show_hide_frames": vmd_data.ik_show_hide_frames,
    }
    count_issues = [
        ExportValidationIssue(
            "OUTPUT_VERIFY_FAILED",
            issue.severity,
            issue.blocking,
            "output." + issue.path if issue.path else "output",
            issue.reason,
            action=issue.action,
            details=dict(issue.details, phase="parse"),
            evidence=issue.evidence,
        )
        for issue in report.issues
    ]
    for section_name, expected in (expected_counts or {}).items():
        if section_name not in section_names:
            count_issues.append(
                _issue(
                    "INPUT_INVALID",
                    "expected_counts",
                    f"unknown VMD count section {section_name!r}",
                    details={"expected_count_contract": sorted(section_names)},
                )
            )
            continue
        try:
            expected_value = int(expected)
        except (TypeError, ValueError, OverflowError):
            count_issues.append(
                _issue(
                    "INPUT_INVALID",
                    "expected_counts",
                    f"VMD expected count for {section_name} must be an integer",
                    details={"expected_count_contract": sorted(section_names)},
                )
            )
            continue
        actual_value = len(section_names[section_name])
        if actual_value != expected_value:
            count_issues.append(
                _issue(
                    "OUTPUT_VERIFY_FAILED",
                    f"output.{section_name}",
                    f"VMD {section_name} count {actual_value} does not match expected count {expected_value}",
                    details={
                        "section": section_name,
                        "expected_count": expected_value,
                        "actual_count": actual_value,
                        "aggregation_discriminator": "output_count",
                    },
                )
            )
    return ExportValidationReport("vmd", tuple(count_issues), mode=report.mode)


def _stream_issue(
    issues: List[ExportValidationIssue],
    code: str,
    path: str,
    message: str,
    *,
    details: Optional[Mapping[str, Any]] = None,
) -> None:
    """Append a bounded blocking issue for the byte-stream verifier."""
    if len(issues) < _STREAM_MAX_ISSUES:
        issues.append(_issue(code, path, message, details=details))


def _stream_name(
    raw_name: bytes,
    path: str,
    issues: List[ExportValidationIssue],
) -> Optional[str]:
    """Decode one fixed CP932 name without retaining it after validation."""
    try:
        name = raw_name.split(b"\x00", 1)[0].decode("cp932")
    except UnicodeDecodeError:
        _stream_issue(
            issues,
            "OUTPUT_VERIFY_FAILED",
            path,
            "VMD fixed-width name is not valid CP932",
        )
        return None
    if not name.strip():
        _stream_issue(issues, "OUTPUT_VERIFY_FAILED", path, "VMD bone or morph name must not be empty")
    return name


def _stream_finite(
    values: Iterable[float],
    path: str,
    issues: List[ExportValidationIssue],
) -> None:
    """Validate already-unpacked f32 values without retaining their record."""
    if not all(math.isfinite(value) for value in values):
        _stream_issue(
            issues,
            "OUTPUT_VERIFY_FAILED",
            path,
            "VMD numeric value must be finite",
        )


def verify_vmd_output_streaming(
    file_path: str,
    export_strategy: str = VMD_EXPORT_BAKE_TIMELINE,
    *,
    expected_counts: Optional[Mapping[str, int]] = None,
    expected_bounds: Optional[Mapping[str, Any]] = None,
    expected_sha256: Optional[str] = None,
    expected_size: Optional[int] = None,
    expected_frame_range: Optional[Tuple[int, int]] = None,
) -> ExportValidationReport:
    """Verify a VMD file one wire record at a time.

    Unlike :func:`verify_vmd_output`, this verifier never constructs
    ``VmdData`` or retains frame/name objects.  It accepts the optional tail
    sections used by legacy VMD writers, while requiring each section that is
    present to have a complete count and complete records.  The stream writer
    emits all six canonical count fields, including an empty IK section.
    """
    normalized_strategy = str(export_strategy or VMD_EXPORT_BAKE_TIMELINE).lower()
    if normalized_strategy not in VMD_EXPORT_STRATEGIES:
        normalized_strategy = VMD_EXPORT_BAKE_TIMELINE
    issues: List[ExportValidationIssue] = []
    metadata_names = frozenset(_STREAM_METADATA_NAMES.values())
    canonical_counts: Optional[Dict[str, int]] = None
    if expected_counts is not None:
        counts_are_canonical = isinstance(expected_counts, Mapping) and set(expected_counts) == metadata_names
        if counts_are_canonical:
            canonical_counts = {
                section: expected_counts[name]
                for section, name in _STREAM_METADATA_NAMES.items()
            }
            counts_are_canonical = all(
                type(value) is int and 0 <= value <= 0xFFFFFFFF
                for value in canonical_counts.values()
            )
        if not counts_are_canonical:
            canonical_counts = None
            _stream_issue(
                issues,
                "INPUT_INVALID",
                "expected_counts",
                "VMD stream counts must declare exactly six canonical unsigned 32-bit counts",
                details={
                    "expected_count_contract": sorted(metadata_names),
                    "actual_type": type(expected_counts).__name__,
                },
            )

    canonical_bounds: Optional[Dict[str, Tuple[Optional[int], Optional[int]]]] = None
    if expected_bounds is not None:
        bounds_are_canonical = isinstance(expected_bounds, Mapping) and set(expected_bounds) == metadata_names
        if bounds_are_canonical:
            try:
                canonical_bounds = {
                    section: (expected_bounds[name].minimum, expected_bounds[name].maximum)
                    for section, name in _STREAM_METADATA_NAMES.items()
                }
            except AttributeError:
                bounds_are_canonical = False
            else:
                bounds_are_canonical = all(
                    (minimum is None and maximum is None)
                    or (
                        type(minimum) is int
                        and type(maximum) is int
                        and 0 <= minimum <= maximum <= 0xFFFFFFFF
                    )
                    for minimum, maximum in canonical_bounds.values()
                )
        if not bounds_are_canonical:
            canonical_bounds = None
            _stream_issue(
                issues,
                "INPUT_INVALID",
                "expected_bounds",
                "VMD stream bounds must declare six canonical minimum/maximum pairs",
                details={
                    "expected_bounds_contract": sorted(metadata_names),
                    "actual_type": type(expected_bounds).__name__,
                },
            )

    normalized_frame_range, frame_range_issue = _normalize_frame_range(expected_frame_range)
    if frame_range_issue is not None:
        _stream_issue(
            issues,
            "INPUT_INVALID",
            frame_range_issue.path,
            frame_range_issue.reason,
            details=dict(frame_range_issue.details),
        )
    output_path = Path(file_path)
    if not output_path.is_file():
        _stream_issue(issues, "OUTPUT_VERIFY_FAILED", "output", "temporary output file does not exist")
        return ExportValidationReport("vmd", tuple(issues), mode=normalized_strategy)
    try:
        file_size = output_path.stat().st_size
    except OSError:
        _stream_issue(issues, "OUTPUT_VERIFY_FAILED", "output", "VMD output size could not be read")
        return ExportValidationReport("vmd", tuple(issues), mode=normalized_strategy)
    if file_size == 0:
        _stream_issue(issues, "OUTPUT_VERIFY_FAILED", "output", "temporary output file is empty")
        return ExportValidationReport("vmd", tuple(issues), mode=normalized_strategy)

    digest = hashlib.sha256()
    bytes_read = 0
    counts = {section: 0 for section, _ in _STREAM_SECTIONS}
    minimums = {section: None for section, _ in _STREAM_SECTIONS}
    maximums = {section: None for section, _ in _STREAM_SECTIONS}

    def read_bytes(handle: Any, size: int) -> bytes:
        nonlocal bytes_read
        data = handle.read(size)
        bytes_read += len(data)
        digest.update(data)
        return data

    def read_exact(handle: Any, size: int, path: str) -> Optional[bytes]:
        data = read_bytes(handle, size)
        if len(data) != size:
            _stream_issue(
                issues,
                "OUTPUT_VERIFY_FAILED",
                path,
                "VMD output is truncated (expected {} bytes, got {})".format(size, len(data)),
            )
            return None
        return data

    def record_frame(section: str, frame_number: int, path: str) -> None:
        counts[section] += 1
        minimum = minimums[section]
        maximum = maximums[section]
        minimums[section] = frame_number if minimum is None else min(minimum, frame_number)
        maximums[section] = frame_number if maximum is None else max(maximum, frame_number)
        if normalized_frame_range is not None:
            start, end = normalized_frame_range
            if not start <= frame_number <= end:
                _stream_issue(
                    issues,
                    "OUTPUT_VERIFY_FAILED",
                    path + ".frame_number",
                    "VMD frame {} is outside requested range {}..{}".format(
                        frame_number,
                        start,
                        end,
                    ),
                    details={
                        "section": section,
                        "expected_bounds": [start, end],
                        "actual_bounds": [frame_number, frame_number],
                        "aggregation_discriminator": "output_range",
                    },
                )

    def consume_remaining(handle: Any) -> None:
        """Finish hashing bytes after a structural failure without retaining them."""
        nonlocal bytes_read
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            digest.update(chunk)

    try:
        with output_path.open("rb") as handle:
            header = read_bytes(handle, 30)
            if len(header) != 30:
                _stream_issue(
                    issues,
                    "OUTPUT_VERIFY_FAILED",
                    "output.header",
                    "VMD output header is truncated",
                )
                consume_remaining(handle)
            elif not (
                header.startswith(_VMD_SIGNATURE_V2) or header.startswith(_VMD_SIGNATURE)
            ):
                _stream_issue(
                    issues,
                    "OUTPUT_VERIFY_FAILED",
                    "output.header",
                    "VMD output header is invalid",
                )
                consume_remaining(handle)
            else:
                model_name = read_exact(handle, 20, "output.model_name")
                if model_name is None:
                    consume_remaining(handle)
                else:
                    try:
                        model_name.split(b"\x00", 1)[0].decode("cp932")
                    except UnicodeDecodeError:
                        _stream_issue(
                            issues,
                            "OUTPUT_VERIFY_FAILED",
                            "output.model_name",
                            "VMD model name is not valid CP932",
                        )

                    stopped = False
                    for section_index, (section, record_size) in enumerate(_STREAM_SECTIONS):
                        count_data = read_bytes(handle, 4)
                        if not count_data:
                            # Camera and later sections were historically
                            # optional.  End-of-file here is valid only for
                            # that optional tail.
                            if section_index < 2 or canonical_counts is not None:
                                _stream_issue(
                                    issues,
                                    "OUTPUT_VERIFY_FAILED",
                                    "output.{}.count".format(section),
                                    "VMD output is missing a required section count",
                                )
                            break
                        if len(count_data) != 4:
                            _stream_issue(
                                issues,
                                "OUTPUT_VERIFY_FAILED",
                                "output.{}.count".format(section),
                                "VMD section count is truncated",
                            )
                            consume_remaining(handle)
                            stopped = True
                            break
                        (section_count,) = struct.unpack("<I", count_data)
                        for index in range(section_count):
                            path = "output.{}[{}]".format(section, index)
                            if section == "ik_show_hide_frames":
                                fixed = read_exact(handle, record_size, path)
                                if fixed is None:
                                    stopped = True
                                    break
                                frame_number, visible, ik_count = struct.unpack("<IBI", fixed)
                                record_frame(section, frame_number, path)
                                if visible not in (0, 1):
                                    _stream_issue(
                                        issues,
                                        "OUTPUT_VERIFY_FAILED",
                                        path + ".visible",
                                        "VMD visibility flag must be 0 or 1",
                                    )
                                for state_index in range(ik_count):
                                    state_path = "{}.ik_states[{}]".format(path, state_index)
                                    state = read_exact(handle, 21, state_path)
                                    if state is None:
                                        stopped = True
                                        break
                                    _stream_name(state[:20], state_path + ".name", issues)
                                    if state[20] not in (0, 1):
                                        _stream_issue(
                                            issues,
                                            "OUTPUT_VERIFY_FAILED",
                                            state_path + ".enabled",
                                            "VMD IK flag must be 0 or 1",
                                        )
                                if stopped:
                                    break
                                continue

                            raw = read_exact(handle, record_size, path)
                            if raw is None:
                                stopped = True
                                break
                            if section == "bone_frames":
                                _stream_name(raw[:15], path + ".bone_name", issues)
                                frame_number = struct.unpack_from("<I", raw, 15)[0]
                                position = struct.unpack_from("<fff", raw, 19)
                                rotation = struct.unpack_from("<ffff", raw, 31)
                                _stream_finite(position, path + ".position", issues)
                                _stream_finite(rotation, path + ".rotation", issues)
                                norm = math.sqrt(sum(value * value for value in rotation))
                                if not math.isfinite(norm) or norm <= 1.0e-12:
                                    _stream_issue(
                                        issues,
                                        "OUTPUT_VERIFY_FAILED",
                                        path + ".rotation",
                                        "VMD quaternion must not be zero",
                                    )
                            elif section == "morph_frames":
                                _stream_name(raw[:15], path + ".morph_name", issues)
                                frame_number = struct.unpack_from("<I", raw, 15)[0]
                                _stream_finite((struct.unpack_from("<f", raw, 19)[0],), path + ".value", issues)
                            elif section == "camera_frames":
                                frame_number = struct.unpack_from("<I", raw, 0)[0]
                                distance = struct.unpack_from("<f", raw, 4)[0]
                                position = struct.unpack_from("<fff", raw, 8)
                                rotation = struct.unpack_from("<fff", raw, 20)
                                _stream_finite((distance,), path + ".distance", issues)
                                _stream_finite(position, path + ".position", issues)
                                _stream_finite(rotation, path + ".rotation", issues)
                                perspective = raw[60]
                                if perspective not in (0, 1):
                                    _stream_issue(
                                        issues,
                                        "OUTPUT_VERIFY_FAILED",
                                        path + ".perspective",
                                        "VMD perspective must be 0 or 1",
                                    )
                            elif section == "light_frames":
                                frame_number = struct.unpack_from("<I", raw, 0)[0]
                                _stream_finite(struct.unpack_from("<fff", raw, 4), path + ".color", issues)
                                _stream_finite(struct.unpack_from("<fff", raw, 16), path + ".position", issues)
                            else:
                                frame_number = struct.unpack_from("<I", raw, 0)[0]
                                if raw[4] not in (0, 1, 2):
                                    _stream_issue(
                                        issues,
                                        "OUTPUT_VERIFY_FAILED",
                                        path + ".mode",
                                        "VMD shadow mode must be 0, 1, or 2",
                                    )
                                _stream_finite((struct.unpack_from("<f", raw, 5)[0],), path + ".distance", issues)
                            record_frame(section, frame_number, path)
                        if stopped:
                            consume_remaining(handle)
                            break
                    if not stopped:
                        trailing = read_bytes(handle, 1)
                        if trailing:
                            _stream_issue(
                                issues,
                                "OUTPUT_VERIFY_FAILED",
                                "output.trailing_bytes",
                                "VMD output contains trailing bytes after the final section",
                            )
                            consume_remaining(handle)
    except OSError as exc:
        _stream_issue(
            issues,
            "OUTPUT_VERIFY_FAILED",
            "output",
            "VMD output could not be read: {}".format(type(exc).__name__),
        )

    for section, _ in _STREAM_SECTIONS:
        expected = canonical_counts.get(section) if canonical_counts is not None else None
        if expected is not None and expected != counts[section]:
            _stream_issue(
                issues,
                "OUTPUT_VERIFY_FAILED",
                "output.{}".format(section),
                "VMD {} count {} does not match expected count {}".format(
                    section,
                    counts[section],
                    expected,
                ),
                details={
                    "section": section,
                    "expected_count": expected,
                    "actual_count": counts[section],
                    "aggregation_discriminator": "output_count",
                },
            )
    if canonical_bounds is not None:
        for section, expected in canonical_bounds.items():
            actual = (minimums[section], maximums[section])
            if actual != expected:
                _stream_issue(
                    issues,
                    "OUTPUT_VERIFY_FAILED",
                    "output.{}.frame_bounds".format(section),
                    "VMD {} bounds {} do not match expected bounds {}".format(
                        section,
                        actual,
                        expected,
                    ),
                    details={
                        "section": section,
                        "expected_bounds": list(expected),
                        "actual_bounds": list(actual),
                        "aggregation_discriminator": "output_range",
                    },
                )

    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None:
        expected_digest = str(expected_sha256)
        if expected_digest.startswith("sha256:"):
            expected_digest = expected_digest[7:]
        if actual_sha256 != expected_digest:
            _stream_issue(
                issues,
                "OUTPUT_VERIFY_FAILED",
                "output.sha256",
                "VMD output SHA-256 {} does not match expected {}".format(actual_sha256, expected_digest),
            )
    if expected_size is not None:
        try:
            normalized_size = int(expected_size)
        except (TypeError, ValueError, OverflowError):
            normalized_size = None
        if normalized_size is not None and bytes_read != normalized_size:
            _stream_issue(
                issues,
                "OUTPUT_VERIFY_FAILED",
                "output.size",
                "VMD output size {} does not match expected {}".format(bytes_read, normalized_size),
            )
    return ExportValidationReport("vmd", tuple(issues), mode=normalized_strategy)


__all__ = [
    "VMD_EXPORT_BAKE_TIMELINE",
    "VMD_EXPORT_PRESERVE_KEYS",
    "VMD_EXPORT_STRATEGIES",
    "validate_vmd_data",
    "verify_vmd_output",
    "verify_vmd_output_streaming",
]
