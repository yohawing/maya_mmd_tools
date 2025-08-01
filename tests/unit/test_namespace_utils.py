"""
NamespaceUtilsクラスのユニットテスト
"""

import unittest
from unittest.mock import patch, MagicMock, call

from mmd_tools.core.namespace_utils import NamespaceUtils


class TestNamespaceUtils(unittest.TestCase):
    """NamespaceUtilsクラスのテスト"""

    def test_generate_namespace_japanese(self):
        """日本語名からのnamespace生成テスト"""
        # テスト用のsanitize_text関数をモック
        with patch('mmd_tools.core.namespace_utils.sanitize_text') as mock_sanitize:
            mock_sanitize.return_value = "Hatsune_Miku"
            
            result = NamespaceUtils.generate_namespace("初音ミク")
            self.assertEqual(result, "Hatsune_Miku")
            mock_sanitize.assert_called_once_with("初音ミク")

    def test_generate_namespace_number_prefix(self):
        """数字で始まる名前の処理テスト"""
        with patch('mmd_tools.core.namespace_utils.sanitize_text') as mock_sanitize:
            mock_sanitize.return_value = "01_model"
            
            result = NamespaceUtils.generate_namespace("01_model")
            self.assertEqual(result, "Model_01_model")

    def test_generate_namespace_special_chars(self):
        """特殊文字を含む名前の処理テスト"""
        with patch('mmd_tools.core.namespace_utils.sanitize_text') as mock_sanitize:
            mock_sanitize.return_value = "model@test#2024"
            
            result = NamespaceUtils.generate_namespace("model@test#2024")
            self.assertEqual(result, "model_test_2024")

    def test_generate_namespace_empty(self):
        """空の名前の処理テスト"""
        with patch('mmd_tools.core.namespace_utils.sanitize_text') as mock_sanitize:
            mock_sanitize.return_value = ""
            
            result = NamespaceUtils.generate_namespace("")
            self.assertEqual(result, "MMDModel")

    def test_generate_namespace_underscores(self):
        """連続アンダースコアの処理テスト"""
        with patch('mmd_tools.core.namespace_utils.sanitize_text') as mock_sanitize:
            mock_sanitize.return_value = "model___test____name"
            
            result = NamespaceUtils.generate_namespace("model___test____name")
            self.assertEqual(result, "model_test_name")

    @patch('maya.cmds.namespace')
    def test_ensure_unique_namespace_not_exists(self, mock_namespace):
        """重複しないnamespaceの場合のテスト"""
        mock_namespace.exists.return_value = False
        
        result = NamespaceUtils.ensure_unique_namespace("TestModel")
        self.assertEqual(result, "TestModel")
        mock_namespace.exists.assert_called_once_with("TestModel")

    @patch('maya.cmds.namespace')
    def test_ensure_unique_namespace_exists(self, mock_namespace):
        """既存namespaceがある場合のテスト"""
        # 最初の2回はTrue（既存）、3回目はFalse（利用可能）
        mock_namespace.exists.side_effect = [True, True, False]
        
        result = NamespaceUtils.ensure_unique_namespace("TestModel")
        self.assertEqual(result, "TestModel_3")
        
        # 呼び出し順序を確認
        expected_calls = [
            call.exists("TestModel"),
            call.exists("TestModel_2"),
            call.exists("TestModel_3")
        ]
        self.assertEqual(mock_namespace.method_calls, expected_calls)

    @patch('maya.cmds.namespace')
    def test_create_namespace_success(self, mock_namespace):
        """namespace作成成功のテスト"""
        mock_namespace.exists.return_value = False
        
        result = NamespaceUtils.create_namespace("TestNamespace")
        self.assertTrue(result)
        mock_namespace.add.assert_called_once_with("TestNamespace")

    @patch('maya.cmds.namespace')
    def test_create_namespace_already_exists(self, mock_namespace):
        """既存namespace作成時のテスト"""
        mock_namespace.exists.return_value = True
        
        result = NamespaceUtils.create_namespace("TestNamespace")
        self.assertTrue(result)
        mock_namespace.add.assert_not_called()

    @patch('maya.cmds.namespace')
    def test_create_namespace_error(self, mock_namespace):
        """namespace作成エラーのテスト"""
        mock_namespace.exists.return_value = False
        mock_namespace.add.side_effect = RuntimeError("Invalid namespace")
        
        result = NamespaceUtils.create_namespace("Invalid@Name")
        self.assertFalse(result)

    @patch('maya.cmds.namespace')
    @patch('maya.cmds.namespaceInfo')
    def test_namespace_context_new(self, mock_info, mock_namespace):
        """新規namespace contextのテスト"""
        mock_info.currentNamespace.return_value = ":"
        mock_namespace.exists.return_value = False
        
        with NamespaceUtils.namespace_context("TestNS") as ns:
            self.assertEqual(ns, "TestNS")
            mock_namespace.add.assert_called_once_with("TestNS")
            mock_namespace.set.assert_any_call(":TestNS")
        
        # コンテキスト終了後に元に戻ることを確認
        mock_namespace.set.assert_called_with(":")

    @patch('maya.cmds.namespace')
    @patch('maya.cmds.namespaceInfo')
    def test_namespace_context_none(self, mock_info, mock_namespace):
        """Noneを渡した場合のcontext testのテスト"""
        with NamespaceUtils.namespace_context(None) as ns:
            self.assertIsNone(ns)
            mock_namespace.add.assert_not_called()
            mock_namespace.set.assert_not_called()

    @patch('maya.cmds.namespace')
    @patch('maya.cmds.ls')
    @patch('maya.cmds.delete')
    def test_cleanup_namespace_force(self, mock_delete, mock_ls, mock_namespace):
        """強制削除モードのクリーンアップテスト"""
        mock_namespace.exists.return_value = True
        mock_ls.return_value = ["TestNS:cube1", "TestNS:sphere1"]
        
        NamespaceUtils.cleanup_namespace("TestNS", force=True)
        
        mock_delete.assert_called_once_with(["TestNS:cube1", "TestNS:sphere1"])
        mock_namespace.removeNamespace.assert_called_once_with(
            "TestNS", mergeNamespaceWithParent=False
        )

    @patch('maya.cmds.namespace')
    @patch('maya.cmds.ls')
    def test_cleanup_namespace_merge(self, mock_ls, mock_namespace):
        """マージモードのクリーンアップテスト"""
        mock_namespace.exists.return_value = True
        mock_ls.return_value = ["TestNS:cube1"]
        
        NamespaceUtils.cleanup_namespace("TestNS", force=False)
        
        mock_namespace.removeNamespace.assert_called_once_with(
            "TestNS", mergeNamespaceWithParent=True
        )

    @patch('maya.cmds.namespaceInfo')
    def test_list_model_namespaces(self, mock_info):
        """モデルnamespace一覧取得のテスト"""
        mock_info.listOnlyNamespaces.return_value = [
            "UI", "shared", "Model1", "Character_A", ":nested"
        ]
        
        result = NamespaceUtils.list_model_namespaces()
        self.assertEqual(result, ["Model1", "Character_A"])

    def test_get_namespace_from_node(self):
        """ノード名からnamespace取得のテスト"""
        # 通常のnamespace付きノード
        self.assertEqual(
            NamespaceUtils.get_namespace_from_node("Model1:joint1"),
            "Model1"
        )
        
        # ネストしたnamespace
        self.assertEqual(
            NamespaceUtils.get_namespace_from_node("Project:Model1:joint1"),
            "Project:Model1"
        )
        
        # namespaceなし
        self.assertIsNone(
            NamespaceUtils.get_namespace_from_node("joint1")
        )
        
        # 空のケース
        self.assertIsNone(
            NamespaceUtils.get_namespace_from_node(":joint1")
        )


if __name__ == '__main__':
    unittest.main()