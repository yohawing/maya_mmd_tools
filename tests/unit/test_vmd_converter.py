"""VmdConverterのユニットテスト

VmdConverterクラスの基本的な機能をテスト。
Maya環境内で実行されるが、シーン操作を伴わないテストを行う。
"""

import maya.cmds as cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.vmd_mock import create_test_vmd_data
from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.test_fixture_provider import TestFixtureProvider


class TestVmdConverter(MayaTestBase):
    """VmdConverterクラスのユニットテスト"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
        # VmdConverterのインスタンスを作成
        self.converter = VmdConverter()

        # テストフィクスチャプロバイダーを作成
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テスト後のクリーンアップ"""
        super().tearDown()
        # 一時ファイルのクリーンアップ
        self.fixture_provider.cleanup_temp_files()

        # テスト用カメラとライトを削除
        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_CAMERA_NAME, DEFAULT_LIGHT_NAME

        if cmds.objExists(DEFAULT_CAMERA_NAME):
            cmds.delete(DEFAULT_CAMERA_NAME)
        if cmds.objExists(DEFAULT_LIGHT_NAME):
            cmds.delete(DEFAULT_LIGHT_NAME)

        # アニメーションレイヤーをクリーンアップ
        anim_layers = cmds.ls(type="animLayer")
        for layer in anim_layers:
            if layer != "BaseAnimation":  # BaseAnimationレイヤーは削除しない
                try:
                    cmds.delete(layer)
                except Exception:
                    pass

    def test_init(self):
        """初期化のテスト"""
        self.assertEqual(self.converter.bone_name_mapping, {})
        self.assertEqual(self.converter.morph_name_mapping, {})
        self.assertEqual(self.converter.fps, 60.0)
        self.assertIsNotNone(self.converter.logger)
        self.assertEqual(len(self.converter._failed_bones), 0)
        self.assertTrue(self.converter.use_animation_layers)
        self.assertIsNone(self.converter.anim_layer)

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

    def test_convert_with_test_vmd_data(self):
        """テスト用VMDデータでの変換テスト"""
        # テスト用VMDデータを作成
        vmd_data = create_test_vmd_data()

        # ボーン名マッピングを設定
        bone_mapping = {"センター": "center", "上半身": "upper_body", "頭": "head"}
        self.converter.set_bone_name_mapping(bone_mapping)

        # 変換実行（実際のMayaシーンにボーンがないためFalseを返すが、
        # エラーが発生しないことを確認）
        self.converter.convert(vmd_data)

        # フレーム数が正しく設定されていることを確認
        # (VMDデータの通常フレームは30)
        import maya.cmds as cmds

        self.assertEqual(cmds.playbackOptions(q=True, max=True), 30)

    def test_get_or_create_camera(self):
        """カメラの作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # 新規作成
        camera_name = self.converter._get_or_create_camera()
        self.assertIsNotNone(camera_name)
        self.assertTrue(cmds.objExists(camera_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_CAMERA, node=camera_name, exists=True))

        # 既存カメラの取得
        camera_name2 = self.converter._get_or_create_camera()
        self.assertEqual(camera_name, camera_name2)

    def test_get_or_create_light(self):
        """照明の作成・取得テスト"""
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_LIGHT

        # 新規作成
        light_name = self.converter._get_or_create_light()
        self.assertIsNotNone(light_name)
        self.assertTrue(cmds.objExists(light_name))
        self.assertTrue(cmds.attributeQuery(ATTR_MMD_LIGHT, node=light_name, exists=True))

        # 既存照明の取得
        light_name2 = self.converter._get_or_create_light()
        self.assertEqual(light_name, light_name2)

    def test_convert_camera_animation(self):
        """カメラアニメーション変換テスト"""
        from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame

        # テスト用カメラフレームを作成
        camera_frames = []
        for i in range(3):
            frame = VmdCameraFrame()
            frame.frame_number = i * 10
            frame.position = (i * 1.0, i * 2.0, i * 3.0)
            frame.rotation = (0.0, 0.0, 0.0)
            frame.distance = 10.0 + i
            frame.viewing_angle = 30 + i * 5
            camera_frames.append(frame)

        # 変換実行
        result = self.converter._convert_camera_animation(camera_frames)
        self.assertTrue(result)

        # カメラが作成されたことを確認
        import maya.cmds as cmds
        from mmd_tools.core.constants import ATTR_MMD_CAMERA

        # カメラ名を正確に確認（変換関数が返すカメラ名をチェック）
        cameras = cmds.ls(type="camera")
        camera_found = False
        for cam in cameras:
            transform = cmds.listRelatives(cam, parent=True)
            if transform and cmds.attributeQuery(ATTR_MMD_CAMERA, node=transform[0], exists=True):
                camera_found = True
                # キーフレームが設定されたことを確認
                keyframes = cmds.keyframe(f"{transform[0]}.translateX", query=True)
                self.assertIsNotNone(keyframes)
                self.assertEqual(len(keyframes), 3)
                break

        self.assertTrue(camera_found, "MMDカメラが作成されていません")

    def test_convert_light_animation(self):
        """照明アニメーション変換テスト"""
        from mmd_tools.core.vmd_data.light_frame import VmdLightFrame

        # テスト用照明フレームを作成
        light_frames = []
        for i in range(3):
            frame = VmdLightFrame()
            frame.frame_number = i * 10
            frame.position = (0.0, -1.0, 0.0)  # 方向ベクトル
            frame.color = (1.0 - i * 0.1, 1.0 - i * 0.1, 1.0 - i * 0.1)
            light_frames.append(frame)

        # 変換実行
        result = self.converter._convert_light_animation(light_frames)
        self.assertTrue(result)

        # 照明が作成されたことを確認
        import maya.cmds as cmds
        from mmd_tools.core.constants import DEFAULT_LIGHT_NAME

        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        # キーフレームが設定されたことを確認
        light_shape = cmds.listRelatives(DEFAULT_LIGHT_NAME, shapes=True, type="directionalLight")[0]
        keyframes = cmds.keyframe(f"{light_shape}.colorR", query=True)
        self.assertIsNotNone(keyframes)
        self.assertEqual(len(keyframes), 3)

    def test_convert_morph_animation(self):
        """モーフアニメーション変換テスト"""
        from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame

        # テスト用モーフフレームを作成
        morph_frames = []
        for i in range(3):
            frame = VmdMorphFrame()
            frame.frame_number = i * 10
            frame.morph_name = "mabataki"  # ASCII文字に変更
            frame.value = i * 0.5  # 0.0, 0.5, 1.0
            morph_frames.append(frame)

        # テスト用ブレンドシェイプを作成
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]

        # テスト用ターゲットを追加
        target = cmds.duplicate(cube)[0]
        cmds.move(1, 0, 0, f"{target}.vtx[*]", relative=True)
        cmds.blendShape(blend_shape, edit=True, target=(cube, 0, target, 1.0))
        cmds.aliasAttr("mabataki", f"{blend_shape}.weight[0]")

        # モーフマッピングを設定
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, 0, "mabataki")

        # 変換実行
        result = self.converter._convert_morph_animation(morph_frames)
        self.assertTrue(result)

        # キーフレームが設定されたことを確認
        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIsNotNone(keyframes)
        self.assertEqual(len(keyframes), 3)

        # クリーンアップ
        cmds.delete(cube, target)

    def test_build_morph_mappings(self):
        """モーフマッピング構築テスト"""
        # テスト用メッシュとブレンドシェイプを作成
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]

        # テスト用ターゲットを追加（ASCII文字に変更）
        morph_names = ["mabataki", "egao", "wink"]
        for i, morph_name in enumerate(morph_names):
            target = cmds.duplicate(cube)[0]
            cmds.move(i + 1, 0, 0, f"{target}.vtx[*]", relative=True)
            cmds.blendShape(blend_shape, edit=True, target=(cube, i, target, 1.0))
            cmds.aliasAttr(morph_name, f"{blend_shape}.weight[{i}]")
            cmds.delete(target)

        # デバッグ情報を出力
        print(f"Created blend shape: {blend_shape}")
        print(f"Weight count: {cmds.blendShape(blend_shape, query=True, weightCount=True)}")
        for i in range(3):
            alias = cmds.aliasAttr(f"{blend_shape}.weight[{i}]", query=True)
            print(f"Alias for weight[{i}]: {alias}")

        # マッピングを構築
        self.converter._build_morph_mappings()

        # デバッグ情報を出力
        print(f"Morph mapping: {self.converter.morph_name_mapping}")

        # マッピングが作成されたことを確認
        self.assertEqual(len(self.converter.morph_name_mapping), 3)
        self.assertIn("mabataki", self.converter.morph_name_mapping)
        self.assertIn("egao", self.converter.morph_name_mapping)
        self.assertIn("wink", self.converter.morph_name_mapping)

        # クリーンアップ
        cmds.delete(cube)

    def _create_test_joints_for_vmd(self):
        """VMDテスト用のジョイントを作成"""
        from mmd_tools.core.constants import ATTR_MMD_BONE_NAME

        # センタージョイント
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.addAttr(center, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{center}.{ATTR_MMD_BONE_NAME}", "センター", type="string")

        # 上半身ジョイント
        upper_body = cmds.joint(name="upper_body", position=[0, 15, 0])
        cmds.addAttr(upper_body, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{upper_body}.{ATTR_MMD_BONE_NAME}", "上半身", type="string")

        # 頭ジョイント
        head = cmds.joint(name="head", position=[0, 20, 0])
        cmds.addAttr(head, longName=ATTR_MMD_BONE_NAME, dataType="string")
        cmds.setAttr(f"{head}.{ATTR_MMD_BONE_NAME}", "頭", type="string")

        cmds.select(clear=True)

        return {"center": center, "upper_body": upper_body, "head": head}

    def test_convert_with_fixture_vmd_camera(self):
        """フィクスチャを使用したカメラアニメーション変換テスト"""
        # テスト用VMDファイルを取得
        try:
            vmd_path = self.fixture_provider.get_vmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用VMDファイルが見つかりません")

        # VMDファイルをパース
        from mmd_tools.core.vmd_data import VmdData

        parser = VmdData()
        parser.parse_file(vmd_path)

        # カメラアニメーション変換
        if hasattr(parser, "camera_frames") and parser.camera_frames:
            result = self.converter._convert_camera_animation(parser.camera_frames)
            self.assertTrue(result, "カメラアニメーション変換に失敗しました")
        else:
            self.skipTest("VMDファイルにカメラアニメーションが含まれていません")

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

        # キーフレームが設定されたことを確認
        keyframes = cmds.keyframe(f"{mmd_camera}.translateX", query=True)
        self.assertIsNotNone(keyframes, "カメラにキーフレームが設定されていません")
        self.assertGreater(len(keyframes), 0, "カメラにキーフレームが設定されていません")
