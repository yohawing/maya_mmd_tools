"""Verify model-root ownership boundaries used by export collectors."""

import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import export_scene_collector as export_scene_collector_module  # noqa: E402
from mmd_tools.converters import morph_converter as morph_converter_module  # noqa: E402
from mmd_tools.converters.export_scene_collector import ExportSceneCollector  # noqa: E402
from mmd_tools.converters.material_shader_parameters import (  # noqa: E402
    ATTR_MMD_DIFFUSE_ALPHA,
    ATTR_MMD_EDGE_ALPHA,
)
from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_FLAG,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TOON_TEXTURE_INDEX,
)
from mmd_tools.converters.morph_converter import MorphConverter  # noqa: E402
from mmd_tools.core import maya_material_utils  # noqa: E402


class TestExportScope(unittest.TestCase):
    """Keep root-scoped network morph collection explicit and testable."""

    def _collect_single_material(self, attrs, is_pmd=False):
        """Collect one fake shading-group material through the Maya stubs."""
        attrs = dict(attrs)

        class FakeMesh:
            numPolygons = 1

            def getConnectedShaders(self, _instance):
                return ["sg1"], []

            def getPolygonVertices(self, _polygon_id):
                return [0, 1, 2]

        class FakeDependencyNode:
            def setObject(self, obj):
                self.obj = obj

            def name(self):
                return self.obj

        def attribute_query(attr, node, exists):
            return exists and node == "shader1" and attr in attrs

        def get_attr(path):
            return attrs[path.split(".", 1)[1]]

        with (
            mock.patch.object(export_scene_collector_module.cmds, "listConnections", return_value=["shader1"]),
            mock.patch.object(export_scene_collector_module.cmds, "attributeQuery", side_effect=attribute_query),
            mock.patch.object(export_scene_collector_module.cmds, "getAttr", side_effect=get_attr),
            mock.patch.object(
                export_scene_collector_module.om,
                "MFnDependencyNode",
                return_value=FakeDependencyNode(),
            ),
        ):
            materials, faces = export_scene_collector_module._collect_materials_per_face(
                "meshShape",
                FakeMesh(),
                is_pmd=is_pmd,
            )

        self.assertEqual(faces, [[2, 1, 0]])
        self.assertEqual(materials[0]["face_count"], 3)
        return materials[0]

    def test_mmd_shader_semantics_override_defaults_and_keep_provenance(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_NAME: "日本語材質",
                ATTR_MMD_MATERIAL_NAME_EN: "English Material",
                ATTR_MMD_DIFFUSE_COLOR: (0.1, 0.2, 0.3),
                ATTR_MMD_DIFFUSE_ALPHA: 0.4,
                ATTR_MMD_SPECULAR_COLOR: (0.5, 0.6, 0.7),
                ATTR_MMD_SHININESS: 12.5,
                ATTR_MMD_AMBIENT_COLOR: (0.2, 0.3, 0.4),
                ATTR_MMD_DRAW_FLAGS: 0x23,
                ATTR_MMD_EDGE_COLOR: (0.7, 0.8, 0.9),
                ATTR_MMD_EDGE_ALPHA: 0.6,
                ATTR_MMD_EDGE_SIZE: 2.5,
                ATTR_MMD_TEXTURE_INDEX: 4,
                ATTR_MMD_SPHERE_TEXTURE_INDEX: 5,
                ATTR_MMD_SPHERE_MODE: 2,
                ATTR_MMD_SHARED_TOON_FLAG: 1,
                ATTR_MMD_TOON_TEXTURE_INDEX: 6,
                ATTR_MMD_MEMO: "authored memo",
                "mmd_texture_path": "textures/body.png",
                ATTR_MMD_SPHERE_PATH: "textures/body.spa",
            }
        )

        self.assertEqual(material["name"], "日本語材質")
        self.assertEqual(material["name_english"], "English Material")
        self.assertEqual(material["diffuse"], [0.1, 0.2, 0.3, 0.4])
        self.assertEqual(material["specular"], [0.5, 0.6, 0.7])
        self.assertEqual(material["specular_coefficient"], 12.5)
        self.assertEqual(material["ambient"], [0.2, 0.3, 0.4])
        self.assertEqual(material["draw_flag"], 0x23)
        self.assertEqual(material["edge_color"], [0.7, 0.8, 0.9, 0.6])
        self.assertEqual(material["edge_size"], 2.5)
        self.assertNotIn("texture_index", material)
        self.assertNotIn("sphere_texture_index", material)
        self.assertEqual(material["source_texture_index"], 4)
        self.assertEqual(material["source_sphere_texture_index"], 5)
        self.assertEqual(material["sphere_mode"], 2)
        self.assertEqual(material["shared_toon_flag"], 1)
        self.assertEqual(material["toon_texture_index"], 6)
        self.assertEqual(material["memo"], "authored memo")
        self.assertEqual(material["texture_path"], "textures/body.png")
        self.assertEqual(material["sphere_texture_path"], "textures/body.spa")
        self.assertEqual(material["semantic_missing"], ["texture_table"])

    def test_mmd_shader_non_shared_toon_index_is_not_writer_facing_without_table(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_SHARED_TOON_FLAG: 0,
                ATTR_MMD_TOON_TEXTURE_INDEX: 2,
            }
        )

        self.assertNotIn("toon_texture_index", material)
        self.assertEqual(material["source_toon_texture_index"], 2)
        self.assertIn("texture_table", material["semantic_missing"])

    def test_mmd_shader_texture_path_without_index_is_fail_closed(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                "mmd_texture_path": "textures/body.png",
            }
        )

        self.assertEqual(material["texture_path"], "textures/body.png")
        self.assertIn("texture_table", material["semantic_missing"])

    def test_pmd_toon_index_stays_embedded_and_pmd_fields_are_collected(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_NAME: "PMD material",
                ATTR_MMD_MATERIAL_NAME_EN: "PMD material",
                ATTR_MMD_DIFFUSE_COLOR: (0.1, 0.2, 0.3),
                ATTR_MMD_DIFFUSE_ALPHA: 1.0,
                ATTR_MMD_SPECULAR_COLOR: (0.4, 0.5, 0.6),
                ATTR_MMD_SHININESS: 4.0,
                ATTR_MMD_AMBIENT_COLOR: (0.2, 0.3, 0.4),
                ATTR_MMD_DRAW_FLAGS: 0x10,
                ATTR_MMD_TOON_TEXTURE_INDEX: 3,
                ATTR_MMD_SPHERE_TEXTURE_INDEX: 1,
                ATTR_MMD_SPHERE_PATH: "textures/body.spa",
                "mmd_texture_path": "textures/body.bmp",
            },
            is_pmd=True,
        )

        self.assertEqual(material["specular_power"], 4.0)
        self.assertEqual(material["edge_flag"], 1)
        self.assertEqual(material["toon_texture_index"], 3)
        self.assertEqual(material["texture_file_name"], "textures/body.bmp*textures/body.spa")
        self.assertNotIn("texture_table", material["semantic_missing"])
        self.assertEqual(material["semantic_missing"], [])

    def test_pmd_direct_edge_flag_is_used_when_present(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_EDGE_FLAG: 1,
            },
            is_pmd=True,
        )

        self.assertEqual(material["edge_flag"], 1)
        self.assertNotIn("edge_flag", material["semantic_missing"])

    def test_pmd_does_not_require_unused_english_material_name(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_NAME: "PMD material",
            },
            is_pmd=True,
        )

        self.assertNotIn("name_english", material["semantic_missing"])

    def test_pmd_missing_shader_path_uses_file_node_provenance(self):
        with (
            mock.patch.object(
                maya_material_utils,
                "find_material_texture_file_node",
                return_value="file1",
            ),
            mock.patch.object(
                maya_material_utils,
                "get_mmd_original_texture_path",
                return_value="textures/missing.bmp",
            ),
        ):
            material = self._collect_single_material(
                {ATTR_MMD_MATERIAL: 1},
                is_pmd=True,
            )

        self.assertEqual(material["texture_file_name"], "textures/missing.bmp")
        self.assertNotIn("texture_path", material["semantic_missing"])

    def test_connected_texture_without_provenance_is_fail_closed(self):
        with (
            mock.patch.object(
                maya_material_utils,
                "find_material_texture_file_node",
                return_value="file1",
            ),
            mock.patch.object(
                maya_material_utils,
                "get_mmd_original_texture_path",
                return_value=None,
            ),
        ):
            material = self._collect_single_material(
                {ATTR_MMD_MATERIAL: 1},
                is_pmd=True,
            )

        self.assertIn("texture_path", material["semantic_missing"])

    def test_texture_provenance_lookup_error_is_fail_closed(self):
        with mock.patch.object(
            maya_material_utils,
            "find_material_texture_file_node",
            side_effect=RuntimeError("Maya connection lookup failed"),
        ):
            material = self._collect_single_material(
                {ATTR_MMD_MATERIAL: 1},
                is_pmd=True,
            )

        self.assertIn("texture_path", material["semantic_missing"])

    def test_pmd_empty_texture_path_with_index_is_fail_closed(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_TEXTURE_INDEX: 2,
                "mmd_texture_path": "",
            },
            is_pmd=True,
        )

        self.assertNotIn("texture_file_name", material)
        self.assertIn("texture_path", material["semantic_missing"])

    def test_absolute_texture_path_uses_file_node_provenance(self):
        with (
            mock.patch.object(
                maya_material_utils,
                "find_material_texture_file_node",
                return_value="file1",
            ),
            mock.patch.object(
                maya_material_utils,
                "get_mmd_original_texture_path",
                return_value="textures/body.png",
            ),
        ):
            material = self._collect_single_material(
                {
                    ATTR_MMD_MATERIAL: 1,
                    "mmd_texture_path": r"C:\resolved\body.png",
                }
            )

        self.assertEqual(material["texture_path"], "textures/body.png")
        self.assertNotIn("texture_path", material["semantic_missing"])

    def test_fractional_integer_semantic_is_missing_instead_of_truncated(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_DRAW_FLAGS: 1.5,
            }
        )

        self.assertNotIn("draw_flag", material)
        self.assertIn("draw_flag", material["semantic_missing"])

    def test_mmd_shader_missing_semantics_are_explicit_and_not_defaults(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_NAME: "Incomplete",
            }
        )

        self.assertIn("diffuse", material["semantic_missing"])
        self.assertIn("specular_coefficient", material["semantic_missing"])
        self.assertNotIn("diffuse", material)
        self.assertNotIn("specular_coefficient", material)
        self.assertNotEqual(material.get("diffuse"), [0.8, 0.8, 0.8, 1.0])
        self.assertNotEqual(material.get("specular_coefficient"), 5.0)

    def test_untagged_shader_keeps_legacy_material_defaults(self):
        material = self._collect_single_material({})

        self.assertEqual(material["name"], "shader1")
        self.assertEqual(material["diffuse"], [0.8, 0.8, 0.8, 1.0])
        self.assertEqual(material["specular_coefficient"], 5.0)
        self.assertNotIn("semantic_missing", material)

    def test_model_collector_passes_root_to_morph_collection(self):
        roots = []

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None):
                roots.append(root_group)
                return []

        mesh_data = {
            "vertices": [
                {
                    "position": [0.0, 0.0, 0.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [0.0, 0.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
                {
                    "position": [1.0, 0.0, 0.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [1.0, 0.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
                {
                    "position": [0.0, 0.0, 1.0],
                    "normal": [0.0, 1.0, 0.0],
                    "uv": [0.0, 1.0],
                    "bone_indices": [0],
                    "bone_weights": [1.0],
                },
            ],
            "faces": [[0, 1, 2]],
            "materials": [{"name": "material", "face_count": 3}],
            "bones": [],
            "morphs": [],
        }

        with (
            mock.patch.object(export_scene_collector_module, "MorphConverter", FakeMorphConverter),
            mock.patch.object(export_scene_collector_module, "_list_export_mesh_shapes", return_value=["mesh"]),
            mock.patch.object(export_scene_collector_module, "_collect_model_bones", return_value=[]),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module, "_collect_display_frames", return_value=[]),
            mock.patch.object(
                ExportSceneCollector,
                "collect_from_mesh",
                return_value=mesh_data,
            ) as collect_from_mesh,
            mock.patch.object(export_scene_collector_module, "_get_attr", return_value=""),
            mock.patch(
                "mmd_tools.converters.physics_export_collector.collect_physics_from_scene",
                return_value=([], []),
            ),
        ):
            payload = ExportSceneCollector().collect(
                {"target_model": "|hero:model_ROOT", "export_format": "pmd"}
            )

        self.assertEqual(roots, ["|hero:model_ROOT"])
        self.assertEqual(payload["model_name"], "Hero")
        collect_from_mesh.assert_called_once_with("mesh", is_pmd=True)

    def test_network_morph_collection_passes_selected_root(self):
        converter = object.__new__(MorphConverter)
        converter.logger = mock.Mock()

        with mock.patch.object(
            morph_converter_module,
            "iter_morph_network_metadata",
            return_value=[],
        ) as iterator:
            result = converter.collect_morphs_from_scene_for_export(
                root_group="|hero:model_ROOT",
            )

        self.assertEqual(result, [])
        iterator.assert_called_once_with(
            root_group="|hero:model_ROOT",
            morph_types={"bone", "material"},
        )


if __name__ == "__main__":
    unittest.main()
