"""Pure Maya-channel refit planning for generic reduced pose tracks.

This module deliberately has no Maya dependency and does not author scene
curves.  It consumes a detached ``MmdRuntimeReducedPoseResult`` together with
the already-collected dense Maya local channel cache, then returns either an
atomic sparse scalar-channel plan or an explicit failure reason.  Generic
Euler segment diagnostics are never read or
treated as Maya rotations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL,
    MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT,
    MmdRuntimeReducedPoseResult,
)


_BONE_CHANNELS = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)
_TRANSLATE_CHANNELS = set(_BONE_CHANNELS[:3])
_ROTATE_CHANNELS = set(_BONE_CHANNELS[3:])
_TAU = 2.0 * math.pi


@dataclass(frozen=True)
class ScalarKeyPlan:
    """One detached Maya-time scalar key with fixed-tangent slopes."""

    maya_time: float
    value: float
    in_slope: float
    out_slope: float


@dataclass(frozen=True)
class ScalarCurvePlan:
    """Sparse plan for one Maya scalar channel."""

    owner_kind: str
    owner_index: int
    target: str
    channel: str
    keys: Tuple[ScalarKeyPlan, ...]
    source_key_count: int
    max_error: float

    @property
    def reduced_key_count(self) -> int:
        """Number of keys that the later Maya authoring step should create."""
        return len(self.keys)


@dataclass(frozen=True)
class ReducedPoseChannelReport:
    """Aggregate refit evidence for all planned scalar channels."""

    source_key_count: int
    reduced_key_count: int
    reduction_ratio: float
    max_translate_error: float
    max_rotate_error_radians: float
    max_morph_error: float
    curve_reports: Tuple[ScalarCurvePlan, ...]


@dataclass(frozen=True)
class ReducedPoseChannelPlanOutcome:
    """Atomic success/failure envelope for adapter callers."""

    success: bool
    curves: Tuple[ScalarCurvePlan, ...] = ()
    report: Optional[ReducedPoseChannelReport] = None
    failure_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Alias useful to callers that use conventional result naming."""
        return self.success


def _failure(reason: str) -> ReducedPoseChannelPlanOutcome:
    return ReducedPoseChannelPlanOutcome(False, (), None, reason)


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _finite_nonnegative(value: Any) -> Optional[float]:
    number = _finite_float(value)
    return number if number is not None and number >= 0.0 else None


