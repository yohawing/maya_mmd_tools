"""Pure-Python checks for MeshConverter texture issue reporting."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters.mesh_converter import (  # noqa: E402
    MeshConverter,
    _set_dx11_color_uniform,
    bind_dx11_texture_file_node,
    migrate_legacy_glsl_diffuse_contracts,
    sync_dx11_generated_uniforms,
)
from mmd_tools.converters.material_shader_parameters import ATTR_MMD_DIFFUSE_ALPHA  # noqa: E402
from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.core import maya_material_utils  # noqa: E402


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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
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
                "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
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

    def test_set_dx11_color_uniform_skips_locked_generated_rgb_attr(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch("mmd_tools.converters.mesh_converter.LOGGER.warning") as mock_warning:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: (
                plug == "Face_shader.DiffuseColorRGB" if kwargs.get("lock") else None
            )
            mock_cmds.listConnections.return_value = []

            _set_dx11_color_uniform("Face_shader", "DiffuseColor", [0.1, 0.2, 0.3, 0.4])

        mock_set_attribute.assert_called_once_with(
            "Face_shader",
            "DiffuseColor",
            [0.1, 0.2, 0.3, 0.4],
            "double4",
        )
        self.assertNotIn(
            call("Face_shader.DiffuseColorRGB", 0.1, 0.2, 0.3, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        mock_warning.assert_not_called()

    def test_set_dx11_color_uniform_skips_connected_generated_rgb_attr(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ), patch("mmd_tools.converters.mesh_converter.LOGGER.warning") as mock_warning:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.getAttr.return_value = False
            mock_cmds.listConnections.side_effect = lambda plug, **_kwargs: (
                ["mayaInternal.output"] if plug == "Face_shader.DiffuseColorRGB" else []
            )

            _set_dx11_color_uniform("Face_shader", "DiffuseColor", [0.1, 0.2, 0.3, 0.4])

        self.assertNotIn(
            call("Face_shader.DiffuseColorRGB", 0.1, 0.2, 0.3, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        mock_warning.assert_not_called()

    def test_set_dx11_color_uniform_skips_protected_child_and_alpha_attrs(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ), patch("mmd_tools.converters.mesh_converter.LOGGER.warning") as mock_warning:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: (
                plug == "Face_shader.DiffuseColorR" if kwargs.get("lock") else None
            )
            mock_cmds.listConnections.side_effect = lambda plug, **_kwargs: (
                ["mayaInternal.output"] if plug == "Face_shader.DiffuseColorA" else []
            )

            _set_dx11_color_uniform("Face_shader", "DiffuseColor", [0.1, 0.2, 0.3, 0.4])

        self.assertNotIn(
            call("Face_shader.DiffuseColorRGB", 0.1, 0.2, 0.3, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        self.assertNotIn(call("Face_shader.DiffuseColorR", 0.1), mock_cmds.setAttr.call_args_list)
        self.assertIn(call("Face_shader.DiffuseColorG", 0.2), mock_cmds.setAttr.call_args_list)
        self.assertIn(call("Face_shader.DiffuseColorB", 0.3), mock_cmds.setAttr.call_args_list)
        self.assertNotIn(call("Face_shader.DiffuseColorA", 0.4), mock_cmds.setAttr.call_args_list)
        mock_warning.assert_not_called()

    def test_sync_migrates_legacy_glsl_vec4_without_changing_rgb_or_alpha(self):
        legacy = (0.17, 0.29, 0.41, 0.63)
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material",
                "DiffuseColor",
                "DiffuseColorRGB",
                "DiffuseColorA",
            }
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else (
                [legacy[:3]] if plug.endswith("DiffuseColorRGB") else legacy[3] if plug.endswith("DiffuseColorA") else [legacy]
            )
            mock_cmds.listConnections.return_value = []

            mock_cmds.ls.side_effect = lambda **kwargs: (
                ["legacy_glsl"] if kwargs.get("type") == "GLSLShader" else []
            )
            synced = sync_dx11_generated_uniforms()

        self.assertEqual(synced, 1)
        self.assertIn(
            call("legacy_glsl.DiffuseColorRGB", *legacy[:3], type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        self.assertIn(call("legacy_glsl.DiffuseColorA", legacy[3]), mock_cmds.setAttr.call_args_list)
        self.assertIn(
            call("legacy_glsl.mmdDiffuseRgbContractVersion", 1),
            mock_cmds.setAttr.call_args_list,
        )

    def test_scene_wide_markerless_current_glsl_does_not_migrate(self):
        pmx_rgb = (0.11, 0.22, 0.33)
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.ls.side_effect = lambda **kwargs: ["current_glsl"] if kwargs.get("type") == "GLSLShader" else []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "diffuse_color", "Opacity", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"
            }
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else (
                [pmx_rgb] if plug.endswith(".diffuse_color") else 0.44
            )
            mock_cmds.listConnections.return_value = []

            synced = sync_dx11_generated_uniforms()

        self.assertEqual(synced, 1)
        self.assertIn(
            call("current_glsl.DiffuseColorRGB", *pmx_rgb, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        self.assertNotIn(call("current_glsl.DiffuseColorRGB", 0.8, 0.8, 0.8, type="double3"), mock_cmds.setAttr.call_args_list)

    def test_dedicated_legacy_migration_does_not_write_current_glsl_or_dx11(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.return_value = ["current_glsl"]
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "diffuse_color", "Opacity", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"
            }

            self.assertEqual(migrate_legacy_glsl_diffuse_contracts(), 0)

        mock_cmds.setAttr.assert_not_called()
        self.assertEqual(mock_cmds.ls.call_args_list, [call(type="GLSLShader")])

    def test_scene_wide_current_glsl_marker_preserves_existing_rgb_and_alpha(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.side_effect = lambda **kwargs: ["current_glsl"] if kwargs.get("type") == "GLSLShader" else []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr == "mmdDiffuseRgbContractVersion"
            mock_cmds.getAttr.return_value = 1

            synced = sync_dx11_generated_uniforms()

        self.assertEqual(synced, 0)
        mock_cmds.setAttr.assert_not_called()

    def test_scene_wide_legacy_glsl_migration_is_idempotent_after_marker(self):
        legacy = (0.2, 0.3, 0.4, 0.5)
        marker_exists = False

        def attribute_query(attr, **_kwargs):
            if attr == "mmdDiffuseRgbContractVersion":
                return marker_exists
            return attr in {"mmd_material", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"}

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.side_effect = lambda **kwargs: ["legacy_glsl"] if kwargs.get("type") == "GLSLShader" else []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = attribute_query
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else (
                [legacy[:3]] if plug.endswith("DiffuseColorRGB") else legacy[3] if plug.endswith("DiffuseColorA") else [legacy]
            )
            mock_cmds.listConnections.return_value = []

            self.assertEqual(sync_dx11_generated_uniforms(), 1)
            marker_exists = True
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else 1
            first_calls = list(mock_cmds.setAttr.call_args_list)
            self.assertEqual(sync_dx11_generated_uniforms(), 0)

        self.assertEqual(mock_cmds.setAttr.call_args_list, first_calls)

    def test_legacy_glsl_failed_write_is_not_marked_and_retries(self):
        legacy = (0.2, 0.3, 0.4, 0.5)
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.side_effect = lambda **kwargs: ["legacy_glsl"] if kwargs.get("type") == "GLSLShader" else []
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"
            }
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else (
                [legacy[:3]] if plug.endswith("DiffuseColorRGB") else legacy[3] if plug.endswith("DiffuseColorA") else [legacy]
            )
            mock_cmds.listConnections.return_value = []
            mock_cmds.setAttr.side_effect = RuntimeError("write failed")

            self.assertEqual(sync_dx11_generated_uniforms(), 0)
            self.assertEqual(sync_dx11_generated_uniforms(), 0)

        marker_calls = [
            item for item in mock_cmds.setAttr.call_args_list if item.args[0].endswith("mmdDiffuseRgbContractVersion")
        ]
        self.assertEqual(marker_calls, [])
        rgb_attempts = [
            item for item in mock_cmds.setAttr.call_args_list if item.args[0].endswith("DiffuseColorRGB")
        ]
        self.assertGreaterEqual(len(rgb_attempts), 2)

    def test_driven_legacy_glsl_is_not_frozen_or_marked(self):
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.LOGGER.warning"
        ) as warning:
            mock_cmds.ls.return_value = ["driven_legacy"]
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"
            }
            mock_cmds.listConnections.side_effect = lambda plug, **_kwargs: (
                ["animCurve.output"] if plug == "driven_legacy.DiffuseColor" else []
            )

            self.assertEqual(migrate_legacy_glsl_diffuse_contracts(), 0)

        mock_cmds.setAttr.assert_not_called()
        warning.assert_called_once()

    def test_legacy_glsl_alpha_write_failure_rolls_back_rgb_and_alpha(self):
        original_rgb = (0.8, 0.7, 0.6)
        original_alpha = 0.9
        state = {"rgb": original_rgb, "alpha": original_alpha}

        def get_attr(plug, **kwargs):
            if kwargs.get("lock"):
                return False
            if plug.endswith("DiffuseColorRGB"):
                return [state["rgb"]]
            if plug.endswith("DiffuseColorA"):
                return state["alpha"]
            return [(0.2, 0.3, 0.4, 0.5)]

        alpha_failed = False

        def set_attr(plug, *values, **_kwargs):
            nonlocal alpha_failed
            if plug.endswith("DiffuseColorRGB"):
                state["rgb"] = tuple(values[:3])
            elif plug.endswith("DiffuseColorA"):
                if not alpha_failed:
                    alpha_failed = True
                    raise RuntimeError("alpha write failed")
                state["alpha"] = values[0]

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.return_value = ["legacy_glsl"]
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA"
            }
            mock_cmds.listConnections.return_value = []
            mock_cmds.getAttr.side_effect = get_attr
            mock_cmds.setAttr.side_effect = set_attr

            self.assertEqual(migrate_legacy_glsl_diffuse_contracts(), 0)

        self.assertEqual(state["rgb"], original_rgb)
        self.assertEqual(state["alpha"], original_alpha)
        marker_calls = [
            item for item in mock_cmds.setAttr.call_args_list if item.args[0].endswith("mmdDiffuseRgbContractVersion")
        ]
        self.assertEqual(marker_calls, [])

    def test_marker_set_failure_preserves_preexisting_zero_marker(self):
        state = {"rgb": (0.8, 0.7, 0.6), "alpha": 0.9, "marker": 0}
        marker_set_attempts = 0

        def get_attr(plug, **kwargs):
            if kwargs.get("lock"):
                return False
            if plug.endswith("DiffuseColorRGB"):
                return [state["rgb"]]
            if plug.endswith("DiffuseColorA"):
                return state["alpha"]
            if plug.endswith("mmdDiffuseRgbContractVersion"):
                return state["marker"]
            return [(0.2, 0.3, 0.4, 0.5)]

        def set_attr(plug, *values, **_kwargs):
            nonlocal marker_set_attempts
            if plug.endswith("DiffuseColorRGB"):
                state["rgb"] = tuple(values[:3])
            elif plug.endswith("DiffuseColorA"):
                state["alpha"] = values[0]
            elif plug.endswith("mmdDiffuseRgbContractVersion"):
                marker_set_attempts += 1
                if marker_set_attempts == 1:
                    raise RuntimeError("marker set failed")
                state["marker"] = values[0]

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds:
            mock_cmds.ls.return_value = ["legacy_glsl"]
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "mmd_material", "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA",
                "mmdDiffuseRgbContractVersion",
            }
            # A value-0 marker is intentionally incomplete and remains a migration candidate.
            mock_cmds.listConnections.return_value = []
            mock_cmds.getAttr.side_effect = get_attr
            mock_cmds.setAttr.side_effect = set_attr

            self.assertEqual(migrate_legacy_glsl_diffuse_contracts(), 0)

        self.assertEqual(state["marker"], 0)
        mock_cmds.deleteAttr.assert_not_called()

    def test_sync_explicit_new_glsl_uses_pmx_custom_diffuse_not_compat_default(self):
        pmx_rgb = (0.12, 0.34, 0.56)
        pmx_alpha = 0.78
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "DiffuseColor",
                "DiffuseColorRGB",
                "DiffuseColorA",
                "diffuse_color",
                "Opacity",
                ATTR_MMD_DIFFUSE_ALPHA,
            }
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: (
                False
                if kwargs.get("lock")
                else [pmx_rgb]
                if plug.endswith(".diffuse_color")
                else pmx_alpha
                if plug.endswith(f".{ATTR_MMD_DIFFUSE_ALPHA}")
                else pmx_alpha
                if plug.endswith(".Opacity")
                else [pmx_rgb]
                if plug.endswith(".DiffuseColorRGB")
                else pmx_alpha
                if plug.endswith(".DiffuseColorA")
                else [(0.8, 0.8, 0.8, 1.0)]
            )
            mock_cmds.listConnections.return_value = []

            synced = sync_dx11_generated_uniforms(["new_glsl"])

        self.assertEqual(synced, 1)
        self.assertIn(
            call("new_glsl.DiffuseColorRGB", *pmx_rgb, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )
        self.assertIn(call("new_glsl.DiffuseColorA", pmx_alpha), mock_cmds.setAttr.call_args_list)
        self.assertIn(call("new_glsl.Opacity", 1.0), mock_cmds.setAttr.call_args_list)
        self.assertIn(
            call("new_glsl.mmdDiffuseRgbContractVersion", 1),
            mock_cmds.setAttr.call_args_list,
        )
        self.assertNotIn(
            call("new_glsl.DiffuseColorRGB", 0.8, 0.8, 0.8, type="double3"),
            mock_cmds.setAttr.call_args_list,
        )

    def test_current_glsl_write_failure_does_not_set_contract_marker(self):
        pmx_rgb = (0.12, 0.34, 0.56)
        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ):
            mock_cmds.objExists.return_value = True
            mock_cmds.nodeType.return_value = "GLSLShader"
            mock_cmds.attributeQuery.side_effect = lambda attr, **_kwargs: attr in {
                "DiffuseColor", "DiffuseColorRGB", "DiffuseColorA", "diffuse_color", "Opacity"
            }
            mock_cmds.listConnections.return_value = []
            mock_cmds.getAttr.side_effect = lambda plug, **kwargs: False if kwargs.get("lock") else (
                [pmx_rgb] if plug.endswith(".diffuse_color") else 0.7 if plug.endswith(".Opacity") else [(0.8, 0.8, 0.8)]
            )
            mock_cmds.setAttr.side_effect = RuntimeError("write failed")

            sync_dx11_generated_uniforms(["current_glsl"])

        marker_calls = [
            item for item in mock_cmds.setAttr.call_args_list if item.args[0].endswith("mmdDiffuseRgbContractVersion")
        ]
        self.assertEqual(marker_calls, [])

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
        self.assertEqual(len(issue["search_candidates"]), 1)
        self.assertEqual(Path(issue["search_candidates"][0]["path"]), self.texture)
        self.assertTrue(issue["search_candidates"][0]["accepted"])
        self.assertTrue(issue["path_diagnostics"]["current_path_has_non_ascii"])
        self.assertTrue(issue["path_diagnostics"]["original_path_has_non_ascii"])
        self.assertEqual(issue["path_diagnostics"]["current_path_unreadable_reason"], "non_ascii_path")
        self.assertEqual(converter.unresolved_texture_count, 1)
        self.assertEqual(converter.profile["unresolved_texture_count"], 1)
        self.assertEqual(converter.profile["unresolved_textures"], [issue])
        json.dumps(issue)

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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ) as mock_set_custom_attributes, patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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
        self.assertIn(call("Face_shader", "HasToonTexture", 0, "long"), mock_set_attribute.call_args_list)
        self.assertNotIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_absent_toon_keeps_has_flag_disabled(self):
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=-1, shared_toon_flag=0)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ):
            mock_cmds.attributeQuery.return_value = True

            converter._setup_dx11_shader(
                "Face_shader", material, None, [self.ascii_texture.name], is_pmd=False, material_index=0
            )

        self.assertIn(call("Face_shader", "HasToonTexture", 0, "long"), mock_set_attribute.call_args_list)
        self.assertNotIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)
        mock_cmds.connectAttr.assert_not_called()

    def test_setup_dx11_shader_binds_resolved_shared_toon_and_enables_has_flag(self):
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=0, shared_toon_flag=1)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter._resolve_pmx_toon_texture_path",
            return_value=str(self.ascii_texture),
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
        ) as mock_mark:
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.shadingNode.return_value = "Face_shader_toon_texture"

            converter._setup_dx11_shader(
                "Face_shader", material, None, [], is_pmd=False, material_index=0
            )

        mock_mark.assert_called_once_with(
            "Face_shader_toon_texture",
            "",
            str(self.model),
            unresolved=False,
            source_kind="shared_toon",
            shared_toon_id="shared_toon:1",
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_shader_toon_texture.outColor", "Face_shader.ToonTexture", force=True
        )
        self.assertIn(call("Face_shader", "HasToonTexture", 1, "long"), mock_set_attribute.call_args_list)

    def test_setup_dx11_shader_readable_toon_texture_skips_auto_resolve(self):
        settings.set("import.model.auto_resolve_textures", True)
        converter = MeshConverter(str(self.model))
        material = self._material(toon_texture_index=0, shared_toon_flag=0)

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
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

    def test_setup_glsl_shader_binds_main_sphere_and_toon_texture_slots(self):
        """GLSL setup must honor the same texture-slot contract as DX11."""
        converter = MeshConverter(str(self.model))
        material = self._material(
            sphere_texture_index=0,
            sphere_mode=1,
            toon_texture_index=0,
            shared_toon_flag=0,
        )

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter._ensure_mmd_shader_uniform_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter._set_shader_attribute_checked",
            return_value=True,
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
        ):
            mock_cmds.attributeQuery.return_value = True
            mock_cmds.listConnections.return_value = []
            mock_cmds.shadingNode.side_effect = lambda _type, **kwargs: kwargs["name"]

            result = converter._setup_glsl_shader(
                "Face_shader",
                material,
                self.ascii_texture.name,
                [self.ascii_texture.name],
                is_pmd=False,
                material_index=0,
            )

        self.assertTrue(result)
        for suffix, texture_attr, flag_attr in (
            ("_texture", "MainTexture", "HasMainTexture"),
            ("_sphere_texture", "SphereTexture", "HasSphereTexture"),
            ("_toon_texture", "ToonTexture", "HasToonTexture"),
        ):
            mock_cmds.connectAttr.assert_any_call(
                f"Face_shader{suffix}.outColor",
                f"Face_shader.{texture_attr}",
                force=True,
            )
            self.assertIn(call("Face_shader", flag_attr, 1, "long"), mock_set_attribute.call_args_list)

    def test_glsl_effect_declares_uv_and_all_mmd_texture_samplers(self):
        source = (Path(__file__).parents[2] / "mmd_tools" / "shaders" / "MMDShader.ogsfx").read_text(
            encoding="utf-8"
        )
        self.assertIn("vec2 UVset0   : TEXCOORD0", source)
        self.assertIn("vec3 sphereNormal = normalize((View * vec4(n, 0.0)).xyz)", source)
        for texture, sampler in (
            ("MainTexture", "MainSampler"),
            ("SphereTexture", "SphereSampler"),
            ("ToonTexture", "ToonSampler"),
        ):
            self.assertIn(f"uniform texture2D {texture}", source)
            self.assertIn(f"Texture = <{texture}>;", source)
            self.assertIn(f"texture2D({sampler}", source)

    def test_glsl_effect_matches_dx11_light_color_and_gamma_contract(self):
        source = (Path(__file__).parents[2] / "mmd_tools" / "shaders" / "MMDShader.ogsfx").read_text(
            encoding="utf-8"
        )
        self.assertIn("uniform vec3 MmdControllerLightVector", source)
        self.assertIn("uniform vec3 MmdControllerLightRgb", source)
        self.assertIn("vec3 lightDir = -normalize(MmdControllerLightVector)", source)
        self.assertIn("float halfLambert = ndotl * 0.5 + 0.5", source)
        self.assertIn("vec3 srgbToLinear(vec3 color)", source)
        self.assertIn("colorOut = vec4(srgbToLinear(lighting), opacity)", source)
        self.assertIn("if (texColor.a < 0.003 || opacity <= 0.0)", source)
        self.assertIn("discard;", source)

    def test_dx11_and_glsl_effects_keep_no_toon_diffuse_flat(self):
        shader_dir = Path(__file__).parents[2] / "mmd_tools" / "shaders"
        dx11 = (shader_dir / "MMDShader.fx").read_text(encoding="utf-8")
        glsl = (shader_dir / "MMDShader.ogsfx").read_text(encoding="utf-8")

        self.assertIn(
            "saturate(DiffuseColorRGB * lightColor + AmbientColor) * texColor.rgb", dx11
        )
        self.assertIn("toonColor = float3(1.0, 1.0, 1.0)", dx11)
        self.assertNotIn("toonColor = rampCoord.xxx", dx11)
        self.assertIn("float3 diffuse = materialBase * shadow", dx11)
        self.assertIn("diffuse *= toonColor;", dx11)
        self.assertNotIn("diffuse *= toonColor * shadow", dx11)
        self.assertIn(
            "clamp(DiffuseColorRGB * lightColor + AmbientColor, 0.0, 1.0) * texColor.rgb",
            glsl,
        )
        self.assertIn("toonColor = vec3(1.0)", glsl)
        self.assertNotIn("toonColor = vec3(rampCoord)", glsl)
        self.assertIn("uniform float ShadowAttenuation = 1.0", glsl)
        self.assertIn("vec3 diffuse = materialBase * ShadowAttenuation", glsl)
        self.assertIn("diffuse *= toonColor;", glsl)

    def test_setup_standard_shader_multiplies_texture_alpha_by_pmx_alpha(self):
        """Resolved fallback textures must drive opacity through PMX alpha."""
        converter = MeshConverter(str(self.model))
        material = self._material(diffuse=[0.8, 0.7, 0.6, 0.25])

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
        ):
            mock_cmds.shadingNode.side_effect = lambda _type, **kwargs: kwargs["name"]

            converter._setup_standard_shader(
                "Face_shader",
                material,
                self.ascii_texture.name,
                [self.ascii_texture.name],
                is_pmd=False,
                material_index=0,
            )

        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outAlpha",
            "Face_opacityMultiply.input1X",
            force=True,
        )
        for channel in "RGB":
            mock_cmds.connectAttr.assert_any_call(
                "Face_opacityMultiply.outputX",
                f"Face_shader.opacity{channel}",
                force=True,
            )
        self.assertIn(
            call("Face_opacityMultiply", "operation", 1, "long"),
            mock_set_attribute.call_args_list,
        )
        self.assertIn(
            call("Face_opacityMultiply", "input2X", 0.25, "float"),
            mock_set_attribute.call_args_list,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outColor",
            "Face_diffuseMultiply.input1",
            force=True,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_diffuseMultiply.output",
            "Face_shader.baseColor",
            force=True,
        )
        self.assertIn(
            call("Face_diffuseMultiply", "operation", 1, "long"),
            mock_set_attribute.call_args_list,
        )
        for channel, value in zip("XYZ", (0.8, 0.7, 0.6)):
            self.assertIn(
                call("Face_diffuseMultiply", f"input2{channel}", value, "float"),
                mock_set_attribute.call_args_list,
            )
        self.assertIn(
            call("Face_shader", "emission", 1.0, "float"),
            mock_set_attribute.call_args_list,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outColor",
            "Face_ambientMultiply.input1",
            force=True,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_ambientMultiply.output",
            "Face_shader.emissionColor",
            force=True,
        )
        self.assertIn(
            call("Face_ambientMultiply", "operation", 1, "long"),
            mock_set_attribute.call_args_list,
        )
        for channel, value in zip("XYZ", (0.1, 0.1, 0.1)):
            self.assertIn(
                call("Face_ambientMultiply", f"input2{channel}", value, "float"),
                mock_set_attribute.call_args_list,
            )

    def test_standard_surface_texture_repair_discovers_file_through_diffuse_multiply(self):
        """Texture repair must find the file node behind the tint utility."""
        def node_type(node):
            return {
                "Face_shader": "standardSurface",
                "Face_diffuseMultiply": "multiplyDivide",
                "Face_file": "file",
            }.get(node, "transform")

        def list_connections(plug, **_kwargs):
            return {
                "Face_shader.baseColor": ["Face_diffuseMultiply.output"],
                "Face_diffuseMultiply.input1": ["Face_file.outColor"],
            }.get(plug, [])

        with patch.object(maya_material_utils, "cmds") as mock_cmds:
            mock_cmds.nodeType.side_effect = node_type
            mock_cmds.attributeQuery.return_value = False
            mock_cmds.listConnections.side_effect = list_connections

            file_node = maya_material_utils.find_material_texture_file_node("Face_shader")

        self.assertEqual(file_node, "Face_file")

    def test_setup_standard_shader_without_texture_keeps_ambient_as_additive_color(self):
        """A textureless fallback uses PMX ambient directly as its additive term."""
        converter = MeshConverter(str(self.model))
        material = self._material(ambient=[0.12, 0.23, 0.34])

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ):
            converter._setup_standard_shader(
                "Face_shader",
                material,
                None,
                [],
                is_pmd=False,
                material_index=0,
            )

        self.assertIn(
            call("Face_shader", "baseColor", [0.8, 0.7, 0.6], "double3"),
            mock_set_attribute.call_args_list,
        )
        self.assertIn(
            call("Face_shader", "emission", 1.0, "float"),
            mock_set_attribute.call_args_list,
        )
        self.assertIn(
            call("Face_shader", "emissionColor", (0.12, 0.23, 0.34), "double3"),
            mock_set_attribute.call_args_list,
        )
        mock_cmds.shadingNode.assert_not_called()

    def test_setup_standard_shader_keeps_unresolved_texture_repair_connections(self):
        """Unresolved standard textures stay discoverable by the repair path."""
        settings.set("import.model.auto_resolve_textures", False)
        converter = MeshConverter(str(self.model))
        material = self._material()

        with patch("mmd_tools.converters.mesh_converter.cmds") as mock_cmds, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_attribute"
        ) as mock_set_attribute, patch(
            "mmd_tools.converters.mesh_converter.maya_attribute_utils.set_custom_attributes"
        ), patch(
            "mmd_tools.converters.mesh_converter.maya_material_utils.mark_mmd_texture_file_node"
        ):
            mock_cmds.shadingNode.side_effect = lambda _type, **kwargs: kwargs["name"]

            converter._setup_standard_shader(
                "Face_shader",
                material,
                "missing.png",
                ["missing.png"],
                is_pmd=False,
                material_index=0,
            )

        mock_cmds.connectAttr.assert_any_call(
            "Face_place2dTexture.outUV",
            "Face_file.uvCoord",
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outColor",
            "Face_diffuseMultiply.input1",
            force=True,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_diffuseMultiply.output",
            "Face_shader.baseColor",
            force=True,
        )
        for channel, value in zip("XYZ", (0.8, 0.7, 0.6)):
            self.assertIn(
                call("Face_diffuseMultiply", f"input2{channel}", value, "float"),
                mock_set_attribute.call_args_list,
            )
        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outColor",
            "Face_ambientMultiply.input1",
            force=True,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_ambientMultiply.output",
            "Face_shader.emissionColor",
            force=True,
        )
        mock_cmds.connectAttr.assert_any_call(
            "Face_file.outAlpha",
            "Face_opacityMultiply.input1X",
            force=True,
        )
        for channel in "RGB":
            mock_cmds.connectAttr.assert_any_call(
                "Face_opacityMultiply.outputX",
                f"Face_shader.opacity{channel}",
                force=True,
            )
        self.assertIn(
            call("Face_opacityMultiply", "input2X", 1.0, "float"),
            mock_set_attribute.call_args_list,
        )
        self.assertEqual(converter.unresolved_texture_count, 1)
        self.assertEqual(converter.profile["unresolved_textures"][0]["reason"], "missing_file")

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
