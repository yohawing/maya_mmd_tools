"""
VMDファイルの構造を人間とLLMが読みやすい独自テキスト形式でダンプするツール
"""

import argparse
import sys
from typing import List, Optional, TextIO

from mmd_tools.core.vmd_data import VmdData


class VmdDumper:
    """VMDファイルの内容を人間が読みやすい形式でダンプするクラス"""

    def __init__(self, vmd_data: VmdData):
        """
        Args:
            vmd_data: 解析済みのVMDデータ
        """
        self.vmd = vmd_data

    def dump(
        self,
        output: Optional[TextIO] = None,
        sections: Optional[List[str]] = None,
    ) -> str:
        """
        VMDデータをダンプする

        Args:
            output: 出力先（None の場合は文字列として返す）
            sections: 出力するセクションのリスト（None の場合は全セクション）

        Returns:
            ダンプ結果の文字列（output が指定されている場合は空文字列）
        """
        buffer = []

        # 利用可能なセクション
        available_sections = {
            "header": self._dump_header,
            "statistics": self._dump_statistics,
            "bones": self._dump_bones,
            "morphs": self._dump_morphs,
            "camera": self._dump_camera,
            "light": self._dump_light,
            "shadow": self._dump_shadow,
            "ikdisplay": self._dump_ik_show_hide,
        }

        # セクションが指定されていない場合はデフォルトセット
        if sections is None:
            sections = [
                "header",
                "statistics",
                "bones",
                "morphs",
                "camera",
                "light",
                "shadow",
                "ikdisplay",
            ]

        # 各セクションを出力
        for section in sections:
            if section in available_sections:
                try:
                    content = available_sections[section]()
                    buffer.append(content)
                except Exception as e:
                    buffer.append(f"\n=== {section.upper()} [ERROR] ===")
                    buffer.append(f"Failed to dump section: {e}")

        result = "\n".join(buffer)

        if output:
            output.write(result)
            return ""
        else:
            return result

    def _dump_header(self) -> str:
        """ヘッダー情報をダンプ"""
        lines = [
            "=== VMD MOTION DEBUG DUMP ===",
            f"Model Name: {self.vmd.header.model_name}",
        ]

        return "\n".join(lines)

    def _dump_statistics(self) -> str:
        """統計情報をダンプ"""
        lines = [
            "\n=== STATISTICS ===",
            f"Bone Frames: {len(self.vmd.bone_frames):,}",
            f"Morph Frames: {len(self.vmd.morph_frames):,}",
            f"Camera Frames: {len(self.vmd.camera_frames):,}",
            f"Light Frames: {len(self.vmd.light_frames):,}",
            f"Shadow Frames: {len(self.vmd.shadow_frames):,}",
            f"IK/Show/Hide Frames: {len(self.vmd.ik_show_hide_frames):,}",
        ]

        # フレーム範囲を計算
        all_frames = []

        if self.vmd.bone_frames:
            all_frames.extend([f.frame_number for f in self.vmd.bone_frames])
        if self.vmd.morph_frames:
            all_frames.extend([f.frame_number for f in self.vmd.morph_frames])
        if self.vmd.camera_frames:
            all_frames.extend([f.frame_number for f in self.vmd.camera_frames])
        if self.vmd.light_frames:
            all_frames.extend([f.frame_number for f in self.vmd.light_frames])
        if self.vmd.shadow_frames:
            all_frames.extend([f.frame_number for f in self.vmd.shadow_frames])
        if self.vmd.ik_show_hide_frames:
            all_frames.extend([f.frame_number for f in self.vmd.ik_show_hide_frames])

        if all_frames:
            lines.append(f"\nFrame Range: {min(all_frames)} - {max(all_frames)}")
            lines.append(
                f"Duration: {max(all_frames) - min(all_frames)} frames ({(max(all_frames) - min(all_frames)) / 30:.2f} seconds @ 30fps)"
            )

        return "\n".join(lines)

    def _dump_bones(self) -> str:
        """ボーンフレーム情報をダンプ"""
        if not self.vmd.bone_frames:
            return "\n=== BONE FRAMES (0) ===\nNo bone frames found."

        lines = [f"\n=== BONE FRAMES ({len(self.vmd.bone_frames):,}) ==="]

        # ボーン名別に集計
        bone_counts = {}
        for frame in self.vmd.bone_frames:
            bone_name = frame.bone_name
            if bone_name not in bone_counts:
                bone_counts[bone_name] = []
            bone_counts[bone_name].append(frame.frame_number)

        lines.append(f"Unique Bones: {len(bone_counts)}")
        lines.append("\nBone Frame Counts:")

        # 最も多いボーンから表示（上位10個）
        sorted_bones = sorted(bone_counts.items(), key=lambda x: len(x[1]), reverse=True)
        for i, (bone_name, frames) in enumerate(sorted_bones[:10]):
            frame_range = f"[{min(frames)}-{max(frames)}]" if len(frames) > 1 else f"[{frames[0]}]"
            lines.append(f"  {bone_name}: {len(frames)} frames {frame_range}")

        if len(sorted_bones) > 10:
            lines.append(f"  ... and {len(sorted_bones) - 10} more bones")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.bone_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}: {frame.bone_name}")
            lines.append(f"    Pos: ({frame.position[0]:.3f}, {frame.position[1]:.3f}, {frame.position[2]:.3f})")
            lines.append(
                f"    Rot: ({frame.rotation[0]:.3f}, {frame.rotation[1]:.3f}, {frame.rotation[2]:.3f}, {frame.rotation[3]:.3f})"
            )

            # 補間曲線の情報
            if hasattr(frame, "interpolation") and frame.interpolation:
                interp = frame.interpolation
                lines.append(
                    f"    Interpolation: X({interp[0]},{interp[1]},{interp[2]},{interp[3]}) "
                    f"Y({interp[4]},{interp[5]},{interp[6]},{interp[7]}) "
                    f"Z({interp[8]},{interp[9]},{interp[10]},{interp[11]}) "
                    f"R({interp[12]},{interp[13]},{interp[14]},{interp[15]})"
                )

        if len(self.vmd.bone_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.bone_frames) - 5:,} more frames")

        return "\n".join(lines)

    def _dump_morphs(self) -> str:
        """モーフフレーム情報をダンプ"""
        if not self.vmd.morph_frames:
            return "\n=== MORPH FRAMES (0) ===\nNo morph frames found."

        lines = [f"\n=== MORPH FRAMES ({len(self.vmd.morph_frames):,}) ==="]

        # モーフ名別に集計
        morph_counts = {}
        for frame in self.vmd.morph_frames:
            morph_name = frame.morph_name
            if morph_name not in morph_counts:
                morph_counts[morph_name] = []
            morph_counts[morph_name].append(frame.frame_number)

        lines.append(f"Unique Morphs: {len(morph_counts)}")
        lines.append("\nMorph Frame Counts:")

        # 最も多いモーフから表示（上位10個）
        sorted_morphs = sorted(morph_counts.items(), key=lambda x: len(x[1]), reverse=True)
        for i, (morph_name, frames) in enumerate(sorted_morphs[:10]):
            frame_range = f"[{min(frames)}-{max(frames)}]" if len(frames) > 1 else f"[{frames[0]}]"
            lines.append(f"  {morph_name}: {len(frames)} frames {frame_range}")

        if len(sorted_morphs) > 10:
            lines.append(f"  ... and {len(sorted_morphs) - 10} more morphs")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.morph_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}: {frame.morph_name} = {frame.value:.3f}")

        if len(self.vmd.morph_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.morph_frames) - 5:,} more frames")

        return "\n".join(lines)

    def _dump_camera(self) -> str:
        """カメラフレーム情報をダンプ"""
        if not self.vmd.camera_frames:
            return "\n=== CAMERA FRAMES (0) ===\nNo camera frames found."

        lines = [f"\n=== CAMERA FRAMES ({len(self.vmd.camera_frames):,}) ==="]

        # フレーム範囲
        frame_times = [f.frame_number for f in self.vmd.camera_frames]
        lines.append(f"Frame Range: {min(frame_times)} - {max(frame_times)}")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.camera_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}:")
            lines.append(f"    Position: ({frame.position[0]:.3f}, {frame.position[1]:.3f}, {frame.position[2]:.3f})")
            lines.append(f"    Rotation: ({frame.rotation[0]:.3f}, {frame.rotation[1]:.3f}, {frame.rotation[2]:.3f})")
            lines.append(f"    Distance: {frame.distance:.3f}")
            lines.append(f"    FOV: {frame.viewing_angle}°")
            lines.append(f"    Perspective: {'ON' if frame.perspective else 'OFF'}")

        if len(self.vmd.camera_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.camera_frames) - 5:,} more frames")

        return "\n".join(lines)

    def _dump_light(self) -> str:
        """照明フレーム情報をダンプ"""
        if not self.vmd.light_frames:
            return "\n=== LIGHT FRAMES (0) ===\nNo light frames found."

        lines = [f"\n=== LIGHT FRAMES ({len(self.vmd.light_frames):,}) ==="]

        # フレーム範囲
        frame_times = [f.frame_number for f in self.vmd.light_frames]
        lines.append(f"Frame Range: {min(frame_times)} - {max(frame_times)}")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.light_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}:")
            lines.append(f"    Color: ({frame.color[0]:.3f}, {frame.color[1]:.3f}, {frame.color[2]:.3f})")
            lines.append(f"    Direction: ({frame.direction[0]:.3f}, {frame.direction[1]:.3f}, {frame.direction[2]:.3f})")

        if len(self.vmd.light_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.light_frames) - 5:,} more frames")

        return "\n".join(lines)

    def _dump_shadow(self) -> str:
        """セルフシャドウフレーム情報をダンプ"""
        if not self.vmd.shadow_frames:
            return "\n=== SHADOW FRAMES (0) ===\nNo shadow frames found."

        lines = [f"\n=== SHADOW FRAMES ({len(self.vmd.shadow_frames):,}) ==="]

        # フレーム範囲
        frame_times = [f.frame_number for f in self.vmd.shadow_frames]
        lines.append(f"Frame Range: {min(frame_times)} - {max(frame_times)}")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.shadow_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}:")
            lines.append(f"    Mode: {frame.mode}")
            lines.append(f"    Distance: {frame.distance:.3f}")

        if len(self.vmd.shadow_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.shadow_frames) - 5:,} more frames")

        return "\n".join(lines)

    def _dump_ik_show_hide(self) -> str:
        """IK表示/非表示フレーム情報をダンプ"""
        if not self.vmd.ik_show_hide_frames:
            return "\n=== IK SHOW/HIDE FRAMES (0) ===\nNo IK show/hide frames found."

        lines = [f"\n=== IK SHOW/HIDE FRAMES ({len(self.vmd.ik_show_hide_frames):,}) ==="]

        # フレーム範囲
        frame_times = [f.frame_number for f in self.vmd.ik_show_hide_frames]
        lines.append(f"Frame Range: {min(frame_times)} - {max(frame_times)}")

        # サンプルフレーム（最初の5フレーム）
        lines.append("\nSample Frames:")
        for i, frame in enumerate(self.vmd.ik_show_hide_frames[:5]):
            lines.append(f"  Frame {frame.frame_number}:")
            lines.append(f"    Visible: {'ON' if frame.visible else 'OFF'}")

            if hasattr(frame, "ik_states") and frame.ik_states:
                lines.append(f"    IK States: {len(frame.ik_states)} IKs")
                for ik_name, show_flag in frame.ik_states[:3]:
                    lines.append(f"      {ik_name}: {'ON' if show_flag else 'OFF'}")
                if len(frame.ik_states) > 3:
                    lines.append(f"      ... and {len(frame.ik_states) - 3} more IKs")

        if len(self.vmd.ik_show_hide_frames) > 5:
            lines.append(f"  ... and {len(self.vmd.ik_show_hide_frames) - 5:,} more frames")

        return "\n".join(lines)


def main():
    """CLIエントリーポイント"""
    parser = argparse.ArgumentParser(description="VMDファイルの構造を人間が読みやすい形式でダンプします")
    parser.add_argument("vmd_file", help="ダンプするVMDファイルのパス")
    parser.add_argument("-o", "--output", help="出力ファイルパス（指定しない場合は標準出力）")
    parser.add_argument(
        "-s",
        "--sections",
        nargs="+",
        choices=[
            "header",
            "statistics",
            "bones",
            "morphs",
            "camera",
            "light",
            "shadow",
            "ikdisplay",
        ],
        help="出力するセクションを指定（デフォルト: 全セクション）",
    )

    args = parser.parse_args()

    try:
        # VMDファイルを解析
        vmd_parser = VmdData()
        vmd_parser.parse_file(args.vmd_file)

        # ダンパーを作成
        dumper = VmdDumper(vmd_parser)

        # 出力先を決定
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                dumper.dump(f, sections=args.sections)
            print(f"Dump completed: {args.output}")
        else:
            print(dumper.dump(sections=args.sections))

    except Exception as e:
        print(f"Failed to dump VMD file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
