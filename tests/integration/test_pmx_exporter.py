"""Integration tests for PMX export via ExportSceneCollector + PmxExporter.

These tests run under Maya 2024 mayapy and verify the full
collect → export → parse round-trip for a minimum geometry.
"""

import json
import os
from unittest.mock import patch

from maya import cmds

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.converters import export_scene_collector
from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.converters.morph_converter import MorphConverter
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorphType
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

    def _make_two_mesh_model_root(self, root_name: str = "export_model_root"):
        """Create a root with two child triangle meshes and return (root, meshes)."""
        root = cmds.group(empty=True, name=root_name)
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "MergedExport", type="string")

        mesh_a, _ = self._make_triangle(name=f"{root_name}_mesh_a")
        mesh_b, _ = self._make_triangle(name=f"{root_name}_mesh_b")
        cmds.move(2.0, 0.0, 0.0, mesh_b, absolute=True)
        cmds.parent(mesh_a, root)
        cmds.parent(mesh_b, root)
        shader_a = self._assign_shader(mesh_a, shader_name=f"{root_name}_MatA")
        shader_b = self._assign_shader(mesh_b, shader_name=f"{root_name}_MatB")
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

    def _create_scene_morph_metadata(self, mesh_name: str):
        """Create vertex/bone/material morph metadata through MorphConverter."""

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
            panel = 5
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
                "morphs": [FakeVertexMorph(), FakeBoneMorph(), FakeMaterialMorph()],
            },
        )()
        result = MorphConverter().convert_pmx_morphs(fake_data, mesh_name)
        self.assertTrue(result.get("success", False))
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
        self.assertEqual(result.exported_path, output_path)
        self.assertTrue(os.path.exists(output_path), "PMX file was not written")

        pmx = _parse_pmx(output_path)
        self.assertEqual(len(pmx.vertices), 3)
        self.assertEqual(len(pmx.faces), 1)
        self.assertEqual(pmx.materials[0].name, shader)

    def test_export_model_action_collects_target_mesh_and_writes_pmd(self):
        """ExportModelAction の default collector 経由で PMD を書き出せる。"""
        transform, _ = self._make_triangle(name="action_pmd_tri_mesh")
        cmds.addAttr(transform, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{transform}.{ATTR_MMD_MODEL_NAME}", "PmdTri", type="string")
        self._assign_shader(transform, shader_name="ActionPmdTriMat")
        output_path = self.get_temp_filename("action_triangle.pmd")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmd", "target_mesh": transform},
            )
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, output_path)
        self.assertTrue(os.path.exists(output_path), "PMD file was not written")

        pmd = PmdData()
        pmd.parse_file(output_path)
        self.assertEqual(pmd.header.model_name, "PmdTri")
        self.assertEqual(len(pmd.vertices), 3)
        self.assertEqual(len(pmd.faces), 1)
        self.assertEqual(len(pmd.materials), 1)
        self.assertEqual(pmd.materials[0].face_count, 3)
        self.assertEqual(len(pmd.bones), 1)

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

    def test_export_model_action_collects_model_root_meshes_to_pmd(self):
        """target_model 配下の複数 mesh を PMD の単一 model data にまとめる。"""
        root, _meshes, _shaders = self._make_two_mesh_model_root("pmd_multi_root")
        output_path = self.get_temp_filename("multi_mesh_model.pmd")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmd", "target_model": root},
            )
        )

        self.assertTrue(result.succeeded)
        pmd = PmdData()
        pmd.parse_file(output_path)

        self.assertEqual(pmd.header.model_name, "MergedExport")
        self.assertEqual(len(pmd.vertices), 6)
        self.assertEqual(len(pmd.faces), 2)
        self.assertEqual(len(pmd.materials), 2)
        self.assertEqual([mat.face_count for mat in pmd.materials], [3, 3])

    def test_export_model_action_collects_scene_morph_metadata_to_pmx(self):
        """target_model export は scene の vertex/bone/material morph metadata を PMX に書き戻す。"""
        root, (mesh_a, _mesh_b), _shaders = self._make_two_mesh_model_root("pmx_morph_root")
        self._create_scene_morph_metadata(mesh_a)
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

        self.assertEqual(len(pmx.morphs), 3)
        vertex_morph = next(m for m in pmx.morphs if m.name == "頂点にこり")
        self.assertEqual(int(vertex_morph.morph_type), 1)
        self.assertEqual(vertex_morph.offsets[0]["vertex_index"], 1)
        self.assertAlmostEqual(vertex_morph.offsets[0]["position_offset"][0], 0.25)
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
        self.assertEqual(pmx.vertices[0].weight_transform_type, 0)
        self.assertEqual(pmx.vertices[0].bone_indices, [0])
        self.assertEqual(pmx.vertices[1].weight_transform_type, 1)
        self.assertEqual(pmx.vertices[1].bone_indices, [1, 0])
        self.assertAlmostEqual(pmx.vertices[1].bone_weights[0], 0.75)
        self.assertEqual(pmx.vertices[2].weight_transform_type, 0)
        self.assertEqual(pmx.vertices[2].bone_indices, [1])

    def test_export_model_action_collects_skincluster_weights_to_pmd(self):
        """skinCluster の influence と weight を PMD bones/vertex weight に書き出す。"""
        transform, _joints, _skin_cluster = self._make_skinned_triangle("pmd_skinned_tri")
        output_path = self.get_temp_filename("skinned_triangle.pmd")

        result = ExportModelAction().execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmd", "target_mesh": transform},
            )
        )

        self.assertTrue(result.succeeded)
        pmd = PmdData()
        pmd.parse_file(output_path)

        self.assertEqual([bone.name for bone in pmd.bones], ["センター", "上半身"])
        self.assertEqual([bone.parent_bone_index for bone in pmd.bones], [-1, 0])
        self.assertEqual(pmd.vertices[0].bone_indices, (0, 0))
        self.assertEqual(pmd.vertices[0].bone_weight, 100)
        self.assertEqual(pmd.vertices[1].bone_indices, (1, 0))
        self.assertEqual(pmd.vertices[1].bone_weight, 75)
        self.assertEqual(pmd.vertices[2].bone_indices, (1, 0))
        self.assertEqual(pmd.vertices[2].bone_weight, 100)

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
