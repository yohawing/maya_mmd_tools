"""Integration tests for PMX export via ExportSceneCollector + PmxExporter.

These tests run under Maya 2024 mayapy and verify the full
collect → export → parse round-trip for a minimum geometry.
"""

import json
import os
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.converters import export_scene_collector
from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.converters.morph_converter import MorphConverter
from mmd_tools.converters.material_shader_parameters import (
    ATTR_MMD_DIFFUSE_ALPHA,
    ATTR_MMD_EDGE_ALPHA,
)
from mmd_tools.core.constants import (
    ATTR_MMD_ADDITIONAL_UVS_JSON,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECTION_BONE,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_AMBIENT_COLOR,
    ATTR_MMD_DIFFUSE_COLOR,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
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
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_SHININESS,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_SPECULAR_COLOR,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_TOON_TEXTURE_INDEX,
    ATTR_MMD_PMX_REST_POSITION,
    ATTR_MMD_PMX_SOFT_BODY_COUNT,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
)
from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.model_registry import REGISTRY_CATEGORY_MORPH, list_model_registry_members
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.pmx_data.soft_body import PmxSoftBody
from mmd_tools.core.logger import get_logger
from mmd_tools.io.model_import_pipeline import ModelImportPipeline
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.pmx_mock import PmxMock
from tests.common.maya_test_base import MayaTestBase


def _parse_pmx(path):
    """Read exporter output with the legacy PMX reader for writer roundtrip checks."""
    return parse_pmx_file(
        path,
        use_native_pmx_parse=False,
        require_native_pmx_parse=False,
    )


