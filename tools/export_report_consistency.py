"""Validate consistency and provenance of canonical export report artifacts.

The checker is deliberately Maya-independent.  It reads the canonical JSON
report and the Markdown report emitted by ``report_artifacts.py``, validates
the source-controlled issue catalog, and compares the report fields that are
presented to users in both artifacts.
"""

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
    get_issue_catalog_entry,
)


PathLike = Union[str, Path]
_REPORT_FIELDS = (
    "status",
    "format",
    "mode",
    "summary",
    "requires_warning_ack",
    "evidence",
)
_ISSUE_FIELDS = (
    "code",
    "category",
    "path",
    "observed",
    "expected",
    "impact",
    "remediation",
    "evidence",
)
_ISSUE_COMPARE_FIELDS = _ISSUE_FIELDS + ("severity", "blocking", "provenance")
_AI_MARKER_PATTERN = re.compile(
    r"chatgpt|openai|ai[\s_-]*generated|generated[\s_-]*by[\s_-]*ai|"
    r"artificial[\s_-]*intelligence|llm[\s_-]*generated",
    re.IGNORECASE,
)
_MARKDOWN_ISSUE_HEADING = re.compile(r"^### (\d+)\. \[([A-Z]+)\] `([^`]+)`$")
_MARKDOWN_INTEGER = re.compile(r"^- (Fatal|Warning|Info): (\d+)$")


class ReportConsistencyError(ValueError):
    """Raised when a report pair is invalid or inconsistent."""

    def __init__(self, *reasons: str):
        """Create an error with one or more bounded failure reasons."""
        normalized = tuple(_bounded_text(reason) for reason in reasons if reason)
        super().__init__("; ".join(normalized) or "report consistency validation failed")
        self.reasons = normalized or ("report consistency validation failed",)


def _bounded_text(value: Any, limit: int = 240) -> str:
    """Return a single-line bounded representation for CLI diagnostics."""
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _fail(message: str) -> None:
    """Raise a consistency error with one bounded reason."""
    raise ReportConsistencyError(message)


def _read_text(path: PathLike, label: str) -> str:
    """Read one UTF-8 artifact and convert I/O errors into bounded failures."""
    try:
        artifact_path = Path(path)
    except (TypeError, ValueError) as exc:
        _fail(f"{label} path is invalid ({type(exc).__name__})")
    try:
        return artifact_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"unable to read {label} ({type(exc).__name__})")


def _load_json_report(path: PathLike) -> tuple[dict[str, Any], str]:
    """Load a JSON report and return its object plus original text."""
    text = _read_text(path, "JSON report")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"JSON report is invalid ({exc.msg}, line {exc.lineno}, column {exc.colno})")
    if not isinstance(payload, dict):
        _fail("JSON report root must be an object")
    return payload, text


def _check_ai_markers(text: str, label: str) -> None:
    """Reject text markers that indicate an AI-generated formal report."""
    match = _AI_MARKER_PATTERN.search(text)
    if match is not None:
        _fail(f"AI-like marker {match.group(0)!r} found in {label}")


def _check_ai_markers_in_value(value: Any, label: str) -> None:
    """Reject markers after JSON decoding, including escaped string values."""
    if isinstance(value, str):
        _check_ai_markers(value, label)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _check_ai_markers_in_value(key, f"{label} key")
            _check_ai_markers_in_value(item, label)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _check_ai_markers_in_value(item, label)


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    """Require a JSON object for a named field."""
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str) -> str:
    """Require a non-empty string for a named report field."""
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _require_bool(value: Any, field: str) -> bool:
    """Require a JSON boolean rather than an integer-like value."""
    if not isinstance(value, bool):
        _fail(f"{field} must be a boolean")
    return value


