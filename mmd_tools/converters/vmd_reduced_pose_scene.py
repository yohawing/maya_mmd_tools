"""Transactional Maya animCurve authoring for detached reduced channel plans.

The adapter in :mod:`vmd_reduced_pose_adapter` deliberately stops at scalar
Maya-channel replay.  This module is the next boundary: it accepts a
successful detached plan plus explicit target plugs, creates fixed-Hermite
animCurves without ``cmds.setKeyframe``, and connects them in one DG
transaction.  Any preflight, keying, tangent, or connection failure deletes
all created nodes and returns an atomic rollback outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from mmd_tools.converters.vmd_reduced_pose_adapter import (
    ReducedPoseChannelPlanOutcome,
    ScalarCurvePlan,
)


@dataclass(frozen=True)
class AuthoredReducedCurve:
    """One successfully created and connected Maya animCurve."""

    owner_kind: str
    owner_index: int
    channel: str
    target: str
    curve_name: str
    key_count: int


@dataclass(frozen=True)
class ReducedPoseSceneAuthoringOutcome:
    """Atomic scene-authoring result and rollback evidence."""

    success: bool
    created_curves: Tuple[AuthoredReducedCurve, ...] = ()
    failure_reason: Optional[str] = None
    rolled_back: bool = False

    @property
    def ok(self) -> bool:
        """Alias for callers using conventional outcome naming."""
        return self.success


def _failure(
    reason: str,
    *,
    rolled_back: bool = False,
) -> ReducedPoseSceneAuthoringOutcome:
    return ReducedPoseSceneAuthoringOutcome(False, (), reason, rolled_back)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _maya_modules(maya_api: Any = None) -> Tuple[Any, Any]:
    if maya_api is not None:
        return getattr(maya_api, "om", maya_api), getattr(maya_api, "oma", maya_api)
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma

    return om, oma


def _resolve_target(target: Any, om: Any) -> Any:
    if not isinstance(target, str):
        return target
    selection = om.MSelectionList()
    selection.add(target)
    return selection.getPlug(0)


def _plug_bool(plug: Any, name: str) -> Optional[bool]:
    try:
        value = getattr(plug, name)
        value = value() if callable(value) else value
        return bool(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _plug_label(plug: Any, fallback: str) -> str:
    try:
        value = getattr(plug, "name")
        value = value() if callable(value) else value
        return str(value)
    except Exception:
        return fallback


def _validate_plug_type(plug: Any, expected: str, om: Any) -> Optional[str]:
    """Validate scalar/unit type when the API exposes attribute metadata."""
    marker = getattr(plug, "curve_type", None)
    if marker is not None:
        return None if str(marker) == expected else f"target plug type {marker!r} is not {expected}"
    if getattr(plug, "isCompound", False) or getattr(plug, "isArray", False):
        return "target plug must be a scalar, non-array plug"
    try:
        attribute = plug.attribute()
        if expected == "TA":
            unit_attribute = om.MFnUnitAttribute(attribute)
            if unit_attribute.type() != om.MFn.kUnitAttribute or unit_attribute.unitType() != om.MFnUnitAttribute.kAngle:
                return "rotation target is not angular"
        elif expected == "TL":
            unit_attribute = om.MFnUnitAttribute(attribute)
            if unit_attribute.type() != om.MFn.kUnitAttribute or unit_attribute.unitType() != om.MFnUnitAttribute.kDistance:
                return "translation target is not distance-valued"
        else:
            numeric_attribute = om.MFnNumericAttribute(attribute)
            if numeric_attribute.type() != om.MFn.kNumericAttribute:
                return "morph target is not a numeric scalar attribute"
    except Exception as exc:
        return f"unable to validate target plug unit/type: {exc}"
    return None


def _curve_type(plan: ScalarCurvePlan, target: Any = None) -> str:
    if plan.owner_kind == "morph":
        return "TU"
    target_text = str(target or "")
    if plan.channel.startswith("rotate") or plan.channel.startswith("baseRotate") or ".baseRotate" in target_text:
        return "TA"
    return "TL"


def _curve_constant(oma: Any, curve_type: str) -> Any:
    return getattr(oma.MFnAnimCurve, f"kAnimCurve{curve_type}")


def _queue_static_value(modifier: Any, plug: Any, value: float, om: Any) -> None:
    """Queue a static value using the Maya unit type of ``plug``.

    Runtime static rotation channels are radians.  Passing those values
    through ``newPlugValueDouble`` bypasses Maya's unit wrapper and fails for
    angle/distance plugs (notably the physics bake path).
    """
    try:
        unit_attribute = om.MFnUnitAttribute(plug.attribute())
        unit_type = unit_attribute.unitType()
    except Exception:
        unit_type = None
    if unit_type == getattr(om.MFnUnitAttribute, "kAngle", object()):
        angle_unit = getattr(om.MAngle, "kRadians", None)
        angle = om.MAngle(float(value), angle_unit) if angle_unit is not None else om.MAngle(float(value))
        modifier.newPlugValueMAngle(plug, angle)
    elif unit_type == getattr(om.MFnUnitAttribute, "kDistance", object()):
        distance_unit = getattr(om.MDistance, "uiUnit", lambda: None)()
        distance = (
            om.MDistance(float(value), distance_unit)
            if distance_unit is not None
            else om.MDistance(float(value))
        )
        modifier.newPlugValueMDistance(plug, distance)
    else:
        modifier.newPlugValueDouble(plug, float(value))


def _find_output_plug(curve: Any) -> Any:
    try:
        return curve.findPlug("output", False)
    except TypeError:
        return curve.findPlug("output")


def _delete_nodes(om: Any, nodes: Sequence[Any]) -> None:
    if not nodes:
        return
    modifier = om.MDGModifier()
    for node in nodes:
        modifier.deleteNode(node)
    try:
        modifier.doIt()
    except Exception:
        # An MDGModifier is all-or-nothing from the caller's perspective.  If
        # Maya reports a deletion failure, restore any partial operation before
        # propagating it; the caller must retain the authored scene in that
        # case instead of pretending the rollback completed.
        try:
            modifier.undoIt()
        except Exception:
            pass
        raise


def _validate_plan_and_targets(
    plan_outcome: ReducedPoseChannelPlanOutcome,
    bone_channel_targets: Mapping[Tuple[int, str], Any],
    morph_targets: Mapping[int, Sequence[Any]],
    om: Any,
    active_animation_layer: Optional[str],
    static_values: Mapping[Any, float],
) -> Tuple[
    Optional[List[Tuple[ScalarCurvePlan, Any, str]]],
    Optional[List[Tuple[Any, float]]],
    Optional[str],
]:
    if plan_outcome is None or not plan_outcome.success:
        reason = getattr(plan_outcome, "failure_reason", None) or "channel plan is not successful"
        return None, None, f"reduced channel plan unavailable: {reason}"
    if active_animation_layer:
        return None, None, "active animation layer authoring is unsupported"
    curves = tuple(plan_outcome.curves or ())
    if not curves:
        return None, None, "channel plan contains no curves"
    report = getattr(plan_outcome, "report", None)
    if report is None:
        return None, None, "channel plan report is missing"
    expected_reduced_count = sum(len(curve.keys or ()) for curve in curves)
    if int(getattr(report, "reduced_key_count", -1)) != expected_reduced_count:
        return None, None, "channel plan report/key count mismatch"
    expected_source_count = sum(int(curve.source_key_count) for curve in curves)
    if int(getattr(report, "source_key_count", -1)) != expected_source_count:
        return None, None, "channel plan report/source count mismatch"
    resolved: List[Tuple[ScalarCurvePlan, Any, str]] = []
    seen_targets = set()
    for plan in curves:
        if not isinstance(plan, ScalarCurvePlan):
            return None, None, "channel plan contains an unsupported curve DTO"
        if plan.owner_kind == "bone":
            target_value = bone_channel_targets.get((plan.owner_index, plan.channel))
            target_values = () if target_value is None else (target_value,)
            if not target_values:
                continue
        elif plan.owner_kind == "morph":
            raw_targets = morph_targets.get(plan.owner_index, ())
            target_values = (raw_targets,) if isinstance(raw_targets, str) else tuple(raw_targets)
            if not target_values:
                continue
        else:
            return None, None, f"unsupported curve owner kind: {plan.owner_kind}"
        keys = tuple(plan.keys or ())
        if not keys:
            return None, None, f"no keys for {plan.owner_kind}[{plan.owner_index}].{plan.channel}"
        if any(
            not all(_finite(getattr(key, field, None)) for field in ("maya_time", "value", "in_slope", "out_slope"))
            for key in keys
        ):
            return None, None, f"non-finite key data for {plan.owner_kind}[{plan.owner_index}].{plan.channel}"
        if any(right.maya_time <= left.maya_time for left, right in zip(keys, keys[1:])):
            return None, None, f"key times are not strictly increasing for {plan.owner_kind}[{plan.owner_index}].{plan.channel}"
        if plan.source_key_count < len(keys) or plan.source_key_count <= 0:
            return None, None, f"invalid source/reduced key counts for {plan.owner_kind}[{plan.owner_index}].{plan.channel}"
        for target_value in target_values:
            try:
                plug = _resolve_target(target_value, om)
            except Exception as exc:
                return None, None, f"target resolution failed for {target_value!r}: {exc}"
            is_null = _plug_bool(plug, "isNull")
            if plug is None or is_null is None or is_null:
                return None, None, f"target plug is null for {target_value!r}"
            is_destination = _plug_bool(plug, "isDestination")
            if is_destination is None or is_destination:
                return None, None, f"target plug is not available: {_plug_label(plug, str(target_value))}"
            expected_type = _curve_type(plan, target_value)
            type_reason = _validate_plug_type(plug, expected_type, om)
            if type_reason:
                return None, None, f"{_plug_label(plug, str(target_value))}: {type_reason}"
            target_identity = _plug_label(plug, str(target_value))
            if target_identity in seen_targets:
                return None, None, f"duplicate target plug: {target_identity}"
            seen_targets.add(target_identity)
            resolved.append((plan, plug, expected_type))
    resolved_static: List[Tuple[Any, float]] = []
    for target_value, value in static_values.items():
        try:
            plug = _resolve_target(target_value, om)
        except Exception as exc:
            return None, None, f"static target resolution failed for {target_value!r}: {exc}"
        is_null = _plug_bool(plug, "isNull")
        is_destination = _plug_bool(plug, "isDestination")
        if plug is None or is_null is None or is_null or is_destination is None or is_destination:
            return None, None, f"static target plug is not available: {_plug_label(plug, str(target_value))}"
        free_to_change = getattr(plug, "isFreeToChange", None)
        if callable(free_to_change):
            try:
                free_result = free_to_change()
                free_ok = free_result if isinstance(free_result, bool) else int(free_result) == int(
                    getattr(om.MPlug, "kFreeToChange", 0)
                )
                if not free_ok:
                    return None, None, f"static target plug is locked: {_plug_label(plug, str(target_value))}"
            except Exception as exc:
                return None, None, f"static target free-to-change check failed: {exc}"
        if not _finite(value):
            return None, None, f"static target value is not finite: {target_value!r}"
        resolved_static.append((plug, float(value)))
    return resolved, resolved_static, None


def author_reduced_pose_channel_plan(
    plan_outcome: ReducedPoseChannelPlanOutcome,
    bone_channel_targets: Mapping[Tuple[int, str], Any],
    morph_targets: Mapping[int, Sequence[Any]],
    *,
    maya_api: Any = None,
    active_animation_layer: Optional[str] = None,
    static_values: Optional[Mapping[Any, float]] = None,
) -> ReducedPoseSceneAuthoringOutcome:
    """Create and connect all planned scalar curves atomically.

    ``bone_channel_targets`` is keyed by ``(bone_index, channel)`` and
    ``morph_targets`` by morph index.  Both are explicit plug paths or
    ``MPlug``-compatible objects; morph placeholder text from the plan is never
    resolved implicitly.
    """
    try:
        om, oma = _maya_modules(maya_api)
    except Exception as exc:
        return _failure(f"Maya API unavailable: {exc}")
    try:
        resolved, resolved_static, reason = _validate_plan_and_targets(
            plan_outcome,
            bone_channel_targets,
            morph_targets,
            om,
            active_animation_layer,
            static_values or {},
        )
    except Exception as exc:
        return _failure(f"preflight failed: {exc}")
    if resolved is None or resolved_static is None:
        return _failure(reason or "preflight failed")

    created_nodes: List[Any] = []
    authored: List[AuthoredReducedCurve] = []
    modifier = None
    try:
        for plan, _target_plug, expected_type in resolved:
            curve = oma.MFnAnimCurve()
            node = curve.create(_curve_constant(oma, expected_type))
            created_nodes.append(node)
            times = om.MTimeArray()
            values = om.MDoubleArray()
            for key in plan.keys:
                times.append(om.MTime(float(key.maya_time), om.MTime.uiUnit()))
                values.append(float(key.value))
            curve.addKeys(
                times,
                values,
                oma.MFnAnimCurve.kTangentFixed,
                oma.MFnAnimCurve.kTangentFixed,
                False,
            )
            for index, key in enumerate(plan.keys):
                curve.setTangentsLocked(index, False)
                curve.setInTangentType(index, oma.MFnAnimCurve.kTangentFixed)
                curve.setOutTangentType(index, oma.MFnAnimCurve.kTangentFixed)
                # Adapter slopes are radians per Maya frame for angle
                # channels.  With convertUnits=True Maya expects the angle
                # component in the current UI angle unit (degrees), while
                # translation/morph curves remain in their native scalar
                # units.
                slope_in = float(key.in_slope)
                slope_out = float(key.out_slope)
                if expected_type == "TA":
                    slope_in = math.degrees(slope_in)
                    slope_out = math.degrees(slope_out)
                curve.setTangent(index, 1.0, slope_in, True, convertUnits=True)
                curve.setTangent(index, 1.0, slope_out, False, convertUnits=True)
            authored.append(
                AuthoredReducedCurve(
                    plan.owner_kind,
                    plan.owner_index,
                    plan.channel,
                    _plug_label(_target_plug, plan.target),
                    str(curve.name()),
                    len(plan.keys),
                )
            )

        modifier = om.MDGModifier()
        for target_plug, value in resolved_static:
            _queue_static_value(modifier, target_plug, value, om)
        for (_plan, target_plug, _expected_type), node in zip(resolved, created_nodes):
            curve = oma.MFnAnimCurve(node)
            modifier.connect(_find_output_plug(curve), target_plug)
        modifier.doIt()
        return ReducedPoseSceneAuthoringOutcome(
            True,
            tuple(authored),
            None,
            False,
        )
    except Exception as exc:
        rollback_errors = []
        try:
            if modifier is not None:
                modifier.undoIt()
        except Exception as rollback_exc:
            rollback_errors.append(f"DG undo failed: {rollback_exc}")
        try:
            _delete_nodes(om, created_nodes)
        except Exception as cleanup_exc:
            rollback_errors.append(f"node cleanup failed: {cleanup_exc}")
        if rollback_errors:
            return _failure(
                f"authoring failed: {exc}; {'; '.join(rollback_errors)}",
                rolled_back=False,
            )
        return _failure(
            f"authoring failed and was rolled back: {exc}",
            rolled_back=True,
        )


__all__ = [
    "AuthoredReducedCurve",
    "ReducedPoseSceneAuthoringOutcome",
    "author_reduced_pose_channel_plan",
]
