"""
mmd_tools.core.settings モジュールのユニットテスト (Maya 非依存)

Settings クラスの辞書アクセス・ドット記法 get/set・flatten・シングルトン・
reset・デフォルト値読み込みを検証する。Maya が存在しない環境で実行可能。
"""

import sys
import os
import unittest

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from mmd_tools.core.settings import Settings, get_settings


def _reset_singleton():
    """Settings シングルトンをリセットするヘルパー関数。

    各テストが独立した状態から始まれるよう、シングルトンインスタンスを破棄する。
    """
    Settings._instance = None


class TestSettingsSingleton(unittest.TestCase):
    """Settings のシングルトンパターンのテスト"""

    def setUp(self):
        _reset_singleton()

    def tearDown(self):
        _reset_singleton()

    def test_same_instance_returned(self):
        """2 回インスタンス化しても同じオブジェクトが返ることを確認する。"""
        s1 = Settings()
        s2 = Settings()
        self.assertIs(s1, s2)

    def test_get_settings_returns_settings_instance(self):
        """get_settings() が Settings インスタンスを返すことを確認する。"""
        s = get_settings()
        self.assertIsInstance(s, Settings)

    def test_get_settings_same_instance(self):
        """get_settings() の連続呼び出しが同じインスタンスを返すことを確認する。"""
        s1 = get_settings()
        s2 = get_settings()
        self.assertIs(s1, s2)


class TestSettingsDefaults(unittest.TestCase):
    """Settings のデフォルト値読み込みテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_defaults_loaded(self):
        """デフォルト設定が空でないことを確認する（JSON ファイルが存在する場合）。"""
        # default_settings.json が存在すれば data に値が入るはず
        self.assertIsInstance(self.settings.data, dict)

    def test_import_section_exists(self):
        """'import' キーがデフォルト設定に含まれることを確認する。"""
        self.assertIn("import", self.settings.data)

    def test_logging_section_exists(self):
        """'logging' キーがデフォルト設定に含まれることを確認する。"""
        self.assertIn("logging", self.settings.data)

    def test_scale_factor_default(self):
        """scale_factor のデフォルト値が 1.0 であることを確認する。"""
        value = self.settings.get("import.general.scale_factor", None)
        self.assertIsNotNone(value)
        self.assertAlmostEqual(float(value), 1.0)

    def test_create_mmd_shaders_default(self):
        """MMDシェーダー作成が既定で有効なことを確認する。"""
        self.assertTrue(self.settings.get("import.model.create_mmd_shaders", False))

    def test_logging_enabled_default(self):
        """logging.enabled のデフォルト値が bool であることを確認する。"""
        value = self.settings.get("logging.enabled", None)
        self.assertIsNotNone(value)
        self.assertIsInstance(value, bool)


class TestSettingsGet(unittest.TestCase):
    """Settings.get() のドット記法アクセスのテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_get_top_level_key(self):
        """トップレベルキーにアクセスできることを確認する。"""
        value = self.settings.get("import")
        self.assertIsInstance(value, dict)

    def test_get_nested_key(self):
        """ネストされたキーにドット記法でアクセスできることを確認する。"""
        value = self.settings.get("import.general.scale_factor")
        self.assertIsNotNone(value)

    def test_get_missing_key_returns_default(self):
        """存在しないキーに対してデフォルト値が返ることを確認する。"""
        result = self.settings.get("nonexistent.key.path", "fallback")
        self.assertEqual(result, "fallback")

    def test_get_missing_key_returns_none_by_default(self):
        """default 引数未指定時に None が返ることを確認する。"""
        result = self.settings.get("no.such.key")
        self.assertIsNone(result)

    def test_get_deeply_nested_key(self):
        """3 段階以上のネストに対してアクセスできることを確認する。"""
        value = self.settings.get("import.general.scale_factor")
        self.assertIsNotNone(value)


