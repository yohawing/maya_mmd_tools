"""Build source and native-runtime provenance for VMD import/export routes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

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


def _frame_value(frame: Any, name: str, default: Any = None) -> Any:
    """Read one VMD frame field from either an object or a mapping."""
    if isinstance(frame, Mapping):
        return frame.get(name, default)
    return getattr(frame, name, default)


def _finite_tuple(value: Any, length: int) -> Optional[tuple[float, ...]]:
    """Normalize one finite numeric vector for raw JSON provenance."""
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if len(result) != length or not all(math.isfinite(item) for item in result):
        return None
    return result


def build_raw_bone_interpolation_provenance(
    frames: Optional[Iterable[Any]],
) -> Dict[str, Any]:
    """Serialize raw bone payload authority for JSON storage.

    The records remain keyed by the original VMD bone name and frame number.
    Interpolation and transform completeness are tracked independently so
    older interpolation-only metadata remains readable while a partial raw
    transform cannot pass a new Mode A payload gate.
    """
    if frames is None:
        return {
            "available": False,
            "complete": False,
            "transform_complete": False,
            "key_count": 0,
            "records": [],
        }

    records = []
    seen = set()
    complete = True
    transform_complete = True
    key_count = 0
    for frame in frames:
        key_count += 1
        name = str(_frame_value(frame, "bone_name", "") or "")
        frame_number = _frame_value(frame, "frame_number")
        interpolation = _frame_value(frame, "interpolation")
        try:
            frame_number = int(frame_number)
            raw = bytes(interpolation)
        except (TypeError, ValueError, OverflowError):
            complete = False
            continue
        key = (name, frame_number)
        if not name or frame_number < 0 or len(raw) != 64 or key in seen:
            complete = False
            continue
        seen.add(key)
        record = {
            "bone_name": name,
            "frame_number": frame_number,
            "interpolation": list(raw),
        }
        position = _finite_tuple(_frame_value(frame, "position"), 3)
        rotation = _finite_tuple(_frame_value(frame, "rotation"), 4)
        if position is None or rotation is None:
            transform_complete = False
        else:
            record["position"] = list(position)
            record["rotation"] = list(rotation)
        records.append(record)

    records.sort(key=lambda item: (item["bone_name"], item["frame_number"]))
    return {
        "available": True,
        "complete": complete and len(records) == key_count,
        "transform_complete": (
            transform_complete and complete and len(records) == key_count
        ),
        "key_count": key_count,
        "records": records,
    }


def build_runtime_registration_provenance(
    *,
    vmd_bytes: bytes,
    pmx_bytes: bytes,
    vmd_source_path: Optional[str],
    pmx_source_path: Optional[str],
    runtime_library_path: Optional[Path],
    runtime_abi_version: int,
    runtime_feature_flags: int,
    raw_bone_frames: Optional[Iterable[Any]] = None,
) -> Dict[str, Any]:
    """Return the model-paired registered import provenance payload.

    Raw source bytes remain authoritative.  The payload records identity only;
    it never stores or substitutes a derived VMD clip.
    """
    payload = {
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
    if raw_bone_frames is not None:
        raw_provenance = build_raw_bone_interpolation_provenance(raw_bone_frames)
        payload.update(
            {
                "raw_bone_interpolation": raw_provenance["records"],
                "raw_bone_interpolation_complete": raw_provenance["complete"],
                "raw_bone_transform_complete": raw_provenance["transform_complete"],
                "raw_bone_key_count": raw_provenance["key_count"],
            }
        )
    return payload


def build_raw_vmd_source_provenance(
    *,
    vmd_bytes: Optional[bytes],
    pmx_bytes: Optional[bytes],
    vmd_source_path: Optional[str],
    pmx_source_path: Optional[str],
    raw_bone_frames: Optional[Iterable[Any]],
) -> Dict[str, Any]:
    """Build fail-closed raw provenance for non-runtime VMD import paths."""
    payload = build_runtime_registration_provenance(
        vmd_bytes=vmd_bytes or b"",
        pmx_bytes=pmx_bytes or b"",
        vmd_source_path=vmd_source_path,
        pmx_source_path=pmx_source_path,
        runtime_library_path=None,
        runtime_abi_version=0,
        runtime_feature_flags=0,
        raw_bone_frames=raw_bone_frames,
    )
    payload.update(
        {
            "registration_mode": "raw_vmd_source",
            "status": "pending",
            "fallback": "legacy",
            "evaluation_mode": "legacy_scene_animation",
        }
    )
    return payload


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
