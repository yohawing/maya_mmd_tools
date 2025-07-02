# -*- coding: utf-8 -*-
"""
UTF-8/UTF-16文字列をMaya安全なASCII文字列に変換する機能

MayaのASCII環境では日本語・中国語などの文字列の取り扱いに制限があるため、
辞書変換とBase64エンコードを組み合わせて安全に変換する。
"""

import base64
import json
import logging
import os
from typing import Dict, Set, List


class UnicodeToAsciiConverter:
    """
    Unicode文字列とMaya互換ASCII文字列の相互変換を行うクラス
    """

    def __init__(self, dictionary_path: str = None):
        """
        コンバーターを初期化
        
        Args:
            dictionary_path: 辞書ファイルのパス（指定しない場合はデフォルト辞書を使用）
        """
        self.logger = logging.getLogger(__name__)
        self.B64_PREFIX = "utfb64_"
        self._conversion_cache = {}
        self._restoration_cache = {}

        self._load_dictionary(dictionary_path)

    def _load_dictionary(self, dictionary_path: str = None):
        """
        辞書ファイルを読み込む
        
        Args:
            dictionary_path: 辞書ファイルのパス
        """
        if dictionary_path is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            dictionary_path = os.path.join(
                os.path.dirname(current_dir), 'config', 'unicode_dictionary.json'
            )

        try:
            if os.path.exists(dictionary_path):
                with open(dictionary_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                self.languages = data.get('languages', [])
                self.dictionary = data.get('dictionary', [])
                self.MAYA_INVALID_CHARS = data.get('maya_invalid_chars', {})

                self._build_translation_maps()

                self.logger.info(f"辞書ファイルを読み込みました: {dictionary_path}")
                self.logger.info(f"対応言語: {self.languages}")
                self.logger.info(f"辞書エントリ数: {len(self.dictionary)}")
            else:
                self._load_default_dictionary()
                self.logger.warning(f"辞書ファイルが見つかりません: {dictionary_path}")
                self.logger.info("デフォルト辞書を使用します")

        except Exception as e:
            self._load_default_dictionary()
            self.logger.error(f"辞書ファイルの読み込みに失敗しました: {e}")
            self.logger.info("デフォルト辞書を使用します")

    def _build_translation_maps(self):
        """翻訳・逆引きマップを構築する"""
        self.translation_maps = {lang: {} for lang in self.languages}
        self.reverse_maps = {lang: {} for lang in self.languages}

        if not self.languages:
            return

        # For each language, create a map from all other languages to it.
        for target_lang_index, target_lang in enumerate(self.languages):
            for entry in self.dictionary:
                if len(entry) <= target_lang_index:
                    continue
                target_word = entry[target_lang_index]
                if not target_word:
                    continue

                # Map all other words in the row to the target word
                for source_lang_index, source_word in enumerate(entry):
                    if source_lang_index == target_lang_index or not source_word:
                        continue
                    self.translation_maps[target_lang][source_word] = target_word

        # For reverse mapping, we need a primary language. Let's use Japanese.
        try:
            jp_index = self.languages.index('jp')
            for target_lang_index, target_lang in enumerate(self.languages):
                if target_lang == 'jp':
                    continue
                for entry in self.dictionary:
                    if len(entry) > jp_index and len(entry) > target_lang_index:
                        jp_word = entry[jp_index]
                        target_word = entry[target_lang_index]
                        if jp_word and target_word:
                            self.reverse_maps[target_lang][target_word] = jp_word
        except ValueError:
            self.logger.warning("'jp' が言語リストにないため、復元機能は辞書を使用しません。")

    def _load_default_dictionary(self):
        """
        デフォルト辞書を読み込む（フォールバック用）
        """
        self.languages = ["jp", "en"]
        self.dictionary = [
            ["全ての親", "master"],
            ["センター", "center"],
            ["左腕", "left_arm"],
            ["右腕", "right_arm"],
            ["頭", "head"],
            ["髪", "hair"],
            ["表情", "expression"]
        ]
        self.MAYA_INVALID_CHARS = {':': '_colon_', ' ': '_space_', '-': '_dash_', '.': '_dot_', '|': '_pipe_'}
        self._build_translation_maps()

    def convert(self, text: str, language: str = 'en') -> str:
        """
        Unicode文字列をMaya互換ASCII文字列に変換
        
        Args:
            text (str): 変換対象の文字列
            language (str): 変換先の言語
            
        Returns:
            str: Maya互換ASCII文字列
        """
        if not text:
            return text

        if language not in self.languages:
            self.logger.warning(f"言語 '{language}' は辞書にありません。Base64変換フォールバックを使用します。")
            return self._convert_internal(text, None)

        # キャッシュから確認
        if language in self._conversion_cache and text in self._conversion_cache[language]:
            return self._conversion_cache[language][text]

        result = self._convert_internal(text, language)

        # キャッシュに保存
        self._conversion_cache.setdefault(language, {})[text] = result
        return result

    def _convert_internal(self, text: str, language: str) -> str:
        """内部変換処理"""
        # ASCII専用文字列はそのまま
        if self.is_ascii_only(text):
            return self.maya_safe_name(text)

        # 辞書変換を優先
        if language and text in self.translation_maps.get(language, {}):
            return self.maya_safe_name(self.translation_maps[language][text])

        # Base64変換
        try:
            encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
            result = f"{self.B64_PREFIX}{encoded}"
            return self.maya_safe_name(result)
        except Exception as e:
            self.logger.error(f"Base64エンコードエラー: {text}, エラー: {e}")
            return f"ENCODE_ERROR_{hash(text) % 100000}"

    def restore(self, text: str, language: str = 'en') -> str:
        """
        Maya互換ASCII文字列をUnicode文字列に復元
        
        Args:
            text (str): 復元対象の文字列
            language (str): 変換元の言語
            
        Returns:
            str: 復元された文字列
        """
        if not text:
            return text

        if language not in self.languages:
            self.logger.warning(f"言語 '{language}' は辞書にありません。")
            return self._restore_internal(text, None)

        # キャッシュから確認
        if language in self._restoration_cache and text in self._restoration_cache[language]:
            return self._restoration_cache[language][text]

        result = self._restore_internal(text, language)

        # キャッシュに保存
        self._restoration_cache.setdefault(language, {})[text] = result
        return result

    def _restore_internal(self, text: str, language: str) -> str:
        """内部復元処理"""
        restored_text = self.restore_maya_chars(text)

        if restored_text.startswith(self.B64_PREFIX):
            encoded_part = restored_text[len(self.B64_PREFIX):]
            try:
                decoded_bytes = base64.b64decode(encoded_part)
                return decoded_bytes.decode('utf-8')
            except (base64.binascii.Error, UnicodeDecodeError) as e:
                self.logger.error(f"Base64復元エラー: {text}, エラー: {e}")
                return f"DECODE_ERROR_{text}"

        # 辞書復元
        if language and restored_text in self.reverse_maps.get(language, {}):
            return self.reverse_maps[language][restored_text]

        return restored_text

    def maya_safe_name(self, text: str) -> str:
        """Maya用に無効文字を置換"""
        result = text
        for invalid, replacement in self.MAYA_INVALID_CHARS.items():
            result = result.replace(invalid, replacement)
        return result

    def restore_maya_chars(self, text: str) -> str:
        """Maya無効文字の置換を元に戻す"""
        result = text
        maya_replacement_to_char = {v: k for k, v in self.MAYA_INVALID_CHARS.items()}
        for replacement, original in maya_replacement_to_char.items():
            result = result.replace(replacement, original)
        return result

    def is_ascii_only(self, text: str) -> bool:
        """ASCII専用文字列かチェック"""
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    def is_converted_base64(self, text: str) -> bool:
        """Base64変換された文字列かチェック"""
        restored_text = self.restore_maya_chars(text)
        return restored_text.startswith(self.B64_PREFIX)

    def is_dictionary_converted(self, text: str, language: str = 'en') -> bool:
        """辞書変換された文字列かチェック"""
        restored_text = self.restore_maya_chars(text)
        return language in self.reverse_maps and restored_text in self.reverse_maps[language]

    def get_encoding_type(self, text: str, language: str = 'en') -> str:
        """文字列のエンコード方式を判定"""
        if self.is_converted_base64(text):
            return 'base64'
        elif self.is_dictionary_converted(text, language):
            return 'dictionary'
        else:
            return 'original'


    def batch_convert(self, texts: list, language: str = 'en') -> Dict[str, str]:
        """複数の文字列を一括変換"""
        result = {}
        existing_names = set()

        for text in texts:
            converted = self.convert(text, language)
            result[text] = converted
            existing_names.add(converted)

        return result

    def batch_restore(self, texts: list, language: str = 'en') -> Dict[str, str]:
        """複数の文字列を一括復元"""
        return {text: self.restore(text, language) for text in texts}

    def get_conversion_stats(self, converted_names: list, language: str = 'en') -> Dict[str, int]:
        """変換統計の取得"""
        stats = {'dictionary': 0, 'base64': 0, 'original': 0, 'total': len(converted_names)}
        for name in converted_names:
            encoding_type = self.get_encoding_type(name, language)
            if encoding_type in stats:
                stats[encoding_type] += 1
        return stats

    def clear_cache(self):
        """変換キャッシュをクリア"""
        self._conversion_cache.clear()
        self._restoration_cache.clear()

    def add_dictionary_entry(self, entry: List[str]):
        """
        辞書エントリを追加
        
        Args:
            entry (List[str]): 全言語分の翻訳リスト
        """
        if len(entry) != len(self.languages):
            self.logger.error("追加するエントリの言語数が一致しません。")
            return

        self.dictionary.append(entry)
        self._build_translation_maps()  # マップを再構築
        self.clear_cache()

    def get_dictionary_info(self) -> Dict:
        """現在の辞書情報を取得"""
        return {
            'languages': self.languages,
            'total_entries': len(self.dictionary),
            'cache_size': sum(len(cache) for cache in self._conversion_cache.values()),
            'maya_invalid_chars': len(self.MAYA_INVALID_CHARS),
            'sample_entries': self.dictionary[:5]
        }


# グローバルインスタンス（シングルトンパターン）
_converter_instance = None


def get_converter() -> UnicodeToAsciiConverter:
    """
    コンバーターのグローバルインスタンスを取得
    
    Returns:
        UnicodeToAsciiConverter: コンバーターインスタンス
    """
    global _converter_instance
    if _converter_instance is None:
        _converter_instance = UnicodeToAsciiConverter()
    return _converter_instance
