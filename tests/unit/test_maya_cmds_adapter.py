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

    def test_reference_query_delegates_read_only_probe(self):
        self.cmds.referenceQuery.return_value = True

        result = self.adapter.reference_query("|model|joint", isNodeReferenced=True)

        self.cmds.referenceQuery.assert_called_once_with(
            "|model|joint", isNodeReferenced=True
        )
        self.assertTrue(result)

    def test_current_time_delegates_query_without_kwargs_on_adapter(self):
        self.cmds.currentTime.return_value = 24

        result = self.adapter.current_time()

        self.cmds.currentTime.assert_called_once_with(query=True)
        self.assertEqual(result, 24)

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

    def test_attribute_range_queries_optional_min_and_max(self):
        def attribute_query(_attr, node=None, **kwargs):
            self.assertEqual(node, "pCube1")
            if kwargs.get("minExists") or kwargs.get("maxExists"):
                return True
            if kwargs.get("minimum"):
                return [0.0]
            if kwargs.get("maximum"):
                return [1.0]
            return False

        self.cmds.attributeQuery.side_effect = attribute_query

        self.assertEqual(self.adapter.attribute_range("friction", "pCube1"), (0.0, 1.0))

    def test_get_attr_delegates_attr_path(self):
        self.cmds.getAttr.return_value = 3.0

        result = self.adapter.get_attr("pCube1.translateX")

        self.cmds.getAttr.assert_called_once_with("pCube1.translateX")
        self.assertEqual(result, 3.0)

    def test_get_attr_forwards_multi_indices_query(self):
        self.cmds.getAttr.return_value = [5500, 6000]

        result = self.adapter.get_attr("blendShape1.inputTargetItem", multiIndices=True)

        self.cmds.getAttr.assert_called_once_with(
            "blendShape1.inputTargetItem",
            multiIndices=True,
        )
        self.assertEqual(result, [5500, 6000])

    def test_is_attr_settable_queries_get_attr(self):
        self.cmds.getAttr.return_value = True

        self.assertTrue(self.adapter.is_attr_settable("pCube1.translateX"))
        self.cmds.getAttr.assert_called_once_with("pCube1.translateX", settable=True)

    def test_set_attr_delegates_args_and_kwargs(self):
        expected = None
        self.cmds.setAttr.return_value = expected

        result = self.adapter.set_attr("pCube1.translateX", 3.0, lock=True)

        self.cmds.setAttr.assert_called_once_with("pCube1.translateX", 3.0, lock=True)
        self.assertIs(result, expected)

    def test_create_node_delegates_args_and_kwargs(self):
        expected = "pCubeShape1"
        self.cmds.createNode.return_value = expected

        result = self.adapter.create_node("mesh", name="pCubeShape1", parent="pCube1")

        self.cmds.createNode.assert_called_once_with("mesh", name="pCubeShape1", parent="pCube1")
        self.assertIs(result, expected)

    def test_all_node_types_delegates_args_and_kwargs(self):
        expected = ["transform", "mesh"]
        self.cmds.allNodeTypes.return_value = expected

        result = self.adapter.all_node_types(includeAbstract=True)

        self.cmds.allNodeTypes.assert_called_once_with(includeAbstract=True)
        self.assertIs(result, expected)

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

    def test_node_type_delegates_args_and_kwargs(self):
        self.cmds.nodeType.return_value = "standardSurface"

        result = self.adapter.node_type("mat1", inherited=True)

        self.cmds.nodeType.assert_called_once_with("mat1", inherited=True)
        self.assertEqual(result, "standardSurface")

    def test_list_attr_delegates_args_and_kwargs(self):
        expected = ["mmd_material_name"]
        self.cmds.listAttr.return_value = expected

        result = self.adapter.list_attr("mat1", userDefined=True)

        self.cmds.listAttr.assert_called_once_with("mat1", userDefined=True)
        self.assertIs(result, expected)

    def test_alias_attr_delegates_args_and_kwargs(self):
        expected = ["smile", "faceBlendShape.weight[0]"]
        self.cmds.aliasAttr.return_value = expected

        result = self.adapter.alias_attr("smile", "faceBlendShape.weight[0]", query=True)

        self.cmds.aliasAttr.assert_called_once_with("smile", "faceBlendShape.weight[0]", query=True)
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

    def test_shading_node_delegates_args_and_kwargs(self):
        self.cmds.shadingNode.return_value = "mat1_texture"

        result = self.adapter.shading_node("file", asTexture=True, name="mat1_texture")

        self.cmds.shadingNode.assert_called_once_with("file", asTexture=True, name="mat1_texture")
        self.assertEqual(result, "mat1_texture")

    def test_connect_attr_delegates_args_and_kwargs(self):
        expected = None
        self.cmds.connectAttr.return_value = expected

        result = self.adapter.connect_attr("file1.outColor", "mat1.color", force=True)

        self.cmds.connectAttr.assert_called_once_with("file1.outColor", "mat1.color", force=True)
        self.assertIs(result, expected)

    def test_hyper_shade_delegates_args_and_kwargs(self):
        expected = None
        self.cmds.hyperShade.return_value = expected

        result = self.adapter.hyper_shade("mat1", assign="mat1")

        self.cmds.hyperShade.assert_called_once_with("mat1", assign="mat1")
        self.assertIs(result, expected)

    def test_window_delegates_args_and_kwargs(self):
        self.cmds.window.return_value = True

        result = self.adapter.window("hyperShadePanel1Window", exists=True)

        self.cmds.window.assert_called_once_with("hyperShadePanel1Window", exists=True)
        self.assertIs(result, True)

    def test_workspace_delegates_args_and_kwargs(self):
        self.cmds.workspace.return_value = "F:/Project/"

        result = self.adapter.workspace(query=True, rootDirectory=True)

        self.cmds.workspace.assert_called_once_with(query=True, rootDirectory=True)
        self.assertEqual(result, "F:/Project/")

    def test_xform_delegates_args_and_kwargs(self):
        expected = [1.0, 2.0, 3.0]
        self.cmds.xform.return_value = expected

        result = self.adapter.xform("bone_jnt", query=True, worldSpace=True, translation=True)

        self.cmds.xform.assert_called_once_with("bone_jnt", query=True, worldSpace=True, translation=True)
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

    def test_undo_delegates(self):
        expected = object()
        self.cmds.undo.return_value = expected

        self.assertIs(self.adapter.undo(), expected)
        self.cmds.undo.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
