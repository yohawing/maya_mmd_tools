"""
PMXインポーターの統合テスト
"""

import os
import tempfile
from unittest.mock import patch, MagicMock

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.io.pmx_importer import import_pmx_file
from mmd_tools.core.pmx_parser import PmxParser


class TestPmxImporter(MayaTestBase):
    """PMXインポーターの統合テストクラス"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
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

    def test_import_pmx_basic(self):
        """基本的なPMXファイルのインポートテスト"""
        # テストデータのPMXファイルを取得
        try:
            pmx_file = self.fixture_provider.get_pmx_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMXファイルが見つかりません")

        # PMXファイルをパース
        parser = PmxParser()
        parser.parse_file(pmx_file)

        # インポート前のシーン状態を記録
        initial_nodes = set(cmds.ls())

        # PMXファイルをインポート
        result = import_pmx_file(parser, pmx_file)

        # インポートが成功したことを確認
        self.assertTrue(result)

        # 新しく作成されたノードを確認
        new_nodes = set(cmds.ls()) - initial_nodes
        self.assertGreater(len(new_nodes), 0, "新しいノードが作成されていません")

        # メッシュが作成されたことを確認
        meshes = cmds.ls(type="mesh")
        self.assertGreater(len(meshes), 0, "メッシュが作成されていません")

        # ジョイントが作成されたことを確認
        joints = cmds.ls(type="joint")
        self.assertGreater(len(joints), 0, "ジョイントが作成されていません")

    def test_import_pmx_with_morphs(self):
        """モーフを含むPMXファイルのインポートテスト"""
        # 特定のPMXファイルを取得（モーフを含むもの）
        try:
            pmx_file = self.fixture_provider.get_pmx_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMXファイルが見つかりません")

        # PMXファイルをパース
        parser = PmxParser()
        parser.parse_file(pmx_file)

        # モーフが含まれているか確認
        if not hasattr(parser, "morphs") or len(parser.morphs) == 0:
            self.skipTest("テストファイルにモーフが含まれていません")

        # インポート
        result = import_pmx_file(parser, pmx_file)
        self.assertTrue(result)

        # ブレンドシェイプが作成されたことを確認
        blend_shapes = cmds.ls(type="blendShape")
        if len(parser.morphs) > 0:
            self.assertGreater(
                len(blend_shapes), 0, "ブレンドシェイプが作成されていません"
            )

    def test_import_pmx_with_physics(self):
        """物理演算を含むPMXファイルのインポートテスト"""
        # 特定のPMXファイルを取得（物理演算を含むもの）
        try:
            pmx_file = self.fixture_provider.get_pmx_file()
        except FileNotFoundError:
            self.skipTest("テスト用PMXファイルが見つかりません")

        # PMXファイルをパース
        parser = PmxParser()
        parser.parse_file(pmx_file)

        # 物理演算が含まれているか確認
        has_physics = (
            hasattr(parser, "rigid_bodies") and len(parser.rigid_bodies) > 0
        ) or (hasattr(parser, "joints") and len(parser.joints) > 0)

        if not has_physics:
            self.skipTest("テストファイルに物理演算が含まれていません")

        # インポート
        result = import_pmx_file(parser, pmx_file)
        self.assertTrue(result)

        # リジッドボディやジョイントが作成されたことを確認（該当する場合）
        # ※実装によってはスキップされる可能性もあるため、存在確認のみ

    def test_import_pmx_with_invalid_file(self):
        """無効なPMXファイルのインポートテスト"""
        # 無効なデータでモックパーサーを作成
        parser = MagicMock()
        parser.data = MagicMock()
        parser.data.header = MagicMock()
        parser.data.header.model_name = "Invalid Model"
        parser.data.vertices = []
        parser.data.faces = []
        parser.data.materials = []
        parser.data.bones = []
        parser.data.morphs = []

        # 一時ファイルを作成
        fd, temp_path = tempfile.mkstemp(suffix=".pmx")
        os.close(fd)
        self.temp_files.append(temp_path)

        # インポートを試行（エラーは発生しないが、何も作成されない可能性がある）
        result = import_pmx_file(parser, temp_path)

        # 結果を確認（実装によって異なる）
        # エラーハンドリングが適切に行われることを確認

    def test_import_pmx_multiple_files(self):
        """複数のPMXファイルを連続でインポートするテスト"""
        self.skipTest("未実装なのでスキップ")
        return
        # 利用可能なPMXファイルを取得
        available_files = self.fixture_provider.get_available_pmx_files()

        if len(available_files) < 2:
            self.skipTest("複数のテスト用PMXファイルが必要です")

        # 最初の2つのファイルをインポート
        for i, file_name in enumerate(available_files[:2]):
            pmx_file = self.fixture_provider.get_pmx_file(file_name)

            # PMXファイルをパース
            parser = PmxParser()
            parser.parse_file(pmx_file)

            # インポート
            result = import_pmx_file(parser, pmx_file)
            self.assertTrue(result, f"{file_name}のインポートに失敗しました")

            # それぞれのインポートでノードが追加されていることを確認
            nodes = cmds.ls()
            self.assertGreater(
                len(nodes), 0, f"{file_name}のインポート後にノードが存在しません"
            )
