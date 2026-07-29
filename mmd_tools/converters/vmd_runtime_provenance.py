"""Build source and native-runtime provenance for registered VMD imports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

import maya.cmds as cmds

from ..core.constants import ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON
from ..core import maya_attribute_utils


def _sha256_bytes(payload: Optional[bytes]) -> str:
    """Return a stable SHA-256 for available source bytes."""
    return hashlib.sha256(payload).hexdigest() if payload else ""


def _sha256_file(path: Optional[Path]) -> str:
    """Return a file SHA-256 without making provenance collection fatal."""
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def build_runtime_registration_provenance(
    *,
    vmd_bytes: bytes,
    pmx_bytes: bytes,
    vmd_source_path: Optional[str],
    pmx_source_path: Optional[str],
    runtime_library_path: Optional[Path],
    runtime_abi_version: int,
    runtime_feature_flags: int,
) -> Dict[str, Any]:
    """Return the model-paired registered import provenance payload.

    Raw source bytes remain authoritative.  The payload records identity only;
    it never stores or substitutes a derived VMD clip.
    """
    return {
        "registration_mode": "model_paired_registered",
        "status": "pending",
        "fallback": "none",
        "raw_vmd_path": str(vmd_source_path or ""),
        "raw_vmd_sha256": _sha256_bytes(vmd_bytes),
        "pmx_path": str(pmx_source_path or ""),
        "pmx_sha256": _sha256_bytes(pmx_bytes),
        "runtime_library_path": str(runtime_library_path or ""),
        "runtime_library_sha256": _sha256_file(runtime_library_path),
        "runtime_abi_version": int(runtime_abi_version),
        "runtime_feature_flags": int(runtime_feature_flags),
    }


def store_runtime_registration_provenance(
    target_model: Optional[str],
    payload: Dict[str, Any],
) -> bool:
    """Persist successful registered-import provenance on one model root."""
    if not target_model or not cmds.objExists(target_model):
        return False
    try:
        if not cmds.attributeQuery(
            ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
            node=target_model,
            exists=True,
        ):
            cmds.addAttr(
                target_model,
                longName=ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
                dataType="string",
            )
        maya_attribute_utils.set_attribute(
            target_model,
            ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "string",
        )
        return True
    except Exception:
        return False
