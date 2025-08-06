"""
UI翻訳管理クラス

UIの多言語対応を管理するシングルトンクラス。
JSONファイルから翻訳データを読み込み、現在の言語設定に基づいて適切な文字列を返す。
"""

import json
import os
from typing import Dict, Optional

from mmd_tools.core.logger import get_logger

logger = get_logger(__name__)


class UITranslator:
    """UI翻訳を管理するシングルトンクラス"""

    _instance = None

    # サポートされている言語
    SUPPORTED_LANGUAGES = {
        "ja": "日本語",
        "en": "English",
        "zh-TW": "繁體中文",
        "zh-CN": "简体中文",
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._translations: Dict[str, Dict] = {}
            self._current_language = "ja"  # デフォルトは日本語
            self._load_translations()
            self._initialized = True

    @classmethod
    def instance(cls) -> "UITranslator":
        """シングルトンインスタンスを取得"""
        return cls()

    def _get_translations_dir(self) -> str:
        """翻訳ファイルのディレクトリパスを取得"""
        # 現在のファイルのディレクトリ（translations/translator.py）
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # translationsディレクトリは現在のディレクトリ
        return current_dir

    def _load_translations(self):
        """翻訳ファイルを読み込み"""
        translations_dir = self._get_translations_dir()

        if not os.path.exists(translations_dir):
            logger.warning(f"翻訳ディレクトリが見つかりません: {translations_dir}")
            return

        for lang_code in self.SUPPORTED_LANGUAGES:
            # zh-TW -> zh_tw.json のように変換
            filename = f"{lang_code.lower().replace('-', '_')}.json"
            filepath = os.path.join(translations_dir, filename)

            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        self._translations[lang_code] = json.load(f)
                    logger.debug(f"翻訳ファイルを読み込みました: {filename}")
                except Exception as e:
                    logger.error(f"翻訳ファイルの読み込みに失敗しました {filename}: {e}")
                    self._translations[lang_code] = {}
            else:
                logger.warning(f"翻訳ファイルが見つかりません: {filepath}")
                self._translations[lang_code] = {}

    def set_language(self, language: str):
        """現在の言語を設定

        Args:
            language: 言語コード (ja, en, zh-TW, zh-CN)
        """
        if language in self.SUPPORTED_LANGUAGES:
            self._current_language = language
            logger.info(f"言語を {self.SUPPORTED_LANGUAGES[language]} に設定しました")
        else:
            logger.warning(f"サポートされていない言語です: {language}")

    def get_language(self) -> str:
        """現在の言語コードを取得"""
        return self._current_language

    def translate(self, key: str, category: Optional[str] = None) -> str:
        """キーから翻訳を取得

        Args:
            key: 翻訳キー
            category: カテゴリ（指定しない場合はドット記法で解釈）

        Returns:
            翻訳された文字列。見つからない場合は英語、それも見つからない場合はキーをそのまま返す

        Examples:
            translate("import", "buttons") -> "インポート"
            translate("buttons.import") -> "インポート"
        """
        # 現在の言語の翻訳データを取得
        current_translations = self._translations.get(self._current_language, {})

        # カテゴリが指定されている場合
        if category:
            category_data = current_translations.get(category, {})
            if key in category_data:
                return category_data[key]
        else:
            # ドット記法の解析（例: "buttons.import"）
            parts = key.split(".")
            data = current_translations

            for part in parts:
                if isinstance(data, dict) and part in data:
                    data = data[part]
                else:
                    data = None
                    break

            if data and isinstance(data, str):
                return data

        # フォールバック: 英語
        if self._current_language != "en":
            en_translations = self._translations.get("en", {})

            if category:
                category_data = en_translations.get(category, {})
                if key in category_data:
                    return category_data[key]
            else:
                parts = key.split(".")
                data = en_translations

                for part in parts:
                    if isinstance(data, dict) and part in data:
                        data = data[part]
                    else:
                        data = None
                        break

                if data and isinstance(data, str):
                    return data

        # 最終フォールバック: キーをそのまま返す
        logger.warning(f"翻訳が見つかりません: {key} (category: {category})")
        return key

    def get_supported_languages(self) -> Dict[str, str]:
        """サポートされている言語のリストを取得

        Returns:
            言語コードと表示名の辞書
        """
        return self.SUPPORTED_LANGUAGES.copy()

    def reload_translations(self):
        """翻訳ファイルを再読み込み"""
        self._translations.clear()
        self._load_translations()
        logger.info("翻訳ファイルを再読み込みしました")
