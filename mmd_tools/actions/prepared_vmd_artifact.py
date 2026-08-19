"""Private, verified VMD artifacts owned by a prepared export token.

The artifact is intentionally independent from the public export path.  A
Mode C preparation can therefore pay the writer and verifier cost once while
the later Workflow export only needs to consume an identity-checked file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
from typing import Any, Optional, Tuple

from ..validation.export_validator import ExportValidationReport
from ..validation.vmd_validator import verify_vmd_output_streaming


PREPARED_VMD_ARTIFACT_SCHEMA_VERSION = 1

_STREAM_SECTION_TO_RECEIPT = {
    "bones": "bone_frames",
    "morphs": "morph_frames",
    "cameras": "camera_frames",
    "lights": "light_frames",
    "shadows": "shadow_frames",
    "ik": "ik_show_hide_frames",
}

_UNSET = object()


class PreparedVmdArtifactError(ValueError):
    """Raised when a private staged VMD artifact cannot be trusted."""


def _digest_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


class PreparedVmdStageSession:
    """Own one incremental private VMD stage until it is promoted.

    The session is deliberately a small adapter around :class:`VmdStreamWriter`.
    It keeps no frame collection and exposes only ordered section writes.  A
    successful ``finish`` transfers the private directory to the returned
    :class:`PreparedVmdArtifactReceipt`; every other exit path removes it.
    """

    def __init__(
        self,
        model_name: str = "",
        *,
        mode: str = "C",
        output_verifier: Any = verify_vmd_output_streaming,
        raw_loss_warning_required: bool = False,
        expected_frame_range: Optional[Tuple[int, int]] = None,
    ) -> None:
        self._mode = mode
        self._output_verifier = output_verifier
        self._raw_loss_warning_required = bool(raw_loss_warning_required)
        self._expected_frame_range = expected_frame_range
        self._stage_directory = Path(tempfile.mkdtemp(prefix="mmd-vmd-stage-"))
        self._file_path = self._stage_directory / "prepared.vmd"
        self._writer: Optional[Any] = None
        self._summary: Optional[Any] = None
        self._receipt: Optional[PreparedVmdArtifactReceipt] = None
        self._cleaned = False
        try:
            # Import lazily to avoid the existing io package initialization
            # path cycling back through the Maya prepare backend and action.
            from ..io.vmd_stream_writer import VmdStreamWriter

            self._writer = VmdStreamWriter(self._file_path, model_name)
        except BaseException:
            self._cleanup()
            raise

    @property
    def stage_directory(self) -> str:
        """Return the private stage directory, including after promotion."""

        return str(self._stage_directory)

    @property
    def file_path(self) -> str:
        """Return the staged VMD path."""

        return str(self._file_path)

    def __enter__(self) -> "PreparedVmdStageSession":
        if self._cleaned and self._receipt is None:
            raise PreparedVmdArtifactError("VMD stage session is closed")
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if self._receipt is None:
            self._cleanup()
        return False

    def _cleanup(self) -> None:
        """Release writer handles and remove this session's private stage."""

        if self._receipt is not None or self._cleaned:
            return
        self._cleaned = True
        writer = self._writer
        self._writer = None
        if writer is not None and self._summary is None:
            try:
                writer.abort()
            except BaseException:
                # Preserve the original write/finalize/cancellation exception.
                pass
        try:
            shutil.rmtree(str(self._stage_directory), ignore_errors=True)
        except BaseException:
            # Temporary-stage cleanup must not replace the triggering error.
            pass

    def cleanup(self) -> bool:
        """Abort this pending stage; repeated calls are harmless."""

        was_pending = self._receipt is None and not self._cleaned
        self._cleanup()
        return was_pending

    def _handle_failure(self) -> None:
        self._cleanup()

    def _writer_or_fail(self) -> Any:
        writer = self._writer
        if writer is None:
            raise PreparedVmdArtifactError("VMD stage session is not writable")
        return writer

    def begin_section(self, section: str) -> None:
        try:
            self._writer_or_fail().begin_section(section)
        except BaseException:
            self._handle_failure()
            raise

    def end_section(self) -> None:
        try:
            self._writer_or_fail().end_section()
        except BaseException:
            self._handle_failure()
            raise

    def write_frame(self, section: str, frame: Any) -> None:
        """Write one frame in canonical VMD section order."""

        try:
            self._writer_or_fail().write_frame(section, frame)
        except BaseException:
            self._handle_failure()
            raise

    def set_expected_frame_range(self, frame_range: Tuple[int, int]) -> None:
        """Set converted VMD bounds before the writer is finalized."""

        if self._summary is not None or self._receipt is not None or self._cleaned:
            raise PreparedVmdArtifactError(
                "VMD stage frame range cannot change after collection"
            )
        self._expected_frame_range = frame_range

    def finish_collection(self) -> Any:
        """Flush the writer and return its bounded summary.

        This does not promote the stage.  A context that exits after this
        method without calling ``promote`` still removes the private stage.
        """

        if self._summary is not None:
            return self._summary
        try:
            summary = self._writer_or_fail().finish()
            self._writer = None
            self._summary = summary
            return summary
        except BaseException:
            self._handle_failure()
            raise

    def _verification_kwargs(
        self,
        summary: Any,
        *,
        raw_loss_warning_required: Any = _UNSET,
    ) -> dict[str, Any]:
        result = {
            "expected_counts": summary.counts,
            "expected_bounds": summary.frame_bounds,
            "expected_sha256": summary.sha256,
            "expected_size": summary.size,
            "raw_loss_warning_required": (
                self._raw_loss_warning_required
                if raw_loss_warning_required is _UNSET
                else bool(raw_loss_warning_required)
            ),
            # Preparation records warnings on the receipt.  Only the final
            # publish workflow may acknowledge them.
            "ack_warnings": False,
        }
        if self._expected_frame_range is not None:
            result["expected_frame_range"] = self._expected_frame_range
        return result

    def _verify(
        self,
        summary: Any,
        *,
        raw_loss_warning_required: Any = _UNSET,
    ) -> ExportValidationReport:
        verifier = self._output_verifier
        if verifier is None:
            verifier = verify_vmd_output_streaming
        report = verifier(
            str(self._file_path),
            self._mode,
            **self._verification_kwargs(
                summary,
                raw_loss_warning_required=raw_loss_warning_required,
            ),
        )
        if not isinstance(report, ExportValidationReport):
            raise PreparedVmdArtifactError("VMD output verifier returned no validation report")
        if report.is_blocking or report.valid is False:
            raise PreparedVmdArtifactError(
                "staged VMD output verification blocked: {}".format(report)
            )
        return report

    def _assert_summary_identity(self, summary: Any) -> None:
        """Reject tampering before or during the bounded verification pass."""

        if not self._file_path.is_file() or self._file_path.stat().st_size != summary.size:
            raise PreparedVmdArtifactError("staged VMD output changed before promotion")
        if _digest_file(self._file_path) != summary.sha256:
            raise PreparedVmdArtifactError("staged VMD output changed before promotion")

    def promote(
        self,
        *,
        raw_loss_warning_required: Any = _UNSET,
    ) -> PreparedVmdArtifactReceipt:
        """Verify and transfer stage ownership to an immutable receipt."""

        if self._receipt is not None:
            return self._receipt
        try:
            summary = self._summary
            if summary is None:
                raise PreparedVmdArtifactError("VMD collection has not been finished")
            self._assert_summary_identity(summary)
            report = self._verify(
                summary,
                raw_loss_warning_required=raw_loss_warning_required,
            )
            self._assert_summary_identity(summary)
            counts = {
                receipt_name: int(summary.counts.get(stream_name, 0))
                for stream_name, receipt_name in _STREAM_SECTION_TO_RECEIPT.items()
            }
            frame_bounds = None
            if summary.min_frame is not None and summary.max_frame is not None:
                frame_bounds = (summary.min_frame, summary.max_frame)
            receipt = PreparedVmdArtifactReceipt(
                schema_version=PREPARED_VMD_ARTIFACT_SCHEMA_VERSION,
                stage_directory=str(self._stage_directory),
                file_path=str(self._file_path),
                sha256=summary.sha256,
                size=summary.size,
                section_counts=MappingProxyType(counts),
                frame_bounds=frame_bounds,
                output_validation_report=report,
            )
            self._receipt = receipt
            self._writer = None
            self._cleaned = True
            return receipt
        except BaseException:
            self._handle_failure()
            raise

    def finish(
        self,
        *,
        raw_loss_warning_required: Any = _UNSET,
    ) -> PreparedVmdArtifactReceipt:
        """Finish, bounded-verify, and promote this stage in one operation."""

        if self._receipt is not None:
            return self._receipt
        self.finish_collection()
        return self.promote(
            raw_loss_warning_required=raw_loss_warning_required,
        )

__all__ = [
    "PREPARED_VMD_ARTIFACT_SCHEMA_VERSION",
    "PreparedVmdArtifactError",
    "PreparedVmdArtifactReceipt",
    "PreparedVmdStageSession",
]
