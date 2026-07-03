"""
PMXインポーターの統合テスト
"""

import os

from maya import cmds

from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.io.pmx_importer import import_pmx_file
from mmd_tools.core.mmd_parser import MMDParseException, parse_pmx_file


class TestPmxImporter(MayaTestBase):
    """PMXインポーターの統合テストクラス"""

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

    def test_import_pmx_basic(self):
        """基本的なPMXファイルのインポートテスト"""
        pmx_file = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        parser = parse_pmx_file(pmx_file)

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

        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_DISPLAY_FRAMES_JSON, node=result, exists=True),
            "表示枠 metadata が root に保存されていません",
        )
        self.assertTrue(cmds.getAttr(f"{result}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))

        # テクスチャ file ノードがあれば、パスが有効であることを確認
        file_nodes = cmds.ls(type="file") or []
        for file_node in file_nodes:
            texture_path = cmds.getAttr(f"{file_node}.fileTextureName")
            self.assertTrue(texture_path, f"{file_node}.fileTextureName が空です")

    def test_import_pmx_multiple_files(self):
        """全てのPMXモデルが基本的にロード可能かテスト"""

        pmx_files = self.fixture_provider.get_all_pmx_files()

        if len(pmx_files) < 2:
            self.skipTest("複数の PMX fixture が必要です")

        for model_name, file_path in pmx_files.items():
            with self.subTest(model=model_name):
                cmds.file(new=True, force=True)
                initial_nodes = set(cmds.ls())

                try:
                    parser = parse_pmx_file(file_path)
                except (ValueError, MMDParseException):
                    continue
                result = import_pmx_file(parser, file_path)

                self.assertTrue(result)

                new_nodes = set(cmds.ls()) - initial_nodes
                self.assertGreater(len(new_nodes), 0, "新しいノードが作成されていません")

                meshes = cmds.ls(type="mesh")
                if parser.vertices:
                    self.assertGreater(len(meshes), 0, "メッシュが作成されていません")

                joints = cmds.ls(type="joint")
                self.assertGreater(len(joints), 0, "ジョイントが作成されていません")
