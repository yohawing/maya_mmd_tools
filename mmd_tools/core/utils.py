# Utility関数

import struct
from typing import List

from mmd_tools.core.pmx_data.header import PmxEncoding


def parsePMXString(f, encoding=0):
    """PMX形式の文字列をファイルから読み込み、通常の文字列に変換します。

    Args:
        f: バイナリ読み込みモードで開かれたファイルハンドル
        encoding (int): 文字エンコーディング（0: UTF-16LE, 1: UTF-8）

    Returns:
        str: 読み込まれた文字列

    """
    (length,) = struct.unpack("<i", f.read(4))
    if length == 0:
        return ""
    (buf,) = struct.unpack("<%ds" % length, f.read(length))
    return buf.decode("utf-16-le" if encoding == PmxEncoding.UTF16LE else "utf-8", errors="replace")


def decodePMDString(byteString):
    """PMD形式のバイト文字列を通常の文字列に変換します。

    Args:
        byteString (bytes): デコードするバイト列

    Returns:
        str: デコードされた文字列
    """
    if not byteString:
        return ""
    return decodeCp932String(byteString)


def decodeCp932String(byteString):
    """
    CP932形式のバイト文字列を通常の文字列に変換します。

    Args:
        byteString (bytes): デコードするバイト列
    Returns:
        str: デコードされた文字列
    """
    try:
        null_index = byteString.index(b"\x00")
        byteString = byteString[:null_index]
    except ValueError:
        # ヌル文字が見つからない場合は、文字列全体を使用します
        pass
    return byteString.decode("cp932", errors="replace")


def encodeCp932String(string):
    """通常の文字列をCP932形式のバイト文字列に変換します。

    Args:
        string (str): エンコードする文字列

    Returns:
        bytes: CP932形式のバイト列
    """
    try:
        return string.encode("cp932")
    except UnicodeEncodeError:
        return b"\x00" + string.encode("cp932", errors="replace")[1:]


def encodePMDString(string, length):
    """
    文字列をPMD形式（固定長Shift-JIS）にエンコードします。

    Args:
        string (str): エンコードする文字列
        length (int): 固定長バイト数

    Returns:
        bytes: 固定長のバイト列
    """
    if not string:
        return b"\x00" * length

    encoded = encodeCp932String(string)
    if len(encoded) >= length:
        # 長すぎる場合は切り詰める
        return encoded[: length - 1] + b"\x00"
    else:
        # 短い場合はヌル文字でパディング
        return encoded + b"\x00" * (length - len(encoded))


def get_pmx_encoding_string(encoding_flag: PmxEncoding) -> str:
    """
    PMXのエンコーディングフラグ（0または1）を文字列に変換

    Args:
        encoding_flag (int): 0=UTF-16LE, 1=UTF-8

    Returns:
        str: エンコーディング文字列
    """
    return "utf-16-le" if encoding_flag == PmxEncoding.UTF16LE else "utf-8"


def encodePMXString(string, encoding=PmxEncoding.UTF16LE):
    """
    文字列をPMX形式（長さプレフィックス付き）にエンコードします。

    Args:
        string (str): エンコードする文字列
        encoding (str): 文字エンコーディング

    Returns:
        bytes: 長さプレフィックス付きのバイト列
    """
    if not string:
        return struct.pack("<i", 0)
    if encoding == PmxEncoding.UTF16LE:
        encoded = string.encode("utf-16-le")
    else:
        encoded = string.encode("utf-8")
    return struct.pack("<i", len(encoded)) + encoded


# Unicode文字列変換API（シンプルインターフェース）
def convert_utf8_to_ascii(text):
    """
    Unicode文字列（日本語・中国語等）をMaya互換ASCII文字列に変換

    Args:
        text (str): 変換対象の文字列

    Returns:
        str: Maya互換ASCII文字列

    Examples:
        >>> convert_unicode_to_maya_safe("ボーン")
        'bone'
        >>> convert_unicode_to_maya_safe("骨骼")
        'bone'
        >>> convert_unicode_to_maya_safe("左足IK")
        'utfb64_5bem6Laz...'
    """
    from .unicode_converter import get_converter

    return get_converter().convert(text)


def convert_utf8_to_ascii_batch(names):
    """
    複数のUnicode文字列を一括でMaya互換文字列に変換
    重複しない名前を保証します

    Args:
        names (list): 変換対象文字列のリスト

    Returns:
        dict: 元の文字列 -> 変換後文字列のマッピング

    Examples:
        >>> convert_utf8_to_ascii_batch(["ボーン", "骨骼", "材質01"])
        {'ボーン': 'bone', '骨骼': 'bone_1', '材質01': 'material_01'}
    """
    from .unicode_converter import get_converter

    return get_converter().batch_convert(names)


