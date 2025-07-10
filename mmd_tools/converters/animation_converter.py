"""
VMDアニメーションをMayaに変換するコンバーター
"""

from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import maya.cmds as cmds
import maya.api.OpenMaya as om
from mmd_tools.core.logger import get_logger
from mmd_tools.core.vmd_parser import VmdParser


class VmdConverter:
    """
    VMDファイルのアニメーションデータをMayaのアニメーションに変換するクラス
    """

    def __init__(self):
        self.bone_name_mapping = {}  # VMDボーン名 -> Mayaジョイント名
        self.morph_name_mapping = {}  # VMDモーフ名 -> Mayaブレンドシェイプターゲット名
        self.fps = 30.0
        self.logger = get_logger("VmdConverter")

    def convert(self, vmd_data: VmdParser, target_namespace: str = None) -> bool:
        """
        メイン変換処理

        Args:
            vmd_data: パース済みのVMDデータ
            target_namespace: ターゲットのネームスペース

        Returns:
            bool: 変換成功の可否
        """
        try:
            self.logger.info("VMDアニメーション変換を開始します")

            # 名前マッピングの構築
            self._build_name_mappings(target_namespace)

            # タイムラインの設定
            self._setup_timeline(vmd_data)

            # ボーンアニメーション変換
            if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
                # リストを辞書に変換
                bone_frames_dict = self._organize_bone_frames(vmd_data.bone_frames)
                self._convert_bone_animation(bone_frames_dict)

            # モーフアニメーション変換（フェーズ2で実装）
            # if hasattr(vmd_data, 'morph_frames') and vmd_data.morph_frames:
            #     self._convert_morph_animation(vmd_data.morph_frames)

            self.logger.info("VMDアニメーション変換が完了しました")
            return True

        except Exception as e:
            self.logger.error(f"VMDアニメーション変換中にエラーが発生しました: {e}")
            return False

    def _build_name_mappings(self, target_namespace: str = None):
        """
        名前マッピングの構築

        Args:
            target_namespace: ターゲットのネームスペース
        """
        self.logger.debug("名前マッピングを構築中...")

        # ジョイントの名前マッピング
        joints = cmds.ls(type="joint", long=True)
        if target_namespace:
            joints = [j for j in joints if j.startswith(f"|{target_namespace}:")]

        for joint in joints:
            # カスタム属性から元の名前を取得
            for attr in ["pmx_bone_name", "pmd_bone_name"]:
                if cmds.attributeQuery(attr, node=joint, exists=True):
                    original_name = cmds.getAttr(f"{joint}.{attr}")
                    if original_name:
                        short_name = joint.split("|")[-1]
                        self.bone_name_mapping[original_name] = short_name
                        self.logger.debug(
                            f"ボーンマッピング: {original_name} -> {short_name}"
                        )
                        break

    def _organize_bone_frames(self, bone_frames_list: List) -> Dict[str, List]:
        """
        ボーンフレームのリストを辞書に整理

        Args:
            bone_frames_list: ボーンフレームのリスト

        Returns:
            ボーン名をキーとする辞書
        """
        organized = defaultdict(list)
        for frame in bone_frames_list:
            organized[frame.bone_name].append(frame)

        # フレーム番号順にソート
        for bone_name in organized:
            organized[bone_name].sort(key=lambda f: f.frame_number)

        return dict(organized)

    def _setup_timeline(self, vmd_data: VmdParser):
        """
        タイムラインの設定

        Args:
            vmd_data: VMDデータ
        """
        # 最大フレーム番号を取得
        max_frame = 0
        if hasattr(vmd_data, "bone_frames") and vmd_data.bone_frames:
            for frame in vmd_data.bone_frames:
                max_frame = max(max_frame, frame.frame_number)

        if hasattr(vmd_data, "morph_frames") and vmd_data.morph_frames:
            for frame in vmd_data.morph_frames:
                max_frame = max(max_frame, frame.frame_number)

        # タイムラインの範囲を設定
        if max_frame > 0:
            cmds.playbackOptions(min=0, max=max_frame, ast=0, aet=max_frame)
            self.logger.info(f"タイムライン範囲を設定: 0 - {max_frame}")

    def _convert_bone_animation(self, bone_frames: Dict):
        """
        ボーンアニメーション変換

        Args:
            bone_frames: ボーンフレームデータの辞書
        """
        self.logger.info("ボーンアニメーション変換を開始...")

        converted_count = 0
        skipped_count = 0

        for vmd_bone_name, frames in bone_frames.items():
            # Mayaジョイント名を取得
            maya_joint_name = self._get_maya_joint_name(vmd_bone_name)

            if not maya_joint_name:
                self.logger.warning(
                    f"ボーン '{vmd_bone_name}' に対応するMayaジョイントが見つかりません"
                )
                skipped_count += 1
                continue

            # キーフレームを設定
            self._apply_bone_keyframes(maya_joint_name, frames)
            converted_count += 1

        self.logger.info(
            f"ボーンアニメーション変換完了: {converted_count}個成功, {skipped_count}個スキップ"
        )

    def _get_maya_joint_name(self, vmd_bone_name: str) -> Optional[str]:
        """
        VMDボーン名からMayaジョイント名を取得

        Args:
            vmd_bone_name: VMDのボーン名

        Returns:
            Mayaのジョイント名（見つからない場合はNone）
        """
        # 完全一致
        if vmd_bone_name in self.bone_name_mapping:
            return self.bone_name_mapping[vmd_bone_name]

        # サニタイズした名前で検索
        sanitized_name = self._sanitize_name(vmd_bone_name)
        for original, maya_name in self.bone_name_mapping.items():
            if self._sanitize_name(original) == sanitized_name:
                return maya_name

        # 部分一致（将来的に実装）

        return None

    def _sanitize_name(self, name: str) -> str:
        """
        名前をサニタイズ（正規化）

        Args:
            name: 元の名前

        Returns:
            サニタイズされた名前
        """
        import unicodedata

        # Unicode正規化
        normalized = unicodedata.normalize("NFKC", name)
        # 空白を除去
        return normalized.strip()

    def _apply_bone_keyframes(self, joint_name: str, frames: List):
        """
        ジョイントにキーフレームを適用

        Args:
            joint_name: Mayaのジョイント名
            frames: フレームデータのリスト
        """
        # 既存のキーフレームをクリア（オプション）
        # cmds.cutKey(joint_name, attribute=['translateX', 'translateY', 'translateZ',
        #                                   'rotateX', 'rotateY', 'rotateZ'])

        for frame in frames:
            frame_time = float(frame.frame_number)

            # 位置の設定（座標系変換を適用）
            tx = frame.position[0]
            ty = frame.position[1]
            tz = -frame.position[2]  # Z軸反転

            # 回転の設定（クォータニオンからオイラー角へ）
            rx, ry, rz = self._quaternion_to_euler(frame.rotation)

            # キーフレームを設定
            cmds.setKeyframe(
                joint_name, attribute="translateX", value=tx, time=frame_time
            )
            cmds.setKeyframe(
                joint_name, attribute="translateY", value=ty, time=frame_time
            )
            cmds.setKeyframe(
                joint_name, attribute="translateZ", value=tz, time=frame_time
            )

            cmds.setKeyframe(joint_name, attribute="rotateX", value=rx, time=frame_time)
            cmds.setKeyframe(joint_name, attribute="rotateY", value=ry, time=frame_time)
            cmds.setKeyframe(joint_name, attribute="rotateZ", value=rz, time=frame_time)

            # 線形補間を設定（フェーズ1）
            for attr in [
                "translateX",
                "translateY",
                "translateZ",
                "rotateX",
                "rotateY",
                "rotateZ",
            ]:
                cmds.keyTangent(
                    joint_name,
                    attribute=attr,
                    time=(frame_time,),
                    inTangentType="linear",
                    outTangentType="linear",
                )

    def _quaternion_to_euler(self, quat: List[float]) -> Tuple[float, float, float]:
        """
        クォータニオンをオイラー角（度）に変換

        Args:
            quat: クォータニオン [x, y, z, w]

        Returns:
            オイラー角 (rx, ry, rz) in degrees
        """
        # Maya APIを使用してクォータニオンをオイラー角に変換
        quaternion = om.MQuaternion(quat[0], quat[1], quat[2], quat[3])
        euler = quaternion.asEulerRotation()

        # ラジアンから度に変換
        import math

        rx = math.degrees(euler.x)
        ry = math.degrees(euler.y)
        rz = math.degrees(euler.z)

        return rx, ry, rz

    def _convert_morph_animation(self, morph_frames: Dict):
        """
        モーフアニメーション変換（フェーズ2で実装）

        Args:
            morph_frames: モーフフレームデータの辞書
        """
        # TODO: フェーズ2で実装
        pass

    def _apply_interpolation(self, frame_data: List) -> List:
        """
        補間曲線の適用（フェーズ3で実装）

        Args:
            frame_data: フレームデータのリスト

        Returns:
            補間が適用されたデータ
        """
        # TODO: フェーズ3で実装
        pass

    def _set_keyframes(self, node: str, attribute: str, keyframes: List):
        """
        Mayaへのキーフレーム設定（ヘルパーメソッド）

        Args:
            node: ノード名
            attribute: アトリビュート名
            keyframes: キーフレームデータのリスト
        """
        for keyframe in keyframes:
            cmds.setKeyframe(
                node,
                attribute=attribute,
                value=keyframe["value"],
                time=keyframe["time"],
            )


# 既存のAnimationConverterクラスは互換性のために残す
class AnimationConverter:
    """
    MMDのアニメーションデータをMayaのキーフレームアニメーションに変換するクラス。
    """

    def __init__(self):
        pass

    def convert_vmd_animation(self, vmd_data):
        """
        VMDのアニメーションデータをMayaのキーフレームアニメーションに変換する。

        Args:
            vmd_data (VmdParser): 解析されたVMDデータオブジェクト。

        Returns:
            None
        """
        # VmdConverterに処理を委譲
        converter = VmdConverter()
        converter.convert(vmd_data)
