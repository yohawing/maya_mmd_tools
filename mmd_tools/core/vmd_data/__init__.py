import os
import struct

from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.header import VmdHeader
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame

from mmd_tools.core.exceptions import MMDParseException


def _read_optional_uint32(f):
    data = f.read(4)
    if not data:
        return None
    if len(data) != 4:
        raise struct.error("unpack requires a buffer of 4 bytes")
    return struct.unpack("<I", data)[0]


class VmdData:
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
        self.source_file = None

    def parse_file(self, file_path) -> "VmdData":
        """
        指定されたVMDファイルを読み込み、各セクションを解析してデータを格納する。

        Args:
            file_path (str): 解析するVMDファイルのパス。

        Returns:
            VmdParser: メソッドチェーニングをサポートするための自身のインスタンス。

        Raises:
            FileNotFoundError: ファイルが見つからない場合。
            MMDParseException: ファイルの解析に失敗した場合。
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"VMD file not found: {file_path}")
        self.source_file = os.path.abspath(file_path)

        with open(file_path, "rb") as f:
            try:
                # Header
                self.header.parse(f)

                # Bone Frames
                num_bone_frames = struct.unpack("<I", f.read(4))[0]
                for _ in range(num_bone_frames):
                    frame = VmdBoneFrame()
                    frame.parse(f.read(VmdBoneFrame.size()))
                    self.bone_frames.append(frame)

                # Morph Frames
                num_morph_frames = struct.unpack("<I", f.read(4))[0]
                for _ in range(num_morph_frames):
                    frame = VmdMorphFrame()
                    frame.parse(f.read(VmdMorphFrame.size()))
                    self.morph_frames.append(frame)

                # Camera Frames
                num_camera_frames = struct.unpack("<I", f.read(4))[0]
                for _ in range(num_camera_frames):
                    frame = VmdCameraFrame()
                    frame.parse(f.read(VmdCameraFrame.size()))
                    self.camera_frames.append(frame)

                # Light Frames
                num_light_frames = struct.unpack("<I", f.read(4))[0]
                for _ in range(num_light_frames):
                    frame = VmdLightFrame()
                    frame.parse(f.read(VmdLightFrame.size()))
                    self.light_frames.append(frame)

                # Shadow Frames
                # Some VMDs end after the light section and omit self-shadow and IK sections.
                num_shadow_frames = _read_optional_uint32(f)
                if num_shadow_frames is not None:
                    for _ in range(num_shadow_frames):
                        frame = VmdShadowFrame()
                        frame.parse(f.read(VmdShadowFrame.size()))
                        self.shadow_frames.append(frame)

                # IK Show/Hide Frames
                # VMD 2.0ではIK表示/非表示フレームは存在しない場合があるため、ファイルの終端チェックを行う
                num_ik_show_hide_frames = _read_optional_uint32(f)
                if num_ik_show_hide_frames is not None:
                    for _ in range(num_ik_show_hide_frames):
                        frame = VmdIKShowHideFrame()
                        # IK表示/非表示フレームは可変長なので、個別に読み込む
                        # まずは固定長部分を読み込み、ik_countを取得
                        fixed_data = f.read(VmdIKShowHideFrame.size())
                        frame.frame_number = struct.unpack_from("<I", fixed_data, 0)[0]
                        frame.visible = struct.unpack_from("<B", fixed_data, 4)[0]
                        frame.ik_count = struct.unpack_from("<I", fixed_data, 5)[0]

                        # ik_statesの可変長部分を読み込む
                        for _ in range(frame.ik_count):
                            ik_name = f.read(20).split(b"\x00")[0].decode("shift_jis")
                            show_flag = struct.unpack("<B", f.read(1))[0]
                            frame.ik_states.append((ik_name, show_flag))
                        self.ik_show_hide_frames.append(frame)

            except struct.error as e:
                raise MMDParseException(f"Failed to parse VMD file: {file_path}") from e

        return self

    def write_file(self, file_path):
        """
        VMDデータをファイルに書き込む。

        Args:
            file_path (str): 書き込むVMDファイルのパス。

        Raises:
            IOError: ファイル書き込みに失敗した場合。
        """
        try:
            with open(file_path, "wb") as f:
                # Header
                self.header.write(f)

                # Bone Frames
                bone_frame_count = len(self.bone_frames)
                f.write(struct.pack("<I", bone_frame_count))
                for frame in self.bone_frames:
                    f.write(frame.write())

                # Morph Frames
                morph_frame_count = len(self.morph_frames)
                f.write(struct.pack("<I", morph_frame_count))
                for frame in self.morph_frames:
                    f.write(frame.write())

                # Camera Frames
                camera_frame_count = len(self.camera_frames)
                f.write(struct.pack("<I", camera_frame_count))
                for frame in self.camera_frames:
                    f.write(frame.write())

                # Light Frames
                light_frame_count = len(self.light_frames)
                f.write(struct.pack("<I", light_frame_count))
                for frame in self.light_frames:
                    f.write(frame.write())

                # Shadow Frames
                shadow_frame_count = len(self.shadow_frames)
                f.write(struct.pack("<I", shadow_frame_count))
                for frame in self.shadow_frames:
                    f.write(frame.write())

                # IK Show/Hide Frames (optional)
                if self.ik_show_hide_frames:
                    ik_frame_count = len(self.ik_show_hide_frames)
                    f.write(struct.pack("<I", ik_frame_count))
                    for frame in self.ik_show_hide_frames:
                        f.write(frame.write())

        except Exception as e:
            raise IOError(f"Failed to write VMD file: {file_path}") from e