def get_encoding_type(text):
    """
    文字列のエンコード方式を判定

    Args:
        text (str): 判定対象文字列

    Returns:
        str: エンコード方式 ('base64', 'dictionary', 'original')

    Examples:
        >>> get_encoding_type("bone")
        'dictionary'
        >>> get_encoding_type("utfb64_...")
        'base64'
    """
    from .unicode_converter import get_converter

    return get_converter().get_encoding_type(text)


def is_unicode_converted_name(text):
    """
    文字列がUnicodeから変換されたものかチェック

    Args:
        text (str): チェック対象文字列

    Returns:
        bool: Unicodeから変換された場合True
    """
    from .unicode_converter import get_converter

    converter = get_converter()
    return converter.is_converted_base64(text) or converter.is_dictionary_converted(text)


def add_dictionary_entry(unicode_text: str, ascii_text: str):
    """
    辞書エントリを追加します（メモリ内のみ）

    Args:
        unicode_text: Unicode文字列
        ascii_text: ASCII対訳
    """
    from .unicode_converter import get_converter

    converter = get_converter()
    converter.add_dictionary_entry(unicode_text, ascii_text)


def get_dictionary_info():
    """
    辞書情報を取得します

    Returns:
        Dict: 辞書情報
    """
    from .unicode_converter import get_converter

    converter = get_converter()
    return converter.get_dictionary_info()


def reload_dictionary(dictionary_path: str = None):
    """
    辞書ファイルを再読み込みします

    Args:
        dictionary_path: 辞書ファイルのパス
    """
    from .unicode_converter import get_converter

    converter = get_converter()
    converter._load_dictionary(dictionary_path)
    converter.clear_cache()


# ベクトル演算関数
def subtract_vectors(v1: List[float], v2: List[float]) -> List[float]:
    """
    ベクトルの減算を行います（v1 - v2）

    Args:
        v1: ベクトル1 [x, y, z]
        v2: ベクトル2 [x, y, z]

    Returns:
        結果ベクトル [x, y, z]
    """
    return [v1[0] - v2[0], v1[1] - v2[1], v1[2] - v2[2]]


def vector_length(v: List[float]) -> float:
    """
    ベクトルの長さを計算します

    Args:
        v: ベクトル [x, y, z]

    Returns:
        ベクトルの長さ
    """
    import math

    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def create_bone_joint_mapping(bones, maya_joints, format_type):
    """
    ボーン名とMayaジョイント名のマッピングを作成する。

    Args:
        bones: MMDボーンデータのリスト
        maya_joints: Mayaジョイント名のリスト
        format_type: "pmx" または "pmd"

    Returns:
        dict: ボーン名からMayaジョイント名へのマッピング
    """
    mapping = {}

    for i, (bone, joint_name) in enumerate(zip(bones, maya_joints)):
        # インデックスベースのボーン名を作成（PhysicsConverterで使用される形式）
        bone_key = f"bone_{i}"
        mapping[bone_key] = joint_name

        # 追加でボーンの実際の名前でもマッピング（将来の拡張用）
        if hasattr(bone, "name") and bone.name:
            mapping[bone.name] = joint_name

    return mapping


# ベクトル演算関数（Maya非依存）
def cross_product(vec1, vec2):
    """
    2つのベクトルの外積を計算する。

    Args:
        vec1 (list): ベクトル1 [x, y, z]
        vec2 (list): ベクトル2 [x, y, z]

    Returns:
        list: 外積ベクトル [x, y, z]
    """
    return [
        vec1[1] * vec2[2] - vec1[2] * vec2[1],
        vec1[2] * vec2[0] - vec1[0] * vec2[2],
        vec1[0] * vec2[1] - vec1[1] * vec2[0],
    ]


def normalize_vector(vector):
    """
    ベクトルを正規化する。

    Args:
        vector (list): ベクトル [x, y, z]

    Returns:
        list: 正規化されたベクトル [x, y, z]
    """
    import math

    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length < 1e-6:  # ゼロベクトルのチェック
        return [0.0, 0.0, 0.0]
    return [vector[0] / length, vector[1] / length, vector[2] / length]


def pmx_to_maya_vector(pmx_vector):
    """
    PMXの右手座標系ベクトルをMayaの左手座標系に変換する。
    PMX: X(右), Y(上), Z(手前)
    Maya: X(右), Y(上), Z(奥)

    Args:
        pmx_vector (list): PMXベクトル [x, y, z]

    Returns:
        list: Mayaベクトル [x, y, -z]
    """
    return [pmx_vector[0], pmx_vector[1], -pmx_vector[2]]
