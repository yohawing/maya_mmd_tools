"""Tests for RigConverter unified node type names.

After the typeName unification (C++ and Python both register as mmdAppend /
mmdCcdIk), the converter always returns the unified names regardless of
which plugin is loaded.
"""

from contextlib import ExitStack
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.converters import rig_converter  # noqa: E402
from mmd_tools.converters.rig_converter import RigConverter  # noqa: E402


class TestRigConverterUnifiedNodeTypes(unittest.TestCase):
    def test_self_grant_is_disabled_with_named_continue_diagnostic(self):
        converter = RigConverter()
        converter.logger = MagicMock()
        grant = {
            "targetBoneIndex": 0,
            "sourceBoneIndex": 0,
        }

        filtered = converter._drop_cycle_closing_grants(
            [grant],
            target_index=lambda item: item["targetBoneIndex"],
            source_index=lambda item: item["sourceBoneIndex"],
            bone_name=lambda _item: "self_bone",
        )

        self.assertEqual(filtered, [])
        message = converter.logger.info.call_args[0][0]
        self.assertIn("self_bone", message)
        self.assertIn("self-grant is skipped", message)
        self.assertIn("import continues", message)

    def test_two_bone_cycle_omits_only_cycle_closing_grant(self):
        converter = RigConverter()
        converter.logger = MagicMock()
        grants = [
            {"targetBoneIndex": 0, "sourceBoneIndex": 1},
            {"targetBoneIndex": 1, "sourceBoneIndex": 0},
        ]

        filtered = converter._drop_cycle_closing_grants(
            grants,
            target_index=lambda item: item["targetBoneIndex"],
            source_index=lambda item: item["sourceBoneIndex"],
            bone_name=lambda item: ("A", "B")[item["targetBoneIndex"]],
        )

        self.assertEqual(filtered, [grants[0]])
        message = converter.logger.warning.call_args[0][0]
        self.assertIn("B", message)
        self.assertIn("0 -> 1", message)
        self.assertIn("import continues", message)

    def test_acyclic_grants_preserve_parent_first_order(self):
        converter = RigConverter()
        grants = [
            {"targetBoneIndex": 2, "sourceBoneIndex": 1},
            {"targetBoneIndex": 1, "sourceBoneIndex": 0},
        ]

        filtered = converter._drop_cycle_closing_grants(
            grants,
            target_index=lambda item: item["targetBoneIndex"],
            source_index=lambda item: item["sourceBoneIndex"],
            bone_name=lambda item: str(item["targetBoneIndex"]),
        )
        ordered = converter._resolve_grant_dependencies_from_manifest(filtered)

        self.assertEqual([item["targetBoneIndex"] for item in ordered], [1, 2])

    def test_cpp_v2_lookup_is_scoped_to_legacy_symbol_owner_module(self):
        source = (
            Path(__file__).resolve().parents[2] / "cpp" / "src" / "MmdCcdIkNode.cpp"
        ).read_text(encoding="utf-8")
        resolver = source.split("IkChainCreateV2Fn resolveIkChainCreateV2()", 1)[1].split(
            "struct CcdIkChainConfig", 1
        )[0]

        self.assertIn("GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS", resolver)
        self.assertIn("&mmd_runtime_ik_chain_create", resolver)
        self.assertIn("dladdr", resolver)
        self.assertIn("ownerInfo.dli_fname", resolver)
        self.assertNotIn("GetModuleHandleA(", resolver)
        self.assertNotIn("RTLD_DEFAULT", resolver)

    def test_ik_chain_json_preserves_local_axis_on_remapped_slot(self):
        converter = RigConverter()
        local_axis = {"x": [0.0, 0.0, 1.0], "z": [0.0, 1.0, 0.0]}
        manifest = SimpleNamespace(
            bones=[
                {"parentIndex": -1, "restPosition": [0.0, 0.0, 0.0]},
                {"parentIndex": 0, "restPosition": [0.0, 1.0, 0.0], "localAxis": local_axis},
            ]
        )
        payload, _ = converter._build_ik_chain_json(
            manifest,
            {"controllerBoneIndex": 0, "targetBoneIndex": 1, "links": [{"boneIndex": 1}]},
            {"pmx_to_slot": {0: 0, 1: 1}, "slot_to_pmx": {0: 0, 1: 1}},
            [],
        )

        bones = json.loads(payload)["bones"]
        self.assertIsNone(bones[0]["local_axis"])
        self.assertEqual(bones[1]["local_axis"], local_axis)

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
        converter.logger = MagicMock()
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

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call_args[0][0] for call_args in converter.logger.debug.call_args_list if call_args[0]
        ]
        info_messages = [
            call_args[0][0] for call_args in converter.logger.info.call_args_list if call_args[0]
        ]
        expected_append_detail = (
            "mmdAppend node 'target_mmdAppend': source_joint -> target_joint (ratio=0.5)"
        )
        self.assertIn(expected_append_detail, debug_messages)
        self.assertNotIn(expected_append_detail, info_messages)


if __name__ == "__main__":
    unittest.main()
