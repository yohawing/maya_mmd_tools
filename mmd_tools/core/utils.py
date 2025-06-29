# Utility関数

import struct


def parsePMXString(f, encoding = "utf-16-le"):
    """PMX形式の文字列をファイルから読み込み、通常の文字列に変換します。"""
    length, = struct.unpack("<i", f.read(4))
    if length == 0:
        return ""
    buf, = struct.unpack("<%ds" % length, f.read(length))
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
        null_index = byteString.index(b'\x00')
        byteString = byteString[:null_index]
    except ValueError:
        # ヌル文字が見つからない場合は、文字列全体を使用します
        pass
    return byteString.decode('cp932', errors='replace')


def encodeCp932String(string):
    """通常の文字列をCP932形式のバイト文字列に変換します。"""
    try:
        return string.encode("cp932")
    except UnicodeEncodeError:
        return b"\x00" + string.encode("cp932", errors="replace")[1:]