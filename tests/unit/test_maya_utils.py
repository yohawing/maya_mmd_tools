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

        # 既存のアトリビュートを更新できるかテスト
        update_data = {
            "mmd_string": "Updated Model Name",
            "mmd_file_version": 2.1,
            "mmd_bool": False,
            "mmd_int": 100,
        }
        maya_utils.set_custom_attributes(mesh_name, update_data)

        # 更新された値を確認
        for key, value in update_data.items():
            attr_value = cmds.getAttr(mesh_name + "." + key)
            if isinstance(value, float):
                self.assertAlmostEqual(attr_value, value, places=5)
            else:
                self.assertEqual(attr_value, value)

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

        # 制限が設定されているか確認（度数単位で比較）
        import math

        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.minRotXLimit"), math.degrees(limit_min[0]), places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.minRotYLimit"), math.degrees(limit_min[1]), places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.minRotZLimit"), math.degrees(limit_min[2]), places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.maxRotXLimit"), math.degrees(limit_max[0]), places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.maxRotYLimit"), math.degrees(limit_max[1]), places=5
        )
        self.assertAlmostEqual(
            cmds.getAttr(f"{joint}.maxRotZLimit"), math.degrees(limit_max[2]), places=5
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

    def test_find_all_mmd_models(self):
        """MMDモデルを検索できるか"""
        # MMDモデルのルートノードを作成
        model1 = cmds.group(empty=True, name="Model1_root")
        cmds.addAttr(model1, longName="mmd_model_name_jp", dataType="string")
        cmds.setAttr(f"{model1}.mmd_model_name_jp", "テストモデル1", type="string")

        model2 = cmds.group(empty=True, name="Model2_root")
        cmds.addAttr(model2, longName="mmd_model_name_en", dataType="string")
        cmds.setAttr(f"{model2}.mmd_model_name_en", "Test Model 2", type="string")

        # MMDではないルートノード
        not_mmd = cmds.group(empty=True, name="NotMMD_root")

        # 検索
        mmd_models = maya_utils.find_all_mmd_models()

        self.assertIn(model1, mmd_models)
        self.assertIn(model2, mmd_models)
        self.assertNotIn(not_mmd, mmd_models)
        self.assertEqual(len(mmd_models), 2)

    def test_get_parent_mmd_root(self):
        """親のMMDルートを取得できるか"""
        # MMDモデルの階層を作成
        root = cmds.group(empty=True, name="TestModel_root")
        cmds.addAttr(root, longName="mmd_model_name_jp", dataType="string")

        skeleton_group = cmds.group(empty=True, name="Skeleton", parent=root)
        joint = cmds.joint(name="test_joint", parent=skeleton_group)

        # ジョイントから親のMMDルートを取得
        parent_root = maya_utils.get_parent_mmd_root(joint)
        self.assertEqual(parent_root, root)

        # ルートノード自体を渡した場合
        parent_root2 = maya_utils.get_parent_mmd_root(root)
        self.assertEqual(parent_root2, root)

        # MMDモデルではないオブジェクト
        cube = cmds.polyCube(name="test_cube")[0]
        parent_root3 = maya_utils.get_parent_mmd_root(cube)
        self.assertIsNone(parent_root3)

    def test_get_mmd_model_display_name(self):
        """MMDモデルの表示名を取得できるか"""
        # 日本語名があるモデル
        model1 = cmds.group(empty=True, name="Model1_root")
        cmds.addAttr(model1, longName="mmd_model_name_jp", dataType="string")
        cmds.setAttr(f"{model1}.mmd_model_name_jp", "初音ミク", type="string")

        display_name1 = maya_utils.get_mmd_model_display_name(model1)
        self.assertEqual(display_name1, "初音ミク")

        # 英語名のみのモデル
        model2 = cmds.group(empty=True, name="Model2_root")
        cmds.addAttr(model2, longName="mmd_model_name_en", dataType="string")
        cmds.setAttr(f"{model2}.mmd_model_name_en", "Hatsune Miku", type="string")

        display_name2 = maya_utils.get_mmd_model_display_name(model2)
        self.assertEqual(display_name2, "Hatsune Miku")

        # 名前属性がないモデル
        model3 = cmds.group(empty=True, name="Model3_root")
        display_name3 = maya_utils.get_mmd_model_display_name(model3)
        self.assertEqual(display_name3, "Model3")


if __name__ == "__main__":
    unittest.main()
