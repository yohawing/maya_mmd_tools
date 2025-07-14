"""
Bone validation module for MMD models.

This module provides functionality to validate bone structures
against MMD standard bone requirements.
"""

from typing import Dict, List, Tuple, Optional, Set


class BoneValidator:
    """標準ボーンの存在と命名規則をチェックするクラス"""

    # 必須標準ボーン定義（日本語名: [英語名のバリエーション]）
    STANDARD_BONES: Dict[str, List[str]] = {
        # コアボーン
        "センター": ["center", "センター"],
        "下半身": ["lower_body", "下半身"],
        "上半身": ["upper_body", "上半身"],
        "首": ["neck", "首"],
        "頭": ["head", "頭"],
        # 体幹ボーン
        "上半身2": ["upper_body_2", "上半身2", "上半身２"],
        "左目": ["left_eye", "左目"],
        "右目": ["right_eye", "右目"],
        # 腕ボーン（左）
        "左肩": ["left_shoulder", "左肩"],
        "左腕": ["left_arm", "左腕"],
        "左ひじ": ["left_elbow", "左ひじ", "左肘"],
        "左手首": ["left_wrist", "左手首"],
        # 腕ボーン（右）
        "右肩": ["right_shoulder", "右肩"],
        "右腕": ["right_arm", "右腕"],
        "右ひじ": ["right_elbow", "右ひじ", "右肘"],
        "右手首": ["right_wrist", "右手首"],
        # 脚・足IKボーン（左）
        "左足": ["left_leg", "左足"],
        "左ひざ": ["left_knee", "左ひざ", "左膝"],
        "左足首": ["left_ankle", "左足首"],
        "左足ＩＫ": ["left_leg_ik", "左足ＩＫ", "左足IK"],
        "左つま先": ["left_toe", "左つま先"],
        "左つま先ＩＫ": ["left_toe_ik", "左つま先ＩＫ", "左つま先IK"],
        # 脚・足IKボーン（右）
        "右足": ["right_leg", "右足"],
        "右ひざ": ["right_knee", "右ひざ", "右膝"],
        "右足首": ["right_ankle", "右足首"],
        "右足ＩＫ": ["right_leg_ik", "右足ＩＫ", "右足IK"],
        "右つま先": ["right_toe", "右つま先"],
        "右つま先ＩＫ": ["right_toe_ik", "右つま先ＩＫ", "右つま先IK"],
    }

    # 指ボーンの定義
    FINGER_BONES: Dict[str, Dict[str, Dict[str, List[str]]]] = {
        "左": {
            "親指": {
                "左親指０": ["left_thumb_0", "左親指０", "左親指0"],
                "左親指１": ["left_thumb_1", "左親指１", "左親指1"],
                "左親指２": ["left_thumb_2", "左親指２", "左親指2"],
            },
            "人指": {
                "左人指１": [
                    "left_index_1",
                    "左人指１",
                    "左人指1",
                    "左人差指１",
                    "左人差指1",
                ],
                "左人指２": [
                    "left_index_2",
                    "左人指２",
                    "左人指2",
                    "左人差指２",
                    "左人差指2",
                ],
                "左人指３": [
                    "left_index_3",
                    "左人指３",
                    "左人指3",
                    "左人差指３",
                    "左人差指3",
                ],
            },
            "中指": {
                "左中指１": ["left_middle_1", "左中指１", "左中指1"],
                "左中指２": ["left_middle_2", "左中指２", "左中指2"],
                "左中指３": ["left_middle_3", "左中指３", "左中指3"],
            },
            "薬指": {
                "左薬指１": ["left_ring_1", "左薬指１", "左薬指1"],
                "左薬指２": ["left_ring_2", "左薬指２", "左薬指2"],
                "左薬指３": ["left_ring_3", "左薬指３", "左薬指3"],
            },
            "小指": {
                "左小指１": ["left_pinky_1", "左小指１", "左小指1"],
                "左小指２": ["left_pinky_2", "左小指２", "左小指2"],
                "左小指３": ["left_pinky_3", "左小指３", "左小指3"],
            },
        },
        "右": {
            "親指": {
                "右親指０": ["right_thumb_0", "右親指０", "右親指0"],
                "右親指１": ["right_thumb_1", "右親指１", "右親指1"],
                "右親指２": ["right_thumb_2", "右親指２", "右親指2"],
            },
            "人指": {
                "右人指１": [
                    "right_index_1",
                    "右人指１",
                    "右人指1",
                    "右人差指１",
                    "右人差指1",
                ],
                "右人指２": [
                    "right_index_2",
                    "右人指２",
                    "右人指2",
                    "右人差指２",
                    "右人差指2",
                ],
                "右人指３": [
                    "right_index_3",
                    "右人指３",
                    "右人指3",
                    "右人差指３",
                    "右人差指3",
                ],
            },
            "中指": {
                "右中指１": ["right_middle_1", "右中指１", "右中指1"],
                "右中指２": ["right_middle_2", "右中指２", "右中指2"],
                "右中指３": ["right_middle_3", "右中指３", "右中指3"],
            },
            "薬指": {
                "右薬指１": ["right_ring_1", "右薬指１", "右薬指1"],
                "右薬指２": ["right_ring_2", "右薬指２", "右薬指2"],
                "右薬指３": ["right_ring_3", "右薬指３", "右薬指3"],
            },
            "小指": {
                "右小指１": ["right_pinky_1", "右小指１", "右小指1"],
                "右小指２": ["right_pinky_2", "右小指２", "右小指2"],
                "右小指３": ["right_pinky_3", "右小指３", "右小指3"],
            },
        },
    }

    # 準標準ボーン定義
    SEMI_STANDARD_BONES: Dict[str, List[str]] = {
        "グルーブ": ["groove", "グルーブ"],
        "腰": ["waist", "腰"],
        "足IK親": ["leg_ik_parent", "足IK親", "足ＩＫ親"],
        "足先EX": ["toe_ex", "足先EX", "足先ＥＸ"],
        "全ての親": ["parent_of_all", "全ての親"],
        "操作中心": ["operation_center", "操作中心"],
        "左手捻": ["left_hand_twist", "左手捻", "左手捩"],
        "左腕捻": ["left_arm_twist", "左腕捻", "左腕捩"],
        "右手捻": ["right_hand_twist", "右手捻", "右手捩"],
        "右腕捻": ["right_arm_twist", "右腕捻", "右腕捩"],
    }

    def __init__(self):
        """初期化"""
        self._build_name_mapping()

    def _build_name_mapping(self):
        """ボーン名のマッピングを構築"""
        self.name_to_standard: Dict[str, str] = {}

        # 標準ボーンのマッピング
        for standard_name, variations in self.STANDARD_BONES.items():
            for variation in variations:
                self.name_to_standard[variation.lower()] = standard_name

        # 指ボーンのマッピング
        for side, fingers in self.FINGER_BONES.items():
            for finger_type, bones in fingers.items():
                for standard_name, variations in bones.items():
                    for variation in variations:
                        self.name_to_standard[variation.lower()] = standard_name

        # 準標準ボーンのマッピング
        for standard_name, variations in self.SEMI_STANDARD_BONES.items():
            for variation in variations:
                self.name_to_standard[variation.lower()] = standard_name

    def validate_bones(
        self, bones: List[str]
    ) -> Tuple[List[str], List[Dict[str, str]], Dict[str, str]]:
        """
        標準ボーンの存在確認と命名規則の検証

        Args:
            bones: 検証対象のボーン名リスト

        Returns:
            Tuple[missing_bones, naming_issues, bone_mapping]:
                missing_bones: 不足している標準ボーンのリスト
                naming_issues: 命名規則の問題リスト
                bone_mapping: ボーン名の標準名へのマッピング
        """
        found_standard_bones: Set[str] = set()
        naming_issues: List[Dict[str, str]] = []
        bone_mapping: Dict[str, str] = {}

        # 各ボーンをチェック
        for bone_name in bones:
            lower_name = bone_name.lower()

            if lower_name in self.name_to_standard:
                standard_name = self.name_to_standard[lower_name]
                found_standard_bones.add(standard_name)
                bone_mapping[bone_name] = standard_name

                # 命名規則の警告（全角・半角の混在など）
                if self._check_naming_issue(bone_name, standard_name):
                    naming_issues.append(
                        {
                            "bone": bone_name,
                            "issue": "全角・半角文字の混在",
                            "suggestion": self._get_preferred_name(standard_name),
                        }
                    )

        # 不足している必須ボーンを特定
        required_bones = set(self.STANDARD_BONES.keys())
        missing_bones = list(required_bones - found_standard_bones)

        # 指ボーンの不足をチェック
        missing_fingers = self._check_missing_fingers(found_standard_bones)
        missing_bones.extend(missing_fingers)

        return sorted(missing_bones), naming_issues, bone_mapping

    def _check_naming_issue(self, bone_name: str, standard_name: str) -> bool:
        """命名規則の問題をチェック"""
        # IKボーンの全角・半角チェック
        if "IK" in bone_name or "ＩＫ" in bone_name:
            has_halfwidth_ik = "IK" in bone_name
            has_fullwidth_ik = "ＩＫ" in bone_name
            if has_halfwidth_ik and has_fullwidth_ik:
                return True

        # 数字の全角・半角チェック
        has_halfwidth_num = any(c in "0123456789" for c in bone_name)
        has_fullwidth_num = any(c in "０１２３４５６７８９" for c in bone_name)
        if has_halfwidth_num and has_fullwidth_num:
            return True

        return False

    def _get_preferred_name(self, standard_name: str) -> str:
        """推奨されるボーン名を取得"""
        if standard_name in self.STANDARD_BONES:
            return self.STANDARD_BONES[standard_name][0]

        # 指ボーンの推奨名を探す
        for side, fingers in self.FINGER_BONES.items():
            for finger_type, bones in fingers.items():
                if standard_name in bones:
                    return bones[standard_name][0]

        if standard_name in self.SEMI_STANDARD_BONES:
            return self.SEMI_STANDARD_BONES[standard_name][0]

        return standard_name

    def _check_missing_fingers(self, found_bones: Set[str]) -> List[str]:
        """不足している指ボーンをチェック"""
        missing = []

        for side, fingers in self.FINGER_BONES.items():
            for finger_type, bones in fingers.items():
                finger_found = False
                for bone_name in bones.keys():
                    if bone_name in found_bones:
                        finger_found = True
                        break

                if not finger_found:
                    # この指の全てのボーンが不足
                    missing.extend(bones.keys())

        return missing

    def get_bone_info(self, bone_name: str) -> Optional[Dict[str, any]]:
        """
        指定されたボーン名の情報を取得

        Args:
            bone_name: 検索するボーン名

        Returns:
            ボーン情報の辞書、見つからない場合はNone
        """
        lower_name = bone_name.lower()

        if lower_name not in self.name_to_standard:
            return None

        standard_name = self.name_to_standard[lower_name]

        # ボーンのタイプを判定
        bone_type = "standard"
        if standard_name in self.SEMI_STANDARD_BONES:
            bone_type = "semi_standard"
        elif any(
            standard_name in bones
            for fingers in self.FINGER_BONES.values()
            for bones in fingers.values()
        ):
            bone_type = "finger"

        return {
            "name": bone_name,
            "standard_name": standard_name,
            "type": bone_type,
            "preferred_name": self._get_preferred_name(standard_name),
        }

    def validate_bone_hierarchy(self, bones: List[any]) -> Dict[str, List[str]]:
        """
        ボーンの階層構造を検証する。

        Args:
            bones: ボーンオブジェクトのリスト（parent_bone_indexとget_name()メソッドを持つ）

        Returns:
            dict: 階層の問題を含む辞書
                - orphan_bones: 親が存在しない孤立したボーン
                - circular_references: 循環参照のあるボーン
                - invalid_references: 無効な親インデックスを持つボーン
        """
        issues = {
            "orphan_bones": [],
            "circular_references": [],
            "invalid_references": [],
        }

        # ボーンインデックスと名前のマッピング
        index_to_name = {i: bone.get_name() for i, bone in enumerate(bones)}

        # 各ボーンの親子関係をチェック
        for i, bone in enumerate(bones):
            if hasattr(bone, "parent_bone_index"):
                parent_idx = bone.parent_bone_index

                # 無効な親インデックスのチェック
                if parent_idx >= len(bones):
                    issues["invalid_references"].append(
                        {
                            "bone": bone.get_name(),
                            "index": i,
                            "parent_index": parent_idx,
                            "max_index": len(bones) - 1,
                        }
                    )
                elif parent_idx != -1:
                    # 循環参照のチェック
                    if self._check_circular_reference(i, parent_idx, bones):
                        issues["circular_references"].append(
                            {"bone": bone.get_name(), "index": i}
                        )

        return issues

    def _check_circular_reference(
        self, bone_index: int, parent_index: int, bones: List[any]
    ) -> bool:
        """循環参照をチェックする"""
        visited = set()
        current = parent_index

        while current != -1 and current < len(bones):
            if current in visited:
                return True
            if current == bone_index:
                return True
            visited.add(current)

            if hasattr(bones[current], "parent_bone_index"):
                current = bones[current].parent_bone_index
            else:
                break

        return False

    def generate_report(self, bones: List[str]) -> str:
        """
        ボーン構造の検証レポートを生成

        Args:
            bones: 検証対象のボーン名リスト

        Returns:
            検証レポートの文字列
        """
        missing_bones, naming_issues, bone_mapping = self.validate_bones(bones)

        report = ["=== MMDボーン構造検証レポート ===\n"]

        # サマリー
        report.append(f"検証対象ボーン数: {len(bones)}")
        report.append(f"標準ボーン検出数: {len(bone_mapping)}")
        report.append(f"不足ボーン数: {len(missing_bones)}")
        report.append(f"命名規則の問題: {len(naming_issues)}\n")

        # 不足ボーン
        if missing_bones:
            report.append("【不足している標準ボーン】")
            for bone in missing_bones:
                report.append(f"  - {bone}")
            report.append("")

        # 命名規則の問題
        if naming_issues:
            report.append("【命名規則の問題】")
            for issue in naming_issues:
                report.append(f"  - {issue['bone']}: {issue['issue']}")
                report.append(f"    推奨: {issue['suggestion']}")
            report.append("")

        # 検出された標準ボーン
        if bone_mapping:
            report.append("【検出された標準ボーン】")
            for original, standard in sorted(bone_mapping.items()):
                if original != standard:
                    report.append(f"  - {original} → {standard}")
                else:
                    report.append(f"  - {original}")

        return "\n".join(report)
