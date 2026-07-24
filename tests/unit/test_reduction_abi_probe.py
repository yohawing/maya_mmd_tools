"""Deterministic fixture contracts for the reduction ABI probe."""

from __future__ import annotations

import math
import unittest
import ctypes
from unittest import mock

from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimeReducedPose
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeBatchEvaluation
from tests.release.reduction_abi_probe import (
    CURRENT_ABI_VERSION,
    GenericCurveKey,
    STATUS_BUFFER_TOO_SMALL,
    STATUS_OK,
    SUPPORTED_ABI_VERSIONS,
    _generic_curve_keys,
    make_dense_fixture,
)


class _FakeGenericCurveLibrary:
    """Minimal fake for the generic ABI's stride-aware two-call contract."""

    def __init__(self, *, mutate_short=False):
        self.mutate_short = mutate_short

    def mmd_runtime_reduced_pose_generic_curve_keys(self, _pose, _index, out_keys, capacity, stride, out_required):
        self.last_stride = stride
        out_required._obj.value = 2
        if not out_keys or capacity < 2:
            if self.mutate_short and out_keys:
                out_keys[0].sample_index = 999
            return STATUS_BUFFER_TOO_SMALL
        for index in range(2):
            out_keys[index].sample_index = index
            out_keys[index].frame = 10.0 + index
            out_keys[index].rotation_xyzw[3] = 1.0
        return STATUS_OK


class _OldRuntimeLibrary:
    def mmd_runtime_feature_flags(self):
        return 0


class _FakeModel:
    handle = ctypes.c_void_p(1)


class ReductionAbiProbeFixtureTest(unittest.TestCase):
    """Keep the probe input stable while the native gate exercises the DLL."""

    def test_fixture_is_deterministic_and_dense(self):
        first = make_dense_fixture()
        second = make_dense_fixture()
        self.assertEqual(first, second)
        self.assertEqual(first["frame_count"], 31)
        self.assertEqual(len(first["world_matrices"]), 31 * 2 * 16)
        self.assertEqual(len(first["morph_weights"]), 31)
        self.assertTrue(all(math.isfinite(value) for value in first["world_matrices"]))
        self.assertTrue(all(math.isfinite(value) for value in first["morph_weights"]))

    def test_abi_contract_accepts_current_and_compat_versions(self):
        self.assertEqual(CURRENT_ABI_VERSION, 3)
        self.assertEqual(SUPPORTED_ABI_VERSIONS, (2, 3))

    def test_fixture_has_parent_and_child_world_samples(self):
        fixture = make_dense_fixture()
        self.assertEqual(fixture["parents"], [-1, 0])
        # Column-major translation slots for root and child differ at frame 0,
        # proving the child samples are world matrices rather than local data.
        self.assertEqual(fixture["world_matrices"][12:15], [0.0, 0.0, 0.0])
        child_translation = fixture["world_matrices"][28:31]
        self.assertAlmostEqual(math.hypot(child_translation[0], child_translation[1]), 1.0)
        self.assertAlmostEqual(child_translation[2], 0.0)

    def test_generic_curve_key_two_call_and_stride_contract(self):
        library = _FakeGenericCurveLibrary()
        first, second, short, keys, short_unchanged = _generic_curve_keys(library, ctypes.c_void_p(1), 0)
        self.assertEqual(first, STATUS_BUFFER_TOO_SMALL)
        self.assertEqual(short, STATUS_BUFFER_TOO_SMALL)
        self.assertEqual(second, STATUS_OK)
        self.assertTrue(short_unchanged)
        self.assertEqual(len(keys), 2)
        self.assertEqual(library.last_stride, ctypes.sizeof(GenericCurveKey))
        self.assertEqual([key.sample_index for key in keys], [0, 1])

    def test_short_buffer_mutation_is_reported_and_keys_are_rejected(self):
        library = _FakeGenericCurveLibrary(mutate_short=True)
        first, second, short, keys, short_unchanged = _generic_curve_keys(library, ctypes.c_void_p(1), 0)
        self.assertEqual(first, STATUS_BUFFER_TOO_SMALL)
        self.assertEqual(short, STATUS_BUFFER_TOO_SMALL)
        self.assertEqual(second, STATUS_BUFFER_TOO_SMALL)
        self.assertFalse(short_unchanged)
        self.assertEqual(keys, [])

    def test_old_runtime_returns_unsupported_without_native_calls(self):
        fixture = make_dense_fixture()
        batch = MmdRuntimeBatchEvaluation(
            fixture["frame_count"],
            2,
            1,
            fixture["world_matrices"],
            fixture["morph_weights"],
        )
        with mock.patch.object(MmdRuntimeReducedPose, "_get_library", staticmethod(lambda: _OldRuntimeLibrary())):
            result = MmdRuntimeReducedPose.from_dense(_FakeModel(), batch)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
