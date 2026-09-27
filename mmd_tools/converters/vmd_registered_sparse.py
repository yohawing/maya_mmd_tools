"""Adapt compiled registered bone keys to Maya's sparse authoring contract."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Mapping, Sequence, Tuple

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


def _semantic_points(curve: MmdRuntimeBoneTrackCurve) -> Tuple[float, float, float, float]:
    """Return normalized controls without decoding a raw VMD byte layout."""
    if curve.kind != MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER:
        return (0.0, 0.0, 1.0, 1.0)
    return (curve.x1, curve.y1, curve.x2, curve.y2)


class _RegisteredKeyView(MappingABC):
    """Reference an owned native key; expose its controls only when requested."""

    __slots__ = ("bone_name", "_key")
    _channels = {"translate_x": "translation_x", "translate_y": "translation_y",
                 "translate_z": "translation_z", "rotation": "rotation"}

    def __init__(self, bone_name, key):
        self.bone_name = bone_name
        self._key = key

    bone_index = property(lambda self: self._key.bone_index)
    frame_number = property(lambda self: self._key.frame)
    position = property(lambda self: self._key.position_xyz)
    rotation = property(lambda self: self._key.rotation_xyzw)
    semantic_interpolation = property(lambda self: self)

    def __getitem__(self, channel):
        return _semantic_points(getattr(self._key, self._channels[channel]))

    def __iter__(self):
        return iter(self._channels)

    def __len__(self):
        return len(self._channels)


class RegisteredSparseFrames(tuple):
    """Flat compatibility view retaining compiled per-bone groups for authoring."""

    def __new__(cls, groups):
        instance = super().__new__(cls, (frame for frames in groups.values() for frame in frames))
        instance.by_bone = groups
        return instance

    def for_names(self, names):
        return type(self)({identity: frames for identity, frames in self.by_bone.items()
                           if frames and frames[0].bone_name in names})


def registered_sparse_bone_frames(
    tracks: Sequence[MmdRuntimeBoneTrack],
    *,
    bone_names_by_index: Mapping[int, str],
    imported_bone_indices: Mapping[int, str],
) -> RegisteredSparseFrames:
    """Convert compiled tracks after exact PMX bone-index validation.

    Args:
        tracks: Owned compiled authored tracks from ``mmd-anim``.
        bone_names_by_index: Imported PMX ordered index to original bone name.
        imported_bone_indices: Imported PMX ordered index to Maya joint.


    Raises:
        ValueError: If compiled indices disagree with the imported PMX table.
    """
    groups = {}
    seen_indices = set()
    for track in tracks:
        bone_index = int(track.descriptor.bone_index)
        if bone_index in seen_indices:
            raise ValueError(f"duplicate compiled bone track index: {bone_index}")
        seen_indices.add(bone_index)
        if bone_index not in imported_bone_indices or bone_index not in bone_names_by_index:
            raise ValueError(f"compiled bone index is absent from imported PMX table: {bone_index}")
        bone_name = str(bone_names_by_index[bone_index])
        frames = []
        for key in track.keys:
            if int(key.bone_index) != bone_index:
                raise ValueError(f"compiled key/track bone index mismatch: {key.bone_index} != {bone_index}")
            frames.append(_RegisteredKeyView(bone_name, key))
        if frames:
            groups[("index", bone_index)] = frames
    return RegisteredSparseFrames(groups)
