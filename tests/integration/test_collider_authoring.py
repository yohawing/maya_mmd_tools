"""Numerical oracles for the authoring collider transform contract."""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from maya import cmds
import maya.api.OpenMaya as om

from tests.common.maya_test_base import MayaTestBase
from tests.common.maya_coordinate_oracle import reflected_mmd_euler_matrix
from mmd_tools.core.collider_authoring import (
    connect_collider_authoring_follow,
    set_collider_authoring_pose,
)
from mmd_tools.core.coordinate_transform import (
    mmd_matrix_to_maya,
    mmd_point_to_maya,
)
from mmd_tools.nodes.mmd_rigid_body_draw_override import _draw_box


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

    def test_raw_pmx_pose_is_preserved_while_unbound_display_uses_maya_space(self):
        transform = cmds.createNode("transform", name="editableCollider")
        shape = cmds.createNode("mmdRigidBodyShape", name="editableColliderShape", parent=transform)
        position = (1.25, 2.5, 3.75)
        rotation = (0.1, -0.2, 0.3)
        display_scale = 2.5
        set_collider_authoring_pose(
            transform,
            shape,
            position,
            rotation,
            display_scale,
        )

        self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], position)
        self.assertListAlmostEqual(
            cmds.getAttr(f"{shape}.rotation")[0],
            [math.degrees(value) for value in rotation],
        )
        self.assertListAlmostEqual(
            cmds.getAttr(f"{transform}.translate")[0],
            mmd_point_to_maya(position, display_scale),
        )
        self.assertListAlmostEqual(
            cmds.getAttr(f"{transform}.rotate")[0],
            [-math.degrees(rotation[0]), -math.degrees(rotation[1]), math.degrees(rotation[2])],
        )
        self.assertListAlmostEqual(
            cmds.getAttr(f"{transform}.scale")[0],
            (display_scale, display_scale, display_scale),
        )
        for shape_attr, transform_attr in (
            ("positionX", "translateX"),
            ("positionY", "translateY"),
            ("positionZ", "translateZ"),
            ("rotationX", "rotateX"),
            ("rotationY", "rotateY"),
            ("rotationZ", "rotateZ"),
        ):
            self.assertFalse(
                cmds.isConnected(f"{shape}.{shape_attr}", f"{transform}.{transform_attr}")
            )

    def test_display_rotation_matches_independent_z_reflection_matrix_oracle(self):
        rotations = (
            (0.37, 0.0, 0.0),
            (0.0, -0.61, 0.0),
            (0.0, 0.0, 0.83),
            (0.37, -0.61, 0.83),
        )

        for index, rotation in enumerate(rotations):
            with self.subTest(rotation=rotation):
                transform = cmds.createNode("transform", name=f"reflectedCollider{index}")
                shape = cmds.createNode(
                    "mmdRigidBodyShape",
                    name=f"reflectedCollider{index}Shape",
                    parent=transform,
                )
                set_collider_authoring_pose(transform, shape, (0.0, 0.0, 0.0), rotation)

                mmd_matrix = om.MEulerRotation(*rotation).asMatrix()
                expected_matrix = reflected_mmd_euler_matrix(rotation)
                expected = [float(expected_matrix[element]) for element in range(16)]

                actual = cmds.xform(transform, query=True, objectSpace=True, matrix=True)
                max_error = max(abs(actual[element] - expected[element]) for element in range(16))
                self.assertLessEqual(max_error, 1.0e-10)

                converted_matrix = mmd_matrix_to_maya(
                    [float(mmd_matrix[element]) for element in range(16)]
                )
                quaternion = om.MEulerRotation(*rotation).asQuaternion()
                reflected_quaternion_matrix = om.MQuaternion(
                    -quaternion.x,
                    -quaternion.y,
                    quaternion.z,
                    quaternion.w,
                ).asMatrix()
                for element in range(16):
                    self.assertAlmostEqual(converted_matrix[element], expected[element], places=10)
                    self.assertAlmostEqual(
                        reflected_quaternion_matrix[element], expected[element], places=10
                    )

    def test_bound_collider_keeps_bone_offset_through_animation_and_reopen(self):
        model = cmds.createNode("transform", name="followModel")
        master = cmds.createNode("transform", name="followMaster", parent=model)
        bone = cmds.createNode("joint", name="followBone", parent=master)
        physics = cmds.createNode("transform", name="followPhysics", parent=model)
        transform = cmds.createNode("transform", name="followCollider", parent=physics)
        shape = cmds.createNode("mmdRigidBodyShape", name="followColliderShape", parent=transform)
        cmds.setAttr(f"{shape}.shapeType", 1)
        cmds.setAttr(f"{shape}.shapeSize", 0.5, 1.0, 1.5, type="double3")
        position = (2.0, 4.0, 6.0)
        rotation = (0.15, -0.25, 0.35)
        set_collider_authoring_pose(transform, shape, position, rotation)
        cmds.connectAttr(f"{bone}.message", f"{shape}.relatedBone")
        constraint = connect_collider_authoring_follow(transform, shape)
        self.assertTrue(constraint)

        def relative_matrix():
            collider_world = om.MMatrix(cmds.xform(transform, query=True, worldSpace=True, matrix=True))
            bone_world = om.MMatrix(cmds.xform(bone, query=True, worldSpace=True, matrix=True))
            return collider_world * bone_world.inverse()

        def set_pose(node, frame, translate, rotate):
            cmds.currentTime(frame)
            cmds.setAttr(f"{node}.translate", *translate, type="double3")
            cmds.setAttr(f"{node}.rotate", *rotate, type="double3")
            cmds.setKeyframe(node, attribute="translate")
            cmds.setKeyframe(node, attribute="rotate")

        set_pose(model, 1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        set_pose(master, 1, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        set_pose(bone, 1, (0.5, 1.0, -1.5), (0.0, 0.0, 0.0))
        cmds.currentTime(1)
        rest_offset = relative_matrix()

        set_pose(model, 12, (3.0, -2.0, 1.0), (0.0, 15.0, 0.0))
        set_pose(master, 12, (-1.0, 2.0, 4.0), (5.0, -10.0, 20.0))
        set_pose(bone, 12, (2.5, -0.5, 3.0), (-12.0, 18.0, 27.0))
        cmds.currentTime(12)
        animated_offset = relative_matrix()
        for index in range(16):
            self.assertAlmostEqual(animated_offset[index], rest_offset[index], places=6)

        bbox = cmds.exactWorldBoundingBox(transform)
        bbox_center = tuple((bbox[axis] + bbox[axis + 3]) * 0.5 for axis in range(3))
        world_position = cmds.xform(transform, query=True, worldSpace=True, translation=True)
        self.assertListAlmostEqual(bbox_center, world_position, places=5)
        self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], position)
        self.assertListAlmostEqual(
            cmds.getAttr(f"{shape}.rotation")[0],
            [math.degrees(value) for value in rotation],
        )

        scene_path = self.get_temp_filename("collider_follow_reopen.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        transform = "followCollider"
        shape = "followColliderShape"
        bone = "followBone"
        self.assertEqual(connect_collider_authoring_follow(transform, shape), constraint)
        cmds.currentTime(1)
        reopen_rest = relative_matrix()
        cmds.currentTime(12)
        reopen_animated = relative_matrix()
        for index in range(16):
            self.assertAlmostEqual(reopen_animated[index], reopen_rest[index], places=6)
        self.assertListAlmostEqual(cmds.getAttr(f"{shape}.position")[0], position)

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

    def test_box_draw_uses_pmx_half_extents_on_matching_local_axes(self):
        class DrawManagerProbe:
            def __init__(self):
                self.args = None

            def box(self, *args):
                self.args = args

        manager = DrawManagerProbe()
        center = om.MPoint(0.0, 0.0, 0.0)
        x_axis = om.MVector(1.0, 0.0, 0.0)
        y_axis = om.MVector(0.0, 1.0, 0.0)

        _draw_box(manager, center, x_axis, y_axis, (1.0, 2.0, 3.0))

        self.assertIsNotNone(manager.args)
        self.assertEqual(manager.args[1], y_axis)
        self.assertEqual(manager.args[2], x_axis)
        self.assertEqual(manager.args[3:6], (1.0, 2.0, 3.0))
        self.assertFalse(manager.args[6])

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
