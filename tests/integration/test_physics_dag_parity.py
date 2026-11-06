"""DAG descriptor parity against the same unmodified PMX in the native reader.

Compound body orientation is also checked against a fixed independent oracle.
Requires Maya and the native physics DLL with Bullet support.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.constants import ATTR_MMD_PMX_REST_POSITION
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeInstance,
    MmdRuntimeModel,
    MmdRuntimePhysicsWorld,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
)
from mmd_tools.core.model_dag_descriptor import build_model_descriptors_from_dag
from mmd_tools.core.physics_dag_descriptor import build_descriptors_from_dag
from mmd_tools.core.physics_descriptor import build_descriptors_from_pmx
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from mmd_tools.converters.physics_scene_builder import build_physics_scene

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"
RESET_STATE_MAX_COMPONENT_DELTA = 1e-3


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


def _create_minimal_joints(bones, scale=1.0) -> list[str]:
    """Create flat Maya joints at PMX bone rest positions."""
    joints = []
    for i, bone in enumerate(bones):
        name = f"bone_{i}"
        jnt = cmds.createNode("joint", name=name)
        cmds.xform(
            jnt,
            worldSpace=True,
            translation=list(mmd_point_to_maya(bone.position, scale)),
        )
        joints.append(jnt)
    return joints


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
class TestPhysicsDagParity(MayaTestBase):
    """DAG world must match the raw PMX native reference."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

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
        super().tearDownClass()

    def _build_dag_scene(self, scale=1.0):
        """Import physics DAG from parsed PMX into current scene."""
        root = cmds.group(empty=True, name="test_model_root")
        maya_joints = _create_minimal_joints(self.pmx.bones, scale)
        rb_transforms, jt_transforms = build_physics_scene(
            rigid_bodies=self.pmx.rigid_bodies,
            joints=self.pmx.joints,
            bones=self.pmx.bones,
            maya_joints=maya_joints,
            root_group=root,
            scale=scale,
        )
        return root, maya_joints, rb_transforms, jt_transforms

    def test_dag_descriptor_count_matches_pmx(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        self.assertEqual(len(dag_desc_set.rigid_bodies), len(self.pmx_desc_set.rigid_bodies))
        self.assertEqual(len(dag_desc_set.joints), len(self.pmx_desc_set.joints))

    def test_namespaced_dag_descriptor_count_matches_pmx(self):
        cmds.namespace(add="Citlali")
        cmds.namespace(set="Citlali")
        try:
            root, maya_joints, _, _ = self._build_dag_scene()
        finally:
            cmds.namespace(set=":")

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

    def test_non_contiguous_pmx_indices_map_to_dense_native_slots(self):
        root, maya_joints, rb_transforms, jt_transforms = self._build_dag_scene()
        source_to_shifted = {}
        for source_index, transform in enumerate(rb_transforms):
            shape = cmds.listRelatives(transform, shapes=True, type="mmdRigidBodyShape")[0]
            shifted_index = source_index * 2 + 10
            source_to_shifted[source_index] = shifted_index
            cmds.setAttr(f"{shape}.pmxIndex", shifted_index)

        for transform in jt_transforms:
            shape = cmds.listRelatives(transform, shapes=True, type="mmdPhysicsJointShape")[0]
            for attr in ("rigidBodyAIndex", "rigidBodyBIndex"):
                source_index = cmds.getAttr(f"{shape}.{attr}")
                if source_index >= 0:
                    cmds.setAttr(f"{shape}.{attr}", source_to_shifted[source_index])

        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        valid_pmx_joints = [
            joint for joint in self.pmx.joints
            if joint.rigid_body_a_index >= 0 and joint.rigid_body_b_index >= 0
        ]
        self.assertEqual(len(dag_desc_set.joints), len(valid_pmx_joints))
        for dag_joint, pmx_joint in zip(dag_desc_set.joints, valid_pmx_joints):
            self.assertEqual(dag_joint.rigidbody_a, pmx_joint.rigid_body_a_index)
            self.assertEqual(dag_joint.rigidbody_b, pmx_joint.rigid_body_b_index)

    def test_scaled_dag_physics_descriptors_use_effective_linear_data_only(self):
        scale = 0.5
        root, maya_joints, rb_transforms, _ = self._build_dag_scene(scale=scale)
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )

        for dag_rb, pmx_rb in zip(dag_desc_set.rigid_bodies, self.pmx_desc_set.rigid_bodies):
            for component in range(3):
                self.assertAlmostEqual(
                    dag_rb.shape_size[component], pmx_rb.shape_size[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_rb.position_xyz[component], pmx_rb.position_xyz[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_rb.body_from_bone_position_xyz[component],
                    pmx_rb.body_from_bone_position_xyz[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_rb.bone_from_body_position_xyz[component],
                    pmx_rb.bone_from_body_position_xyz[component] * scale,
                )
            for component in range(3):
                self.assertAlmostEqual(
                    dag_rb.rotation_euler_xyz[component], pmx_rb.rotation_euler_xyz[component],
                )

        valid_pmx_joints = [
            joint for joint in self.pmx.joints
            if joint.rigid_body_a_index >= 0 and joint.rigid_body_b_index >= 0
        ]
        for dag_joint, pmx_joint in zip(dag_desc_set.joints, valid_pmx_joints):
            for component in range(3):
                self.assertAlmostEqual(
                    dag_joint.position_xyz[component], pmx_joint.position[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_joint.translation_lower_limit_xyz[component],
                    pmx_joint.translation_limit_min[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_joint.translation_upper_limit_xyz[component],
                    pmx_joint.translation_limit_max[component] * scale,
                )
                self.assertAlmostEqual(
                    dag_joint.spring_translation_factor_xyz[component],
                    pmx_joint.spring_translation[component],
                )

        shape = cmds.listRelatives(
            rb_transforms[0], shapes=True, type="mmdRigidBodyShape"
        )[0]
        self.assertEqual(
            tuple(cmds.getAttr(f"{shape}.shapeSize")[0]),
            tuple(value * scale for value in self.pmx.rigid_bodies[0].size),
        )

    def test_missing_rigid_body_reference_fails_closed(self):
        root, maya_joints, _rb_transforms, jt_transforms = self._build_dag_scene()
        shape = cmds.listRelatives(
            jt_transforms[4], shapes=True, type="mmdPhysicsJointShape"
        )[0]
        cmds.setAttr(f"{shape}.rigidBodyAIndex", 9999)

        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        self.assertFalse(dag_desc_set.is_valid)
        self.assertTrue(any(
            error.kind == "joint" and error.field == "rigidbody_a_pmx_index"
            for error in dag_desc_set.validation_errors
        ))

    def test_dag_descriptor_uses_persisted_rest_position_not_current_pose(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        for joint, bone in zip(maya_joints, self.pmx.bones):
            cmds.addAttr(joint, longName=ATTR_MMD_PMX_REST_POSITION, attributeType="double3")
            for axis in "XYZ":
                cmds.addAttr(
                    joint,
                    longName=f"{ATTR_MMD_PMX_REST_POSITION}{axis}",
                    attributeType="double",
                    parent=ATTR_MMD_PMX_REST_POSITION,
                )
            cmds.setAttr(
                f"{joint}.{ATTR_MMD_PMX_REST_POSITION}",
                *bone.position,
                type="double3",
            )
            cmds.xform(joint, worldSpace=True, translation=[p + 10.0 for p in bone.position])

        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        for index, (dag_rb, pmx_rb) in enumerate(
            zip(dag_desc_set.rigid_bodies, self.pmx_desc_set.rigid_bodies)
        ):
            for component in range(3):
                self.assertAlmostEqual(
                    dag_rb.body_from_bone_position_xyz[component],
                    pmx_rb.body_from_bone_position_xyz[component],
                    places=4,
                    msg=f"rb[{index}].body_from_bone_position[{component}]",
                )

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
    def test_dag_model_rest_world_matches_pmx_direct_before_physics(self):
        """Compare independent PMX/direct and payload-free DAG model rest poses."""
        from mmd_tools.io.pmx_importer import import_pmx_file

        root = import_pmx_file(
            self.pmx,
            str(FIXTURE_PATH),
            options={"import_physics": True, "create_mmd_shaders": False},
        )
        self.assertTrue(root)
        descriptors = build_model_descriptors_from_dag(root)
        pmx_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        dag_model = MmdRuntimeModel.from_descriptors(descriptors)
        self.assertIsNotNone(pmx_model)
        self.assertIsNotNone(dag_model)
        pmx_instance = MmdRuntimeInstance.for_model(pmx_model)
        dag_instance = MmdRuntimeInstance.for_model(dag_model)
        self.assertIsNotNone(pmx_instance)
        self.assertIsNotNone(dag_instance)
        try:
            self.assertTrue(pmx_instance.evaluate_rest_pose())
            self.assertTrue(dag_instance.evaluate_rest_pose())
            pmx_matrices = pmx_instance.get_world_matrices()
            dag_matrices = dag_instance.get_world_matrices()
            self.assertIsNotNone(pmx_matrices)
            self.assertIsNotNone(dag_matrices)
            self.assertEqual(len(pmx_matrices), len(dag_matrices))
            differences = []
            for bone_index, (pmx_matrix, dag_matrix) in enumerate(
                zip(pmx_matrices, dag_matrices)
            ):
                for component, (pmx_value, dag_value) in enumerate(
                    zip(pmx_matrix, dag_matrix)
                ):
                    delta = abs(pmx_value - dag_value)
                    if delta != 0.0:
                        differences.append((delta, bone_index, component, pmx_value, dag_value))
            if differences:
                differences.sort(reverse=True)
                self.fail(
                    "pure rest model mismatch; largest differences: "
                    f"{differences[:8]}"
                )
        finally:
            dag_instance.free()
            pmx_instance.free()
            dag_model.free()
            pmx_model.free()

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_scaled_import_initial_and_reset_static_states_match_reference(self):
        """Initial bodies and reset static bodies retain the imported scale."""
        from mmd_tools.io.pmx_importer import import_pmx_file
        from mmd_tools.core.physics_solver import _collect_bone_joints

        scale = 0.5
        root = import_pmx_file(
            self.pmx,
            str(FIXTURE_PATH),
            scale=scale,
            options={"import_physics": True, "create_mmd_shaders": False},
        )
        self.assertTrue(root)
        bone_joints = _collect_bone_joints(root)
        dag_physics = build_descriptors_from_dag(
            root, bone_joints=bone_joints, bone_count=len(bone_joints),
        )
        dag_model_desc = build_model_descriptors_from_dag(root)
        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_physics.rigid_bodies, dag_physics.joints,
        )
        dag_model = MmdRuntimeModel.from_descriptors(dag_model_desc)
        dag_instance = MmdRuntimeInstance.for_model(dag_model)
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        pmx_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        pmx_instance = MmdRuntimeInstance.for_model(pmx_model)
        self.assertIsNotNone(dag_world)
        self.assertIsNotNone(dag_model)
        self.assertIsNotNone(dag_instance)
        self.assertIsNotNone(pmx_world)
        self.assertIsNotNone(pmx_model)
        self.assertIsNotNone(pmx_instance)
        try:
            self.assertTrue(dag_instance.evaluate_rest_pose())
            self.assertTrue(pmx_instance.evaluate_rest_pose())
            # reset_runtime_physics settles for 1/60 s before dynamic readback.
            # Equal gravity does not give scale-proportional dynamic displacement;
            # compare all initial bodies, then only bone-driven bodies after reset.
            # Dynamic reset replay is covered by the solver session tests.
            for reset in (False, True):
                if reset:
                    self.assertIsNotNone(dag_world.reset(dag_instance))
                    self.assertIsNotNone(pmx_world.reset(pmx_instance))
                dag_states = dag_world.copy_rigidbody_states()
                pmx_states = pmx_world.copy_rigidbody_states()
                self.assertEqual(len(dag_states), len(pmx_states))
                for body_index, ((dag_pos, dag_rot), (pmx_pos, pmx_rot)) in enumerate(
                    zip(dag_states, pmx_states)
                ):
                    if reset and self.pmx.rigid_bodies[body_index].physics_mode != 0:
                        continue
                    for component in range(3):
                        self.assertAlmostEqual(
                            dag_pos[component], pmx_pos[component] * scale,
                            delta=0.005,
                            msg=f"body[{body_index}] scaled position[{component}]",
                        )
                    for component in range(4):
                        self.assertAlmostEqual(
                            dag_rot[component], pmx_rot[component],
                            delta=0.005,
                            msg=f"body[{body_index}] rotation[{component}]",
                        )
        finally:
            dag_instance.free()
            dag_model.free()
            dag_world.free()
            pmx_instance.free()
            pmx_model.free()
            pmx_world.free()

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
    def test_dag_world_initial_states_match_raw_pmx_reference(self):
        root, maya_joints, _, _ = self._build_dag_scene()
        dag_desc_set = build_descriptors_from_dag(
            root, bone_joints=maya_joints, bone_count=len(self.pmx.bones),
        )
        dag_world = MmdRuntimePhysicsWorld.from_descriptors(
            dag_desc_set.rigid_bodies, dag_desc_set.joints,
        )
        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)

        # Keep each reset on an independent runtime instance.  Reusing one
        # instance can hide state leakage between the descriptor and direct
        # worlds, while the release gate is intended to compare fresh resets.
        dag_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        dag_instance = MmdRuntimeInstance.for_model(dag_model)
        pmx_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        pmx_instance = MmdRuntimeInstance.for_model(pmx_model)
        dag_instance.evaluate_rest_pose()
        pmx_instance.evaluate_rest_pose()

        dag_world.reset(dag_instance)
        pmx_world.reset(pmx_instance)

        dag_states = dag_world.copy_rigidbody_states()
        pmx_states = pmx_world.copy_rigidbody_states()
        self.assertEqual(len(dag_states), len(pmx_states))

        for body_idx, ((dag_pos, dag_rot), (pmx_pos, pmx_rot)) in enumerate(
            zip(dag_states, pmx_states)
        ):
            for c in range(3):
                self.assertAlmostEqual(
                    dag_pos[c], pmx_pos[c], delta=RESET_STATE_MAX_COMPONENT_DELTA,
                    msg=f"initial reset position delta body[{body_idx}] component[{c}]",
                )
            for c in range(4):
                self.assertAlmostEqual(
                    dag_rot[c], pmx_rot[c], delta=RESET_STATE_MAX_COMPONENT_DELTA,
                    msg=f"initial reset rotation delta body[{body_idx}] component[{c}]",
                )

        dag_instance.free()
        dag_model.free()
        pmx_instance.free()
        pmx_model.free()
        dag_world.free()
        pmx_world.free()

    @unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
    def test_dag_world_multi_step_matches_raw_pmx_reference(self):
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
    def test_dag_bone_world_matrices_match_raw_pmx_reference_after_stepping(self):
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

    def _run_split_eval_steps(self, world, num_steps=10, tick_config=None):
        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        if tick_config is not None:
            self.assertTrue(instance.set_physics_tick_config(*tick_config))
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


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestCompoundColliderNativeContract(MayaTestBase):
    """Independent PMX body Ry*Rx*Rz contract, independent of native parsing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_plugin(str(Path(__file__).resolve().parents[2] / 'mmd_tools/plugin_main.py'))

    @staticmethod
    def fixture(scale):
        from types import SimpleNamespace
        root = cmds.createNode('transform', name='CompoundColliderModel')
        bone = cmds.createNode('joint', name='ColliderBone', parent=root)
        cmds.setAttr(bone + '.translate', scale, 2 * scale, -3 * scale)
        bodies = [SimpleNamespace(name=name, name_english=name, shape_type=i, size=size,
                  position=(3 + i * 7, 4, 5), rotation=(.23, -.41, .67), physics_mode=0,
                  mass=0., velocity_attenuation=.1, rotation_attenuation=.1,
                  friction=.5, elasticity=0., group=1, collision_mask=0, related_bone_index=0)
                  for i, (name, size) in enumerate([('sphere', (1.2, 0, 0)),
                  ('box', (1.1, 2.3, .7)), ('capsule', (.8, 2.6, 0))])]
        constraint = SimpleNamespace(name='frame', name_english='frame', joint_type=0,
                     position=(5, 6, 7), rotation=(.17, -.29, .51),
                     translation_limit_min=(0, 0, 0), translation_limit_max=(0, 0, 0),
                     rotation_limit_min=(-.1, -.2, -.3), rotation_limit_max=(.1, .2, .3),
                     spring_translation=(0, 0, 0), spring_rotation=(0, 0, 0),
                     rigid_body_a_index=0, rigid_body_b_index=1)
        transforms, _ = build_physics_scene(rigid_bodies=bodies, joints=[constraint], bones=[],
                                           maya_joints=[bone], root_group=root, scale=scale)
        return root, bone, bodies, transforms

    def verify(self, root, bone, bodies, transforms, scale):
        import math
        from ctypes import c_float
        from maya.api import OpenMaya as om
        from mmd_tools.converters.physics_export_collector import collect_physics_from_scene
        from mmd_tools.core.collider_geometry import collider_half_extents

        def multiply(a, b):
            x, y, z, w = a
            X, Y, Z, W = b
            return (w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
                    w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z)

        x, y, z = bodies[0].rotation
        # Hamilton product from Saba's explicit Ry * Rx * Rz body matrix.
        expected_q = multiply(multiply((0, math.sin(y/2), 0, math.cos(y/2)),
                                      (math.sin(x/2), 0, 0, math.cos(x/2))),
                              (0, 0, math.sin(z/2), math.cos(z/2)))
        descriptors = build_descriptors_from_dag(root, bone_joints=[bone], bone_count=1)
        self.assertTrue(descriptors.is_valid)
        exports, joint_exports = collect_physics_from_scene(root, {bone: 0})
        # Joint frames retain the existing Bullet ZYX contract; body conversion
        # must not silently apply the body's YXZ convention to constraints.
        self.assertEqual(len(descriptors.joints), 1)
        self.assertEqual(list(descriptors.joints[0].rotation_euler_xyz),
                         [c_float(v).value for v in (.17, -.29, .51)])
        self.assertListAlmostEqual(joint_exports[0]['rotation'], (.17, -.29, .51))
        world = MmdRuntimePhysicsWorld.from_descriptors(descriptors.rigid_bodies, [])
        self.assertIsNotNone(world)
        try:
            states = world.copy_rigidbody_states()
            self.assertEqual(len(states), 3)
            for body, transform, descriptor, state, exported in zip(
                    bodies, transforms, descriptors.rigid_bodies, states, exports):
                position, rotation = state
                self.assertLess(1-abs(sum(a*b for a,b in zip(rotation,expected_q))), 1e-6)
                shape = cmds.listRelatives(transform, shapes=True, fullPath=True)[0]
                self.assertListAlmostEqual(cmds.getAttr(shape+'.rotation')[0],
                                           [math.degrees(v) for v in body.rotation])
                self.assertListAlmostEqual(exported['rotation'], body.rotation)
                self.assertEqual(list(descriptor.shape_size), [c_float(v*scale).value for v in body.size])
                selection = om.MSelectionList()
                selection.add(shape)
                dag = selection.getDagPath(0)
                bbox = om.MFnDagNode(dag).boundingBox
                self.assertListAlmostEqual([bbox.width/2,bbox.height/2,bbox.depth/2],
                                           collider_half_extents(body.shape_type, descriptor.shape_size), places=5)
                native = om.MTransformationMatrix()
                native.setRotation(om.MQuaternion(-rotation[0],-rotation[1],rotation[2],rotation[3]))
                native.setTranslation(om.MVector(position[0],position[1],-position[2]),om.MSpace.kTransform)
                self.assertLess(max(abs(a-b) for a,b in zip(dag.inclusiveMatrix(),native.asMatrix())),1e-5)
                self.assertListAlmostEqual(descriptor.body_from_bone_position_xyz,
                                           [(body.position[i]-(1,2,3)[i])*scale for i in range(3)],places=5)
                self.assertLess(1-abs(sum(a*b for a,b in zip(descriptor.body_from_bone_rotation_xyzw,expected_q))),1e-6)
                # Inverse offset composed with the body offset must be identity.
                q = om.MQuaternion(*descriptor.body_from_bone_rotation_xyzw)
                inverse_position = om.MVector(*descriptor.bone_from_body_position_xyz).rotateBy(q)
                self.assertLess((inverse_position+om.MVector(*descriptor.body_from_bone_position_xyz)).length(),1e-5)
        finally:
            world.free()

    def test_three_shapes_three_scales_match_native_and_preserve_raw_export(self):
        for scale in (.5, 1., 10.):
            with self.subTest(scale=scale):
                cmds.file(new=True, force=True)
                self.verify(*self.fixture(scale), scale)

    def test_raw_native_and_descriptors_match_fixed_mmd_orientation(self):
        expected = (0.16635528384310905, 0.13165804153184718,
                    0.6064593322922748, 0.7662892129692844)
        pmx = parse_pmx_file(str(FIXTURE_PATH))
        self.assertEqual(tuple(pmx.rigid_bodies[0].rotation),
                         (.09540669620037079, .41741126775741577, 1.3591997623443604))
        descriptors = build_descriptors_from_pmx(pmx.rigid_bodies, pmx.joints, pmx.bones)
        adapted = MmdRuntimePhysicsWorld.from_descriptors(descriptors.rigid_bodies, descriptors.joints)
        native = MmdRuntimePhysicsWorld.from_pmx_bytes(FIXTURE_PATH.read_bytes())
        self.assertIsNotNone(adapted)
        self.assertIsNotNone(native)
        try:
            actual = adapted.copy_rigidbody_states()[0][1]
            raw = native.copy_rigidbody_states()[0][1]
            self.assertLess(max(abs(a-b) for a, b in zip(actual, expected)), 1e-6)
            self.assertLess(max(abs(a-b) for a, b in zip(raw, expected)), 1e-6)
        finally:
            adapted.free()
            native.free()

    def test_compound_collider_undo_and_ascii_roundtrip(self):
        import tempfile
        root, bone, bodies, transforms = self.fixture(.5)
        cmds.undoInfo(state=True)
        cmds.setAttr(transforms[1]+'.translateX', 99.)
        cmds.undo()
        self.verify(root, bone, bodies, transforms, .5)
        with tempfile.TemporaryDirectory() as directory:
            scene = str(Path(directory)/'compound.ma')
            cmds.file(rename=scene)
            cmds.file(save=True, type='mayaAscii', force=True)
            cmds.file(new=True, force=True)
            cmds.file(scene, open=True, force=True)
            self.verify(root, bone, bodies, transforms, .5)


if __name__ == "__main__":
    unittest.main()
