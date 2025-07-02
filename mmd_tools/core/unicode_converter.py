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
from typing import Dict, Set


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
        # ログ設定
        self.logger = logging.getLogger(__name__)
        
        # 辞書ファイルの読み込み
        self._load_dictionary(dictionary_path)
        
        # 識別プレフィックス
        self.B64_PREFIX = "utfb64_"
        
        # 変換キャッシュ（パフォーマンス向上）
        self._conversion_cache = {}
        self._restoration_cache = {}
    
    def _load_dictionary(self, dictionary_path: str = None):
        """
        辞書ファイルを読み込む
        
        Args:
            dictionary_path: 辞書ファイルのパス
        """
        if dictionary_path is None:
            # デフォルト辞書ファイルのパスを取得
            current_dir = os.path.dirname(os.path.abspath(__file__))
            dictionary_path = os.path.join(
                os.path.dirname(current_dir), 'config', 'unicode_dictionary.json'
            )
        
        try:
            if os.path.exists(dictionary_path):
                with open(dictionary_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 辞書データを設定
                self.unicode_to_ascii = data.get('dictionary', {})
                self.MAYA_INVALID_CHARS = data.get('maya_invalid_chars', {})
                
                # 逆引き辞書を生成
                self.ascii_to_unicode = {v: k for k, v in self.unicode_to_ascii.items()}
                self.MAYA_REPLACEMENT_TO_CHAR = {v: k for k, v in self.MAYA_INVALID_CHARS.items()}
                
                self.logger.info(f"辞書ファイルを読み込みました: {dictionary_path}")
                self.logger.info(f"辞書エントリ数: {len(self.unicode_to_ascii)}")
            else:
                # デフォルト辞書にフォールバック
                self._load_default_dictionary()
                self.logger.warning(f"辞書ファイルが見つかりません: {dictionary_path}")
                self.logger.info("デフォルト辞書を使用します")
                
        except Exception as e:
            # エラー時はデフォルト辞書にフォールバック
            self._load_default_dictionary()
            self.logger.error(f"辞書ファイルの読み込みに失敗しました: {e}")
            self.logger.info("デフォルト辞書を使用します")
    
    def _load_default_dictionary(self):
        """
        デフォルト辞書を読み込む（フォールバック用）
        """
        # 最小限の辞書データ
        self.unicode_to_ascii = {
            "ボーン": "bone",
            "骨骼": "bone",
            "左腕": "left_arm",
            "左臂": "left_arm",
            "右腕": "right_arm",
            "右臂": "right_arm",
            "頭": "head",
            "头部": "head",
            "髪": "hair",
            "头发": "hair",
            "表情": "expression"
        }
        
        self.MAYA_INVALID_CHARS = {
            ':': '_colon_',
            ' ': '_space_',
            '-': '_dash_',
            '.': '_dot_',
            '|': '_pipe_'
        }
        
        # 逆引き辞書を生成
        self.ascii_to_unicode = {v: k for k, v in self.unicode_to_ascii.items()}
        self.MAYA_REPLACEMENT_TO_CHAR = {v: k for k, v in self.MAYA_INVALID_CHARS.items()}
    
    def convert(self, text: str) -> str:
        """
        Unicode文字列をMaya互換ASCII文字列に変換
        
        Args:
            text (str): 変換対象の文字列
            
        Returns:
            str: Maya互換ASCII文字列
        """
        if not text:
            return text
        
        # キャッシュから確認
        if text in self._conversion_cache:
            return self._conversion_cache[text]
        
        result = self._convert_internal(text)
        
        # キャッシュに保存
        self._conversion_cache[text] = result
        return result
    
    def _convert_internal(self, text: str) -> str:
        """内部変換処理"""
        # ASCII専用文字列はそのまま
        if self.is_ascii_only(text):
            return self.maya_safe_name(text)
        
        # 辞書変換を優先
        if text in self.unicode_to_ascii:
            return self.maya_safe_name(self.unicode_to_ascii[text])
        
        # Base64変換
        try:
            encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
            result = f"{self.B64_PREFIX}{encoded}"
            return self.maya_safe_name(result)
        except Exception as e:
            self.logger.error(f"Base64エンコードエラー: {text}, エラー: {e}")
            # フォールバック: エラー識別可能な形式
            return f"ENCODE_ERROR_{hash(text) % 100000}"
    
    def restore(self, text: str) -> str:
        """
        Maya互換ASCII文字列をUnicode文字列に復元
        
        Args:
            text (str): 復元対象の文字列
            
        Returns:
            str: 復元された文字列（Unicode または元の英語）
        """
        if not text:
            return text
        
        # キャッシュから確認
        if text in self._restoration_cache:
            return self._restoration_cache[text]
        
        result = self._restore_internal(text)
        
        # キャッシュに保存
        self._restoration_cache[text] = result
        return result
    
    def _restore_internal(self, text: str) -> str:
        """内部復元処理"""
        # Maya無効文字を元に戻す
        restored_text = self.restore_maya_chars(text)
        
        # Base64復元
        if restored_text.startswith(self.B64_PREFIX):
            encoded_part = restored_text[len(self.B64_PREFIX):]
            try:
                decoded_bytes = base64.b64decode(encoded_part)
                return decoded_bytes.decode('utf-8')
            except (base64.binascii.Error, UnicodeDecodeError) as e:
                self.logger.error(f"Base64復元エラー: {text}, エラー: {e}")
                return f"DECODE_ERROR_{text}"
        
        # 辞書復元
        if restored_text in self.ascii_to_unicode:
            return self.ascii_to_unicode[restored_text]
        
        # 元の文字列を返す
        return restored_text
    
    def maya_safe_name(self, text: str) -> str:
        """
        Maya用に無効文字を置換
        
        Args:
            text (str): 対象文字列
            
        Returns:
            str: Maya互換文字列
        """
        result = text
        for invalid, replacement in self.MAYA_INVALID_CHARS.items():
            result = result.replace(invalid, replacement)
        return result
    
    def restore_maya_chars(self, text: str) -> str:
        """
        Maya無効文字の置換を元に戻す
        
        Args:
            text (str): 対象文字列
            
        Returns:
            str: 元の文字列
        """
        result = text
        for replacement, original in self.MAYA_REPLACEMENT_TO_CHAR.items():
            result = result.replace(replacement, original)
        return result
    
    def is_ascii_only(self, text: str) -> bool:
        """
        ASCII専用文字列かチェック
        
        Args:
            text (str): チェック対象文字列
            
        Returns:
            bool: ASCII専用の場合True
        """
        try:
            text.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False
    
    def is_converted_base64(self, text: str) -> bool:
        """
        Base64変換された文字列かチェック
        
        Args:
            text (str): チェック対象文字列
            
        Returns:
            bool: Base64変換された場合True
        """
        restored_text = self.restore_maya_chars(text)
        return restored_text.startswith(self.B64_PREFIX)
    
    def is_dictionary_converted(self, text: str) -> bool:
        """
        辞書変換された文字列かチェック
        
        Args:
            text (str): チェック対象文字列
            
        Returns:
            bool: 辞書変換された場合True
        """
        restored_text = self.restore_maya_chars(text)
        return restored_text in self.ascii_to_unicode
    
    def get_encoding_type(self, text: str) -> str:
        """
        文字列のエンコード方式を判定
        
        Args:
            text (str): 判定対象文字列
            
        Returns:
            str: エンコード方式 ('base64', 'dictionary', 'original')
        """
        if self.is_converted_base64(text):
            return 'base64'
        elif self.is_dictionary_converted(text):
            return 'dictionary'
        else:
            return 'original'
    
    def ensure_unique_name(self, converted_name: str, existing_names: Set[str]) -> str:
        """
        Maya内での名前重複を防ぐ
        
        Args:
            converted_name (str): 変換後の名前
            existing_names (Set[str]): 既存の名前セット
            
        Returns:
            str: 重複しない名前
        """
        if converted_name not in existing_names:
            return converted_name
        
        counter = 1
        while f"{converted_name}_{counter}" in existing_names:
            counter += 1
        return f"{converted_name}_{counter}"
    
    def batch_convert(self, texts: list) -> Dict[str, str]:
        """
        複数の文字列を一括変換
        
        Args:
            texts (list): 変換対象文字列のリスト
            
        Returns:
            Dict[str, str]: 元の文字列 -> 変換後文字列のマッピング
        """
        result = {}
        existing_names = set()
        
        for text in texts:
            converted = self.convert(text)
            unique_name = self.ensure_unique_name(converted, existing_names)
            result[text] = unique_name
            existing_names.add(unique_name)
        
        return result
    
    def batch_restore(self, texts: list) -> Dict[str, str]:
        """
        複数の文字列を一括復元
        
        Args:
            texts (list): 復元対象文字列のリスト
            
        Returns:
            Dict[str, str]: 変換後文字列 -> 元の文字列のマッピング
        """
        return {text: self.restore(text) for text in texts}
    
    def get_conversion_stats(self, converted_names: list) -> Dict[str, int]:
        """
        変換統計の取得
        
        Args:
            converted_names (list): 変換後の名前リスト
            
        Returns:
            Dict[str, int]: 変換方式別の統計
        """
        stats = {
            'dictionary': 0,
            'base64': 0,
            'original': 0,
            'total': len(converted_names)
        }
        
        for name in converted_names:
            encoding_type = self.get_encoding_type(name)
            if encoding_type in stats:
                stats[encoding_type] += 1
        
        return stats
    
    def clear_cache(self):
        """変換キャッシュをクリア"""
        self._conversion_cache.clear()
        self._restoration_cache.clear()
    
    def add_dictionary_entry(self, unicode_text: str, ascii_text: str):
        """
        辞書エントリを追加
        
        Args:
            unicode_text (str): Unicode文字列
            ascii_text (str): ASCII文字列
        """
        self.unicode_to_ascii[unicode_text] = ascii_text
        self.ascii_to_unicode[ascii_text] = unicode_text
        # キャッシュをクリア（新しい辞書エントリが反映されるように）
        self.clear_cache()
    
    def get_dictionary_info(self) -> Dict:
        """
        現在の辞書情報を取得
        
        Returns:
            Dict: 辞書情報
        """
        return {
            'total_entries': len(self.unicode_to_ascii),
            'cache_size': len(self._conversion_cache),
            'maya_invalid_chars': len(self.MAYA_INVALID_CHARS),
            'sample_entries': dict(list(self.unicode_to_ascii.items())[:5])
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
