"""Tests for RigConverter unified node type names.

After the typeName unification (C++ and Python both register as mmdAppend /
mmdCcdIk), the converter always returns the unified names regardless of
which plugin is loaded.
"""

from contextlib import ExitStack
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.converters import rig_converter  # noqa: E402
from mmd_tools.converters.rig_converter import RigConverter  # noqa: E402


class TestRigConverterUnifiedNodeTypes(unittest.TestCase):
    def test_append_node_type_always_returns_unified_name(self):
        converter = RigConverter()
        self.assertEqual(converter._append_node_type(), "mmdAppend")

    def test_ccd_ik_node_type_always_returns_unified_name(self):
        converter = RigConverter()
        self.assertEqual(converter._ccd_ik_node_type(), "mmdCcdIk")

    def test_setup_pmx_rig_records_native_unavailable_fallback_warning(self):
        converter = RigConverter()
        pmx_data = SimpleNamespace(bones=[])

        with patch("mmd_tools.converters.rig_converter.is_rig_primitive_available", return_value=False):
            result = converter.setup_pmx_rig(
                pmx_data,
                maya_joints=[],
                bone_map={},
                skeleton_group="skeleton",
                pmx_filepath="model.pmx",
            )

        self.assertIsNone(result["native_rig"])
        self.assertEqual(result["warnings"][0]["source"], "rig_converter")
        self.assertEqual(result["warnings"][0]["code"], "native_rig_unavailable")
        self.assertEqual(result["warnings"][0]["severity"], "warning")
        self.assertEqual(result["warnings"][0]["fallback"], "python_constraints")

    def test_create_append_nodes_sets_explicit_compat_schema_mode(self):
        converter = RigConverter()
        manifest = SimpleNamespace(
            grants=[
                {
                    "targetBoneIndex": 1,
                    "sourceBoneIndex": 0,
                    "ratio": 0.5,
                    "affectRotation": True,
                    "affectTranslation": False,
                    "local": False,
                }
            ],
            bones=[{}, {}],
        )

        def _attribute_query(attr, *_, **__):
            return attr in {"localAppend", "schemaMode", "sourceJointOrient", "targetJointOrient", "mmd_grant_node"}

        def _get_attr(attr, *_, **__):
            if attr.endswith(".jointOrient"):
                return [(0.0, 0.0, 0.0)]
            return [(0.0, 0.0, 0.0)]

        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.converters.rig_converter.maya_scene_utils.object_exists", return_value=True)
            )
            stack.enter_context(patch.object(rig_converter.cmds, "createNode", return_value="target_mmdAppend"))
            stack.enter_context(patch.object(rig_converter.cmds, "attributeQuery", side_effect=_attribute_query))
            stack.enter_context(patch.object(rig_converter.cmds, "getAttr", side_effect=_get_attr))
            set_attr = stack.enter_context(patch.object(rig_converter.cmds, "setAttr"))
            stack.enter_context(patch.object(rig_converter.cmds, "connectAttr"))
            stack.enter_context(patch.object(rig_converter.cmds, "listConnections", return_value=[]))
            nodes = converter._create_append_nodes_from_manifest(manifest, ["source_joint", "target_joint"])

        self.assertEqual(nodes, ["target_mmdAppend"])
        set_attr.assert_has_calls(
            [
                call("target_mmdAppend.ratio", 0.5),
                call("target_mmdAppend.affectRotation", True),
                call("target_mmdAppend.affectTranslation", False),
                call("target_mmdAppend.localAppend", False),
                call("target_mmdAppend.schemaMode", rig_converter.MMD_APPEND_SCHEMA_MODE_COMPAT),
            ]
        )


if __name__ == "__main__":
    unittest.main()
