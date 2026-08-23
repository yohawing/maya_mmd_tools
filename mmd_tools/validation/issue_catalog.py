"""Stable taxonomy and English policy for export-validation reports.

The validator keeps the observed reason at the detection boundary. This
module owns only the small, stable machine taxonomy and the default action
shown when a detector does not provide a more specific remediation.
"""

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, Mapping, Optional


STABLE_ISSUE_CODES = (
    "INPUT_INVALID",
    "REFERENCE_INVALID",
    "UNSUPPORTED_FEATURE",
    "SCENE_INVALID",
    "OWNERSHIP_CONFLICT",
    "ROUTE_UNRESOLVED",
    "EXPORT_OPTIONS_INVALID",
    "COLLECTION_FAILED",
    "STALE_STATE",
    "OUTPUT_WRITE_FAILED",
    "OUTPUT_VERIFY_FAILED",
    "EXTERNAL_TOOL_FAILED",
    "INTERNAL_ERROR",
)


class UnknownValidationIssueError(KeyError):
    """Raised when a report contains a code outside the stable taxonomy."""


@dataclass(frozen=True)
class IssueCatalogEntry:
    """Stable policy metadata for one report code."""

    code: str
    reason: str
    action: str


_CATALOG_TEXT = {
    "INPUT_INVALID": (
        "The export payload is malformed or contains an invalid value.",
        "Repair the payload field and run export validation again.",
    ),
    "REFERENCE_INVALID": (
        "An export reference or count does not resolve to valid data.",
        "Repair the referenced index or count before exporting.",
    ),
    "UNSUPPORTED_FEATURE": (
        "The selected export format does not support this feature.",
        "Remove the unsupported feature or choose a supported export path.",
    ),
    "SCENE_INVALID": (
        "The selected scene target is missing or invalid.",
        "Select a live MMD target and repair the scene before exporting.",
    ),
    "OWNERSHIP_CONFLICT": (
        "Scene ownership is ambiguous or conflicts with the requested export route.",
        "Resolve the owning rig or authoring authority before exporting.",
    ),
    "ROUTE_UNRESOLVED": (
        "The requested authoring route cannot be resolved safely.",
        "Repair the route binding or keep the export fail-closed.",
    ),
    "EXPORT_OPTIONS_INVALID": (
        "The export options are outside the supported contract.",
        "Correct the format, output path, scale, or frame range and retry.",
    ),
    "COLLECTION_FAILED": (
        "Scene or semantic collection did not produce a trustworthy payload.",
        "Repair the source scene or collection boundary before exporting.",
    ),
    "STALE_STATE": (
        "The validated scene or payload is stale.",
        "Revalidate the current scene and export from the fresh snapshot.",
    ),
    "OUTPUT_WRITE_FAILED": (
        "The temporary export output could not be written.",
        "Inspect the output directory and permissions, then retry the export.",
    ),
    "OUTPUT_VERIFY_FAILED": (
        "The generated output failed header, parse, count, or range verification.",
        "Inspect the writer output and fix the export path before replacing the target.",
    ),
    "EXTERNAL_TOOL_FAILED": (
        "An external runtime or binding verification step failed.",
        "Inspect the external tool diagnostics and retry with a valid runtime.",
    ),
    "INTERNAL_ERROR": (
        "An unexpected export workflow error occurred.",
        "Capture the diagnostics and report the failure without bypassing validation.",
    ),
}

ISSUE_CATALOG = {
    code: IssueCatalogEntry(code, reason, action)
    for code, (reason, action) in _CATALOG_TEXT.items()
}


def get_issue_catalog_entry(code: str) -> IssueCatalogEntry:
    """Return stable policy metadata or fail closed for an unknown code."""
    try:
        return ISSUE_CATALOG[code]
    except KeyError as exc:
        raise UnknownValidationIssueError(
            f"Validation issue code is not registered in the stable taxonomy: {code}"
        ) from exc


def validate_issue_catalog(codes: Iterable[str]) -> None:
    """Fail if any emitted code is absent from the stable taxonomy."""
    unknown = sorted(set(codes).difference(ISSUE_CATALOG))
    if unknown:
        raise UnknownValidationIssueError(
            f"Unregistered validation issue codes: {', '.join(unknown)}"
        )


