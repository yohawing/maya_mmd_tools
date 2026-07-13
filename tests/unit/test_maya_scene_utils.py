"""Unit tests for direct Maya scene utility helpers."""

import unittest

from maya import cmds

from mmd_tools.core import maya_scene_utils
from tests.common.maya_test_base import MayaTestBase


class TestMayaSceneUtils(MayaTestBase):
    def test_select_objects(self):
        cube1 = cmds.polyCube(name="scene_utils_test_cube1")[0]
        cube2 = cmds.polyCube(name="scene_utils_test_cube2")[0]

        self.assertTrue(maya_scene_utils.select_objects(clear=True))
        self.assertEqual(cmds.ls(selection=True), [])

        self.assertTrue(maya_scene_utils.select_objects(cube1))
        self.assertEqual(cmds.ls(selection=True), [cube1])

        self.assertTrue(maya_scene_utils.select_objects(cube2, add=True, clear=False, replace=False))
        selected = cmds.ls(selection=True)
        self.assertIn(cube1, selected)
        self.assertIn(cube2, selected)

    def test_object_exists(self):
        cube = cmds.polyCube(name="scene_utils_exists_cube")[0]
        self.assertTrue(maya_scene_utils.object_exists(cube))

        cmds.delete(cube)
        self.assertFalse(maya_scene_utils.object_exists(cube))
        self.assertFalse(maya_scene_utils.object_exists("scene_utils_missing_cube"))

    def test_parent_objects(self):
        parent = cmds.group(empty=True, name="scene_utils_parent")
        child = cmds.polyCube(name="scene_utils_child")[0]

        result = maya_scene_utils.parent_objects(child, parent)

        self.assertEqual(len(result), 1)
        self.assertEqual(cmds.listRelatives(child, parent=True)[0], parent)

        result = maya_scene_utils.parent_objects(child, world=True)

        self.assertEqual(len(result), 1)
        self.assertIsNone(cmds.listRelatives(child, parent=True))

    def test_list_objects(self):
        cmds.file(new=True, force=True)
        cmds.select(clear=True)
        cmds.joint(name="scene_utils_joint1")
        cmds.joint(name="scene_utils_joint2")
        cmds.polyCube(name="scene_utils_cube")

        joints = maya_scene_utils.list_objects(type="joint")
        joint_names = [joint.split("|")[-1] for joint in joints]
        self.assertIn("scene_utils_joint1", joint_names)
        self.assertIn("scene_utils_joint2", joint_names)

        filtered = maya_scene_utils.list_objects(object_filter="*scene_utils_cube*")
        filtered_names = [node.split("|")[-1] for node in filtered]
        self.assertIn("scene_utils_cube", filtered_names)


if __name__ == "__main__":
    unittest.main()
