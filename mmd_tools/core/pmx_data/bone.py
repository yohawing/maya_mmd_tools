from ..exceptions import MMDParseException

class PmxBone:
    """PMXファイルのボーンデータを保持するクラス。"""
    def __init__(self):
        self.name_jp = ''
        self.name_en = ''
        self.position = (0.0, 0.0, 0.0)
        self.parent_bone_index = -1
        self.deform_level = 0
        self.bone_flags = 0
        self.tail_position_offset = (0.0, 0.0, 0.0)
        self.fixed_axis = (0.0, 0.0, 0.0)
        self.local_axis_x = (0.0, 0.0, 0.0)
        self.local_axis_z = (0.0, 0.0, 0.0)
        self.external_parent_bone_index = -1
        self.ik_data = None # PmxIK object

    def parse(self, file_handle, header):
        """
        ファイルハンドルからPMXボーンデータを解析し、自身の属性に格納する。

        Args:
            file_handle (file): バイナリ読み込みモードで開かれたファイルハンドル。
            header (PmxHeader): PMXヘッダ情報（ボーンインデックスサイズなどに使用）。

        Raises:
            MMDParseException: ボーンデータの解析に失敗した場合。
        """
        # TODO: PMXボーンデータのバイナリ解析ロジックを実装する。
        # Name JP (variable length string), Name EN (variable length string)
        # Position (3 floats)
        # Parent Bone Index (variable size), Deform Level (int)
        # Bone Flags (2 bytes)
        # Depending on Bone Flags, read additional data:
        #   Tail Position Offset (3 floats) or Tail Bone Index (variable size)
        #   Fixed Axis (3 floats)
        #   Local Axis X (3 floats), Local Axis Z (3 floats)
        #   External Parent Bone Index (variable size)
        #   IK Data (PmxIK object)
        pass