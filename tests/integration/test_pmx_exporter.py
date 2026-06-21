"""Integration tests for PMX export via ExportSceneCollector + PmxExporter.

These tests run under Maya 2024 mayapy and verify the full
collect → export → parse round-trip for a minimum geometry.
"""

import os

from maya import cmds

from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.core.pmx_data import PmxData
from tests.common.maya_test_base import MayaTestBase


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

        pmx = PmxData()
        pmx.parse_file(output_path)

        # Vertex / face counts
        self.assertEqual(len(pmx.vertices), 3)
        self.assertEqual(len(pmx.faces), 1)  # 1 PmxFace (triangle)

        # Material
        self.assertEqual(len(pmx.materials), 1)
        self.assertEqual(pmx.materials[0].face_count, 3)
        self.assertEqual(pmx.materials[0].name, shader)

        # Bones: exporter auto-creates one default root bone when bones=None
        self.assertEqual(len(pmx.bones), 1)

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

        pmx = PmxData()
        pmx.parse_file(output_path)

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

        pmx = PmxData()
        pmx.parse_file(output_path)

        self.assertEqual(len(pmx.materials), 2)
        pmx_mat_names = {m.name for m in pmx.materials}
        self.assertIn(shader_a, pmx_mat_names)
        self.assertIn(shader_b, pmx_mat_names)

        for mat in pmx.materials:
            self.assertEqual(mat.face_count, 6)

        # 2 quads → 4 triangulated PmxFace objects.
        self.assertEqual(len(pmx.faces), 4)
