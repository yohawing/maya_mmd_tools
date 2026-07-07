"""Pure-Python tests for post-hoc dx11Shader texture rebinding."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.core import maya_material_utils, maya_utils  # noqa: E402


class TestMayaUtilsDx11Rebind(unittest.TestCase):
    def test_unconnected_file_node_is_inferred_from_main_texture_name(self):
        # maya_material_utils owns DX11 slot resolution and writes attributes
        # through maya_attribute_utils directly.
        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds, patch(
            "mmd_tools.core.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listConnections.return_value = []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "dx11Shader"
            mock_cmds.attributeQuery.return_value = True

            result = maya_material_utils.rebind_resolved_mmd_dx11_texture("Face_shader_texture")

        self.assertEqual(result["status"], "rebound")
        self.assertEqual(result["texture_attr"], "MainTexture")
        mock_cmds.connectAttr.assert_called_once_with(
            "Face_shader_texture.outColor",
            "Face_shader.MainTexture",
            force=True,
        )
        mock_set_attribute.assert_called_once_with("Face_shader", "HasMainTexture", 1, "long")

    def test_unconnected_secondary_file_nodes_are_inferred_from_names(self):
        cases = [
            ("Face_shader_sphere_texture", "SphereTexture", "HasSphereTexture"),
            ("Face_shader_toon_texture", "ToonTexture", "HasToonTexture"),
        ]
        for file_node, texture_attr, has_attr in cases:
            with self.subTest(texture_attr=texture_attr), patch(
                "mmd_tools.core.maya_material_utils.cmds"
            ) as mock_cmds, patch("mmd_tools.core.maya_attribute_utils.set_attribute") as mock_set_attribute:
                mock_cmds.listConnections.return_value = []
                mock_cmds.objExists.return_value = True
                mock_cmds.nodeType.return_value = "dx11Shader"
                mock_cmds.attributeQuery.return_value = True

                result = maya_material_utils.rebind_resolved_mmd_dx11_texture(file_node)

            self.assertEqual(result["status"], "rebound")
            self.assertEqual(result["texture_attr"], texture_attr)
            mock_cmds.connectAttr.assert_called_once_with(
                f"{file_node}.outColor",
                f"Face_shader.{texture_attr}",
                force=True,
            )
            mock_set_attribute.assert_called_once_with("Face_shader", has_attr, 1, "long")

    def test_existing_dx11_connection_sets_has_flag_without_duplicate_connect(self):
        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds, patch(
            "mmd_tools.core.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listConnections.return_value = ["Face_shader.MainTexture"]
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "dx11Shader"
            mock_cmds.attributeQuery.return_value = True

            result = maya_material_utils.rebind_resolved_mmd_dx11_texture("Renamed_texture_file")

        self.assertEqual(result["status"], "rebound")
        mock_cmds.connectAttr.assert_not_called()
        mock_set_attribute.assert_called_once_with("Face_shader", "HasMainTexture", 1, "long")

    def test_rebind_skips_non_dx11_and_missing_slot_cases(self):
        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds:
            mock_cmds.listConnections.return_value = []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "standardSurface"
            mock_cmds.attributeQuery.return_value = True

            result = maya_material_utils.rebind_resolved_mmd_dx11_texture("Face_shader_texture")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_dx11_shader")
        mock_cmds.connectAttr.assert_not_called()

        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds:
            mock_cmds.listConnections.return_value = []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "dx11Shader"
            mock_cmds.attributeQuery.side_effect = lambda attr, node, exists: attr != "MainTexture"

            result = maya_material_utils.rebind_resolved_mmd_dx11_texture("Face_shader_texture")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "texture_attr_missing")
        mock_cmds.connectAttr.assert_not_called()

        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds:
            mock_cmds.listConnections.return_value = []
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.objExists.return_value = False

            result = maya_material_utils.rebind_resolved_mmd_dx11_texture("unrelated_file")

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "dx11_texture_slot_not_found")
        mock_cmds.connectAttr.assert_not_called()

    def test_resolve_scene_rebinds_only_resolved_results_and_refreshes_once(self):
        classifications = [
            SimpleNamespace(status="resolvable"),
            SimpleNamespace(status="resolvable"),
        ]
        resolved = SimpleNamespace(status="resolved")
        unrecoverable = SimpleNamespace(status="unrecoverable")

        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds, patch(
            "mmd_tools.core.maya_material_utils.classify_mmd_texture_file_node",
            side_effect=classifications,
        ), patch(
            "mmd_tools.core.maya_material_utils.resolve_mmd_texture_file_node",
            side_effect=[resolved, unrecoverable],
        ), patch(
            "mmd_tools.core.maya_material_utils.rebind_resolved_mmd_dx11_texture",
            return_value={
                "status": "rebound",
                "reason": "connected",
                "shader": "Face_shader",
                "texture_attr": "MainTexture",
                "has_attr": "HasMainTexture",
            },
        ) as mock_rebind:
            mock_cmds.ls.return_value = ["resolved_file", "failed_file"]
            mock_cmds.attributeQuery.return_value = True

            results = maya_material_utils.resolve_scene_mmd_textures()

        self.assertEqual(results, [resolved, unrecoverable])
        self.assertEqual(resolved.file_node, "resolved_file")
        self.assertEqual(unrecoverable.file_node, "failed_file")
        mock_rebind.assert_called_once_with("resolved_file")
        mock_cmds.refresh.assert_called_once_with(force=True)
        self.assertEqual(resolved.rebind_status, "rebound")
        self.assertEqual(resolved.rebind_texture_attr, "MainTexture")
        self.assertFalse(hasattr(unrecoverable, "rebind_status"))

    def test_rebind_summary_does_not_refresh_when_all_resolved_nodes_skip(self):
        result = SimpleNamespace(status="resolved", file_node="Face_shader_texture")
        with patch("mmd_tools.core.maya_material_utils.cmds") as mock_cmds, patch(
            "mmd_tools.core.maya_material_utils.rebind_resolved_mmd_dx11_texture",
            return_value={"status": "skipped", "reason": "dx11_texture_slot_not_found"},
        ) as mock_rebind:
            summary = maya_material_utils.rebind_resolved_scene_mmd_dx11_textures([result])

        mock_rebind.assert_called_once_with("Face_shader_texture")
        mock_cmds.refresh.assert_not_called()
        self.assertEqual(summary, {"rebound": 0, "skipped": 1, "failed": 0})
        self.assertEqual(result.rebind_status, "skipped")
        self.assertEqual(result.rebind_reason, "dx11_texture_slot_not_found")

    def test_maya_utils_compatibility_wrapper_delegates_to_material_utils(self):
        with patch(
            "mmd_tools.core.maya_material_utils.resolve_scene_mmd_textures",
            return_value=["resolved"],
        ) as mock_resolve:
            result = maya_utils.resolve_scene_mmd_textures(workspace_root="F:/workspace")

        self.assertEqual(result, ["resolved"])
        mock_resolve.assert_called_once_with(workspace_root="F:/workspace")


if __name__ == "__main__":
    unittest.main()