def _require_nonnegative_integer(value: Any, field: str) -> int:
    """Require a non-negative JSON integer for a summary count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{field} must be a non-negative integer")
    return value


def _validate_json_payload(payload: Mapping[str, Any]) -> None:
    """Validate report structure, catalog wording, and report semantics."""
    required_fields = {
        "schema_version",
        "status",
        "requires_warning_ack",
        "format",
        "mode",
        "summary",
        "evidence",
        "issues",
    }
    missing = sorted(required_fields.difference(payload))
    if missing:
        _fail(f"JSON report is missing required fields: {', '.join(missing)}")
    if payload["schema_version"] != 1:
        _fail("JSON report schema_version must be 1")
    status = _require_string(payload["status"], "status")
    if status not in {"ready", "warning", "blocked"}:
        _fail(f"unsupported report status: {status!r}")
    if payload["format"] is not None and not isinstance(payload["format"], str):
        _fail("format must be a string or null")
    _require_string(payload["mode"], "mode")
    requires_warning_ack = _require_bool(payload["requires_warning_ack"], "requires_warning_ack")
    summary = _require_mapping(payload["summary"], "summary")
    if set(summary) != {"fatal", "warning", "info"}:
        _fail("summary must contain exactly fatal, warning, and info")
    for key in ("fatal", "warning", "info"):
        _require_nonnegative_integer(summary[key], f"summary.{key}")
    _require_mapping(payload["evidence"], "evidence")
    issues = payload["issues"]
    if not isinstance(issues, list):
        _fail("issues must be an array")

    counts = {"fatal": 0, "warning": 0, "info": 0}
    expected_warning_ack = False
    for index, issue in enumerate(issues, start=1):
        _validate_json_issue(issue, index)
        severity = issue["severity"]
        counts[severity] += 1
        if severity == "warning" and not issue["blocking"]:
            expected_warning_ack = True
    if dict(summary) != counts:
        _fail(f"summary counts do not match issues: expected {counts!r}")
    if requires_warning_ack != expected_warning_ack:
        _fail("requires_warning_ack does not match non-blocking warning issues")
    expected_status = "blocked" if any(issue["blocking"] for issue in issues) else (
        "warning" if expected_warning_ack else "ready"
    )
    if status != expected_status:
        _fail(f"status does not match issues: expected {expected_status!r}")


def _validate_json_issue(issue: Any, index: int) -> None:
    """Validate one canonical issue and its source-controlled wording."""
    if not isinstance(issue, Mapping):
        _fail(f"issue {index} must be an object")
    required_fields = {
        "code",
        "severity",
        "blocking",
        "loss_policy",
        "category",
        "path",
        "title_key",
        "observed",
        "expected",
        "impact",
        "action_key",
        "message",
        "remediation",
        "provenance",
        "evidence",
    }
    missing = sorted(required_fields.difference(issue))
    if missing:
        _fail(f"issue {index} is missing required fields: {', '.join(missing)}")
    code = _require_string(issue["code"], f"issue {index}.code")
    try:
        entry = get_issue_catalog_entry(code)
    except KeyError:
        _fail(f"issue {index} uses unregistered issue code {code!r}")
    severity = _require_string(issue["severity"], f"issue {index}.severity")
    if severity not in {"fatal", "warning", "info"}:
        _fail(f"issue {index}.severity is unsupported: {severity!r}")
    _require_bool(issue["blocking"], f"issue {index}.blocking")
    if issue["loss_policy"] != entry.loss_policy:
        _fail(f"issue {index} has catalog mismatch for loss_policy")
    if issue["category"] != entry.category:
        _fail(f"issue {index} has catalog mismatch for category")
    if issue["title_key"] != entry.title_key or issue["action_key"] != entry.action_key:
        _fail(f"issue {index} has catalog mismatch for message keys")
    for field in ("observed", "expected", "impact", "message", "remediation", "provenance"):
        _require_string(issue[field], f"issue {index}.{field}")
    if issue["message"] != issue["observed"]:
        _fail(f"issue {index}.message must match observed")
    for field in ("expected", "impact", "remediation"):
        if issue[field] != getattr(entry, field):
            _fail(f"issue {index}.{field} does not match the issue catalog")
    if issue["path"] is not None and not isinstance(issue["path"], str):
        _fail(f"issue {index}.path must be a string or null")
    _require_mapping(issue["evidence"], f"issue {index}.evidence")


def _parse_backtick_value(line: str, label: str) -> str:
    """Parse a Markdown list field whose value is enclosed in backticks."""
    prefix = f"- {label}: `"
    if not line.startswith(prefix) or not line.endswith("`"):
        _fail(f"Markdown field has invalid format: {label}")
    return line[len(prefix) : -1]


def _parse_json_code_value(line: str, label: str) -> Any:
    """Parse a JSON value wrapped in the renderer's inline code marker."""
    prefix = f"- {label}: `"
    if not line.startswith(prefix) or not line.endswith("`"):
        _fail(f"Markdown field has invalid format: {label}")
    value_text = line[len(prefix) : -1]
    try:
        return json.loads(value_text)
    except json.JSONDecodeError as exc:
        _fail(f"Markdown {label.lower()} is invalid JSON ({exc.msg})")


