#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for the maya.cmds pass-through adapter."""

import unittest
from unittest.mock import Mock

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter


class TestMayaCmdsAdapter(unittest.TestCase):
    """Verify MayaCmdsAdapter delegates calls without changing arguments."""

    def setUp(self):
        self.cmds = Mock()
        self.adapter = MayaCmdsAdapter(cmds_module=self.cmds)

    def test_new_scene_delegates_force_true(self):
        expected = object()
        self.cmds.file.return_value = expected

        result = self.adapter.new_scene(force=True)

        self.cmds.file.assert_called_once_with(new=True, force=True)
        self.assertIs(result, expected)

    def test_new_scene_delegates_force_false(self):
        expected = object()
        self.cmds.file.return_value = expected

        result = self.adapter.new_scene(force=False)

        self.cmds.file.assert_called_once_with(new=True, force=False)
        self.assertIs(result, expected)

    def test_object_exists_delegates_node(self):
        self.cmds.objExists.return_value = True

        result = self.adapter.object_exists("pCube1")

        self.cmds.objExists.assert_called_once_with("pCube1")
        self.assertIs(result, True)

    def test_ls_delegates_args_and_kwargs(self):
        expected = ["root"]
        self.cmds.ls.return_value = expected

        result = self.adapter.ls("*", type="transform")

        self.cmds.ls.assert_called_once_with("*", type="transform")
        self.assertIs(result, expected)

    def test_attribute_exists_delegates_attr_and_node(self):
        self.cmds.attributeQuery.return_value = True

        result = self.adapter.attribute_exists("translateX", "pCube1")

        self.cmds.attributeQuery.assert_called_once_with("translateX", node="pCube1", exists=True)
        self.assertIs(result, True)

    def test_get_attr_delegates_attr_path(self):
        self.cmds.getAttr.return_value = 3.0

        result = self.adapter.get_attr("pCube1.translateX")

        self.cmds.getAttr.assert_called_once_with("pCube1.translateX")
        self.assertEqual(result, 3.0)

    def test_list_relatives_delegates_kwargs(self):
        expected = ["root|pCube1"]
        self.cmds.listRelatives.return_value = expected

        result = self.adapter.list_relatives("pCubeShape1", parent=True, fullPath=True)

        self.cmds.listRelatives.assert_called_once_with("pCubeShape1", parent=True, fullPath=True)
        self.assertIs(result, expected)

    def test_poly_evaluate_delegates_vertex_true(self):
        self.cmds.polyEvaluate.return_value = 24

        result = self.adapter.poly_evaluate("pCubeShape1", vertex=True)

        self.cmds.polyEvaluate.assert_called_once_with("pCubeShape1", vertex=True)
        self.assertEqual(result, 24)

    def test_list_connections_delegates_kwargs(self):
        expected = ["initialShadingGroup"]
        self.cmds.listConnections.return_value = expected

        result = self.adapter.list_connections("pCubeShape1", type="shadingEngine")

        self.cmds.listConnections.assert_called_once_with("pCubeShape1", type="shadingEngine")
        self.assertIs(result, expected)

    def test_list_history_delegates_shapes(self):
        expected = ["polyCube1"]
        self.cmds.listHistory.return_value = expected

        result = self.adapter.list_history(["pCubeShape1"])

        self.cmds.listHistory.assert_called_once_with(["pCubeShape1"])
        self.assertIs(result, expected)

    def test_blend_shape_delegates_kwargs(self):
        expected = ["smile"]
        self.cmds.blendShape.return_value = expected

        result = self.adapter.blend_shape("faceBlendShape", query=True, target=True)

        self.cmds.blendShape.assert_called_once_with("faceBlendShape", query=True, target=True)
        self.assertIs(result, expected)

    def test_select_delegates_replace_true(self):
        expected = None
        self.cmds.select.return_value = expected

        result = self.adapter.select(["pCube1"], replace=True)

        self.cmds.select.assert_called_once_with(["pCube1"], replace=True)
        self.assertIs(result, expected)

    def test_select_delegates_replace_false(self):
        expected = None
        self.cmds.select.return_value = expected

        result = self.adapter.select(["pCube1"], replace=False)

        self.cmds.select.assert_called_once_with(["pCube1"], replace=False)
        self.assertIs(result, expected)


if __name__ == "__main__":
    unittest.main()
