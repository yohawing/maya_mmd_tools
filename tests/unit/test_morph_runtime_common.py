"""Unit tests for shared PMX morph runtime helpers."""

import unittest
from unittest.mock import MagicMock

import mmd_tools.converters.morph_runtime_common as morph_runtime_common


class TestMorphRuntimeCommon(unittest.TestCase):
    """Shared morph runtime helper behavior."""

    def setUp(self):
        self.cmds = MagicMock()
        self.cmds.attributeQuery.return_value = False
        self.cmds.getAttr.return_value = None
        self.original_cmds = morph_runtime_common.cmds
        morph_runtime_common.cmds = self.cmds

    def tearDown(self):
        morph_runtime_common.cmds = self.original_cmds

    def test_parse_morph_offsets_json_reads_list(self):
        self.cmds.getAttr.return_value = '[{"bone_index": 1}]'

        offsets = morph_runtime_common.parse_morph_offsets_json("morphNode", "mmd_bone_morph_offsets_json")

        self.assertEqual(offsets, [{"bone_index": 1}])
        self.cmds.getAttr.assert_called_once_with("morphNode.mmd_bone_morph_offsets_json")

    def test_parse_morph_offsets_json_rejects_invalid_or_non_list_json(self):
        self.cmds.getAttr.return_value = '{"bone_index": 1}'
        self.assertIsNone(morph_runtime_common.parse_morph_offsets_json("morphNode", "offsets"))

        self.cmds.getAttr.return_value = "["
        self.assertIsNone(morph_runtime_common.parse_morph_offsets_json("morphNode", "offsets"))

    def test_parse_morph_offsets_json_treats_empty_attr_as_empty_list(self):
        self.cmds.getAttr.return_value = ""

        self.assertEqual(morph_runtime_common.parse_morph_offsets_json("morphNode", "offsets"), [])

    def test_get_morph_order_reads_index(self):
        self.cmds.attributeQuery.return_value = True
        self.cmds.getAttr.return_value = "7"

        self.assertEqual(morph_runtime_common.get_morph_order("morphNode"), 7)

    def test_get_morph_order_falls_back_to_zero(self):
        self.cmds.attributeQuery.return_value = False
        self.assertEqual(morph_runtime_common.get_morph_order("morphNode"), 0)

        self.cmds.attributeQuery.return_value = True
        self.cmds.getAttr.side_effect = RuntimeError("broken attr")
        self.assertEqual(morph_runtime_common.get_morph_order("morphNode"), 0)

    def test_get_morph_order_returns_zero_when_attribute_query_raises(self):
        self.cmds.attributeQuery.side_effect = RuntimeError("node does not exist")

        self.assertEqual(morph_runtime_common.get_morph_order("missing"), 0)


if __name__ == "__main__":
    unittest.main()
