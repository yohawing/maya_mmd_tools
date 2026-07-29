"""Maya integration parity gates for transactional physics pre-roll."""

from __future__ import annotations

import unittest
from pathlib import Path

import maya.api.OpenMaya as om
from maya import cmds

from mmd_tools.core import physics_preroll
from mmd_tools.nodes import mmd_physics_solver_node
from tests.common.maya_test_base import MayaTestBase
from tests.integration.test_physics_solver_node import (
    FIXTURE_PATH,
    _import_payload_free_scene,
    _native_physics_available,
)


def _matrix_values(value):
    matrix = value if isinstance(value, om.MMatrix) else om.MMatrix(value)
    return tuple(
        float(matrix.getElement(row, column))
        for row in range(4)
        for column in range(4)
    )


def _matrix_residual(left, right):
    return max(abs(a - b) for a, b in zip(left, right))


def _skin_products(joints):
    """Capture JO-aware skin deformation matrices keyed by influence plug."""
    products = {}
    joint_set = set(cmds.ls(joints, long=True) or joints)
    for skin in cmds.ls(type="skinCluster") or []:
        for index in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{index}]",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            if not sources:
                continue
            source_node = str(sources[0]).split(".", 1)[0]
            joint = (cmds.ls(source_node, long=True) or [source_node])[0]
            if joint not in joint_set:
                continue
            bind_pre = om.MMatrix(cmds.getAttr(f"{skin}.bindPreMatrix[{index}]"))
            world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
            products[f"{skin}.matrix[{index}]"] = _matrix_values(bind_pre * world)
    return products


def _rigid_body_cache():
    return {
        path: _matrix_values(matrix)
        for path, matrix in mmd_physics_solver_node._SIMULATED_RB_CACHE.items()
    }


@unittest.skipUnless(FIXTURE_PATH.exists(), "hair physics fixture not found")
@unittest.skipUnless(_native_physics_available(), "native physics DLL not available")
class TestPhysicsPrerollParity(MayaTestBase):
    """Compare nonzero enable pre-roll with ordinary frame-by-frame playback."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = str(Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py")
        try:
            cmds.loadPlugin(plugin_path)
        except Exception:
            pass

    def test_preroll_matches_continuous_playback_in_all_evaluation_modes(self):
        root, joints, solver = _import_payload_free_scene(FIXTURE_PATH)
        world = (cmds.listConnections(
            f"{solver}.inWorldSettings",
            source=True,
            destination=False,
            type="mmdPhysicsWorldShape",
        ) or [None])[0]
        self.assertIsNotNone(world)
        cmds.currentUnit(time="ntsc")
        cmds.setAttr(f"{world}.startFrame", 0)
        cmds.select(root, replace=True)

        for evaluation_mode in ("off", "serial", "parallel"):
            with self.subTest(evaluation_mode=evaluation_mode):
                cmds.evaluationManager(mode=evaluation_mode)
                cmds.setAttr(f"{world}.enable", False)
                self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
                cmds.currentTime(0, edit=True)
                physics_preroll._invalidate_solver_runtime([solver])

                generation = int(cmds.getAttr(f"{world}.resetGeneration"))
                cmds.setAttr(f"{world}.resetGeneration", generation + 1)
                cmds.setAttr(f"{world}.enable", True)
                for frame in range(0, 11):
                    cmds.currentTime(frame, edit=True)
                    self.assertTrue(cmds.getAttr(f"{solver}.outSolved"))
                continuous_solver = tuple(cmds.getAttr(f"{solver}.outBoneMatrices"))
                continuous_rigid = _rigid_body_cache()
                continuous_skin = _skin_products(joints)
                self.assertTrue(continuous_rigid)
                self.assertTrue(continuous_skin)

                cmds.setAttr(f"{world}.enable", False)
                self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
                cmds.currentTime(0, edit=True)
                self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))
                cmds.currentTime(10, edit=True)
                self.assertFalse(cmds.getAttr(f"{solver}.outSolved"))

                result = physics_preroll.run_physics_preroll(world, [solver])
                self.assertEqual(result.start_frame, 0.0)
                self.assertEqual(result.target_frame, 10.0)
                self.assertEqual(result.step_count, 10)
                self.assertEqual(cmds.currentTime(query=True), 10.0)
                self.assertEqual(cmds.evaluationManager(query=True, mode=True)[0], evaluation_mode)
                root_long = (cmds.ls(root, long=True) or [root])[0]
                self.assertEqual(cmds.ls(selection=True, long=True), [root_long])

                preroll_solver = tuple(cmds.getAttr(f"{solver}.outBoneMatrices"))
                preroll_rigid = _rigid_body_cache()
                preroll_skin = _skin_products(joints)
                self.assertLessEqual(_matrix_residual(continuous_solver, preroll_solver), 1.0e-8)
                self.assertEqual(set(preroll_rigid), set(continuous_rigid))
                self.assertEqual(set(preroll_skin), set(continuous_skin))
                for key in continuous_rigid:
                    self.assertLessEqual(
                        _matrix_residual(continuous_rigid[key], preroll_rigid[key]),
                        1.0e-8,
                        key,
                    )
                for key in continuous_skin:
                    self.assertLessEqual(
                        _matrix_residual(continuous_skin[key], preroll_skin[key]),
                        1.0e-8,
                        key,
                    )

    def tearDown(self):
        try:
            cmds.evaluationManager(mode="off")
        except Exception:
            pass
        super().tearDown()


if __name__ == "__main__":
    unittest.main()
