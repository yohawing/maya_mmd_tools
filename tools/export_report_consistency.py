"""Validate schema-v2 Export Validation JSON/Markdown artifact parity."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import sys
from typing import Any, Optional, Union

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mmd_tools.validation.issue_catalog import (  # noqa: E402
    STABLE_ISSUE_CODES,
)

PathLike = Union[str, Path]
_REPORT_FIELDS = (
    "status",
    "format",
    "mode",
    "target_identity",
    "snapshot_fingerprint",
    "summary",
    "requires_warning_ack",
    "evidence",
    "issue_aggregation",
)
_ISSUE_FIELDS = (
    "code",
    "severity",
    "blocking",
    "path",
    "reason",
    "action",
    "details",
    "evidence",
    "provenance",
)
_ISSUE_AGGREGATION_FIELDS = ("occurrence_count", "path_pattern", "sample_paths")
_ISSUE_PROJECTION_FIELDS = _ISSUE_FIELDS + _ISSUE_AGGREGATION_FIELDS
_AI_MARKER_PATTERN = re.compile(
    r"chatgpt|openai|ai[\s_-]*generated|generated[\s_-]*by[\s_-]*ai|"
    r"artificial[\s_-]*intelligence|llm[\s_-]*generated",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^### (\d+)\. \[([A-Z]+)\] `([^`]+)`$")


class ReportConsistencyError(ValueError):
    """Raised when a report pair is invalid or inconsistent."""

    def __init__(self, *reasons: str):
        normalized = tuple(_bounded_text(reason) for reason in reasons if reason)
        super().__init__("; ".join(normalized) or "report consistency validation failed")
        self.reasons = normalized or ("report consistency validation failed",)


def _bounded_text(value: Any, limit: int = 240) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _fail(message: str) -> None:
    raise ReportConsistencyError(message)


def _read_text(path: PathLike, label: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        _fail(f"unable to read {label} ({type(exc).__name__})")


def _load_json(path: PathLike) -> tuple[dict[str, Any], str]:
    text = _read_text(path, "JSON report")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"JSON report is invalid ({exc.msg}, line {exc.lineno}, column {exc.colno})")
    if not isinstance(value, dict):
        _fail("JSON report root must be an object")
    return value, text


def _check_ai(value: Any, label: str) -> None:
    if isinstance(value, str):
        match = _AI_MARKER_PATTERN.search(value)
        if match:
            _fail(f"AI-like marker {match.group(0)!r} found in {label}")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _check_ai(key, f"{label} key")
            _check_ai(child, label)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for child in value:
            _check_ai(child, label)


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{field} must be a boolean")
    return value


def _require_count(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        _fail(f"{field} must be a {'positive' if positive else 'non-negative'} integer")
    return value


def _validate_aggregation(value: Any) -> None:
    aggregation = _require_mapping(value, "issue_aggregation")
    required = {
        "max_display_issues", "total_occurrences", "shown_occurrences",
        "omitted_occurrences", "total_groups", "shown_groups", "omitted_groups",
        "has_blocking", "requires_warning_ack",
    }
    if set(aggregation) != required:
        _fail("issue_aggregation fields do not match schema v2")
    _require_count(aggregation["max_display_issues"], "issue_aggregation.max_display_issues", positive=True)
    for field in required - {"max_display_issues", "has_blocking", "requires_warning_ack"}:
        _require_count(aggregation[field], f"issue_aggregation.{field}")
    _require_bool(aggregation["has_blocking"], "issue_aggregation.has_blocking")
    _require_bool(aggregation["requires_warning_ack"], "issue_aggregation.requires_warning_ack")


def _validate_issue(issue: Any, index: int) -> None:
    if not isinstance(issue, Mapping):
        _fail(f"issue {index} must be an object")
    if not set(_ISSUE_FIELDS).issubset(issue):
        _fail(f"issue {index} is missing schema-v2 fields")
    forbidden = {"message", "observed", "expected", "impact", "remediation", "category", "title_key", "action_key", "loss_policy", "reason_id", "subcode"}
    if forbidden.intersection(issue):
        _fail(f"issue {index} contains removed legacy fields")
    code = _require_string(issue["code"], f"issue {index}.code")
    if code not in STABLE_ISSUE_CODES:
        _fail(f"issue {index} uses unregistered issue code {code!r}")
    severity = _require_string(issue["severity"], f"issue {index}.severity")
    if severity not in {"fatal", "warning", "info"}:
        _fail(f"issue {index}.severity is unsupported: {severity!r}")
    _require_bool(issue["blocking"], f"issue {index}.blocking")
    if issue["path"] is not None and not isinstance(issue["path"], str):
        _fail(f"issue {index}.path must be a string or null")
    _require_string(issue["reason"], f"issue {index}.reason")
    _require_string(issue["action"], f"issue {index}.action")
    _require_mapping(issue["details"], f"issue {index}.details")
    _require_mapping(issue["evidence"], f"issue {index}.evidence")
    _require_string(issue["provenance"], f"issue {index}.provenance")
    present = set(_ISSUE_AGGREGATION_FIELDS).intersection(issue)
    if present and present != set(_ISSUE_AGGREGATION_FIELDS):
        _fail(f"issue {index} has incomplete aggregation metadata")
    if present:
        _require_count(issue["occurrence_count"], f"issue {index}.occurrence_count", positive=True)
        if not isinstance(issue["path_pattern"], str):
            _fail(f"issue {index}.path_pattern must be a string")
        paths = issue["sample_paths"]
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
            _fail(f"issue {index}.sample_paths must be a non-empty string array")


def _validate_json(payload: Mapping[str, Any]) -> None:
    required = {"schema_version", "status", "requires_warning_ack", "format", "mode", "target_identity", "snapshot_fingerprint", "summary", "evidence", "issues"}
    if not required.issubset(payload):
        _fail("JSON report is missing schema-v2 fields")
    if payload["schema_version"] != 2:
        _fail("JSON report schema_version must be 2")
    if payload["status"] not in {"ready", "warning", "blocked"}:
        _fail(f"unsupported report status: {payload['status']!r}")
    if payload["format"] is not None and not isinstance(payload["format"], str):
        _fail("format must be a string or null")
    _require_string(payload["mode"], "mode")
    for field in ("target_identity", "snapshot_fingerprint"):
        if payload[field] is not None and not isinstance(payload[field], str):
            _fail(f"{field} must be a string or null")
    requires_ack = _require_bool(payload["requires_warning_ack"], "requires_warning_ack")
    summary = _require_mapping(payload["summary"], "summary")
    if set(summary) != {"fatal", "warning", "info"}:
        _fail("summary must contain exactly fatal, warning, and info")
    for key in summary:
        _require_count(summary[key], f"summary.{key}")
    _require_mapping(payload["evidence"], "evidence")
    issues = payload["issues"]
    if not isinstance(issues, list):
        _fail("issues must be an array")
    if payload.get("issue_aggregation") is not None:
        _validate_aggregation(payload["issue_aggregation"])
    counts = {"fatal": 0, "warning": 0, "info": 0}
    expected_ack = False
    for index, issue in enumerate(issues, 1):
        _validate_issue(issue, index)
        count = issue.get("occurrence_count", 1)
        counts[issue["severity"]] += count
        expected_ack |= issue["severity"] == "warning" and not issue["blocking"]
    aggregation = payload.get("issue_aggregation")
    if aggregation is not None:
        shown = sum(issue.get("occurrence_count", 1) for issue in issues)
        if aggregation["shown_groups"] != len(issues) or aggregation["shown_occurrences"] != shown:
            _fail("issue_aggregation shown values do not match issues")
        if aggregation["omitted_occurrences"] != aggregation["total_occurrences"] - shown:
            _fail("issue_aggregation.omitted_occurrences is inconsistent")
        if aggregation["omitted_groups"] != aggregation["total_groups"] - aggregation["shown_groups"]:
            _fail("issue_aggregation.omitted_groups is inconsistent")
        if any(issue["blocking"] for issue in issues) and not aggregation["has_blocking"]:
            _fail("issue_aggregation.has_blocking omits a shown blocking issue")
        expected_ack |= aggregation["requires_warning_ack"]
    if aggregation is None:
        if dict(summary) != counts:
            _fail(f"summary counts do not match issues: expected {counts!r}")
    else:
        if sum(summary.values()) != aggregation["total_occurrences"]:
            _fail("summary total does not match issue_aggregation.total_occurrences")
        if any(counts[severity] > summary[severity] for severity in counts):
            _fail("shown issue severities exceed the full summary")
    if requires_ack != expected_ack:
        _fail("requires_warning_ack does not match non-blocking warning issues")
    has_blocking = aggregation["has_blocking"] if aggregation is not None else any(
        issue["blocking"] for issue in issues
    )
    expected_status = "blocked" if has_blocking else ("warning" if expected_ack else "ready")
    if payload["status"] != expected_status:
        _fail(f"status does not match issues: expected {expected_status!r}")


def _parse_inline(line: str, label: str) -> str:
    prefix = f"- {label}: `"
    if not line.startswith(prefix) or not line.endswith("`"):
        _fail(f"Markdown field has invalid format: {label}")
    return line[len(prefix):-1]


def _parse_json_inline(line: str, label: str) -> Any:
    try:
        return json.loads(_parse_inline(line, label))
    except json.JSONDecodeError as exc:
        _fail(f"Markdown {label} is invalid JSON ({exc.msg})")


def _parse_json_field(line: str, label: str) -> Any:
    """Parse a single-line JSON value without Markdown sentinel aliases."""
    prefix = f"- {label}: "
    if not line.startswith(prefix):
        _fail(f"Markdown field has invalid format: {label}")
    try:
        return json.loads(line[len(prefix):])
    except json.JSONDecodeError as exc:
        _fail(f"Markdown {label} is invalid JSON ({exc.msg})")


def _parse_markdown_issue(section: Sequence[str], heading: re.Match[str]) -> dict[str, Any]:
    values: dict[str, Any] = {"code": heading.group(3), "severity": heading.group(2).lower()}
    expected_order = ("Path", "Decision", "Reason", "Action", "Details", "Provenance", "Evidence")
    position = 0
    for line in section:
        if not line:
            continue
        if line.startswith("- Occurrences:") or line.startswith("- Path pattern:") or line.startswith("- Sample paths:"):
            label = line[2:line.index(":")]
            if "occurrence_count" not in values and label != "Occurrences":
                _fail(f"Markdown issue {values['code']!r} has misplaced aggregation fields")
            if label == "Occurrences":
                values["occurrence_count"] = int(_parse_inline(line, label))
            elif label == "Path pattern":
                values["path_pattern"] = _parse_inline(line, label)
            else:
                values["sample_paths"] = _parse_json_inline(line, label)
            continue
        if position >= len(expected_order):
            _fail(f"Markdown issue {values['code']!r} has extra fields")
        label = expected_order[position]
        prefix = f"- {label}: "
        if not line.startswith(prefix):
            _fail(f"Markdown issue {values['code']!r} has fields out of order")
        text = line[len(prefix):]
        if label == "Decision":
            text = _parse_inline(line, label)
        elif label in {"Path", "Reason", "Action", "Provenance"}:
            text = _parse_json_field(line, label)
        elif label == "Details" or label == "Evidence":
            text = _parse_json_inline(line, label)
        values[label.lower()] = text
        position += 1
    if position != len(expected_order):
        _fail(f"Markdown issue {values['code']!r} is missing fields")
    if values["decision"] not in {"BLOCK", "ALLOW"}:
        _fail(f"Markdown issue {values['code']!r} has invalid decision")
    result = {
        "code": values["code"], "severity": values["severity"],
        "blocking": values["decision"] == "BLOCK",
        "path": values["path"],
        "reason": values["reason"], "action": values["action"],
        "details": values["details"], "provenance": values["provenance"],
        "evidence": values["evidence"],
    }
    for field in _ISSUE_AGGREGATION_FIELDS:
        if field in values:
            result[field] = values[field]
    return result


def _parse_markdown(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    required_prefix = ["# Export Validation Report", "", "- Status: ", "- Format: ", "- Mode: ", "- Target: ", "- Snapshot fingerprint: "]
    if len(lines) < len(required_prefix) or lines[:2] != required_prefix[:2]:
        _fail("Markdown header is invalid")
    cursor = 2
    status = _parse_json_field(lines[cursor], "Status")
    cursor += 1
    format_name = _parse_json_field(lines[cursor], "Format")
    cursor += 1
    mode = _parse_json_field(lines[cursor], "Mode")
    cursor += 1
    target = _parse_json_field(lines[cursor], "Target")
    cursor += 1
    snapshot = _parse_json_field(lines[cursor], "Snapshot fingerprint")
    cursor += 1
    if lines[cursor:cursor + 3] != ["", "## Summary", ""]:
        _fail("Markdown summary header is invalid")
    cursor += 3
    summary = {}
    for key in ("Fatal", "Warning", "Info"):
        prefix = f"- {key}: "
        if not lines[cursor].startswith(prefix):
            _fail("Markdown summary fields are out of order")
        summary[key.lower()] = int(lines[cursor][len(prefix):])
        cursor += 1
    warning_line = lines[cursor]
    cursor += 1
    warning = _parse_inline(warning_line, "Warning acknowledgement required")
    issue_aggregation = None
    if cursor < len(lines) and lines[cursor].startswith("- Issue display limit: "):
        issue_aggregation = {}
        prefix = "- Issue display limit: "
        issue_aggregation["max_display_issues"] = int(lines[cursor][len(prefix):])
        cursor += 1
        prefix = "- Issue occurrences shown: "
        issue_aggregation["shown_occurrences"] = int(lines[cursor][len(prefix):])
        cursor += 1
        prefix = "- Issue occurrences omitted: "
        issue_aggregation["omitted_occurrences"] = int(lines[cursor][len(prefix):])
        cursor += 1
        prefix = "- Issue groups shown: "
        groups = lines[cursor][len(prefix):].split(" / ", 1)
        if len(groups) != 2:
            _fail("Markdown issue groups summary is invalid")
        issue_aggregation["shown_groups"] = int(groups[0])
        issue_aggregation["total_groups"] = int(groups[1])
        cursor += 1
        issue_aggregation["has_blocking"] = _parse_inline(
            lines[cursor], "Blocking issue present"
        ) == "true"
        cursor += 1
        issue_aggregation["total_occurrences"] = (
            issue_aggregation["shown_occurrences"]
            + issue_aggregation["omitted_occurrences"]
        )
        issue_aggregation["omitted_groups"] = (
            issue_aggregation["total_groups"] - issue_aggregation["shown_groups"]
        )
        issue_aggregation["requires_warning_ack"] = warning == "true"
    if lines[cursor:cursor + 2] != ["", "## Evidence"]:
        _fail("Markdown evidence header is invalid")
    cursor += 2
    if lines[cursor] != "":
        _fail("Markdown evidence spacing is invalid")
    cursor += 1
    evidence = _parse_json_inline(lines[cursor], "Report evidence")
    cursor += 1
    if lines[cursor:cursor + 3] != ["", "## Issues", ""]:
        _fail("Markdown issues header is invalid")
    cursor += 3
    issues = []
    if cursor < len(lines) and lines[cursor] == "No validation issues.":
        cursor += 1
    else:
        while cursor < len(lines):
            if not lines[cursor]:
                cursor += 1
                continue
            heading = _HEADING.fullmatch(lines[cursor])
            if heading is None or int(heading.group(1)) != len(issues) + 1:
                _fail("Markdown issue numbering or heading is invalid")
            end = cursor + 1
            while end < len(lines) and not _HEADING.fullmatch(lines[end]):
                end += 1
            issues.append(_parse_markdown_issue(lines[cursor + 1:end], heading))
            cursor = end
    if any(line for line in lines[cursor:]):
        _fail("Markdown has unexpected trailing content")
    return {
        "status": status.lower(), "format": format_name,
        "mode": mode, "target_identity": target,
        "snapshot_fingerprint": snapshot,
        "summary": summary, "requires_warning_ack": warning == "true",
        "evidence": evidence, "issues": issues, "issue_aggregation": issue_aggregation,
    }


def _projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        field: payload.get(field)
        for field in _REPORT_FIELDS
    }
    projection["issues"] = [
        {
            field: (
                None
                if field == "path" and issue.get(field) in (None, "", "model_data")
                else issue.get(field, 1 if field == "occurrence_count" else None)
            )
            for field in _ISSUE_PROJECTION_FIELDS
        }
        for issue in payload["issues"]
    ]
    return projection


def validate_report_consistency(json_path: PathLike, markdown_path: PathLike) -> None:
    """Validate a schema-v2 ``report.json``/``report.md`` pair."""
    json_payload, json_text = _load_json(json_path)
    markdown_text = _read_text(markdown_path, "Markdown report")
    _check_ai(json_text, "JSON report")
    _check_ai(markdown_text, "Markdown report")
    _check_ai(json_payload, "JSON report")
    _validate_json(json_payload)
    try:
        markdown_payload = _parse_markdown(markdown_text)
    except ReportConsistencyError:
        raise
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        _fail(f"Markdown report is invalid ({type(exc).__name__})")
    if _projection(json_payload) != _projection(markdown_payload):
        _fail("JSON and Markdown report projections differ")


def check_report_consistency(json_path: PathLike, markdown_path: PathLike) -> tuple[str, ...]:
    try:
        validate_report_consistency(json_path, markdown_path)
    except ReportConsistencyError as exc:
        return exc.reasons
    return ()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ReportConsistencyError(f"argument error: {message}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _ArgumentParser(description="Validate schema-v2 export validation report artifacts.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("markdown_path", type=Path)
    try:
        args = parser.parse_args(argv)
        validate_report_consistency(args.json_path, args.markdown_path)
    except ReportConsistencyError as exc:
        print(f"FAIL: {_bounded_text(exc)}", file=sys.stderr)
        return 1
    print("OK: report.json and report.md are consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ReportConsistencyError", "check_report_consistency", "main", "validate_report_consistency"]
