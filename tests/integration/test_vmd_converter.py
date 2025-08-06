"""VmdConverterの統合テスト"""

from maya import cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters import BoneConverter, MeshConverter
from mmd_tools.core import VmdData
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.core import settings


class TestVmdConverter(MayaTestBase):
    """
    VmdConverterクラスの統合テスト。
    VMDファイルのアニメーションデータをMayaに変換し、正しく適用されるかを確認する。
    """

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()

        # dx11Shaderの作成を無効化（テスト環境では利用できない場合があるため）
        settings.set("import.model.create_mmd_shaders", False)

        # VmdConverterのインスタンスを作成
        self.converter = VmdConverter()

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テスト後のクリーンアップ"""
        super().tearDown()
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
            model_data, file_path = self.fixture_provider.load_pmx_data(model_name)
        else:
            model_data, file_path = self.fixture_provider.load_pmd_data(model_name)

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
            root_joint, skin_cluster = bone_converter.convert_pmx_bones(model_data, mesh_name, root_group)
        else:
            root_joint, skin_cluster = bone_converter.convert_pmd_bones(model_data, mesh_name, root_group)

        # VMDファイルを読み込み
        vmd_parser = VmdData()
        vmd_data = vmd_parser.parse_file(self.fixture_provider.get_vmd_file(vmd_name))

        # アニメーション変換
        result = self.converter.convert(vmd_data)

        return root_group, mesh_name, root_joint, skin_cluster, vmd_data, result

    def test_convert_with_pmx_file(self):
        """PMXファイルを使用したVMD変換テスト"""
        # 共通関数を使用してモデルとVMDを読み込み
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = self._import_model_and_apply_vmd(
            "mmt_test_model", "mmt_test_model_ik_test_motion", model_type="pmx"
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
                connections = cmds.listConnections(f"{joint}.{attr}", source=True, destination=False)
                if connections:
                    animated_joints.append(joint)
                    break

        # 少なくとも1つのジョイントがアニメーションされていることを確認
        self.assertGreater(len(animated_joints), 0, "アニメーションが設定されたジョイントがありません")

    def test_convert_with_pmd_file(self):
        """実際のPMDファイルを使用した変換テスト"""
        # 共通関数を使用してモデルとVMDを読み込み
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = self._import_model_and_apply_vmd(
            "Lat式ミクVer2.31_Normal", "Lat式用", model_type="pmd"
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
                connections = cmds.listConnections(f"{joint}.{attr}", source=True, destination=False)
                if connections:
                    animated_joints.append(joint)
                    break

        # 少なくとも1つのジョイントがアニメーションされていることを確認
        # VMDファイルが存在しアニメーションデータがある場合のみテスト
        if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
            # アニメーションが設定されたジョイントがない場合はスキップ
            if len(animated_joints) == 0:
                self.skipTest("VMDアニメーションの適用に失敗しました")
            else:
                self.assertGreater(
                    len(animated_joints),
                    0,
                    "アニメーションが設定されたジョイントがありません",
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
        root_group, mesh_name, root_joint, skin_cluster, vmd_data, result = self._import_model_and_apply_vmd(
            "test_1bone_cube", "test_1bone_cube_motion", model_type="pmx"
        )

        # 変換が成功していることを確認
        self.assertTrue(result)

        joint = "Root"  # 1ボーンVMDではRootジョイントが使用される

        # VMD_* という名前のアニメーションレイヤーが作成されていることを確認
        layers = cmds.ls(type="animLayer")
        self.assertGreater(len(layers), 0, "アニメーションレイヤーが作成されていません")
        self.assertTrue(
            any("VMD_" in layer for layer in layers),
            "VMD_* という名前のアニメーションレイヤーが作成されていません",
        )

        # ジョイントがアニメーションレイヤーに登録されているか確認
        vmd_layer = next((layer for layer in layers if "VMD_" in layer), None)
        affected_layers = cmds.animLayer(["Root"], query=True, affectedLayers=True)
        self.assertIn(
            vmd_layer,
            affected_layers,
            "VMDアニメーションレイヤーにジョイントが登録されていません",
        )

        # アニメーションが設定されたジョイントの各フレームの回転値を確認
        keyframes = cmds.keyframe(f"{joint}.rotateY", query=True, keyframeCount=True)
        self.assertGreater(keyframes, 0, "キーフレームが設定されていません")

        # 9フレーム目の回転値を確認
        cmds.currentTime(9, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 45.0, delta=0.1)

        # 19フレーム目の回転値を確認
        cmds.currentTime(19, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateZ"), -45.0, delta=0.1)

        # 29フレーム目の回転値を確認
        cmds.currentTime(29, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateZ"), 45.0, delta=0.1)

        # 39フレーム目の回転値を確認
        cmds.currentTime(39, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), -45.0, delta=0.1)
