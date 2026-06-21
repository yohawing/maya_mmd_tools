"""
mmd_tools.core.exceptions モジュールのユニットテスト

MMDParseException の基本動作（継承関係・メッセージ保持・raise/catch）を検証する。
"""

import sys
import os
import unittest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mmd_tools.core.exceptions import MMDParseException


class TestMMDParseException(unittest.TestCase):
    """MMDParseException の基本動作テスト"""

    # -----------------------------------------------------------------------
    # 継承関係
    # -----------------------------------------------------------------------

    def test_is_exception_subclass(self):
        """MMDParseException が Exception のサブクラスであることを確認する。"""
        self.assertTrue(issubclass(MMDParseException, Exception))

    def test_is_not_base_exception_only(self):
        """MMDParseException が BaseException の直接サブクラスではないことを確認する。"""
        # Exception を継承しているので、isinstance(e, Exception) が True になる
        exc = MMDParseException("test")
        self.assertIsInstance(exc, Exception)

    # -----------------------------------------------------------------------
    # メッセージ保持
    # -----------------------------------------------------------------------

    def test_message_stored(self):
        """コンストラクタで渡したメッセージが args[0] として保持されることを確認する。"""
        msg = "Unsupported MMD file format: test.xyz"
        exc = MMDParseException(msg)
        self.assertEqual(exc.args[0], msg)

    def test_str_representation(self):
        """str(exc) がメッセージを含むことを確認する。"""
        msg = "parse error"
        exc = MMDParseException(msg)
        self.assertIn(msg, str(exc))

    def test_empty_message(self):
        """メッセージ無しで作成できることを確認する（境界ケース）。"""
        exc = MMDParseException()
        self.assertEqual(exc.args, ())

    def test_multiple_args(self):
        """複数引数を渡せることを確認する（境界ケース）。"""
        exc = MMDParseException("error", 42, {"key": "value"})
        self.assertEqual(len(exc.args), 3)
        self.assertEqual(exc.args[1], 42)

    # -----------------------------------------------------------------------
    # raise / catch
    # -----------------------------------------------------------------------

    def test_raise_and_catch_as_mmd_parse_exception(self):
        """MMDParseException として raise & catch できることを確認する。"""
        with self.assertRaises(MMDParseException):
            raise MMDParseException("test raise")

    def test_raise_and_catch_as_exception(self):
        """親クラス Exception として catch できることを確認する。"""
        with self.assertRaises(Exception):
            raise MMDParseException("caught as Exception")

    def test_not_caught_as_other_exception(self):
        """無関係な例外クラスでは catch されないことを確認する。"""
        with self.assertRaises(MMDParseException):
            try:
                raise MMDParseException("should not be caught as ValueError")
            except ValueError:
                pass  # ここには到達しない
            except MMDParseException:
                raise  # 正しい経路

    def test_raise_with_real_world_message(self):
        """実際の使用パターン（ファイルパス付きメッセージ）での raise を検証する。"""
        file_path = "/path/to/model.unknown"
        with self.assertRaises(MMDParseException) as ctx:
            raise MMDParseException(f"Unsupported MMD file format: {file_path}")
        self.assertIn(file_path, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
