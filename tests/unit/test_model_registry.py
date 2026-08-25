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
        events = []

        def create_node(*args, **kwargs):
            events.append(("createNode", args, kwargs))
            return "Model_root_modelRegistry"

        with (
            mock.patch.object(model_registry, "_canonical_root", return_value="|Model_root"),
            mock.patch.object(model_registry, "get_model_registry", return_value=None),
            mock.patch.object(
                model_registry.cmds,
                "createNode",
                side_effect=create_node,
            ),
            mock.patch.object(model_registry, "_has_attr", return_value=False),
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry.cmds, "addAttr"),
            mock.patch.object(model_registry.cmds, "setAttr"),
            mock.patch.object(model_registry.cmds, "connectAttr") as connect_attr,
        ):
            registry = model_registry.ensure_model_registry(
                "Model_root",
                mutation_boundary=lambda: events.append(("mutation-boundary",)),
            )

        self.assertEqual(registry, "Model_root_modelRegistry")
        self.assertEqual([event[0] for event in events], ["createNode", "mutation-boundary"])
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

    def test_registry_creation_failure_does_not_notify_mutation_boundary(self):
        boundary = mock.Mock()
        with (
            mock.patch.object(model_registry, "_canonical_root", return_value="|Model_root"),
            mock.patch.object(model_registry, "get_model_registry", return_value=None),
            mock.patch.object(
                model_registry.cmds,
                "createNode",
                side_effect=RuntimeError("create failed before mutation"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "create failed before mutation"):
                model_registry.ensure_model_registry(
                    "Model_root",
                    mutation_boundary=boundary,
                )

        boundary.assert_not_called()

    def test_existing_registry_does_not_notify_creation_boundary(self):
        boundary = mock.Mock()
        with (
            mock.patch.object(model_registry, "_canonical_root", return_value="|Model_root"),
            mock.patch.object(model_registry, "get_model_registry", return_value="ModelRegistry"),
            mock.patch.object(model_registry.cmds, "createNode") as create_node,
        ):
            registry = model_registry.ensure_model_registry(
                "Model_root",
                mutation_boundary=boundary,
            )

        self.assertEqual(registry, "ModelRegistry")
        boundary.assert_not_called()
        create_node.assert_not_called()

    def test_unknown_schema_is_rejected(self):
        with (
            mock.patch.object(model_registry.cmds, "objExists", return_value=True),
            mock.patch.object(model_registry, "_has_attr", return_value=True),
            mock.patch.object(model_registry.cmds, "getAttr", return_value="2"),
        ):
            with self.assertRaises(model_registry.ModelRegistryError):
                model_registry._validate_registry_node("ModelRegistry")

    def test_unregister_members_disconnects_only_requested_category_members(self):
        boundary = mock.Mock()
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
                mutation_boundary=boundary,
            )

        self.assertEqual(remaining, ["shaderA"])
        disconnect_attr.assert_called_once_with(
            "shaderB.message",
            "ModelRegistry.materialMembers[1]",
        )
        boundary.assert_called_once_with()

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
