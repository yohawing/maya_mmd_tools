"""Bullet collision verification for the mmd_tools physics runtime.

Verifies that Bullet collision actually works through the mmd-anim FFI
bindings, independent of Maya pose input. Rigid bodies are built directly as
typed descriptors with ``bone_index=-1`` (unbound), so
``copy_rigidbody_states`` reflects raw Bullet simulation results rather than
bone-driven readback.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
)
from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"

_IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)


def _native_physics_available() -> bool:
    try:
        return is_native_physics_available()
    except Exception:
        return False


def _set3(field, values):
    field[0], field[1], field[2] = values


def _set4(field, values):
    field[0], field[1], field[2], field[3] = values


def _make_rigidbody_desc(
    shape,
    shape_size,
    position,
    mode,
    mass=0.0,
    collision_group=0,
    collision_mask=0xFFFF,
):
    """Build a standalone rigid body descriptor unbound from any bone."""
    desc = MmdRuntimeFfiPhysicsRigidbodyDesc()
    desc.shape = shape
    _set3(desc.shape_size, shape_size)
    _set3(desc.position_xyz, position)
    _set3(desc.rotation_euler_xyz, (0.0, 0.0, 0.0))
    desc.mass = mass
    desc.linear_damping = 0.0
    desc.angular_damping = 0.0
    desc.friction = 0.5
    desc.restitution = 0.0
    desc.collision_group = collision_group
    desc.collision_mask = collision_mask
    desc.bone_index = -1
    desc.mode = mode
    _set3(desc.body_from_bone_position_xyz, (0.0, 0.0, 0.0))
    _set4(desc.body_from_bone_rotation_xyzw, _IDENTITY_QUAT)
    _set3(desc.bone_from_body_position_xyz, (0.0, 0.0, 0.0))
    _set4(desc.bone_from_body_rotation_xyzw, _IDENTITY_QUAT)
    return desc


class TestNativePhysicsCollision(unittest.TestCase):
    """Verify Bullet collision through the mmd-anim FFI without Maya."""

    @classmethod
    def setUpClass(cls):
        if not FIXTURE_PATH.is_file():
            raise RuntimeError(f"Native physics fixture not found: {FIXTURE_PATH}")
        if not _native_physics_available():
            raise RuntimeError("Native physics runtime is unavailable")
        cls.pmx_bytes = FIXTURE_PATH.read_bytes()

    def _build_instance(self):
        """Create a model+instance pair to drive reset()/step_runtime() calls.

        The fixture's bones are irrelevant to descriptor-only bodies with
        ``bone_index=-1``; this only supplies a valid runtime instance handle.
        """
        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        self.assertIsNotNone(model)
        instance = MmdRuntimeInstance.for_model(model)
        self.assertIsNotNone(instance)
        instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        instance.evaluate_rest_pose()
        return model, instance

    def _run_steps(self, world, num_steps):
        model, instance = self._build_instance()
        world.reset(instance)
        dt = 1.0 / 60.0
        for step in range(num_steps):
            report = world.step_runtime(instance, dt)
            self.assertIsNotNone(report, f"step {step} failed")
        states = world.copy_rigidbody_states()
        instance.free()
        model.free()
        return states

    def test_dynamic_body_falls_under_gravity(self):
        desc = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
            (0.5, 0.0, 0.0),
            (0.0, 10.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
            mass=1.0,
        )
        world = MmdRuntimePhysicsWorld.from_descriptors([desc], [])
        self.assertIsNotNone(world)

        states = self._run_steps(world, 60)
        self.assertIsNotNone(states)
        self.assertEqual(len(states), 1)

        final_y = states[0][0][1]
        self.assertLess(final_y, 5.0, f"body should have fallen significantly: y={final_y}")

        world.free()

    def test_static_floor_stops_falling_body(self):
        floor = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX,
            (5.0, 0.5, 5.0),
            (0.0, 0.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
        )
        ball = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
            (0.5, 0.0, 0.0),
            (0.0, 5.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
            mass=1.0,
        )
        world = MmdRuntimePhysicsWorld.from_descriptors([floor, ball], [])
        self.assertIsNotNone(world)

        states = self._run_steps(world, 120)
        self.assertIsNotNone(states)
        self.assertEqual(len(states), 2)

        ball_y = states[1][0][1]
        self.assertGreater(ball_y, 0.0, f"ball should be stopped by floor: y={ball_y}")

        world.free()

    def test_collision_group_mask_filtering(self):
        floor = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_BOX,
            (5.0, 0.5, 5.0),
            (0.0, 0.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
            collision_group=0,
            collision_mask=0xFFFF,
        )
        body_a = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
            (0.5, 0.0, 0.0),
            (0.0, 5.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
            mass=1.0,
            collision_group=1,
            collision_mask=0x0001,
        )
        body_b = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
            (0.5, 0.0, 0.0),
            (3.0, 5.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC,
            mass=1.0,
            collision_group=2,
            collision_mask=0x0000,
        )
        world = MmdRuntimePhysicsWorld.from_descriptors([floor, body_a, body_b], [])
        self.assertIsNotNone(world)

        states = self._run_steps(world, 120)
        self.assertIsNotNone(states)
        self.assertEqual(len(states), 3)

        y_a = states[1][0][1]
        y_b = states[2][0][1]
        self.assertGreater(y_a, 0.0, f"body A (mask matches floor group) should be stopped: y={y_a}")
        self.assertLess(y_b, -5.0, f"body B (mask=0) should fall through: y={y_b}")

        world.free()

    def test_kinematic_body_not_affected_by_gravity(self):
        desc = _make_rigidbody_desc(
            MMD_RUNTIME_PHYSICS_RIGIDBODY_SHAPE_SPHERE,
            (0.5, 0.0, 0.0),
            (0.0, 5.0, 0.0),
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_STATIC,
        )
        world = MmdRuntimePhysicsWorld.from_descriptors([desc], [])
        self.assertIsNotNone(world)

        states = self._run_steps(world, 60)
        self.assertIsNotNone(states)
        self.assertEqual(len(states), 1)

        final_y = states[0][0][1]
        self.assertAlmostEqual(final_y, 5.0, delta=0.01)

        world.free()

    def test_descriptor_vs_pmx_bytes_collision_parity(self):
        pmx = parse_pmx_file(str(FIXTURE_PATH))
        desc_set = build_descriptors_from_pmx(pmx.rigid_bodies, pmx.joints, pmx.bones)
        blocking = [e for e in desc_set.validation_errors if e.kind == "rigid_body"]
        self.assertEqual(blocking, [])

        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        desc_world = MmdRuntimePhysicsWorld.from_descriptors(desc_set.rigid_bodies, desc_set.joints)
        self.assertIsNotNone(pmx_world)
        self.assertIsNotNone(desc_world)

        pmx_states = self._run_steps(pmx_world, 30)
        desc_states = self._run_steps(desc_world, 30)
        self.assertIsNotNone(pmx_states)
        self.assertIsNotNone(desc_states)
        self.assertEqual(len(pmx_states), len(desc_states))

        for body_idx, ((pmx_pos, pmx_rot), (desc_pos, desc_rot)) in enumerate(
            zip(pmx_states, desc_states)
        ):
            for i, (p, d) in enumerate(zip(pmx_pos, desc_pos)):
                self.assertAlmostEqual(
                    p, d, delta=0.01,
                    msg=f"body[{body_idx}] pos[{i}]: pmx={p} desc={d}",
                )
            for i, (p, d) in enumerate(zip(pmx_rot, desc_rot)):
                self.assertAlmostEqual(
                    p, d, delta=0.01,
                    msg=f"body[{body_idx}] rot[{i}]: pmx={p} desc={d}",
                )

        pmx_world.free()
        desc_world.free()


if __name__ == "__main__":
    unittest.main()
