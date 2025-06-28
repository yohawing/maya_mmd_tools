import struct

class PmdDisplayFrame:
    """PMDファイルの表示枠データを保持するクラス。"""
    def __init__(self):
        self.name = ''
        self.elements = [] # List of (bone_or_morph_index, type_flag)
        self.english_name = ''

    def parse(self, f):
        """
        ファイルハンドルからPMD表示枠データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.name = f.read(50).decode('shift_jis').strip('\x00')
        num_elements = struct.unpack('<I', f.read(4))[0]
        for _ in range(num_elements):
            index = struct.unpack('<I', f.read(4))[0]
            type_flag = struct.unpack('<B', f.read(1))[0]
            self.elements.append((index, type_flag))

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMD表示枠名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.english_name = f.read(50).decode('cp932').strip('\x00')
