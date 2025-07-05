import unittest

from maya import cmds

from mmd_tools.core import maya_utils
from tests.common.maya_test_base import MayaTestBase


class TestMayaUtils(MayaTestBase):

    def test_sanitize_maya_name(self):
        """ASCII変換でうまくサニタイズされるか"""
        self.assertEqual(maya_utils.sanitize_text("髪"), "hair")
        self.assertEqual(maya_utils.sanitize_text("invalid-name!"), "invalid_name_")
        self.assertEqual(maya_utils.sanitize_text(" "), "_")
        self.assertEqual(maya_utils.sanitize_text(" name"), "_name")
        self.assertEqual(maya_utils.sanitize_text("name "), "name_")

    def test_create_mesh_with_uvs(self):
        """UV付きのメッシュを作成できるか"""
        name = "test_mesh"
        vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        face_counts = [4]
        face_connects = [0, 1, 2, 3]
        uvs = [0, 0, 1, 0, 1, 1, 0, 1]
        face_uv_connects = [0, 1, 2, 3]

        mesh_transform = maya_utils.create_mesh_with_uvs(name, vertices, face_counts, face_connects, uvs, face_uv_connects)

        self.assertTrue(cmds.objExists(mesh_transform))
        self.assertEqual(cmds.objectType(mesh_transform), "transform")

        shape = cmds.listRelatives(mesh_transform, shapes=True)[0]
        self.assertEqual(cmds.objectType(shape), "mesh")

        # Verify UVs
        uv_coords = cmds.polyEditUV(shape + ".map[*]", query=True)
        self.assertIsNotNone(uv_coords)
        self.assertEqual(len(uv_coords), 8)

    def test_create_material(self):
        """マテリアルを作成できるか"""
        material_name = "test_material"
        color = (0.5, 0.6, 0.7, 0.8)

        shader_node = maya_utils.create_material(material_name, color)

        self.assertTrue(cmds.objExists(shader_node))
        self.assertEqual(cmds.objectType(shader_node), "lambert")

        # Verify color
        rgb = cmds.getAttr(shader_node + ".color")[0]
        self.assertAlmostEqual(rgb[0], color[0], places=5)
        self.assertAlmostEqual(rgb[1], color[1], places=5)
        self.assertAlmostEqual(rgb[2], color[2], places=5)

        # Verify transparency (alpha)
        transparency = cmds.getAttr(shader_node + ".transparency")[0]
        expected_transparency = 1.0 - color[3]
        self.assertAlmostEqual(transparency[0], expected_transparency, places=5)
        self.assertAlmostEqual(transparency[1], expected_transparency, places=5)
        self.assertAlmostEqual(transparency[2], expected_transparency, places=5)

    def test_assign_material(self):
        """メッシュにマテリアルを割り当てられるか"""
        mesh_name = "test_mesh_for_material"
        material_name = "test_material_to_assign"

        # Create a mesh
        cmds.polyCube(name=mesh_name)

        # Create a material
        shader_node = maya_utils.create_material(material_name, (1, 0, 0, 1))

        # Assign the material
        maya_utils.assign_material(mesh_name, shader_node)

        # Verify assignment
        shading_groups = cmds.listConnections(cmds.listRelatives(mesh_name, shapes=True)[0], type='shadingEngine')
        self.assertIn(shader_node + "SG", shading_groups)


    def test_set_custom_attributes(self):
        """カスタムアトリビュートを設定できるか"""
        mesh_name = "test_mesh_for_custom_attr"
        cmds.polyCube(name=mesh_name)

        attr_name = "customAttr"
        data = {
            "mmd_bytes": b'PMX',
            "mmd_file_version": 2.0,
            "mmd_utf8": "裙花蕊颜顔",
            "mmd_string": "Test Model EN",
            "mmd_bool": True,
            "mmd_int": 42
        }
        maya_utils.set_custom_attributes(mesh_name, data)

        # Verify the attribute exists and has the correct value
        for key, value in data.items():
            self.assertTrue(cmds.attributeQuery(key, node=mesh_name, exists=True))
            attr_value = cmds.getAttr(mesh_name + "." + key)
            self.assertEqual(attr_value, value.decode('utf-8') if isinstance(value, bytes) else value)

if __name__ == '__main__':
    unittest.main()
