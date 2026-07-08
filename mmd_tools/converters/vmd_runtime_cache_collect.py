"""Runtime bake cache collection helpers for VMD conversion."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .vmd_context import VmdRuntimeCacheCollectContext


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


def _resolve_runtime_cache_collect_context(
    converter_or_context: Union[Any, VmdRuntimeCacheCollectContext],
) -> VmdRuntimeCacheCollectContext:
    if isinstance(converter_or_context, VmdRuntimeCacheCollectContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_runtime_cache_collect_context", None)
    if callable(factory):
        return factory()
    def _ensure_bone_hierarchy_maps() -> None:
        if not hasattr(converter_or_context, "_bone_parent_map") or len(
            getattr(converter_or_context, "_bone_parent_map", {})
        ) == 0:
            build_maps = getattr(converter_or_context, "_build_bone_hierarchy_and_order_maps", None)
            if callable(build_maps):
                build_maps()

    return VmdRuntimeCacheCollectContext(
        logger=getattr(converter_or_context, "logger", None),
        bone_index_to_joint=getattr(converter_or_context, "bone_index_to_joint", {}),
        outer_refresh_suspended=bool(getattr(converter_or_context, "_vmd_import_refresh_suspended", False)),
        get_anim_layer=lambda: getattr(converter_or_context, "anim_layer", None),
        set_anim_layer=lambda value: setattr(converter_or_context, "anim_layer", value),
        create_runtime_joint_channel_arrays=getattr(
            converter_or_context,
            "_create_runtime_joint_channel_arrays",
            lambda: {},
        ),
        create_runtime_joint_channel_static_state=getattr(
            converter_or_context,
            "_create_runtime_joint_channel_static_state",
            lambda: {},
        ),
        compute_native_local_channel_batch=getattr(
            converter_or_context,
            "_compute_native_local_channel_batch",
            lambda _batch_result: None,
        ),
        runtime_batch_morph_weights_for_frame=getattr(
            converter_or_context,
            "_runtime_batch_morph_weights_for_frame",
            lambda _batch_result, _frame_index: [],
        ),
        ensure_bone_hierarchy_maps=_ensure_bone_hierarchy_maps,
        native_local_channel_batch_for_frame=getattr(
            converter_or_context,
            "_native_local_channel_batch_for_frame",
            lambda _native_local_batch, _frame_index: {},
        ),
        runtime_batch_world_matrices_for_frame=getattr(
            converter_or_context,
            "_runtime_batch_world_matrices_for_frame",
            lambda _batch_result, _frame_index: [],
        ),
        compute_all_bone_locals=getattr(
            converter_or_context,
            "_compute_all_bone_locals",
            lambda _world_matrices: {},
        ),
        append_bone_locals_to_channel_arrays=getattr(
            converter_or_context,
            "_append_bone_locals_to_channel_arrays",
            lambda _bone_locals, _values, _static: None,
        ),
    )


def _collect_runtime_bake_cache(
    context: VmdRuntimeCacheCollectContext,
    instance,
    clip,
    bake_samples,
) -> RuntimeBakeCache:
    """Evaluate runtime frames and collect Maya channel arrays without scene writes."""
    runtime_anim_layer = context.get_anim_layer()
    context.set_anim_layer(None)
    refresh_suspended = False

    baked_frames: List[float] = []
    bake_times = om.MTimeArray()
    joint_channel_values = context.create_runtime_joint_channel_arrays()
    joint_channel_static = context.create_runtime_joint_channel_static_state()
    morph_cache: List[Tuple[float, list]] = []
    eval_start = time.perf_counter()
    batch_mode = False
    eval_copy_elapsed = 0.0
    batch_unpack_elapsed = 0.0
    local_elapsed = 0.0
    append_elapsed = 0.0

    try:
        if not context.outer_refresh_suspended:
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
            context.logger.info(
                "Using mmd-anim runtime batch evaluation "
                f"(frames={batch_result.frame_count}, bones={batch_result.bone_count}, "
                f"morphs={batch_result.morph_count})"
            )
            local_start = time.perf_counter()
            native_local_batch = context.compute_native_local_channel_batch(batch_result)
            local_elapsed += time.perf_counter() - local_start
            if native_local_batch is not None:
                context.logger.info(
                    "Using native batch local decomposition "
                    f"(frames={native_local_batch['frame_count']}, "
                    f"bones={native_local_batch['bone_count']})"
                )
            for frame_index, (maya_time, _vmd_frame) in enumerate(bake_samples):
                unpack_start = time.perf_counter()
                morph_weights = context.runtime_batch_morph_weights_for_frame(batch_result, frame_index)
                batch_unpack_elapsed += time.perf_counter() - unpack_start

                bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                if context.bone_index_to_joint:
                    context.ensure_bone_hierarchy_maps()
                    local_start = time.perf_counter()
                    if native_local_batch is not None:
                        bone_locals = context.native_local_channel_batch_for_frame(
                            native_local_batch,
                            frame_index,
                        )
                    else:
                        world_matrices = context.runtime_batch_world_matrices_for_frame(
                            batch_result, frame_index
                        )
                        bone_locals = context.compute_all_bone_locals(world_matrices)
                    local_elapsed += time.perf_counter() - local_start

                append_start = time.perf_counter()
                baked_frames.append(float(maya_time))
                bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                context.append_bone_locals_to_channel_arrays(
                    bone_locals, joint_channel_values, joint_channel_static
                )
                morph_cache.append((float(maya_time), morph_weights))
                append_elapsed += time.perf_counter() - append_start
        else:
            if bake_samples:
                context.logger.info("mmd-anim runtime batch evaluation unavailable; using per-frame ABI")
            for maya_time, vmd_frame in bake_samples:
                eval_copy_start = time.perf_counter()
                if not instance.evaluate_clip_frame(clip, float(vmd_frame)):
                    eval_copy_elapsed += time.perf_counter() - eval_copy_start
                    continue

                world_matrices = instance.get_world_matrices() or []
                morph_weights = instance.get_morph_weights() or []
                eval_copy_elapsed += time.perf_counter() - eval_copy_start

                bone_locals: Dict[int, Tuple[float, float, float, float, float, float]] = {}
                if context.bone_index_to_joint:
                    context.ensure_bone_hierarchy_maps()
                    local_start = time.perf_counter()
                    bone_locals = context.compute_all_bone_locals(world_matrices)
                    local_elapsed += time.perf_counter() - local_start

                append_start = time.perf_counter()
                baked_frames.append(float(maya_time))
                bake_times.append(om.MTime(float(maya_time), om.MTime.uiUnit()))
                context.append_bone_locals_to_channel_arrays(
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
        context.set_anim_layer(runtime_anim_layer)

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


def collect_runtime_bake_cache(converter_or_context, instance, clip, bake_samples) -> RuntimeBakeCache:
    """Evaluate runtime frames and collect Maya channel arrays without scene writes."""
    context = _resolve_runtime_cache_collect_context(converter_or_context)
    return _collect_runtime_bake_cache(context, instance, clip, bake_samples)