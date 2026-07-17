"""Numerical oracles for the authoring collider transform contract."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from maya import cmds
import maya.api.OpenMaya as om

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.core.collider_authoring import set_collider_authoring_pose


class TestColliderAuthoringTransform(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin), query=True, loaded=True):
            cmds.loadPlugin(str(plugin))

    def test_parent_world_authoring_and_draw_matrices_match(self):
        parent = cmds.createNode("transform", name="colliderParent")
        cmds.setAttr(f"{parent}.translate", 3.0, -2.0, 5.0, type="double3")
        cmds.setAttr(f"{parent}.rotate", 13.0, 27.0, -9.0, type="double3")
        transform = cmds.createNode("transform", name="collider", parent=parent)
        shape = cmds.createNode("mmdRigidBodyShape", name="colliderShape", parent=transform)
        set_collider_authoring_pose(
            transform,
            shape,
            (1.25, 2.5, -0.75),
            tuple(math.radians(value) for value in (11.0, -22.0, 33.0)),
        )

        selection = om.MSelectionList()
        selection.add(shape)
        draw_matrix = selection.getDagPath(0).inclusiveMatrix()
        authoring_matrix = om.MMatrix(cmds.getAttr(f"{shape}.authoringMatrix"))
        world_matrix = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))

        for index in range(16):
            self.assertAlmostEqual(authoring_matrix[index], world_matrix[index], places=10)
            self.assertAlmostEqual(draw_matrix[index], world_matrix[index], places=10)

    def test_persisted_shape_pose_drives_transform_channels(self):
        transform = cmds.createNode("transform", name="editableCollider")
        shape = cmds.createNode("mmdRigidBodyShape", name="editableColliderShape", parent=transform)
        set_collider_authoring_pose(transform, shape, (1.0, 2.0, 3.0), (0.1, 0.2, 0.3))
        cmds.setAttr(f"{shape}.positionY", 9.5)
        cmds.setAttr(f"{shape}.rotationZ", -45.0)
        self.assertAlmostEqual(cmds.getAttr(f"{transform}.translateY"), 9.5)
        self.assertAlmostEqual(cmds.getAttr(f"{transform}.rotateZ"), -45.0)

    def test_locator_world_bbox_matches_each_primitive(self):
        expected = {
            0: (-2.0, -2.0, -2.0, 2.0, 2.0, 2.0),
            1: (-2.0, -4.0, -6.0, 2.0, 4.0, 6.0),
            2: (-2.0, -4.0, -2.0, 2.0, 4.0, 2.0),
        }
        for shape_type in (0, 1, 2):
            transform = cmds.createNode("transform", name=f"bboxCollider{shape_type}")
            shape = cmds.createNode(
                "mmdRigidBodyShape", name=f"bboxCollider{shape_type}Shape", parent=transform
            )
            cmds.setAttr(f"{shape}.shapeType", shape_type)
            cmds.setAttr(f"{shape}.shapeSize", 2.0, 4.0, 6.0, type="double3")
            actual = cmds.exactWorldBoundingBox(transform)
            for actual_value, expected_value in zip(actual, expected[shape_type]):
                self.assertAlmostEqual(actual_value, expected_value, places=6)

    def test_rotated_box_draw_and_bbox_share_canonical_parent_transform(self):
        transform = cmds.createNode("transform", name="rotatedBox")
        shape = cmds.createNode("mmdRigidBodyShape", name="rotatedBoxShape", parent=transform)
        cmds.setAttr(f"{shape}.shapeType", 1)
        cmds.setAttr(f"{shape}.shapeSize", 2.0, 4.0, 6.0, type="double3")
        set_collider_authoring_pose(transform, shape, (0.0, 0.0, 0.0), (0.0, 0.0, math.pi / 2.0))

        self.assertTrue(cmds.isConnected(f"{transform}.worldMatrix[0]", f"{shape}.authoringMatrix"))
        actual = cmds.exactWorldBoundingBox(transform)
        expected = (-4.0, -2.0, -6.0, 4.0, 2.0, 6.0)
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, places=6)


if __name__ == "__main__":
    unittest.main()