class TestPmxExporter(MayaTestBase):
    """Round-trip tests: Maya scene → collect → export PMX → parse back."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)

    def tearDown(self):
        super().tearDown()
        cmds.file(new=True, force=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _make_triangle(self, name: str = "test_mesh"):
        """Create a single-triangle polygon mesh and return (transform, shape)."""
        result = cmds.polyCreateFacet(
            p=[(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            name=name,
        )
        transform = result[0]
        shapes = cmds.listRelatives(transform, shapes=True, type="mesh") or []
        shape = shapes[0]
        return transform, shape

    def _assign_shader(self, transform: str, shader_name: str = "TestMaterial") -> str:
        """Create a standardSurface shader and assign it to *transform*.

        Returns the shader node name (which Maya may have uniquified).
        """
        shader = cmds.shadingNode("standardSurface", asShader=True, name=shader_name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True,
            name=shader_name + "SG",
        )
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        cmds.sets(transform, edit=True, forceElement=sg)
        return shader

    def _set_tagged_shader_with_textures(
        self,
        shader: str,
        material_index: int,
        material_name: str = "Tagged material",
    ) -> None:
        """Persist complete tagged PMX material semantics on an existing shader."""
        maya_attribute_utils.set_custom_attributes(
            shader,
            {
                ATTR_MMD_MATERIAL: 1,
                ATTR_MMD_MATERIAL_INDEX: material_index,
                ATTR_MMD_MATERIAL_NAME: material_name,
                ATTR_MMD_MATERIAL_NAME_EN: material_name,
                ATTR_MMD_DIFFUSE_COLOR: [0.8, 0.7, 0.6],
                ATTR_MMD_DIFFUSE_ALPHA: 1.0,
                ATTR_MMD_SPECULAR_COLOR: [0.2, 0.3, 0.4],
                ATTR_MMD_SHININESS: 5.0,
                ATTR_MMD_AMBIENT_COLOR: [0.1, 0.1, 0.1],
                ATTR_MMD_DRAW_FLAGS: 0x13,
                ATTR_MMD_EDGE_COLOR: [0.0, 0.0, 0.0],
                ATTR_MMD_EDGE_ALPHA: 1.0,
                ATTR_MMD_EDGE_SIZE: 1.0,
                ATTR_MMD_TEXTURE_INDEX: 0,
                ATTR_MMD_SPHERE_TEXTURE_INDEX: 1,
                ATTR_MMD_SPHERE_MODE: 0,
                ATTR_MMD_SHARED_TOON_FLAG: 1,
                ATTR_MMD_TOON_TEXTURE_INDEX: 0,
                ATTR_MMD_MEMO: "",
                "mmd_texture_path": "textures/body.png",
                ATTR_MMD_SPHERE_PATH: "textures/body.spa",
            },
        )

    def _assign_tagged_shader_with_textures(
        self,
        transform: str,
        material_index: int = 0,
        shader_name: str = "TaggedTextureMaterial",
        material_name: str = "Tagged material",
    ) -> str:
        """Assign a complete tagged PMX material with relative texture provenance."""
        shader = self._assign_shader(transform, shader_name=shader_name)
        self._set_tagged_shader_with_textures(shader, material_index, material_name)
        return shader

    def _assign_shader_to_component(self, component: str, shader_name: str) -> str:
        """Create a standardSurface shader and assign it to a face component.

        Args:
            component: Maya component path, e.g. ``"pPlane1.f[0]"``.
            shader_name: Desired shader node name.

        Returns:
            Actual shader node name (Maya may uniquify it).
        """
        shader = cmds.shadingNode("standardSurface", asShader=True, name=shader_name)
        sg = cmds.sets(
            renderable=True, noSurfaceShader=True, empty=True,
            name=shader_name + "SG",
        )
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        cmds.sets(component, edit=True, forceElement=sg)
        return shader

    def _assign_tagged_shader_to_component_with_textures(
        self,
        component: str,
        material_index: int,
        shader_name: str,
        material_name: str,
    ) -> str:
        """Assign a complete tagged PMX material to one polygon component."""
        shader = self._assign_shader_to_component(component, shader_name)
        self._set_tagged_shader_with_textures(shader, material_index, material_name)
        return shader

    def _make_two_mesh_model_root(self, root_name: str = "export_model_root"):
        """Create a root with two child triangle meshes and return (root, meshes)."""
        root = cmds.group(empty=True, name=root_name)
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "MergedExport", type="string")
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME_EN, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}", "MergedExport", type="string")
        cmds.addAttr(root, longName=ATTR_MMD_COMMENT, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_COMMENT}", "", type="string")
        cmds.addAttr(root, longName=ATTR_MMD_COMMENT_EN, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_COMMENT_EN}", "", type="string")

        bone = cmds.createNode("joint", name=f"{root_name}_bone", parent=root)
        for attr, attr_type, value in (
            (ATTR_MMD_BONE_INDEX, "long", 0),
            (ATTR_MMD_BONE_PARENT_INDEX, "long", -1),
        ):
            cmds.addAttr(bone, longName=attr, attributeType=attr_type)
            cmds.setAttr(f"{bone}.{attr}", value)
        for attr, value in (
            (ATTR_MMD_BONE_NAME, "センター"),
            (ATTR_MMD_BONE_NAME_EN, "Center"),
        ):
            cmds.addAttr(bone, longName=attr, dataType="string")
            cmds.setAttr(f"{bone}.{attr}", value, type="string")
        maya_attribute_utils.set_custom_attributes(
            bone,
            {
                ATTR_MMD_BONE_FLAGS: 0,
                ATTR_MMD_BONE_OFFSET: [0.0, -1.0, 0.0],
                ATTR_MMD_PMX_REST_POSITION: [0.0, 0.0, 0.0],
                ATTR_MMD_DEFORM_LAYER: 0,
            },
        )

        mesh_a, _ = self._make_triangle(name=f"{root_name}_mesh_a")
        mesh_b, _ = self._make_triangle(name=f"{root_name}_mesh_b")
        cmds.move(2.0, 0.0, 0.0, mesh_b, absolute=True)
        cmds.parent(mesh_a, root)
        cmds.parent(mesh_b, root)
        shader_a = self._assign_shader(mesh_a, shader_name=f"{root_name}_MatA")
        shader_b = self._assign_shader(mesh_b, shader_name=f"{root_name}_MatB")
        self._set_tagged_shader_with_textures(shader_a, 0, shader_a)
        self._set_tagged_shader_with_textures(shader_b, 1, shader_b)
        return root, (mesh_a, mesh_b), (shader_a, shader_b)

    def _make_skinned_triangle(self, name: str = "skinned_tri"):
        """Create a skinned triangle with two MMD-tagged influence joints."""
        transform, _shape = self._make_triangle(name=name)
        self._assign_shader(transform, shader_name=f"{name}_Mat")

        cmds.select(clear=True)
        root_joint = cmds.joint(name=f"{name}_root_jnt", position=[0.0, 0.0, 0.0])
        child_joint = cmds.joint(name=f"{name}_child_jnt", position=[0.0, 2.0, 0.0])
        for joint, bone_index, parent_index, bone_name, bone_name_en in [
            (root_joint, 0, -1, "センター", "Center"),
            (child_joint, 1, 0, "上半身", "UpperBody"),
        ]:
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_INDEX, attributeType="long")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}", bone_index)
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_PARENT_INDEX, attributeType="long")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_PARENT_INDEX}", parent_index)
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME, dataType="string")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME}", bone_name, type="string")
            cmds.addAttr(joint, longName=ATTR_MMD_BONE_NAME_EN, dataType="string")
            cmds.setAttr(f"{joint}.{ATTR_MMD_BONE_NAME_EN}", bone_name_en, type="string")

        skin_cluster = cmds.skinCluster(
            [root_joint, child_joint],
            transform,
            toSelectedBones=True,
            maximumInfluences=2,
            normalizeWeights=1,
            name=f"{name}_skinCluster",
        )[0]
        cmds.skinPercent(
            skin_cluster,
            f"{transform}.vtx[0]",
            transformValue=[(root_joint, 1.0), (child_joint, 0.0)],
        )
        cmds.skinPercent(
            skin_cluster,
            f"{transform}.vtx[1]",
            transformValue=[(root_joint, 0.25), (child_joint, 0.75)],
        )
        cmds.skinPercent(
            skin_cluster,
            f"{transform}.vtx[2]",
            transformValue=[(root_joint, 0.0), (child_joint, 1.0)],
        )
        return transform, (root_joint, child_joint), skin_cluster

    def _create_scene_morph_metadata(self, mesh_name: str, root_group: Optional[str] = None):
        """Create morph metadata and optionally register network leaves to a model root."""

        class FakeVertexMorph:
            name = "頂点にこり"
            name_english = "vertex_smile"
            panel = 3
            morph_type = PmxMorphType.VertexMorph
            offsets = [
                {
                    "vertex_index": 1,
                    "position_offset": (0.25, 0.0, 0.0),
                }
            ]

            def get_name(self):
                return self.name

        class FakeGroupMorph:
            name = "グループ笑い"
            name_english = "group_smile"
            panel = 4
            morph_type = PmxMorphType.GroupMorph
            offsets = [
                {
                    "morph_index": 1,
                    "morph_rate": 0.5,
                }
            ]

            def get_name(self):
                return self.name

        class FakeBoneMorph:
            name = "ボーン笑い"
            name_english = "bone_smile"
            panel = 4
            morph_type = PmxMorphType.BoneMorph
            offsets = [
                {
                    "bone_index": 0,
                    "translation": (1.0, 2.0, 3.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            ]

            def get_name(self):
                return self.name

        class FakeMaterialMorph:
            name = "材質点滅"
            name_english = "material_flash"
            panel = 4
            morph_type = PmxMorphType.MaterialMorph
            offsets = [
                {
                    "material_index": 0,
                    "operation_type": 0,
                    "diffuse": (0.1, 0.2, 0.3, 0.4),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [],
                "materials": [],
                "morphs": [
                    FakeGroupMorph(),
                    FakeVertexMorph(),
                    FakeBoneMorph(),
                    FakeMaterialMorph(),
                ],
            },
        )()
        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)
        self.assertTrue(result.get("success", False))
        self.assertEqual(len(result["group_morph_nodes"]), 1)
        if root_group is not None:
            morph_converter.build_morph_controller(fake_data, root_group, result)
            # Match PMX import ownership: all semantic morph metadata leaves
            # are registered to the model root; vertex offsets remain on the
            # blendShape as their runtime authority.
            pipeline = ModelImportPipeline(
                logger=get_logger(__name__),
                filepath="<test fixture>",
                scale=1.0,
                options={},
            )
            model_registry = pipeline.create_model_registry(root_group)
            pipeline.connect_morph_nodes_to_root(
                root_group,
                result,
                model_registry=model_registry,
            )
            registered_morph_nodes = list_model_registry_members(root_group, REGISTRY_CATEGORY_MORPH)
            self.assertIsNotNone(registered_morph_nodes)
            self.assertEqual(
                set(registered_morph_nodes),
                set(
                    result["group_morph_nodes"]
                    + result["bone_morph_nodes"]
                    + result["material_morph_nodes"]
                    + result["vertex_morph_nodes"]
                ),
            )
        return result

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def test_collect_single_triangle_vertex_face_counts(self):
        """Collector returns 3 vertices and 1 face for a triangle mesh."""
        transform, _ = self._make_triangle()
        self._assign_shader(transform)

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        self.assertEqual(len(maya_data["vertices"]), 3)
        self.assertEqual(len(maya_data["faces"]), 1)

    def test_collect_material_face_count(self):
        """face_count in collected material equals total triangle index count (3)."""
        transform, _ = self._make_triangle()
        self._assign_shader(transform)

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        self.assertEqual(len(maya_data["materials"]), 1)
        # 1 triangle × 3 indices per triangle = 3
        self.assertEqual(maya_data["materials"][0]["face_count"], 3)

    def test_collect_material_name_from_shader(self):
        """Material name is taken from the assigned shader node name."""
        transform, _ = self._make_triangle()
        shader = self._assign_shader(transform, shader_name="MyShader")

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        self.assertEqual(maya_data["materials"][0]["name"], shader)

    def test_roundtrip_converts_geometry_to_mmd_basis(self):
        """Maya-space geometry is exported with MMD Z basis and reversed winding."""
        result = cmds.polyCreateFacet(
            p=[(0, 0, 2), (1, 0, 2), (0, 1, 2)],
            name="basis_tri_mesh",
        )
        transform = result[0]
        self._assign_shader(transform, shader_name="BasisTriMat")

        maya_data = ExportSceneCollector().collect_from_mesh(transform)
        self.assertEqual(maya_data["faces"], [[2, 1, 0]])

        output_path = self.get_temp_filename("basis_triangle.pmx")
        PmxExporter().export_pmx_model(output_path, maya_data)

        pmx = _parse_pmx(output_path)

        self.assertEqual(pmx.faces[0].indices, (2, 1, 0))
        self.assertAlmostEqual(pmx.vertices[0].position[2], -2.0)
        self.assertAlmostEqual(pmx.vertices[0].normal[2], -1.0)

    def test_roundtrip_single_triangle(self):
        """Full round-trip: collect → export PMX → parse → assert structure."""
        transform, _ = self._make_triangle(name="tri_mesh")
        shader = self._assign_shader(transform, shader_name="TriMat")

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        output_path = self.get_temp_filename("test_triangle.pmx")
        exporter = PmxExporter()
        exporter.export_pmx_model(output_path, maya_data)

        self.assertTrue(os.path.exists(output_path), "PMX file was not written")

        pmx = _parse_pmx(output_path)

        # Vertex / face counts
        self.assertEqual(len(pmx.vertices), 3)
        self.assertEqual(len(pmx.faces), 1)  # 1 PmxFace (triangle)

        # Material
        self.assertEqual(len(pmx.materials), 1)
        self.assertEqual(pmx.materials[0].face_count, 3)
        self.assertEqual(pmx.materials[0].name, shader)

        # Bones: exporter auto-creates one default root bone when bones=None
        self.assertEqual(len(pmx.bones), 1)

    def test_roundtrip_imported_additional_uv_storage(self):
        """Collector reads canonical imported additional UVs and writer preserves them."""
        transform, _ = self._make_triangle(name="additional_uv_tri_mesh")
        self._assign_shader(transform, shader_name="AdditionalUvMat")
        maya_attribute_utils.write_json_attr(
            transform,
            ATTR_MMD_ADDITIONAL_UVS_JSON,
            {
                "schema_version": 1,
                "vertex_count": 3,
                "source_vertex_count": 3,
                "channel_count": 1,
                "source_vertex_indices": [0, 1, 2],
                "additional_uvs": [
                    [[0.1, 0.2, 0.3, 0.4]],
                    [[1.1, 1.2, 1.3, 1.4]],
                    [[2.1, 2.2, 2.3, 2.4]],
                ],
            },
        )

        maya_data = ExportSceneCollector().collect_from_mesh(transform)
        self.assertEqual(
            maya_data["vertices"][0]["additional_uvs"],
            [[0.1, 0.2, 0.3, 0.4]],
        )

        output_path = self.get_temp_filename("additional_uv_triangle.pmx")
        PmxExporter().export_pmx_model(output_path, maya_data)
        pmx = _parse_pmx(output_path)

        self.assertEqual(pmx.header.additional_uv, 1)
        self.assertEqual(
            [tuple(round(value, 6) for value in vertex.additional_uvs[0]) for vertex in pmx.vertices],
            [
                (0.1, 0.2, 0.3, 0.4),
                (1.1, 1.2, 1.3, 1.4),
                (2.1, 2.2, 2.3, 2.4),
            ],
        )

    def test_imported_uv_metadata_survives_pmx_fresh_import(self):
        """Fresh PMX import restores canonical additional-UV payloads."""
        transform, _ = self._make_triangle(name="fresh_metadata_tri_mesh")
        self._assign_shader(transform, shader_name="FreshMetadataMat")
        maya_attribute_utils.write_json_attr(
            transform,
            ATTR_MMD_ADDITIONAL_UVS_JSON,
            {
                "schema_version": 1,
                "vertex_count": 3,
                "source_vertex_count": 3,
                "channel_count": 1,
                "source_vertex_indices": [0, 1, 2],
                "additional_uvs": [
                    [[0.1, 0.2, 0.3, 0.4]],
                    [[1.1, 1.2, 1.3, 1.4]],
                    [[2.1, 2.2, 2.3, 2.4]],
                ],
            },
        )
        collected = ExportSceneCollector().collect_from_mesh(transform)
        output_path = self.get_temp_filename("fresh_import_metadata.pmx")
        PmxExporter().export_pmx_model(output_path, collected)

        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(
            output_path,
            options={
                "create_mmd_shaders": False,
                "import_physics": False,
                "setup_rig": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        self.assertIsNotNone(fresh_root)
        fresh = ExportSceneCollector().collect_from_model_root(fresh_root)

        for actual_channels, expected_channels in zip(
            fresh["vertices"][0]["additional_uvs"],
            collected["vertices"][0]["additional_uvs"],
        ):
            for actual, expected in zip(actual_channels, expected_channels):
                self.assertAlmostEqual(actual, expected, places=6)
    def test_imported_pmx_soft_body_provenance_blocks_export(self):
        """Unsupported PMX 2.1 soft bodies survive import as a blocking export sentinel."""
        source_path = self.get_temp_filename("soft_body_import.pmx")
        with open(source_path, "wb") as stream:
            stream.write(PmxMock.create_minimal_pmx(version=2.1))

        pmx = _parse_pmx(source_path)
        soft_body = PmxSoftBody(
            material_index_size=pmx.header.material_index_size,
            rigid_body_index_size=pmx.header.rigid_body_index_size,
            vertex_index_size=pmx.header.vertex_index_size,
            encoding_flag=0,
        )
        soft_body.name = "cloth"
        soft_body.name_english = "cloth"
        soft_body.material_index = 0
        pmx.soft_bodies = [soft_body]
        pmx.write_file(source_path)

        cmds.file(new=True, force=True)
        root = import_mmd_file(
            source_path,
            options={
                "create_mmd_shaders": False,
                "import_physics": False,
                "setup_rig": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        self.assertIsNotNone(root)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_PMX_SOFT_BODY_COUNT}"), 1)

        model_data = ExportSceneCollector().collect_from_model_root(root)
        self.assertEqual(model_data["soft_bodies"], [{"count": 1}])

        output_path = self.get_temp_filename("soft_body_rejected.pmx")
        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": root},
            )
        )
        self.assertFalse(result.succeeded)
        self.assertIn(
            "UNSUPPORTED_FEATURE",
            [issue.code for issue in result.validation_report.issues],
        )
        self.assertFalse(os.path.exists(output_path))

    def test_roundtrip_single_tagged_material_preserves_texture_table(self):
        """Tagged material semantics survive collection, export, and parsing."""
        transform, _ = self._make_triangle(name="tagged_texture_mesh")
        self._assign_tagged_shader_with_textures(transform)

        maya_data = ExportSceneCollector().collect_from_mesh(transform)

        self.assertEqual(maya_data["textures"], ["textures/body.png", "textures/body.spa"])
        material = maya_data["materials"][0]
        self.assertEqual(material["name"], "Tagged material")
        self.assertEqual(material["name_english"], "Tagged material")
        self.assertListAlmostEqual(material["diffuse"], [0.8, 0.7, 0.6, 1.0])
        self.assertListAlmostEqual(material["specular"], [0.2, 0.3, 0.4])
        self.assertAlmostEqual(material["specular_coefficient"], 5.0)
        self.assertListAlmostEqual(material["ambient"], [0.1, 0.1, 0.1])
        self.assertEqual(material["draw_flag"], 0x13)
        self.assertListAlmostEqual(material["edge_color"], [0.0, 0.0, 0.0, 1.0])
        self.assertAlmostEqual(material["edge_size"], 1.0)
        self.assertEqual(material["texture_index"], 0)
        self.assertEqual(material["sphere_texture_index"], 1)
        self.assertEqual(material["sphere_mode"], 0)
        self.assertEqual(material["shared_toon_flag"], 1)
        self.assertEqual(material["toon_texture_index"], 0)
        self.assertEqual(material["memo"], "")
        self.assertEqual(material["semantic_missing"], [])

        output_path = self.get_temp_filename("tagged_texture_triangle.pmx")
        PmxExporter().export_pmx_model(output_path, maya_data)

        pmx = _parse_pmx(output_path)
        self.assertEqual(pmx.textures, ["textures/body.png", "textures/body.spa"])
        parsed_material = pmx.materials[0]
        self.assertEqual(parsed_material.name, "Tagged material")
        self.assertEqual(parsed_material.name_english, "Tagged material")
        self.assertListAlmostEqual(parsed_material.diffuse, [0.8, 0.7, 0.6, 1.0])
        self.assertListAlmostEqual(parsed_material.specular, [0.2, 0.3, 0.4])
        self.assertAlmostEqual(parsed_material.specular_coefficient, 5.0)
        self.assertListAlmostEqual(parsed_material.ambient, [0.1, 0.1, 0.1])
        self.assertEqual(int(parsed_material.draw_flag), 0x13)
        self.assertListAlmostEqual(parsed_material.edge_color, [0.0, 0.0, 0.0, 1.0])
        self.assertAlmostEqual(parsed_material.edge_size, 1.0)
        self.assertEqual(parsed_material.texture_index, 0)
        self.assertEqual(parsed_material.sphere_texture_index, 1)
        self.assertEqual(int(parsed_material.sphere_mode), 0)
        self.assertEqual(int(parsed_material.shared_toon_flag), 1)
        self.assertEqual(parsed_material.toon_texture_index, 0)
        self.assertEqual(parsed_material.memo, "")

    def test_collect_single_mesh_orders_tagged_materials_and_faces_by_source_index(self):
        """Reverse face/SG order is restored to the canonical PMX material order."""
        result = cmds.polyPlane(w=2, h=1, sx=2, sy=1, ch=False)
        transform = result[0]

        self._assign_tagged_shader_to_component_with_textures(
            f"{transform}.f[0]",
            1,
            "TaggedSourceOne",
            "Material One",
        )
        self._assign_tagged_shader_to_component_with_textures(
            f"{transform}.f[1]",
            0,
            "TaggedSourceZero",
            "Material Zero",
        )

        maya_data = ExportSceneCollector().collect_from_mesh(transform)

        self.assertEqual(
            [material["source_material_index"] for material in maya_data["materials"]],
            [0, 1],
        )
        self.assertEqual(
            [material["name"] for material in maya_data["materials"]],
            ["Material Zero", "Material One"],
        )
        self.assertEqual(
            [material["face_count"] for material in maya_data["materials"]],
            [6, 6],
        )

        shape = (cmds.listRelatives(transform, shapes=True, type="mesh", fullPath=True) or [])[0]
        selection = om.MSelectionList()
        selection.add(shape)
        mesh_fn = om.MFnMesh(selection.getDagPath(0))
        expected_faces = [
            list(reversed(mesh_fn.getPolygonVertices(1))),
            list(reversed(mesh_fn.getPolygonVertices(0))),
        ]
        self.assertEqual(maya_data["faces"], expected_faces)

        output_path = self.get_temp_filename("tagged_material_source_order.pmx")
        PmxExporter().export_pmx_model(output_path, maya_data)
        pmx = _parse_pmx(output_path)
        self.assertEqual([material.name for material in pmx.materials], ["Material Zero", "Material One"])

    def test_export_model_action_collects_target_mesh_and_writes_pmx(self):
        """ExportModelAction の default collector 経由で PMX を書き出せる。"""
        transform, _ = self._make_triangle(name="action_tri_mesh")
        shader = self._assign_shader(transform, shader_name="ActionTriMat")
        output_path = self.get_temp_filename("action_triangle.pmx")

        with patch(
            "mmd_tools.converters.export_scene_collector.cmds.skinPercent",
            side_effect=AssertionError("export skin collection must use bulk API"),
        ):
            result = ExportModelAction().execute(
                ExportModelRequest(
                    file_path=output_path,
                    options={"export_format": "pmx", "target_mesh": transform},
                )
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(
            Path(result.exported_path).resolve(), Path(output_path).resolve()
        )
        self.assertTrue(os.path.exists(output_path), "PMX file was not written")

        pmx = _parse_pmx(output_path)
        self.assertEqual(len(pmx.vertices), 3)
        self.assertEqual(len(pmx.faces), 1)
        self.assertEqual(pmx.materials[0].name, shader)

    def test_skinned_mesh_collection_uses_rest_geometry_without_changing_pose(self):
        """PMX vertices stay at skin rest geometry while the current pose remains live."""
        transform, (root_joint, child_joint), _skin_cluster = self._make_skinned_triangle(
            "posed_export_tri"
        )
        collector = ExportSceneCollector()
        rest_data = collector.collect_from_mesh(transform)
        rest_vertices = [
            tuple(vertex["position"])
            for vertex in rest_data["vertices"]
        ]
        rest_bones = [tuple(bone["position"]) for bone in rest_data["bones"]]

        cmds.setAttr(f"{child_joint}.rotateZ", 45.0)
        cmds.setAttr(f"{child_joint}.translateX", 1.0)
        placement = cmds.group(empty=True, name="posed_export_placement")
        cmds.parent(transform, root_joint, placement)
        cmds.setAttr(f"{placement}.translate", 7.0, 8.0, 9.0, type="double3")
        shape = (
            cmds.listRelatives(
                transform,
                shapes=True,
                noIntermediate=True,
                fullPath=True,
            )
            or []
        )[0]
        selection = om.MSelectionList()
        selection.add(shape)
        visible_points = om.MFnMesh(selection.getDagPath(0)).getPoints(om.MSpace.kWorld)
        self.assertNotAlmostEqual(float(visible_points[2].x), 0.0)

        posed_rotation = cmds.getAttr(f"{child_joint}.rotateZ")
        posed_translation = cmds.getAttr(f"{child_joint}.translateX")
        posed_data = collector.collect_from_mesh(transform)
        posed_vertices = [
            tuple(vertex["position"])
            for vertex in posed_data["vertices"]
        ]
        posed_bones = [tuple(bone["position"]) for bone in posed_data["bones"]]

        output_path = self.get_temp_filename("posed_mesh_rest_export.pmx")
        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_mesh": transform},
            )
        )
        exported = _parse_pmx(output_path)
        exported_vertices = [tuple(vertex.position) for vertex in exported.vertices]
        exported_bones = [tuple(bone.position) for bone in exported.bones]

        self.assertEqual(posed_vertices, rest_vertices)
        self.assertEqual(posed_bones, rest_bones)
        self.assertTrue(result.succeeded)
        for exported_vertex, expected_vertex in zip(exported_vertices, rest_vertices):
            for exported_value, expected_value in zip(exported_vertex, expected_vertex):
                self.assertAlmostEqual(exported_value, expected_value, places=6)
        for exported_bone, expected_bone in zip(exported_bones, rest_bones):
            for exported_value, expected_value in zip(exported_bone, expected_bone):
                self.assertAlmostEqual(exported_value, expected_value, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{child_joint}.rotateZ"), posed_rotation)
        self.assertAlmostEqual(cmds.getAttr(f"{child_joint}.translateX"), posed_translation)

    def test_export_model_action_collects_model_root_meshes_to_pmx(self):
        """target_model 配下の複数 mesh を PMX の単一 model data にまとめる。"""
        root, _meshes, shaders = self._make_two_mesh_model_root("pmx_multi_root")
        output_path = self.get_temp_filename("multi_mesh_model.pmx")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": root},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)

        self.assertEqual(pmx.header.model_name, "MergedExport")
        self.assertEqual(len(pmx.vertices), 6)
        self.assertEqual(len(pmx.faces), 2)
        self.assertEqual(len(pmx.materials), 2)
        self.assertEqual([mat.face_count for mat in pmx.materials], [3, 3])
        self.assertEqual({mat.name for mat in pmx.materials}, set(shaders))

    def test_target_model_orders_tagged_material_ranges_by_source_index(self):
        """Reverse-DAG child meshes export PMX materials and face ranges by source order."""
        root, (mesh_a, mesh_b), (shader_a, shader_b) = self._make_two_mesh_model_root(
            "pmx_tagged_multi_root"
        )
        self._set_tagged_shader_with_textures(shader_a, 1, "Material One")
        self._set_tagged_shader_with_textures(shader_b, 0, "Material Zero")
        output_path = self.get_temp_filename("tagged_multi_mesh_model.pmx")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": root},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)
        self.assertEqual(
            [material.name for material in pmx.materials],
            ["Material Zero", "Material One"],
        )
        self.assertEqual([material.face_count for material in pmx.materials], [3, 3])
        self.assertEqual(
            [tuple(face.indices) for face in pmx.faces],
            [(5, 4, 3), (2, 1, 0)],
        )

    def test_export_model_action_collects_scene_morph_metadata_to_pmx(self):
        """target_model export は scene の vertex/group/bone/material morph metadata を PMX に書き戻す。"""
        root, (mesh_a, _mesh_b), _shaders = self._make_two_mesh_model_root("pmx_morph_root")
        self._create_scene_morph_metadata(mesh_a, root_group=root)
        output_path = self.get_temp_filename("morph_metadata_model.pmx")

        with patch(
            "mmd_tools.converters.export_scene_collector.cmds.pointPosition",
            side_effect=AssertionError("vertex morph collection must use MFnMesh.getPoints"),
        ):
            result = ExportModelAction().execute(
                ExportModelRequest(
                    file_path=output_path,
                    options={"export_format": "pmx", "target_model": root},
                )
            )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)

        self.assertEqual(len(pmx.morphs), 4)
        self.assertEqual(
            [m.name for m in pmx.morphs],
            ["グループ笑い", "頂点にこり", "ボーン笑い", "材質点滅"],
        )
        vertex_morph = next(m for m in pmx.morphs if m.name == "頂点にこり")
        self.assertEqual(int(vertex_morph.morph_type), 1)
        self.assertEqual(vertex_morph.offsets[0]["vertex_index"], 1)
        self.assertAlmostEqual(vertex_morph.offsets[0]["position_offset"][0], 0.25)
        group_morph = next(m for m in pmx.morphs if m.name == "グループ笑い")
        self.assertEqual(int(group_morph.morph_type), int(PmxMorphType.GroupMorph))
        self.assertEqual(group_morph.offsets[0]["morph_index"], 1)
        self.assertAlmostEqual(group_morph.offsets[0]["morph_rate"], 0.5)
        self.assertTrue(any(m.name == "ボーン笑い" and int(m.morph_type) == 2 for m in pmx.morphs))
        self.assertTrue(any(m.name == "材質点滅" and int(m.morph_type) == 8 for m in pmx.morphs))

    def test_vertex_morph_collection_preserves_connected_locked_keyed_state(self):
        """Stored target deltas export without mutating the evaluated blendShape graph."""
        mesh, shape = self._make_triangle(name="protected_morph_mesh")
        self._create_scene_morph_metadata(mesh)
        blend_shape = next(
            node for node in (cmds.listHistory(shape, pruneDagObjects=True) or [])
            if cmds.nodeType(node) == "blendShape"
        )
        weight = f"{blend_shape}.weight[0]"
        cmds.setKeyframe(weight, time=1, value=0.2)
        cmds.setKeyframe(weight, time=12, value=0.6)
        cmds.setAttr(weight, lock=True)
        cmds.setAttr(f"{blend_shape}.envelope", 0.35)
        cmds.currentTime(12)

        def snapshot():
            return {
                "weight": cmds.getAttr(weight),
                "locked": cmds.getAttr(weight, lock=True),
                "incoming": cmds.listConnections(weight, source=True, destination=False, plugs=True) or [],
                "key_times": cmds.keyframe(weight, query=True, timeChange=True) or [],
                "key_values": cmds.keyframe(weight, query=True, valueChange=True) or [],
                "envelope": cmds.getAttr(f"{blend_shape}.envelope"),
                "time": cmds.currentTime(query=True),
                "points": cmds.xform(f"{shape}.vtx[*]", query=True, objectSpace=True, translation=True),
            }

        before = snapshot()
        morphs = export_scene_collector._collect_vertex_morphs(shape)
        self.assertEqual(snapshot(), before)
        self.assertEqual([morph["name"] for morph in morphs], ["頂点にこり"])
        self.assertEqual(morphs[0]["offsets"][0]["vertex_index"], 1)
        self.assertAlmostEqual(morphs[0]["offsets"][0]["position_offset"][0], 0.25)

        real_get_attr = cmds.getAttr

        def mismatched_points(plug, *args, **kwargs):
            if str(plug).endswith(".inputPointsTarget"):
                return [(0.25, 0.0, 0.0, 1.0), (0.5, 0.0, 0.0, 1.0)]
            return real_get_attr(plug, *args, **kwargs)

        with patch.object(export_scene_collector.cmds, "getAttr", side_effect=mismatched_points):
            with self.assertRaisesRegex(ValueError, f"{blend_shape}.*target 0"):
                export_scene_collector._collect_vertex_morphs(shape)
        self.assertEqual(snapshot(), before)

    def test_vertex_morph_collection_uses_geometry_index_and_flattens_ranges(self):
        """Multi-geometry targets use their logical index and Maya's component flattening."""
        base_a = cmds.polyPlane(name="morph_geo_a", subdivisionsX=1, subdivisionsY=1)[0]
        base_b = cmds.polyPlane(name="morph_geo_b", subdivisionsX=1, subdivisionsY=1)[0]
        target_a = cmds.duplicate(base_a, name="morph_target_a")[0]
        target_b = cmds.duplicate(base_b, name="morph_target_b")[0]
        cmds.move(0.25, 0.5, -0.75, f"{target_a}.vtx[1:3]", relative=True)
        cmds.move(-0.5, 0.75, 0.25, f"{target_b}.vtx[1:3]", relative=True)

        blend_shape = cmds.blendShape(base_a, name="multiGeometryBlendShape")[0]
        cmds.blendShape(blend_shape, edit=True, geometry=base_b)
        cmds.blendShape(blend_shape, edit=True, target=(base_a, 0, target_a, 1.0))
        cmds.blendShape(blend_shape, edit=True, target=(base_b, 0, target_b, 1.0))
        cmds.aliasAttr("range_morph", f"{blend_shape}.weight[0]")
        empty_target = cmds.duplicate(base_b, name="empty_morph_target")[0]
        cmds.blendShape(blend_shape, edit=True, target=(base_b, 1, empty_target, 1.0))
        cmds.aliasAttr("empty_morph", f"{blend_shape}.weight[1]")
        cmds.delete(target_a, target_b, empty_target)

        shape_b = cmds.listRelatives(base_b, shapes=True, type="mesh")[0]
        self.assertEqual(export_scene_collector._blendshape_geometry_index(blend_shape, shape_b), 1)
        morphs = export_scene_collector._collect_vertex_morphs(shape_b)

        self.assertEqual([morph["name"] for morph in morphs], ["range_morph"])
        self.assertEqual([offset["vertex_index"] for offset in morphs[0]["offsets"]], [1, 2, 3])
        for offset in morphs[0]["offsets"]:
            self.assertListAlmostEqual(offset["position_offset"], [-0.5, 0.75, -0.25])

        shape_a = cmds.listRelatives(base_a, shapes=True, type="mesh")[0]
        morphs_a = export_scene_collector._collect_vertex_morphs(shape_a)
        self.assertEqual([morph["name"] for morph in morphs_a], ["range_morph"])

        with self.assertRaisesRegex(ValueError, f"{blend_shape}.*target 99.*6000"):
            export_scene_collector._stored_blendshape_target_offsets(
                blend_shape,
                shape_b,
                1,
                99,
                4,
                0,
            )

    def test_export_model_action_collects_display_frames_to_pmx(self):
        """target_model export は root の表示枠 metadata を PMX に書き戻す。"""
        root, _meshes, _shaders = self._make_two_mesh_model_root("pmx_display_frame_root")
        display_frames = [
            {
                "name": "Root",
                "name_english": "Root",
                "special_flag": 1,
                "elements": [{"type": 0, "index": 0}],
            },
            {
                "name": "表情",
                "name_english": "Exp",
                "special_flag": 1,
                "elements": [],
            },
            {
                "name": "操作",
                "name_english": "Controls",
                "special_flag": 0,
                "elements": [{"type": 0, "index": 0}],
            },
        ]
        cmds.addAttr(root, longName=ATTR_MMD_DISPLAY_FRAMES_JSON, dataType="string")
        cmds.setAttr(
            f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}",
            json.dumps(display_frames, ensure_ascii=False),
            type="string",
        )
        output_path = self.get_temp_filename("display_frame_metadata_model.pmx")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": root},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)

        self.assertEqual([frame.name for frame in pmx.display_frames], ["Root", "表情", "操作"])
        self.assertEqual(pmx.display_frames[2].name_english, "Controls")
        self.assertEqual(pmx.display_frames[2].elements, [{"type": 0, "index": 0}])

    def test_export_model_action_collects_skincluster_weights_to_pmx(self):
        """skinCluster の influence と weight を PMX bones/BDEF に書き出す。"""
        transform, _joints, _skin_cluster = self._make_skinned_triangle("pmx_skinned_tri")
        output_path = self.get_temp_filename("skinned_triangle.pmx")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_mesh": transform},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)

        self.assertEqual([bone.name for bone in pmx.bones], ["センター", "上半身"])
        self.assertEqual([bone.parent_bone_index for bone in pmx.bones], [-1, 0])
        self.assertEqual(pmx.vertices[0].weight_transform_type, 2)
        self.assertEqual(pmx.vertices[0].bone_indices, [0, 0, 0, 0])
        self.assertEqual(pmx.vertices[1].weight_transform_type, 2)
        self.assertEqual(pmx.vertices[1].bone_indices, [1, 0, 1, 1])
        self.assertAlmostEqual(pmx.vertices[1].bone_weights[0], 0.75)
        self.assertAlmostEqual(pmx.vertices[1].bone_weights[1], 0.25)
        self.assertEqual(pmx.vertices[2].weight_transform_type, 2)
        self.assertEqual(pmx.vertices[2].bone_indices, [1, 1, 1, 1])

    def test_export_model_action_roundtrips_non_ik_bone_semantics(self):
        """Canonical non-IK bone attrs survive Maya → PMX → parser round-trip."""
        transform, joints, _skin_cluster = self._make_skinned_triangle("pmx_bone_semantics")
        root_joint, child_joint = joints
        authored_flags = int(
            PmxBoneFlag.CONNECT_BONE
            | PmxBoneFlag.ROTATABLE
            | PmxBoneFlag.MOVABLE
            | PmxBoneFlag.DISPLAY
            | PmxBoneFlag.OPERATABLE
            | PmxBoneFlag.LOCAL
            | PmxBoneFlag.GRANT_PARENT_ROTATE
            | PmxBoneFlag.GRANT_PARENT_MOVE
            | PmxBoneFlag.AXIS_FIXED
            | PmxBoneFlag.LOCAL_AXIS
            | PmxBoneFlag.DEFORM_AFTER_PHYSICS
            | PmxBoneFlag.EXTERNAL_PARENT_DEFORM
        )
        maya_attribute_utils.set_custom_attributes(
            root_joint,
            {
                ATTR_MMD_BONE_FLAGS: int(
                    PmxBoneFlag.ROTATABLE
                    | PmxBoneFlag.MOVABLE
                    | PmxBoneFlag.DISPLAY
                    | PmxBoneFlag.OPERATABLE
                ),
                ATTR_MMD_BONE_OFFSET: [1.0, 2.0, 3.0],
            },
        )
        maya_attribute_utils.set_custom_attributes(
            child_joint,
            {
                ATTR_MMD_BONE_FLAGS: authored_flags,
                ATTR_MMD_DEFORM_LAYER: 7,
                ATTR_MMD_PMX_REST_POSITION: [7.0, 8.0, 9.0],
                ATTR_MMD_CONNECTION_BONE: "センター",
                ATTR_MMD_CONNECT_INDEX: 0,
                ATTR_MMD_CONNECT_BONE_INDEX: 0,
                ATTR_MMD_GRANT_PARENT: "センター",
                ATTR_MMD_GRANT_PARENT_INDEX: 0,
                ATTR_MMD_GRANT_RATE: 0.25,
                ATTR_MMD_FIXED_AXIS: [0.0, 1.0, 0.0],
                ATTR_MMD_AXIS_DIRECTION: [0.0, 1.0, 0.0],
                ATTR_MMD_LOCAL_X_AXIS: [1.0, 0.0, 0.0],
                ATTR_MMD_X_AXIS_DIRECTION: [1.0, 0.0, 0.0],
                ATTR_MMD_LOCAL_Z_AXIS: [0.0, 0.0, 1.0],
                ATTR_MMD_Z_AXIS_DIRECTION: [0.0, 0.0, 1.0],
                ATTR_MMD_EXTERNAL_PARENT_KEY: 42,
            },
        )

        output_path = self.get_temp_filename("bone_semantics.pmx")
        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_mesh": transform},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)
        bone = pmx.bones[1]
        self.assertEqual(pmx.bones[0].connect_position_offset, (1.0, 2.0, 3.0))
        self.assertEqual(int(bone.bone_flag), authored_flags)
        self.assertEqual(bone.position, (7.0, 8.0, 9.0))
        self.assertEqual(bone.transform_layer, 7)
        self.assertEqual(bone.connect_bone_index, 0)
        self.assertEqual(bone.grant_parent_bone_index, 0)
        self.assertAlmostEqual(bone.grant_rate, 0.25)
        self.assertEqual(bone.axis_direction, (0.0, 1.0, 0.0))
        self.assertEqual(bone.x_axis_direction, (1.0, 0.0, 0.0))
        self.assertEqual(bone.z_axis_direction, (0.0, 0.0, 1.0))
        self.assertEqual(bone.key_value, 42)

    def test_export_model_action_roundtrips_supported_ik_metadata(self):
        """Maya IK attributes reach parsed PMX target, loop, limit, and links."""
        transform, joints, _skin_cluster = self._make_skinned_triangle("pmx_ik_tri")
        ik_joint = joints[1]
        cmds.addAttr(ik_joint, longName=ATTR_MMD_BONE_FLAGS, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_BONE_FLAGS}", 0x003E)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_TARGET, dataType="string")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_TARGET}", "センター", type="string")
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_TARGET_INDEX, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_TARGET_INDEX}", 0)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LOOP, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_LOOP}", 8)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LIMIT_ANGLE, attributeType="double")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_LIMIT_ANGLE}", 0.5)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LINKS, dataType="string")
        cmds.setAttr(
            f"{ik_joint}.{ATTR_MMD_IK_LINKS}",
            json.dumps(
                [
                    {
                        "bone": 0,
                        "limit_enabled": True,
                        "lower_limit": [-0.5, -0.25, -0.1],
                        "upper_limit": [0.5, 0.25, 0.1],
                    }
                ]
            ),
            type="string",
        )

        output_path = self.get_temp_filename("ik_metadata.pmx")
        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_mesh": transform},
            )
        )

        self.assertTrue(result.succeeded)
        pmx = _parse_pmx(output_path)
        ik_bone = pmx.bones[1]
        self.assertEqual(int(ik_bone.bone_flag) & 0x0020, 0x0020)
        self.assertEqual(ik_bone.ik_target_bone_index, 0)
        self.assertEqual(ik_bone.ik_loop_count, 8)
        self.assertAlmostEqual(ik_bone.ik_limit_angle, 0.5)
        self.assertEqual(len(ik_bone.ik_links), 1)
        self.assertEqual(ik_bone.ik_links[0].ik_bone_index, 0)
        self.assertEqual(ik_bone.ik_links[0].angle_limit, 1)
        for actual, expected in zip(ik_bone.ik_links[0].limit_min, (-0.5, -0.25, -0.1)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(ik_bone.ik_links[0].limit_max, (0.5, 0.25, 0.1)):
            self.assertAlmostEqual(actual, expected)

    def test_invalid_ik_metadata_is_rejected_before_writer(self):
        """An unresolved Maya IK target must not enter the PMX writer."""
        transform, joints, _skin_cluster = self._make_skinned_triangle("pmx_invalid_ik")
        ik_joint = joints[1]
        cmds.addAttr(ik_joint, longName=ATTR_MMD_BONE_FLAGS, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_BONE_FLAGS}", 0x003E)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_TARGET_INDEX, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_TARGET_INDEX}", 99)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LOOP, attributeType="long")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_LOOP}", 8)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LIMIT_ANGLE, attributeType="double")
        cmds.setAttr(f"{ik_joint}.{ATTR_MMD_IK_LIMIT_ANGLE}", 0.5)
        cmds.addAttr(ik_joint, longName=ATTR_MMD_IK_LINKS, dataType="string")
        cmds.setAttr(
            f"{ik_joint}.{ATTR_MMD_IK_LINKS}",
            json.dumps([{"bone": 0}]),
            type="string",
        )

        output_path = self.get_temp_filename("invalid_ik_metadata.pmx")
        with patch.object(PmxExporter, "export_pmx_model") as writer:
            result = ExportModelAction().execute(
                ExportModelRequest(
                    file_path=output_path,
                    options={"export_format": "pmx", "target_mesh": transform},
                )
            )

        self.assertFalse(result.succeeded)
        self.assertIsNotNone(result.validation_report)
        self.assertTrue(result.validation_report.is_blocking)
        writer.assert_not_called()

    def test_model_export_keeps_metadata_bone_without_skin_influence(self):
        """model exportは0-weight jointもSkeleton metadataからPMX boneへ戻す。"""
        transform, joints, skin_cluster = self._make_skinned_triangle("pmx_zero_weight_bone")
        root = cmds.group(empty=True, name="pmx_zero_weight_root")
        maya_attribute_utils.set_custom_attributes(
            root,
            {
                ATTR_MMD_MODEL_NAME: "ZeroWeight",
                ATTR_MMD_MODEL_NAME_EN: "ZeroWeight",
                ATTR_MMD_COMMENT: "",
                ATTR_MMD_COMMENT_EN: "",
            },
        )
        cmds.parent(transform, root)
        cmds.parent(joints[0], root)
        for joint, rest_position in zip(joints, ([0.0, 0.0, 0.0], [0.0, 2.0, 0.0])):
            maya_attribute_utils.set_custom_attributes(
                joint,
                {
                    ATTR_MMD_BONE_FLAGS: 0,
                    ATTR_MMD_BONE_OFFSET: [0.0, -1.0, 0.0],
                    ATTR_MMD_PMX_REST_POSITION: rest_position,
                    ATTR_MMD_DEFORM_LAYER: 0,
                },
            )
        mesh_shape = (cmds.listRelatives(transform, shapes=True, type="mesh") or [None])[0]
        shader_groups = (
            cmds.listConnections(mesh_shape, type="shadingEngine") if mesh_shape else []
        ) or []
        for shading_group in shader_groups:
            shaders = cmds.listConnections(
                f"{shading_group}.surfaceShader",
                source=True,
                destination=False,
            ) or []
            for shader in shaders:
                self._set_tagged_shader_with_textures(shader, 0, shader)
        cmds.select(joints[1], replace=True)
        unused_joint = cmds.joint(name="pmx_unused_ik_target", position=[0.0, 3.0, 0.0])
        for attr, value in [
            (ATTR_MMD_BONE_INDEX, 2),
            (ATTR_MMD_BONE_PARENT_INDEX, 1),
        ]:
            cmds.addAttr(unused_joint, longName=attr, attributeType="long")
            cmds.setAttr(f"{unused_joint}.{attr}", value)
        for attr, value in [
            (ATTR_MMD_BONE_NAME, "ＩＫ先"),
            (ATTR_MMD_BONE_NAME_EN, "IKTarget"),
        ]:
            cmds.addAttr(unused_joint, longName=attr, dataType="string")
            cmds.setAttr(f"{unused_joint}.{attr}", value, type="string")
        maya_attribute_utils.set_custom_attributes(
            unused_joint,
            {
                ATTR_MMD_BONE_FLAGS: 0,
                ATTR_MMD_BONE_OFFSET: [0.0, -1.0, 0.0],
                ATTR_MMD_PMX_REST_POSITION: [0.0, 3.0, 0.0],
                ATTR_MMD_DEFORM_LAYER: 0,
            },
        )

        output_path = self.get_temp_filename("zero_weight_bone.pmx")
        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": root},
            )
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(cmds.skinCluster(skin_cluster, query=True, influence=True)), 2)
        pmx = _parse_pmx(output_path)
        self.assertEqual([bone.name for bone in pmx.bones], ["センター", "上半身", "ＩＫ先"])
        self.assertEqual([bone.parent_bone_index for bone in pmx.bones], [-1, 0, 1])

    def test_roundtrip_quad_triangulates_to_two_faces(self):
        """Quad polygon → fan triangulation → 2 PmxFace objects, face_count=6."""
        result = cmds.polyCreateFacet(
            p=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            name="quad_mesh",
        )
        transform = result[0]
        self._assign_shader(transform, shader_name="QuadMat")

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        # Collector should produce 1 polygon (the quad) in faces
        self.assertEqual(len(maya_data["faces"]), 1)
        self.assertEqual(len(maya_data["faces"][0]), 4)  # 4 vertices
        # Fan triangulation of a quad: 2 triangles → face_count = 6
        self.assertEqual(maya_data["materials"][0]["face_count"], 6)

        output_path = self.get_temp_filename("test_quad.pmx")
        exporter = PmxExporter()
        exporter.export_pmx_model(output_path, maya_data)

        pmx = _parse_pmx(output_path)

        self.assertEqual(len(pmx.faces), 2)  # 2 triangles after fan-triangulation
        self.assertEqual(pmx.materials[0].face_count, 6)

    def test_roundtrip_two_material_faces(self):
        """Two polygon faces with different shaders export as two PMX materials.

        Creates a 2-quad plane (sx=2, sy=1), assigns MatA to face 0 and MatB to
        face 1, then verifies the full collect → export → parse round-trip:
        - two materials are present in the PMX;
        - material names match the assigned shader node names;
        - each material's face_count equals 6 (one quad → 2 triangles × 3 indices);
        - total PMX face count is 4 triangulated faces.
        """
        result = cmds.polyPlane(w=2, h=1, sx=2, sy=1, ch=False)
        transform = result[0]

        shader_a = self._assign_shader_to_component(f"{transform}.f[0]", "MatA")
        shader_b = self._assign_shader_to_component(f"{transform}.f[1]", "MatB")

        collector = ExportSceneCollector()
        maya_data = collector.collect_from_mesh(transform)

        # Two materials, ordered by first polygon occurrence.
        self.assertEqual(len(maya_data["materials"]), 2)

        mat_names = {m["name"] for m in maya_data["materials"]}
        self.assertIn(shader_a, mat_names)
        self.assertIn(shader_b, mat_names)

        # Each quad fan-triangulates to 2 triangles → face_count = 6.
        for mat in maya_data["materials"]:
            self.assertEqual(mat["face_count"], 6)

        # Two faces total (one quad per material).
        self.assertEqual(len(maya_data["faces"]), 2)

        output_path = self.get_temp_filename("test_two_mat.pmx")
        exporter = PmxExporter()
        exporter.export_pmx_model(output_path, maya_data)

        self.assertTrue(os.path.exists(output_path), "PMX file was not written")

        pmx = _parse_pmx(output_path)

        self.assertEqual(len(pmx.materials), 2)
        pmx_mat_names = {m.name for m in pmx.materials}
        self.assertIn(shader_a, pmx_mat_names)
        self.assertIn(shader_b, pmx_mat_names)

        for mat in pmx.materials:
            self.assertEqual(mat.face_count, 6)

        # 2 quads → 4 triangulated PmxFace objects.
        self.assertEqual(len(pmx.faces), 4)
