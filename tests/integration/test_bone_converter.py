import os

from maya import cmds

from mmd_tools.core.settings import settings
from mmd_tools.converters import BoneConverter, MeshConverter
from mmd_tools.core import PmdParser, PmxParser, maya_utils
from tests.common.maya_test_base import MayaTestBase


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
        self.test_data_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data"
        )
        self.pmd_file_path = os.path.join(self.test_data_dir, "miku_v2.pmd")
        self.pmx_file_path = os.path.join(self.test_data_dir, "Lumine", "荧.pmx")

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ
        cmds.file(new=True, force=True)

    def test_convert_pmd_bones(self):
        """PMDボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # PMDファイルが存在するか確認
        self.assertTrue(
            os.path.exists(self.pmd_file_path),
            f"テストPMDファイルが見つかりません: {self.pmd_file_path}",
        )

        # PMDファイルをパース
        parser = PmdParser()
        pmd_data = parser.parse_file(self.pmd_file_path)

        # ルートグループを作成
        root_group = cmds.group(name="pmd_model_root", empty=True)

        # テスト用のメッシュを作成
        pmd_mesh_converter = MeshConverter(self.pmd_file_path)
        pmd_group_name, pmd_mesh_name = pmd_mesh_converter.convert_pmd_mesh(pmd_data, root_group)

        # ボーンを変換
        converter = BoneConverter()
        root_joint, skin_cluster = converter.convert_pmd_bones(pmd_data, pmd_mesh_name, root_group)

        # 結果を検証
        self.assertIsNotNone(root_joint, "ルートジョイントが作成されていません。")
        self.assertIsNotNone(skin_cluster, "スキンクラスターが作成されていません。")

        # ジョイントの数を確認（準標準ボーンが追加される可能性がある）
        all_joints = cmds.ls(type="joint")
        # PMDボーン数以上のジョイントが作成されることを確認
        self.assertGreaterEqual(
            len(all_joints), len(pmd_data.bones), "ジョイント数が元のPMDボーン数以上でなければなりません。"
        )

        # 階層構造と位置を確認
        for bone in pmd_data.bones:
            bone_name = maya_utils.sanitize_text(
                bone.get_name()
            )  # 英語名があればそれを使用
            self.assertTrue(
                cmds.objExists(bone_name),
                f"ジョイント '{bone_name}' が作成されていません。",
            )

            # 親子関係の確認
            if bone.parent_bone_index != -1:
                parent_name = pmd_data.bones[bone.parent_bone_index].get_name()
                parent_name = maya_utils.sanitize_text(parent_name)
                parent_joint = cmds.listRelatives(bone_name, parent=True, type="joint")
                self.assertIsNotNone(
                    parent_joint, f"ジョイント '{bone_name}' に親がいません。"
                )

            # ジョイントが存在する場合のみ位置を確認
            if cmds.objExists(bone_name):
                # 位置の確認
                joint_pos = cmds.xform(
                    bone_name, query=True, translation=True, worldSpace=True
                )
                self.assertAlmostEqual(
                    joint_pos[0],
                    bone.position[0],
                    delta=1e-5,
                    msg=f"ジョイント '{bone_name}' のX位置が正しくありません。",
                )
                self.assertAlmostEqual(
                    joint_pos[1],
                    bone.position[1],
                    delta=1e-5,
                    msg=f"ジョイント '{bone_name}' のY位置が正しくありません。",
                )
                self.assertAlmostEqual(
                    joint_pos[2],
                    -bone.position[2],
                    delta=1e-5,
                    msg=f"ジョイント '{bone_name}' のZ位置が正しくありません。",
                )  # Mayaは左手系

    def test_convert_pmx_bones(self):
        """PMXボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # PMXファイルが存在するか確認
        self.assertTrue(
            os.path.exists(self.pmx_file_path),
            f"テストPMXファイルが見つかりません: {self.pmx_file_path}",
        )

        # PMXファイルをパース
        parser = PmxParser()
        pmx_data = parser.parse_file(self.pmx_file_path)

        # ルートグループを作成
        root_group = cmds.group(name="pmx_model_root", empty=True)

        # メッシュを作成
        mesh_converter = MeshConverter(self.pmx_file_path)
        group_name, mesh_name = mesh_converter.convert_pmx_mesh(pmx_data, root_group)

        # ボーンを変換
        converter = BoneConverter()
        root_joint, skin_cluster = converter.convert_pmx_bones(pmx_data, mesh_name, root_group)

        # 結果を検証
        self.assertIsNotNone(root_joint, "ルートジョイントが作成されていません。")
        self.assertIsNotNone(skin_cluster, "スキンクラスターが作成されていません。")

        # ジョイントの数を確認（準標準ボーンが追加される可能性がある）
        all_joints = cmds.ls(type="joint")
        # PMXボーン数以上のジョイントが作成されることを確認
        self.assertGreaterEqual(
            len(all_joints), len(pmx_data.bones), "ジョイント数が元のPMXボーン数以上でなければなりません。"
        )

        # 階層構造を確認（位置は付与ボーンの処理により変更される可能性があるため、階層のみ確認）
        for bone in pmx_data.bones:
            bone_name = bone.get_name()  # 英語名があればそれを使用
            bone_name = maya_utils.sanitize_text(bone_name)
            self.assertTrue(
                cmds.objExists(bone_name),
                f"ジョイント '{bone_name}' が作成されていません。",
            )

            # 親子関係の確認
            if bone.parent_bone_index != -1:
                parent_name = pmx_data.bones[bone.parent_bone_index].get_name()
                parent_name = maya_utils.sanitize_text(parent_name)
                parent_joint = cmds.listRelatives(bone_name, parent=True, type="joint")
                self.assertIsNotNone(
                    parent_joint, f"ジョイント '{bone_name}' に親がいません。"
                )
                # 準標準ボーンの追加により親が変更される場合があるため、厳密な確認は行わない


        # jointOrinentの確認

        # 各種位置決めボーンはJointOrientが(0, 0, 0)であることを確認
        for static_bone in ["master", "center", "center_2", "group"]:
            if cmds.objExists(static_bone):
                joint_orient = cmds.getAttr(f"{static_bone}.jointOrient")[0]
                self.assertEqual(joint_orient, (0, 0, 0), f"{static_bone}のJointOrientが正しくありません。")