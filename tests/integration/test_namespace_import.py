"""
Namespace機能の統合テスト

複数モデルのインポートとnamespace管理をテストします。
"""

import os
import tempfile
import unittest

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.namespace_utils import NamespaceUtils


class TestNamespaceImport(unittest.TestCase):
    """Namespace付きインポートの統合テスト"""

    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        # テストデータのパスを設定
        cls.test_data_dir = os.path.join(
            os.path.dirname(__file__), "..", "test_data"
        )
        cls.pmx_file = os.path.join(cls.test_data_dir, "simple_cube.pmx")
        cls.pmd_file = os.path.join(cls.test_data_dir, "simple_model.pmd")

    def setUp(self):
        """各テストの前処理"""
        # 新規シーンを作成
        cmds.file(new=True, force=True)
        
        # namespace設定を有効化
        settings.set("import.general.use_namespace", True)

    def tearDown(self):
        """各テストの後処理"""
        # namespace設定をリセット
        settings.set("import.general.use_namespace", False)
        
        # 作成されたnamespaceをクリーンアップ
        model_namespaces = NamespaceUtils.list_model_namespaces()
        for ns in model_namespaces:
            try:
                NamespaceUtils.cleanup_namespace(ns, force=True)
            except:
                pass

    def test_single_model_with_namespace(self):
        """単一モデルのnamespace付きインポート"""
        # インポートオプション
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }
        
        # PMXファイルをインポート
        if os.path.exists(self.pmx_file):
            root_node = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root_node)
            
            # namespaceが作成されていることを確認
            namespaces = NamespaceUtils.list_model_namespaces()
            self.assertGreater(len(namespaces), 0)
            
            # root_nodeがnamespace付きであることを確認
            self.assertIn(":", root_node)

    def test_multiple_models_unique_namespaces(self):
        """同じモデルを複数回インポートした際の連番付与"""
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }
        
        if os.path.exists(self.pmx_file):
            # 1回目のインポート
            root1 = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root1)
            
            # 2回目のインポート
            root2 = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root2)
            
            # 異なるnamespaceが使用されていることを確認
            ns1 = NamespaceUtils.get_namespace_from_node(root1)
            ns2 = NamespaceUtils.get_namespace_from_node(root2)
            
            self.assertIsNotNone(ns1)
            self.assertIsNotNone(ns2)
            self.assertNotEqual(ns1, ns2)
            
            # 連番が付与されていることを確認
            self.assertTrue(ns2.endswith("_2") or ns2.endswith("_3"))

    def test_namespace_disabled(self):
        """namespace無効時の動作確認"""
        options = {
            "use_namespace": False,
            "scale": 1.0,
        }
        
        if os.path.exists(self.pmx_file):
            root_node = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root_node)
            
            # namespaceが使用されていないことを確認
            self.assertNotIn(":", root_node)
            
            # モデル用namespaceが作成されていないことを確認
            namespaces = NamespaceUtils.list_model_namespaces()
            self.assertEqual(len(namespaces), 0)

    def test_namespace_with_japanese_name(self):
        """日本語モデル名のnamespace変換"""
        # 日本語名を持つモデルをシミュレート
        # 実際のテストではPMXパーサーのモックが必要
        japanese_name = "初音ミク"
        namespace = NamespaceUtils.generate_namespace(japanese_name)
        
        # 英数字に変換されていることを確認
        self.assertTrue(namespace.replace("_", "").isalnum())
        self.assertNotIn(" ", namespace)
        self.assertNotIn("@", namespace)

    def test_vmd_import_with_namespace(self):
        """namespace付きモデルへのVMDインポート"""
        # まずモデルをnamespace付きでインポート
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }
        
        if os.path.exists(self.pmx_file):
            root_node = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root_node)
            
            # VMDインポート時のnamespace検出をテスト
            # （実際のVMDファイルが必要）
            namespace = NamespaceUtils.get_namespace_from_node(root_node)
            self.assertIsNotNone(namespace)

    def test_namespace_cleanup_on_error(self):
        """エラー時のnamespaceクリーンアップ"""
        # エラーを発生させるために不正なファイルパスを使用
        invalid_file = "non_existent_file.pmx"
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }
        
        # インポートは失敗するはず
        result = import_mmd_file(invalid_file, options=options)
        self.assertIsNone(result)
        
        # namespaceが残っていないことを確認
        namespaces = NamespaceUtils.list_model_namespaces()
        self.assertEqual(len(namespaces), 0)

    def test_namespace_object_access(self):
        """namespace内のオブジェクトへのアクセス"""
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }
        
        if os.path.exists(self.pmx_file):
            root_node = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root_node)
            
            # namespace内のオブジェクトを検索
            namespace = NamespaceUtils.get_namespace_from_node(root_node)
            objects_in_ns = cmds.ls(f"{namespace}:*", type="transform")
            
            # オブジェクトが存在することを確認
            self.assertGreater(len(objects_in_ns), 0)
            
            # root_nodeが含まれていることを確認
            self.assertIn(root_node, objects_in_ns)


if __name__ == "__main__":
    unittest.main()