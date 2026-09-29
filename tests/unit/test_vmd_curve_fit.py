"""Quantized interpolation must preserve sampled motion or fail explicitly."""

import math
import unittest

from mmd_tools.converters.vmd_curve_fit import (
    fit_scene_track, bezier_value, _slerp, _angle, ROTATION_TOLERANCE,
)


class TestVmdCurveFit(unittest.TestCase):
    def test_nonlinear_position_and_rotation_are_fit_after_quantization(self):
        control = (13, 102, 38, 127)
        q1 = (0., math.sin(.4), 0., math.cos(.4))
        def sample(t):
            progress = bezier_value(control, t/30.)
            return (3*progress, 0., -progress), _slerp((0., 0., 0., 1.), q1, progress)
        frames = fit_scene_track("bone", [0, 30], sample)
        self.assertLess(len(frames), 31)
        for left, right in zip(frames, frames[1:]):
            raw = right["interpolation"]
            controls = [tuple(raw[axis+4*i] for i in range(4)) for axis in range(4)]
            for i in range(1, 100):
                x = i/100.
                t = left["frame_number"] + x*(right["frame_number"]-left["frame_number"])
                p, q = sample(t)
                actual = [left["position"][j]+(right["position"][j]-left["position"][j])*bezier_value(controls[j],x) for j in range(3)]
                self.assertLessEqual(math.dist(p, actual), .001)
                self.assertLessEqual(_angle(q, _slerp(left["rotation"],right["rotation"],bezier_value(controls[3],x))), ROTATION_TOLERANCE)

    def test_morph_adds_integer_keys_to_preserve_edited_tangents(self):
        frames = fit_scene_track("morph", [0, 30], lambda t:(t/30.)**2, morph=True)
        self.assertGreater(len(frames), 2)
        self.assertEqual(frames[-1]["weight"], 1.)
        for left, right in zip(frames, frames[1:]):
            t = (left["frame_number"]+right["frame_number"])/2
            self.assertLessEqual(abs((t/30.)**2-(left["weight"]+right["weight"])/2), .001)

    def test_unrepresentable_adjacent_frame_motion_fails(self):
        with self.assertRaisesRegex(ValueError, "morph.*frames 0..1"):
            fit_scene_track("morph", [0, 1], lambda t:t*t, morph=True)
        def excursion(t):
            return (0., 0., 0.), (math.sin(math.pi*t)*.2, 0., 0., 1.)
        with self.assertRaisesRegex(ValueError, "bone.*frames 0..1"):
            fit_scene_track("bone", [0, 1], excursion)

    def test_fractional_source_keys_are_witnesses_not_rounded_away(self):
        with self.assertRaisesRegex(ValueError, "tolerance exceeded"):
            fit_scene_track("morph", [0, .123, 1], lambda t:1. if t == .123 else 0., morph=True)

    def test_nonfinite_and_negative_input_fail(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            fit_scene_track("morph", [0, 1], lambda t:float("nan"), morph=True)
        with self.assertRaisesRegex(ValueError, "frame range"):
            fit_scene_track("morph", [-1, 1], lambda t:t, morph=True)

    def test_float32_endpoint_precision_is_checked_even_for_one_key(self):
        with self.assertRaisesRegex(ValueError, "key precision"):
            fit_scene_track("bone", [0], lambda t:((100000.004, 0., 0.), (0.,0.,0.,1.)))

    def test_dense_witnesses_do_not_force_requantized_output_keys(self):
        def sample(t):
            value = bezier_value((64, 0, 64, 127), t / 5.)
            return (0., 6. * value, 4. * value), (0., 0., 0., 1.)
        frames = fit_scene_track("bone", list(range(6)), sample, authored_times=[0, 5])
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 5])

    def test_representable_adjacent_curve_survives_local_search_minimum(self):
        def sample(t):
            return (10 * bezier_value((34, 16, 106, 65), t), 0., 0.), (0., 0., 0., 1.)
        frames = fit_scene_track("representable", [0, 1], sample)
        self.assertEqual([frame["frame_number"] for frame in frames], [0, 1])
