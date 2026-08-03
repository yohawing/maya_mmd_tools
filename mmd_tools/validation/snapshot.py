"""Immutable payload snapshots and deterministic export fingerprints."""

import copy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Optional


def fingerprint_payload(payload: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-shaped payloads.

    ``allow_nan=False`` is intentional: a payload that cannot be represented
    deterministically is not safe to use as a validation snapshot.
    """
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("export payload cannot be fingerprinted deterministically") from exc
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_revision(scene_revision: Any) -> Optional[str]:
    """Normalize a scene revision token without inventing one."""
    if scene_revision is None:
        return None
    return str(scene_revision)


@dataclass(frozen=True)
class ExportValidationSnapshot:
    """Deep-copied payload and provenance captured for one export attempt."""

    export_format: str
    model_data: Any
    payload_fingerprint: str
    scene_revision: Optional[str] = None
    target_identity: Optional[str] = None

    @classmethod
    def capture(
        cls,
        model_data: Any,
        export_format: str,
        *,
        scene_revision: Any = None,
        target_identity: Any = None,
    ) -> "ExportValidationSnapshot":
        """Capture a deep copy so validation and writing share one payload."""
        snapshot_data = copy.deepcopy(model_data)
        normalized_format = (export_format or "").lower().lstrip(".")
        return cls(
            export_format=normalized_format,
            model_data=snapshot_data,
            payload_fingerprint=fingerprint_payload(snapshot_data),
            scene_revision=_normalize_revision(scene_revision),
            target_identity=(str(target_identity) if target_identity is not None else None),
        )

    def matches(
        self,
        model_data: Any,
        export_format: str,
        *,
        scene_revision: Any = None,
        target_identity: Any = None,
    ) -> bool:
        """Return whether current payload/provenance still matches the snapshot."""
        normalized_format = (export_format or "").lower().lstrip(".")
        if normalized_format != self.export_format:
            return False
        if self.scene_revision != _normalize_revision(scene_revision):
            return False
        normalized_target = str(target_identity) if target_identity is not None else None
        if self.target_identity != normalized_target:
            return False
        try:
            return fingerprint_payload(model_data) == self.payload_fingerprint
        except ValueError:
            return False

    def copy_for_export(self) -> Any:
        """Return a writer-owned copy so a writer cannot mutate the snapshot."""
        return copy.deepcopy(self.model_data)


__all__ = ["ExportValidationSnapshot", "fingerprint_payload"]
