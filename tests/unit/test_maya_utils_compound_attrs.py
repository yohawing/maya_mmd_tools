import unittest
from unittest.mock import MagicMock, call, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.core import maya_utils  # noqa: E402


class TestMayaUtilsCompoundAttrs(unittest.TestCase):
    def setUp(self):
        self.original_cmds = maya_utils.cmds
        self.original_om = maya_utils.om
        self.cmds = MagicMock(name="cmds")
        self.om = MagicMock(name="om")
        maya_utils.cmds = self.cmds
        maya_utils.om = self.om

    def tearDown(self):
        maya_utils.cmds = self.original_cmds
        maya_utils.om = self.original_om

    def _configure_plug_failure(self, setter_name):
        selection_list = MagicMock(name="selection_list")
        depend_fn = MagicMock(name="depend_fn")
        plug = MagicMock(name="plug")
        child_plug = MagicMock(name="child_plug")
        getattr(child_plug, setter_name).side_effect = RuntimeError("OM write failed")
        plug.child.return_value = child_plug
        depend_fn.findPlug.return_value = plug
        self.om.MSelectionList.return_value = selection_list
        self.om.MFnDependencyNode.return_value = depend_fn

    def test_set_attribute_double3_falls_back_to_cmds_set_attr(self):
        self._configure_plug_failure("setDouble")

        with patch.object(maya_utils.logger, "error") as logger_error:
            maya_utils.set_attribute("Mt_HairLine", "mmd_edge_color", (0.1, 0.2, 0.3), "double3")

        self.cmds.setAttr.assert_called_once_with(
            "Mt_HairLine.mmd_edge_color",
            0.1,
            0.2,
            0.3,
            type="double3",
        )
        logger_error.assert_not_called()

    def test_set_attribute_long3_falls_back_to_cmds_set_attr(self):
        self._configure_plug_failure("setInt")

        with patch.object(maya_utils.logger, "error") as logger_error:
            maya_utils.set_attribute("node", "triple_index", (1, 2, 3), "long3")

        self.cmds.setAttr.assert_called_once_with("node.triple_index", 1, 2, 3, type="long3")
        logger_error.assert_not_called()

    def test_set_attribute_double4_falls_back_to_cmds_set_attr(self):
        self._configure_plug_failure("setDouble")

        with patch.object(maya_utils.logger, "error") as logger_error:
            maya_utils.set_attribute("node", "rgba", (0.1, 0.2, 0.3, 0.4), "double4")

        self.cmds.setAttr.assert_called_once_with("node.rgba", 0.1, 0.2, 0.3, 0.4, type="double4")
        logger_error.assert_not_called()

    def test_set_custom_attributes_falls_back_to_cmds_add_attr_for_double3_creation(self):
        self.cmds.attributeQuery.return_value = False

        with patch.object(maya_utils, "add_typed_attribute") as add_typed_attribute:
            with patch.object(maya_utils, "set_attribute") as set_attribute:
                maya_utils.set_custom_attributes("Mt_HairLine", {"mmd_edge_color": (0.1, 0.2, 0.3)})

        add_typed_attribute.assert_called_once_with("Mt_HairLine", "mmd_edge_color", "double3")
        self.cmds.addAttr.assert_has_calls(
            [
                call("Mt_HairLine", ln="mmd_edge_color", at="double3"),
                call("Mt_HairLine", ln="mmd_edge_colorX", at="double", p="mmd_edge_color"),
                call("Mt_HairLine", ln="mmd_edge_colorY", at="double", p="mmd_edge_color"),
                call("Mt_HairLine", ln="mmd_edge_colorZ", at="double", p="mmd_edge_color"),
            ]
        )
        set_attribute.assert_called_once_with(
            "Mt_HairLine",
            "mmd_edge_color",
            (0.1, 0.2, 0.3),
            "double3",
        )

    def test_set_custom_attributes_infers_numeric_sequence_types(self):
        self.cmds.attributeQuery.return_value = True

        with patch.object(maya_utils, "set_attribute") as set_attribute:
            maya_utils.set_custom_attributes(
                "node",
                {
                    "all_float": (0.0, 0.0, 0.0),
                    "mixed_numeric": (0, 0, 0.5),
                    "four_numeric": (0, 1, 0.5, 1.0),
                },
            )

        self.assertEqual(
            [args[0][3] for args in set_attribute.call_args_list],
            ["double3", "double3", "double4"],
        )


if __name__ == "__main__":
    unittest.main()
