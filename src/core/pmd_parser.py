import os

from src.core.mmd_parser import MMDParseException
from src.core.pmd_data.header import PmdHeader
from src.core.pmd_data.vertex import PmdVertex
from src.core.pmd_data.material import PmdMaterial
from src.core.pmd_data.bone import PmdBone
from src.core.pmd_data.ik import PmdIK
from src.core.pmd_data.morph import PmdMorph
from src.core.pmd_data.display_frame import PmdDisplayFrame
from src.core.pmd_data.rigid_body import PmdRigidBody
from src.core.pmd_data.joint import PmdJoint

class PmdParser:
    """
    PMDファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """
    def __init__(self):
        self.header = PmdHeader()
        self.vertices = []
        self.faces = []
        self.materials = []
        self.bones = []
        self.ik_data = []
        self.morphs = []
        self.display_frames = []
        self.rigid_bodies = []
        self.joints = []

    def parse_file(self, file_path):
        """
        指定されたPMDファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するPMDファイルのパス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        # TODO: ファイルを開き、各セクションの数を読み込み、対応するクラスのparseメソッドを呼び出す。
        # ヘッダ、頂点、面、材質、ボーン、IK、モーフ、表示枠、剛体、ジョイントの順に解析する。
        # 各セクションのデータ構造と読み込み順序はMMDの仕様に厳密に従う。
        pass
