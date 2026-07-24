"""Pure-Python tests for the Maya scalar reduced-pose adapter."""

from __future__ import annotations

import math
import unittest

from mmd_tools.converters.vmd_reduced_pose_adapter import (
    ScalarKeyPlan,
    _fit_scalar,
    _hermite_value,
    build_reduced_pose_channel_plan,
    _make_keys,
    _replay_error,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MmdRuntimeGenericCurve,
    MmdRuntimeGenericCurveDescriptor,
    MmdRuntimeGenericCurveInfo,
    MmdRuntimeGenericCurveKey,
    MmdRuntimePoseReductionReport,
    MmdRuntimeReducedPoseResult,
)


def _key(sample_index: int, frame: float, *, scalar: float = 0.0) -> MmdRuntimeGenericCurveKey:
    """Create a generic key while keeping all diagnostic fields inert."""
    zeros3 = (0.0, 0.0, 0.0)
    return MmdRuntimeGenericCurveKey(
        sample_index,
        frame,
        zeros3,
        (0.0, 0.0, 0.0, 1.0),
        scalar,
        zeros3,
        zeros3,
        zeros3,
        zeros3,
        zeros3,
        zeros3,
        0.0,
        0.0,
    )


def _pose(frame_count: int, bone_indices=(0, -1), morph_indices=(0, -1)) -> MmdRuntimeReducedPoseResult:
    bone_keys = tuple(_key(index, float(index)) for index in bone_indices)
    morph_keys = tuple(_key(index, float(index)) for index in morph_indices)
    bone_descriptor = MmdRuntimeGenericCurveDescriptor(40, 1, 0, 0, -1, 3, 2, 1, len(bone_keys))
    morph_descriptor = MmdRuntimeGenericCurveDescriptor(40, 1, 1, 0, -1, 4, 2, 0, len(morph_keys))
    info = MmdRuntimeGenericCurveInfo(72, 1, 2, 0, 0, 0, 0, 0, 7, 0.0, 1.0, frame_count, 1, 1)
    report = MmdRuntimePoseReductionReport(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return MmdRuntimeReducedPoseResult(
        info,
        (MmdRuntimeGenericCurve(bone_descriptor, bone_keys), MmdRuntimeGenericCurve(morph_descriptor, morph_keys)),
        report,
    )


def _dense_joint(values_by_channel, frame_count):
    channels = {}
    static = {}
    for channel in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"):
        values = values_by_channel.get(channel, [0.0] * frame_count)
        if values is None:
            channels[channel] = None
            static[channel] = {"first": 0.0, "is_static": True, "count": frame_count}
        else:
            channels[channel] = list(values)
            static[channel] = {"first": float(values[0]), "is_static": False, "count": frame_count}
    return {"root": channels}, {"root": static}


def _build(frame_times, joint_channels, morph_weights, *, pose=None, **kwargs):
    frame_count = len(frame_times)
    pose = pose or _pose(frame_count, (0, frame_count - 1), (0, frame_count - 1))
    values, static = _dense_joint(joint_channels, frame_count)
    morph_cache = list(zip(frame_times, ([weight] for weight in morph_weights)))
    return build_reduced_pose_channel_plan(
        pose,
        frame_times,
        {0: "root"},
        values,
        static,
        morph_cache,
        **kwargs,
    )


def _sparse_neighbor_keys(times, values, indices):
    """Reproduce the pre-change selected-key chord slope estimation."""
    ordered = sorted(set(int(index) for index in indices))
    keys = []
    for position, sample_index in enumerate(ordered):
        if len(ordered) == 1:
            slope = 0.0
        elif position == 0:
            delta_time = times[ordered[1]] - times[sample_index]
            slope = (values[ordered[1]] - values[sample_index]) / delta_time
        elif position == len(ordered) - 1:
            delta_time = times[sample_index] - times[ordered[position - 1]]
            slope = (values[sample_index] - values[ordered[position - 1]]) / delta_time
        else:
            delta_time = times[ordered[position + 1]] - times[ordered[position - 1]]
            slope = (values[ordered[position + 1]] - values[ordered[position - 1]]) / delta_time
        keys.append(ScalarKeyPlan(times[sample_index], values[sample_index], slope, slope))
    return tuple(keys)


def _old_all_violations_fit(times, values, seed_indices, tolerance, max_iterations=100):
    """Reference implementation of the pre-refinement all-violations loop."""
    indices = {0, len(times) - 1, *(int(index) for index in seed_indices)}
    for _iteration in range(max_iterations + 1):
        ordered = sorted(indices)
        keys = _sparse_neighbor_keys(times, values, ordered)
        max_error = 0.0
        violating = []
        for sample_index, (time, value) in enumerate(zip(times, values)):
            if sample_index in indices:
                error = 0.0
            else:
                right_position = next(
                    position for position, key in enumerate(keys) if key.maya_time >= time
                )
                left_position = max(0, right_position - 1)
                error = abs(_hermite_value(keys[left_position], keys[right_position], time) - value)
            max_error = max(max_error, error)
            if error > tolerance:
                violating.append(sample_index)
        if max_error <= tolerance:
            return keys
        additions = set(violating) - indices
        if not additions:
            return None
        indices.update(additions)
    return None


def _bounded_refine_fit(times, values, seed_indices, tolerance, key_builder, max_iterations=100):
    """Bounded segment-worst refinement using an injected slope estimator."""
    indices = {0, len(times) - 1, *(int(index) for index in seed_indices)}
    for _iteration in range(max_iterations + 1):
        ordered = sorted(indices)
        keys = key_builder(times, values, ordered)
        candidates = []
        max_error = 0.0
        for segment_position, (left_index, right_index) in enumerate(zip(ordered, ordered[1:])):
            segment_error = 0.0
            segment_sample = None
            for sample_index in range(left_index + 1, right_index):
                error = abs(
                    _hermite_value(keys[segment_position], keys[segment_position + 1], times[sample_index])
                    - values[sample_index]
                )
                max_error = max(max_error, error)
                if error > segment_error:
                    segment_error = error
                    segment_sample = sample_index
            if segment_sample is not None and segment_error > tolerance:
                candidates.append(segment_sample)
        if max_error <= tolerance:
            return keys, max_error
        additions = set(candidates) - indices
        if not additions:
            return None, max_error
        indices.update(additions)
    return None, max_error


class ReducedPoseAdapterTest(unittest.TestCase):
    def test_static_tracks_are_left_for_scalar_scene_assignment(self):
        result = _build([10.0, 12.0, 14.0], {"translateX": None}, [0.25, 0.25, 0.25])

        self.assertTrue(result.success)
        self.assertEqual(len(result.curves), 6)
        self.assertTrue(all(len(curve.keys) == 2 for curve in result.curves))
        self.assertEqual(result.report.source_key_count, 18)
        self.assertEqual(result.report.reduced_key_count, 12)
        self.assertFalse(any(curve.channel == "translateX" for curve in result.curves))

    def test_nonlinear_translation_refines_seed_keys_and_replays(self):
        values = [float(index**3) for index in range(5)]
        result = _build([0.0, 1.0, 2.0, 3.0, 4.0], {"translateX": values}, [0.0] * 5, translate_tolerance=1.0e-6)

        self.assertTrue(result.success)
        translate = next(curve for curve in result.curves if curve.channel == "translateX")
        self.assertGreater(len(translate.keys), 2)
        self.assertLessEqual(translate.max_error, 1.0e-6)
        self.assertLessEqual(result.report.max_translate_error, 1.0e-6)

    def test_rotation_wrap_is_unwrapped_before_refit(self):
        result = _build(
            [0.0, 1.0, 2.0],
            {"rotateX": [3.0, -3.0, -2.8]},
            [0.0, 0.0, 0.0],
            rotate_tolerance_radians=1.0e-6,
        )

        self.assertTrue(result.success)
        rotate = next(curve for curve in result.curves if curve.channel == "rotateX")
        self.assertGreater(rotate.keys[1].value, math.pi)
        self.assertLessEqual(rotate.max_error, 1.0e-6)

    def test_morph_and_non_unit_maya_spacing_are_replayed(self):
        times = [100.0, 100.5, 101.5, 103.0]
        result = _build(
            times,
            {"translateX": [0.0, 0.5, 1.5, 3.0]},
            [0.0, 1.0, 0.0, 0.5],
            translate_tolerance=1.0e-6,
            morph_tolerance=1.0e-6,
        )

        self.assertTrue(result.success)
        translate = next(curve for curve in result.curves if curve.channel == "translateX")
        morph = next(curve for curve in result.curves if curve.owner_kind == "morph")
        self.assertEqual((translate.keys[0].maya_time, translate.keys[-1].maya_time), (100.0, 103.0))
        self.assertEqual([key.maya_time for key in morph.keys], times)

    def test_malformed_input_fails_atomically(self):
        result = _build([1.0, 1.0], {"translateX": [0.0, 1.0]}, [0.0, 0.0])

        self.assertFalse(result.success)
        self.assertEqual(result.curves, ())
        self.assertIsNone(result.report)
        self.assertIn("strictly increasing", result.failure_reason)

    def test_segment_worst_refinement_avoids_unnecessary_dense_keys(self):
        # Four native seed keys partition a dense ten-sample clip into three
        # segments.  The old loop retained every violating sample in one pass;
        # the bounded loop keeps only the worst sample per segment and reaches
        # the same replay tolerance with fewer authored keys.
        times = list(range(10))
        values = [
            -0.144141,
            0.296909,
            0.623788,
            0.807518,
            0.862413,
            0.924387,
            0.929690,
            0.759235,
            0.601481,
            0.271236,
        ]
        tolerance = 0.05
        seeds = [0, 3, 6, 9]

        refined, max_error, reason = _fit_scalar(
            times,
            values,
            seeds,
            tolerance,
            max_iterations=100,
        )
        retained_by_old_loop = _old_all_violations_fit(times, values, seeds, tolerance)

        self.assertIsNone(reason)
        self.assertIsNotNone(refined)
        self.assertIsNotNone(retained_by_old_loop)
        self.assertLess(len(refined), len(retained_by_old_loop))
        self.assertLessEqual(max_error, tolerance)
        self.assertEqual(len(retained_by_old_loop), 9)

    def test_dense_local_slopes_improve_smooth_motion_refit(self):
        times = list(range(20))
        values = [math.sin(index / 10.0) + 0.0001 * math.sin(index) for index in times]
        tolerance = 1.0e-4
        seeds = [0, len(times) - 1]

        refined, max_error, reason = _fit_scalar(
            times,
            values,
            seeds,
            tolerance,
            max_iterations=100,
        )
        sparse, sparse_error = _bounded_refine_fit(
            times,
            values,
            seeds,
            tolerance,
            _sparse_neighbor_keys,
            max_iterations=100,
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(refined)
        self.assertIsNotNone(sparse)
        self.assertLessEqual(max_error, tolerance)
        self.assertLessEqual(sparse_error, tolerance)
        self.assertLess(len(refined), len(sparse))

    def test_native_seed_hint_is_prioritized_for_meaningful_scalar_motion(self):
        times = list(range(6))
        values = [0.0, 1.0, 0.0, 3.0, 0.0, 0.0]
        tolerance = 0.1
        initial_indices = [0, 5]
        initial_keys = _make_keys(times, values, initial_indices)

        max_error, candidates = _replay_error(
            times,
            values,
            initial_indices,
            initial_keys,
            tolerance,
            seed_hints=[1],
        )
        refined, refined_error, reason = _fit_scalar(
            times,
            values,
            [1],
            tolerance,
            max_iterations=100,
        )

        self.assertGreater(max_error, tolerance)
        self.assertEqual(candidates, [1])
        self.assertIsNone(reason)
        self.assertIsNotNone(refined)
        self.assertLessEqual(refined_error, tolerance)
        self.assertIn(1.0, [key.maya_time for key in refined])

if __name__ == "__main__":
    unittest.main()
