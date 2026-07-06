"""
UTF-8/UTF-16文字列をMaya安全なASCII文字列に変換する機能

MayaのASCII環境では日本語・中国語などの文字列の取り扱いに制限があるため、
辞書変換とハッシュフォールバックを組み合わせて安全に変換する。

設計方針：
- 辞書による固定変換（MMDでよく使われる文字）
- ハッシュによる機械的変換（一意性と作業性の担保）
- Prefix/Suffix処理（左右、数字の自動変換）
- Maya安全名保証（変換結果は必ずMayaで使用可能）
"""

import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional


class _DictionaryState:
    """辞書ロード結果を保持するコンテナ"""

    __slots__ = (
        "unicode_to_ascii",
        "ascii_to_unicode",
        "exact_match",
        "exact_match_reverse",
        "maya_invalid_chars",
        "prefix_map",
        "suffix_map",
        "languages",
    )

    def __init__(
        self,
        unicode_to_ascii: Dict[str, str],
        ascii_to_unicode: Dict[str, str],
        exact_match: Optional[Dict[str, str]],
        exact_match_reverse: Optional[Dict[str, str]],
        maya_invalid_chars: Dict[str, str],
        prefix_map: List[List[str]],
        suffix_map: List[List[str]],
        languages: List[str],
    ):
        self.unicode_to_ascii = unicode_to_ascii
        self.ascii_to_unicode = ascii_to_unicode
        self.exact_match = exact_match
        self.exact_match_reverse = exact_match_reverse
        self.maya_invalid_chars = maya_invalid_chars
        self.prefix_map = prefix_map
        self.suffix_map = suffix_map
        self.languages = languages


