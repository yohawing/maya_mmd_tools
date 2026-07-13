"""Parity oracle: typed descriptor world vs PMX direct world.

Requires the native physics DLL with Bullet support. Skipped when unavailable.
This is the PHS-0 exit gate: if these tests fail, Maya node implementation
must not proceed.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.native.mmd_anim_runtime import (
    is_native_physics_available,
)
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
)
from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _native_physics_available() -> bool:
    try:
        return is_native_physics_available()
    except Exception:
        return False


def _pmx_fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestDescriptorWorldParity(unittest.TestCase):
    """Typed descriptor world must match PMX direct world exactly."""

    @classmethod
    def setUpClass(cls):
        cls.pmx_bytes = _pmx_fixture_bytes()
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))
        cls.desc_set = build_descriptors_from_pmx(
            cls.pmx.rigid_bodies, cls.pmx.joints, cls.pmx.bones,
        )

    def test_descriptor_set_has_no_blocking_errors(self):
        blocking = [
            e for e in self.desc_set.validation_errors
            if e.kind == "rigid_body"
        ]
        self.assertEqual(blocking, [])

    def test_both_worlds_have_same_rigidbody_count(self):
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        self.assertIsNotNone(pmx_world)
        desc_world = MmdRuntimePhysicsWorld.from_descriptors(
            self.desc_set.rigid_bodies, self.desc_set.joints,
        )
        self.assertIsNotNone(desc_world)

        pmx_count = pmx_world.rigidbody_count()
        desc_count = desc_world.rigidbody_count()
        self.assertIsNotNone(pmx_count)
        self.assertIsNotNone(desc_count)
        self.assertEqual(pmx_count, desc_count)
        self.assertEqual(pmx_count, 16)

        pmx_world.free()
        desc_world.free()

    def test_initial_rigidbody_states_match(self):
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        desc_world = MmdRuntimePhysicsWorld.from_descriptors(
            self.desc_set.rigid_bodies, self.desc_set.joints,
        )
        self.assertIsNotNone(pmx_world)
        self.assertIsNotNone(desc_world)

        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        self.assertIsNotNone(model)
        instance = MmdRuntimeInstance.for_model(model)
        self.assertIsNotNone(instance)
        instance.evaluate_rest_pose()

        pmx_world.reset(instance)
        desc_world.reset(instance)

        pmx_states = pmx_world.copy_rigidbody_states()
        desc_states = desc_world.copy_rigidbody_states()
        self.assertIsNotNone(pmx_states)
        self.assertIsNotNone(desc_states)
        self.assertEqual(len(pmx_states), len(desc_states))

        for body_idx, ((pmx_pos, pmx_rot), (desc_pos, desc_rot)) in enumerate(
            zip(pmx_states, desc_states)
        ):
            for i, (p, d) in enumerate(zip(pmx_pos, desc_pos)):
                self.assertAlmostEqual(
                    p, d, places=5,
                    msg=f"body[{body_idx}] pos[{i}]: pmx={p} desc={d}",
                )
            for i, (p, d) in enumerate(zip(pmx_rot, desc_rot)):
                self.assertAlmostEqual(
                    p, d, places=5,
                    msg=f"body[{body_idx}] rot[{i}]: pmx={p} desc={d}",
                )

        instance.free()
        model.free()
        pmx_world.free()
        desc_world.free()

    def _run_steps(self, world, num_steps=10):
        """Run physics steps with an independent model/instance and return rigidbody states."""
        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        instance.evaluate_rest_pose()
        world.reset(instance)

        dt = 1.0 / 60.0
        for step in range(num_steps):
            report = world.step_runtime(instance, dt)
            self.assertIsNotNone(report, f"step {step} failed")

        states = world.copy_rigidbody_states()
        instance.free()
        model.free()
        return states

    def test_multi_step_states_match(self):
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        desc_world = MmdRuntimePhysicsWorld.from_descriptors(
            self.desc_set.rigid_bodies, self.desc_set.joints,
        )

        pmx_states = self._run_steps(pmx_world)
        desc_states = self._run_steps(desc_world)
        self.assertIsNotNone(pmx_states)
        self.assertIsNotNone(desc_states)
        self.assertEqual(len(pmx_states), len(desc_states))

        for body_idx, ((pmx_pos, pmx_rot), (desc_pos, desc_rot)) in enumerate(
            zip(pmx_states, desc_states)
        ):
            for i, (p, d) in enumerate(zip(pmx_pos, desc_pos)):
                self.assertAlmostEqual(
                    p, d, delta=0.005,
                    msg=f"after 10 steps: body[{body_idx}] pos[{i}]: pmx={p} desc={d}",
                )
            for i, (p, d) in enumerate(zip(pmx_rot, desc_rot)):
                self.assertAlmostEqual(
                    p, d, delta=0.005,
                    msg=f"after 10 steps: body[{body_idx}] rot[{i}]: pmx={p} desc={d}",
                )

        pmx_world.free()
        desc_world.free()

    def test_determinism_two_runs_identical(self):
        results = []
        for _ in range(2):
            world = MmdRuntimePhysicsWorld.from_descriptors(
                self.desc_set.rigid_bodies, self.desc_set.joints,
            )
            model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
            instance = MmdRuntimeInstance.for_model(model)
            instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
            instance.evaluate_rest_pose()
            world.reset(instance)

            dt = 1.0 / 60.0
            for _ in range(5):
                world.step_runtime(instance, dt)

            states = world.copy_rigidbody_states()
            results.append(states)

            instance.free()
            model.free()
            world.free()

        self.assertIsNotNone(results[0])
        self.assertIsNotNone(results[1])
        for body_idx, ((pos_a, rot_a), (pos_b, rot_b)) in enumerate(
            zip(results[0], results[1])
        ):
            for i, (va, vb) in enumerate(zip(pos_a, pos_b)):
                self.assertEqual(
                    va, vb,
                    msg=f"determinism: body[{body_idx}] pos[{i}]: run1={va} run2={vb}",
                )
            for i, (va, vb) in enumerate(zip(rot_a, rot_b)):
                self.assertEqual(
                    va, vb,
                    msg=f"determinism: body[{body_idx}] rot[{i}]: run1={va} run2={vb}",
                )


if __name__ == "__main__":
    unittest.main()
