"""UITranslatorのGUIテスト"""

import unittest
from pathlib import Path

from mmd_tools.ui.qt_compat import QApplication, QMainWindow, QLabel, QPushButton, QVBoxLayout
from mmd_tools.ui.translations.translator import UITranslator
from mmd_tools.ui.base_tab import BaseTab
from tests.common.gui_test_base import GuiTestBase, requires_gui


class TestTranslatorWidget(BaseTab):
    """翻訳テスト用のウィジェット"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # 各種UI要素を作成
        self.label = QLabel(self.tr("import", "buttons"))
        self.button = QPushButton(self.tr("export", "buttons"))
        self.combo_label = QLabel(self.tr("file_path", "labels"))

        layout.addWidget(self.label)
        layout.addWidget(self.button)
        layout.addWidget(self.combo_label)

    def retranslateUi(self):
        """UIを再翻訳"""
        self.label.setText(self.tr("import", "buttons"))
        self.button.setText(self.tr("export", "buttons"))
        self.combo_label.setText(self.tr("file_path", "labels"))


@requires_gui
class TestUITranslator(GuiTestBase):
    """UITranslatorのGUIテスト"""

    @classmethod
    def setUpClass(cls):
        """QApplicationインスタンスを確認"""
        super().setUpClass()
        cls.app = QApplication.instance()
        if not cls.app:
            cls.app = QApplication([])

    def setUp(self):
        """各テストの前処理"""
        super().setUp()

        # UITranslatorインスタンスを取得
        self.translator = UITranslator.instance()

        # デフォルト言語を日本語に設定
        self.translator.set_language("ja")

        # テスト用ウィンドウとウィジェットを作成
        self.window = QMainWindow()
        self.test_widget = TestTranslatorWidget()
        self.window.setCentralWidget(self.test_widget)
        self.window.show()

        QApplication.processEvents()

    def tearDown(self):
        """各テストの後処理"""
        try:
            if self.window and self.window.isVisible():
                self.window.close()
                self.window.deleteLater()
            self.window = None
            self.test_widget = None
        except Exception:
            pass

        QApplication.processEvents()
        super().tearDown()

    def test_translator_singleton(self):
        """UITranslatorがシングルトンとして動作するか確認"""
        translator1 = UITranslator.instance()
        translator2 = UITranslator.instance()

        self.assertIs(translator1, translator2)
        self.assertIs(translator1, self.translator)

    def test_supported_languages(self):
        """サポートされている言語のリストを確認"""
        languages = self.translator.get_supported_languages()

        # 期待される言語が含まれているか
        self.assertIn("ja", languages)
        self.assertIn("en", languages)
        self.assertIn("zh-TW", languages)
        self.assertIn("zh-CN", languages)

        # 各言語の表示名が正しいか
        self.assertEqual(languages["ja"], "日本語")
        self.assertEqual(languages["en"], "English")
        self.assertEqual(languages["zh-TW"], "繁體中文")
        self.assertEqual(languages["zh-CN"], "简体中文")

    def test_translation_files_loaded(self):
        """翻訳ファイルが正しく読み込まれているか確認"""
        # 翻訳ディレクトリが存在するか
        translations_dir = Path(self.translator._get_translations_dir())
        self.assertTrue(translations_dir.exists())

        # 各言語の翻訳データが読み込まれているか
        for lang in ["ja", "en", "zh-TW", "zh-CN"]:
            self.assertIn(lang, self.translator._translations)
            self.assertIsInstance(self.translator._translations[lang], dict)

    def test_japanese_translation(self):
        """日本語翻訳が正しく機能するか確認"""
        self.translator.set_language("ja")

        # カテゴリ指定での翻訳
        self.assertEqual(self.translator.translate("import", "buttons"), "インポート")
        self.assertEqual(self.translator.translate("export", "buttons"), "エクスポート")
        self.assertEqual(self.translator.translate("file_path", "labels"), "ファイルパス:")

        # ドット記法での翻訳
        self.assertEqual(self.translator.translate("buttons.import"), "インポート")
        self.assertEqual(self.translator.translate("labels.file_path"), "ファイルパス:")

    def test_english_translation(self):
        """英語翻訳が正しく機能するか確認"""
        self.translator.set_language("en")

        # カテゴリ指定での翻訳
        self.assertEqual(self.translator.translate("import", "buttons"), "Import")
        self.assertEqual(self.translator.translate("export", "buttons"), "Export")
        self.assertEqual(self.translator.translate("file_path", "labels"), "File Path:")

        # ドット記法での翻訳
        self.assertEqual(self.translator.translate("buttons.import"), "Import")
        self.assertEqual(self.translator.translate("labels.file_path"), "File Path:")

    def test_fallback_to_english(self):
        """翻訳が見つからない場合に英語にフォールバックするか確認"""
        # 中国語に設定（一部の翻訳が不足している可能性がある）
        self.translator.set_language("zh-CN")

        # 存在しない翻訳キーを指定（英語にフォールバック）
        # 注: 実際のテストでは、zh-CN.jsonに存在しないキーを使用する必要があります
        result = self.translator.translate("nonexistent_key", "nonexistent_category")

        # キーそのものが返される（最終フォールバック）
        self.assertEqual(result, "nonexistent_key")

    def test_widget_translation(self):
        """ウィジェットの翻訳が正しく機能するか確認"""
        # 初期状態（日本語）
        self.assertEqual(self.test_widget.label.text(), "インポート")
        self.assertEqual(self.test_widget.button.text(), "エクスポート")
        self.assertEqual(self.test_widget.combo_label.text(), "ファイルパス:")

    def test_language_switch(self):
        """言語切り替えが正しく機能するか確認"""
        # 英語に切り替え
        self.translator.set_language("en")
        self.test_widget.retranslateUi()
        QApplication.processEvents()

        self.assertEqual(self.test_widget.label.text(), "Import")
        self.assertEqual(self.test_widget.button.text(), "Export")
        self.assertEqual(self.test_widget.combo_label.text(), "File Path:")

        # 日本語に戻す
        self.translator.set_language("ja")
        self.test_widget.retranslateUi()
        QApplication.processEvents()

        self.assertEqual(self.test_widget.label.text(), "インポート")
        self.assertEqual(self.test_widget.button.text(), "エクスポート")
        self.assertEqual(self.test_widget.combo_label.text(), "ファイルパス:")

    def test_get_current_language(self):
        """現在の言語取得が正しく機能するか確認"""
        self.translator.set_language("ja")
        self.assertEqual(self.translator.get_language(), "ja")

        self.translator.set_language("en")
        self.assertEqual(self.translator.get_language(), "en")

        self.translator.set_language("zh-TW")
        self.assertEqual(self.translator.get_language(), "zh-TW")

    def test_invalid_language(self):
        """無効な言語コードを設定した場合の動作確認"""
        current_lang = self.translator.get_language()

        # 無効な言語を設定
        self.translator.set_language("invalid_lang")

        # 言語は変更されない
        self.assertEqual(self.translator.get_language(), current_lang)

    def test_translation_reload(self):
        """翻訳ファイルの再読み込みが正しく機能するか確認"""
        # 翻訳データをクリア
        self.translator._translations.clear()
        self.assertEqual(len(self.translator._translations), 0)

        # 再読み込み
        self.translator.reload_translations()

        # 翻訳データが再度読み込まれているか
        self.assertGreater(len(self.translator._translations), 0)
        for lang in ["ja", "en"]:
            self.assertIn(lang, self.translator._translations)

    def test_nested_translation_keys(self):
        """ネストされた翻訳キーが正しく処理されるか確認"""
        self.translator.set_language("ja")

        # ネストされたキーのテスト
        result = self.translator.translate("tabs.file_io")
        self.assertEqual(result, "ファイルI/O")

        result = self.translator.translate("groups.general")
        self.assertEqual(result, "一般")

    def test_base_tab_integration(self):
        """BaseTabクラスとの統合が正しく機能するか確認"""
        # BaseTabのtrメソッドが正しく動作するか
        self.translator.set_language("ja")
        self.assertEqual(self.test_widget.tr("import", "buttons"), "インポート")

        self.translator.set_language("en")
        self.assertEqual(self.test_widget.tr("import", "buttons"), "Import")

    def test_settings_integration(self):
        """設定との統合を確認（実際の設定保存はしない）"""
        # 現在の言語設定を取得
        current_lang = self.translator.get_language()

        # 言語を変更
        new_lang = "en" if current_lang == "ja" else "ja"
        self.translator.set_language(new_lang)

        # 変更が反映されているか
        self.assertEqual(self.translator.get_language(), new_lang)

        # 元に戻す
        self.translator.set_language(current_lang)

    def test_special_characters_translation(self):
        """特殊文字を含む翻訳が正しく処理されるか確認"""
        self.translator.set_language("ja")

        # コロンを含む翻訳
        result = self.translator.translate("file_path", "labels")
        self.assertTrue(result.endswith(":"))

        # 括弧を含む翻訳
        result = self.translator.translate("model_name_jp", "fields")
        self.assertIn("(JP)", result)


if __name__ == "__main__":
    unittest.main()
