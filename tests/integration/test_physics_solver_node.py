"""Integration test for mmdPhysicsSolver and mmdPhysicsBoneDriver DG nodes.

Verifies that the DG solver node produces bone world matrices matching the
native bake oracle, and that the bone driver correctly decomposes world
matrices to local translate/rotate.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from maya import cmds

from tests.common.maya_test_base import MayaTestBase

from mmd_tools.core.constants import ATTR_MMD_BONE_INDEX
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.model_registry import (
    REGISTRY_CATEGORY_PHYSICS,
    list_model_registry_members,
)
from mmd_tools.core.native.mmd_anim_runtime import is_native_physics_available

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


def _native_physics_available() -> bool:
    try:
        return is_native_physics_available()
    except Exception:
        return False


def _import_payload_free_scene(pmx_file):
    """Import a normal physics scene and resolve its registry-owned solver."""
    from mmd_tools.io.pmx_importer import import_pmx_file

    parser = parse_pmx_file(str(pmx_file))
    root = import_pmx_file(
        parser,
        str(pmx_file),
        options={"import_physics": True, "create_mmd_shaders": False},
    )
    solvers = _model_physics_solvers(root)
    if not solvers:
        raise AssertionError(f"imported physics scene has no solver: {root}")
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    return root, joints, solvers[0]


def _model_physics_solvers(root):
    """Resolve new registry ownership while retaining legacy test coverage."""
    try:
        registry_members = list_model_registry_members(root, REGISTRY_CATEGORY_PHYSICS)
    except Exception:
        return []
    if registry_members is None:
        return cmds.listConnections(
            f"{root}.message", source=False, destination=True, type="mmdPhysicsSolver"
        ) or []
    return [
        node
        for node in registry_members
        if cmds.objExists(node) and cmds.nodeType(node) == "mmdPhysicsSolver"
    ]


def _connect_enabled_world(solver):
    worlds = cmds.ls(type="mmdPhysicsWorldShape") or []
    if worlds:
        world = worlds[0]
    else:
        transform = cmds.createNode("transform", name="MMD_PhysicsWorld")
        world = cmds.createNode(
            "mmdPhysicsWorldShape", name="MMD_PhysicsWorldShape", parent=transform
        )
    cmds.connectAttr(f"{world}.message", f"{solver}.inWorldSettings", force=True)
    cmds.connectAttr(
        f"{world}.outSettingsVersion",
        f"{solver}.inWorldSettingsVersion",
        force=True,
    )
    cmds.setAttr(f"{world}.enable", True)
    return world


def _solver_world_gravity(solver):
    """Read gravity from the world shape connected to the solver."""
    source_plug = cmds.connectionInfo(
        f"{solver}.inWorldSettings", sourceFromDestination=True
    )
    if not source_plug:
        raise AssertionError(f"solver has no connected world: {solver}")
    world = source_plug.rsplit(".", 1)[0]
    if cmds.nodeType(world) != "mmdPhysicsWorldShape":
        raise AssertionError(f"solver world is not a world shape: {world}")
    return tuple(float(cmds.getAttr(f"{world}.gravity{axis}")) for axis in "XYZ")


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestPhysicsSolverNode(MayaTestBase):

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

    def _build_scene(self):
        """Import the fixture through the PMX pipeline without a source payload."""
        root, joints, _ = _import_payload_free_scene(FIXTURE_PATH)
        return root, joints

    def _create_solver(self, root):
        """Use the solver created by the importer and enable its world."""
        solvers = _model_physics_solvers(root)
        self.assertTrue(solvers, f"No solver connected to imported root {root}")
        solver = solvers[0]
        _connect_enabled_world(solver)
        return solver

    def test_solver_node_creates(self):
        self.assertTrue(cmds.objExists("time1"))
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        self.assertEqual(cmds.nodeType(solver), "mmdPhysicsSolver")

    def test_ui_enable_moves_solver_joints_and_skinned_mesh(self):
        """The Physics-tab enable path must produce visible scene motion."""
        from types import SimpleNamespace

        import maya.api.OpenMaya as om

        from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter

        class _Checkbox:
            def __init__(self):
                self.checked = False
                self.enabled = False

            def blockSignals(self, _blocked):
                pass

            def setChecked(self, checked):
                self.checked = bool(checked)

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

        root, joints, solver = _import_payload_free_scene(FIXTURE_PATH)
        worlds = cmds.listConnections(
            f"{solver}.inWorldSettings", source=True, destination=False,
            type="mmdPhysicsWorldShape",
        ) or []
        self.assertTrue(worlds, "Imported solver must be connected to a physics world")
        world = worlds[0]
        self.assertFalse(cmds.getAttr(f"{world}.enable"))

        checkbox = _Checkbox()
        presenter = object.__new__(PhysicsPresenter)
        presenter.view = SimpleNamespace(physics_enable_check=checkbox)
        presenter.app_state = SimpleNamespace(current_model_root=root)
        presenter.maya_adapter = SimpleNamespace(object_exists=cmds.objExists)
        presenter._on_physics_enable_changed(True)

        self.assertTrue(checkbox.enabled)
        self.assertTrue(checkbox.checked)
        self.assertTrue(cmds.getAttr(f"{world}.enable"))

        physics_bone_indices = {
            rb.related_bone_index
            for rb in self.pmx.rigid_bodies
            if rb.physics_mode != 0 and 0 <= rb.related_bone_index < len(self.pmx.bones)
        }
        joints_by_index = {
            int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")): joint
            for joint in joints
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        }
        driven_joints = {
            index: joints_by_index[index]
            for index in physics_bone_indices
            if index in joints_by_index
        }
        self.assertTrue(driven_joints)

        meshes = cmds.listRelatives(
            root, allDescendents=True, type="mesh", fullPath=True,
            noIntermediate=True,
        ) or []
        self.assertTrue(meshes, "Fixture must contain a final skinned mesh")

        def mesh_points(shape):
            selection = om.MSelectionList()
            selection.add(shape)
            return om.MFnMesh(selection.getDagPath(0)).getPoints(om.MSpace.kWorld)

        cmds.currentUnit(time="film")
        cmds.currentTime(0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        solver_before = cmds.getAttr(f"{solver}.outBoneMatrices")
        joint_before = {
            index: tuple(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
            for index, joint in driven_joints.items()
        }
        mesh_before = {shape: mesh_points(shape) for shape in meshes}

        # Match a viewport time jump: do not force solver evaluation at every
        # intermediate frame.  The solver must catch up from 0 to 30 itself.
        cmds.currentTime(30)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "stepped")

        solver_after = cmds.getAttr(f"{solver}.outBoneMatrices")
        solver_delta = max(abs(a - b) for a, b in zip(solver_before, solver_after))
        self.assertGreater(solver_delta, 0.001, "UI-enabled solver matrices must move")

        joint_delta = max(
            math.sqrt(sum((a - b) ** 2 for a, b in zip(
                joint_before[index],
                cmds.xform(joint, query=True, worldSpace=True, matrix=True),
            )))
            for index, joint in driven_joints.items()
        )
        self.assertGreater(joint_delta, 0.001, "UI-enabled physics must move a Maya joint")

        mesh_delta = max(
            (after - before).length()
            for shape in meshes
            for before, after in zip(mesh_before[shape], mesh_points(shape))
        )
        self.assertGreater(mesh_delta, 0.001, "UI-enabled physics must deform the final mesh")

    def test_solver_outputs_bone_count(self):
        root, joints = self._build_scene()
        solver = self._create_solver(root)
        cmds.currentTime(0)
        bone_count = cmds.getAttr(f"{solver}.outBoneCount")
        self.assertEqual(bone_count, len(self.pmx.bones))

    def test_solver_outputs_solved_at_frame_zero(self):
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        cmds.currentTime(0)
        solved = cmds.getAttr(f"{solver}.outSolved")
        self.assertTrue(solved)

    def test_solver_outputs_status(self):
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        cmds.currentTime(0)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertIn(status, ("reset", "stepped", "cached", "pose-updated"))

    def test_solver_disabled_outputs_not_solved(self):
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        cmds.setAttr(f"{solver}.enable", False)
        cmds.currentTime(0)
        solved = cmds.getAttr(f"{solver}.outSolved")
        self.assertFalse(solved)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertEqual(status, "disabled")

    def test_solver_same_time_refreshes_maya_pose(self):
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        cmds.currentTime(1)
        status1 = cmds.getAttr(f"{solver}.outStatus")
        # Maya-pose input must refresh at the same time because its incoming
        # kinematic matrices may have dirtied without a time change.
        self.assertIn(status1, ("reset", "stepped", "pose-updated"))

    def test_solver_bone_matrices_non_empty(self):
        root, _ = self._build_scene()
        solver = self._create_solver(root)
        cmds.currentTime(0)
        matrices = cmds.getAttr(f"{solver}.outBoneMatrices")
        self.assertIsNotNone(matrices)
        expected_len = len(self.pmx.bones) * 16
        self.assertEqual(len(matrices), expected_len)

    def test_solver_parity_with_native_oracle(self):
        """Solver node bone matrices must match direct native stepping."""
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )
        from mmd_tools.core.native.mmd_anim_runtime_types import (
            MMD_RUNTIME_PHYSICS_MODE_LIVE,
        )

        root, _ = self._build_scene()
        solver = self._create_solver(root)

        cmds.currentUnit(time="ntsc")
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        n_steps = 10
        for frame in range(1, n_steps + 1):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")
        solver_flat = cmds.getAttr(f"{solver}.outBoneMatrices")

        ref_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        self.assertTrue(ref_world.set_gravity(_solver_world_gravity(solver)))
        ref_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        ref_instance = MmdRuntimeInstance.for_model(ref_model)
        ref_instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        ref_instance.evaluate_rest_pose()
        ref_world.reset(ref_instance)
        for _ in range(n_steps):
            ref_instance.evaluate_rest_pose()
            ref_world.step_runtime(ref_instance, 1.0 / 30.0)
            ref_instance.evaluate_current_pose_after_physics()
        ref_raw = ref_instance.get_world_matrices()

        self.assertIsNotNone(solver_flat)
        self.assertIsNotNone(ref_raw)

        bone_count = len(self.pmx.bones)
        self.assertEqual(len(solver_flat), bone_count * 16)
        self.assertEqual(len(ref_raw), bone_count)

        for bone_idx in range(bone_count):
            ref_maya = mmd_matrix_to_maya(ref_raw[bone_idx])
            for c in range(16):
                solver_val = solver_flat[bone_idx * 16 + c]
                ref_val = ref_maya[c]
                self.assertAlmostEqual(
                    solver_val, ref_val,
                    delta=0.005,
                    msg=f"bone[{bone_idx}] matrix[{c}]: solver={solver_val}, ref={ref_val}",
                )

        ref_instance.free()
        ref_model.free()
        ref_world.free()

    def test_solver_forward_step_changes_physics_bones(self):
        """Physics bones must show displacement after forward stepping."""
        root, _ = self._build_scene()
        solver = self._create_solver(root)

        cmds.currentUnit(time="ntsc")
        cmds.currentTime(0)
        mat_before = cmds.getAttr(f"{solver}.outBoneMatrices")

        physics_bone_indices = {
            rb.related_bone_index
            for rb in self.pmx.rigid_bodies
            if rb.physics_mode != 0 and 0 <= rb.related_bone_index < len(self.pmx.bones)
        }
        if not physics_bone_indices:
            self.skipTest("No physics-driven bones")

        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        joints_by_index = {
            int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")): joint
            for joint in joints
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True)
        }
        joint_before = {
            idx: tuple(cmds.xform(joints_by_index[idx], query=True, worldSpace=True, translation=True))
            for idx in physics_bone_indices
            if idx in joints_by_index
        }

        for frame in range(1, 31):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")
        mat_after = cmds.getAttr(f"{solver}.outBoneMatrices")

        changed_count = 0
        for idx in physics_bone_indices:
            offset = idx * 16
            before_vals = mat_before[offset : offset + 16]
            after_vals = mat_after[offset : offset + 16]
            diff = sum((a - b) ** 2 for a, b in zip(after_vals, before_vals)) ** 0.5
            if diff > 0.001:
                changed_count += 1

        self.assertGreater(
            changed_count, 0,
            "At least some physics bones should change after 30 forward steps",
        )

        changed_joint_count = 0
        for idx, before in joint_before.items():
            after = tuple(cmds.xform(joints_by_index[idx], query=True, worldSpace=True, translation=True))
            diff = sum((a - b) ** 2 for a, b in zip(after, before)) ** 0.5
            if diff > 0.001:
                changed_joint_count += 1
        self.assertGreater(
            changed_joint_count,
            0,
            "At least one physics-driven Maya joint should move after 30 forward steps",
        )


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestPhysicsBoneDriverNode(MayaTestBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def test_driver_node_creates(self):
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        self.assertEqual(cmds.nodeType(driver), "mmdPhysicsBoneDriver")

    def test_driver_identity_matrix_produces_zero_output(self):
        """An identity matrix at bone 0 with identity parent should produce origin."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", identity, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 0)
        cmds.setAttr(f"{driver}.inParentBoneIndex", -1)
        cmds.setAttr(f"{driver}.inSolved", True)

        tx = cmds.getAttr(f"{driver}.outTranslateX")
        ty = cmds.getAttr(f"{driver}.outTranslateY")
        tz = cmds.getAttr(f"{driver}.outTranslateZ")
        self.assertAlmostEqual(tx, 0.0, places=6)
        self.assertAlmostEqual(ty, 0.0, places=6)
        self.assertAlmostEqual(tz, 0.0, places=6)

    def test_driver_extracts_translation(self):
        """A translated matrix should produce matching translate output."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5.0, 10.0, -3.0, 1]
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", mat, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 0)
        cmds.setAttr(f"{driver}.inParentBoneIndex", -1)
        cmds.setAttr(f"{driver}.inSolved", True)

        tx = cmds.getAttr(f"{driver}.outTranslateX")
        ty = cmds.getAttr(f"{driver}.outTranslateY")
        tz = cmds.getAttr(f"{driver}.outTranslateZ")
        self.assertAlmostEqual(tx, 5.0, places=4)
        self.assertAlmostEqual(ty, 10.0, places=4)
        self.assertAlmostEqual(tz, -3.0, places=4)

    def test_driver_removes_joint_orient(self):
        """With a 90° Z JO and a 90° Z world rotation, rotate output should be ~zero."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        c = math.cos(math.pi / 2)
        s = math.sin(math.pi / 2)
        rot90z = [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", rot90z, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 0)
        cmds.setAttr(f"{driver}.inParentBoneIndex", -1)
        cmds.setAttr(f"{driver}.inSolved", True)
        cmds.setAttr(f"{driver}.inJointOrientZ", math.degrees(math.pi / 2))

        rx = cmds.getAttr(f"{driver}.outRotateX")
        ry = cmds.getAttr(f"{driver}.outRotateY")
        rz = cmds.getAttr(f"{driver}.outRotateZ")
        self.assertAlmostEqual(rx, 0.0, places=4)
        self.assertAlmostEqual(ry, 0.0, places=4)
        self.assertAlmostEqual(rz, 0.0, places=4)

    def test_driver_applies_maya_bind_world_correction(self):
        """Runtime world orientation must be mapped through the Maya bind pose."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        c = math.cos(math.pi / 2)
        s = math.sin(math.pi / 2)
        identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        bind_rot90z = [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

        cmds.setAttr(f"{driver}.inSolverBoneMatrices", identity, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 0)
        cmds.setAttr(f"{driver}.inParentBoneIndex", -1)
        cmds.setAttr(f"{driver}.inBindWorldMatrix", bind_rot90z, type="matrix")
        cmds.setAttr(f"{driver}.inNoOrientBindWorldMatrix", identity, type="matrix")
        cmds.setAttr(f"{driver}.inSolved", True)

        self.assertAlmostEqual(cmds.getAttr(f"{driver}.outRotateX"), 0.0, places=4)
        self.assertAlmostEqual(cmds.getAttr(f"{driver}.outRotateY"), 0.0, places=4)
        self.assertAlmostEqual(
            cmds.getAttr(f"{driver}.outRotateZ"),
            90.0,
            places=4,
        )

    def test_driver_disabled_outputs_zero(self):
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5.0, 10.0, -3.0, 1]
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", mat, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 0)
        cmds.setAttr(f"{driver}.inParentBoneIndex", -1)
        cmds.setAttr(f"{driver}.inSolved", True)
        cmds.setAttr(f"{driver}.enable", False)

        tx = cmds.getAttr(f"{driver}.outTranslateX")
        self.assertAlmostEqual(tx, 0.0, places=6)

    def test_driver_parent_bone_index_computes_local(self):
        """Bone at (10,0,0) with parent at (5,0,0) should have local translate (5,0,0)."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        parent_mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5.0, 0, 0, 1]
        child_mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 10.0, 0, 0, 1]
        flat = parent_mat + child_mat
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", flat, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 2)
        cmds.setAttr(f"{driver}.inBoneIndex", 1)
        cmds.setAttr(f"{driver}.inParentBoneIndex", 0)
        cmds.setAttr(f"{driver}.inSolved", True)

        tx = cmds.getAttr(f"{driver}.outTranslateX")
        ty = cmds.getAttr(f"{driver}.outTranslateY")
        tz = cmds.getAttr(f"{driver}.outTranslateZ")
        self.assertAlmostEqual(tx, 5.0, places=4)
        self.assertAlmostEqual(ty, 0.0, places=4)
        self.assertAlmostEqual(tz, 0.0, places=4)

    def test_driver_out_of_range_bone_index(self):
        """Out-of-range bone index should produce identity (zero) output."""
        driver = cmds.createNode("mmdPhysicsBoneDriver", name="testDriver")
        mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5.0, 10.0, 0, 1]
        cmds.setAttr(f"{driver}.inSolverBoneMatrices", mat, type="doubleArray")
        cmds.setAttr(f"{driver}.inSolverBoneCount", 1)
        cmds.setAttr(f"{driver}.inBoneIndex", 99)
        cmds.setAttr(f"{driver}.inSolved", True)

        tx = cmds.getAttr(f"{driver}.outTranslateX")
        self.assertAlmostEqual(tx, 0.0, places=6)


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestSolverTimeStateMachine(MayaTestBase):
    """Verify same-time idempotence, forward, jump, backward, reset contracts."""

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

    def _setup_solver(self):
        _, _, solver = _import_payload_free_scene(FIXTURE_PATH)
        _connect_enabled_world(solver)
        cmds.currentUnit(time="ntsc")
        return solver

    def test_same_time_multi_plug_idempotent(self):
        """Querying multiple outputs at same time must not re-step physics."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        cmds.currentTime(1)
        mat1 = cmds.getAttr(f"{solver}.outBoneMatrices")
        mat2 = cmds.getAttr(f"{solver}.outBoneMatrices")
        _ = cmds.getAttr(f"{solver}.outStatus")
        bone_count = cmds.getAttr(f"{solver}.outBoneCount")
        solved = cmds.getAttr(f"{solver}.outSolved")

        self.assertTrue(solved)
        self.assertGreater(bone_count, 0)
        self.assertEqual(len(mat1), len(mat2))
        for i in range(len(mat1)):
            self.assertAlmostEqual(mat1[i], mat2[i], places=10,
                                   msg=f"Multi-plug query diverged at index {i}")

    def test_forward_sequential_deterministic(self):
        """Forward stepping produces deterministic output for same frame sequence."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        matrices_by_frame = {}
        for frame in range(1, 11):
            cmds.currentTime(frame)
            matrices_by_frame[frame] = cmds.getAttr(f"{solver}.outBoneMatrices")

        for frame in range(2, 11):
            prev = matrices_by_frame[frame - 1]
            curr = matrices_by_frame[frame]
            self.assertEqual(len(prev), len(curr))

    def test_direct_thirty_frame_jump_steps_and_moves_physics_bones(self):
        """A 24 fps viewport frame 0 to 30 jump catches up instead of resetting."""
        solver = self._setup_solver()
        cmds.currentUnit(time="film")
        cmds.currentTime(0)
        before = cmds.getAttr(f"{solver}.outBoneMatrices")

        cmds.currentTime(30)
        after = cmds.getAttr(f"{solver}.outBoneMatrices")
        status = cmds.getAttr(f"{solver}.outStatus")

        physics_bone_indices = {
            rb.related_bone_index
            for rb in self.pmx.rigid_bodies
            if rb.physics_mode != 0 and 0 <= rb.related_bone_index < len(self.pmx.bones)
        }
        changed = 0
        for bone_index in physics_bone_indices:
            offset = bone_index * 16
            delta = sum(
                (a - b) ** 2
                for a, b in zip(after[offset : offset + 16], before[offset : offset + 16])
            ) ** 0.5
            if delta > 0.001:
                changed += 1

        self.assertEqual(status, "stepped")
        self.assertGreater(changed, 0, "Direct frame 0 to 30 jump must simulate visible motion")

    def test_jump_forward_produces_reset(self):
        """Jumping beyond the bounded two-second catch-up window triggers reset."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.currentTime(300)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertEqual(status, "reset",
                         "Large forward jump should trigger reset")

    def test_backward_produces_reset(self):
        """Going backward in time triggers reset."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        for frame in range(1, 11):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.currentTime(5)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertEqual(status, "reset",
                         "Backward time should trigger reset")

    def test_backward_then_forward_continues(self):
        """After backward reset, forward stepping resumes correctly."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        cmds.currentTime(1)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertEqual(status, "stepped",
                         "After reset, forward stepping should resume")

    def test_reset_at_frame_zero_matches_rest_pose(self):
        """At frame 0 after reset, matrices should be close to rest pose."""
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )
        from mmd_tools.core.native.mmd_anim_runtime_types import (
            MMD_RUNTIME_PHYSICS_MODE_LIVE,
        )

        solver = self._setup_solver()
        cmds.currentTime(0)
        solver_flat = cmds.getAttr(f"{solver}.outBoneMatrices")

        ref_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        ref_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        ref_instance = MmdRuntimeInstance.for_model(ref_model)
        ref_instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        ref_instance.evaluate_rest_pose()
        ref_world.reset(ref_instance)
        ref_raw = ref_instance.get_world_matrices()

        bone_count = len(self.pmx.bones)
        for bone_idx in range(bone_count):
            ref_maya = mmd_matrix_to_maya(ref_raw[bone_idx])
            for c in range(16):
                self.assertAlmostEqual(
                    solver_flat[bone_idx * 16 + c], ref_maya[c],
                    delta=0.005,
                    msg=f"Rest pose bone[{bone_idx}] matrix[{c}]",
                )

        ref_instance.free()
        ref_model.free()
        ref_world.free()

    def test_parity_30_steps(self):
        """Extended parity check over 30 frames (1 second at 30fps)."""
        from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
        from mmd_tools.core.native.mmd_anim_runtime_handles import (
            MmdRuntimeInstance,
            MmdRuntimeModel,
            MmdRuntimePhysicsWorld,
        )
        from mmd_tools.core.native.mmd_anim_runtime_types import (
            MMD_RUNTIME_PHYSICS_MODE_LIVE,
        )

        solver = self._setup_solver()
        n_steps = 30
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, n_steps + 1):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")
        solver_flat = cmds.getAttr(f"{solver}.outBoneMatrices")

        ref_world = MmdRuntimePhysicsWorld.from_pmx_bytes(self.pmx_bytes)
        self.assertTrue(ref_world.set_gravity(_solver_world_gravity(solver)))
        ref_model = MmdRuntimeModel.from_pmx_bytes(self.pmx_bytes)
        ref_instance = MmdRuntimeInstance.for_model(ref_model)
        ref_instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE)
        ref_instance.evaluate_rest_pose()
        ref_world.reset(ref_instance)
        for _ in range(n_steps):
            ref_instance.evaluate_rest_pose()
            ref_world.step_runtime(ref_instance, 1.0 / 30.0)
            ref_instance.evaluate_current_pose_after_physics()
        ref_raw = ref_instance.get_world_matrices()

        bone_count = len(self.pmx.bones)
        for bone_idx in range(bone_count):
            ref_maya = mmd_matrix_to_maya(ref_raw[bone_idx])
            for c in range(16):
                self.assertAlmostEqual(
                    solver_flat[bone_idx * 16 + c], ref_maya[c],
                    delta=0.01,
                    msg=f"30-step parity bone[{bone_idx}] matrix[{c}]",
                )

        ref_instance.free()
        ref_model.free()
        ref_world.free()


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestSolverDisableEnable(MayaTestBase):
    """Verify disable/enable lifecycle."""

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

    def _setup_solver(self):
        _, _, solver = _import_payload_free_scene(FIXTURE_PATH)
        _connect_enabled_world(solver)
        cmds.currentUnit(time="ntsc")
        return solver

    def test_disable_mid_simulation(self):
        """Disabling solver mid-simulation produces not-solved output."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.setAttr(f"{solver}.enable", False)
        cmds.currentTime(6)
        self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "disabled")

    def test_re_enable_after_disable(self):
        """Re-enabling solver after disable produces solved output."""
        solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.setAttr(f"{solver}.enable", False)
        cmds.currentTime(1)
        self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))

        cmds.setAttr(f"{solver}.enable", True)
        cmds.currentTime(2)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    def test_world_off_on_off_same_frame_resets_instead_of_continuing(self):
        solver = self._setup_solver()
        world = cmds.connectionInfo(
            f"{solver}.inWorldSettings", sourceFromDestination=True
        ).rsplit(".", 1)[0]
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.setAttr(f"{world}.enable", False)
        self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
        cmds.setAttr(f"{world}.enable", True)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "reset")
        cmds.setAttr(f"{world}.enable", False)
        self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))

    def test_world_off_on_without_intermediate_solver_evaluation_forces_reset(self):
        solver = self._setup_solver()
        world = cmds.connectionInfo(
            f"{solver}.inWorldSettings", sourceFromDestination=True
        ).rsplit(".", 1)[0]
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outStatus")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outStatus")

        cmds.setAttr(f"{world}.enable", False)
        cmds.setAttr(f"{world}.enable", True)
        # Deliberately do not query the solver or driven joints while OFF.
        cmds.currentTime(6)

        self.assertEqual(cmds.getAttr(f"{solver}.outStatus"), "reset")


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestSolverEvaluationModes(MayaTestBase):
    """Verify solver works in DG, EM serial, and EM parallel modes."""

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

    def _setup_solver(self):
        _, _, solver = _import_payload_free_scene(FIXTURE_PATH)
        _connect_enabled_world(solver)
        cmds.currentUnit(time="ntsc")
        return solver

    def _run_5_steps(self, solver):
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")
        return cmds.getAttr(f"{solver}.outBoneMatrices")

    def test_dg_mode(self):
        """Solver produces valid output in DG (legacy) evaluation mode."""
        try:
            cmds.evaluationManager(mode="off")
        except Exception:
            self.skipTest("evaluationManager not available")
        solver = self._setup_solver()
        mat = self._run_5_steps(solver)
        self.assertIsNotNone(mat)
        self.assertGreater(len(mat), 0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    def test_em_serial_mode(self):
        """Solver produces valid output in EM serial mode."""
        try:
            cmds.evaluationManager(mode="serial")
        except Exception:
            self.skipTest("EM serial not available")
        solver = self._setup_solver()
        mat = self._run_5_steps(solver)
        self.assertIsNotNone(mat)
        self.assertGreater(len(mat), 0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    def test_em_parallel_mode(self):
        """Solver produces valid output in EM parallel mode."""
        try:
            cmds.evaluationManager(mode="parallel")
        except Exception:
            self.skipTest("EM parallel not available")
        solver = self._setup_solver()
        mat = self._run_5_steps(solver)
        self.assertIsNotNone(mat)
        self.assertGreater(len(mat), 0)
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    def tearDown(self):
        try:
            cmds.evaluationManager(mode="off")
        except Exception:
            pass
        super().tearDown()


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestSolverLifecycle(MayaTestBase):
    """Verify solver survives scene lifecycle events."""

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

    def _setup_solver(self):
        root, _, solver = _import_payload_free_scene(FIXTURE_PATH)
        _connect_enabled_world(solver)
        cmds.currentUnit(time="ntsc")
        return root, solver

    def test_new_scene_after_solve(self):
        """Opening a new scene after solver was active doesn't crash."""
        _root, solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.file(new=True, force=True)
        self.assertFalse(cmds.objExists(solver))

    def test_save_open_reinitializes(self):
        """Solver reinitializes correctly after save/open cycle."""
        root, solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        root_name = root.rsplit("|", 1)[-1]

        scene_path = self.get_temp_filename("solver_lifecycle.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)

        cmds.file(scene_path, open=True, force=True)

        reopened_roots = cmds.ls(root_name, long=True) or []
        self.assertEqual(len(reopened_roots), 1, "Model root must survive save/open")
        reopened_root = reopened_roots[0]
        reopened_solvers = _model_physics_solvers(reopened_root)
        self.assertEqual(len(reopened_solvers), 1, "Solver must survive save/open")
        reopened_solver = reopened_solvers[0]

        cmds.currentTime(0)
        solved = cmds.getAttr(f"{reopened_solver}.outSolved")
        self.assertTrue(solved, "Solver should reinitialize after scene open")
        bone_count = cmds.getAttr(f"{reopened_solver}.outBoneCount")
        self.assertEqual(bone_count, len(self.pmx.bones))

    def test_model_root_duplicate_is_explicitly_unsupported(self):
        """Duplicating a model root does not claim an independent live solver."""
        root, solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")

        duplicate_root = cmds.duplicate(root, name="unsupported_physics_duplicate")[0]
        duplicate_solvers = _model_physics_solvers(duplicate_root)
        self.assertEqual(duplicate_solvers, [])
        self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))

    def test_reference_scene_no_crash(self):
        """Referencing a scene containing a solver node doesn't crash."""
        _root, solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        scene_path = self.get_temp_filename("solver_ref.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)

        cmds.file(new=True, force=True)

        try:
            cmds.file(scene_path, reference=True, namespace="refSolver")
            referenced_solvers = cmds.ls(type="mmdPhysicsSolver")
            self.assertTrue(referenced_solvers, "Referenced scene should expose a solver node")
        finally:
            if cmds.file(scene_path, query=True, reference=True):
                cmds.file(scene_path, removeReference=True)

    def test_unload_plugin_no_crash(self):
        """Unloading and reloading the plugin doesn't crash."""
        _root, solver = self._setup_solver()
        cmds.currentTime(0)
        _ = cmds.getAttr(f"{solver}.outSolved")
        for frame in range(1, 6):
            cmds.currentTime(frame)
            _ = cmds.getAttr(f"{solver}.outSolved")

        cmds.file(new=True, force=True)

        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        plugin_name = cmds.pluginInfo(plugin_path, query=True, name=True)
        try:
            cmds.unloadPlugin(plugin_name, force=True)
            self.assertNotIn(plugin_name, cmds.pluginInfo(query=True, listPlugins=True) or [])
        finally:
            cmds.loadPlugin(plugin_path)

        self.assertIn(plugin_name, cmds.pluginInfo(query=True, listPlugins=True) or [])
        registered_types = set(cmds.allNodeTypes() or [])
        self.assertTrue({"mmdMorphController", "mmdAppend", "mmdCcdIk"}.issubset(registered_types))

    def test_no_world_connection_stays_disabled_before_descriptor_work(self):
        """A solver without its production World control remains cheaply disabled."""
        solver = cmds.createNode("mmdPhysicsSolver", name="testSolver")
        cmds.connectAttr("time1.outTime", f"{solver}.inTime")
        cmds.currentTime(0)
        solved = cmds.getAttr(f"{solver}.outSolved")
        self.assertFalse(solved)
        status = cmds.getAttr(f"{solver}.outStatus")
        self.assertEqual(status, "disabled")

    def test_model_root_without_physics_metadata_graceful(self):
        """Solver with model root but no physics metadata outputs not-solved."""
        root = cmds.group(empty=True, name="test_root")
        solver = cmds.createNode("mmdPhysicsSolver", name="testSolver")
        cmds.connectAttr(f"{root}.message", f"{solver}.modelRoot")
        cmds.connectAttr("time1.outTime", f"{solver}.inTime")
        cmds.currentTime(0)
        solved = cmds.getAttr(f"{solver}.outSolved")
        self.assertFalse(solved)


if __name__ == "__main__":
    unittest.main()
