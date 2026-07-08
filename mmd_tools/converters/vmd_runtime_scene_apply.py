"""Runtime bake scene-application helpers for VMD conversion."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Union

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .vmd_context import VmdRuntimeSceneApplyContext


def _resolve_runtime_scene_apply_context(
    converter_or_context: Union[Any, VmdRuntimeSceneApplyContext],
) -> VmdRuntimeSceneApplyContext:
    if isinstance(converter_or_context, VmdRuntimeSceneApplyContext):
        return converter_or_context
    factory = getattr(converter_or_context, "_runtime_scene_apply_context", None)
    if callable(factory):
        return factory()
    apply_runtime_channel_arrays = getattr(
        converter_or_context,
        "_apply_runtime_channel_arrays_to_scene",
        None,
    )
    if not callable(apply_runtime_channel_arrays):
        def apply_runtime_channel_arrays(*args):
            return apply_runtime_channel_arrays_to_scene(converter_or_context, *args)
    noop_logger = SimpleNamespace(info=lambda *_args, **_kwargs: None, debug=lambda *_args, **_kwargs: None)
    return VmdRuntimeSceneApplyContext(
        logger=getattr(converter_or_context, "logger", noop_logger),
        outer_refresh_suspended=bool(getattr(converter_or_context, "_vmd_import_refresh_suspended", False)),
        collect_append_info=getattr(converter_or_context, "_collect_append_info", lambda: {}),
        collect_mmd_ik_passthrough_info=getattr(
            converter_or_context,
            "_collect_mmd_ik_passthrough_info",
            lambda: {},
        ),
        decompose_append_rotations_for_scene=getattr(
            converter_or_context,
            "_decompose_append_rotations_for_scene",
            lambda *_args: {},
        ),
        decompose_append_translations_for_scene=getattr(
            converter_or_context,
            "_decompose_append_translations_for_scene",
            lambda *_args: {},
        ),
        key_mmd_ik_passthrough_rotation=getattr(
            converter_or_context,
            "_key_mmd_ik_passthrough_rotation",
            lambda *_args, **_kwargs: 0,
        ),
        batch_create_and_key_curve_arrays=getattr(
            converter_or_context,
            "_batch_create_and_key_curve_arrays",
            lambda *_args: (0, 0),
        ),
        bake_morph_weight_cache_from_runtime=getattr(
            converter_or_context,
            "_bake_morph_weight_cache_from_runtime",
            lambda *_args: None,
        ),
        apply_runtime_channel_arrays=apply_runtime_channel_arrays,
    )


def apply_runtime_channel_arrays_to_scene(
    converter_or_context,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    bake_times: om.MTimeArray,
    baked_frames: List[float],
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: List[str],
) -> None:
    """Apply collected runtime bake channel arrays to the Maya scene."""
    context = _resolve_runtime_scene_apply_context(converter_or_context)
    _apply_runtime_channel_arrays_to_scene(
        context,
        joint_channel_values,
        joint_channel_static,
        bake_times,
        baked_frames,
        morph_cache,
        pmx_morph_names,
    )


def _apply_runtime_channel_arrays_to_scene(
    context: VmdRuntimeSceneApplyContext,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    bake_times: om.MTimeArray,
    baked_frames: List[float],
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: List[str],
) -> None:
    """Apply collected runtime bake channel arrays to the Maya scene."""
    keyed_channels = 0
    skipped_static_channels = 0
    total_channels = 0

    append_info = context.collect_append_info()
    ik_passthrough_info = context.collect_mmd_ik_passthrough_info()
    decomposed_rotations = context.decompose_append_rotations_for_scene(
        joint_channel_values,
        joint_channel_static,
        append_info,
        len(baked_frames),
    )
    decomposed_translations = context.decompose_append_translations_for_scene(
        joint_channel_values,
        joint_channel_static,
        append_info,
        len(baked_frames),
    )

    for joint, channels in joint_channel_values.items():
        total_channels += len(channels)
        try:
            ik_info = ik_passthrough_info.get(joint)
            if ik_info:
                channels = dict(channels)
                redirected = context.key_mmd_ik_passthrough_rotation(
                    ik_info,
                    channels,
                    joint_channel_static.get(joint, {}),
                    bake_times,
                    baked_frames,
                )
                if redirected:
                    keyed_channels += redirected
                    for attr in ("rotateX", "rotateY", "rotateZ"):
                        channels.pop(attr, None)
                if not channels:
                    continue

            target_static = joint_channel_static.get(joint, {})
            info = append_info.get(joint)
            if info and info["attr_map"]:
                append_node = info["node"]
                attr_map = dict(info["attr_map"])
                decomposed_rotation_channels = decomposed_rotations.get(joint, {})
                decomposed_translation_channels = decomposed_translations.get(joint, {})
                decomposed_channels = dict(decomposed_rotation_channels)
                decomposed_channels.update(decomposed_translation_channels)

                if info["affect_rotation"] and not decomposed_rotation_channels:
                    attr_map.pop("rotateX", None)
                    attr_map.pop("rotateY", None)
                    attr_map.pop("rotateZ", None)
                if info["affect_translation"] and not decomposed_translation_channels:
                    attr_map.pop("translateX", None)
                    attr_map.pop("translateY", None)
                    attr_map.pop("translateZ", None)

                redirected_channels = {}
                redirected_static = {}
                passthrough_channels = {}
                passthrough_static = {}
                for attr, values in channels.items():
                    new_attr = attr_map.get(attr)
                    if new_attr:
                        redirected_channels[new_attr] = decomposed_channels.get(attr, values)
                        if attr not in decomposed_channels:
                            orig_state = target_static.get(attr)
                            if orig_state:
                                redirected_static[new_attr] = orig_state
                    else:
                        passthrough_channels[attr] = values
                        orig_state = target_static.get(attr)
                        if orig_state:
                            passthrough_static[attr] = orig_state

                if redirected_channels:
                    keyed, skipped = context.batch_create_and_key_curve_arrays(
                        append_node,
                        redirected_channels,
                        redirected_static,
                        bake_times,
                        baked_frames,
                    )
                    keyed_channels += keyed
                    skipped_static_channels += skipped

                if passthrough_channels:
                    keyed, skipped = context.batch_create_and_key_curve_arrays(
                        joint,
                        passthrough_channels,
                        passthrough_static,
                        bake_times,
                        baked_frames,
                    )
                    keyed_channels += keyed
                    skipped_static_channels += skipped

                if redirected_channels or passthrough_channels:
                    continue

            keyed, skipped = context.batch_create_and_key_curve_arrays(
                joint,
                channels,
                target_static,
                bake_times,
                baked_frames,
            )
            keyed_channels += keyed
            skipped_static_channels += skipped
        except Exception as e:
            context.logger.debug(f"batch array keying error for {joint}: {e}")
            raise

    context.logger.info(
        "runtime joint channel pruning: "
        f"keyed={keyed_channels}, skipped_static={skipped_static_channels}, "
        f"total={total_channels}"
    )

    context.bake_morph_weight_cache_from_runtime(morph_cache, pmx_morph_names)

    context.logger.info(f"Applied runtime cache: keyed {len(baked_frames)} frames")


def apply_runtime_channel_arrays_to_scene_with_undo_disabled(
    converter_or_context,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    bake_times: om.MTimeArray,
    baked_frames: List[float],
    morph_cache: List[Tuple[float, list]],
    pmx_morph_names: List[str],
) -> None:
    """Apply runtime channels while suppressing Maya undo recording."""
    context = _resolve_runtime_scene_apply_context(converter_or_context)
    undo_was_enabled = True
    refresh_suspended = False
    try:
        undo_was_enabled = bool(cmds.undoInfo(q=True, state=True))
    except Exception:
        undo_was_enabled = True
    try:
        cmds.undoInfo(stateWithoutFlush=False)
    except Exception:
        pass
    if not context.outer_refresh_suspended:
        try:
            cmds.refresh(suspend=True)
            refresh_suspended = True
        except Exception:
            refresh_suspended = False
    try:
        if context.apply_runtime_channel_arrays is not None:
            context.apply_runtime_channel_arrays(
                joint_channel_values,
                joint_channel_static,
                bake_times,
                baked_frames,
                morph_cache,
                pmx_morph_names,
            )
        else:
            _apply_runtime_channel_arrays_to_scene(
                context,
                joint_channel_values,
                joint_channel_static,
                bake_times,
                baked_frames,
                morph_cache,
                pmx_morph_names,
            )
    finally:
        if refresh_suspended:
            try:
                cmds.refresh(suspend=False)
            except Exception:
                pass
        if undo_was_enabled:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass