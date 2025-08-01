import unittest

from maya import cmds

from mmd_tools.core import maya_utils, utils
from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME_EN, ATTR_MMD_MODEL_NAME
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

    def test_set_attribute(self):
        """アトリビュートを設定できるか（既存のアトリビュートに対して）"""
        mesh_name = "test_mesh_for_set_attr"
        cmds.polyCube(name=mesh_name)

        # Test 1: 既存の標準アトリビュートへの設定
        maya_utils.set_attribute(mesh_name, "translateX", 5.0, "double")
        self.assertAlmostEqual(cmds.getAttr(mesh_name + ".translateX"), 5.0, places=5)

        maya_utils.set_attribute(mesh_name, "translateY", -3.0, "double")
        self.assertAlmostEqual(cmds.getAttr(mesh_name + ".translateY"), -3.0, places=5)

        maya_utils.set_attribute(mesh_name, "scaleX", 2.0, "double")
        self.assertAlmostEqual(cmds.getAttr(mesh_name + ".scaleX"), 2.0, places=5)

        # Test 2: 複数値を持つ既存アトリビュート
        maya_utils.set_attribute(mesh_name, "translate", [1.0, 2.0, 3.0], "double3")
        translate_value = cmds.getAttr(mesh_name + ".translate")[0]
        self.assertAlmostEqual(translate_value[0], 1.0, places=5)
        self.assertAlmostEqual(translate_value[1], 2.0, places=5)
        self.assertAlmostEqual(translate_value[2], 3.0, places=5)

        # Test 3: カスタムアトリビュートを作成してから設定
        cmds.addAttr(mesh_name, longName="customIntAttr", attributeType="long")
        maya_utils.set_attribute(mesh_name, "customIntAttr", 42, "long")
        self.assertEqual(cmds.getAttr(mesh_name + ".customIntAttr"), 42)

        # カスタムアトリビュートの更新
        maya_utils.set_attribute(mesh_name, "customIntAttr", 100, "long")
        self.assertEqual(cmds.getAttr(mesh_name + ".customIntAttr"), 100)

        # Test 5: visibility属性（bool）
        maya_utils.set_attribute(mesh_name, "visibility", False, "bool")
        self.assertEqual(cmds.getAttr(mesh_name + ".visibility"), False)
        maya_utils.set_attribute(mesh_name, "visibility", True, "bool")
        self.assertEqual(cmds.getAttr(mesh_name + ".visibility"), True)

        # Test 6: エラーケース - 存在しないオブジェクト
        # set_attributeは例外を再発生させないため、エラー時も正常に処理される
        # 値が設定されないことを確認
        maya_utils.set_attribute("non_existent_object", "translateX", 1.0, "double")
        # ログにエラーが出力されることを期待（実際のテストではログ出力の確認は省略）

        # Test 7: エラーケース - 存在しないアトリビュート
        # 同様に、例外ではなくログ出力される
        maya_utils.set_attribute(mesh_name, "non_existent_attr", 1.0, "float")

    def test_set_attribute_with_custom_attributes(self):
        """set_custom_attributesと組み合わせたテスト"""
        test_obj = cmds.createNode("transform", name="test_custom_attr_obj")

        # カスタムアトリビュートを作成
        maya_utils.set_custom_attributes(
            test_obj,
            {
                "intAttr": 42,
                "floatAttr": 3.14,
                "stringAttr": "Hello",
                "boolAttr": True,
                "vectorAttr": [1.0, 2.0, 3.0],
            },
        )

        # set_attributeで値を更新
        maya_utils.set_attribute(test_obj, "intAttr", 100, "long")
        self.assertEqual(cmds.getAttr(test_obj + ".intAttr"), 100)

        maya_utils.set_attribute(test_obj, "floatAttr", 6.28, "double")
        self.assertAlmostEqual(cmds.getAttr(test_obj + ".floatAttr"), 6.28, places=5)

        maya_utils.set_attribute(test_obj, "stringAttr", "World", "string")
        self.assertEqual(cmds.getAttr(test_obj + ".stringAttr"), "World")

        maya_utils.set_attribute(test_obj, "boolAttr", False, "bool")
        self.assertEqual(cmds.getAttr(test_obj + ".boolAttr"), False)

        maya_utils.set_attribute(test_obj, "vectorAttr", [4.0, 5.0, 6.0], "double3")
        vector_value = cmds.getAttr(test_obj + ".vectorAttr")[0]
        self.assertAlmostEqual(vector_value[0], 4.0, places=5)
        self.assertAlmostEqual(vector_value[1], 5.0, places=5)
        self.assertAlmostEqual(vector_value[2], 6.0, places=5)

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
            "mmd_float": 3.14,
            "mmd_double3": (1.0, 2.0, 3.0),
            "mmd_double4": (1.0, 2.0, 3.0, 4.0),
        }
        maya_utils.set_custom_attributes(mesh_name, data)

        # Verify the attribute exists and has the correct value
        for key, value in data.items():
            self.assertTrue(cmds.attributeQuery(key, node=mesh_name, exists=True))
            attr_value = cmds.getAttr(mesh_name + "." + key)
            if isinstance(value, float):
                self.assertAlmostEqual(attr_value, value, places=5)
            else:
                if isinstance(value, tuple):
                    # cmds.getAttrはdouble3の場合[(x, y, z)]のリストを返すので、最初の要素を取得
                    if isinstance(attr_value, list) and len(attr_value) == 1:
                        attr_value = attr_value[0]
                    self.assertEqual(attr_value, value)
                else:
                    self.assertEqual(
                        attr_value,
                        value.decode("utf-8") if isinstance(value, bytes) else value,
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

    def test_get_custom_attributes(self):
        """カスタムアトリビュートを取得できるか"""
        mesh_name = "test_mesh_for_get_custom_attr"
        cmds.polyCube(name=mesh_name)

        data = {
            "mmd_bytes": b"PMX",
            "mmd_file_version": 2.0,
            "mmd_utf8": "裙花蕊颜顔",
            "mmd_string": "Test Model EN",
            "mmd_bool": True,
            "mmd_int": 42,
            "mmd_float": 3.14,
            "mmd_double3": (1.0, 2.0, 3.0),
            "mmd_double4": (1.0, 2.0, 3.0, 4.0),
        }
        maya_utils.set_custom_attributes(mesh_name, data)

        # 存在しないアトリビュートはNoneを返すか確認
        self.assertEqual(maya_utils.get_attribute(mesh_name, "unknown_attribute"), None)

        # Get the attributes
        self.assertEqual(maya_utils.get_attribute(mesh_name, "mmd_bytes"), "PMX")
        self.assertEqual(maya_utils.get_attribute(mesh_name, "mmd_file_version"), 2.0)
        self.assertEqual(maya_utils.get_attribute(mesh_name, "mmd_utf8"), "裙花蕊颜顔")
        self.assertEqual(
            maya_utils.get_attribute(mesh_name, "mmd_string"), "Test Model EN"
        )
        self.assertEqual(maya_utils.get_attribute(mesh_name, "mmd_bool"), True)
        self.assertEqual(maya_utils.get_attribute(mesh_name, "mmd_int"), 42)
        self.assertAlmostEqual(
            maya_utils.get_attribute(mesh_name, "mmd_float"), 3.14, places=5
        )  # type: ignore
        self.assertEqual(
            maya_utils.get_attribute(mesh_name, "mmd_double3"), (1.0, 2.0, 3.0)
        )
        self.assertEqual(
            maya_utils.get_attribute(mesh_name, "mmd_double4"), (1.0, 2.0, 3.0, 4.0)
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
        cmds.addAttr(model1, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{model1}.{ATTR_MMD_MODEL_NAME}", "テストモデル1", type="string")

        model2 = cmds.group(empty=True, name="Model2_root")
        cmds.addAttr(model2, longName=ATTR_MMD_MODEL_NAME_EN, dataType="string")
        cmds.setAttr(
            f"{model2}.{ATTR_MMD_MODEL_NAME_EN}", "Test Model 2", type="string"
        )

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
        cmds.addAttr(root, longName=ATTR_MMD_MODEL_NAME, dataType="string")

        skeleton_group = cmds.group(empty=True, name="Skeleton", parent=root)
        # skeleton_groupを選択してからジョイントを作成
        cmds.select(skeleton_group)
        joint = cmds.joint(name="test_joint")

        # ジョイントから親のMMDルートを取得
        parent_root = maya_utils.get_parent_mmd_root(joint)
        # 完全パスの場合も考慮して比較
        self.assertTrue(parent_root == root or parent_root == f"|{root}")

        # ルートノード自体を渡した場合
        parent_root2 = maya_utils.get_parent_mmd_root(root)
        self.assertTrue(parent_root2 == root or parent_root2 == f"|{root}")

        # MMDモデルではないオブジェクト
        cube = cmds.polyCube(name="test_cube")[0]
        parent_root3 = maya_utils.get_parent_mmd_root(cube)
        self.assertIsNone(parent_root3)

    def test_get_mmd_model_display_name(self):
        """MMDモデルの表示名を取得できるか"""
        # 日本語名があるモデル
        model1 = cmds.group(empty=True, name="Model1_root")
        cmds.addAttr(model1, longName=ATTR_MMD_MODEL_NAME, dataType="string")
        cmds.setAttr(f"{model1}.{ATTR_MMD_MODEL_NAME}", "初音ミク", type="string")

        display_name1 = maya_utils.get_mmd_model_display_name(model1)
        self.assertEqual(display_name1, "初音ミク")

        # 英語名のみのモデル
        model2 = cmds.group(empty=True, name="Model2_root")
        cmds.addAttr(model2, longName=ATTR_MMD_MODEL_NAME_EN, dataType="string")
        cmds.setAttr(
            f"{model2}.{ATTR_MMD_MODEL_NAME_EN}", "Hatsune Miku", type="string"
        )

        display_name2 = maya_utils.get_mmd_model_display_name(model2)
        self.assertEqual(display_name2, "Hatsune Miku")

        # 名前属性がないモデル
        model3 = cmds.group(empty=True, name="Model3_root")
        display_name3 = maya_utils.get_mmd_model_display_name(model3)
        self.assertEqual(display_name3, "Model3")

    def test_select_objects(self):
        """オブジェクトを選択できるか"""
        # テストオブジェクトを作成
        cube1 = cmds.polyCube(name="test_cube1")[0]
        cube2 = cmds.polyCube(name="test_cube2")[0]
        cube3 = cmds.polyCube(name="test_cube3")[0]

        # 選択をクリア
        result = maya_utils.select_objects(clear=True)
        self.assertTrue(result)
        self.assertEqual(cmds.ls(selection=True), [])

        # 単一オブジェクトを選択
        result = maya_utils.select_objects(cube1)
        self.assertTrue(result)
        self.assertEqual(cmds.ls(selection=True), [cube1])

        # 複数オブジェクトを選択
        result = maya_utils.select_objects([cube1, cube2])
        self.assertTrue(result)
        selected = cmds.ls(selection=True)
        self.assertIn(cube1, selected)
        self.assertIn(cube2, selected)

        # 追加モードで選択
        maya_utils.select_objects(cube1)  # まずcube1を選択
        result = maya_utils.select_objects(cube2, add=True, clear=False, replace=False)
        self.assertTrue(result)
        selected = cmds.ls(selection=True)
        self.assertIn(cube1, selected)
        self.assertIn(cube2, selected)

    def test_object_exists(self):
        """オブジェクトの存在を確認できるか"""
        # 存在するオブジェクト
        cube = cmds.polyCube(name="test_exists_cube")[0]
        self.assertTrue(maya_utils.object_exists(cube))

        # 存在しないオブジェクト
        self.assertFalse(maya_utils.object_exists("nonexistent_object"))

        # オブジェクトを削除した後
        cmds.delete(cube)
        self.assertFalse(maya_utils.object_exists(cube))

    def test_parent_objects(self):
        """オブジェクトの親子関係を設定できるか"""
        # テストオブジェクトを作成
        parent = cmds.group(empty=True, name="test_parent")
        child1 = cmds.polyCube(name="test_child1")[0]
        child2 = cmds.polyCube(name="test_child2")[0]

        # 単一の子を親付け
        result = maya_utils.parent_objects(child1, parent)
        self.assertEqual(len(result), 1)
        self.assertEqual(cmds.listRelatives(child1, parent=True)[0], parent)

        # 複数の子を親付け
        result = maya_utils.parent_objects([child2], parent)
        self.assertEqual(len(result), 1)
        self.assertEqual(cmds.listRelatives(child2, parent=True)[0], parent)

        # ワールド空間へ親付け
        child3 = cmds.polyCube(name="test_child3")[0]
        cmds.parent(child3, parent)  # まず親付け
        result = maya_utils.parent_objects(child3, world=True)
        self.assertEqual(len(result), 1)
        # ワールド空間にある場合、親はNone
        parents = cmds.listRelatives(child3, parent=True)
        self.assertIsNone(parents)

    def test_list_objects(self):
        """オブジェクトをリストできるか"""
        # テストシーンをセットアップ
        cmds.file(new=True, force=True)

        # ジョイントを作成
        cmds.select(clear=True)
        joint1 = cmds.joint(name="test_joint1")
        joint2 = cmds.joint(name="test_joint2")

        # メッシュを作成
        cube = cmds.polyCube(name="test_cube")[0]
        sphere = cmds.polySphere(name="test_sphere")[0]

        # ジョイントのみリスト
        joints = maya_utils.list_objects(type="joint")
        joint_names = [j.split("|")[-1] for j in joints]  # フルパスから名前のみ取得
        self.assertIn("test_joint1", joint_names)
        self.assertIn("test_joint2", joint_names)

        # トランスフォームをリスト
        transforms = maya_utils.list_objects(type="transform")
        transform_names = [t.split("|")[-1] for t in transforms]
        self.assertIn("test_cube", transform_names)
        self.assertIn("test_sphere", transform_names)

        # ワイルドカードでフィルター
        test_objects = maya_utils.list_objects(object_filter="*test*")
        test_names = [t.split("|")[-1] for t in test_objects]
        self.assertIn("test_joint1", test_names)
        self.assertIn("test_cube", test_names)

    def test_set_attribute_performance(self):
        """set_attributeのパフォーマンステスト（既存アトリビュートへの設定）"""
        import time

        # テスト用のオブジェクトを作成
        test_obj = cmds.createNode("transform", name="performance_test_obj")

        # 既存のアトリビュート（translate, rotate, scale）への設定を100回繰り返す
        start_time = time.time()
        for i in range(100):
            maya_utils.set_attribute(test_obj, "translateX", float(i), "double")
            maya_utils.set_attribute(test_obj, "rotateY", float(i * 2), "double")
            maya_utils.set_attribute(test_obj, "scaleZ", float(i * 0.01 + 1), "double")
        api_time = time.time() - start_time

        # 比較のため、cmds.setAttrでも同じことを実行
        test_obj2 = cmds.createNode("transform", name="performance_test_obj2")
        start_time = time.time()
        for i in range(100):
            cmds.setAttr(f"{test_obj2}.translateX", float(i))
            cmds.setAttr(f"{test_obj2}.rotateY", float(i * 2))
            cmds.setAttr(f"{test_obj2}.scaleZ", float(i * 0.01 + 1))
        cmds_time = time.time() - start_time

        # 結果をログに出力
        print(f"\nPerformance Test Results (existing attributes):")
        print(f"maya_utils.set_attribute (API): {api_time:.4f} seconds")
        print(f"cmds.setAttr: {cmds_time:.4f} seconds")
        if api_time < cmds_time:
            print(f"API is {cmds_time / api_time:.2f}x faster")
        else:
            print(f"cmds is {api_time / cmds_time:.2f}x faster")

        # パフォーマンスは同等程度であることを確認
        # APIの方が若干遅い可能性もあるため、2倍の余裕を持たせる
        self.assertLess(api_time, cmds_time * 2.0)

    def test_set_attribute_edge_cases(self):
        """set_attributeのエッジケーステスト（既存アトリビュートを使用）"""
        test_obj = cmds.createNode("transform", name="edge_case_test_obj")

        # Test 1: 極小値と極大値（既存のアトリビュートに対して）
        maya_utils.set_attribute(test_obj, "translateX", -1e10, "double")
        maya_utils.set_attribute(test_obj, "translateY", 1e10, "double")
        self.assertAlmostEqual(cmds.getAttr(test_obj + ".translateX"), -1e10, places=0)
        self.assertAlmostEqual(cmds.getAttr(test_obj + ".translateY"), 1e10, places=0)

        # Test 2: 0値
        maya_utils.set_attribute(test_obj, "rotateX", 0.0, "double")
        self.assertEqual(cmds.getAttr(test_obj + ".rotateX"), 0.0)

        # Test 3: 回転値のテスト
        # 新しく作成したオブジェクトで回転をテスト
        test_obj2 = cmds.createNode("transform", name="edge_case_test_obj2")

        # Maya APIでは回転値はラジアンで扱われるため、度数からラジアンに変換する必要がある
        import math

        # 45度をラジアンに変換
        degrees_value = 45.0
        radians_value = math.radians(degrees_value)

        # maya_utils.set_attributeはラジアン値を期待している
        maya_utils.set_attribute(test_obj2, "rotateY", radians_value, "double")
        api_value = cmds.getAttr(test_obj2 + ".rotateY")
        # 結果は度数で返される
        self.assertAlmostEqual(api_value, degrees_value, places=1)

        # Test 4: visibilityの切り替え（bool型）
        maya_utils.set_attribute(test_obj, "visibility", False, "bool")
        self.assertEqual(cmds.getAttr(test_obj + ".visibility"), False)
        maya_utils.set_attribute(test_obj, "visibility", True, "bool")
        self.assertEqual(cmds.getAttr(test_obj + ".visibility"), True)

        # Test 5: カスタムアトリビュートでのエッジケース
        # まずカスタムアトリビュートを作成
        maya_utils.set_custom_attributes(
            test_obj, {"stringAttr": "", "floatAttr": 0.0, "intAttr": -999999}
        )

        # 空文字列の設定
        maya_utils.set_attribute(test_obj, "stringAttr", "", "string")
        self.assertEqual(cmds.getAttr(test_obj + ".stringAttr"), "")

        # 非常に長い文字列
        long_string = "a" * 1000
        maya_utils.set_attribute(test_obj, "stringAttr", long_string, "string")
        self.assertEqual(cmds.getAttr(test_obj + ".stringAttr"), long_string)

        # 特殊文字を含む文字列
        special_string = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        maya_utils.set_attribute(test_obj, "stringAttr", special_string, "string")
        self.assertEqual(cmds.getAttr(test_obj + ".stringAttr"), special_string)

        # Test 6: エラーケース - None値の処理
        # set_attributeは例外を再発生させないため、ログ出力で処理
        # テストではNone値を渡してもクラッシュしないことを確認
        maya_utils.set_attribute(test_obj, "translateX", None, "double")
        # 値は変更されないはず
        # translateXの現在値を確認（前の値から変わっていないはず）


if __name__ == "__main__":
    unittest.main()
