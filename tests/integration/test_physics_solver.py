"""Integration test for PhysicsSolverSession — full DAG-to-joint pipeline.

Verifies that the Python physics solver creates a working session from
DAG descriptors and PMX bytes, steps the simulation, and writes bone
world matrices back to Maya joints with observable position changes.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_PMX_REST_POSITION,
)
from mmd_tools.core.coordinate_transform import mmd_point_to_maya
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available
from mmd_tools.core.physics_solver import PhysicsSolverSession
from mmd_tools.converters.physics_scene_builder import build_physics_scene

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _native_physics_available() -> bool:
    try:
        return is_native_physics_available()
    except Exception:
        return False


def _create_minimal_joints(bones, scale=1.0, parent=None) -> list[str]:
    joints = []
    for i, bone in enumerate(bones):
        jnt = cmds.createNode("joint", name=f"bone_{i}", parent=parent)
        cmds.xform(
            jnt,
            worldSpace=True,
            translation=list(mmd_point_to_maya(bone.position, scale)),
        )
        maya_attribute_utils.set_custom_attributes(
            jnt,
            {
                ATTR_MMD_BONE_INDEX: i,
                ATTR_MMD_BONE_PARENT_INDEX: bone.parent_bone_index,
                ATTR_MMD_PMX_REST_POSITION: bone.position,
                ATTR_MMD_BONE_FLAGS: 0,
                ATTR_MMD_DEFORM_LAYER: 0,
            },
        )
        joints.append(jnt)
    return joints


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestPhysicsSolverSession(MayaTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass
        cls.pmx_bytes = FIXTURE_PATH.read_bytes()
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def _build_scene(self, scale=1.0):
        root = cmds.group(empty=True, name="test_solver_root")
        maya_attribute_utils.set_custom_attributes(root, {ATTR_MMD_IMPORT_SCALE: scale})
        maya_joints = _create_minimal_joints(self.pmx.bones, scale, parent=root)
        build_physics_scene(
            rigid_bodies=self.pmx.rigid_bodies,
            joints=self.pmx.joints,
            bones=self.pmx.bones,
            maya_joints=maya_joints,
            root_group=root,
            scale=scale,
        )
        return root, maya_joints

    def test_create_session(self):
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        self.assertIsNotNone(session)
        session.free()

    def test_create_scaled_scene_uses_dag_model_descriptors(self):
        """The public create path must preserve DAG model scale too."""
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
        )

        scale = 0.5
        root, maya_joints = self._build_scene(scale=scale)
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        self.assertIsNotNone(session)

        raw_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        raw_instance = MmdRuntimeInstance.for_model(raw_model)
        self.assertIsNotNone(raw_model)
        self.assertIsNotNone(raw_instance)
        try:
            self.assertTrue(session.reset())
            actual = session.get_bone_world_matrices()
            self.assertTrue(raw_instance.evaluate_rest_pose())
            raw = raw_instance.get_world_matrices()
            self.assertIsNotNone(actual)
            self.assertIsNotNone(raw)
            self.assertEqual(len(actual), len(raw))
            for bone_index, (actual_matrix, raw_matrix) in enumerate(zip(actual, raw)):
                for component in range(16):
                    expected = raw_matrix[component]
                    if component in (12, 13, 14):
                        expected *= scale
                    self.assertAlmostEqual(
                        actual_matrix[component], expected, delta=5e-3,
                        msg=f"bone[{bone_index}] matrix[{component}]",
                    )
        finally:
            raw_instance.free()
            raw_model.free()
            session.free()

    def test_reset_succeeds(self):
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        self.assertTrue(session.reset())
        session.free()

    def test_step_succeeds(self):
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        session.reset()
        self.assertTrue(session.step(dt=1.0 / 30.0))
        session.free()

    def test_scaled_scene_step_and_reset_restore_same_pose(self):
        root, maya_joints = self._build_scene(scale=1.5)
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        try:
            self.assertTrue(session.reset())
            initial = session.get_bone_world_matrices()
            self.assertIsNotNone(initial)
            for _ in range(30):
                self.assertTrue(session.step(dt=1.0 / 30.0))
            stepped = session.get_bone_world_matrices()
            self.assertIsNotNone(stepped)
            self.assertTrue(
                any(
                    abs(before[index] - after[index]) > 1.0e-4
                    for before, after in zip(initial, stepped)
                    for index in (12, 13, 14)
                ),
                "scaled physics simulation must advance before reset",
            )

            self.assertTrue(session.reset())
            restored = session.get_bone_world_matrices()
            self.assertIsNotNone(restored)
            for bone_index, (before, after) in enumerate(zip(initial, restored)):
                for component, (expected, actual) in enumerate(zip(before, after)):
                    self.assertAlmostEqual(
                        actual,
                        expected,
                        delta=5.0e-3,
                        msg=f"bone[{bone_index}] matrix[{component}]",
                    )
        finally:
            session.free()

    def test_get_bone_world_matrices_returns_data(self):
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        session.reset()
        session.step(dt=1.0 / 30.0)
        matrices = session.get_bone_world_matrices()
        self.assertIsNotNone(matrices)
        self.assertEqual(len(matrices), len(self.pmx.bones))
        session.free()

    def test_apply_to_joints_updates_positions(self):
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        session.reset()

        physics_bone_indices = set()
        for rb in self.pmx.rigid_bodies:
            if rb.physics_mode != 0 and 0 <= rb.related_bone_index < len(maya_joints):
                physics_bone_indices.add(rb.related_bone_index)

        if not physics_bone_indices:
            session.free()
            self.skipTest("No physics-driven bones in fixture")

        sample_idx = min(physics_bone_indices)
        sample_joint = maya_joints[sample_idx]
        pos_before = cmds.xform(sample_joint, query=True, worldSpace=True, translation=True)

        for _ in range(30):
            session.step(dt=1.0 / 30.0)

        updated = session.apply_to_joints()
        self.assertGreater(updated, 0)

        pos_after = cmds.xform(sample_joint, query=True, worldSpace=True, translation=True)
        displacement = sum((a - b) ** 2 for a, b in zip(pos_after, pos_before)) ** 0.5
        self.assertGreater(
            displacement, 0.001,
            f"Physics bone {sample_idx} should have moved after 30 steps "
            f"(before={pos_before}, after={pos_after})",
        )
        session.free()

    def test_multi_step_applies_gravity(self):
        """Physics bones should drop under gravity over multiple steps."""
        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        session.reset()

        physics_bone_indices = []
        for rb in self.pmx.rigid_bodies:
            if rb.physics_mode != 0 and 0 <= rb.related_bone_index < len(maya_joints):
                physics_bone_indices.append(rb.related_bone_index)
        if not physics_bone_indices:
            session.free()
            self.skipTest("No physics-driven bones")

        y_positions_before = {}
        for idx in physics_bone_indices:
            pos = cmds.xform(maya_joints[idx], query=True, worldSpace=True, translation=True)
            y_positions_before[idx] = pos[1]

        for _ in range(60):
            session.step(dt=1.0 / 30.0)
        session.apply_to_joints()

        dropped_count = 0
        for idx in physics_bone_indices:
            pos = cmds.xform(maya_joints[idx], query=True, worldSpace=True, translation=True)
            if pos[1] < y_positions_before[idx] - 0.001:
                dropped_count += 1

        self.assertGreater(
            dropped_count, 0,
            "At least some physics bones should drop under gravity",
        )
        session.free()

    def test_session_parity_with_pmx_world(self):
        """Solver session bone matrices must match direct PMX-bytes world."""
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )
        from mmd_tools.core.native.mmd_anim_runtime_types import (
            MMD_RUNTIME_PHYSICS_MODE_LIVE,
        )

        root, maya_joints = self._build_scene()
        session = PhysicsSolverSession.create(root, self.pmx_bytes, maya_joints)
        session.reset()
        for _ in range(10):
            session.step(dt=1.0 / 60.0)
        dag_matrices = session.get_bone_world_matrices()

        pmx_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        instance.evaluate_rest_pose()
        pmx_world.reset(instance)
        for _ in range(10):
            instance.evaluate_rest_pose()
            pmx_world.step_runtime(instance, 1.0 / 60.0)
            instance.evaluate_current_pose_after_physics()
        pmx_matrices = instance.get_world_matrices()

        self.assertIsNotNone(dag_matrices)
        self.assertIsNotNone(pmx_matrices)
        self.assertEqual(len(dag_matrices), len(pmx_matrices))

        for bone_idx in range(len(dag_matrices)):
            for c in range(16):
                self.assertAlmostEqual(
                    dag_matrices[bone_idx][c], pmx_matrices[bone_idx][c],
                    delta=0.005,
                    msg=f"bone[{bone_idx}] matrix[{c}]",
                )

        session.free()
        instance.free()
        model.free()
        pmx_world.free()

if __name__ == "__main__":
    unittest.main()
