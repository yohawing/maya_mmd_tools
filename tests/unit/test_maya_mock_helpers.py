"""
Maya mock helpersの動作確認テスト
"""

import unittest
import sys
from tests.common.maya_mock import MayaMockSetup
from tests.common.maya_mock_helpers import (
    MayaMockFactory, AnimationMockHelper, create_mock_scene
)


class TestMayaMockHelpers(unittest.TestCase):
    """Maya mock helpersの動作を確認するテストクラス"""
    
    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        cls.maya, cls.cmds, cls.om = MayaMockSetup.setup_maya_mocks()
    
    @classmethod
    def tearDownClass(cls):
        """テストクラスのクリーンアップ"""
        MayaMockSetup.teardown_maya_mocks()
    
    def setUp(self):
        """各テストのセットアップ"""
        self.cmds.reset()
    
    def test_create_mmd_bone_hierarchy(self):
        """MMDボーン階層作成のテスト"""
        bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
        
        # 主要なボーンが作成されていることを確認
        self.assertIn("センター", bone_mapping)
        self.assertIn("上半身", bone_mapping)
        self.assertIn("頭", bone_mapping)
        self.assertIn("左手首", bone_mapping)
        self.assertIn("右足首", bone_mapping)
        
        # Mayaジョイントが存在することを確認
        for mmd_name, maya_name in bone_mapping.items():
            self.assertTrue(self.cmds.objExists(maya_name))
            self.assertEqual(self.cmds.nodeType(maya_name), "joint")
        
        # 親子関係を確認
        center = bone_mapping["センター"]
        upper_body = bone_mapping["上半身"]
        self.assertEqual(self.cmds._scene_objects[upper_body]["parent"], center)
    
    def test_create_mmd_ik_setup(self):
        """MMD IKセットアップのテスト"""
        # まずボーン階層を作成
        bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
        
        # IKセットアップを作成
        ik_info = MayaMockFactory.create_mmd_ik_setup(bone_mapping)
        
        # 左足IKが作成されていることを確認
        self.assertIn("left_leg_ik", ik_info)
        left_ik = ik_info["left_leg_ik"]
        self.assertTrue(self.cmds.objExists(left_ik["handle"]))
        self.assertTrue(self.cmds.objExists(left_ik["controller"]))
        
        # 右足IKが作成されていることを確認
        self.assertIn("right_leg_ik", ik_info)
        right_ik = ik_info["right_leg_ik"]
        self.assertTrue(self.cmds.objExists(right_ik["handle"]))
        self.assertTrue(self.cmds.objExists(right_ik["controller"]))
    
    def test_create_mmd_mesh(self):
        """MMDメッシュ作成のテスト"""
        mesh_info = MayaMockFactory.create_mmd_mesh("test_model")
        
        # メッシュが作成されていることを確認
        self.assertTrue(self.cmds.objExists(mesh_info["mesh"]))
        self.assertTrue(self.cmds.objExists(mesh_info["shape"]))
        self.assertTrue(self.cmds.objExists(mesh_info["skin_cluster"]))
        
        # 頂点とフェースが設定されていることを確認
        self.assertEqual(len(mesh_info["vertices"]), 8)
        self.assertEqual(len(mesh_info["faces"]), 6)
        
        # メッシュデータが正しく設定されていることを確認
        mesh_obj = self.cmds._scene_objects[mesh_info["mesh"]]
        self.assertEqual(len(mesh_obj["vertices"]), 8)
        self.assertEqual(len(mesh_obj["faces"]), 6)
        self.assertEqual(len(mesh_obj["uvs"]), 24)  # 6面 × 4頂点
        self.assertEqual(len(mesh_obj["normals"]), 8)
    
    def test_create_material(self):
        """マテリアル作成のテスト"""
        # テクスチャなしのマテリアル
        mat_info = MayaMockFactory.create_material("test_mat", color=(1.0, 0.0, 0.0))
        
        self.assertTrue(self.cmds.objExists(mat_info["shader"]))
        self.assertTrue(self.cmds.objExists(mat_info["shading_group"]))
        self.assertEqual(mat_info["color"], (1.0, 0.0, 0.0))
        
        # テクスチャありのマテリアル
        tex_mat_info = MayaMockFactory.create_material("tex_mat", 
                                                      color=(0.5, 0.5, 0.5),
                                                      texture="test.png")
        
        self.assertTrue(self.cmds.objExists(tex_mat_info["texture"]))
        self.assertTrue(self.cmds.objExists(tex_mat_info["place2d"]))
        
        # ファイルノードのテクスチャパスを確認
        file_node = self.cmds._scene_objects[tex_mat_info["texture"]]
        self.assertEqual(file_node["fileTextureName"], "test.png")
    
    def test_create_blend_shape(self):
        """ブレンドシェイプ作成のテスト"""
        # メッシュを作成
        mesh_info = MayaMockFactory.create_mmd_mesh("test_mesh")
        
        # ブレンドシェイプを作成
        vertex_deltas = [(0, (0.1, 0.0, 0.0)), (1, (-0.1, 0.0, 0.0))]
        blend_shape = MayaMockFactory.create_blend_shape(mesh_info["mesh"], 
                                                        "smile", 
                                                        vertex_deltas)
        
        # ブレンドシェイプが作成されていることを確認
        self.assertTrue(self.cmds.objExists(blend_shape))
        bs_obj = self.cmds._scene_objects[blend_shape]
        self.assertEqual(bs_obj["type"], "blendShape")
        self.assertIn("smile", bs_obj["targets"])
        
        # ターゲットの頂点デルタを確認
        smile_target = bs_obj["targets"]["smile"]
        self.assertEqual(len(smile_target["vertex_deltas"]), 2)
        self.assertEqual(smile_target["weight"], 0.0)
    
    def test_create_animation_curve(self):
        """アニメーションカーブ作成のテスト"""
        # ジョイントを作成
        joint = self.cmds.joint(name="test_joint")
        
        # アニメーションカーブを作成
        keys = [(0, 0.0), (10, 5.0), (20, 0.0)]
        anim_curve = AnimationMockHelper.create_animation_curve(joint, "translateX", keys)
        
        # アニメーションカーブが作成されていることを確認
        self.assertTrue(self.cmds.objExists(anim_curve))
        curve_obj = self.cmds._scene_objects[anim_curve]
        self.assertEqual(curve_obj["type"], "animCurveTU")
        self.assertEqual(curve_obj["keys"], keys)
        
        # キーフレームが設定されていることを確認
        self.assertIn(joint, self.cmds._keyframes)
        self.assertIn("translateX", self.cmds._keyframes[joint])
        self.assertEqual(len(self.cmds._keyframes[joint]["translateX"]), 3)
    
    def test_create_vmd_animation(self):
        """VMDアニメーション作成のテスト"""
        # ボーン階層を作成
        bone_mapping = MayaMockFactory.create_mmd_bone_hierarchy()
        
        # VMDフレームデータを準備
        bone_frames = {
            "センター": [
                {"frame_number": 0, "position": (0, 0, 0), "rotation": (0, 0, 0)},
                {"frame_number": 30, "position": (0, 2, 0), "rotation": (0, 0.1, 0)},
                {"frame_number": 60, "position": (0, 0, 0), "rotation": (0, 0, 0)},
            ],
            "左腕": [
                {"frame_number": 0, "position": (0, 0, 0), "rotation": (0, 0, 0)},
                {"frame_number": 15, "position": (0, 0, 0), "rotation": (0, 0, -0.5)},
                {"frame_number": 30, "position": (0, 0, 0), "rotation": (0, 0, 0)},
            ],
        }
        
        # アニメーションを作成
        created_curves = AnimationMockHelper.create_vmd_animation(bone_mapping, bone_frames)
        
        # センターボーンのアニメーションを確認
        center_joint = bone_mapping["センター"]
        self.assertIn(center_joint, created_curves)
        self.assertEqual(len(created_curves[center_joint]), 6)  # 6つのアトリビュート
        
        # 左腕のアニメーションを確認
        left_arm_joint = bone_mapping["左腕"]
        self.assertIn(left_arm_joint, created_curves)
        self.assertEqual(len(created_curves[left_arm_joint]), 6)
    
    def test_create_mock_scene(self):
        """完全なモックシーン作成のテスト"""
        scene_info = create_mock_scene()
        
        # すべての要素が作成されていることを確認
        self.assertIn("bone_mapping", scene_info)
        self.assertIn("ik_info", scene_info)
        self.assertIn("mesh_info", scene_info)
        self.assertIn("material_info", scene_info)
        self.assertIn("blend_shape", scene_info)
        
        # ボーンが存在することを確認
        for mmd_name, maya_name in scene_info["bone_mapping"].items():
            self.assertTrue(self.cmds.objExists(maya_name))
        
        # IKが存在することを確認
        for ik_name, ik_data in scene_info["ik_info"].items():
            self.assertTrue(self.cmds.objExists(ik_data["handle"]))
            self.assertTrue(self.cmds.objExists(ik_data["controller"]))
        
        # メッシュが存在することを確認
        self.assertTrue(self.cmds.objExists(scene_info["mesh_info"]["mesh"]))
        
        # マテリアルが存在することを確認
        self.assertTrue(self.cmds.objExists(scene_info["material_info"]["shader"]))
        
        # ブレンドシェイプが存在することを確認
        self.assertTrue(self.cmds.objExists(scene_info["blend_shape"]))


if __name__ == "__main__":
    unittest.main()