"""Test the scene registry ownership contract without a live Maya scene."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.core import model_registry  # noqa: E402


class TestModelRegistry(unittest.TestCase):
    """Keep category mapping and message registration fail-closed."""

    def test_register_members_uses_registry_message_array(self):
        with (
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry.cmds, "listConnections", return_value=[]),
            mock.patch.object(model_registry.cmds, "getAttr", return_value=[]),
            mock.patch.object(model_registry, "_ensure_message_attr"),
            mock.patch.object(model_registry, "_validate_registry_node", return_value="|Model_root"),
            mock.patch.object(model_registry, "_canonical_node", side_effect=lambda node: node),
            mock.patch.object(model_registry.cmds, "connectAttr") as connect_attr,
        ):
            result = model_registry.register_model_members(
                "ModelRegistry",
                model_registry.REGISTRY_CATEGORY_MORPH,
                ["bone_morph", "material_morph"],
            )

        self.assertEqual(result, ["bone_morph", "material_morph"])
        self.assertEqual(
            [call.args for call in connect_attr.call_args_list],
            [
                ("bone_morph.message", "ModelRegistry.morphMembers[0]"),
                ("material_morph.message", "ModelRegistry.morphMembers[1]"),
            ],
        )

    def test_material_category_uses_material_members_message_array(self):
        self.assertEqual(
            model_registry.registry_category_attribute(
                model_registry.REGISTRY_CATEGORY_MATERIAL
            ),
            "materialMembers",
        )
        with (
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry.cmds, "listConnections", return_value=[]),
            mock.patch.object(model_registry.cmds, "getAttr", return_value=[]),
            mock.patch.object(model_registry, "_ensure_message_attr") as ensure_attr,
            mock.patch.object(model_registry, "_validate_registry_node", return_value="|Model_root"),
            mock.patch.object(model_registry, "_canonical_node", side_effect=lambda node: node),
            mock.patch.object(model_registry.cmds, "connectAttr") as connect_attr,
        ):
            result = model_registry.register_model_members(
                "ModelRegistry",
                model_registry.REGISTRY_CATEGORY_MATERIAL,
                ["dx11Shader1", "glslShader1"],
            )

        self.assertEqual(result, ["dx11Shader1", "glslShader1"])
        ensure_attr.assert_called_once_with(
            "ModelRegistry",
            "materialMembers",
            multi=True,
        )
        self.assertEqual(
            [call.args for call in connect_attr.call_args_list],
            [
                ("dx11Shader1.message", "ModelRegistry.materialMembers[0]"),
                ("glslShader1.message", "ModelRegistry.materialMembers[1]"),
            ],
        )

    def test_new_registry_has_one_root_connection(self):
        with (
            mock.patch.object(model_registry, "_canonical_root", return_value="|Model_root"),
            mock.patch.object(model_registry, "get_model_registry", return_value=None),
            mock.patch.object(
                model_registry.cmds,
                "createNode",
                return_value="Model_root_modelRegistry",
            ),
            mock.patch.object(model_registry, "_has_attr", return_value=False),
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry.cmds, "addAttr"),
            mock.patch.object(model_registry.cmds, "setAttr"),
            mock.patch.object(model_registry.cmds, "connectAttr") as connect_attr,
        ):
            registry = model_registry.ensure_model_registry("Model_root")

        self.assertEqual(registry, "Model_root_modelRegistry")
        self.assertIn(
            mock.call("|Model_root.message", "Model_root_modelRegistry.modelRoot"),
            connect_attr.call_args_list,
        )
        self.assertIn(
            mock.call(
                "Model_root_modelRegistry.message",
                "|Model_root.mmd_model_registry",
                force=True,
            ),
            connect_attr.call_args_list,
        )

    def test_unknown_schema_is_rejected(self):
        with (
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry, "_has_attr", return_value=True),
            mock.patch.object(model_registry.cmds, "getAttr", return_value="2"),
        ):
            with self.assertRaises(model_registry.ModelRegistryError):
                model_registry._validate_registry_node("ModelRegistry")

    def test_unregister_members_disconnects_only_requested_category_members(self):
        with (
            mock.patch.object(model_registry, "_validate_registry_node"),
            mock.patch.object(model_registry.cmds, "listConnections", return_value=["shaderA", "shaderB"]),
            mock.patch.object(model_registry, "_canonical_node", side_effect=lambda node: node),
            mock.patch.object(model_registry.cmds, "disconnectAttr") as disconnect_attr,
        ):
            remaining = model_registry.unregister_model_members(
                "ModelRegistry",
                model_registry.REGISTRY_CATEGORY_MATERIAL,
                ["shaderB"],
            )

        self.assertEqual(remaining, ["shaderA"])
        disconnect_attr.assert_called_once_with(
            "shaderB.message",
            "ModelRegistry.materialMembers[1]",
        )

    def test_unregister_members_uses_actual_sparse_destination_plug(self):
        def list_connections(_endpoint, **kwargs):
            if kwargs.get("connections") and kwargs.get("plugs"):
                return [
                    "shaderA.message",
                    "ModelRegistry.materialMembers[0]",
                    "shaderB.message",
                    "ModelRegistry.materialMembers[2]",
                ]
            return ["shaderA", "shaderB"]

        with (
            mock.patch.object(model_registry, "_validate_registry_node"),
            mock.patch.object(model_registry.cmds, "listConnections", side_effect=list_connections),
            mock.patch.object(model_registry, "_canonical_node", side_effect=lambda node: node),
            mock.patch.object(model_registry.cmds, "disconnectAttr") as disconnect_attr,
        ):
            remaining = model_registry.unregister_model_members(
                "ModelRegistry",
                model_registry.REGISTRY_CATEGORY_MATERIAL,
                ["shaderB"],
            )

        self.assertEqual(remaining, ["shaderA"])
        disconnect_attr.assert_called_once_with(
            "shaderB.message",
            "ModelRegistry.materialMembers[2]",
        )

    def test_unregister_members_rejects_unknown_requested_member(self):
        def list_connections(_endpoint, **kwargs):
            if kwargs.get("connections") and kwargs.get("plugs"):
                return ["shaderA.message", "ModelRegistry.materialMembers[4]"]
            return ["shaderA"]

        with (
            mock.patch.object(model_registry, "_validate_registry_node"),
            mock.patch.object(model_registry.cmds, "listConnections", side_effect=list_connections),
            mock.patch.object(model_registry, "_canonical_node", side_effect=lambda node: node),
            mock.patch.object(model_registry.cmds, "disconnectAttr") as disconnect_attr,
        ):
            with self.assertRaises(model_registry.ModelRegistryError):
                model_registry.unregister_model_members(
                    "ModelRegistry",
                    model_registry.REGISTRY_CATEGORY_MATERIAL,
                    ["shaderMissing"],
                )

        disconnect_attr.assert_not_called()

if __name__ == "__main__":
    unittest.main()
