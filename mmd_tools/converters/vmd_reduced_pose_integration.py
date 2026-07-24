"""Runtime-bake orchestration for opt-in reduced Maya animation keys.

This module is deliberately a narrow integration boundary.  It reuses the
exact dense arrays and runtime batch collected by ``vmd_runtime_cache_collect``
and prepares explicit scene routes before calling the transactional authoring
boundary.  Any unsupported route returns an error without mutating the scene;
the opt-in reduction path does not retry with a dense bake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from mmd_tools.converters.vmd_reduced_pose_adapter import (
    ReducedPoseChannelPlanOutcome,
    build_reduced_pose_channel_plan,
)
from mmd_tools.converters.vmd_reduced_pose_scene import (
    ReducedPoseSceneAuthoringOutcome,
    author_reduced_pose_channel_plan,
)


@dataclass(frozen=True)
class ReducedPoseIntegrationOutcome:
    """Outcome consumed by ``VmdConverter`` to accept or reject sparse apply."""

    success: bool
    reason: Optional[str] = None
    plan: Optional[ReducedPoseChannelPlanOutcome] = None
    authoring: Optional[ReducedPoseSceneAuthoringOutcome] = None
    route_count: int = 0
    morph_fanout_count: int = 0


def _failure(reason: str, plan=None, authoring=None) -> ReducedPoseIntegrationOutcome:
    return ReducedPoseIntegrationOutcome(False, reason, plan, authoring)


def _copy_channels(channels: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for joint, values in channels.items():
        result[str(joint)] = {
            str(attr): None if array is None else [float(value) for value in array]
            for attr, array in values.items()
        }
    return result


def _copy_static(static_state: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> Dict[str, Dict[str, dict]]:
    return {
        str(joint): {str(attr): dict(state) for attr, state in states.items()}
        for joint, states in static_state.items()
    }


def _static_target_is_settable(target: str) -> bool:
    """Return true only when Maya explicitly reports a static plug settable.

    Dense runtime bake skips static writes when Maya reports a locked or
    incoming/non-settable plug.  Reduced authoring follows that parity rule;
    no connection is enumerated or modified here.
    """
    try:
        import maya.cmds as cmds

        value = cmds.getAttr(target, settable=True)
        return value is True
    except Exception:
        return False


def prepare_reduced_pose_inputs(
    converter: Any,
    runtime_cache: Any,
    reduced_pose: Any,
    pmx_morph_names: Sequence[str],
    *,
    translate_tolerance: float = 5.0e-4,
    rotate_tolerance_radians: float = 1.0e-3,
    morph_tolerance: float = 1.0e-3,
) -> Tuple[
    Optional[ReducedPoseChannelPlanOutcome],
    Dict[Tuple[int, str], Any],
    Dict[int, Tuple[str, ...]],
    int,
    Optional[str],
]:
    """Build reduced scalar plans and explicit scene target maps pre-mutation."""
    if getattr(runtime_cache, "dense_batch_result", None) is None:
        return None, {}, {}, 0, "dense runtime batch is unavailable for reduction"
    if getattr(converter, "anim_layer", None):
        return None, {}, {}, 0, "active animation layer is unsupported by sparse authoring"
    dense_values = _copy_channels(runtime_cache.joint_channel_values)
    dense_static = _copy_static(runtime_cache.joint_channel_static)

    plan = build_reduced_pose_channel_plan(
        reduced_pose,
        runtime_cache.baked_frames,
        converter.bone_index_to_joint,
        dense_values,
        dense_static,
        runtime_cache.morph_cache,
        translate_tolerance=translate_tolerance,
        rotate_tolerance_radians=rotate_tolerance_radians,
        morph_tolerance=morph_tolerance,
    )
    if not plan.success:
        return None, {}, {}, 0, plan.failure_reason or "reduced pose adapter failed"

    bone_targets: Dict[Tuple[int, str], Any] = {}
    for curve in plan.curves:
        if curve.owner_kind != "bone":
            continue
        target_joint = converter.bone_index_to_joint.get(curve.owner_index)
        if not target_joint:
            return None, {}, {}, 0, f"missing joint target for bone index {curve.owner_index}"
        target = f"{target_joint}.{curve.channel}"
        bone_targets[(curve.owner_index, curve.channel)] = target

    morph_targets: Dict[int, Tuple[str, ...]] = {}
    fanout_count = 0
    for curve in plan.curves:
        if curve.owner_kind != "morph":
            continue
        if curve.owner_index < 0 or curve.owner_index >= len(pmx_morph_names):
            return None, {}, {}, fanout_count, f"morph index {curve.owner_index} is outside PMX names"
        morph_name = pmx_morph_names[curve.owner_index]
        targets = []
        for mapping in converter._iter_morph_mappings(converter.morph_name_mapping.get(morph_name)):
            try:
                node, attr, _ = mapping
            except (TypeError, ValueError):
                return None, {}, {}, fanout_count, f"morph {morph_name!r} mapping is malformed"
            targets.append(f"{node}.{attr}")
        morph_targets[curve.owner_index] = tuple(targets)
        fanout_count += max(0, len(targets) - 1)
    return plan, bone_targets, morph_targets, fanout_count, None


def author_reduced_pose_from_runtime_cache(
    converter: Any,
    runtime_cache: Any,
    reduced_pose: Any,
    pmx_morph_names: Sequence[str],
    *,
    translate_tolerance: float = 5.0e-4,
    rotate_tolerance_radians: float = 1.0e-3,
    morph_tolerance: float = 1.0e-3,
) -> ReducedPoseIntegrationOutcome:
    """Prepare and transactionally author a reduced runtime bake."""
    try:
        plan, bone_targets, morph_targets, fanout_count, reason = prepare_reduced_pose_inputs(
            converter,
            runtime_cache,
            reduced_pose,
            pmx_morph_names,
            translate_tolerance=translate_tolerance,
            rotate_tolerance_radians=rotate_tolerance_radians,
            morph_tolerance=morph_tolerance,
        )
    except Exception as exc:
        return _failure(f"reduced input preparation failed: {exc}")
    if plan is None:
        return _failure(reason or "reduced input preparation failed")
    static_values: Dict[str, float] = {}
    for target_joint, states in _copy_static(runtime_cache.joint_channel_static).items():
        for channel, state in states.items():
            if isinstance(state, Mapping) and state.get("is_static") is True and state.get("first") is not None:
                target = f"{target_joint}.{channel}"
                if _static_target_is_settable(target):
                    static_values[target] = float(state["first"])
    authoring = author_reduced_pose_channel_plan(
        plan,
        bone_targets,
        morph_targets,
        static_values=static_values,
    )
    if not authoring.success:
        return _failure(authoring.failure_reason or "reduced scene authoring failed", plan, authoring)
    route_count = sum(1 for curve in plan.curves if curve.owner_kind == "bone") + sum(
        len(targets) for targets in morph_targets.values()
    )
    return ReducedPoseIntegrationOutcome(True, None, plan, authoring, route_count, fanout_count)


__all__ = [
    "ReducedPoseIntegrationOutcome",
    "author_reduced_pose_from_runtime_cache",
    "prepare_reduced_pose_inputs",
]
