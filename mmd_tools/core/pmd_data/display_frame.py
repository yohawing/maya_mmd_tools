import collections
import struct

from mmd_tools.core import utils


class PmdDisplayFrame:
    """PMDファイルの表示枠データを保持するクラス。"""
    def __init__(self):
        self.morphs_display_list = []
        self.bone_display_names = []
        self.bone_display_names_english = []
        self.bone_display_lists = collections.OrderedDict()

        self.english_name = ''

    def parse(self, f):
        """
        ファイルハンドルからPMD表示枠データを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        self.parse_morphs(f)
        self.parse_bones(f)

    def parse_morphs(self, f):
        """
        ファイルハンドルからPMD表示枠のモーフデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        num_morphs = struct.unpack('<B', f.read(1))[0]
        for _ in range(num_morphs):
            morph_index = struct.unpack('<H', f.read(2))[0]
            self.morphs_display_list.append(morph_index)

    def parse_bones(self, f):
        """
        ファイルハンドルからPMD表示枠のボーンデータを解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        bone_disps = []
        self.bone_display_lists = collections.OrderedDict()
        num_bone_names = struct.unpack('<B', f.read(1))[0]
        for _ in range(num_bone_names):
            name = utils.decodePMDString(f.read(50))
            bone_disps.append(name)
            self.bone_display_lists[name] = []
        self.bone_display_names = [bone_disps, None]

        num_links = struct.unpack('<I', f.read(4))[0]
        for _ in range(num_links):
            bone_index = struct.unpack('<H', f.read(2))[0]
            disp_index = struct.unpack('<B', f.read(1))[0]
            self.bone_display_lists[bone_disps[disp_index - 1]].append(bone_index)

    def parse_english(self, f):
        """
        ファイルハンドルから英語のPMD表示枠名を解析し、自身の属性に格納する。

        Args:
            f (file): バイナリ読み込みモードで開かれたファイルハンドル。
        """
        bone_disps = []
        for _ in range(len(self.bone_display_names)):
            name = utils.decodePMDString(f.read(50))
            bone_disps.append(name)
        self.bone_display_names_english[1] = bone_disps
