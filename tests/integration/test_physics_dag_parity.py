"""DAG descriptor parity: PMX import → DAG → descriptors → physics world.

Verifies that the full pipeline (scene builder → DAG attrs → descriptor
extraction → typed physics world) produces identical simulation results
to the direct PMX bytes path.

Requires Maya (mayapy) and the native physics DLL with Bullet support.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
)
from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag
from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx
from mmd_tools.converters.physics_scene_builder import build_physics_scene

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _native_physics_available() -> bool:
    try:
        return is_native_physics_available()
    except Exception:
        return False


def _plugin_loaded() -> bool:
    try:
        return cmds.pluginInfo("mmd_tools", query=True, loaded=True)
    except Exception:
        return False


def _create_minimal_joints(bones) -> list[str]:
    """Create flat Maya joints at PMX bone rest positions."""
    joints = []
    for i, bone in enumerate(bones):
        name = f"bone_{i}"
        jnt = cmds.createNode("joint", name=name)
        cmds.xform(jnt, worldSpace=True, translation=list(bone.position))
        joints.append(jnt)
    return joints


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
class TestPhysicsDagParity(MayaTestBase):
    """Typed descriptor world from DAG must match PMX direct world."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ["MMD_TOOLS_PHYSICS_NODES"] = "1"

        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        if not _plugin_loaded():
            try:
                cmds.loadPlugin(plugin_path)
            except Exception:
                pass

        cls.pmx_bytes = FIXTURE_PATH.read_bytes()
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))
        cls.pmx_desc_set = build_descriptors_from_pmx(
            cls.pmx.rigid_bodies, cls.pmx.joints, cls.pmx.bones,
        )

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("MMD_TOOLS_PHYSICS_NODES", None)
        super().tearDownClass()

    def _build_dag_scene(self):
        """Import physics DAG from parsed PMX into current scene."""
        root = cmds.group(empty=True, name="test_model_root")
        maya_joints = _create_minimal_joints(self.pmx.bones)
        rb_transforms, jt_transforms = build_physics_scene(
            rigid_bodies=self.pmx.rigid_bodies,
            joints=self.pmx.joints,
            bones=self.pmx.bones,
            maya_joints=maya_joints,
            root_group=root,
        )
        return root, maya_joints, rb_transforms, jt_transforms

    def test_dag_descriptor_count_matches_pmx(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        self.assertEqual(len(dag_desc_set.rigid_bodies), len(self.pmx_desc_set.rigid_bodies))
        self.assertEqual(len(dag_desc_set.joints), len(self.pmx_desc_set.joints))

    def test_dag_descriptor_identity_matches_pmx(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        self.assertEqual(dag_desc_set.identity_hash, self.pmx_desc_set.identity_hash)

    def test_dag_rigid_body_fields_match_pmx(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        for i, (dag_rb, pmx_rb) in enumerate(
            zip(dag_desc_set.rigid_bodies, self.pmx_desc_set.rigid_bodies)
        ):
            self.assertEqual(dag_rb.shape, pmx_rb.shape, f"rb[{i}].shape")
            self.assertEqual(dag_rb.mode, pmx_rb.mode, f"rb[{i}].mode")
            self.assertEqual(dag_rb.bone_index, pmx_rb.bone_index, f"rb[{i}].bone_index")
            self.assertEqual(dag_rb.collision_group, pmx_rb.collision_group, f"rb[{i}].collision_group")
            self.assertEqual(dag_rb.collision_mask, pmx_rb.collision_mask, f"rb[{i}].collision_mask")
            for c in range(3):
                self.assertAlmostEqual(
                    dag_rb.position_xyz[c], pmx_rb.position_xyz[c], places=4,
                    msg=f"rb[{i}].position[{c}]",
                )
                self.assertAlmostEqual(
                    dag_rb.rotation_euler_xyz[c], pmx_rb.rotation_euler_xyz[c], places=4,
                    msg=f"rb[{i}].rotation[{c}]",
                )
                self.assertAlmostEqual(
                    dag_rb.shape_size[c], pmx_rb.shape_size[c], places=4,
                    msg=f"rb[{i}].shape_size[{c}]",
                )
            self.assertAlmostEqual(dag_rb.mass, pmx_rb.mass, places=4, msg=f"rb[{i}].mass")
            self.assertAlmostEqual(dag_rb.friction, pmx_rb.friction, places=4, msg=f"rb[{i}].friction")
            self.assertAlmostEqual(dag_rb.restitution, pmx_rb.restitution, places=4, msg=f"rb[{i}].restitution")
            self.assertAlmostEqual(dag_rb.linear_damping, pmx_rb.linear_damping, places=4, msg=f"rb[{i}].linear_damping")
            self.assertAlmostEqual(dag_rb.angular_damping, pmx_rb.angular_damping, places=4, msg=f"rb[{i}].angular_damping")
            for c in range(3):
                self.assertAlmostEqual(
                    dag_rb.body_from_bone_position_xyz[c], pmx_rb.body_from_bone_position_xyz[c],
                    places=4, msg=f"rb[{i}].bfb_pos[{c}]",
                )
                self.assertAlmostEqual(
                    dag_rb.bone_from_body_position_xyz[c], pmx_rb.bone_from_body_position_xyz[c],
                    places=4, msg=f"rb[{i}].bfr_pos[{c}]",
                )
            for c in range(4):
                self.assertAlmostEqual(
                    dag_rb.body_from_bone_rotation_xyzw[c], pmx_rb.body_from_bone_rotation_xyzw[c],
                    places=4, msg=f"rb[{i}].bfb_rot[{c}]",
                )
                self.assertAlmostEqual(
                    dag_rb.bone_from_body_rotation_xyzw[c], pmx_rb.bone_from_body_rotation_xyzw[c],
                    places=4, msg=f"rb[{i}].bfr_rot[{c}]",
                )

    def test_dag_joint_fields_match_pmx(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        for i, (dag_jt, pmx_jt) in enumerate(
            zip(dag_desc_set.joints, self.pmx_desc_set.joints)
        ):
            self.assertEqual(dag_jt.kind, pmx_jt.kind, f"jt[{i}].kind")
            self.assertEqual(dag_jt.rigidbody_a, pmx_jt.rigidbody_a, f"jt[{i}].rigidbody_a")
            self.assertEqual(dag_jt.rigidbody_b, pmx_jt.rigidbody_b, f"jt[{i}].rigidbody_b")
            for c in range(3):
                self.assertAlmostEqual(
                    dag_jt.position_xyz[c], pmx_jt.position_xyz[c], places=4,
                    msg=f"jt[{i}].position[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.rotation_euler_xyz[c], pmx_jt.rotation_euler_xyz[c], places=4,
                    msg=f"jt[{i}].rotation[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.translation_lower_limit_xyz[c], pmx_jt.translation_lower_limit_xyz[c],
                    places=4, msg=f"jt[{i}].trans_min[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.translation_upper_limit_xyz[c], pmx_jt.translation_upper_limit_xyz[c],
                    places=4, msg=f"jt[{i}].trans_max[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.rotation_lower_limit_xyz[c], pmx_jt.rotation_lower_limit_xyz[c],
                    places=4, msg=f"jt[{i}].rot_min[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.rotation_upper_limit_xyz[c], pmx_jt.rotation_upper_limit_xyz[c],
                    places=4, msg=f"jt[{i}].rot_max[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.spring_translation_factor_xyz[c], pmx_jt.spring_translation_factor_xyz[c],
                    places=4, msg=f"jt[{i}].spring_trans[{c}]",
                )
                self.assertAlmostEqual(
                    dag_jt.spring_rotation_factor_xyz[c], pmx_jt.spring_rotation_factor_xyz[c],
                    places=4, msg=f"jt[{i}].spring_rot[{c}]",
                )

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_dag_world_rigidbody_count_matches_pmx_world(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_desc_set.rigid_bodies, dag_desc_set.joints,
        )
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)

        self.assertEqual(dag_world.rigidbody_count(), pmx_world.rigidbody_count())
        self.assertEqual(dag_world.rigidbody_count(), 16)

        dag_world.free()
        pmx_world.free()

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_dag_world_initial_states_match_pmx_world(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_desc_set.rigid_bodies, dag_desc_set.joints,
        )
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)

        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        instance.evaluate_rest_pose()

        dag_world.reset(instance)
        pmx_world.reset(instance)

        dag_states = dag_world.copy_rigidbody_states()
        pmx_states = pmx_world.copy_rigidbody_states()
        self.assertEqual(len(dag_states), len(pmx_states))

        for body_idx, ((dag_pos, dag_rot), (pmx_pos, pmx_rot)) in enumerate(
            zip(dag_states, pmx_states)
        ):
            for c in range(3):
                self.assertAlmostEqual(
                    dag_pos[c], pmx_pos[c], places=5,
                    msg=f"initial: body[{body_idx}] pos[{c}]",
                )
            for c in range(4):
                self.assertAlmostEqual(
                    dag_rot[c], pmx_rot[c], places=5,
                    msg=f"initial: body[{body_idx}] rot[{c}]",
                )

        instance.free()
        model.free()
        dag_world.free()
        pmx_world.free()

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_dag_world_multi_step_matches_pmx_world(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )

        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_desc_set.rigid_bodies, dag_desc_set.joints,
        )
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)

        dag_states = self._run_steps(dag_world, num_steps=10)
        pmx_states = self._run_steps(pmx_world, num_steps=10)
        self.assertEqual(len(dag_states), len(pmx_states))

        for body_idx, ((dag_pos, dag_rot), (pmx_pos, pmx_rot)) in enumerate(
            zip(dag_states, pmx_states)
        ):
            for c in range(3):
                self.assertAlmostEqual(
                    dag_pos[c], pmx_pos[c], delta=0.005,
                    msg=f"10 steps: body[{body_idx}] pos[{c}]",
                )
            for c in range(4):
                self.assertAlmostEqual(
                    dag_rot[c], pmx_rot[c], delta=0.005,
                    msg=f"10 steps: body[{body_idx}] rot[{c}]",
                )

        dag_world.free()
        pmx_world.free()

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_dag_bone_world_matrices_match_pmx_after_stepping(self):
        """Bone world matrices must match after split-evaluation physics stepping."""
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_desc_set.rigid_bodies, dag_desc_set.joints,
        )
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)

        dag_matrices = self._run_split_eval_steps(dag_world, num_steps=10)
        pmx_matrices = self._run_split_eval_steps(pmx_world, num_steps=10)

        self.assertIsNotNone(dag_matrices, "DAG world matrices retrieval failed")
        self.assertIsNotNone(pmx_matrices, "PMX world matrices retrieval failed")
        self.assertEqual(len(dag_matrices), len(pmx_matrices))

        for bone_idx, (dag_mat, pmx_mat) in enumerate(zip(dag_matrices, pmx_matrices)):
            for c in range(16):
                self.assertAlmostEqual(
                    dag_mat[c], pmx_mat[c], delta=0.005,
                    msg=f"bone[{bone_idx}] matrix[{c}]",
                )

        dag_world.free()
        pmx_world.free()

    def _run_steps(self, world, num_steps=10):
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

    def _run_split_eval_steps(self, world, num_steps=10):
        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        instance.evaluate_rest_pose()
        world.reset(instance)

        dt = 1.0 / 60.0
        for step in range(num_steps):
            instance.evaluate_rest_pose()
            report = world.step_runtime(instance, dt)
            self.assertIsNotNone(report, f"split eval step {step} failed")
            ok = instance.evaluate_current_pose_after_physics()
            self.assertTrue(ok, f"evaluate_current_pose_after_physics failed at step {step}")

        matrices = instance.get_world_matrices()
        instance.free()
        model.free()
        return matrices


if __name__ == "__main__":
    unittest.main()
