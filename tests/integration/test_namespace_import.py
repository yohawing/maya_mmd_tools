"""
Namespace機能の統合テスト

複数モデルのインポートとnamespace管理をテストします。
"""

import os
import unittest

from maya import cmds

from mmd_tools.core import settings
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core.namespace_utils import NamespaceUtils


class TestNamespaceImport(unittest.TestCase):
    """Namespace付きインポートの統合テスト"""

    @classmethod
    def setUpClass(cls):
        """テストクラスのセットアップ"""
        # テストデータのパスを設定
        cls.test_data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        cls.pmx_file = os.path.join(cls.test_data_dir, "mmt_test_model.pmx")

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
            except Exception:
                pass

    def test_single_model_with_namespace(self):
        """単一モデルのnamespace付きインポート"""
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        # インポートオプション
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }

        root_node = import_mmd_file(self.pmx_file, options=options)
        self.assertIsNotNone(root_node)

        # namespaceが作成されていることを確認
        namespaces = NamespaceUtils.list_model_namespaces()
        self.assertGreater(len(namespaces), 0)

        # root_nodeがnamespace付きであることを確認
        self.assertIn(":", root_node)

    def test_multiple_models_unique_namespaces(self):
        """同じモデルを複数回インポートした際の連番付与"""
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        options = {
            "use_namespace": True,
            "scale": 1.0,
        }

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

    def test_split_mesh_import_from_existing_namespace_does_not_nest_namespace(self):
        """既存namespace内からの分割メッシュインポートでnamespaceを二重化しない。"""
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        existing_namespace = "MMT_TestModel"
        cmds.namespace(add=existing_namespace)
        cmds.namespace(set=f":{existing_namespace}")

        options = {
            "use_namespace": True,
            "separate_meshes_by_material": True,
            "create_mmd_shaders": False,
            "import_physics": False,
            "import_morphs": False,
            "setup_rig": False,
            "scale": 1.0,
        }

        try:
            root_node = import_mmd_file(self.pmx_file, options=options)
            self.assertIsNotNone(root_node)
            self.assertEqual(
                NamespaceUtils.get_namespace_from_node(root_node),
                f"{existing_namespace}_2",
            )

            current_namespace = cmds.namespaceInfo(currentNamespace=True)
            self.assertEqual(current_namespace.lstrip(":"), existing_namespace)

            mesh_shapes = cmds.listRelatives(
                root_node,
                allDescendents=True,
                type="mesh",
                fullPath=True,
            ) or []
            self.assertTrue(mesh_shapes)
            for shape in mesh_shapes:
                leaf_name = shape.rsplit("|", 1)[-1]
                self.assertEqual(leaf_name.count(":"), 1)
                self.assertEqual(len(cmds.ls(leaf_name, long=True) or []), 1)
                self.assertTrue(cmds.attributeQuery("doubleSided", node=shape, exists=True))
        finally:
            cmds.namespace(set=":")

    def test_namespace_disabled(self):
        """namespace無効時の動作確認"""
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        options = {
            "use_namespace": False,
            "scale": 1.0,
        }

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
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        # まずモデルをnamespace付きでインポート
        options = {
            "use_namespace": True,
            "scale": 1.0,
        }

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

        # インポートは例外で失敗するはず
        with self.assertRaises(MMDImportException):
            import_mmd_file(invalid_file, options=options)

        # namespaceが残っていないことを確認
        namespaces = NamespaceUtils.list_model_namespaces()
        self.assertEqual(len(namespaces), 0)

    def test_namespace_object_access(self):
        """namespace内のオブジェクトへのアクセス"""
        if not os.path.exists(self.pmx_file):
            self.skipTest(f"テストフィクスチャが見つかりません: {self.pmx_file}")

        options = {
            "use_namespace": True,
            "scale": 1.0,
        }

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
