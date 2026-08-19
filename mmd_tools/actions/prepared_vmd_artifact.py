"""Private, verified VMD artifacts owned by a prepared export token.

The artifact is intentionally independent from the public export path.  A
Mode C preparation can therefore pay the writer and verifier cost once while
the later Workflow export only needs to consume an identity-checked file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
from typing import Any, Optional, Tuple

from ..validation.export_validator import ExportValidationReport


PREPARED_VMD_ARTIFACT_SCHEMA_VERSION = 1

_VMD_SECTIONS = (
    "bone_frames",
    "morph_frames",
    "camera_frames",
    "light_frames",
    "shadow_frames",
    "ik_show_hide_frames",
)


class PreparedVmdArtifactError(ValueError):
    """Raised when a private staged VMD artifact cannot be trusted."""


def _digest_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section_counts(vmd_data: Any) -> dict[str, int]:
    return {
        section: len(getattr(vmd_data, section, ()) or ())
        for section in _VMD_SECTIONS
    }


def _frame_bounds(vmd_data: Any) -> Optional[Tuple[int, int]]:
    frame_numbers = []
    for section in _VMD_SECTIONS:
        for frame in getattr(vmd_data, section, ()) or ():
            try:
                frame_numbers.append(int(frame.frame_number))
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise PreparedVmdArtifactError(
                    f"staged VMD frame number is invalid in {section}"
                ) from exc
    if not frame_numbers:
        return None
    return min(frame_numbers), max(frame_numbers)


@dataclass(frozen=True)
class PreparedVmdArtifactReceipt:
    """Immutable identity and lifecycle handle for one private VMD stage."""

    schema_version: int
    stage_directory: str
    file_path: str
    sha256: str
    size: int
    section_counts: Mapping[str, int]
    frame_bounds: Optional[Tuple[int, int]]
    output_validation_report: ExportValidationReport

    def __post_init__(self) -> None:
        """Freeze mappings even when a caller supplied a mutable dictionary."""

        object.__setattr__(self, "section_counts", MappingProxyType(dict(self.section_counts)))
        if not isinstance(self.output_validation_report, ExportValidationReport):
            raise TypeError("output_validation_report must be ExportValidationReport")

    @property
    def path(self) -> str:
        """Compatibility alias for consumers that use path terminology."""

        return self.file_path

    @property
    def digest(self) -> str:
        """Compatibility alias for the artifact SHA-256."""

        return self.sha256

    @property
    def byte_size(self) -> int:
        """Compatibility alias for the artifact byte count."""

        return self.size

    def validate_identity(self) -> bool:
        """Verify that the owned stage still matches its published receipt."""

        if self.schema_version != PREPARED_VMD_ARTIFACT_SCHEMA_VERSION:
            raise PreparedVmdArtifactError("staged VMD artifact schema version is unsupported")
        if not self.sha256 or len(self.sha256) != 64:
            raise PreparedVmdArtifactError("staged VMD artifact digest is invalid")
        path = Path(self.file_path)
        stage_directory = Path(self.stage_directory)
        if path.parent != stage_directory:
            raise PreparedVmdArtifactError("staged VMD artifact path escaped its private directory")
        if path.is_symlink() or not path.is_file():
            raise PreparedVmdArtifactError("staged VMD artifact is missing")
        actual_size = path.stat().st_size
        if actual_size != self.size:
            raise PreparedVmdArtifactError("staged VMD artifact size changed")
        if _digest_file(path) != self.sha256:
            raise PreparedVmdArtifactError("staged VMD artifact digest changed")
        return True

    def cleanup(self) -> bool:
        """Remove the exact stage file and its private temporary directory."""

        removed = False
        path = Path(self.file_path)
        directory = Path(self.stage_directory)
        if path.parent != directory:
            return False
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed = True
        except FileNotFoundError:
            pass
        except OSError:
            return False
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass
        return removed


def stage_vmd_artifact(
    vmd_data: Any,
    *,
    exporter: Any,
    output_verifier: Any,
    mode: str,
    ack_warnings: bool = False,
) -> PreparedVmdArtifactReceipt:
    """Write and verify one private VMD stage, cleaning failures eagerly."""

    stage_directory = Path(tempfile.mkdtemp(prefix="mmd-vmd-stage-"))
    file_path = stage_directory / "prepared.vmd"
    try:
        expected_counts = _section_counts(vmd_data)
        frame_bounds = _frame_bounds(vmd_data)
        exporter.export_vmd_animation(str(file_path), vmd_data)
        if not file_path.is_file() or file_path.stat().st_size <= 0:
            raise PreparedVmdArtifactError("VMD exporter did not produce a non-empty stage")

        try:
            parameters = inspect.signature(output_verifier).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        verifier_kwargs = (
            {"expected_counts": expected_counts}
            if accepts_kwargs or "expected_counts" in parameters
            else {}
        )
        report = output_verifier(str(file_path), mode, **verifier_kwargs)
        if report is None:
            raise PreparedVmdArtifactError("VMD output verifier returned no report")
        if bool(getattr(report, "is_blocking", False)) or getattr(report, "valid", True) is False:
            raise PreparedVmdArtifactError(f"staged VMD output verification blocked: {report}")
        # A warning is retained on the receipt and acknowledged only by the
        # final publish workflow.  Blocking output findings still fail closed
        # above and clean the private stage in the exception path.

        return PreparedVmdArtifactReceipt(
            schema_version=PREPARED_VMD_ARTIFACT_SCHEMA_VERSION,
            stage_directory=str(stage_directory),
            file_path=str(file_path),
            sha256=_digest_file(file_path),
            size=file_path.stat().st_size,
            section_counts=MappingProxyType(expected_counts),
            frame_bounds=frame_bounds,
            output_validation_report=report,
        )
    except Exception:
        shutil.rmtree(stage_directory, ignore_errors=True)
        raise

__all__ = [
    "PREPARED_VMD_ARTIFACT_SCHEMA_VERSION",
    "PreparedVmdArtifactError",
    "PreparedVmdArtifactReceipt",
    "stage_vmd_artifact",
]
