"""Runtime bake frame sampling and batch-buffer helpers."""

from __future__ import annotations

import math
from typing import Dict, List, Tuple


def vmd_frame_to_maya_time(frame: float, fps: float) -> float:
    """Convert VMD's fixed 30fps frame number to Maya time."""
    return float(frame) * float(fps) / 30.0


def maya_time_to_vmd_frame(time_value: float, fps: float) -> float:
    """Convert Maya time back to VMD's fixed 30fps frame number."""
    return float(time_value) * 30.0 / float(fps)


def iter_runtime_bake_frame_samples(min_frame: int, max_frame: int, fps: float) -> List[Tuple[float, float]]:
    """Return (Maya output time, VMD evaluation frame) samples for runtime bake."""
    if max_frame < min_frame:
        return []
    min_time = vmd_frame_to_maya_time(min_frame, fps)
    max_time = vmd_frame_to_maya_time(max_frame, fps)
    min_maya_frame = int(math.ceil(min_time - 1e-9))
    max_maya_frame = int(math.floor(max_time + 1e-9))
    if max_maya_frame < min_maya_frame:
        return []
    return [
        (float(maya_time), maya_time_to_vmd_frame(float(maya_time), fps))
        for maya_time in range(min_maya_frame, max_maya_frame + 1)
    ]


def iter_runtime_bake_frames(min_frame: int, max_frame: int, fps: float) -> List[float]:
    """Return VMD evaluation frames sampled for runtime bake."""
    return [
        vmd_frame
        for _maya_time, vmd_frame in iter_runtime_bake_frame_samples(min_frame, max_frame, fps)
    ]


def runtime_batch_world_matrices_for_frame(batch_result, frame_index: int) -> List[List[float]]:
    """Extract one frame of PMX bone world matrices from a flat batch buffer."""
    bone_count = int(getattr(batch_result, "bone_count", 0) or 0)
    frame_count = int(getattr(batch_result, "frame_count", 0) or 0)
    if frame_index < 0 or frame_index >= frame_count or bone_count <= 0:
        return []
    buffer = getattr(batch_result, "world_matrices", None)
    if buffer is None:
        return []
    frame_offset = frame_index * bone_count * 16
    matrices: List[List[float]] = []
    for bone_index in range(bone_count):
        start = frame_offset + bone_index * 16
        matrices.append([float(buffer[start + column]) for column in range(16)])
    return matrices


def runtime_batch_morph_weights_for_frame(batch_result, frame_index: int) -> List[float]:
    """Extract one frame of PMX morph weights from a flat batch buffer."""
    morph_count = int(getattr(batch_result, "morph_count", 0) or 0)
    frame_count = int(getattr(batch_result, "frame_count", 0) or 0)
    if frame_index < 0 or frame_index >= frame_count or morph_count <= 0:
        return []
    buffer = getattr(batch_result, "morph_weights", None)
    if buffer is None:
        return []
    frame_offset = frame_index * morph_count
    return [float(buffer[frame_offset + index]) for index in range(morph_count)]


def native_local_channel_batch_for_frame(
    native_batch,
    frame_index: int,
) -> Dict[int, Tuple[float, float, float, float, float, float]]:
    """Extract one frame of local channel tuples from native batch output."""
    ordered_bone_indices = native_batch["ordered_bone_indices"]
    bone_count = native_batch["bone_count"]
    channels = native_batch["local_channels"]
    frame_start = int(frame_index) * bone_count * 6
    return {
        bidx: tuple(float(channels[frame_start + slot * 6 + offset]) for offset in range(6))
        for slot, bidx in enumerate(ordered_bone_indices)
    }
