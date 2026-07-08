"""Unit coverage for material morph runtime evaluator availability guards."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.converters import material_morph_runtime  # noqa: E402


class TestMaterialMorphRuntimeGuard(unittest.TestCase):
    def test_create_evaluator_rejects_node_without_required_attrs(self):
        cmds = mock.Mock()
        cmds.createNode.return_value = "bad_materialMorphEval"
        cmds.objExists.return_value = True
        cmds.nodeType.return_value = material_morph_runtime.EVAL_NODE_TYPE
        cmds.attributeQuery.return_value = False

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            node = material_morph_runtime._create_evaluator("shader1")

        self.assertIsNone(node)
        cmds.delete.assert_called_once_with("bad_materialMorphEval")

    def test_create_evaluator_accepts_node_with_required_attrs(self):
        cmds = mock.Mock()
        cmds.createNode.return_value = "good_materialMorphEval"
        cmds.objExists.return_value = True
        cmds.nodeType.return_value = material_morph_runtime.EVAL_NODE_TYPE
        cmds.attributeQuery.return_value = True

        with mock.patch.object(material_morph_runtime, "cmds", cmds):
            node = material_morph_runtime._create_evaluator("shader1")

        self.assertEqual(node, "good_materialMorphEval")
        cmds.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
