"""Runtime bake cache collection helpers for VMD conversion."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
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
    # Routing outcome for native physics bake (opt-in). Empty when not requested.
    physics_bake: Dict[str, Any] = field(default_factory=dict)


def _is_uniform_step(values: List[float], *, eps: float = 1e-9) -> bool:
    """Return True when successive samples share a single positive step."""
    if len(values) < 2:
        return True
    step = float(values[1]) - float(values[0])
    if not math.isfinite(step) or step <= 0.0:
        return False
    for index in range(2, len(values)):
        current = float(values[index]) - float(values[index - 1])
        if not math.isfinite(current) or abs(current - step) > eps:
            return False
    return True


def _physics_dt_and_frame_step(
    bake_samples,
    fps: float,
) -> Optional[Tuple[float, float, float]]:
    """Return (start_vmd_frame, vmd_frame_step, dt_seconds) for uniform samples.

    ``dt_seconds`` is derived from adjacent Maya output times divided by scene
    FPS (for the common uniform case ``maya_step / fps``, e.g. 1/60 at 60fps),
    never from ``vmd_frame_step / fps`` alone. At 60fps output with VMD
    ``frame_step=0.5``, this yields ``dt_seconds=1/60``.
    """
    if not bake_samples:
        return None
    maya_times = [float(sample[0]) for sample in bake_samples]
    vmd_frames = [float(sample[1]) for sample in bake_samples]
    if not _is_uniform_step(maya_times) or not _is_uniform_step(vmd_frames):
        return None
    fps_value = float(fps)
    if not math.isfinite(fps_value) or fps_value <= 0.0:
        return None
    if len(bake_samples) == 1:
        return float(vmd_frames[0]), 1.0, 1.0 / fps_value
    maya_step = float(maya_times[1]) - float(maya_times[0])
    vmd_step = float(vmd_frames[1]) - float(vmd_frames[0])
    if maya_step <= 0.0 or vmd_step <= 0.0:
        return None
    return float(vmd_frames[0]), vmd_step, maya_step / fps_value


def _try_native_physics_batch(
    context: VmdRuntimeCacheCollectContext,
    physics_world,
    instance,
    clip,
    bake_samples,
    fps: float,
) -> Tuple[Any, Dict[str, Any]]:
    """Attempt sequential native physics bake; return (batch_result_or_None, routing)."""
    routing: Dict[str, Any] = {
        "requested": True,
        "used": False,
        "reason": "",
        "frame_count": len(bake_samples) if bake_samples else 0,
        "fps": float(fps),
    }
    if physics_world is None:
        routing["reason"] = "physics_world_unavailable"
        return None, routing
    if not bake_samples:
        routing["reason"] = "empty_bake_samples"
        return None, routing

    resolved = _physics_dt_and_frame_step(bake_samples, fps)
    if resolved is None:
        routing["reason"] = "non_uniform_or_invalid_sampling"
        context.logger.warning(
            "Native physics bake skipped: non-uniform or invalid Maya/VMD sampling "
            "(fps=%s, samples=%s); falling back to non-physics runtime batch",
            fps,
            len(bake_samples),
        )
        return None, routing

    start_frame, frame_step, dt_seconds = resolved
    routing["start_frame"] = start_frame
    routing["frame_step"] = frame_step
    routing["dt_seconds"] = dt_seconds
    context.logger.info(
        "Attempting native physics bake "
        f"(start={start_frame}, frame_step={frame_step}, dt_seconds={dt_seconds}, "
        f"frames={len(bake_samples)}, fps={fps:g})"
    )
    try:
        batch_result = physics_world.bake_clip_frames_with_physics(
            instance,
            clip,
            start_frame,
            frame_step,
            len(bake_samples),
            dt_seconds,
        )
    except Exception as exc:
        routing["reason"] = f"physics_bake_exception:{exc}"
        context.logger.warning(
            "Native physics bake raised; falling back to non-physics runtime batch: %s",
            exc,
            exc_info=True,
        )
        return None, routing

    if batch_result is None:
        routing["reason"] = "physics_bake_failed_or_unsupported"
        context.logger.warning(
            "Native physics bake returned failure/unsupported "
            "(start=%s, frame_step=%s, dt_seconds=%s, frames=%s); "
            "falling back to non-physics runtime batch",
            start_frame,
            frame_step,
            dt_seconds,
            len(bake_samples),
        )
        return None, routing

    routing["used"] = True
    routing["reason"] = "ok"
    routing["bone_count"] = int(getattr(batch_result, "bone_count", 0) or 0)
    routing["morph_count"] = int(getattr(batch_result, "morph_count", 0) or 0)
    context.logger.info(
        "Using mmd-anim native physics bake "
        f"(frames={batch_result.frame_count}, bones={batch_result.bone_count}, "
        f"morphs={batch_result.morph_count}, dt_seconds={dt_seconds}, frame_step={frame_step})"
    )
    return batch_result, routing


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
    *,
    physics_world=None,
    fps: float = 30.0,
    use_native_physics_bake: bool = False,
) -> RuntimeBakeCache:
    """Evaluate runtime frames and collect Maya channel arrays without scene writes.

    When ``use_native_physics_bake`` is True and ``physics_world`` is provided,
    prefer sequential ``bake_clip_frames_with_physics`` and reuse the same
    batch-result → channel-array pipeline. On feature/world/sample/bake failure
    fall back to the existing non-physics ``evaluate_clip_frame_batch`` path.
    """
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
    physics_bake: Dict[str, Any] = {"requested": bool(use_native_physics_bake), "used": False}

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
            if use_native_physics_bake:
                batch_result, physics_bake = _try_native_physics_batch(
                    context,
                    physics_world,
                    instance,
                    clip,
                    bake_samples,
                    fps,
                )
            if batch_result is None:
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
            if not physics_bake.get("used"):
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
                context.logger.debug("mmd-anim runtime batch evaluation unavailable; using per-frame ABI")
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
        physics_bake=physics_bake,
    )


def collect_runtime_bake_cache(
    converter_or_context,
    instance,
    clip,
    bake_samples,
    *,
    physics_world=None,
    fps: float = 30.0,
    use_native_physics_bake: bool = False,
) -> RuntimeBakeCache:
    """Evaluate runtime frames and collect Maya channel arrays without scene writes."""
    context = _resolve_runtime_cache_collect_context(converter_or_context)
    return _collect_runtime_bake_cache(
        context,
        instance,
        clip,
        bake_samples,
        physics_world=physics_world,
        fps=fps,
        use_native_physics_bake=use_native_physics_bake,
    )