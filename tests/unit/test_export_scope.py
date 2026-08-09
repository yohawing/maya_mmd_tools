"""Verify model-root ownership boundaries used by export collectors."""

import json
import unittest
from types import SimpleNamespace
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
    ATTR_MMD_ADDITIONAL_UVS_JSON,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MATERIAL_INDEX,
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
    ATTR_MMD_TOON_PATH,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
)
from mmd_tools.converters.morph_converter import MorphConverter  # noqa: E402
from mmd_tools.core import maya_material_utils  # noqa: E402


class TestExportScope(unittest.TestCase):
    """Keep root-scoped network morph collection explicit and testable."""

    def test_additional_uv_storage_readback_is_strict_and_vertex_ordered(self):
        payload = {
            "schema_version": 1,
            "vertex_count": 2,
            "source_vertex_count": 3,
            "channel_count": 1,
            "source_vertex_indices": [2, 0],
            "additional_uvs": [
                [[0.1, 0.2, 0.3, 0.4]],
                [[1.1, 1.2, 1.3, 1.4]],
            ],
        }

        with mock.patch.object(
            export_scene_collector_module.cmds,
            "attributeQuery",
            side_effect=lambda attr, node, exists: attr == ATTR_MMD_ADDITIONAL_UVS_JSON
            and node == "meshTransform",
        ), mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value=json.dumps(payload),
        ):
            values, error = export_scene_collector_module._read_additional_uv_storage(
                "meshTransform",
                "meshShape",
                2,
                expected_channel_count=1,
            )

        self.assertIsNone(error)
        self.assertEqual(values, payload["additional_uvs"])

    def test_additional_uv_storage_missing_for_imported_channels_is_fail_closed(self):
        with mock.patch.object(
            export_scene_collector_module.cmds,
            "attributeQuery",
            return_value=False,
        ):
            values, error = export_scene_collector_module._read_additional_uv_storage(
                "meshTransform",
                "meshShape",
                2,
                expected_channel_count=1,
            )

        self.assertIsNone(values)
        self.assertEqual(error, "additional_uvs_storage")

    def _collect_single_material(self, attrs):
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
            )

        self.assertEqual(faces, [[2, 1, 0]])
        self.assertEqual(materials[0]["face_count"], 3)
        return materials[0]

    def _collect_two_material_groups(self, poly_shader_indices, shader_attrs):
        """Collect two fake material groups with controllable face order."""
        class FakeMesh:
            numPolygons = 2

            def getConnectedShaders(self, _instance):
                return ["sg_a", "sg_b"], poly_shader_indices

            def getPolygonVertices(self, polygon_id):
                return [[0, 1, 2], [3, 4, 5]][polygon_id]

        class FakeDependencyNode:
            def setObject(self, obj):
                self.obj = obj

            def name(self):
                return self.obj

        def list_connections(path):
            if path.endswith(".surfaceShader"):
                return ["shader_a" if path.startswith("sg_a") else "shader_b"]
            return []

        def attribute_query(attr, node, exists):
            return exists and attr in shader_attrs[node]

        def get_attr(path):
            node, attr = path.rsplit(".", 1)
            return shader_attrs[node][attr]

        with (
            mock.patch.object(
                export_scene_collector_module.cmds,
                "listConnections",
                side_effect=list_connections,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ),
            mock.patch.object(
                export_scene_collector_module.om,
                "MFnDependencyNode",
                return_value=FakeDependencyNode(),
            ),
        ):
            return export_scene_collector_module._collect_materials_per_face(
                "meshShape",
                FakeMesh(),
            )

    def _collect_mock_model_root(self, mesh_data_by_shape):
        """Collect mocked child meshes through the real model-root merge path."""

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None, require_contiguous=True):
                return []

        with (
            mock.patch.object(export_scene_collector_module, "MorphConverter", FakeMorphConverter),
            mock.patch.object(
                export_scene_collector_module,
                "_list_export_mesh_shapes",
                return_value=list(mesh_data_by_shape),
            ),
            mock.patch.object(export_scene_collector_module, "_collect_model_bones", return_value=[]),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module, "_collect_display_frames", return_value=[]),
            mock.patch.object(
                ExportSceneCollector,
                "collect_from_mesh",
                side_effect=lambda shape, **_kwargs: mesh_data_by_shape[shape],
            ),
            mock.patch.object(export_scene_collector_module, "_get_attr", return_value=None),
            mock.patch(
                "mmd_tools.converters.physics_export_collector.collect_physics_from_scene",
                return_value=([], []),
            ),
        ):
            return ExportSceneCollector().collect_from_model_root("|hero:model_ROOT")

    @staticmethod
    def _mock_root_mesh(material):
        """Build one minimal triangle child payload for root merge tests."""
        return {
            "vertices": [{}, {}, {}],
            "faces": [[0, 1, 2]],
            "materials": [material],
            "bones": [],
            "morphs": [],
        }

    def test_collect_bones_resolves_ik_source_indices_and_authored_names(self):
        """IK refs use exported order, including name-based link references."""
        joints = ["|model|link", "|model|solver", "|model|target"]
        attrs = {
            ("|model|link", ATTR_MMD_BONE_INDEX): 7,
            ("|model|link", ATTR_MMD_BONE_PARENT_INDEX): -1,
            ("|model|link", ATTR_MMD_BONE_NAME): "Link",
            ("|model|link", ATTR_MMD_BONE_NAME_EN): "Link",
            ("|model|solver", ATTR_MMD_BONE_INDEX): 42,
            ("|model|solver", ATTR_MMD_BONE_PARENT_INDEX): 7,
            ("|model|solver", ATTR_MMD_BONE_NAME): "Solver",
            ("|model|solver", ATTR_MMD_BONE_NAME_EN): "Solver",
            ("|model|solver", ATTR_MMD_BONE_FLAGS): 0x003E,
            ("|model|solver", ATTR_MMD_IK_TARGET_INDEX): 100,
            ("|model|solver", ATTR_MMD_IK_LOOP): 8,
            ("|model|solver", ATTR_MMD_IK_LIMIT_ANGLE): 0.5,
            ("|model|solver", ATTR_MMD_IK_LINKS): json.dumps(
                [{"bone": "Link", "limit_enabled": False}]
            ),
            ("|model|target", ATTR_MMD_BONE_INDEX): 100,
            ("|model|target", ATTR_MMD_BONE_PARENT_INDEX): 42,
            ("|model|target", ATTR_MMD_BONE_NAME): "Target",
            ("|model|target", ATTR_MMD_BONE_NAME_EN): "Target",
        }

        def attribute_query(attr, node, exists):
            return exists and (node, attr) in attrs

        def get_attr(path):
            node, attr = path.rsplit(".", 1)
            return attrs[(node, attr)]

        with (
            mock.patch.object(
                export_scene_collector_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "xform",
                return_value=[0.0, 0.0, 0.0],
            ),
        ):
            bones, export_index_by_joint = export_scene_collector_module._collect_bones_from_joints(
                joints
            )

        self.assertEqual(export_index_by_joint["|model|link"], 0)
        solver = bones[1]
        self.assertEqual(solver["bone_flag"] & 0x0020, 0x0020)
        self.assertEqual(solver["ik_target_bone_index"], 2)
        self.assertEqual(solver["ik_loop_count"], 8)
        self.assertAlmostEqual(solver["ik_limit_angle"], 0.5)
        self.assertEqual(solver["ik_links"][0]["bone"], 0)

    def test_collect_bones_ignores_presenter_ik_defaults_on_non_ik_bone(self):
        """UI-created empty IK fields do not change ordinary bone export."""
        joint = "|model|plain"
        attrs = {
            (joint, ATTR_MMD_BONE_INDEX): 0,
            (joint, ATTR_MMD_BONE_PARENT_INDEX): -1,
            (joint, ATTR_MMD_BONE_NAME): "Plain",
            (joint, ATTR_MMD_BONE_NAME_EN): "Plain",
            (joint, ATTR_MMD_BONE_FLAGS): 0x000A,
            (joint, ATTR_MMD_IK_TARGET): "",
            (joint, ATTR_MMD_IK_LOOP): 10,
            (joint, ATTR_MMD_IK_LIMIT_ANGLE): 2.0,
            (joint, ATTR_MMD_IK_LINKS): "[]",
        }

        def attribute_query(attr, node, exists):
            return exists and (node, attr) in attrs

        def get_attr(path):
            node, attr = path.rsplit(".", 1)
            return attrs[(node, attr)]

        with (
            mock.patch.object(
                export_scene_collector_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "xform",
                return_value=[0.0, 0.0, 0.0],
            ),
        ):
            bones, _ = export_scene_collector_module._collect_bones_from_joints([joint])

        self.assertEqual(bones[0]["bone_flag"], 0x000A)
        self.assertNotIn("ik_target_bone_index", bones[0])
        self.assertNotIn("ik_loop_count", bones[0])
        self.assertNotIn("ik_limit_angle", bones[0])
        self.assertNotIn("ik_links", bones[0])

    def test_collect_bones_preserves_authored_non_ik_semantics(self):
        """Canonical PMX fields reach the collector without writer defaults."""
        root = "|model|root"
        child = "|model|child"
        authored_flags = (
            0x0001  # CONNECT_BONE
            | 0x0002 | 0x0004 | 0x0008 | 0x0010
            | 0x0080  # LOCAL
            | 0x0100 | 0x0200  # grant rotate/move
            | 0x0400 | 0x0800  # fixed/local axis
            | 0x1000 | 0x2000  # after physics/external parent
        )
        attrs = {
            (root, ATTR_MMD_BONE_INDEX): 10,
            (root, ATTR_MMD_BONE_PARENT_INDEX): -1,
            (root, ATTR_MMD_BONE_NAME): "Root",
            (root, ATTR_MMD_BONE_NAME_EN): "Root",
            (child, ATTR_MMD_BONE_INDEX): 20,
            (child, ATTR_MMD_BONE_PARENT_INDEX): 10,
            (child, ATTR_MMD_BONE_NAME): "Child",
            (child, ATTR_MMD_BONE_NAME_EN): "Child",
            (child, ATTR_MMD_BONE_FLAGS): authored_flags,
            (child, ATTR_MMD_DEFORM_LAYER): 7,
            (child, ATTR_MMD_PMX_REST_POSITION): [7.0, 8.0, 9.0],
            (child, ATTR_MMD_CONNECTION_BONE): "Root",
            (child, ATTR_MMD_CONNECT_INDEX): 10,
            (child, ATTR_MMD_CONNECT_BONE_INDEX): 10,
            (child, ATTR_MMD_GRANT_PARENT): "Root",
            (child, ATTR_MMD_GRANT_PARENT_INDEX): 10,
            (child, ATTR_MMD_GRANT_RATE): 0.25,
            (child, ATTR_MMD_FIXED_AXIS): [0.0, 1.0, 0.0],
            (child, ATTR_MMD_AXIS_DIRECTION): [0.0, 1.0, 0.0],
            (child, ATTR_MMD_LOCAL_X_AXIS): [1.0, 0.0, 0.0],
            (child, ATTR_MMD_X_AXIS_DIRECTION): [1.0, 0.0, 0.0],
            (child, ATTR_MMD_LOCAL_Z_AXIS): [0.0, 0.0, 1.0],
            (child, ATTR_MMD_Z_AXIS_DIRECTION): [0.0, 0.0, 1.0],
            (child, ATTR_MMD_EXTERNAL_PARENT_KEY): 42,
        }

        def attribute_query(attr, node, exists):
            return exists and (node, attr) in attrs

        def get_attr(path):
            node, attr = path.rsplit(".", 1)
            return attrs[(node, attr)]

        with (
            mock.patch.object(
                export_scene_collector_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "xform",
                return_value=[100.0, 200.0, 300.0],
            ),
        ):
            bones, _ = export_scene_collector_module._collect_bones_from_joints([child, root])

        exported = bones[1]
        self.assertEqual(exported["bone_flag"], authored_flags)
        self.assertEqual(exported["position"], [7.0, 8.0, 9.0])
        self.assertEqual(exported["transform_layer"], 7)
        self.assertEqual(exported["connect_bone_index"], 0)
        self.assertNotIn("connect_position_offset", exported)
        self.assertEqual(exported["grant_parent_bone_index"], 0)
        self.assertEqual(exported["grant_rate"], 0.25)
        self.assertEqual(exported["axis_direction"], [0.0, 1.0, 0.0])
        self.assertEqual(exported["x_axis_direction"], [1.0, 0.0, 0.0])
        self.assertEqual(exported["z_axis_direction"], [0.0, 0.0, 1.0])
        self.assertEqual(exported["key_value"], 42)
        self.assertNotIn("semantic_missing", exported)

    def test_collect_bones_keeps_malformed_rest_and_unresolved_refs_visible(self):
        """Malformed canonical data is retained for fail-closed validation."""
        root = "|model|root"
        child = "|model|child"
        attrs = {
            (root, ATTR_MMD_BONE_INDEX): 0,
            (root, ATTR_MMD_BONE_PARENT_INDEX): -1,
            (root, ATTR_MMD_BONE_NAME): "Root",
            (root, ATTR_MMD_BONE_NAME_EN): "Root",
            (child, ATTR_MMD_BONE_INDEX): 1,
            (child, ATTR_MMD_BONE_PARENT_INDEX): 0,
            (child, ATTR_MMD_BONE_NAME): "Child",
            (child, ATTR_MMD_BONE_NAME_EN): "Child",
            (child, ATTR_MMD_BONE_FLAGS): 0x0001 | 0x0100,
            (child, ATTR_MMD_PMX_REST_POSITION): ["malformed"],
            (child, ATTR_MMD_CONNECT_INDEX): 99,
            (child, ATTR_MMD_CONNECTION_BONE): "missing",
            (child, ATTR_MMD_GRANT_PARENT_INDEX): 0,
            (child, ATTR_MMD_GRANT_PARENT): "missing",
            (child, ATTR_MMD_GRANT_RATE): 0.5,
        }

        def attribute_query(attr, node, exists):
            return exists and (node, attr) in attrs

        def get_attr(path):
            node, attr = path.rsplit(".", 1)
            return attrs[(node, attr)]

        with (
            mock.patch.object(
                export_scene_collector_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "xform",
                return_value=[0.0, 0.0, 0.0],
            ),
        ):
            bones, _ = export_scene_collector_module._collect_bones_from_joints([root, child])

        exported = bones[1]
        self.assertEqual(exported["position"], ["malformed"])
        self.assertIsNone(exported["connect_bone_index"])
        self.assertIsNone(exported["grant_parent_bone_index"])
        self.assertEqual(
            exported["semantic_missing"],
            ["connect_bone_index", "grant_parent_bone_index"],
        )

    def test_mmd_shader_semantics_override_defaults_and_keep_provenance(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_INDEX: 3,
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
        self.assertEqual(material["source_material_index"], 3)
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

    def test_tagged_material_groups_keep_in_order_source_material_indices(self):
        materials, faces = self._collect_two_material_groups(
            [0, 1],
            {
                "shader_a": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material A",
                    ATTR_MMD_MATERIAL_INDEX: 0,
                },
                "shader_b": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material B",
                    ATTR_MMD_MATERIAL_INDEX: 1,
                },
            },
        )

        self.assertEqual([material["source_material_index"] for material in materials], [0, 1])
        self.assertEqual([material["name"] for material in materials], ["Material A", "Material B"])
        self.assertEqual(faces, [[2, 1, 0], [5, 4, 3]])

    def test_tagged_material_groups_reorder_reversed_faces_with_source_material_indices(self):
        materials, faces = self._collect_two_material_groups(
            [1, 0],
            {
                "shader_a": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material A",
                    ATTR_MMD_MATERIAL_INDEX: 0,
                },
                "shader_b": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material B",
                    ATTR_MMD_MATERIAL_INDEX: 1,
                },
            },
        )

        self.assertEqual([material["source_material_index"] for material in materials], [0, 1])
        self.assertEqual([material["name"] for material in materials], ["Material A", "Material B"])
        self.assertEqual(faces, [[5, 4, 3], [2, 1, 0]])

    def test_tagged_material_groups_do_not_reorder_missing_malformed_or_duplicate_index(self):
        materials, faces = self._collect_two_material_groups(
            [1, 0],
            {
                "shader_a": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material A",
                },
                "shader_b": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material B",
                    ATTR_MMD_MATERIAL_INDEX: 1,
                },
            },
        )

        self.assertEqual([material["name"] for material in materials], ["Material B", "Material A"])
        self.assertEqual(faces, [[2, 1, 0], [5, 4, 3]])
        self.assertIn("source_material_index", materials[1]["semantic_missing"])

        materials, faces = self._collect_two_material_groups(
            [1, 0],
            {
                "shader_a": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material A",
                    ATTR_MMD_MATERIAL_INDEX: 0,
                },
                "shader_b": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material B",
                    ATTR_MMD_MATERIAL_INDEX: 0,
                },
            },
        )

        self.assertEqual([material["name"] for material in materials], ["Material B", "Material A"])
        self.assertEqual(faces, [[2, 1, 0], [5, 4, 3]])
        self.assertEqual(
            [material["source_material_index"] for material in materials],
            [0, 0],
        )
        self.assertTrue(
            all(
                "source_material_index" in (material.get("semantic_missing") or [])
                for material in materials
            )
        )

        materials, faces = self._collect_two_material_groups(
            [1, 0],
            {
                "shader_a": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material A",
                    ATTR_MMD_MATERIAL_INDEX: 0.5,
                },
                "shader_b": {
                    ATTR_MMD_MATERIAL: 1,
                    ATTR_MMD_MATERIAL_NAME: "Material B",
                    ATTR_MMD_MATERIAL_INDEX: 1,
                },
            },
        )

        self.assertEqual([material["name"] for material in materials], ["Material B", "Material A"])
        self.assertEqual(faces, [[2, 1, 0], [5, 4, 3]])
        self.assertIn("source_material_index", materials[1]["semantic_missing"])

    def test_mmd_shader_non_shared_toon_index_is_not_writer_facing_without_table(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_SHARED_TOON_FLAG: 0,
                ATTR_MMD_TOON_TEXTURE_INDEX: 2,
                ATTR_MMD_TOON_PATH: "textures/custom_toon.png",
            }
        )

        self.assertNotIn("toon_texture_index", material)
        self.assertEqual(material["source_toon_texture_index"], 2)
        self.assertEqual(material["toon_texture_path"], "textures/custom_toon.png")
        self.assertIn("texture_table", material["semantic_missing"])

    def test_mmd_shader_shared_toon_does_not_collect_custom_toon_path(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_SHARED_TOON_FLAG: 1,
                ATTR_MMD_TOON_TEXTURE_INDEX: 2,
                ATTR_MMD_TOON_PATH: "textures/should_not_be_used.png",
            }
        )

        self.assertEqual(material["toon_texture_index"], 2)
        self.assertNotIn("toon_texture_path", material)

    def test_missing_root_texture_table_reconstructs_non_shared_toon_provenance(self):
        model_data = {
            "materials": [
                {
                    "texture_path": "textures/body.png",
                    "source_texture_index": 0,
                    "toon_texture_path": "textures/custom_toon.png",
                    "source_toon_texture_index": 1,
                    "shared_toon_flag": 0,
                    "semantic_missing": ["texture_table"],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value=None,
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertEqual(
            model_data["textures"],
            ["textures/body.png", "textures/custom_toon.png"],
        )
        self.assertEqual(material["texture_index"], 0)
        self.assertEqual(material["toon_texture_index"], 1)
        self.assertNotIn("source_texture_index", material)
        self.assertNotIn("source_toon_texture_index", material)
        self.assertEqual(material["semantic_missing"], [])

    def test_mmd_shader_texture_path_without_index_is_fail_closed(self):
        material = self._collect_single_material(
            {
                ATTR_MMD_MATERIAL: 1,
                "mmd_texture_path": "textures/body.png",
            }
        )

        self.assertEqual(material["texture_path"], "textures/body.png")
        self.assertIn("texture_table", material["semantic_missing"])

    def test_model_texture_table_restores_writer_indices_without_reconstructing_paths(self):
        model_data = {
            "materials": [
                {
                    "texture_path": "textures/body.png",
                    "source_texture_index": 1,
                    "sphere_texture_path": "textures/body.spa",
                    "source_sphere_texture_index": 2,
                    "semantic_missing": ["texture_table"],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value='["unused.png", "textures/body.png", "textures/body.spa"]',
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertEqual(model_data["textures"], ["unused.png", "textures/body.png", "textures/body.spa"])
        self.assertEqual(material["texture_index"], 1)
        self.assertEqual(material["sphere_texture_index"], 2)
        self.assertNotIn("source_texture_index", material)
        self.assertNotIn("source_sphere_texture_index", material)
        self.assertEqual(material["semantic_missing"], [])

    def test_single_mesh_collection_attaches_provenance_texture_table(self):
        """Direct PMX mesh collection resolves its complete local table."""
        class FakePoint:
            x = 0.0
            y = 0.0
            z = 0.0

        class FakeMesh:
            numVertices = 3
            numPolygons = 1

            def getPoints(self, _space):
                return [FakePoint(), FakePoint(), FakePoint()]

            def getVertexNormals(self, _angle_weighted, _space):
                return [FakePoint(), FakePoint(), FakePoint()]

            def numUVs(self):
                return 0

        selection = mock.Mock()
        selection.getDagPath.return_value = object()
        material = {
            "name": "material",
            "face_count": 3,
            "texture_path": "textures/body.png",
            "source_texture_index": 0,
            "sphere_texture_path": "textures/body.spa",
            "source_sphere_texture_index": 1,
            "semantic_missing": ["texture_table"],
        }

        with (
            mock.patch.object(export_scene_collector_module, "_get_mesh_shape", return_value="meshShape"),
            mock.patch.object(
                export_scene_collector_module.cmds,
                "listRelatives",
                return_value=["|model|mesh"],
            ),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module.om, "MSelectionList", return_value=selection),
            mock.patch.object(export_scene_collector_module.om, "MFnMesh", return_value=FakeMesh()),
            mock.patch.object(export_scene_collector_module, "_find_skin_cluster", return_value=None),
            mock.patch.object(
                export_scene_collector_module,
                "_collect_materials_per_face",
                return_value=([material], [[2, 1, 0]]),
            ),
            mock.patch.object(export_scene_collector_module, "_collect_vertex_morphs", return_value=[]),
        ):
            model_data = ExportSceneCollector().collect_from_mesh("mesh")

        self.assertEqual(model_data["textures"], ["textures/body.png", "textures/body.spa"])
        material = model_data["materials"][0]
        self.assertEqual(material["texture_index"], 0)
        self.assertEqual(material["sphere_texture_index"], 1)
        self.assertNotIn("source_texture_index", material)
        self.assertNotIn("source_sphere_texture_index", material)
        self.assertEqual(material["semantic_missing"], [])

    def test_invalid_texture_table_keeps_source_index_fail_closed(self):
        model_data = {
            "materials": [
                {
                    "source_texture_index": 4,
                    "semantic_missing": ["texture_table"],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value='["only.png"]',
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertEqual(model_data["textures"], ["only.png"])
        self.assertEqual(material["source_texture_index"], 4)
        self.assertNotIn("texture_index", material)
        self.assertEqual(material["semantic_missing"], ["texture_table"])

    def test_missing_root_texture_table_uses_relative_material_provenance(self):
        model_data = {
            "materials": [
                {
                    "texture_path": "textures/body.png",
                    "sphere_texture_path": "textures/body.spa",
                    "semantic_missing": [
                        "texture_index",
                        "sphere_texture_index",
                        "texture_table",
                    ],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value=None,
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertEqual(model_data["textures"], ["textures/body.png", "textures/body.spa"])
        self.assertEqual(material["texture_index"], 0)
        self.assertEqual(material["sphere_texture_index"], 1)
        self.assertEqual(material["semantic_missing"], [])

    def test_missing_root_texture_table_does_not_fill_sparse_authored_index(self):
        model_data = {
            "materials": [
                {
                    "texture_path": "textures/body.png",
                    "source_texture_index": 2,
                    "semantic_missing": ["texture_table"],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value=None,
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertNotIn("textures", model_data)
        self.assertNotIn("texture_index", material)
        self.assertEqual(material["source_texture_index"], 2)
        self.assertEqual(material["semantic_missing"], ["texture_table"])

    def test_missing_root_texture_table_rejects_explicit_no_texture_sentinel(self):
        model_data = {
            "materials": [
                {
                    "texture_path": "textures/stale.png",
                    "texture_index": -1,
                    "semantic_missing": ["texture_table"],
                }
            ]
        }

        with mock.patch.object(
            export_scene_collector_module,
            "_get_attr",
            return_value=None,
        ):
            export_scene_collector_module._apply_texture_table(model_data, "|model_ROOT")

        material = model_data["materials"][0]
        self.assertNotIn("textures", model_data)
        self.assertEqual(material["texture_index"], -1)
        self.assertEqual(material["semantic_missing"], ["texture_table"])

    def test_model_root_collector_attaches_provenance_texture_table(self):
        mesh_data = {
            "vertices": [],
            "faces": [],
            "materials": [
                {
                    "name": "material",
                    "face_count": 0,
                    "texture_path": "textures/body.png",
                    "semantic_missing": ["texture_table"],
                }
            ],
            "bones": [],
            "morphs": [],
        }

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None, require_contiguous=True):
                return []

        with (
            mock.patch.object(export_scene_collector_module, "MorphConverter", FakeMorphConverter),
            mock.patch.object(export_scene_collector_module, "_list_export_mesh_shapes", return_value=["mesh"]),
            mock.patch.object(export_scene_collector_module, "_collect_model_bones", return_value=[]),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module, "_collect_display_frames", return_value=[]),
            mock.patch.object(ExportSceneCollector, "collect_from_mesh", return_value=mesh_data),
            mock.patch.object(export_scene_collector_module, "_get_attr", return_value=None),
            mock.patch(
                "mmd_tools.converters.physics_export_collector.collect_physics_from_scene",
                return_value=([], []),
            ),
        ):
            payload = ExportSceneCollector().collect(
                {"target_model": "|hero:model_ROOT", "export_format": "pmx"}
            )

        self.assertEqual(payload["textures"], ["textures/body.png"])
        self.assertEqual(payload["materials"][0]["texture_index"], 0)
        self.assertEqual(payload["materials"][0]["semantic_missing"], [])

    def test_model_root_orders_materials_and_adjusted_faces_by_source_index(self):
        """Root merge keeps material groups paired while restoring source order."""
        payload = self._collect_mock_model_root(
            {
                "mesh_a": self._mock_root_mesh(
                    {
                        "name": "Material One",
                        "source_material_index": 1,
                        "face_count": 3,
                        "semantic_missing": [],
                    }
                ),
                "mesh_b": self._mock_root_mesh(
                    {
                        "name": "Material Zero",
                        "source_material_index": 0,
                        "face_count": 3,
                        "semantic_missing": [],
                    }
                ),
            }
        )

        self.assertEqual(
            [material["name"] for material in payload["materials"]],
            ["Material Zero", "Material One"],
        )
        self.assertEqual(
            [material["source_material_index"] for material in payload["materials"]],
            [0, 1],
        )
        self.assertEqual(payload["faces"], [[3, 4, 5], [0, 1, 2]])

    def test_model_root_source_index_order_is_fail_closed_for_duplicate_or_malformed(self):
        """Root merge retains DAG order when source provenance is ambiguous."""
        duplicate_payload = self._collect_mock_model_root(
            {
                "mesh_a": self._mock_root_mesh(
                    {
                        "name": "Material A",
                        "source_material_index": 0,
                        "face_count": 3,
                        "semantic_missing": [],
                    }
                ),
                "mesh_b": self._mock_root_mesh(
                    {
                        "name": "Material B",
                        "source_material_index": 0,
                        "face_count": 3,
                        "semantic_missing": [],
                    }
                ),
            }
        )

        self.assertEqual(
            [material["name"] for material in duplicate_payload["materials"]],
            ["Material A", "Material B"],
        )
        self.assertEqual(duplicate_payload["faces"], [[0, 1, 2], [3, 4, 5]])
        self.assertTrue(
            all(
                "source_material_index" in material["semantic_missing"]
                for material in duplicate_payload["materials"]
            )
        )

        malformed_payload = self._collect_mock_model_root(
            {
                "mesh_a": self._mock_root_mesh(
                    {
                        "name": "Material Malformed",
                        "source_material_index": "1",
                        "face_count": 3,
                        "semantic_missing": ["source_material_index"],
                    }
                ),
                "mesh_b": self._mock_root_mesh(
                    {
                        "name": "Material Zero",
                        "source_material_index": 0,
                        "face_count": 3,
                        "semantic_missing": [],
                    }
                ),
            }
        )

        self.assertEqual(
            [material["name"] for material in malformed_payload["materials"]],
            ["Material Malformed", "Material Zero"],
        )
        self.assertEqual(malformed_payload["faces"], [[0, 1, 2], [3, 4, 5]])
        self.assertEqual(
            malformed_payload["materials"][0]["source_material_index"],
            "1",
        )
        self.assertIn(
            "source_material_index",
            malformed_payload["materials"][0]["semantic_missing"],
        )

    def test_model_root_merges_materials_before_resolving_one_global_texture_table(self):
        """Root collection must not resolve each mesh against a local table."""
        mesh_data_by_shape = {
            "mesh_a": {
                "vertices": [],
                "faces": [],
                "materials": [
                    {
                        "name": "material_a",
                        "face_count": 0,
                        "texture_path": "textures/a.png",
                        "semantic_missing": ["texture_table"],
                    }
                ],
                "bones": [],
                "morphs": [],
            },
            "mesh_b": {
                "vertices": [],
                "faces": [],
                "materials": [
                    {
                        "name": "material_b",
                        "face_count": 0,
                        "texture_path": "textures/b.png",
                        "semantic_missing": ["texture_table"],
                    }
                ],
                "bones": [],
                "morphs": [],
            },
        }

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None, require_contiguous=True):
                return []

        with (
            mock.patch.object(export_scene_collector_module, "MorphConverter", FakeMorphConverter),
            mock.patch.object(
                export_scene_collector_module,
                "_list_export_mesh_shapes",
                return_value=["mesh_a", "mesh_b"],
            ),
            mock.patch.object(export_scene_collector_module, "_collect_model_bones", return_value=[]),
            mock.patch.object(export_scene_collector_module, "_get_model_name", return_value="Hero"),
            mock.patch.object(export_scene_collector_module, "_collect_display_frames", return_value=[]),
            mock.patch.object(
                ExportSceneCollector,
                "collect_from_mesh",
                side_effect=lambda shape, **_kwargs: mesh_data_by_shape[shape],
            ) as collect_from_mesh,
            mock.patch.object(export_scene_collector_module, "_get_attr", return_value=None),
            mock.patch(
                "mmd_tools.converters.physics_export_collector.collect_physics_from_scene",
                return_value=([], []),
            ),
        ):
            payload = ExportSceneCollector().collect_from_model_root("|hero:model_ROOT")

        self.assertEqual(payload["textures"], ["textures/a.png", "textures/b.png"])
        self.assertEqual(
            [material["texture_index"] for material in payload["materials"]],
            [0, 1],
        )
        self.assertEqual(
            collect_from_mesh.call_args_list,
            [
                mock.call("mesh_a", _resolve_texture_table=False),
                mock.call("mesh_b", _resolve_texture_table=False),
            ],
        )

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
            )

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
        calls = []

        class FakeMorphConverter:
            def collect_morphs_from_scene_for_export(self, *, root_group=None, require_contiguous=True):
                calls.append((root_group, require_contiguous))
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
                {"target_model": "|hero:model_ROOT", "export_format": "pmx"}
            )

        self.assertEqual(calls, [("|hero:model_ROOT", False)])
        self.assertEqual(payload["model_name"], "Hero")
        collect_from_mesh.assert_called_once_with(
            "mesh",
            _resolve_texture_table=False,
        )

    def test_network_morph_collection_passes_selected_root(self):
        converter = object.__new__(MorphConverter)
        converter.logger = mock.Mock()
        group_metadata = mock.Mock(
            node="group_morph",
            morph_type="group",
            name_english="group_smile",
            panel=4,
            index=0,
        )
        group_metadata.name = "グループ笑い"

        def attribute_query(attribute, node, exists):
            return exists and node == "group_morph" and attribute == "mmd_group_morph_offsets_json"

        with mock.patch.object(
            morph_converter_module,
            "iter_morph_network_metadata",
            return_value=[group_metadata],
        ) as iterator, mock.patch.object(
            morph_converter_module.cmds,
            "attributeQuery",
            side_effect=attribute_query,
        ), mock.patch.object(
            morph_converter_module.cmds,
            "getAttr",
            return_value='[{"morph_index": 0, "morph_rate": 0.5}]',
        ):
            result = converter.collect_morphs_from_scene_for_export(
                root_group="|hero:model_ROOT",
            )

        self.assertEqual(
            result,
            [
                {
                    "type": "group",
                    "name": "グループ笑い",
                    "name_english": "group_smile",
                    "panel": 4,
                    "offsets": [{"morph_index": 0, "morph_rate": 0.5}],
                    "index": 0,
                }
            ],
        )
        iterator.assert_called_once_with(
            root_group="|hero:model_ROOT",
            morph_types={
                "bone",
                "group",
                "material",
                "uv",
                "additional_uv1",
                "additional_uv2",
                "additional_uv3",
                "additional_uv4",
                "flip",
                "impulse",
            },
        )

    def test_group_network_morph_collection_restores_index_order_and_fails_closed(self):
        converter = object.__new__(MorphConverter)
        converter.logger = mock.Mock()
        metadata = [
            SimpleNamespace(
                node="material_morph",
                morph_type="material",
                name="material",
                name_english="material",
                panel=5,
                index=2,
            ),
            SimpleNamespace(
                node="group_morph",
                morph_type="group",
                name="group",
                name_english="group",
                panel=4,
                index=0,
            ),
            SimpleNamespace(
                node="bone_morph",
                morph_type="bone",
                name="bone",
                name_english="bone",
                panel=4,
                index=1,
            ),
        ]
        offsets_attrs = {
            "group_morph": "mmd_group_morph_offsets_json",
            "bone_morph": "mmd_bone_morph_offsets_json",
            "material_morph": "mmd_material_morph_offsets_json",
        }

        def collect(network_metadata):
            def attribute_query(attribute, node, exists):
                return exists and attribute == offsets_attrs[node]

            def get_attr(path):
                if path.startswith("group_morph."):
                    return '[{"morph_index": 1, "morph_rate": 0.5}]'
                return "[]"

            with mock.patch.object(
                morph_converter_module,
                "iter_morph_network_metadata",
                return_value=network_metadata,
            ), mock.patch.object(
                morph_converter_module.cmds,
                "attributeQuery",
                side_effect=attribute_query,
            ), mock.patch.object(
                morph_converter_module.cmds,
                "getAttr",
                side_effect=get_attr,
            ):
                return converter.collect_morphs_from_scene_for_export(root_group="|hero:model_ROOT")

        result = collect(metadata)
        self.assertEqual([morph["name"] for morph in result], ["group", "bone", "material"])
        self.assertEqual([morph["index"] for morph in result], [0, 1, 2])
        self.assertEqual(result[0]["offsets"], [{"morph_index": 1, "morph_rate": 0.5}])

        missing_index = list(metadata)
        missing_index[0] = SimpleNamespace(**{**vars(metadata[0]), "index": None})
        with self.assertRaisesRegex(ValueError, "missing index"):
            collect(missing_index)

        gap_index = list(metadata)
        gap_index[0] = SimpleNamespace(**{**vars(metadata[0]), "index": 3})
        with self.assertRaisesRegex(ValueError, "expected indices"):
            collect(gap_index)


if __name__ == "__main__":
    unittest.main()
