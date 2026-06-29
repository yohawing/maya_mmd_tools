"""Runtime bake cache collection helpers for VMD conversion."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


@dataclass
class RuntimeBakeCache:
    """Collected runtime bake channel arrays and timing stats."""

    baked_frames: List[float]
    bake_times: om.MTimeArray
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]]
    joint_channel_static: Dict[str, Dict[str, dict]]
    morph_cache: List[Tuple[float, list]]
    batch_mode: bool
    eval_elapsed: float
    eval_copy_elapsed: float
    batch_unpack_elapsed: float
    local_elapsed: float
    append_elapsed: float


def collect_runtime_bake_cache(converter, instance, clip, bake_samples) -> RuntimeBakeCache:
    """Evaluate runtime frames and collect Maya channel arrays without scene writes."""
    runtime_anim_layer = converter.anim_layer
    converter.anim_layer = None
    refresh_suspended = False
    outer_refresh_suspended = bool(getattr(converter, "_vmd_import_refresh_suspended", False))

    baked_frames: List[float] = []
    bake_times = om.MTimeArray()
    joint_channel_values = converter._create_runtime_joint_channel_arrays()
    joint_channel_static = converter._create_runtime_joint_channel_static_state()
    morph_cache: List[Tuple[float, list]] = []
    eval_start = time.perf_counter()
    batch_mode = False
    eval_copy_elapsed = 0.0
    batch_unpack_elapsed = 0.0
    local_elapsed = 0.0
    append_elapsed = 0.0

    try:
        if not outer_refresh_suspended:
            try:
                cmds.refresh(suspend=True)
                refresh_suspended = True
            except Exception:
                refresh_suspended = False

        batch_result = None
        if bake_samples:
            batch_start = time.perf_counter()
            batch_vmd_frames = [sample[1] for sample in bake_samples]
            frame_step = (
                float(batch_vmd_frames[1]) - float(batch_vmd_frames[0])
                if len(batch_vmd_frames) > 1
                else 1.0
            )
            batch_result = instance.evaluate_clip_frame_batch(
                clip,
                float(batch_vmd_frames[0]),
                frame_step,
                len(bake_samples),
                worker_count=0,
            )
            eval_copy_elapsed += time.perf_counter() - batch_start

        if batch_result is not None:
            batch_mode = True
            converter.logger.info(
                "Using mmd-anim runtime batch evaluation "
                f"(frames={batch_result.frame_count}, bones={batch_result.bone_count}, "
                f"morphs={batch_result.morph_count})"
            )
            local_start = time.perf_counter()
            native_local_batch = converter._compute_native_local_channel_batch(batch_result)
            local_elapsed += time.perf_counter() - local_start
            if native_local_batch is not None:
                converter.logger.info(
                    "Using native batch local decomposition "
                    f"(frames={native_local_batch['frame_count']}, "
                    f"bones={native_local_batch['bone_count']})"
                )
            for frame_index, (maya_time, _vmd_frame) in enumerate(bake_samples):
                unpack_start = time.perf_counter()
                morph_weights = converter._runtime_batch_morph_weights_for_frame(batch_result, frame_index)
                batch_unpack_elapsed += time.perf_counter() - unpack_start

                bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                if converter.bone_index_to_joint:
                    if not hasattr(converter, "_bone_parent_map") or len(
                        getattr(converter, "_bone_parent_map", {})
                    ) == 0:
                        converter._build_bone_hierarchy_and_order_maps()
                    local_start = time.perf_counter()
                    if native_local_batch is not None:
                        bone_locals = converter._native_local_channel_batch_for_frame(
                            native_local_batch,
                            frame_index,
                        )
                    else:
                        world_matrices = converter._runtime_batch_world_matrices_for_frame(
                            batch_result, frame_index
                        )
                        bone_locals = converter._compute_all_bone_locals(world_matrices)
                    local_elapsed += time.perf_counter() - local_start

                append_start = time.perf_counter()
                baked_frames.append(float(maya_time))
                bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                converter._append_bone_locals_to_channel_arrays(
                    bone_locals, joint_channel_values, joint_channel_static
                )
                morph_cache.append((float(maya_time), morph_weights))
                append_elapsed += time.perf_counter() - append_start
        else:
            if bake_samples:
                converter.logger.info("mmd-anim runtime batch evaluation unavailable; using per-frame ABI")
            for maya_time, vmd_frame in bake_samples:
                eval_copy_start = time.perf_counter()
                if not instance.evaluate_clip_frame(clip, float(vmd_frame)):
                    eval_copy_elapsed += time.perf_counter() - eval_copy_start
                    continue

                world_matrices = instance.get_world_matrices() or []
                morph_weights = instance.get_morph_weights() or []
                eval_copy_elapsed += time.perf_counter() - eval_copy_start

                bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                if converter.bone_index_to_joint:
                    if not hasattr(converter, "_bone_parent_map") or len(
                        getattr(converter, "_bone_parent_map", {})
                    ) == 0:
                        converter._build_bone_hierarchy_and_order_maps()
                    local_start = time.perf_counter()
                    bone_locals = converter._compute_all_bone_locals(world_matrices)
                    local_elapsed += time.perf_counter() - local_start

                append_start = time.perf_counter()
                baked_frames.append(float(maya_time))
                bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                converter._append_bone_locals_to_channel_arrays(
                    bone_locals, joint_channel_values, joint_channel_static
                )
                morph_cache.append((float(maya_time), list(morph_weights)))
                append_elapsed += time.perf_counter() - append_start
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        converter.anim_layer = runtime_anim_layer

    eval_elapsed = time.perf_counter() - eval_start
    return RuntimeBakeCache(
        baked_frames=baked_frames,
        bake_times=bake_times,
        joint_channel_values=joint_channel_values,
        joint_channel_static=joint_channel_static,
        morph_cache=morph_cache,
        batch_mode=batch_mode,
        eval_elapsed=eval_elapsed,
        eval_copy_elapsed=eval_copy_elapsed,
        batch_unpack_elapsed=batch_unpack_elapsed,
        local_elapsed=local_elapsed,
        append_elapsed=append_elapsed,
    )
