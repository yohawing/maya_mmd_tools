"""
NamespaceUtilsクラスのユニットテスト
"""

import unittest
from unittest.mock import patch, call

from mmd_tools.core import namespace_utils
from mmd_tools.core.namespace_utils import NamespaceUtils


def _message_templates(mock_log):
    # call[0] is args tuple (Py3.7-safe; _Call.args is 3.8+)
    return [call[0][0] for call in mock_log.call_args_list if call[0]]


class TestNamespaceUtils(unittest.TestCase):
    """NamespaceUtilsクラスのテスト"""

    def test_generate_namespace_japanese(self):
        """日本語名からのnamespace生成テスト"""
        # テスト用のsanitize_text関数をモック
        with patch("mmd_tools.core.namespace_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.return_value = "Hatsune_Miku"

            result = NamespaceUtils.generate_namespace("初音ミク")
            self.assertEqual(result, "Hatsune_Miku")
            mock_sanitize.assert_called_once_with("初音ミク")

    def test_generate_namespace_number_prefix(self):
        """数字で始まる名前の処理テスト"""
        with patch("mmd_tools.core.namespace_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.return_value = "01_model"

            result = NamespaceUtils.generate_namespace("01_model")
            self.assertEqual(result, "Model_01_model")

    def test_generate_namespace_special_chars(self):
        """特殊文字を含む名前の処理テスト"""
        with patch("mmd_tools.core.namespace_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.return_value = "model@test#2024"

            result = NamespaceUtils.generate_namespace("model@test#2024")
            self.assertEqual(result, "model_test_2024")

    def test_generate_namespace_empty(self):
        """空の名前の処理テスト"""
        with patch("mmd_tools.core.namespace_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.return_value = ""

            result = NamespaceUtils.generate_namespace("")
            self.assertEqual(result, "MMDModel")

    def test_generate_namespace_underscores(self):
        """連続アンダースコアの処理テスト"""
        with patch("mmd_tools.core.namespace_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.return_value = "model___test____name"

            result = NamespaceUtils.generate_namespace("model___test____name")
            self.assertEqual(result, "model_test_name")

    @patch("maya.cmds.namespaceInfo", return_value=":")
    @patch("maya.cmds.namespace")
    def test_ensure_unique_namespace_not_exists(self, mock_namespace, _mock_info):
        """重複しないnamespaceの場合のテスト"""
        # cmds.namespace(exists="TestModel") がFalseを返すように設定
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return False
            return None

        mock_namespace.side_effect = namespace_side_effect

        result = NamespaceUtils.ensure_unique_namespace("TestModel")
        self.assertEqual(result, "TestModel")
        mock_namespace.assert_any_call(exists="TestModel")

    @patch("maya.cmds.namespaceInfo", return_value=":")
    @patch("maya.cmds.namespace")
    def test_ensure_unique_namespace_exists(self, mock_namespace, _mock_info):
        """既存namespaceがある場合のテスト"""
        def namespace_side_effect(**kwargs):
            if kwargs.get("exists") == "TestModel":
                return True
            if kwargs.get("exists") == "TestModel_2":
                return False
            return None

        mock_namespace.side_effect = namespace_side_effect

        result = NamespaceUtils.ensure_unique_namespace("TestModel")
        self.assertEqual(result, "TestModel_2")

        # 呼び出し順序を確認
        expected_calls = [call(exists="TestModel"), call(exists="TestModel_2")]
        exists_calls = [entry for entry in mock_namespace.call_args_list if "exists" in entry[1]]
        self.assertEqual(exists_calls, expected_calls)

    @patch("mmd_tools.core.namespace_utils._MAX_NAMESPACE_SUFFIX_ATTEMPTS", 3)
    @patch("maya.cmds.namespaceInfo", return_value=":")
    @patch("maya.cmds.namespace")
    def test_ensure_unique_namespace_raises_after_suffix_limit(self, mock_namespace, _mock_info):
        """namespace衝突探索が無限に回らないことを確認する。"""
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return True
            return None

        mock_namespace.side_effect = namespace_side_effect

        with self.assertRaisesRegex(RuntimeError, "Could not find unique namespace"):
            NamespaceUtils.ensure_unique_namespace("TestModel")

        expected_calls = [
            call(exists="TestModel"),
            call(exists="TestModel_2"),
            call(exists="TestModel_3"),
        ]
        exists_calls = [entry for entry in mock_namespace.call_args_list if "exists" in entry[1]]
        self.assertEqual(exists_calls, expected_calls)

    @patch("maya.cmds.namespace")
    def test_create_namespace_success(self, mock_namespace):
        """namespace作成成功のテスト"""

        # cmds.namespace(exists="TestNamespace") がFalseを返すように設定
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return False
            elif "add" in kwargs:
                return None

        mock_namespace.side_effect = namespace_side_effect

        with patch.object(namespace_utils, "logger") as mock_logger:
            result = NamespaceUtils.create_namespace("TestNamespace")
        self.assertTrue(result)

        # 呼び出しを確認
        expected_calls = [call(exists="TestNamespace"), call(add="TestNamespace")]
        self.assertEqual(mock_namespace.call_args_list, expected_calls)

        # Create detail is DEBUG, not INFO.
        debug_messages = _message_templates(mock_logger.debug)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn("Created namespace: TestNamespace", debug_messages)
        self.assertNotIn("Created namespace: TestNamespace", info_messages)

    @patch("maya.cmds.namespace")
    def test_create_namespace_already_exists(self, mock_namespace):
        """既存namespace作成時のテスト"""
        # cmds.namespace(exists="TestNamespace") がTrueを返すように設定
        mock_namespace.return_value = True

        result = NamespaceUtils.create_namespace("TestNamespace")
        self.assertTrue(result)

        # existsチェックのみで、addは呼ばれない
        mock_namespace.assert_called_once_with(exists="TestNamespace")

    @patch("maya.cmds.namespace")
    def test_create_namespace_error(self, mock_namespace):
        """namespace作成エラーのテスト"""

        # existsチェックはFalse、addでエラーを発生させる
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return False
            elif "add" in kwargs:
                raise RuntimeError("Invalid namespace")

        mock_namespace.side_effect = namespace_side_effect

        result = NamespaceUtils.create_namespace("Invalid@Name")
        self.assertFalse(result)

    @patch("maya.cmds.namespaceInfo")
    @patch("maya.cmds.namespace")
    def test_namespace_context_new(self, mock_namespace, mock_info):
        """新規namespace contextのテスト"""
        # cmds.namespaceInfo(currentNamespace=True)が":"を返すように設定
        mock_info.return_value = ":"

        # namespace関数の動作を設定
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return False
            elif "add" in kwargs:
                return None
            elif "set" in kwargs:
                return None

        mock_namespace.side_effect = namespace_side_effect

        with NamespaceUtils.namespace_context("TestNS") as ns:
            self.assertEqual(ns, "TestNS")

        # 呼び出しを確認
        # namespaceInfoが呼ばれたことを確認
        mock_info.assert_called_once_with(currentNamespace=True)

        # namespaceの呼び出しを確認
        calls = mock_namespace.call_args_list

        # exists="TestNS"の呼び出しを確認
        self.assertIn(call(exists="TestNS"), calls)
        # add="TestNS"の呼び出しを確認
        self.assertIn(call(add="TestNS"), calls)
        # set=":TestNS"の呼び出しを確認
        self.assertIn(call(set=":TestNS"), calls)
        # 最後のset呼び出しが元のnamespaceに戻すことを確認
        # current_nsはmock_infoの戻り値":"なので、set=":"が呼ばれる
        self.assertIn(call(set=":"), calls)

    @patch("maya.cmds.namespace")
    def test_namespace_context_none(self, mock_namespace):
        """Noneを渡した場合のcontext testのテスト"""
        with NamespaceUtils.namespace_context(None) as ns:
            self.assertIsNone(ns)
            mock_namespace.add.assert_not_called()
            mock_namespace.set.assert_not_called()

    @patch("maya.cmds.namespace")
    def test_cleanup_namespace_force(self, mock_namespace):
        """強制削除モードのクリーンアップテスト"""

        # namespace関数の動作を設定
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return True
            elif "removeNamespace" in kwargs:
                return None

        mock_namespace.side_effect = namespace_side_effect
        with patch.object(namespace_utils, "logger") as mock_logger:
            NamespaceUtils.cleanup_namespace("TestNS", force=True)

        mock_namespace.assert_any_call(
            removeNamespace="TestNS",
            deleteNamespaceContent=True,
        )

        # Cleanup detail is DEBUG, not INFO; force-delete warning remains.
        debug_messages = _message_templates(mock_logger.debug)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn("Cleaned up namespace: TestNS", debug_messages)
        self.assertNotIn("Cleaned up namespace: TestNS", info_messages)

    @patch("maya.cmds.namespace")
    def test_cleanup_namespace_merge(self, mock_namespace):
        """マージモードのクリーンアップテスト"""

        # namespace関数の動作を設定
        def namespace_side_effect(**kwargs):
            if "exists" in kwargs:
                return True
            elif "removeNamespace" in kwargs:
                return None

        mock_namespace.side_effect = namespace_side_effect
        with patch.object(namespace_utils, "logger") as mock_logger:
            NamespaceUtils.cleanup_namespace("TestNS", force=False)

        mock_namespace.assert_any_call(removeNamespace="TestNS", mergeNamespaceWithParent=True)

        # Merge + cleanup details are DEBUG, not INFO.
        debug_messages = _message_templates(mock_logger.debug)
        info_messages = _message_templates(mock_logger.info)
        self.assertIn("Merging namespace: TestNS", debug_messages)
        self.assertIn("Cleaned up namespace: TestNS", debug_messages)
        self.assertNotIn("Merging namespace: TestNS", info_messages)
        self.assertNotIn("Cleaned up namespace: TestNS", info_messages)

    @patch("maya.cmds.namespaceInfo")
    def test_list_model_namespaces(self, mock_info):
        """モデルnamespace一覧取得のテスト"""
        # cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True)の戻り値を設定
        mock_info.return_value = ["UI", "shared", "Model1", "Character_A", ":nested"]

        result = NamespaceUtils.list_model_namespaces()
        self.assertEqual(result, ["Model1", "Character_A"])
        mock_info.assert_called_once_with(listOnlyNamespaces=True, recurse=True)

    def test_get_namespace_from_node(self):
        """ノード名からnamespace取得のテスト"""
        # 通常のnamespace付きノード
        self.assertEqual(NamespaceUtils.get_namespace_from_node("Model1:joint1"), "Model1")

        # ネストしたnamespace
        self.assertEqual(NamespaceUtils.get_namespace_from_node("Project:Model1:joint1"), "Project:Model1")

        # namespaceなし
        self.assertIsNone(NamespaceUtils.get_namespace_from_node("joint1"))

        # 空のケース
        self.assertIsNone(NamespaceUtils.get_namespace_from_node(":joint1"))

        # フル DAG パスでは leaf node の namespace を返す
        self.assertEqual(NamespaceUtils.get_namespace_from_node("|root|Model1:joint1"), "Model1")


if __name__ == "__main__":
    unittest.main()
