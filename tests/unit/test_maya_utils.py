import unittest

from maya import cmds

from mmd_tools.core import maya_utils, utils
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

        mesh_transform = maya_utils.create_mesh_with_uvs(
            name, vertices, face_counts, face_connects, uvs, face_uv_connects
        )

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
        shading_groups = cmds.listConnections(
            cmds.listRelatives(mesh_name, shapes=True)[0], type="shadingEngine"
        )
        self.assertIn(shader_node + "SG", shading_groups)

    def test_set_custom_attributes(self):
        """カスタムアトリビュートを設定できるか"""
        mesh_name = "test_mesh_for_custom_attr"
        cmds.polyCube(name=mesh_name)

        data = {
            "mmd_bytes": b"PMX",
            "mmd_file_version": 2.0,
            "mmd_utf8": "裙花蕊颜顔",
            "mmd_string": "Test Model EN",
            "mmd_bool": True,
            "mmd_int": 42,
        }
        maya_utils.set_custom_attributes(mesh_name, data)

        # Verify the attribute exists and has the correct value
        for key, value in data.items():
            self.assertTrue(cmds.attributeQuery(key, node=mesh_name, exists=True))
            attr_value = cmds.getAttr(mesh_name + "." + key)
            self.assertEqual(
                attr_value, value.decode("utf-8") if isinstance(value, bytes) else value
            )

    def test_create_ik_handle(self):
        """IKハンドルを作成できるか"""
        # ジョイントチェーンを作成
        cmds.select(clear=True)
        joint1 = cmds.joint(position=[0, 0, 0], name="joint1")
        cmds.joint(position=[0, 5, 0], name="joint2")  # 中間ジョイント
        joint3 = cmds.joint(position=[0, 10, 0], name="joint3")

        # IKハンドルを作成
        ik_handle, effector = maya_utils.create_ik_handle(
            start_joint=joint1, end_joint=joint3, solver="ikRPsolver"
        )

        self.assertTrue(cmds.objExists(ik_handle))
        self.assertTrue(cmds.objExists(effector))
        self.assertEqual(cmds.nodeType(ik_handle), "ikHandle")

    def test_create_ik_handle_invalid_joint(self):
        """存在しないジョイントでIKハンドル作成時にエラーが発生するか"""
        with self.assertRaises(ValueError) as context:
            maya_utils.create_ik_handle(
                start_joint="nonexistent_joint", end_joint="another_nonexistent_joint"
            )
        self.assertIn("does not exist", str(context.exception))

    def test_set_joint_limits(self):
        """ジョイントの回転制限を設定できるか"""
        # ジョイントを作成
        cmds.select(clear=True)
        joint = cmds.joint(position=[0, 0, 0], name="test_joint")

        # 回転制限を設定
        limit_min = [-1.57, -0.5, -0.3]  # ラジアン
        limit_max = [1.57, 0.5, 0.3]

        result = maya_utils.set_joint_limits(
            joint=joint, limit_min=limit_min, limit_max=limit_max, enable_limits=True
        )

        self.assertTrue(result)

        # 制限が設定されているか確認
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMinX"), limit_min[0], places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMinY"), limit_min[1], places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMinZ"), limit_min[2], places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMaxX"), limit_max[0], places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMaxY"), limit_max[1], places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.rotateMaxZ"), limit_max[2], places=5
        )

        # 制限が有効になっているか確認
        self.assertTrue(cmds.getAttr(f"{joint}.minRotXLimitEnable"))
        self.assertTrue(cmds.getAttr(f"{joint}.maxRotXLimitEnable"))

    def test_cross_product(self):
        """外積計算が正しいか"""
        vec1 = [1, 0, 0]
        vec2 = [0, 1, 0]
        result = utils.cross_product(vec1, vec2)

        # X × Y = Z
        self.assertEqual(result, [0, 0, 1])

        # Y × Z = X
        vec3 = [0, 0, 1]
        result2 = utils.cross_product(vec2, vec3)
        self.assertEqual(result2, [1, 0, 0])

    def test_matrix_to_euler(self):
        """行列からオイラー角への変換が正しいか"""
        # 単位行列の場合
        identity_matrix = maya_utils.create_matrix_from_axes(
            x_axis=[1, 0, 0], y_axis=[0, 1, 0], z_axis=[0, 0, 1]
        )

        euler = maya_utils.matrix_to_euler(identity_matrix)

        # 単位行列のオイラー角は全て0
        self.assertAlmostEqual(euler[0], 0.0, places=5)
        self.assertAlmostEqual(euler[1], 0.0, places=5)
        self.assertAlmostEqual(euler[2], 0.0, places=5)


if __name__ == "__main__":
    unittest.main()
