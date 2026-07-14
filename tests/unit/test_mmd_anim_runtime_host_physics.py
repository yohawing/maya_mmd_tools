"""Focused fail-closed contracts for the formal mmd-anim host physics ABI."""

from __future__ import annotations

import ctypes
import math
import unittest

from mmd_tools.core.native.mmd_anim_runtime_handles import MmdRuntimeInstance, MmdRuntimePhysicsWorld
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED,
    MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP,
    MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS,
    MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL,
    MMD_RUNTIME_STATUS_OK,
    MmdRuntimeFfiPhysicsRigidbodyBinding,
)


def _value(value):
    return int(value.value) if hasattr(value, "value") else int(value)


class _HostPhysicsLib:
    def __init__(self):
        self.flags = MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS
        self.host_calls = 0
        self.gravity_calls = 0
        self.binding_count_override = None

    def mmd_runtime_feature_flags(self):
        return self.flags

    def mmd_runtime_instance_world_matrix_f32_len(self, _instance):
        return 32  # two bones

    def mmd_runtime_instance_morph_weight_len(self, _instance):
        return 1

    def mmd_runtime_instance_ik_enabled_len(self, _instance):
        return 1

    def mmd_runtime_evaluate_host_frame(self, _instance, _world, pose_ptr, action, *_rest):
        pose = ctypes.cast(pose_ptr, ctypes.POINTER(type(pose_ptr._obj))).contents
        assert pose.bone_count == 2
        assert _value(action) in (MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED, MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP)
        self.host_calls += 1
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_get_gravity(self, _world, output):
        output[0], output[1], output[2] = 0.0, -9.8, 0.0
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_set_gravity(self, _world, _gravity):
        self.gravity_calls += 1
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_rigidbody_count(self, _world, out_count):
        ctypes.cast(out_count, ctypes.POINTER(ctypes.c_size_t)).contents.value = 2
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_copy_rigidbody_bindings(self, _world, output, capacity, out_count):
        if _value(capacity) < 2:
            return MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL
        output[0] = MmdRuntimeFfiPhysicsRigidbodyBinding(0, 1)
        output[1] = MmdRuntimeFfiPhysicsRigidbodyBinding(-1, 0)
        written = 2 if self.binding_count_override is None else self.binding_count_override
        ctypes.cast(out_count, ctypes.POINTER(ctypes.c_size_t)).contents.value = written
        return MMD_RUNTIME_STATUS_OK

    def mmd_runtime_physics_world_physics_driven_bone_mask(self, _world, output, bone_count):
        if _value(bone_count) < 2:
            return MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL
        output[0], output[1] = 0, 1
        return MMD_RUNTIME_STATUS_OK


class TestHostPhysicsContract(unittest.TestCase):
    def setUp(self):
        self.lib = _HostPhysicsLib()
        self.instance = MmdRuntimeInstance(self.lib, ctypes.c_void_p(0x1001))
        self.world = MmdRuntimePhysicsWorld(self.lib, ctypes.c_void_p(0x2002))
        self.pose = {
            "local_position_offsets_xyz": [0.0] * 6,
            "local_rotation_xyzw": [0.0, 0.0, 0.0, 1.0] * 2,
            "local_scales_xyz": [1.0] * 6,
            "morph_weights": [0.25],
            "ik_enabled": [1],
            "action": MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED,
        }

    def test_seed_and_step_use_one_atomic_call(self):
        self.assertIsNotNone(self.world.evaluate_host_frame(self.instance, **self.pose))
        stepped = dict(self.pose, action=MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP, dt_seconds=1.0 / 60.0)
        self.assertIsNotNone(self.world.evaluate_host_frame(self.instance, **stepped))
        self.assertEqual(self.lib.host_calls, 2)

    def test_short_nan_and_non_normalized_buffers_never_cross_abi(self):
        invalid = [
            dict(self.pose, local_position_offsets_xyz=[0.0] * 5),
            dict(self.pose, local_scales_xyz=[1.0, 1.0, math.inf] * 2),
            dict(self.pose, local_rotation_xyzw=[0.0, 0.0, 0.0, 2.0] * 2),
            dict(self.pose, morph_weights=[math.nan]),
            dict(self.pose, ik_enabled=[]),
            dict(self.pose, ik_enabled=[0.5]),
            dict(self.pose, ik_enabled=["1"]),
        ]
        for pose in invalid:
            with self.subTest(pose=pose):
                self.assertIsNone(self.world.evaluate_host_frame(self.instance, **pose))
        self.assertEqual(self.lib.host_calls, 0)

    def test_missing_feature_or_symbol_is_unsupported(self):
        self.lib.flags = 0
        self.assertIsNone(self.world.evaluate_host_frame(self.instance, **self.pose))
        self.assertEqual(self.lib.host_calls, 0)

    def test_gravity_rejects_non_finite_and_round_trips_finite_values(self):
        self.assertFalse(self.world.set_gravity((0.0, math.nan, 0.0)))
        self.assertEqual(self.lib.gravity_calls, 0)
        self.assertTrue(self.world.set_gravity((0.0, -9.8, 0.0)))
        self.assertEqual(self.world.get_gravity(), (0.0, -9.800000190734863, 0.0))

    def test_bindings_mask_and_count_mismatch_fail_closed(self):
        self.assertEqual(self.world.copy_rigidbody_bindings(), [(0, 1), (-1, 0)])
        self.assertIsNone(self.world.physics_driven_bone_mask(1))
        self.assertEqual(self.world.physics_driven_bone_mask(2), [0, 1])
        self.lib.binding_count_override = 1
        self.assertIsNone(self.world.copy_rigidbody_bindings())

    def test_freed_or_cross_library_handles_are_rejected(self):
        other = MmdRuntimeInstance(_HostPhysicsLib(), ctypes.c_void_p(0x3003))
        self.assertIsNone(self.world.evaluate_host_frame(other, **self.pose))
        self.instance._handle = None
        self.assertIsNone(self.world.evaluate_host_frame(self.instance, **self.pose))


if __name__ == "__main__":
    unittest.main()