class _DictionaryLoader:
    """辞書ファイルの読み込みとデフォルト辞書の構築を担当する"""

    @staticmethod
    def default_dictionary_path() -> str:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(current_dir), "config", "unicode_dictionary.json")

    @staticmethod
    def process_list_dictionary(dictionary_list: List[List[str]]) -> Dict[str, str]:
        """
        リスト形式の辞書データを処理してunicode_to_asciiに変換

        Args:
            dictionary_list: [日本語, 英語, 中国語(簡体), 中国語(繁体)]のリスト
        """
        unicode_to_ascii: Dict[str, str] = {}
        for entry in dictionary_list:
            if len(entry) >= 2:
                japanese = entry[0]
                english = entry[1]
                unicode_to_ascii[japanese] = english

                if len(entry) >= 3:
                    chinese_simplified = entry[2]
                    unicode_to_ascii[chinese_simplified] = english
                if len(entry) >= 4:
                    chinese_traditional = entry[3]
                    unicode_to_ascii[chinese_traditional] = english
        return unicode_to_ascii

    @staticmethod
    def build_reverse_map(unicode_to_ascii: Dict[str, str]) -> Dict[str, str]:
        """逆引きマップを構築する"""
        ascii_to_unicode: Dict[str, str] = {}
        for unicode_text, ascii_text in unicode_to_ascii.items():
            if ascii_text not in ascii_to_unicode:
                ascii_to_unicode[ascii_text] = unicode_text
        return ascii_to_unicode

    @staticmethod
    def load_default_dictionary() -> _DictionaryState:
        """デフォルト辞書を読み込む（フォールバック用）"""
        dictionary_list = [
            ["なし", "none", "无", "無"],
            ["全て", "all", "全部", "全部"],
            ["全ての親", "master", "主骨骼", "主骨骼"],
            ["ボーン", "bone", "骨骼", "骨骼"],
            ["腕", "arm", "臂", "臂"],
            ["頭", "head", "头部", "頭部"],
            ["前髪", "bangs", "刘海", "瀏海"],
            ["横髪", "side_hair", "侧发", "側髮"],
            ["後髪", "back_hair", "后发", "後髮"],
            ["髪", "hair", "头发", "頭髮"],
            ["つまさき", "toe", "脚趾", "腳趾"],
            ["肩", "shoulder", "肩", "肩"],
            ["上半身", "spine", "上半身", "上半身"],
            ["元素", "element", "元素", "元素"],
        ]

        unicode_to_ascii = _DictionaryLoader.process_list_dictionary(dictionary_list)
        maya_invalid_chars = {"+": "_plus_", "|": "_pipe_"}
        prefix_map = [
            ["左", "left_", "左", "左"],
            ["右", "right_", "右", "右"],
        ]
        suffix_map = [
            ["先", "_end", "末端", "末端"],
            ["ＩＫ", "_ik", "IK", "IK"],
            ["捩", "_twist", "扭", "扭"],
        ]
        languages = ["jp", "en", "zh-cn", "zh-tw"]

        return _DictionaryState(
            unicode_to_ascii=unicode_to_ascii,
            ascii_to_unicode=_DictionaryLoader.build_reverse_map(unicode_to_ascii),
            exact_match=None,
            exact_match_reverse=None,
            maya_invalid_chars=maya_invalid_chars,
            prefix_map=prefix_map,
            suffix_map=suffix_map,
            languages=languages,
        )

    @staticmethod
    def load(dictionary_path: Optional[str], logger: logging.Logger) -> _DictionaryState:
        """
        辞書ファイルを読み込む

        Args:
            dictionary_path: 辞書ファイルのパス
            logger: ログ出力用
        """
        if dictionary_path is None:
            dictionary_path = _DictionaryLoader.default_dictionary_path()

        try:
            if os.path.exists(dictionary_path):
                with open(dictionary_path, encoding="utf-8") as f:
                    data = json.load(f)

                unicode_to_ascii: Dict[str, str]
                if "dictionary" in data and isinstance(data["dictionary"], dict):
                    unicode_to_ascii = data["dictionary"]
                    maya_invalid_chars = data.get("maya_invalid_chars", {})
                    prefix_map = data.get("prefix", [])
                    suffix_map = data.get("suffix", [])
                    languages = data.get("_meta", {}).get("languages", ["jp", "en", "zh-cn", "zh-tw"])
                elif "dictionary" in data and isinstance(data["dictionary"], list):
                    unicode_to_ascii = _DictionaryLoader.process_list_dictionary(data["dictionary"])
                    maya_invalid_chars = data.get("maya_invalid_chars", {})
                    prefix_map = data.get("prefix", [])
                    suffix_map = data.get("suffix", [])
                    languages = data.get("_meta", {}).get("languages", ["jp", "en", "zh-cn", "zh-tw"])

                exact_match: Optional[Dict[str, str]] = None
                exact_match_reverse: Optional[Dict[str, str]] = None
                if "exact_match" in data:
                    exact_match = data["exact_match"]
                    exact_match_reverse = {}
                    for unicode_text, ascii_text in exact_match.items():
                        exact_match_reverse[ascii_text] = unicode_text

                state = _DictionaryState(
                    unicode_to_ascii=unicode_to_ascii,
                    ascii_to_unicode=_DictionaryLoader.build_reverse_map(unicode_to_ascii),
                    exact_match=exact_match,
                    exact_match_reverse=exact_match_reverse,
                    maya_invalid_chars=maya_invalid_chars,
                    prefix_map=prefix_map,
                    suffix_map=suffix_map,
                    languages=languages,
                )

                logger.debug(f"Loaded dictionary file: {dictionary_path}")
                logger.debug(f"Dictionary entry count: {len(state.unicode_to_ascii)}")
                if state.exact_match:
                    logger.debug(f"Exact-match entry count: {len(state.exact_match)}")
                return state

            logger.warning(f"Dictionary file not found: {dictionary_path}")
            logger.info("Using default dictionary")
            return _DictionaryLoader.load_default_dictionary()

        except Exception as e:
            logger.error(f"Failed to load dictionary file: {e}")
            logger.info("Using default dictionary")
            return _DictionaryLoader.load_default_dictionary()


