"""VPDファイルのデータ構造とパーサー"""

import os
import re
from typing import List

from mmd_tools.core.logger import get_logger

from mmd_tools.core.exceptions import MMDParseException
from .header import VpdHeader
from .bone_pose import BonePose

logger = get_logger(__name__)


class VpdData:
    """VPDファイルのデータを管理するクラス

    VPDファイルの読み込みと解析を行います。書き出しはmmd-animが担当します。

    Attributes:
        header (VpdHeader): ヘッダー情報
        bone_poses (List[BonePose]): ボーンポーズのリスト
    """

    def __init__(self) -> None:
        """VpdDataの初期化"""
        self.header: VpdHeader = VpdHeader()
        self.bone_poses: List[BonePose] = []

    def parse_file(self, file_path: str) -> None:
        """VPDファイルを解析する

        Args:
            file_path (str): VPDファイルのパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
            MMDParseException: ファイルの解析に失敗した場合
        """
        logger.info(f"Starting VPD file parsing: {file_path}")

        if not os.path.exists(file_path):
            logger.error(f"VPD file not found: {file_path}")
            raise FileNotFoundError(f"VPD file not found: {file_path}")

        try:
            # ファイルをShift-JISで読み込み
            with open(file_path, "r", encoding="shift-jis") as f:
                content = f.read()
        except UnicodeDecodeError:
            # Shift-JIS以外のエンコーディングを試す
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                logger.error(f"Could not detect VPD file encoding: {file_path}")
                raise MMDParseException(f"Failed to decode VPD file: {file_path}")

        # 解析処理
        self._parse_content(content)

        logger.info(f"VPD file parsing completed: {len(self.bone_poses)} bone poses")

    def _parse_content(self, content: str) -> None:
        """VPDファイルの内容を解析する

        Args:
            content (str): ファイルの内容

        Raises:
            MMDParseException: 解析に失敗した場合
        """
        lines = content.strip().split("\n")

        if not lines:
            raise MMDParseException("Empty VPD file")

        # ヘッダーの解析
        line_index = 0

        # シグネチャーの確認
        if not lines[line_index].startswith("Vocaloid Pose Data"):
            raise MMDParseException(f"Invalid VPD file signature: {lines[line_index]}")
        self.header.signature = lines[line_index].strip()
        line_index += 1

        # 空行をスキップ
        while line_index < len(lines) and not lines[line_index].strip():
            line_index += 1

        # 親ファイル名（オプション）
        if line_index < len(lines) and lines[line_index].endswith(";"):
            self.header.parent_file = lines[line_index].rstrip(";").strip()
            line_index += 1

        # 空行をスキップ
        while line_index < len(lines) and not lines[line_index].strip():
            line_index += 1

        # ボーン数（オプション）
        if line_index < len(lines) and lines[line_index].strip().endswith(";"):
            try:
                bone_count_str = lines[line_index].rstrip(";").strip()
                self.header.bone_count = int(bone_count_str)
            except ValueError:
                logger.warning(f"Invalid bone count: {lines[line_index]}")
            line_index += 1

        # 空行をスキップ
        while line_index < len(lines) and not lines[line_index].strip():
            line_index += 1

        # ボーンポーズの解析
        self.bone_poses = []
        bone_pattern = re.compile(r"Bone(\d+)\{(.+)")

        while line_index < len(lines):
            line = lines[line_index].strip()

            # ボーンの開始を検出
            match = bone_pattern.match(line)
            if match:
                bone_pose = BonePose()
                bone_pose.bone_index = int(match.group(1))
                bone_pose.bone_name = match.group(2).strip()

                # 次の行から位置と回転を読み取る
                line_index += 1

                # 位置の解析
                if line_index < len(lines):
                    pos_line = lines[line_index].strip()
                    # カンマ区切りの数値を抽出
                    pos_match = re.findall(r"[-+]?\d*\.?\d+", pos_line)
                    if len(pos_match) >= 3:
                        bone_pose.position = [float(pos_match[0]), float(pos_match[1]), float(pos_match[2])]
                    line_index += 1

                # 回転（四元数）の解析
                if line_index < len(lines):
                    quat_line = lines[line_index].strip()
                    # カンマ区切りの数値を抽出
                    quat_match = re.findall(r"[-+]?\d*\.?\d+", quat_line)
                    if len(quat_match) >= 4:
                        bone_pose.quaternion = [
                            float(quat_match[0]),
                            float(quat_match[1]),
                            float(quat_match[2]),
                            float(quat_match[3]),
                        ]
                    line_index += 1

                # }をスキップ
                if line_index < len(lines) and lines[line_index].strip() == "}":
                    line_index += 1

                self.bone_poses.append(bone_pose)
            else:
                line_index += 1

        # ボーン数が指定されていない場合は実際の数を設定
        if self.header.bone_count == 0:
            self.header.bone_count = len(self.bone_poses)

    def __repr__(self) -> str:
        """文字列表現を返す"""
        return f"VpdData(header={self.header}, bone_poses={len(self.bone_poses)} bones)"

    def __str__(self) -> str:
        """読みやすい文字列表現を返す"""
        result = str(self.header) + "\n"
        result += f"Bone Poses: {len(self.bone_poses)} bones\n"
        for i, pose in enumerate(self.bone_poses[:5]):  # 最初の5つだけ表示
            result += f"  [{i}] {pose.bone_name}\n"
        if len(self.bone_poses) > 5:
            result += f"  ... and {len(self.bone_poses) - 5} more bones"
        return result
