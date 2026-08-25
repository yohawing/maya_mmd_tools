"""Write deterministic Export Validation audit artifacts.

The artifact writer keeps the machine-readable JSON and human-audit Markdown
outputs on the same canonical :class:`ExportValidationReport` boundary.  A
caller supplies the run directory explicitly so ordinary exports do not leave
untracked report files in the repository or scene directory.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Union

from .export_validator import ExportValidationReport


PathLike = Union[str, os.PathLike]


@dataclass(frozen=True)
class ValidationReportArtifactPaths:
    """Paths written for one validation run."""

    run_directory: Path
    json_path: Path
    markdown_path: Path


def _write_text_atomically(file_path: Path, content: str) -> None:
    """Replace one UTF-8 text artifact without exposing a partial file."""
    temporary_path: Optional[str] = None
    try:
        temporary_handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            dir=str(file_path.parent),
            delete=False,
        )
        temporary_path = temporary_handle.name
        with temporary_handle:
            temporary_handle.write(content)
            temporary_handle.flush()
            os.fsync(temporary_handle.fileno())
        os.replace(temporary_path, file_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def write_validation_report_artifacts(
    report: ExportValidationReport,
    run_directory: PathLike,
    *,
    target_identity: Optional[str] = None,
    snapshot_fingerprint: Optional[str] = None,
    provenance: str = "PayloadValidator",
    evidence: Optional[Mapping[str, Any]] = None,
) -> ValidationReportArtifactPaths:
    """Write ``report.json`` and ``report.md`` for one validation run.

    Canonical rendering happens before the directory is created.  Therefore an
    unregistered issue code fails closed without leaving a misleading partial
    run directory.  Each final file is replaced atomically after its complete
    UTF-8 content has been flushed.

    Args:
        report: Validation report to render.
        run_directory: Explicit directory for this run, normally a directory
            such as ``build/reports/export_validation/<run_id>``.
        target_identity: Optional scene/model identity recorded in both views.
        snapshot_fingerprint: Optional immutable payload fingerprint.
        provenance: Stable validator/action provenance label.
        evidence: Additional JSON-serializable evidence shared by both views.

    Returns:
        The paths of the two written artifacts.

    Raises:
        UnknownValidationIssueError: If the report contains a code absent from
            the source-controlled issue catalog.
        OSError: If the directory or either artifact cannot be written.
    """
    evidence_payload = dict(evidence or {})
    canonical_json = report.to_canonical_json(
        target_identity=target_identity,
        snapshot_fingerprint=snapshot_fingerprint,
        provenance=provenance,
        evidence=evidence_payload,
    ) + "\n"
    markdown = report.to_markdown(
        target_identity=target_identity,
        snapshot_fingerprint=snapshot_fingerprint,
        provenance=provenance,
        evidence=evidence_payload,
    )
    if not markdown.endswith("\n"):
        markdown += "\n"

    output_directory = Path(run_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "report.json"
    markdown_path = output_directory / "report.md"
    _write_text_atomically(json_path, canonical_json)
    _write_text_atomically(markdown_path, markdown)
    return ValidationReportArtifactPaths(
        run_directory=output_directory,
        json_path=json_path,
        markdown_path=markdown_path,
    )


__all__ = ["ValidationReportArtifactPaths", "write_validation_report_artifacts"]
