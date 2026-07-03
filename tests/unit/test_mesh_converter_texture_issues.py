"""Pure-Python checks for MeshConverter texture issue reporting."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters.mesh_converter import (  # noqa: E402
    MeshConverter,
    bind_dx11_texture_file_node,
)
from mmd_tools.core.settings import settings  # noqa: E402


class TestMeshConverterTextureIssues(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.model = self.root / "model.pmx"
        self.model.write_bytes(b"model")
        self.texture = self.root / "颜.png"
        self.texture.write_bytes(b"texture")
        self.ascii_texture = self.root / "face.png"
        self.ascii_texture.write_bytes(b"texture")
        self._saved_auto_resolve = settings.get("import.model.auto_resolve_textures", True)

    def tearDown(self):
        settings.set("import.model.auto_resolve_textures", self._saved_auto_resolve)
        self.tmp.cleanup()

    def test_bind_dx11_texture_file_node_connects_main_texture_and_sets_has_flag(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listConnections.return_value = []
            mock_cmds.attributeQuery.return_value = True

            result = bind_dx11_texture_file_node(
                "Face_shader",
                "Face_shader_texture",
                "MainTexture",
                "HasMainTexture",
            )

        self.assertTrue(result)
        mock_cmds.connectAttr.assert_called_once_with(
            "Face_shader_texture.outColor",
            "Face_shader.MainTexture",
            force=True,
        )
        mock_set_attribute.assert_called_once_with("Face_shader", "HasMainTexture", 1, "long")

    def test_bind_dx11_texture_file_node_connects_secondary_slots_and_sets_has_flags(self):
        cases = [
            ("Face_shader_sphere_texture", "SphereTexture", "HasSphereTexture"),
            ("Face_shader_toon_texture", "ToonTexture", "HasToonTexture"),
        ]
        for file_node, texture_attr, has_attr in cases:
            with self.subTest(texture_attr=texture_attr), patch(
                "mmd_tools.converters.mesh_converter.cmds"
            ) as mock_cmds, patch(
                "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
            ) as mock_set_attribute:
                mock_cmds.listConnections.return_value = []
                mock_cmds.attributeQuery.return_value = True

                result = bind_dx11_texture_file_node("Face_shader", file_node, texture_attr, has_attr)

            self.assertTrue(result)
            mock_cmds.connectAttr.assert_called_once_with(
                f"{file_node}.outColor",
                f"Face_shader.{texture_attr}",
                force=True,
            )
            mock_set_attribute.assert_called_once_with("Face_shader", has_attr, 1, "long")

    def test_bind_dx11_texture_file_node_avoids_duplicate_connection(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute:
            mock_cmds.listConnections.return_value = ["Face_shader.MainTexture"]
            mock_cmds.attributeQuery.return_value = True

            result = bind_dx11_texture_file_node(
                "Face_shader",
                "Face_shader_texture",
                "MainTexture",
                "HasMainTexture",
            )

        self.assertTrue(result)
        mock_cmds.connectAttr.assert_not_called()
        mock_set_attribute.assert_called_once_with("Face_shader", "HasMainTexture", 1, "long")

    def test_record_unresolved_texture_issue_dict_shape(self):
        converter = MeshConverter(str(self.model))
        material = SimpleNamespace(name="Face")

        issue = converter._record_unresolved_texture_issue(
            file_node="Face_file",
            shader="Face_shader",
            material=material,
            original_path=self.texture.name,
            current_path=str(self.texture),
        )

        self.assertEqual(issue["file_node"], "Face_file")
        self.assertEqual(issue["material"], "Face_shader")
        self.assertEqual(issue["material_name"], "Face")
        self.assertEqual(issue["original_path"], self.texture.name)
        self.assertEqual(issue["current_path"], str(self.texture))
        self.assertIn("reason", issue)
        self.assertEqual(issue["reason"], "non_ascii_path")
        self.assertTrue(issue["resolvable"])
        self.assertEqual(Path(issue["source_path"]), self.texture)
        self.assertEqual(converter.unresolved_texture_count, 1)
        self.assertEqual(converter.profile["unresolved_texture_count"], 1)
        self.assertEqual(converter.profile["unresolved_textures"], [issue])

    def test_setup_dx11_shader_keeps_unreadable_main_texture_unconnected(self):
        settings.set("import.model.auto_resolve_textures", False)
        converter = MeshConverter(str(self.model))
        material = SimpleNamespace(
            material_index=0,
            name="Face",
            diffuse=[0.8, 0.7, 0.6, 1.0],
            ambient=[0.1, 0.1, 0.1],
            specular=[0.2, 0.2, 0.2],
            specular_coefficient=8.0,
            edge_color=[0.0, 0.0, 0.0, 1.0],
            edge_size=0.0,
            sphere_mode=0,
            sphere_texture_index=-1,
            toon_texture_index=-1,
            texture_index=0,
            draw_flag=0,
            memo="",
            shared_toon_flag=0,
        )

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_texture"

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                self.texture.name,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        self.assertEqual(converter.unresolved_texture_count, 1)
        issue = converter.profile["unresolved_textures"][0]
        self.assertEqual(issue["reason"], "non_ascii_path")
        self.assertTrue(issue["resolvable"])
        self.assertEqual(Path(issue["source_path"]), self.texture)
        mock_mark.assert_called_once_with(
            "Face_shader_texture",
            self.texture.name,
            str(self.model),
            unresolved=True,
        )
        mock_cmds.connectAttr.assert_not_called()
        self.assertNotIn(call("Face_shader", "HasMainTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_auto_resolves_unreadable_main_texture_before_connect(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material()

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.workspace.return_value = "C:/workspace/"
            mock_cmds.shadingNode.return_value = "Face_shader_texture"
            mock_resolve.return_value = SimpleNamespace(status="resolved", cache_path="C:/ascii/x.png")

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                self.texture.name,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_called_once_with(
            original_path=self.texture.name,
            file_texture_path=str(self.texture),
            model_path=str(self.model),
            workspace_root="C:/workspace/",
        )
        self.assertIn(
            call("Face_shader_texture", "fileTextureName", "C:/ascii/x.png", "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_texture",
            self.texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_set_custom_attributes.assert_any_call(
            "Face_shader_texture",
            {
                "mmd_texture_cache_path": "C:/ascii/x.png",
                "mmd_texture_unresolved": False,
            },
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_texture.outColor",
            "Face_shader.MainTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasMainTexture", 1, "long"), mock_set_attribute.call_args_list)
        self.assertEqual(converter.unresolved_texture_count, 0)

    def test_setup_dx11_shader_auto_resolve_failure_keeps_unreadable_main_texture_unconnected(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material()

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.workspace.return_value = "C:/workspace/"
            mock_cmds.shadingNode.return_value = "Face_shader_texture"
            mock_resolve.return_value = SimpleNamespace(status="unrecoverable", cache_path=None)

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                self.texture.name,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        self.assertEqual(converter.unresolved_texture_count, 1)
        mock_mark.assert_called_once_with(
            "Face_shader_texture",
            self.texture.name,
            str(self.model),
            unresolved=True,
        )
        mock_cmds.connectAttr.assert_not_called()
        self.assertNotIn(call("Face_shader", "HasMainTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_readable_ascii_main_texture_skips_auto_resolve(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material()

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_texture"

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                self.ascii_texture.name,
                [self.ascii_texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_not_called()
        self.assertIn(
            call("Face_shader_texture", "fileTextureName", str(self.ascii_texture), "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_texture",
            self.ascii_texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_texture.outColor",
            "Face_shader.MainTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasMainTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_auto_resolves_unreadable_sphere_texture_before_connect(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(sphere_texture_index=0, sphere_mode=1)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.workspace.return_value = "C:/workspace/"
            mock_cmds.shadingNode.return_value = "Face_shader_sphere_texture"
            mock_resolve.return_value = SimpleNamespace(status="resolved", cache_path="C:/ascii/sphere.sph")

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_called_once_with(
            original_path=self.texture.name,
            file_texture_path=str(self.texture),
            model_path=str(self.model),
            workspace_root="C:/workspace/",
        )
        self.assertIn(
            call("Face_shader_sphere_texture", "fileTextureName", "C:/ascii/sphere.sph", "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_sphere_texture",
            self.texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_set_custom_attributes.assert_any_call(
            "Face_shader_sphere_texture",
            {
                "mmd_texture_cache_path": "C:/ascii/sphere.sph",
                "mmd_texture_unresolved": False,
            },
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_sphere_texture.outColor",
            "Face_shader.SphereTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasSphereTexture", 1, "long"), mock_set_attribute.call_args_list)
        self.assertEqual(converter.unresolved_texture_count, 0)

    def test_setup_dx11_shader_unreadable_sphere_texture_stays_disconnected_after_resolve_failure(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(sphere_texture_index=0, sphere_mode=1)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.workspace.return_value = "C:/workspace/"
            mock_cmds.shadingNode.return_value = "Face_shader_sphere_texture"
            mock_resolve.return_value = SimpleNamespace(status="unrecoverable", cache_path=None)

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        self.assertEqual(converter.unresolved_texture_count, 1)
        mock_mark.assert_called_once_with(
            "Face_shader_sphere_texture",
            self.texture.name,
            str(self.model),
            unresolved=True,
        )
        mock_cmds.connectAttr.assert_not_called()
        self.assertNotIn(call("Face_shader", "HasSphereTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_readable_sphere_texture_skips_auto_resolve(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(sphere_texture_index=0, sphere_mode=1)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_sphere_texture"

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.ascii_texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_not_called()
        self.assertIn(
            call("Face_shader_sphere_texture", "fileTextureName", str(self.ascii_texture), "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_sphere_texture",
            self.ascii_texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_sphere_texture.outColor",
            "Face_shader.SphereTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasSphereTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_auto_resolves_unreadable_toon_texture_before_connect(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=0, shared_toon_flag=0)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.workspace.return_value = "C:/workspace/"
            mock_cmds.shadingNode.return_value = "Face_shader_toon_texture"
            mock_resolve.return_value = SimpleNamespace(status="resolved", cache_path="C:/ascii/toon.bmp")

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_called_once_with(
            original_path=self.texture.name,
            file_texture_path=str(self.texture),
            model_path=str(self.model),
            workspace_root="C:/workspace/",
        )
        self.assertIn(
            call("Face_shader_toon_texture", "fileTextureName", "C:/ascii/toon.bmp", "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_toon_texture",
            self.texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_set_custom_attributes.assert_any_call(
            "Face_shader_toon_texture",
            {
                "mmd_texture_cache_path": "C:/ascii/toon.bmp",
                "mmd_texture_unresolved": False,
            },
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_toon_texture.outColor",
            "Face_shader.ToonTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)
        self.assertEqual(converter.unresolved_texture_count, 0)

    def test_setup_dx11_shader_unreadable_toon_texture_stays_disconnected_when_auto_resolve_off(self):
        settings.set("import.model.auto_resolve_textures", False)
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=0, shared_toon_flag=0)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_toon_texture"

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_not_called()
        self.assertEqual(converter.unresolved_texture_count, 1)
        mock_mark.assert_called_once_with(
            "Face_shader_toon_texture",
            self.texture.name,
            str(self.model),
            unresolved=True,
        )
        mock_cmds.connectAttr.assert_not_called()
        self.assertNotIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_readable_toon_texture_skips_auto_resolve(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=0, shared_toon_flag=0)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_utils.mark_mmd_texture_file_node"
        ) as mock_mark, patch(
            "mmd_tools.converters.mesh_converter.resolve_texture_to_cache"
        ) as mock_resolve:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_toon_texture"

            converter._setup_dx11_shader(
                "Face_shader",
                material,
                None,
                [self.ascii_texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_resolve.assert_not_called()
        self.assertIn(
            call("Face_shader_toon_texture", "fileTextureName", str(self.ascii_texture), "string"),
            mock_set_attribute.call_args_list,
        )
        mock_mark.assert_called_once_with(
            "Face_shader_toon_texture",
            self.ascii_texture.name,
            str(self.model),
            unresolved=False,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_toon_texture.outColor",
            "Face_shader.ToonTexture",
            force=True,
        )
        self.assertIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)

    @staticmethod
    def _material(**overrides):
        values = {
            "material_index": 0,
            "name": "Face",
            "diffuse": [0.8, 0.7, 0.6, 1.0],
            "ambient": [0.1, 0.1, 0.1],
            "specular": [0.2, 0.2, 0.2],
            "specular_coefficient": 8.0,
            "edge_color": [0.0, 0.0, 0.0, 1.0],
            "edge_size": 0.0,
            "sphere_mode": 0,
            "sphere_texture_index": -1,
            "toon_texture_index": -1,
            "texture_index": 0,
            "draw_flag": 0,
            "memo": "",
            "shared_toon_flag": 0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