def _consume_exact(lines: Sequence[str], cursor: int, expected: str) -> int:
    """Consume one exact Markdown line or fail with a bounded reason."""
    if cursor >= len(lines) or lines[cursor] != expected:
        _fail(f"Markdown expected {expected!r}")
    return cursor + 1


def _line_at(lines: Sequence[str], cursor: int, field: str) -> str:
    """Return one Markdown line or report a bounded missing-field failure."""
    if cursor >= len(lines):
        _fail(f"Markdown is missing {field}")
    return lines[cursor]


def _parse_markdown_issue(lines: Sequence[str], heading: re.Match[str], section: Sequence[str]) -> dict[str, Any]:
    """Parse one renderer-shaped Markdown issue section."""
    fields = (
        "Category",
        "Path",
        "Decision",
        "Title",
        "Observed",
        "Expected",
        "Impact",
        "Remediation",
        "Provenance",
        "Evidence",
    )
    values: dict[str, Any] = {
        "code": heading.group(3),
        "severity": heading.group(2).lower(),
        "blocking": None,
    }
    field_index = 0
    for line in section:
        if not line:
            continue
        if field_index >= len(fields):
            _fail(f"Markdown issue {heading.group(3)!r} has extra fields")
        label = fields[field_index]
        if not line.startswith(f"- {label}: "):
            _fail(f"Markdown issue {heading.group(3)!r} has fields out of order")
        if label in {"Category", "Path", "Decision", "Provenance"}:
            values[label.lower()] = _parse_backtick_value(line, label)
        elif label == "Evidence":
            values["evidence"] = _parse_json_code_value(line, label)
        else:
            values[label.lower()] = line[len(f"- {label}: ") :]
        field_index += 1
    if field_index != len(fields):
        _fail(f"Markdown issue {heading.group(3)!r} is missing fields")
    if values["decision"] not in {"BLOCK", "ALLOW"}:
        _fail(f"Markdown issue {heading.group(3)!r} has invalid decision")
    values["blocking"] = values["decision"] == "BLOCK"
    return {
        "code": values["code"],
        "severity": values["severity"],
        "blocking": values["blocking"],
        "category": values["category"],
        "path": values["path"],
        "observed": values["observed"],
        "expected": values["expected"],
        "impact": values["impact"],
        "remediation": values["remediation"],
        "provenance": values["provenance"],
        "evidence": values["evidence"],
        "title": values["title"],
    }


