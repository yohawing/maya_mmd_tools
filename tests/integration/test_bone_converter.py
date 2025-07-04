import os
import maya.cmds as cmds
from mmd_tools import settings
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.core import PmdParser, PmxParser
from mmd_tools.converters import BoneConverter

class TestBoneConverter(MayaTestBase):
    """
    BoneConverterクラスの統合テスト。
    MMDのボーンデータをMayaのジョイントに変換し、スキニングが正しく適用されるかを確認する。
    """

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備
        cmds.file(new=True, force=True)
        
        # テストデータのパスを設定
        self.test_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
        self.pmd_file_path = os.path.join(self.test_data_dir, 'miku_v2.pmd')
        self.pmx_file_path = os.path.join(self.test_data_dir, 'Lumine', '荧.pmx')

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ
        cmds.file(new=True, force=True)

    def test_convert_pmd_bones(self):
        """PMDボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # PMDファイルが存在するか確認
        self.assertTrue(os.path.exists(self.pmd_file_path), 
                       f"テストPMDファイルが見つかりません: {self.pmd_file_path}")
        
        # PMDファイルをパース
        parser = PmdParser()
        pmd_data = parser.parse_file(self.pmd_file_path)

        # テスト用のメッシュを作成
        mesh_name = "test_mesh"
        cmds.polyPlane(name=mesh_name, width=10, height=10, subdivisionsX=1, subdivisionsY=1)

        # ボーンを変換
        converter = BoneConverter()
        root_joint, skin_cluster = converter.convert_pmd_bones(pmd_data, mesh_name)

        # 結果を検証
        self.assertIsNotNone(root_joint, "ルートジョイントが作成されていません。")
        self.assertIsNotNone(skin_cluster, "スキンクラスターが作成されていません。")

        # ジョイントの数を確認
        all_joints = cmds.ls(type="joint")
        self.assertEqual(len(all_joints), len(pmd_data.bones), "ジョイントの数が一致しません。")

        # 階層構造と位置を確認
        for bone in pmd_data.bones:
            self.assertTrue(cmds.objExists(bone.name), f"ジョイント '{bone.name}' が作成されていません。")
            
            # 親子関係の確認
            if bone.parent_index != -1:
                parent_name = pmd_data.bones[bone.parent_index].name
                parent_joint = cmds.listRelatives(bone.name, parent=True, type="joint")
                self.assertIsNotNone(parent_joint, f"ジョイント '{bone.name}' に親がいません。")
                self.assertEqual(parent_joint[0], parent_name, f"ジョイント '{bone.name}' の親が正しくありません。")

            # 位置の確認
            joint_pos = cmds.xform(bone.name, query=True, translation=True, worldSpace=True)
            self.assertAlmostEqual(joint_pos[0], bone.head_pos[0], delta=1e-5)
            self.assertAlmostEqual(joint_pos[1], bone.head_pos[1], delta=1e-5)
            self.assertAlmostEqual(joint_pos[2], -bone.head_pos[2], delta=1e-5) # Mayaは左手系

    def test_convert_pmx_bones(self):
        """PMXボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # PMXファイルが存在するか確認
        self.assertTrue(os.path.exists(self.pmx_file_path), 
                       f"テストPMXファイルが見つかりません: {self.pmx_file_path}")
        
        # PMXファイルをパース
        parser = PmxParser()
        pmx_data = parser.parse_file(self.pmx_file_path)

        # テスト用のメッシュを作成
        mesh_name = "test_mesh_pmx"
        cmds.polyPlane(name=mesh_name, width=10, height=10, subdivisionsX=1, subdivisionsY=1)

        # ボーンを変換
        converter = BoneConverter()
        root_joint, skin_cluster = converter.convert_pmx_bones(pmx_data, mesh_name)

        # 結果を検証
        self.assertIsNotNone(root_joint, "ルートジョイントが作成されていません。")
        self.assertIsNotNone(skin_cluster, "スキンクラスターが作成されていません。")

        # ジョイントの数を確認
        all_joints = cmds.ls(type="joint")
        self.assertEqual(len(all_joints), len(pmx_data.bones), "ジョイントの数が一致しません。")

        # 階層構造と位置を確認
        for bone in pmx_data.bones:
            self.assertTrue(cmds.objExists(bone.name), f"ジョイント '{bone.name}' が作成されていません。")
            
            # 親子関係の確認
            if bone.parent_index != -1:
                parent_name = pmx_data.bones[bone.parent_index].name
                parent_joint = cmds.listRelatives(bone.name, parent=True, type="joint")
                self.assertIsNotNone(parent_joint, f"ジョイント '{bone.name}' に親がいません。")
                self.assertEqual(parent_joint[0], parent_name, f"ジョイント '{bone.name}' の親が正しくありません。")

            # 位置の確認
            joint_pos = cmds.xform(bone.name, query=True, translation=True, worldSpace=True)
            self.assertAlmostEqual(joint_pos[0], bone.position[0], delta=1e-5)
            self.assertAlmostEqual(joint_pos[1], bone.position[1], delta=1e-5)
            self.assertAlmostEqual(joint_pos[2], -bone.position[2], delta=1e-5) # Mayaは左手系