class TestSettingsSet(unittest.TestCase):
    """Settings.set() のドット記法設定のテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_set_existing_key(self):
        """既存キーの値を変更できることを確認する。"""
        self.settings.set("import.general.scale_factor", 2.0)
        value = self.settings.get("import.general.scale_factor")
        self.assertAlmostEqual(float(value), 2.0)

    def test_set_creates_new_key(self):
        """存在しないキーを新規作成できることを確認する。"""
        self.settings.set("custom.new_key", "new_value")
        value = self.settings.get("custom.new_key")
        self.assertEqual(value, "new_value")

    def test_set_bool_value(self):
        """bool 値を設定・取得できることを確認する。"""
        self.settings.set("import.model.import_models", False)
        value = self.settings.get("import.model.import_models")
        self.assertFalse(value)

    def test_set_int_value(self):
        """int 値を設定・取得できることを確認する。"""
        self.settings.set("import.animation.animation_start_frame", 10)
        value = self.settings.get("import.animation.animation_start_frame")
        self.assertEqual(value, 10)

    def test_set_string_value(self):
        """文字列値を設定・取得できることを確認する。"""
        self.settings.set("logging.level", "DEBUG")
        value = self.settings.get("logging.level")
        self.assertEqual(value, "DEBUG")


class TestSettingsFlattenDict(unittest.TestCase):
    """Settings._flatten_dict() の内部メソッドテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_flatten_simple_dict(self):
        """単純な辞書をフラット化できることを確認する。"""
        d = {"a": 1, "b": 2}
        result = self.settings._flatten_dict(d)
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"], 2)

    def test_flatten_nested_dict(self):
        """ネストした辞書をフラット化できることを確認する。"""
        d = {"outer": {"inner": 42}}
        result = self.settings._flatten_dict(d)
        self.assertIn("outer::inner", result)
        self.assertEqual(result["outer::inner"], 42)

    def test_flatten_ignores_comment_keys(self):
        """アンダースコア始まりのキーが無視されることを確認する。"""
        d = {"_comment": "this is a comment", "real_key": "value"}
        result = self.settings._flatten_dict(d)
        self.assertNotIn("_comment", result)
        self.assertIn("real_key", result)

    def test_flatten_deeply_nested(self):
        """3 段階ネストのフラット化を確認する。"""
        d = {"a": {"b": {"c": "deep"}}}
        result = self.settings._flatten_dict(d)
        self.assertIn("a::b::c", result)
        self.assertEqual(result["a::b::c"], "deep")


class TestSettingsReset(unittest.TestCase):
    """Settings.reset() のテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_reset_restores_defaults(self):
        """reset() でデフォルト値に戻ることを確認する。"""
        original = self.settings.get("import.general.scale_factor")
        self.settings.set("import.general.scale_factor", 99.0)
        self.settings.reset()
        restored = self.settings.get("import.general.scale_factor")
        self.assertEqual(restored, original)

    def test_reset_removes_custom_keys(self):
        """reset() でカスタムキーが削除されることを確認する。"""
        self.settings.set("custom.test_key", "test_value")
        self.settings.reset()
        # リセット後はカスタムキーが存在しない
        value = self.settings.get("custom.test_key")
        self.assertIsNone(value)


class TestSettingsOptionVarKey(unittest.TestCase):
    """Settings.get_option_var_key() のテスト"""

    def setUp(self):
        _reset_singleton()
        self.settings = Settings()

    def tearDown(self):
        _reset_singleton()

    def test_option_var_key_has_prefix(self):
        """optionVar キーがプレフィックス 'mmd_tools_' を持つことを確認する。"""
        key = self.settings.get_option_var_key("my_key")
        self.assertTrue(key.startswith("mmd_tools_"))

    def test_option_var_key_contains_original_key(self):
        """optionVar キーに元のキー名が含まれることを確認する。"""
        key = self.settings.get_option_var_key("some.key")
        self.assertIn("some.key", key)


class TestSettingsProxy(unittest.TestCase):
    """SettingsProxy の基本動作テスト"""

    def setUp(self):
        _reset_singleton()

    def tearDown(self):
        _reset_singleton()

    def test_proxy_get_delegates_to_settings(self):
        """SettingsProxy.get() が Settings.get() に委譲することを確認する。"""
        from mmd_tools.core.settings import settings as proxy
        value = proxy.get("import.general.scale_factor")
        self.assertIsNotNone(value)

    def test_proxy_set_delegates_to_settings(self):
        """SettingsProxy.set() が Settings.set() に委譲することを確認する。"""
        from mmd_tools.core.settings import settings as proxy
        proxy.set("logging.level", "WARNING")
        value = proxy.get("logging.level")
        self.assertEqual(value, "WARNING")


if __name__ == "__main__":
    unittest.main()
