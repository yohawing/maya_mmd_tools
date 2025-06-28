import os

from .exceptions import MMDParseException
from .vmd_data.header import VmdHeader
from .vmd_data.bone_frame import VmdBoneFrame
from .vmd_data.morph_frame import VmdMorphFrame
from .vmd_data.camera_frame import VmdCameraFrame
from .vmd_data.light_frame import VmdLightFrame
from .vmd_data.shadow_frame import VmdShadowFrame
from .vmd_data.ik_show_hide_frame import VmdIKShowHideFrame

class VmdParser:
    """
    VMDファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """
    def __init__(self):
        self.header = VmdHeader()
        self.bone_frames = []
        self.morph_frames = []
        self.camera_frames = []
        self.light_frames = []
        self.shadow_frames = []
        self.ik_show_hide_frames = []

    def parse_file(self, file_path):
        """
        指定されたVMDファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するVMDファイルのパス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        # TODO: ファイルを開き、各セクションの数を読み込み、対応するクラスのparseメソッドを呼び出す。
        # VMDの仕様に従って、ヘッダ、ボーンフレーム、モーフフレーム、カメラフレーム、照明フレーム、
        # セルフシャドウフレーム、IK/表示フレームの順に解析する。
        pass
