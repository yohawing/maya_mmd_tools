import os

from ..mmd_parser import MMDParseException
from .pmx_data.header import PmxHeader
from .pmx_data.vertex import PmxVertex
from .pmx_data.face import PmxFace
from .pmx_data.material import PmxMaterial
from .pmx_data.bone import PmxBone
from .pmx_data.ik import PmxIK
from .pmx_data.ik_link import PmxIKLink
from .pmx_data.morph import PmxMorph
from .pmx_data.display_frame import PmxDisplayFrame
from .pmx_data.rigid_body import PmxRigidBody
from .pmx_data.joint import PmxJoint

class PmxParser:
    """
    PMXファイルを解析し、そのデータをPythonオブジェクトとして保持するクラス。
    """
    def __init__(self):
        self.header = PmxHeader()
        self.vertices = []
        self.faces = []
        self.textures = []
        self.materials = []
        self.bones = []
        self.morphs = []
        self.display_frames = []
        self.rigid_bodies = []
        self.joints = []

    def parse_file(self, file_path):
        """
        指定されたPMXファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するPMXファイルのパス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        # TODO: ファイルを開き、各セクションの数を読み込み、対応するクラスのparseメソッドを呼び出す。
        # PMXの仕様に従って、ヘッダ、頂点、面、テクスチャ、材質、ボーン、モーフ、表示枠、剛体、ジョイントの順に解析する。
        pass