class UnicodeToAsciiConverter:
    """
    Unicode文字列とMaya互換ASCII文字列の相互変換を行うクラス

    設計ガイドに基づいた実装：
    - 辞書による固定変換（MMDでよく使われる文字）
    - ハッシュによる機械的変換（一意性と作業性の担保）
    - Prefix/Suffix処理（左右、数字の自動変換）
    """

    unicode_to_ascii: Dict[str, str] = {}
    ascii_to_unicode: Dict[str, str] = {}
    exact_match: Dict[str, str] = {}
    exact_match_reverse: Dict[str, str] = {}
    maya_invalid_chars: Dict[str, str] = {}
    prefix_map: List[List[str]] = []
    suffix_map: List[List[str]] = []

    def __init__(self, dictionary_path: Optional[str] = None):
        """
        コンバーターを初期化

        Args:
            dictionary_path: 辞書ファイルのパス（指定しない場合はデフォルト辞書を使用）
        """
        self.logger = logging.getLogger(__name__)
        self.HASH_PREFIX = "HASH"
        self.HASH_LENGTH = 8  # ハッシュの長さ（16^8 = 4,294,967,296通り）
        self._conversion_cache = {}
        self._restoration_cache = {}

        # exact_match辞書を初期化
        self.exact_match = {}
        self.exact_match_reverse = {}

        self._apply_dictionary_state(_DictionaryLoader.load(dictionary_path, self.logger))

    def _apply_dictionary_state(self, state: _DictionaryState):
        """ロード済み辞書状態をコンバーターに反映する"""
        self.unicode_to_ascii = state.unicode_to_ascii
        self.ascii_to_unicode = state.ascii_to_unicode
        if state.exact_match is not None:
            self.exact_match = state.exact_match
            self.exact_match_reverse = state.exact_match_reverse
        self.maya_invalid_chars = state.maya_invalid_chars
        self.prefix_map = state.prefix_map
        self.suffix_map = state.suffix_map
        self.languages = state.languages

    def convert(self, text: str) -> str:
        """
        Unicode文字列をMaya互換ASCII文字列に変換

        Args:
            text (str): 変換対象の文字列

        Returns:
            str: Maya互換ASCII文字列
        """
        if text is None:
            return None

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
        # 先頭に数字がある場合は、ASCII専用文字列でも複合処理を行う
        if re.match(r"^\d+", text):
            return self._convert_complex_text(text)

        # ASCII専用文字列はそのまま
        if self.is_ascii_only(text):
            return self.maya_safe_name(text)

        # exact_matchの完全一致変換（最優先）
        if text in self.exact_match:
            return self.maya_safe_name(self.exact_match[text])

        # 完全一致の辞書変換
        if text in self.unicode_to_ascii:
            return self.maya_safe_name(self.unicode_to_ascii[text])

        # 複合的な変換処理
        return self._convert_complex_text(text)

    def _convert_complex_text(self, text: str) -> str:
        """複合的な変換処理（prefix/suffix/hash化を含む）"""
        # 1. 全角数字を半角に変換
        processed_text = self._convert_fullwidth_numbers(text)

        # 2. Prefix、Suffixの処理
        prefix, main_part, suffix = self._process_prefix_suffix(processed_text)

        # 3. 主要部分の最終処理
        final_main = self._finalize_main_part(main_part)

        # 4. 結果の組み立てとMaya安全化
        result = self._assemble_result(prefix, final_main, suffix)
        return self.maya_safe_name(result)

    def _finalize_main_part(self, main_part: str) -> str:
        """主要部分の最終処理（ハッシュ化を含む）"""
        # 既にハッシュ化されている場合
        if main_part.startswith(self.HASH_PREFIX):
            return main_part

        # ASCII文字列の場合
        if self.is_ascii_only(main_part):
            return main_part

        # 辞書に無い文字列はハッシュ化
        hash_value = self._generate_hash(main_part)
        return f"{self.HASH_PREFIX}{hash_value}"

    def _convert_fullwidth_numbers(self, text: str) -> str:
        """全角数字を半角数字に変換"""
        fullwidth_to_halfwidth = {
            "０": "0",
            "１": "1",
            "２": "2",
            "３": "3",
            "４": "4",
            "５": "5",
            "６": "6",
            "７": "7",
            "８": "8",
            "９": "9",
        }

        result = text
        for fw, hw in fullwidth_to_halfwidth.items():
            result = result.replace(fw, hw)

        return result

    def _process_prefix_suffix(self, text: str) -> tuple:
        """Prefix、Suffixを処理して英語に変換"""
        # 1. 先頭の数字を分離
        leading_numbers, text = self._extract_leading_numbers(text)

        # 2. スペースを除去
        text = text.strip()

        # 3. Prefixの処理
        prefix, text = self._extract_prefix(text)

        # 4. 末尾の特殊文字を分離
        text, trailing_chars = self._extract_trailing_chars(text)

        # 5. Suffixの処理
        text, suffix_parts = self._extract_suffix(text)

        # 6. 基本部分の変換
        converted_main = self._convert_main_part(text)

        # 7. 結果の組み立て
        suffix = self._build_suffix(suffix_parts, trailing_chars, leading_numbers)

        return prefix, converted_main, suffix

    def _extract_leading_numbers(self, text: str) -> tuple:
        """先頭の数字を抽出"""
        match = re.match(r"^(\d+)\s*", text)
        if match:
            leading_numbers = match.group(1)
            remaining_text = text[len(match.group(0)) :]

            return leading_numbers, remaining_text

        return "", text

    def _extract_prefix(self, text: str) -> tuple:
        """接頭辞を抽出"""
        for prefix_pair in self.prefix_map:
            if text.startswith(prefix_pair[0]):
                return prefix_pair[1], text[len(prefix_pair[0]) :]
        return "", text

    def _extract_trailing_chars(self, text: str) -> tuple:
        """末尾の数字・英文字・Maya無効文字を分離"""
        # 日本語の接尾辞がある場合は、その前の数字・英文字を分離
        for suffix_pair in self.suffix_map:
            if text.endswith(suffix_pair[0]):
                # 接尾辞を一時的に除去
                text_without_suffix = text[: -len(suffix_pair[0])]
                # 数字・英文字・アンダースコアを探す（単語境界を考慮）
                end_match = re.search(r"([A-Za-z0-9+|_]+)$", text_without_suffix)
                if end_match:
                    trailing_chars = end_match.group(1)
                    remaining_text = text_without_suffix[: -len(trailing_chars)] + suffix_pair[0]
                    return remaining_text, trailing_chars

        # 通常の処理: 末尾の数字・記号のみを抽出（英単語全体は抽出しない）
        # 非ASCII文字が含まれる場合のみ、末尾の数字・記号を分離
        if not self.is_ascii_only(text):
            end_match = re.search(r"([A-Za-z0-9+|_]+)$", text)
            if end_match:
                trailing_chars = end_match.group(1)
                text = text[: -len(trailing_chars)]
                return text, trailing_chars

        return text, ""

    def _extract_suffix(self, text: str) -> tuple:
        """接尾辞を抽出（複合suffixに対応）"""
        # まず、辞書に完全一致する場合は接尾辞を抽出しない
        if text in self.unicode_to_ascii:
            return text, []

        suffix_parts = []
        remaining_text = text

        while remaining_text:
            found_suffix = False
            # 最長一致でsuffixを探す
            for suffix_pair in self.suffix_map:
                if remaining_text.endswith(suffix_pair[0]):
                    # 接尾辞を除いた部分が辞書にある場合、接尾辞を抽出
                    potential_main = remaining_text[: -len(suffix_pair[0])]
                    if potential_main in self.unicode_to_ascii:
                        suffix_parts.insert(0, suffix_pair[1])  # 前に挿入（逆順なので）
                        remaining_text = potential_main
                        found_suffix = True
                        break
                    # 接尾辞を除いた部分が辞書にない場合でも、
                    # 残りの文字列が空でない場合は抽出を続ける
                    elif potential_main:
                        suffix_parts.insert(0, suffix_pair[1])
                        remaining_text = potential_main
                        found_suffix = True
                        break

            if not found_suffix:
                break

        return remaining_text, suffix_parts

    def _convert_main_part(self, text: str) -> str:
        """基本部分の変換"""
        if not text:
            return text

        # 辞書に存在する場合は辞書変換を優先
        if text in self.unicode_to_ascii:
            return self.unicode_to_ascii[text]

        # ASCII専用文字列の場合はそのまま返す
        if self.is_ascii_only(text):
            return text

        # 複合語の場合は個別に変換を試みる
        return self._convert_compound_word(text)

    def _build_suffix(self, suffix_parts: List[str], trailing_chars: str, leading_numbers: str = "") -> str:
        """suffix部分を組み立て"""
        result_parts = []

        # suffixパーツがある場合（接尾辞）
        if suffix_parts:
            result_parts.extend(suffix_parts)

        # 末尾文字がある場合（数字・英文字）
        if trailing_chars:
            # Maya無効文字の変換を直接行う
            converted_trailing = trailing_chars
            for invalid, replacement in self.maya_invalid_chars.items():
                converted_trailing = converted_trailing.replace(invalid, replacement)

            # 既に_で始まる場合は、追加の_を付けない
            if converted_trailing.startswith("_"):
                result_parts.append(converted_trailing.lower())
            else:
                result_parts.append("_" + converted_trailing.lower())

        # 先頭の数字がある場合は末尾に追加
        if leading_numbers:
            result_parts.append("_" + leading_numbers)

        return "".join(result_parts)

    def _assemble_result(self, prefix: str, main: str, suffix: str) -> str:
        """結果を組み立て"""
        return "".join([prefix, main, suffix])

    def _convert_compound_word(self, text: str) -> str:
        """複合語を個別に変換"""
        # 複合語の分解を試みる
        # 例：「つまさき」→「つまさき」（辞書にある）
        # 例：「腕捩」→「腕」+「捩」→「arm」+「twist」

        if text in self.unicode_to_ascii:
            return self.unicode_to_ascii[text]

        # 単語の分解を試みる（greedy matching）
        parts = []
        remaining = text

        while remaining:
            found = False
            # 最長一致を探す
            for length in range(len(remaining), 0, -1):
                substring = remaining[:length]
                if substring in self.unicode_to_ascii:
                    parts.append(self.unicode_to_ascii[substring])
                    remaining = remaining[length:]
                    found = True
                    break

            if not found:
                # 1文字も見つからない場合、その文字を飛ばす
                remaining = remaining[1:]

        # 結果を返す
        if parts:
            return "_".join(parts)
        else:
            # 何も変換できなかった場合はHASH化して返す
            return self.HASH_PREFIX + self._generate_hash(text)

    def _generate_hash(self, text: str) -> str:
        """文字列のハッシュを生成"""
        # UTF-8エンコードしてからハッシュ化
        hash_obj = hashlib.md5(text.encode("utf-8"))
        hash_hex = hash_obj.hexdigest()

        # 指定された長さに切り詰める
        return hash_hex[: self.HASH_LENGTH]

    def _restore_compound_word(self, text: str) -> str:
        """複合語を復元"""
        # 単純な辞書ルックアップ
        if text in self.ascii_to_unicode:
            return self.ascii_to_unicode[text]

        # アンダースコアで分割して個別に復元
        parts = text.split("_")
        restored_parts = []

        for part in parts:
            if part in self.ascii_to_unicode:
                restored_parts.append(self.ascii_to_unicode[part])
            else:
                # 復元できない部分があった場合は元のテキストを返す
                return text

        return "".join(restored_parts)

    def maya_safe_name(self, text: str) -> str:
        """Maya用に無効文字を置換"""
        result = text
        # 先に個別の置換を実行
        for invalid, replacement in self.maya_invalid_chars.items():
            result = result.replace(invalid, replacement)

        # Maya有効文字以外を_に変換
        # 使用可能な文字: 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_
        valid_chars = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")

        # 無効文字を_に変換
        safe_result = "".join(c if c in valid_chars else "_" for c in result)

        return safe_result

    def restore_maya_chars(self, text: str) -> str:
        """Maya無効文字の置換を元に戻す"""
        result = text
        # 個別の置換のみを元に戻す（一般的な_は元に戻さない）
        maya_replacement_to_char = {v: k for k, v in self.maya_invalid_chars.items() if v != "_"}
        for replacement, original in maya_replacement_to_char.items():
            result = result.replace(replacement, original)
        return result

    def is_ascii_only(self, text: str) -> bool:
        """ASCII専用文字列かチェック"""
        try:
            text.encode("ascii")
            return True
        except UnicodeEncodeError:
            return False

    def is_hash_converted(self, text: str) -> bool:
        """ハッシュ変換された文字列かチェック"""
        restored_text = self.restore_maya_chars(text)
        return restored_text.startswith(self.HASH_PREFIX)

    def is_dictionary_converted(self, text: str) -> bool:
        """辞書変換された文字列かチェック"""
        # 直接チェック
        if text in self.ascii_to_unicode:
            return True

        # exact_matchの逆引きチェック
        if text in self.exact_match_reverse:
            return True

        # Maya無効文字の処理を元に戻してからチェック
        restored_text = self.restore_maya_chars(text)
        return restored_text in self.ascii_to_unicode or restored_text in self.exact_match_reverse

    def get_encoding_type(self, text: str) -> str:
        """文字列のエンコード方式を判定"""
        if self.is_hash_converted(text):
            return "hash"
        elif self.is_dictionary_converted(text):
            return "dictionary"
        else:
            return "original"

    def get_unique_name(self, base_name: str, existing_names: Optional[set] = None) -> str:
        """
        一意な名前を生成

        Args:
            base_name: ベース名
            existing_names: 既存の名前のセット

        Returns:
            str: 一意な名前
        """
        if existing_names is None:
            existing_names = set()

        if base_name not in existing_names:
            return base_name

        # 数字サフィックスで一意性を確保
        counter = 1
        while True:
            candidate = f"{base_name}_{counter}"
            if candidate not in existing_names:
                return candidate
            counter += 1

    def batch_convert(self, texts: List[str]) -> List[str]:
        """複数の文字列を一括変換"""
        result = []
        existing_names = set()

        for text in texts:
            converted = self.convert(text)
            # 一意名を生成
            unique_converted = self.get_unique_name(converted, existing_names)
            result.append(unique_converted)
            existing_names.add(unique_converted)

        return result

    def get_conversion_stats(self, converted_names: List[str]) -> Dict[str, int]:
        """変換統計の取得"""
        stats = {
            "dictionary": 0,
            "hash": 0,
            "original": 0,
            "total": len(converted_names),
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
            unicode_text: Unicode文字列
            ascii_text: ASCII文字列
        """
        self.unicode_to_ascii[unicode_text] = ascii_text
        self.ascii_to_unicode[ascii_text] = unicode_text
        self.clear_cache()

    def remove_dictionary_entry(self, unicode_text: str):
        """
        辞書エントリを削除

        Args:
            unicode_text: 削除するUnicode文字列
        """
        if unicode_text in self.unicode_to_ascii:
            ascii_text = self.unicode_to_ascii[unicode_text]
            del self.unicode_to_ascii[unicode_text]
            if ascii_text in self.ascii_to_unicode:
                del self.ascii_to_unicode[ascii_text]
            self.clear_cache()

    def get_dictionary_info(self) -> Dict:
        """現在の辞書情報を取得"""
        return {
            "total_entries": len(self.unicode_to_ascii),
            "conversion_cache_size": len(self._conversion_cache),
            "restoration_cache_size": len(self._restoration_cache),
            "maya_invalid_chars": len(self.maya_invalid_chars),
            "hash_prefix": self.HASH_PREFIX,
            "hash_length": self.HASH_LENGTH,
            "sample_entries": dict(list(self.unicode_to_ascii.items())[:5]),
        }

    def export_dictionary(self, file_path: str):
        """
        辞書をファイルに出力

        Args:
            file_path: 出力ファイルのパス
        """
        data = {
            "_meta": {
                "version": "1.0",
                "description": "多言語対応Unicode→ASCII変換辞書",
                "last_updated": "2025-07-04",
            },
            "dictionary": self.unicode_to_ascii,
            "maya_invalid_chars": self.maya_invalid_chars,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"Exported dictionary: {file_path}")

    def reload_dictionary(self, dictionary_path: Optional[str] = None):
        """
        辞書を再読み込み

        Args:
            dictionary_path: 辞書ファイルのパス
        """
        self.clear_cache()
        self._apply_dictionary_state(_DictionaryLoader.load(dictionary_path, self.logger))


# グローバルインスタンス（シングルトンパターン）
_converter_instance = None


def get_converter(dictionary_path: Optional[str] = None) -> UnicodeToAsciiConverter:
    """
    コンバーターのグローバルインスタンスを取得

    Args:
        dictionary_path: 辞書ファイルのパス（初回のみ有効）

    Returns:
        UnicodeToAsciiConverter: コンバーターインスタンス
    """
    global _converter_instance
    if _converter_instance is None:
        _converter_instance = UnicodeToAsciiConverter(dictionary_path)
    return _converter_instance


def reset_converter():
    """コンバーターインスタンスをリセット"""
    global _converter_instance
    _converter_instance = None
