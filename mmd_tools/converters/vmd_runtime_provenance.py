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


_MAX_EMBEDDED_PROVENANCE_BYTES = 8 * 1024 * 1024
_SOURCE_VMD_STORAGE = "source_vmd_reference"


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
    older metadata remains readable while partial transforms remain visible
    to import diagnostics.
    """
    if frames is None:
        return {
            "available": False,
            "complete": False,
            "transform_complete": False,
            "key_count": 0,
            "records": [],
        }

    records_by_key = {}
    complete = True
    source_key_count = 0
    duplicate_key_count = 0
    ignored_key_count = 0
    for frame in frames:
        source_key_count += 1
        name = str(_frame_value(frame, "bone_name", "") or "")
        if not name:
            # An empty VMD bone name cannot bind to a Maya joint and is not
            # imported.  Keep an audit count, but do not block export of the
            # model-owned motion that actually reached the scene.
            ignored_key_count += 1
            continue
        frame_number = _frame_value(frame, "frame_number")
        interpolation = _frame_value(frame, "interpolation")
        try:
            frame_number = int(frame_number)
            raw = bytes(interpolation)
        except (TypeError, ValueError, OverflowError):
            complete = False
            continue
        key = (name, frame_number)
        if frame_number < 0 or len(raw) != 64:
            complete = False
            continue
        if key in records_by_key:
            # Maya animation curves also collapse repeated keys at the same
            # bone/frame.  Preserve the last VMD record, matching the import
            # result, instead of making an otherwise usable motion impossible
            # to match the import result in diagnostics.
            duplicate_key_count += 1
        record = {
            "bone_name": name,
            "frame_number": frame_number,
            "interpolation": list(raw),
        }
        position = _finite_tuple(_frame_value(frame, "position"), 3)
        rotation = _finite_tuple(_frame_value(frame, "rotation"), 4)
        if position is not None and rotation is not None:
            record["position"] = list(position)
            record["rotation"] = list(rotation)
        records_by_key[key] = record

    records = list(records_by_key.values())
    records.sort(key=lambda item: (item["bone_name"], item["frame_number"]))
    transform_complete = complete and all(
        "position" in record and "rotation" in record for record in records
    )
    return {
        "available": True,
        "complete": complete,
        "transform_complete": transform_complete,
        "key_count": len(records),
        "source_key_count": source_key_count,
        "duplicate_key_count": duplicate_key_count,
        "ignored_key_count": ignored_key_count,
        "records": records,
    }


def build_raw_ik_provenance(frames: Optional[Iterable[Any]]) -> Dict[str, Any]:
    """Serialize the source VMD property/IK section without scene reduction."""

    if frames is None:
        return {
            "available": False,
            "complete": False,
            "key_count": 0,
            "records": [],
        }

    records = []
    complete = True
    source_key_count = 0
    for frame in frames:
        source_key_count += 1
        try:
            frame_number = int(_frame_value(frame, "frame_number"))
            visible = int(_frame_value(frame, "visible"))
            states = list(_frame_value(frame, "ik_states", ()))
        except (TypeError, ValueError, OverflowError):
            complete = False
            continue
        if frame_number < 0 or visible not in (0, 1):
            complete = False
            continue
        normalized_states = []
        malformed = False
        for state in states:
            try:
                name, enabled = state
                enabled = int(enabled)
            except (TypeError, ValueError, OverflowError):
                malformed = True
                break
            if enabled not in (0, 1):
                malformed = True
                break
            normalized_states.append([str(name), enabled])
        if malformed:
            complete = False
            continue
        records.append(
            {
                "frame_number": frame_number,
                "visible": visible,
                "ik_states": normalized_states,
            }
        )
    return {
        "available": True,
        "complete": complete,
        "key_count": len(records),
        "source_key_count": source_key_count,
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
    raw_ik_frames: Optional[Iterable[Any]] = None,
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
                "raw_bone_source_key_count": raw_provenance["source_key_count"],
                "raw_bone_duplicate_key_count": raw_provenance["duplicate_key_count"],
                "raw_bone_ignored_key_count": raw_provenance["ignored_key_count"],
            }
        )
    if raw_ik_frames is not None:
        raw_ik = build_raw_ik_provenance(raw_ik_frames)
        payload.update(
            {
                "raw_ik_frames": raw_ik["records"],
                "raw_ik_complete": raw_ik["complete"],
                "raw_ik_key_count": raw_ik["key_count"],
                "raw_ik_source_key_count": raw_ik["source_key_count"],
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
    raw_ik_frames: Optional[Iterable[Any]] = None,
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
        raw_ik_frames=raw_ik_frames,
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


def _scene_provenance_json(payload: Mapping[str, Any]) -> Optional[str]:
    """Serialize provenance, externalizing oversized raw records to their source VMD."""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(serialized.encode("utf-8")) <= _MAX_EMBEDDED_PROVENANCE_BYTES:
        return serialized
    if not (
        payload.get("raw_bone_interpolation_complete") is True
        and payload.get("raw_bone_transform_complete") is True
        and payload.get("raw_vmd_path")
        and payload.get("raw_vmd_sha256")
    ):
        return None
    compact = dict(payload)
    compact.pop("raw_bone_interpolation", None)
    compact.pop("raw_ik_frames", None)
    compact["raw_bone_interpolation_storage"] = _SOURCE_VMD_STORAGE
    if payload.get("raw_ik_complete") is True:
        compact["raw_ik_storage"] = _SOURCE_VMD_STORAGE
    return json.dumps(
        compact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def materialize_raw_bone_source_provenance(
    payload: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Reload externally stored raw bone authority after identity verification."""

    has_embedded_bones = isinstance(payload.get("raw_bone_interpolation"), list)
    has_embedded_ik = isinstance(payload.get("raw_ik_frames"), list)
    needs_external_bones = not has_embedded_bones
    needs_external_ik = (
        payload.get("raw_ik_storage") == _SOURCE_VMD_STORAGE and not has_embedded_ik
    )
    if has_embedded_bones and not needs_external_ik:
        return dict(payload)
    if needs_external_bones and (
        payload.get("raw_bone_interpolation_storage") != _SOURCE_VMD_STORAGE
    ):
        return None
    source_path = Path(str(payload.get("raw_vmd_path") or ""))
    expected_sha256 = str(payload.get("raw_vmd_sha256") or "")
    if not source_path.is_file() or not expected_sha256:
        return None
    try:
        source_bytes = source_path.read_bytes()
        if _sha256_bytes(source_bytes) != expected_sha256:
            return None
        from ..core.vmd_data import VmdData

        source = VmdData().parse_file(str(source_path))
        raw = build_raw_bone_interpolation_provenance(source.bone_frames)
        expected_count = int(payload.get("raw_bone_key_count", -1))
        raw_ik = build_raw_ik_provenance(source.ik_show_hide_frames)
        expected_ik_count = int(payload.get("raw_ik_key_count", -1))
    except Exception:
        return None
    if (
        needs_external_bones
        and (
            not raw["complete"]
            or not raw["transform_complete"]
            or raw["key_count"] != expected_count
        )
    ):
        return None
    if needs_external_ik and (
        not raw_ik["complete"] or raw_ik["key_count"] != expected_ik_count
    ):
        return None
    materialized = dict(payload)
    if needs_external_bones:
        materialized.update(
            {
                "raw_bone_interpolation": raw["records"],
                "raw_bone_interpolation_complete": True,
                "raw_bone_transform_complete": True,
                "raw_bone_key_count": raw["key_count"],
            }
        )
    if needs_external_ik:
        materialized.update(
            {
                "raw_ik_frames": raw_ik["records"],
                "raw_ik_complete": True,
                "raw_ik_key_count": raw_ik["key_count"],
            }
        )
    return materialized


def store_runtime_registration_provenance(
    target_model: Optional[str],
    payload: Dict[str, Any],
) -> bool:
    """Persist successful registered-import provenance on one model root."""
    if not target_model or not cmds.objExists(target_model):
        return False
    try:
        serialized = _scene_provenance_json(payload)
        if serialized is None:
            return False
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
            serialized,
            "string",
        )
        return True
    except Exception:
        return False