def _parse_markdown_report(markdown: str) -> dict[str, Any]:
    """Parse the deterministic Markdown shape emitted by report artifacts."""
    lines = markdown.splitlines()
    cursor = 0
    cursor = _consume_exact(lines, cursor, "# Export Validation Report")
    cursor = _consume_exact(lines, cursor, "")
    status = _parse_backtick_value(_line_at(lines, cursor, "Status"), "Status")
    cursor += 1
    format_name = _parse_backtick_value(_line_at(lines, cursor, "Format"), "Format")
    cursor += 1
    mode = _parse_backtick_value(_line_at(lines, cursor, "Mode"), "Mode")
    cursor += 1
    _parse_backtick_value(_line_at(lines, cursor, "Target"), "Target")
    cursor += 1
    _parse_backtick_value(_line_at(lines, cursor, "Snapshot fingerprint"), "Snapshot fingerprint")
    cursor += 1
    cursor = _consume_exact(lines, cursor, "")
    cursor = _consume_exact(lines, cursor, "## Summary")
    cursor = _consume_exact(lines, cursor, "")

    summary: dict[str, int] = {}
    for key in ("Fatal", "Warning", "Info"):
        if cursor >= len(lines):
            _fail(f"Markdown summary is missing {key}")
        match = _MARKDOWN_INTEGER.fullmatch(lines[cursor])
        if match is None or match.group(1) != key:
            _fail("Markdown summary has fields out of order")
        summary[key.lower()] = int(match.group(2))
        cursor += 1
    warning_ack = _parse_backtick_value(
        _line_at(lines, cursor, "Warning acknowledgement required"),
        "Warning acknowledgement required",
    )
    if warning_ack not in {"true", "false"}:
        _fail("Markdown warning acknowledgement must be true or false")
    cursor += 1
    cursor = _consume_exact(lines, cursor, "")
    cursor = _consume_exact(lines, cursor, "## Evidence")
    cursor = _consume_exact(lines, cursor, "")
    evidence = _parse_json_code_value(_line_at(lines, cursor, "Report evidence"), "Report evidence")
    if not isinstance(evidence, Mapping):
        _fail("Markdown report evidence must be an object")
    cursor += 1
    cursor = _consume_exact(lines, cursor, "")
    cursor = _consume_exact(lines, cursor, "## Issues")
    cursor = _consume_exact(lines, cursor, "")

    issues: list[dict[str, Any]] = []
    if cursor < len(lines) and lines[cursor] == "No validation issues.":
        cursor += 1
        if any(line for line in lines[cursor:]):
            _fail("Markdown has content after the empty issue list")
    else:
        while cursor < len(lines):
            if not lines[cursor]:
                cursor += 1
                continue
            heading = _MARKDOWN_ISSUE_HEADING.fullmatch(lines[cursor])
            if heading is None:
                _fail("Markdown issue sections are not in the expected order")
            ordinal = int(heading.group(1))
            if ordinal != len(issues) + 1:
                _fail("Markdown issue numbering is not sequential")
            next_cursor = cursor + 1
            while next_cursor < len(lines) and not _MARKDOWN_ISSUE_HEADING.fullmatch(lines[next_cursor]):
                next_cursor += 1
            issues.append(_parse_markdown_issue(lines, heading, lines[cursor + 1 : next_cursor]))
            cursor = next_cursor
        if not issues:
            _fail("Markdown issue list is missing")

    if any(line for line in lines[cursor:]):
        _fail("Markdown has unexpected trailing content")
    return {
        "status": status.lower(),
        "format": None if format_name == "unknown" else format_name,
        "mode": mode,
        "summary": summary,
        "requires_warning_ack": warning_ack == "true",
        "evidence": dict(evidence),
        "issues": issues,
    }


def _normalise_path(value: Any) -> Any:
    """Match the renderer's fallback for an empty issue path."""
    return None if value in (None, "", "model_data") else value


def _json_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Select the report fields that must be identical in both artifacts."""
    return {
        "status": payload["status"],
        "format": payload["format"],
        "mode": payload["mode"],
        "summary": dict(payload["summary"]),
        "requires_warning_ack": payload["requires_warning_ack"],
        "evidence": dict(payload["evidence"]),
        "issues": [
            {
                field: (
                    _normalise_path(issue[field])
                    if field == "path"
                    else issue[field]
                )
                for field in _ISSUE_COMPARE_FIELDS
            }
            for issue in payload["issues"]
        ],
    }


def _markdown_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Select the corresponding fields parsed from Markdown."""
    return {
        "status": payload["status"],
        "format": payload["format"],
        "mode": payload["mode"],
        "summary": dict(payload["summary"]),
        "requires_warning_ack": payload["requires_warning_ack"],
        "evidence": dict(payload["evidence"]),
        "issues": [
            {
                field: (
                    _normalise_path(issue[field])
                    if field == "path"
                    else issue[field]
                )
                for field in _ISSUE_COMPARE_FIELDS
            }
            for issue in payload["issues"]
        ],
    }


