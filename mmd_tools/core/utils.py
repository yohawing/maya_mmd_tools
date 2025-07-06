# Utility関数

import struct


def parsePMXString(f, encoding="utf-16-le"):
    """PMX形式の文字列をファイルから読み込み、通常の文字列に変換します。"""
    (length,) = struct.unpack("<i", f.read(4))
    if length == 0:
        return ""
    (buf,) = struct.unpack("<%ds" % length, f.read(length))
    # return buf.decode(encoding)
    return str(buf, encoding, errors="replace")


def decodePMDString(byteString):
    """PMD形式のバイト文字列を通常の文字列に変換します。
    文字列は最初のヌル文字で終了します。"""
    if not byteString:
        return ""
    return decodeCp932String(byteString)


def decodeCp932String(byteString):
    """
    CP932形式のバイト文字列を通常の文字列に変換します。
    文字列は最初のヌル文字で終了します。
    """
    try:
        null_index = byteString.index(b"\x00")
        byteString = byteString[:null_index]
    except ValueError:
        # ヌル文字が見つからない場合は、文字列全体を使用します
        pass
    return byteString.decode("cp932", errors="replace")


def encodeCp932String(string):
    """通常の文字列をCP932形式のバイト文字列に変換します。"""
    try:
        return string.encode("cp932")
    except UnicodeEncodeError:
        return b"\x00" + string.encode("cp932", errors="replace")[1:]


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
    return converter.is_converted_base64(text) or converter.is_dictionary_converted(
        text
    )


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
