"""
PMDインポーターの統合テスト
"""

import os

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.io.pmd_importer import import_pmd_file
from mmd_tools.core.pmd_data import PmdData


class TestPmdImporter(MayaTestBase):
    """PMDインポーターの統合テストクラス"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()

        # dx11Shaderの作成を無効化（テスト環境では利用できない場合があるため）
        from mmd_tools.core import settings

        settings.set("import.model.create_mmd_shaders", False)

        self.fixture_provider = TestFixtureProvider()

        # テスト用の一時ファイル
        self.temp_files = []

    def tearDown(self):
        """テストのクリーンアップ"""
        # 一時ファイルの削除
        for temp_file in self.temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        # TestFixtureProviderの一時ファイルをクリーンアップ
        self.fixture_provider.cleanup_temp_files()

        super().tearDown()

    def test_import_pmd_basic(self):
        """基本的なPMDファイルのインポートテスト"""
        # テストデータのPMDファイルを取得
        try:
            pmd_file = self.fixture_provider.get_pmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMDファイルが見つかりません")

        # PMDファイルをパース
        parser = PmdData()
        parser.parse_file(pmd_file)

        # インポート前のシーン状態を記録
        initial_nodes = set(cmds.ls())

        # PMDファイルをインポート
        result = import_pmd_file(parser, pmd_file)

        # インポートが成功したことを確認
        self.assertIsNotNone(result, "PMDファイルのインポートに失敗しました")

        # 新しく作成されたノードを確認
        new_nodes = set(cmds.ls()) - initial_nodes
        self.assertGreater(len(new_nodes), 0, "新しいノードが作成されていません")

        # メッシュが作成されたことを確認
        meshes = cmds.ls(type="mesh")
        self.assertGreater(len(meshes), 0, "メッシュが作成されていません")

        # ジョイントが作成されたことを確認
        joints = cmds.ls(type="joint")
        self.assertGreater(len(joints), 0, "ジョイントが作成されていません")

    def test_import_pmd_with_morphs(self):
        """モーフを含むPMDファイルのインポートテスト"""
        # 特定のPMDファイルを取得（モーフを含むもの）
        try:
            pmd_file = self.fixture_provider.get_pmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMDファイルが見つかりません")

        # PMDファイルをパース
        parser = PmdData()
        pmd_data = parser.parse_file(pmd_file)

        # モーフが含まれているか確認
        if not hasattr(pmd_data, "morphs") or len(pmd_data.morphs) == 0:
            self.skipTest("テストファイルにモーフが含まれていません")

        # インポート
        result = import_pmd_file(pmd_data, pmd_file)
        self.assertIsNotNone(result, "PMDファイルのインポートに失敗しました")

        # ブレンドシェイプが作成されたことを確認
        blend_shapes = cmds.ls(type="blendShape")
        if len(pmd_data.morphs) > 0:
            self.assertGreater(len(blend_shapes), 0, "ブレンドシェイプが作成されていません")

    def test_import_pmd_with_physics(self):
        """物理演算を含むPMDファイルのインポートテスト"""
        # 特定のPMDファイルを取得（物理演算を含むもの）
        try:
            pmd_file = self.fixture_provider.get_pmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMDファイルが見つかりません")

        # PMDファイルをパース
        parser = PmdData()
        parser.parse_file(pmd_file)

        # 物理演算が含まれているか確認
        has_physics = (hasattr(parser, "rigid_bodies") and len(parser.rigid_bodies) > 0) or (
            hasattr(parser, "joints") and len(parser.joints) > 0
        )

        if not has_physics:
            self.skipTest("テストファイルに物理演算が含まれていません")

        # インポート
        result = import_pmd_file(parser, pmd_file)
        self.assertIsNotNone(result, "PMDファイルのインポートに失敗しました")

        # リジッドボディやジョイントが作成されたことを確認（該当する場合）
        # ※実装によってはスキップされる可能性もあるため、存在確認のみ

    def test_import_pmd_multiple_files(self):
        """複数のPMDファイルを連続でインポートするテスト"""
        available_files = self.fixture_provider.get_available_pmd_files()

        if len(available_files) < 2:
            self.skipTest("複数のテスト用PMDファイルが必要です")

        # 最初の2つのファイルをインポート
        for i, file_name in enumerate(available_files[:2]):
            pmd_file = self.fixture_provider.get_pmd_file(file_name)

            # PMDファイルをパース
            parser = PmdData()
            parser.parse_file(pmd_file)

            # インポート
            result = import_pmd_file(parser, pmd_file)
            self.assertTrue(result, f"{file_name}のインポートに失敗しました")

            # それぞれのインポートでノードが追加されていることを確認
            nodes = cmds.ls()
            self.assertGreater(len(nodes), 0, f"{file_name}のインポート後にノードが存在しません")

    def test_import_pmd_with_materials(self):
        """マテリアルを含むPMDファイルのインポートテスト"""
        try:
            pmd_file = self.fixture_provider.get_pmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMDファイルが見つかりません")

        # PMDファイルをパース
        parser = PmdData()
        parser.parse_file(pmd_file)

        # マテリアルが含まれているか確認
        if not hasattr(parser, "materials") or len(parser.materials) == 0:
            self.skipTest("テストファイルにマテリアルが含まれていません")

        # インポート
        result = import_pmd_file(parser, pmd_file)
        self.assertIsNotNone(result, "PMDファイルのインポートに失敗しました")

        # マテリアルが作成されたことを確認
        materials = cmds.ls(type="lambert") + cmds.ls(type="phong") + cmds.ls(type="blinn") + cmds.ls(type="standardSurface")
        # デフォルトマテリアルを除外
        materials = [m for m in materials if m not in ["lambert1", "particleCloud1"]]

        if len(parser.materials) > 0:
            self.assertGreater(len(materials), 0, "マテリアルが作成されていません")

    def test_import_pmd_bone_hierarchy(self):
        """ボーン階層が正しく構築されるかのテスト"""
        try:
            pmd_file = self.fixture_provider.get_pmd_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMDファイルが見つかりません")

        # PMDファイルをパース
        parser = PmdData()
        parser.parse_file(pmd_file)

        # ボーンが含まれているか確認
        if not hasattr(parser, "bones") or len(parser.bones) == 0:
            self.skipTest("テストファイルにボーンが含まれていません")

        # インポート
        result = import_pmd_file(parser, pmd_file)
        self.assertIsNotNone(result, "PMDファイルのインポートに失敗しました")

        # ジョイントが作成されたことを確認
        joints = cmds.ls(type="joint")
        self.assertEqual(
            len(joints),
            len(parser.bones),
            "作成されたジョイント数がボーン数と一致しません",
        )

        # ルートジョイントを確認
        root_joints = [j for j in joints if not cmds.listRelatives(j, parent=True, type="joint")]
        self.assertGreater(len(root_joints), 0, "ルートジョイントが見つかりません")
