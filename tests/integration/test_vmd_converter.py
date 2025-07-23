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

    def _import_model_and_apply_vmd(self, model_name, vmd_name, model_type="pmx"):
        """
        モデルを読み込み、VMDファイルを適用する共通関数

        Args:
            model_name (str): モデル名（拡張子なし）
            vmd_name (str): VMDファイル名（拡張子なし）
            model_type (str): モデルタイプ ("pmx" or "pmd")

        Returns:
            tuple: (root_group, mesh_name, root_joint, skin_cluster, vmd_data, result)
        """
        # モデルデータを読み込み
        if model_type == "pmx":
            model_data = self.fixture_provider.load_pmx_data(model_name)
        else:
            model_data = self.fixture_provider.load_pmd_data(model_name)
        
        file_path = model_data["file_path"]
        model_data = model_data["data"]

        # ルートグループを作成
        root_group = cmds.group(name="test_model_root", empty=True)

        # メッシュを作成（スキニングのため）
        mesh_converter = MeshConverter(file_path)
        if model_type == "pmx":
            group_name, mesh_name = mesh_converter.convert_pmx_mesh(model_data, root_group)
        else:
            group_name, mesh_name = mesh_converter.convert_pmd_mesh(model_data, root_group)

        # ボーンを作成
        bone_converter = BoneConverter()
        if model_type == "pmx":
            root_joint, skin_cluster = bone_converter.convert_pmx_bones(
                model_data, mesh_name, root_group
            )
        else:
            root_joint, skin_cluster = bone_converter.convert_pmd_bones(
                model_data, mesh_name, root_group
            )

        # VMDファイルを読み込み
        vmd_parser = VmdParser()
        vmd_data = vmd_parser.parse_file(self.fixture_provider.get_vmd_file(vmd_name))

        # アニメーション変換
        result = self.converter.convert(vmd_data)

        return root_group, mesh_name, root_joint, skin_cluster, vmd_data, result

    def test_convert_with_pmx_file(self):
        """PMXファイルを使用したVMD変換テスト"""
        # 共通関数を使用してモデルとVMDを読み込み
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = (
            self._import_model_and_apply_vmd("Lumine", "Lat式用", model_type="pmx")
        )

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
        # 共通関数を使用してモデルとVMDを読み込み
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = (
            self._import_model_and_apply_vmd("Lat式ミクVer2.31_Normal", "Lat式用", model_type="pmd")
        )

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

    def test_1bone_vmd_conversion(self):
        """1ボーンVMDデータの変換テスト"""
        # 共通関数を使用してモデルとVMDを読み込み
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = (
            self._import_model_and_apply_vmd("test_1bone_cube", "test_1bone_cube_motion", model_type="pmx")
        )

        # ボーン名マッピングを設定
        bone_mapping = {"全ての親": "root"}
        self.converter.set_bone_name_mapping(bone_mapping)

        # 変換実行
        result = self.converter.convert(vmd_data)

        # 0フレーム目は回転が（0, 0, 0）であることを確認
        cmds.currentTime(0)
        rotation = cmds.getAttr(f"{root_joint[0]}.rotate")[0]
        self.assertAlmostEqual(rotation[0], 0.0, places=3)
        self.assertAlmostEqual(rotation[1], 0.0, places=3)
        self.assertAlmostEqual(rotation[2], 0.0, places=3)

        # 9frame目の回転を確認
        cmds.currentTime(9)
        rotation = cmds.getAttr(f"{root_joint[0]}.rotate")[0]
        self.assertAlmostEqual(rotation[0], 45.0, places=3)
        self.assertAlmostEqual(rotation[1], 0.0, places=3)
        self.assertAlmostEqual(rotation[2], 0.0, places=3)

        # 19frame目の回転を確認
        cmds.currentTime(19)
        rotation = cmds.getAttr(f"{root_joint[0]}.rotate")[0]
        self.assertAlmostEqual(rotation[0], 0.0, places=3)
        self.assertAlmostEqual(rotation[1], 0.0, places=3)
        self.assertAlmostEqual(rotation[2], -45.0, places=3)

        # 29frame目の回転を確認
        cmds.currentTime(29)
        rotation = cmds.getAttr(f"{root_joint[0]}.rotate")[0]
        self.assertAlmostEqual(rotation[0], 0.0, places=3)
        self.assertAlmostEqual(rotation[1], 0.0, places=3)
        self.assertAlmostEqual(rotation[2], 45.0, places=3)

        # 39frame目の回転を確認
        cmds.currentTime(39)
        rotation = cmds.getAttr(f"{root_joint[0]}.rotate")[0]
        self.assertAlmostEqual(rotation[0], -45.0, places=3)
        self.assertAlmostEqual(rotation[1], 0.0, places=3)
        self.assertAlmostEqual(rotation[2], 0.0, places=3)

        # エラーが発生しないことを確認
        self.assertTrue(result)

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
        self.assertAlmostEqual(pos[0], 0.5, places=3)
        self.assertAlmostEqual(pos[1], 0.5, places=3)
        self.assertAlmostEqual(pos[2], -0.5, places=3)  # Z軸反転

    def test_pole_vector_generation_for_leg_ik(self):
        """足IKのPoleVector自動生成テスト"""

        # TODO: アニメーションがインポートされた後、PoleVectorが正しく、アニメーションしているかを検証する。