def canonical_issue_dict(
    issue: Any,
    *,
    provenance: str = "PayloadValidator",
    snapshot_fingerprint: Optional[str] = None,
    evidence: Optional[Mapping[str, Any]] = None,
    occurrence_count: Optional[int] = None,
    path_pattern: Optional[str] = None,
    sample_paths: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Render one v2 issue without legacy catalog fields or aliases."""
    code = str(issue.code)
    entry = get_issue_catalog_entry(code)
    issue_evidence = dict(evidence or {})
    issue_evidence.update(dict(getattr(issue, "evidence", {}) or {}))
    if snapshot_fingerprint is not None:
        issue_evidence.setdefault("snapshot_fingerprint", snapshot_fingerprint)
    reason = str(getattr(issue, "reason", ""))
    action = str(getattr(issue, "action", "") or entry.action)
    details = dict(getattr(issue, "details", {}) or {})
    payload = {
        "code": code,
        "severity": issue.severity,
        "blocking": issue.blocking,
        "path": issue.path,
        "reason": reason or entry.reason,
        "action": action,
        "details": details,
        "evidence": issue_evidence,
        "provenance": provenance,
    }
    if occurrence_count is not None:
        payload.update(
            {
                "occurrence_count": occurrence_count,
                "path_pattern": path_pattern,
                "sample_paths": list(sample_paths or ()),
            }
        )
    return payload


def render_validation_report_markdown(
    report: Any,
    *,
    target_identity: Optional[str] = None,
    snapshot_fingerprint: Optional[str] = None,
    provenance: str = "PayloadValidator",
    evidence: Optional[Mapping[str, Any]] = None,
) -> str:
    """Render the v2 canonical report for human audit."""
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
        f"- Status: {json.dumps(payload['status'].upper(), ensure_ascii=False)}",
        f"- Format: {json.dumps(payload['format'], ensure_ascii=False)}",
        f"- Mode: {json.dumps(payload['mode'], ensure_ascii=False)}",
        f"- Target: {json.dumps(payload['target_identity'], ensure_ascii=False)}",
        "- Snapshot fingerprint: "
        + json.dumps(payload["snapshot_fingerprint"], ensure_ascii=False),
        "",
        "## Summary",
        "",
        f"- Fatal: {summary['fatal']}",
        f"- Warning: {summary['warning']}",
        f"- Info: {summary['info']}",
        f"- Warning acknowledgement required: `{str(payload['requires_warning_ack']).lower()}`",
    ]
    aggregation = payload.get("issue_aggregation")
    if aggregation is not None:
        lines.extend(
            [
                f"- Issue display limit: {aggregation['max_display_issues']}",
                f"- Issue occurrences shown: {aggregation['shown_occurrences']}",
                f"- Issue occurrences omitted: {aggregation['omitted_occurrences']}",
                f"- Issue groups shown: {aggregation['shown_groups']} / {aggregation['total_groups']}",
                f"- Blocking issue present: `{str(aggregation['has_blocking']).lower()}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Report evidence: `{json.dumps(payload['evidence'], ensure_ascii=False, sort_keys=True)}`",
            "",
            "## Issues",
            "",
        ]
    )
    if not payload["issues"]:
        lines.append("No validation issues.")
    else:
        for index, issue in enumerate(payload["issues"], start=1):
            lines.extend(
                [
                    f"### {index}. [{issue['severity'].upper()}] `{issue['code']}`",
                    "",
                    f"- Path: {json.dumps(issue['path'], ensure_ascii=False)}",
                    f"- Decision: `{'BLOCK' if issue['blocking'] else 'ALLOW'}`",
                ]
            )
            if "occurrence_count" in issue:
                lines.extend(
                    [
                        f"- Occurrences: `{issue['occurrence_count']}`",
                        f"- Path pattern: `{issue['path_pattern']}`",
                        f"- Sample paths: `{json.dumps(issue['sample_paths'], ensure_ascii=False)}`",
                    ]
                )
            lines.extend(
                [
                    f"- Reason: {json.dumps(issue['reason'], ensure_ascii=False)}",
                    f"- Action: {json.dumps(issue['action'], ensure_ascii=False)}",
                    f"- Details: `{json.dumps(issue['details'], ensure_ascii=False, sort_keys=True)}`",
                    f"- Provenance: {json.dumps(issue['provenance'], ensure_ascii=False)}",
                    f"- Evidence: `{json.dumps(issue['evidence'], ensure_ascii=False, sort_keys=True)}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ISSUE_CATALOG",
    "STABLE_ISSUE_CODES",
    "IssueCatalogEntry",
    "UnknownValidationIssueError",
    "canonical_issue_dict",
    "get_issue_catalog_entry",
    "render_validation_report_markdown",
    "validate_issue_catalog",
]