def _compare_projections(json_payload: Mapping[str, Any], markdown_payload: Mapping[str, Any]) -> None:
    """Fail if requested report fields or issue order differ."""
    json_projection = _json_projection(json_payload)
    markdown_projection = _markdown_projection(markdown_payload)
    for field in _REPORT_FIELDS:
        if json_projection[field] != markdown_projection[field]:
            _fail(f"report field mismatch: {field}")
    json_issues = json_projection["issues"]
    markdown_issues = markdown_projection["issues"]
    if len(json_issues) != len(markdown_issues):
        _fail("issue count mismatch between JSON and Markdown")
    for index, (json_issue, markdown_issue) in enumerate(zip(json_issues, markdown_issues), start=1):
        if json_issue != markdown_issue:
            differing_fields = [
                field
                for field in _ISSUE_COMPARE_FIELDS
                if json_issue[field] != markdown_issue[field]
            ]
            details = ", ".join(differing_fields) or "issue order"
            _fail(f"issue {index} mismatch between JSON and Markdown ({details})")


def _validate_markdown_catalog(markdown_payload: Mapping[str, Any]) -> None:
    """Ensure Markdown's human-facing catalog wording remains source-controlled."""
    for index, issue in enumerate(markdown_payload["issues"], start=1):
        try:
            entry = get_issue_catalog_entry(issue["code"])
        except KeyError:
            _fail(f"Markdown issue {index} uses unregistered issue code {issue['code']!r}")
        if issue["category"] != entry.category:
            _fail(f"Markdown issue {index} has catalog mismatch for category")
        if issue["title"] != entry.title:
            _fail(f"Markdown issue {index}.title does not match the issue catalog")
        for field in ("expected", "impact", "remediation"):
            if issue[field] != getattr(entry, field):
                _fail(f"Markdown issue {index}.{field} does not match the issue catalog")


def validate_report_consistency(json_path: PathLike, markdown_path: PathLike) -> None:
    """Validate a canonical ``report.json``/``report.md`` pair.

    Args:
        json_path: Path to the canonical JSON report.
        markdown_path: Path to the Markdown report emitted for the same run.

    Raises:
        ReportConsistencyError: If either artifact is unreadable, malformed,
            contains AI-like provenance, violates the issue catalog, or does
            not describe the same report as the other artifact.
    """
    json_payload, json_text = _load_json_report(json_path)
    markdown_text = _read_text(markdown_path, "Markdown report")
    _check_ai_markers(json_text, "JSON report")
    _check_ai_markers(markdown_text, "Markdown report")
    _check_ai_markers_in_value(json_payload, "JSON report")
    _validate_json_payload(json_payload)
    markdown_payload = _parse_markdown_report(markdown_text)
    _check_ai_markers_in_value(markdown_payload, "Markdown report")
    _validate_markdown_catalog(markdown_payload)
    _compare_projections(json_payload, markdown_payload)


def check_report_consistency(json_path: PathLike, markdown_path: PathLike) -> tuple[str, ...]:
    """Return bounded validation reasons, or an empty tuple when valid."""
    try:
        validate_report_consistency(json_path, markdown_path)
    except ReportConsistencyError as exc:
        return exc.reasons
    return ()


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser that maps invalid arguments to the CLI failure code."""

    def error(self, message: str) -> None:
        """Raise a validation error instead of argparse's exit code 2."""
        raise ReportConsistencyError(f"argument error: {message}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the report consistency CLI and return its process exit code."""
    parser = _ArgumentParser(
        description="Validate consistency between canonical export report JSON and Markdown."
    )
    parser.add_argument("json_path", type=Path, help="path to report.json")
    parser.add_argument("markdown_path", type=Path, help="path to report.md")
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


__all__ = [
    "ReportConsistencyError",
    "check_report_consistency",
    "main",
    "validate_report_consistency",
]
