"""VmdConverterの統合テスト"""

import os
from maya import cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters import BoneConverter, MeshConverter
from mmd_tools.core import PmdParser, PmxParser, VmdParser, maya_utils
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdConverter(MayaTestBase):
    """
    VmdConverterクラスの統合テスト。
    VMDファイルのアニメーションデータをMayaに変換し、正しく適用されるかを確認する。
    """

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
        # 新規シーンを作成
        cmds.file(new=True, force=True)

        # VmdConverterのインスタンスを作成
        self.converter = VmdConverter()

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テスト後のクリーンアップ"""
        super().tearDown()
        cmds.file(new=True, force=True)
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()

    def test_convert_with_pmx_file(self):
        """PMXファイルを使用したVMD変換テスト"""
        # try:
        #     # PMXファイルを取得
        #     pmx_file_path = self.fixture_provider.get_pmx_file("Lumine.pmx")
        # except FileNotFoundError as e:
        #     self.skipTest(f"PMXファイルが見つかりません: {e}")

        pmx_data = self.fixture_provider.load_pmx_data("Lumine")
        pmx_file_path = pmx_data["file_path"]
        pmx_data = pmx_data["data"]

        # PMXモデルを読み込んでボーンを作成
        # pmx_parser = PmxParser()
        # pmx_data = pmx_parser.parse_file(pmx_file_path)

        # ルートグループを作成
        root_group = cmds.group(name="test_model_root", empty=True)

        # メッシュを作成（スキニングのため）
        mesh_converter = MeshConverter(pmx_file_path)
        group_name, mesh_name = mesh_converter.convert_pmx_mesh(pmx_data, root_group)

        # ボーンを作成
        bone_converter = BoneConverter()
        root_joint, skin_cluster = bone_converter.convert_pmx_bones(
            pmx_data, mesh_name, root_group
        )

        # VMDファイルを読み込み
        vmd_parser = VmdParser()
        vmd_data = vmd_parser.parse_file(self.fixture_provider.get_vmd_file("Lat式用"))

        # アニメーション変換
        result = self.converter.convert(vmd_data)

        # 検証
        self.assertTrue(result)

        # アニメーションが設定されたか確認
        all_joints = cmds.ls(type="joint")
        animated_joints = []
        for joint in all_joints:
            # 各属性にアニメーションカーブが接続されているか確認
            for attr in [
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            ]:
                connections = cmds.listConnections(
                    f"{joint}.{attr}", source=True, destination=False
                )
                if connections:
                    animated_joints.append(joint)
                    break

        # 少なくとも1つのジョイントがアニメーションされていることを確認
        self.assertGreater(
            len(animated_joints), 0, "アニメーションが設定されたジョイントがありません"
        )

    def test_convert_with_pmd_file(self):
        """実際のPMDファイルを使用した変換テスト"""

        # PMDモデルを読み込んでボーンを作成
        pmd_data = self.fixture_provider.load_pmd_data("Lat式ミクVer2.31_Normal")
        pmd_file_path = pmd_data["file_path"]
        pmd_data = pmd_data["data"]

        # ルートグループを作成
        root_group = cmds.group(name="test_model_root", empty=True)

        # メッシュを作成（スキニングのため）
        mesh_converter = MeshConverter(pmd_file_path)
        group_name, mesh_name = mesh_converter.convert_pmd_mesh(pmd_data, root_group)

        # ボーンを作成
        bone_converter = BoneConverter()
        root_joint, skin_cluster = bone_converter.convert_pmd_bones(
            pmd_data, mesh_name, root_group
        )

        # VMDファイルを読み込み
        vmd_parser = VmdParser()
        vmd_data = vmd_parser.parse_file(self.fixture_provider.get_vmd_file("Lat式用"))

        # アニメーション変換
        result = self.converter.convert(vmd_data)

        # 検証
        self.assertTrue(result)

        # アニメーションが設定されたか確認
        all_joints = cmds.ls(type="joint")
        animated_joints = []
        for joint in all_joints:
            # 各属性にアニメーションカーブが接続されているか確認
            for attr in [
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            ]:
                connections = cmds.listConnections(
                    f"{joint}.{attr}", source=True, destination=False
                )
                if connections:
                    animated_joints.append(joint)
                    break

        # 少なくとも1つのジョイントがアニメーションされていることを確認
        self.assertGreater(
            len(animated_joints), 0, "アニメーションが設定されたジョイントがありません"
        )

    def test_get_failed_bones(self):
        """失敗したボーン名の取得テスト"""
        # 初期状態
        self.assertEqual(len(self.converter.get_failed_bones()), 0)

        # 失敗したボーンを追加
        self.converter._failed_bones.add("ボーン1")
        self.converter._failed_bones.add("ボーン2")

        # 取得
        failed = self.converter.get_failed_bones()
        self.assertEqual(len(failed), 2)
        self.assertIn("ボーン1", failed)
        self.assertIn("ボーン2", failed)

        # 元のセットが変更されないことを確認
        failed.add("ボーン3")
        self.assertEqual(len(self.converter._failed_bones), 2)

    def test_convert_with_fixture_bone_hierarchy(self):
        """Fixtureを使用したボーン階層でのVMD変換テスト"""
        from tests.common.vmd_mock import create_test_vmd_data

        # テスト用のMMDボーン階層を作成
        # TODO: fixture_providerにcreate_mmd_bone_hierarchy機能を実装後に更新
        # bone_mapping = self.fixture_provider.create_mmd_bone_hierarchy()

        # 暫定的にマニュアルでボーン階層を作成
        cmds.select(clear=True)
        center = cmds.joint(name="center", position=[0, 0, 0])
        cmds.addAttr(center, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{center}.pmx_bone_name", "センター", type="string")

        cmds.select(center)
        upper_body = cmds.joint(name="upper_body", position=[0, 8, 0])
        cmds.addAttr(upper_body, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{upper_body}.pmx_bone_name", "上半身", type="string")

        cmds.select(upper_body)
        head = cmds.joint(name="head", position=[0, 15, 0])
        cmds.addAttr(head, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{head}.pmx_bone_name", "頭", type="string")

        # 名前マッピングを構築
        self.converter._build_name_mappings()

        # テスト用VMDデータを作成
        vmd_data = create_test_vmd_data()

        # 変換実行
        result = self.converter.convert(vmd_data)

        # 検証
        self.assertTrue(result)
        self.assertEqual(len(self.converter.get_failed_bones()), 0)

        # タイムラインが正しく設定されていることを確認
        self.assertEqual(cmds.playbackOptions(query=True, max=True), 60)

        # アニメーションが設定されていることを確認
        cmds.currentTime(30)
        pos = cmds.getAttr(f"{center}.translate")[0]
        # VMDモックデータの期待値と照合
        self.assertAlmostEqual(pos[0], 0.5, places=5)
        self.assertAlmostEqual(pos[1], 0.5, places=5)
        self.assertAlmostEqual(pos[2], -0.5, places=5)  # Z軸反転

    def test_pole_vector_generation_for_leg_ik(self):
        """足IKのPoleVector自動生成テスト"""

        # TODO: アニメーションがインポートされた後、PoleVectorが正しく、アニメーションしているかを検証する。
