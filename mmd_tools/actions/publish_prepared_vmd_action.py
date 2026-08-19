"""Publish a verified private VMD stage without invoking the VMD writer.

Mode C preparation already pays the expensive encode and output verification
cost.  This action is deliberately a small file boundary: it re-checks the
receipt, streams the private stage into a sibling temporary file, verifies the
bytes that were copied, and only then atomically replaces the requested path.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Optional

from .export_vmd_action import ExportVmdResult
from .prepared_vmd_artifact import PreparedVmdArtifactError, PreparedVmdArtifactReceipt
from ..validation.export_validator import ExportValidationReport


_COPY_CHUNK_SIZE = 1024 * 1024


def _copy_and_digest(source: Path, destination: Path) -> tuple[int, str]:
    """Copy ``source`` into ``destination`` while calculating size and digest."""

    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as source_handle, destination.open("wb") as destination_handle:
        for chunk in iter(lambda: source_handle.read(_COPY_CHUNK_SIZE), b""):
            destination_handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        destination_handle.flush()
        try:
            os.fsync(destination_handle.fileno())
        except OSError:
            # Some filesystems do not expose fsync for temporary files.  The
            # byte-level identity check below remains mandatory in that case.
            pass
    return size, digest.hexdigest()


def publish_prepared_vmd_artifact(
    receipt: PreparedVmdArtifactReceipt,
    target_path: str,
    *,
    validation_report: ExportValidationReport,
    payload_fingerprint: Optional[str] = None,
) -> ExportVmdResult:
    """Atomically publish one receipt-owned VMD stage.

    The target is not opened until the staged bytes have been copied and
    checked.  Consequently a source tamper, copy failure, or replace failure
    leaves an existing output untouched.
    """

    temporary_path: Optional[Path] = None
    target = Path(target_path)
    digest_fingerprint = payload_fingerprint or receipt.sha256
    try:
        if not isinstance(receipt, PreparedVmdArtifactReceipt):
            raise PreparedVmdArtifactError("prepared VMD artifact receipt is invalid")
        # This check is intentionally immediately before opening the source.
        # It closes the receipt-to-copy race that a cached validation report
        # alone cannot cover.
        receipt.validate_identity()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.",
            suffix=target.suffix or ".vmd",
            dir=str(target.parent),
        )
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)

        copied_size, copied_digest = _copy_and_digest(Path(receipt.file_path), temporary_path)
        if copied_size != receipt.size or copied_digest != receipt.sha256:
            raise PreparedVmdArtifactError("prepared VMD artifact changed during publish")
        # The source may have changed just after the read completed.  A final
        # identity check rejects that race before the target is replaced.
        receipt.validate_identity()
        os.replace(str(temporary_path), str(target))
        temporary_path = None
        return ExportVmdResult(
            exported_path=str(target),
            succeeded=True,
            status_message=f"Export complete: {target}",
            validation_report=validation_report,
            payload_fingerprint=digest_fingerprint,
        )
    except Exception as exc:
        return ExportVmdResult(
            status_message=f"Export failed: {exc}",
            error=exc,
            warnings=list(getattr(validation_report, "issues", ()) or ()),
            validation_report=validation_report,
            payload_fingerprint=digest_fingerprint,
        )
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


__all__ = ["publish_prepared_vmd_artifact"]
