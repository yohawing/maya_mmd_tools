"""Adapt compiled registered bone keys to Maya's sparse authoring contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

from ..core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER,
    MmdRuntimeBoneTrack,
    MmdRuntimeBoneTrackCurve,
)


@dataclass(frozen=True)
class RegisteredSparseBoneFrame:
    """One model-indexed authored local key with semantic incoming curves."""

    bone_name: str
    bone_index: int
    frame_number: int
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]
    semantic_interpolation: Mapping[str, Tuple[float, float, float, float]]
    source_interpolation: Optional[bytes] = None


def _semantic_points(curve: MmdRuntimeBoneTrackCurve) -> Tuple[float, float, float, float]:
    """Return normalized controls without decoding a raw VMD byte layout."""
    if curve.kind != MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER:
        return (0.0, 0.0, 1.0, 1.0)
    return (curve.x1, curve.y1, curve.x2, curve.y2)


def registered_sparse_bone_frames(
    tracks: Sequence[MmdRuntimeBoneTrack],
    *,
    bone_names_by_index: Mapping[int, str],
    imported_bone_indices: Mapping[int, str],
    source_interpolation_by_key: Optional[Mapping[Tuple[int, int], bytes]] = None,
) -> Tuple[RegisteredSparseBoneFrame, ...]:
    """Convert compiled tracks after exact PMX bone-index validation.

    Args:
        tracks: Owned compiled authored tracks from ``mmd-anim``.
        bone_names_by_index: Imported PMX ordered index to original bone name.
        imported_bone_indices: Imported PMX ordered index to Maya joint.
        source_interpolation_by_key: Optional raw VMD export authority keyed by
            ``(bone_index, frame_number)``. It is never used for Maya values or
            tangent authoring.

    Raises:
        ValueError: If compiled indices disagree with the imported PMX table.
    """
    frames = []
    source_interpolation_by_key = source_interpolation_by_key or {}
    seen_indices = set()
    for track in tracks:
        bone_index = int(track.descriptor.bone_index)
        if bone_index in seen_indices:
            raise ValueError(f"duplicate compiled bone track index: {bone_index}")
        seen_indices.add(bone_index)
        if bone_index not in imported_bone_indices or bone_index not in bone_names_by_index:
            raise ValueError(f"compiled bone index is absent from imported PMX table: {bone_index}")
        bone_name = str(bone_names_by_index[bone_index])
        for key in track.keys:
            if int(key.bone_index) != bone_index:
                raise ValueError(f"compiled key/track bone index mismatch: {key.bone_index} != {bone_index}")
            frames.append(
                RegisteredSparseBoneFrame(
                    bone_name=bone_name,
                    bone_index=bone_index,
                    frame_number=int(key.frame),
                    position=tuple(key.position_xyz),
                    rotation=tuple(key.rotation_xyzw),
                    semantic_interpolation={
                        "translate_x": _semantic_points(key.translation_x),
                        "translate_y": _semantic_points(key.translation_y),
                        "translate_z": _semantic_points(key.translation_z),
                        "rotation": _semantic_points(key.rotation),
                    },
                    source_interpolation=source_interpolation_by_key.get(
                        (bone_index, int(key.frame))
                    ),
                )
            )
    return tuple(frames)