def _integral(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == value else None


def _finite_sequence(values: Any, expected_count: int) -> Optional[List[float]]:
    if values is None:
        return None
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(result) != expected_count or not all(math.isfinite(value) for value in result):
        return None
    return result


def _unwrap_radians(values: Sequence[float]) -> List[float]:
    """Unwrap dense Maya radians without consulting runtime Euler diagnostics."""
    if not values:
        return []
    result = [float(values[0])]
    for value in values[1:]:
        candidate = float(value)
        previous = result[-1]
        while candidate - previous > math.pi:
            candidate -= _TAU
        while candidate - previous < -math.pi:
            candidate += _TAU
        result.append(candidate)
    return result


def _hermite_value(left: ScalarKeyPlan, right: ScalarKeyPlan, time: float) -> float:
    duration = right.maya_time - left.maya_time
    if duration <= 0.0:
        return left.value
    u = (time - left.maya_time) / duration
    u2 = u * u
    u3 = u2 * u
    h00 = 2.0 * u3 - 3.0 * u2 + 1.0
    h10 = u3 - 2.0 * u2 + u
    h01 = -2.0 * u3 + 3.0 * u2
    h11 = u3 - u2
    return h00 * left.value + h10 * duration * left.out_slope + h01 * right.value + h11 * duration * right.in_slope


def _make_keys(times: Sequence[float], values: Sequence[float], indices: Sequence[int]) -> Tuple[ScalarKeyPlan, ...]:
    ordered = sorted(set(int(index) for index in indices))
    keys: List[ScalarKeyPlan] = []
    for sample_index in ordered:
        # Tangents are estimated from the dense sample neighbourhood, not the
        # distance to the next selected sparse key.  A sparse chord can span a
        # sharp local bend and force unnecessary refinement even when the
        # dense curve is smooth.  Endpoints use one-sided dense differences;
        # internal samples use the immediate centred derivative.
        if len(times) <= 1:
            slope = 0.0
        elif sample_index <= 0:
            delta_time = times[1] - times[0]
            slope = (values[1] - values[0]) / delta_time
        elif sample_index >= len(times) - 1:
            delta_time = times[-1] - times[-2]
            slope = (values[-1] - values[-2]) / delta_time
        else:
            delta_time = times[sample_index + 1] - times[sample_index - 1]
            slope = (values[sample_index + 1] - values[sample_index - 1]) / delta_time
        keys.append(ScalarKeyPlan(times[sample_index], values[sample_index], slope, slope))
    return tuple(keys)


def _replay_error(
    times: Sequence[float],
    values: Sequence[float],
    indices: Sequence[int],
    keys: Sequence[ScalarKeyPlan],
    tolerance: float,
    seed_hints: Optional[Sequence[int]] = None,
) -> Tuple[float, List[int]]:
    """Return max replay error and one worst sample per violating segment.

    ``indices`` partitions the dense samples into current Hermite segments.
    A linear sweep evaluates each interior sample exactly once and retains at
    most the largest-error sample from each segment for the next refinement
    pass.  When a native seed in the segment is violating, its largest error
    is preferred as the candidate; otherwise the segment-wide worst sample is
    selected.  Keeping every violating sample causes dense clips to become
    nearly dense after a single iteration even when one inserted key resolves
    the neighbouring errors.
    """
    ordered_indices = sorted(set(int(index) for index in indices))
    if len(ordered_indices) != len(keys):
        return math.inf, []
    native_seed_set = {int(index) for index in (seed_hints or ())}
    candidates: List[int] = []
    max_error = 0.0
    for segment_position, (left_index, right_index) in enumerate(zip(ordered_indices, ordered_indices[1:])):
        segment_error = 0.0
        segment_sample = None
        seed_error = 0.0
        seed_sample = None
        left_key = keys[segment_position]
        right_key = keys[segment_position + 1]
        for sample_index in range(left_index + 1, right_index):
            error = abs(_hermite_value(left_key, right_key, times[sample_index]) - values[sample_index])
            if error > segment_error:
                segment_error = error
                segment_sample = sample_index
            if sample_index in native_seed_set and error > seed_error:
                seed_error = error
                seed_sample = sample_index
            if error > max_error:
                max_error = error
        if seed_sample is not None and seed_error > tolerance:
            candidates.append(seed_sample)
        elif segment_sample is not None and segment_error > tolerance:
            candidates.append(segment_sample)
    return max_error, candidates


def _fit_scalar(
    times: Sequence[float],
    values: Sequence[float],
    seed_indices: Iterable[int],
    tolerance: float,
    *,
    max_iterations: int,
) -> Tuple[Optional[Tuple[ScalarKeyPlan, ...]], float, Optional[str]]:
    frame_count = len(times)
    # Native sample indices are hints from the generic reducer, not mandatory
    # authored keys.  Validate their range for atomic input handling, then let
    # dense replay choose only samples required by the scalar tolerance;
    # violating hints get priority within their current segment.
    try:
        seed_hints = {int(index) for index in seed_indices}
    except (TypeError, ValueError, OverflowError):
        return None, 0.0, "generic curve sample_index is malformed"
    if any(index < 0 or index >= frame_count for index in seed_hints):
        return None, 0.0, "generic curve sample_index is outside the dense frame range"
    indices = {0, frame_count - 1}

    for _iteration in range(max_iterations + 1):
        keys = _make_keys(times, values, sorted(indices))
        max_error, refinement_candidates = _replay_error(
            times,
            values,
            sorted(indices),
            keys,
            tolerance,
            seed_hints=tuple(seed_hints),
        )
        if max_error <= tolerance:
            return keys, max_error, None
        additions = set(refinement_candidates) - indices
        if not additions:
            return None, max_error, "scalar replay exceeded tolerance without a refinable sample"
        indices.update(additions)
        if len(indices) >= frame_count:
            keys = _make_keys(times, values, sorted(indices))
            return keys, 0.0, None
    return None, max_error, "scalar refit iteration bound exceeded"


def _validate_times(baked_frames: Sequence[Any]) -> Optional[List[float]]:
    try:
        times = [float(value) for value in baked_frames]
    except (TypeError, ValueError, OverflowError):
        return None
    if not times or not all(math.isfinite(value) for value in times):
        return None
    if any(right <= left for left, right in zip(times, times[1:])):
        return None
    return times


def _curve_seed_indices(curves: Sequence[Any], expected_kind: int, expected_index: int) -> Optional[List[int]]:
    matches = []
    try:
        for curve in curves:
            descriptor = getattr(curve, "descriptor", None)
            if descriptor is None:
                return None
            if int(getattr(descriptor, "kind", -1)) == expected_kind and int(getattr(descriptor, "target_index", -1)) == expected_index:
                matches.append(curve)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None
    if len(matches) != 1:
        return None
    keys = getattr(matches[0], "keys", None)
    if not keys:
        return None
    indices = []
    try:
        for key in keys:
            raw_index = getattr(key, "sample_index")
            if isinstance(raw_index, bool) or int(raw_index) != raw_index:
                return None
            indices.append(int(raw_index))
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None
    if any(index < 0 for index in indices) or any(right <= left for left, right in zip(indices, indices[1:])):
        return None
    return indices


def _materialize_joint_values(
    bone_index_to_joint: Mapping[int, str],
    dense_values: Mapping[str, Mapping[str, Any]],
    static_state: Mapping[str, Mapping[str, Mapping[str, Any]]],
    frame_count: int,
) -> Optional[Dict[int, Dict[str, List[float]]]]:
    result: Dict[int, Dict[str, List[float]]] = {}
    for bone_index in range(len(bone_index_to_joint)):
        if bone_index not in bone_index_to_joint:
            return None
        joint = bone_index_to_joint[bone_index]
        try:
            channels = dense_values.get(joint)
            states = static_state.get(joint)
        except AttributeError:
            return None
        if not isinstance(channels, Mapping) or not isinstance(states, Mapping):
            return None
        materialized: Dict[str, List[float]] = {}
        for channel in _BONE_CHANNELS:
            raw = channels.get(channel)
            if raw is not None:
                values = _finite_sequence(raw, frame_count)
                if values is None:
                    return None
            else:
                state = states.get(channel)
                first = _finite_float(state.get("first")) if isinstance(state, Mapping) else None
                if first is None or not isinstance(state, Mapping) or state.get("is_static") is not True:
                    return None
                count = state.get("count")
                if count is not None:
                    try:
                        if int(count) != count or int(count) != frame_count:
                            return None
                    except (TypeError, ValueError, OverflowError):
                        return None
                values = [first] * frame_count
            materialized[channel] = _unwrap_radians(values) if channel in _ROTATE_CHANNELS else values
        result[bone_index] = materialized
    return result


def build_reduced_pose_channel_plan(
    reduced_pose: MmdRuntimeReducedPoseResult,
    baked_frames: Sequence[Any],
    bone_index_to_joint: Mapping[int, str],
    dense_joint_channel_values: Mapping[str, Mapping[str, Any]],
    dense_joint_channel_static: Mapping[str, Mapping[str, Mapping[str, Any]]],
    dense_morph_cache: Sequence[Tuple[Any, Sequence[Any]]],
    *,
    translate_tolerance: float = 5.0e-4,
    rotate_tolerance_radians: float = 1.0e-3,
    morph_tolerance: float = 1.0e-3,
    max_iterations: Optional[int] = None,
) -> ReducedPoseChannelPlanOutcome:
    """Build a detached, replay-validated sparse Maya scalar channel plan.

    The return is atomic: failures contain no partial curves and a reason
    suitable for transactional scene authoring.  ``reduced_pose`` only
    supplies native sample-index hints only; dense replay may omit those
    samples when the scalar tolerance does not require them.  All values and
    slopes are computed from the dense Maya channels.  Runtime diagnostic
    Euler fields are intentionally ignored.
    """
    if reduced_pose is None:
        return _failure("reduced_pose is missing")
    times = _validate_times(baked_frames)
    if times is None:
        return _failure("baked_frames must be finite and strictly increasing")
    frame_count = len(times)
    info = getattr(reduced_pose, "info", None)
    curves = tuple(getattr(reduced_pose, "curves", ()) or ())
    if info is None:
        return _failure("reduced_pose.info is missing")
    try:
        bone_count = _integral(info.bone_count)
        morph_count = _integral(info.morph_count)
        pose_frame_count = _integral(info.frame_count)
    except (AttributeError, TypeError, ValueError):
        return _failure("reduced_pose.info counts are malformed")
    if pose_frame_count is None or bone_count is None or morph_count is None:
        return _failure("reduced_pose.info counts are not integral")
    if pose_frame_count != frame_count or bone_count <= 0 or morph_count < 0:
        return _failure("reduced pose counts do not match dense cache")
    try:
        bone_indices = {int(index) for index in bone_index_to_joint}
    except (TypeError, ValueError, OverflowError):
        return _failure("bone_index_to_joint indices are malformed")
    if bone_indices != set(range(bone_count)):
        return _failure("bone_index_to_joint must contain every runtime bone index")
    tolerances = (
        _finite_nonnegative(translate_tolerance),
        _finite_nonnegative(rotate_tolerance_radians),
        _finite_nonnegative(morph_tolerance),
    )
    if any(value is None for value in tolerances):
        return _failure("scalar tolerances must be finite and non-negative")
    materialized = _materialize_joint_values(
        bone_index_to_joint,
        dense_joint_channel_values,
        dense_joint_channel_static,
        frame_count,
    )
    if materialized is None:
        return _failure("dense joint channel arrays/static state are malformed")
    try:
        morph_times = [float(item[0]) for item in dense_morph_cache]
        morph_values = [list(item[1]) for item in dense_morph_cache]
    except (TypeError, ValueError, IndexError):
        return _failure("dense morph cache is malformed")
    if (
        len(morph_values) != frame_count
        or len(morph_times) != frame_count
        or not all(math.isfinite(value) for value in morph_times)
        or any(abs(morph_time - time) > 1.0e-9 for morph_time, time in zip(morph_times, times))
    ):
        return _failure("dense morph cache times do not match baked_frames")
    morph_dense: List[List[float]] = []
    for weights in morph_values:
        converted = _finite_sequence(weights, morph_count)
        if converted is None:
            return _failure("dense morph weights are malformed")
        morph_dense.append(converted)

    iteration_bound = max_iterations if max_iterations is not None else max(8, frame_count * 2)
    try:
        iteration_bound_int = int(iteration_bound)
    except (TypeError, ValueError, OverflowError):
        return _failure("max_iterations must be a positive integer")
    if isinstance(iteration_bound, bool) or iteration_bound_int != iteration_bound or iteration_bound_int <= 0:
        return _failure("max_iterations must be a positive integer")
    if len(curves) != bone_count + morph_count:
        return _failure("generic curve count does not match pose metadata")

    planned: List[ScalarCurvePlan] = []
    max_translate_error = 0.0
    max_rotate_error = 0.0
    max_morph_error = 0.0
    for bone_index in range(bone_count):
        seed_indices = _curve_seed_indices(curves, MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL, bone_index)
        if seed_indices is None:
            return _failure(f"generic bone curve {bone_index} is missing or malformed")
        joint = str(bone_index_to_joint[bone_index])
        for channel in _BONE_CHANNELS:
            state = dense_joint_channel_static.get(joint, {}).get(channel, {})
            # Dense runtime authoring writes static channels as one scalar
            # value and deliberately creates no animCurve.  Mirror that
            # contract here; the scene transaction applies these values
            # separately so the reduced path cannot create more keys than
            # the dense baseline merely because a bone is stationary.
            if isinstance(state, Mapping) and state.get("is_static") is True:
                continue
            values = materialized[bone_index][channel]
            # Three independently fitted Euler channels compose into one
            # orientation.  Give each axis one third of the requested angular
            # budget so simultaneous XYZ errors cannot exceed the public
            # rotation tolerance merely through composition.
            tolerance = tolerances[1] / 3.0 if channel in _ROTATE_CHANNELS else tolerances[0]
            keys, max_error, reason = _fit_scalar(
                times,
                values,
                seed_indices,
                tolerance,
                max_iterations=iteration_bound_int,
            )
            if keys is None:
                return _failure(f"{joint}.{channel}: {reason}")
            planned.append(ScalarCurvePlan("bone", bone_index, joint, channel, keys, frame_count, max_error))
            if channel in _ROTATE_CHANNELS:
                max_rotate_error = max(max_rotate_error, max_error)
            else:
                max_translate_error = max(max_translate_error, max_error)

    for morph_index in range(morph_count):
        seed_indices = _curve_seed_indices(curves, MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT, morph_index)
        if seed_indices is None:
            return _failure(f"generic morph curve {morph_index} is missing or malformed")
        values = [frame[morph_index] for frame in morph_dense]
        keys, max_error, reason = _fit_scalar(
            times,
            values,
            seed_indices,
            tolerances[2],
            max_iterations=iteration_bound_int,
        )
        if keys is None:
            return _failure(f"morph[{morph_index}]: {reason}")
        planned.append(ScalarCurvePlan("morph", morph_index, f"morph[{morph_index}]", "weight", keys, frame_count, max_error))
        max_morph_error = max(max_morph_error, max_error)

    source_key_count = sum(curve.source_key_count for curve in planned)
    reduced_key_count = sum(curve.reduced_key_count for curve in planned)
    if source_key_count <= 0 or reduced_key_count <= 0:
        return _failure("refit produced no scalar keys")
    report = ReducedPoseChannelReport(
        source_key_count,
        reduced_key_count,
        1.0 - (float(reduced_key_count) / float(source_key_count)),
        max_translate_error,
        max_rotate_error,
        max_morph_error,
        tuple(planned),
    )
    return ReducedPoseChannelPlanOutcome(True, tuple(planned), report, None)


__all__ = [
    "ReducedPoseChannelPlanOutcome",
    "ReducedPoseChannelReport",
    "ScalarCurvePlan",
    "ScalarKeyPlan",
    "build_reduced_pose_channel_plan",
]
