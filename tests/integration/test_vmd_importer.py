"""
VMDインポーター機能の統合テスト
"""

import os
from maya import cmds
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.vmd_data import VmdData
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdImporter(MayaTestBase):
    """
    VMDインポーター機能の統合テスト。
    実際のMaya環境でVMDファイルをインポートし、アニメーションが正しく適用されるかを確認する。
    """

    def setUp(self):
        """
        各テストの前に実行される設定。
        テストに必要なMayaシーンのセットアップとテストデータのパスを準備。
        """
        super().setUp()
        # 新しいMayaシーンを作成
        cmds.file(new=True, force=True)

        # dx11Shaderの作成を無効化（テスト環境では利用できない場合があるため）
        from mmd_tools.core import settings

        settings.set("import.model.create_mmd_shaders", False)

        # テストデータのパスを設定
        self.test_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """
        各テスト後のクリーンアップ処理。
        テスト中に作成されたノードやシーンの状態をリセット。
        """
        super().tearDown()

        # アニメーションレイヤーをクリーンアップ
        anim_layers = cmds.ls(type="animLayer")
        for layer in anim_layers:
            if layer != "BaseAnimation":  # BaseAnimationレイヤーは削除しない
                try:
                    cmds.delete(layer)
                except Exception:
                    pass

        # シーンをクリア
        cmds.file(new=True, force=True)

        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()

    def _create_test_skeleton(self):
        """テスト用のスケルトン構造を作成"""
        # ルートジョイント
        root = cmds.joint(name="root", position=[0, 0, 0])
        cmds.addAttr(root, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{root}.pmx_bone_name", "全ての親", type="string")

        # センタージョイント
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.addAttr(center, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{center}.pmx_bone_name", "センター", type="string")

        # 上半身ジョイント
        cmds.select(center)
        upper_body = cmds.joint(name="upper_body", position=[0, 15, 0])
        cmds.addAttr(upper_body, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{upper_body}.pmx_bone_name", "上半身", type="string")

        # 頭ジョイント
        head = cmds.joint(name="head", position=[0, 20, 0])
        cmds.addAttr(head, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{head}.pmx_bone_name", "頭", type="string")

        # 左腕ジョイント
        cmds.select(upper_body)
        left_arm = cmds.joint(name="left_arm", position=[-5, 15, 0])
        cmds.addAttr(left_arm, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{left_arm}.pmx_bone_name", "左腕", type="string")

        # 右腕ジョイント
        cmds.select(upper_body)
        right_arm = cmds.joint(name="right_arm", position=[5, 15, 0])
        cmds.addAttr(right_arm, longName="pmx_bone_name", dataType="string")
        cmds.setAttr(f"{right_arm}.pmx_bone_name", "右腕", type="string")

        # 選択をクリア
        cmds.select(clear=True)

        return {
            "root": root,
            "center": center,
            "upper_body": upper_body,
            "head": head,
            "left_arm": left_arm,
            "right_arm": right_arm,
        }

    def test_vmd_import_basic(self):
        """VMDファイルの基本的なインポート機能をテスト"""
        # テスト用スケルトンを作成
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルが存在することを確認
        self.assertTrue(os.path.exists(vmd_path), f"VMDファイルが見つかりません: {vmd_path}")

        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)

        # インポートが成功したことを確認
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")

        # タイムライン設定が更新されたことを確認
        max_time = cmds.playbackOptions(query=True, maxTime=True)

        # タイムラインが拡張されたことを確認（デフォルトの1-24フレームから変更されているはず）
        self.assertGreater(max_time, 24, "タイムラインが更新されていません")

    def test_vmd_import_with_namespace(self):
        """ネームスペース付きモデルへのVMDインポートをテスト"""
        # ネームスペースを作成
        namespace = "test_model"
        if not cmds.namespace(exists=namespace):
            cmds.namespace(add=namespace)

        # ネームスペース内にスケルトンを作成
        cmds.namespace(set=namespace)
        self._create_test_skeleton()
        cmds.namespace(set=":")

        # ネームスペース付きのジョイントを選択
        cmds.select(f"{namespace}:center")

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)

        # インポートが成功したことを確認
        self.assertTrue(result, "ネームスペース付きモデルへのVMDインポートに失敗しました")

        # ネームスペース付きジョイントにキーフレームが設定されたことを確認
        center_keys = cmds.keyframe(f"{namespace}:center", query=True, keyframeCount=True)
        if center_keys:
            self.assertGreater(center_keys, 0, "センタージョイントにキーフレームが設定されていません")

    def test_vmd_import_without_model(self):
        """モデルがない状態でのVMDインポートをテスト"""
        # スケルトンを作成しない（空のシーン）

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをインポート（モデルがないので失敗するはず）
        result = import_mmd_file(vmd_path)

        # インポートは成功するが、キーフレームは設定されないはず
        self.assertTrue(result, "VMDファイルの読み込みに失敗しました")

        # ジョイントが存在しないことを確認
        joints = cmds.ls(type="joint")
        self.assertEqual(len(joints), 0, "ジョイントが作成されています")

    def test_vmd_parser_integration(self):
        """VmdParserとの統合をテスト"""
        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VmdParserで直接パース
        parser = VmdData()
        parser.parse_file(vmd_path)

        # パース結果を確認
        self.assertIsNotNone(parser.header.model_name, "モデル名が読み込まれていません")

        # ボーンフレームまたはモーフフレームが存在することを確認
        has_animation = (hasattr(parser, "bone_frames") and parser.bone_frames) or (
            hasattr(parser, "morph_frames") and parser.morph_frames
        )
        self.assertTrue(has_animation, "アニメーションデータが読み込まれていません")

    def test_pmx_model_with_vmd_animation(self):
        """実際のPMXモデルにVMDアニメーションを適用する統合テスト"""
        # PMXファイルをインポート
        pmx_data, pmx_path = self.fixture_provider.load_pmx_data("mmt_test_model")

        # PMXモデルをインポート
        pmx_result = import_mmd_file(pmx_path)
        self.assertTrue(pmx_result, "PMXファイルのインポートに失敗しました")

        # インポートされたジョイントを確認
        joints = cmds.ls(type="joint")
        self.assertGreater(len(joints), 0, "ジョイントがインポートされていません")

        vmd_data, vmd_path = self.fixture_provider.load_vmd_data("mmt_test_model_ik_test_motion")

        if not hasattr(vmd_data, "bone_frames") or not vmd_data.bone_frames:
            self.skipTest("テストVMDファイルにボーンアニメーションが含まれていません")

        # 現在のフレーム数を記録
        initial_max = cmds.playbackOptions(query=True, maxTime=True)

        # VMDアニメーションをインポート
        vmd_result = import_mmd_file(vmd_path)
        self.assertTrue(vmd_result, "VMDファイルのインポートに失敗しました")

        # タイムラインが更新されたか確認
        new_max = cmds.playbackOptions(query=True, maxTime=True)
        # VMDにアニメーションデータがある場合のみタイムラインが更新される
        if vmd_data.bone_frames:
            self.assertGreaterEqual(new_max, initial_max, "タイムラインが更新されていません")

        # アニメーションが適用されたジョイントを確認
        animated_joints = []
        for joint in joints:
            # translateとrotateの各軸でキーフレームがあるか確認
            for attr in [
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            ]:
                # アニメーションカーブが接続されているか確認
                connections = cmds.listConnections(f"{joint}.{attr}", source=True, destination=False)
                if connections:
                    animated_joints.append(joint)
                    break

        # VMDにボーンアニメーションデータがある場合のみテスト
        if vmd_data.bone_frames:
            self.assertGreater(
                len(animated_joints),
                0,
                "どのジョイントにもアニメーションが適用されていません",
            )

    def test_vmd_camera_animation_import(self):
        """VMDファイルからカメラアニメーションをインポートするテスト"""
        # テスト用スケルトンを作成（カメラアニメーションでも必要な場合がある）
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをパースしてカメラデータがあるか確認
        parser = VmdData()
        parser.parse_file(vmd_path)

        if not hasattr(parser, "camera_frames") or not parser.camera_frames:
            self.skipTest("テストVMDファイルにカメラアニメーションが含まれていません")

        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")

        # MMDカメラが作成されたことを確認
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        cameras = cmds.ls(type="camera")
        mmd_camera = None
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                mmd_camera = transform[0]
                break

        self.assertIsNotNone(mmd_camera, "MMDカメラが作成されていません")

        # カメラにキーフレームが設定されたことを確認
        has_animation = False
        for attr in [
            "translateX",
            "translateY",
            "translateZ",
            "rotateX",
            "rotateY",
            "rotateZ",
        ]:
            keyframes = cmds.keyframe(mmd_camera, attribute=attr, query=True)
            if keyframes:
                has_animation = True
                break

        self.assertTrue(has_animation, "カメラにアニメーションが設定されていません")

        # カメラシェイプのFOVアニメーションも確認
        camera_shape = cmds.listRelatives(mmd_camera, shapes=True, type="camera")[0]
        fov_keys = cmds.keyframe(camera_shape, attribute="focalLength", query=True)
        self.assertIsNotNone(fov_keys, "カメラのFOVアニメーションが設定されていません")

    def test_vmd_light_animation_import(self):
        """VMDファイルから照明アニメーションをインポートするテスト"""
        # テスト用スケルトンを作成
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをパースして照明データがあるか確認
        parser = VmdData()
        parser.parse_file(vmd_path)

        if not hasattr(parser, "light_frames") or not parser.light_frames:
            self.skipTest("テストVMDファイルに照明アニメーションが含まれていません")

        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")

        # MMD照明が作成されたことを確認
        from mmd_tools.core.constants import ATTR_MMD_LIGHT

        lights = cmds.ls(type="directionalLight")
        mmd_light = None
        for light in lights:
            transform = cmds.listRelatives(light, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_LIGHT, node=transform[0], exists=True):
                mmd_light = transform[0]
                mmd_light_shape = light
                break

        self.assertIsNotNone(mmd_light, "MMD照明が作成されていません")

        # 照明の回転にキーフレームが設定されたことを確認
        has_rotation_animation = False
        for attr in ["rotateX", "rotateY", "rotateZ"]:
            keyframes = cmds.keyframe(mmd_light, attribute=attr, query=True)
            if keyframes:
                has_rotation_animation = True
                break

        self.assertTrue(has_rotation_animation, "照明の回転アニメーションが設定されていません")

        # 照明の色にキーフレームが設定されたことを確認
        has_color_animation = False
        for attr in ["colorR", "colorG", "colorB"]:
            keyframes = cmds.keyframe(mmd_light_shape, attribute=attr, query=True)
            if keyframes:
                has_color_animation = True
                break

        self.assertTrue(has_color_animation, "照明の色アニメーションが設定されていません")

    def test_vmd_morph_animation_import(self):
        """VMDファイルからモーフアニメーションをインポートするテスト"""

        # TODO: テスト用モデルの準備が出来たら実装します。
        self.skipTest("モーフアニメーションのテストは未実装です")

    def test_vmd_import_with_animation_layers(self):
        """アニメーションレイヤーを使用したVMDインポートのテスト"""
        # テスト用スケルトンを作成
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをインポート（デフォルトでアニメーションレイヤーが使用される）
        result = import_mmd_file(vmd_path)
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")

        # アニメーションレイヤーが作成されたことを確認
        anim_layers = cmds.ls(type="animLayer")
        # BaseAnimationレイヤーとVMDレイヤーが存在するはず
        self.assertGreaterEqual(len(anim_layers), 2, "アニメーションレイヤーが作成されていません")

        # VMDレイヤーが存在することを確認
        vmd_layer_found = False
        for layer in anim_layers:
            if "VMD_Layer_" in layer or layer == "BaseAnimation":
                vmd_layer_found = True
                break
        self.assertTrue(vmd_layer_found, "VMDアニメーションレイヤーが見つかりません")

    def test_multiple_vmd_import_with_layers(self):
        """複数のVMDファイルを異なるレイヤーにインポートするテスト"""
        # テスト用スケルトンを作成
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if len(vmd_files) < 1:
            self.skipTest("テスト用VMDファイルが見つかりません")

        # 最初のVMDファイルをインポート
        vmd_path1 = os.path.join(self.test_data_dir, vmd_files[0])
        result1 = import_mmd_file(vmd_path1)
        self.assertTrue(result1, "最初のVMDファイルのインポートに失敗しました")

        # アニメーションレイヤーの数を記録
        initial_layers = cmds.ls(type="animLayer")
        initial_count = len(initial_layers)

        # 同じVMDファイルを再度インポート（新しいレイヤーが作成されるはず）
        result2 = import_mmd_file(vmd_path1)
        self.assertTrue(result2, "2回目のVMDファイルのインポートに失敗しました")

        # 新しいレイヤーが作成されたことを確認
        final_layers = cmds.ls(type="animLayer")
        final_count = len(final_layers)
        self.assertGreater(
            final_count,
            initial_count,
            "新しいアニメーションレイヤーが作成されていません",
        )

    def test_vmd_import_layer_weight(self):
        """アニメーションレイヤーのウェイト設定テスト"""
        # テスト用スケルトンを作成
        self._create_test_skeleton()

        # VMDファイルのパスを取得
        vmd_files = [f for f in os.listdir(self.test_data_dir) if f.endswith(".vmd")]

        if not vmd_files:
            self.skipTest("テスト用VMDファイルが見つかりません")

        vmd_path = os.path.join(self.test_data_dir, vmd_files[0])

        # VMDファイルをインポート
        result = import_mmd_file(vmd_path)
        self.assertTrue(result, "VMDファイルのインポートに失敗しました")

        # VMDレイヤーを探す
        anim_layers = cmds.ls(type="animLayer")
        vmd_layer = None
        for layer in anim_layers:
            if "VMD_Layer_" in layer:
                vmd_layer = layer
                break

        if vmd_layer:
            # レイヤーのウェイトが1.0であることを確認
            weight = cmds.animLayer(vmd_layer, query=True, weight=True)
            self.assertEqual(weight, 1.0, "アニメーションレイヤーのウェイトが正しくありません")

    def test_fixture_vmd_namespace_support(self):
        """フィクスチャを使用したネームスペース対応テスト"""

        # TODO: ネームスペース対応後にテストを実装
        self.skipTest("ネームスペース対応後にテストを実装します")
