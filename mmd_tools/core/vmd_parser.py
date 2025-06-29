# -*- coding: utf-8 -*-

import os
import struct

from .exceptions import MMDParseException
from mmd_tools.core.vmd_data.header import VmdHeader
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from mmd_tools.core.vmd_data.camera_frame import VmdCameraFrame
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from mmd_tools.core.vmd_data.shadow_frame import VmdShadowFrame
from mmd_tools.core.vmd_data.ik_show_hide_frame import VmdIKShowHideFrame

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
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"VMD file not found: {file_path}")

        with open(file_path, 'rb') as f:
            try:
                # Header
                self.header.parse(f)

                # Bone Frames
                num_bone_frames = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_bone_frames):
                    frame = VmdBoneFrame()
                    frame.parse(f.read(VmdBoneFrame.size()))
                    self.bone_frames.append(frame)

                # Morph Frames
                num_morph_frames = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_morph_frames):
                    frame = VmdMorphFrame()
                    frame.parse(f.read(VmdMorphFrame.size()))
                    self.morph_frames.append(frame)

                # Camera Frames
                num_camera_frames = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_camera_frames):
                    frame = VmdCameraFrame()
                    frame.parse(f.read(VmdCameraFrame.size()))
                    self.camera_frames.append(frame)

                # Light Frames
                num_light_frames = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_light_frames):
                    frame = VmdLightFrame()
                    frame.parse(f.read(VmdLightFrame.size()))
                    self.light_frames.append(frame)

                # Shadow Frames
                num_shadow_frames = struct.unpack('<I', f.read(4))[0]
                for _ in range(num_shadow_frames):
                    frame = VmdShadowFrame()
                    frame.parse(f.read(VmdShadowFrame.size()))
                    self.shadow_frames.append(frame)

                # IK Show/Hide Frames
                # VMD 2.0ではIK表示/非表示フレームは存在しない場合があるため、ファイルの終端チェックを行う
                if f.tell() < os.fstat(f.fileno()).st_size:
                    num_ik_show_hide_frames = struct.unpack('<I', f.read(4))[0]
                    for _ in range(num_ik_show_hide_frames):
                        frame = VmdIKShowHideFrame()
                        # IK表示/非表示フレームは可変長なので、個別に読み込む
                        # まずは固定長部分を読み込み、ik_countを取得
                        fixed_data = f.read(VmdIKShowHideFrame.size())
                        frame.frame_number = struct.unpack_from('<I', fixed_data, 0)[0]
                        frame.visible = struct.unpack_from('<B', fixed_data, 4)[0]
                        frame.ik_count = struct.unpack_from('<I', fixed_data, 5)[0]

                        # ik_statesの可変長部分を読み込む
                        for _ in range(frame.ik_count):
                            ik_name = f.read(20).split(b'\x00')[0].decode('shift_jis')
                            show_flag = struct.unpack('<B', f.read(1))[0]
                            frame.ik_states.append((ik_name, show_flag))
                        self.ik_show_hide_frames.append(frame)

            except struct.error as e:
                raise MMDParseException(f"Failed to parse VMD file: {file_path}") from e
