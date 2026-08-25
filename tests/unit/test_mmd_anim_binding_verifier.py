"""Headless contracts for the optional mmd-anim Python binding verifier."""

import math
from pathlib import Path
import tempfile
import unittest

from mmd_tools.validation.mmd_anim_binding_verifier import verify_mmd_anim_binding_asset


class _Handle:
    def __init__(self, events, name):
        self.events = events
        self.name = name
        self.closed = False

    def __enter__(self):
        self.events.append(("enter", self.name))
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.events.append(("close", self.name))


class _FakeInstance(_Handle):
    def __init__(self, events, matrices, weights):
        super().__init__(events, "instance")
        self.matrices = matrices
        self.weights = weights
        self.evaluated = None

    def evaluate_rest_pose(self):
        self.evaluated = ("rest", None)

    def evaluate_clip_frame(self, clip, frame):
        self.evaluated = ("clip", frame, clip)

    def world_matrices_f32(self):
        return self.matrices

    def morph_weights_f32(self):
        return self.weights


class _FakeClip(_Handle):
    def __init__(self, events):
        super().__init__(events, "clip")


class _FakeModel(_Handle):
    def __init__(self, events, matrices, weights, bones=2, morphs=1):
        super().__init__(events, "model")
        self.matrices = matrices
        self.weights = weights
        self.bones = bones
        self.morphs = morphs

    def bone_count(self):
        return self.bones

    def morph_count(self):
        return self.morphs

    def create_instance_for_model(self):
        return _FakeInstance(self.events, self.matrices, self.weights)


class _FakeRuntime:
    def __init__(self, events, matrices=None, weights=None, failure=None):
        self.events = events
        self.matrices = matrices if matrices is not None else [1.0] * 32
        self.weights = weights if weights is not None else [0.0]
        self.failure = failure
        self.model = None

    def create_model_from_pmx_bytes(self, _data):
        if self.failure:
            raise self.failure
        self.model = _FakeModel(self.events, self.matrices, self.weights)
        return self.model

    def create_clip_from_vmd_bytes(self, _model, _data):
        return _FakeClip(self.events)


class MmdAnimBindingVerifierTest(unittest.TestCase):
    def _files(self, with_motion=True):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        model = root / "model.pmx"
        model.write_bytes(b"pmx")
        motion = root / "motion.vmd"
        if with_motion:
            motion.write_bytes(b"vmd")
        return directory, model, motion

    def test_missing_binding_is_blocking(self):
        directory, model, _motion = self._files(with_motion=False)
        try:
            report = verify_mmd_anim_binding_asset(str(model))
        finally:
            directory.cleanup()
        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertTrue(report.is_blocking)

    def test_runtime_failure_is_blocking(self):
        directory, model, _motion = self._files(with_motion=False)
        try:
            report = verify_mmd_anim_binding_asset(
                str(model),
                runtime_factory=lambda _path: _FakeRuntime([], failure=RuntimeError("boom")),
            )
        finally:
            directory.cleanup()
        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertIn("RuntimeError", report.issues[0].reason)

    def test_rest_pose_passes_and_closes_handles(self):
        directory, model, _motion = self._files(with_motion=False)
        events = []
        runtime = _FakeRuntime(events)
        try:
            report = verify_mmd_anim_binding_asset(
                str(model),
                expected_counts={"bones": 2, "morphs": 1},
                runtime_factory=lambda _path: runtime,
            )
        finally:
            directory.cleanup()
        self.assertTrue(report.valid)
        self.assertIn(("close", "instance"), events)
        self.assertIn(("close", "model"), events)

    def test_motion_frame_passes_and_closes_clip(self):
        directory, model, motion = self._files()
        events = []
        runtime = _FakeRuntime(events)
        try:
            report = verify_mmd_anim_binding_asset(
                str(model),
                motion_path=str(motion),
                frame=12,
                runtime_factory=lambda _path: runtime,
            )
        finally:
            directory.cleanup()
        self.assertTrue(report.valid)
        self.assertIn(("close", "clip"), events)
        self.assertIn(("close", "instance"), events)
        self.assertIn(("close", "model"), events)

    def test_count_mismatch_is_blocking(self):
        directory, model, _motion = self._files(with_motion=False)
        try:
            report = verify_mmd_anim_binding_asset(
                str(model),
                expected_counts={"bones": 3},
                runtime_factory=lambda _path: _FakeRuntime([]),
            )
        finally:
            directory.cleanup()
        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertEqual(report.issues[0].details["section"], "bones")
        self.assertEqual(report.issues[0].details["expected_count"], 3)
        self.assertEqual(report.issues[0].details["actual_count"], 2)

    def test_non_finite_matrix_and_wrong_length_are_blocking(self):
        directory, model, _motion = self._files(with_motion=False)
        try:
            non_finite = verify_mmd_anim_binding_asset(
                str(model),
                runtime_factory=lambda _path: _FakeRuntime([], matrices=[math.nan] + [1.0] * 31),
            )
            wrong_length = verify_mmd_anim_binding_asset(
                str(model),
                runtime_factory=lambda _path: _FakeRuntime([], matrices=[1.0] * 16),
            )
        finally:
            directory.cleanup()
        self.assertEqual(non_finite.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertEqual(wrong_length.issues[0].code, "EXTERNAL_TOOL_FAILED")

    def test_non_finite_morph_weight_is_blocking_with_weight_code(self):
        directory, model, _motion = self._files(with_motion=False)
        try:
            report = verify_mmd_anim_binding_asset(
                str(model),
                runtime_factory=lambda _path: _FakeRuntime([], weights=[math.inf]),
            )
        finally:
            directory.cleanup()
        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")


if __name__ == "__main__":
    unittest.main()
