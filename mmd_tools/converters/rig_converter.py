from tkinter import W
from typing import List, Dict, Optional

import maya.cmds as cmds

from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core import maya_utils
from mmd_tools.core import utils
from mmd_tools.core.logger import get_logger
from mmd_tools.validation.bone_validator import BoneValidator
from mmd_tools.core.settings import settings


class RigConverter:
    """
    Mayaジョイントに対してMMDのリグシステムをセットアップするクラス。
    IKチェーン、ローカル軸、準標準ボーン、付与ボーンなどの
    高度なリグ機能を構築する。
    """

    def __init__(self):
        """
        コンストラクタ。
        """
        self.logger = get_logger(__name__)
        self.bone_validator = BoneValidator()
        self.original_bone_names = {}  # ボーンインデックスから元の日本語名へのマッピング

    def setup_pmx_rig(
        self,
        pmx_data,
        maya_joints: List[str],
        bone_map: Dict[int, str],
        skeleton_group: str,
    ) -> Dict:
        """
        PMXボーンデータを元にMayaのリグシステムをセットアップする。

        Args:
            pmx_data: PMXパーサーデータ
            maya_joints: 作成されたMayaジョイントのリスト
            bone_map: ボーンインデックスからジョイント名へのマッピング
            skeleton_group: スケルトングループ名

        Returns:
            dict: セットアップ結果の情報
        """
        result = {
            "ik_handles": [],
            "semi_standard_bones": {},
            "constraints": [],
            "validation_report": None,
        }

        # ボーン構造の検証を実行
        validation_result = self.validate_bones(pmx_data.bones)
        result["validation_report"] = validation_result
        self.print_validation_report(pmx_data.bones)

        # IKチェーンを抽出してMayaのIKハンドルを作成
        ik_chains = self._extract_ik_chains(pmx_data.bones, bone_map)
        if ik_chains:
            self.logger.info(f"{len(ik_chains)}個のIKチェーンを検出しました")
            result["ik_handles"] = self._create_maya_ik_handles(ik_chains)
            self.logger.info(f"{len(result['ik_handles'])}個のIKハンドルを作成しました")

        # 元のボーン名を保存（日本語名での重複チェック用）
        for i, bone in enumerate(pmx_data.bones):
            self.original_bone_names[i] = bone.get_name()

        # ボーンのローカル軸を設定
        self._apply_bone_local_axes(pmx_data.bones, maya_joints)

        # 準標準ボーンを追加（設定による）
        if settings.get("import.rig.add_semi_standard_bones", False):
            result["semi_standard_bones"] = self._add_semi_standard_bones(
                maya_joints, bone_map, skeleton_group
            )
            if result["semi_standard_bones"]:
                self.logger.info(
                    f"{len(result['semi_standard_bones'])}個の準標準ボーンを追加しました"
                )

        # 付与ボーンの設定
        result["constraints"] = self._setup_given_parent_bones(
            pmx_data.bones, maya_joints
        )
        if result["constraints"]:
            self.logger.info(f"{len(result['constraints'])}個の付与関係を設定しました")

        return result

    def setup_pmd_rig(
        self,
        pmd_data,
        maya_joints: List[str],
        bone_map: Dict[int, str],
        skeleton_group: str,
    ) -> Dict:
        """
        PMDボーンデータを元にMayaのリグシステムをセットアップする。

        Args:
            pmd_data: PMDパーサーデータ
            maya_joints: 作成されたMayaジョイントのリスト
            bone_map: ボーンインデックスからジョイント名へのマッピング
            skeleton_group: スケルトングループ名

        Returns:
            dict: セットアップ結果の情報
        """
        result = {
            "ik_handles": [],
            "semi_standard_bones": {},
            "constraints": [],
            "validation_report": None,
        }

        # ボーン構造の検証を実行
        validation_result = self.validate_bones(pmd_data.bones)
        result["validation_report"] = validation_result
        self.print_validation_report(pmd_data.bones)

        # IKチェーンを抽出してMayaのIKハンドルを作成
        ik_chains = self._extract_ik_chains(pmd_data.bones, bone_map, pmd_data.ik_data)
        if ik_chains:
            self.logger.info(f"{len(ik_chains)}個のIKチェーンを検出しました")
            result["ik_handles"] = self._create_maya_ik_handles(ik_chains)
            self.logger.info(f"{len(result['ik_handles'])}個のIKハンドルを作成しました")

        # 元のボーン名を保存（日本語名での重複チェック用）
        for i, bone in enumerate(pmd_data.bones):
            self.original_bone_names[i] = bone.get_name()

        # 準標準ボーンを追加（設定による）
        if settings.get("import.rig.add_semi_standard_bones", False):
            result["semi_standard_bones"] = self._add_semi_standard_bones(
                maya_joints, bone_map, skeleton_group
            )
            if result["semi_standard_bones"]:
                self.logger.info(
                    f"{len(result['semi_standard_bones'])}個の準標準ボーンを追加しました"
                )

        return result

    def validate_bones(self, bones) -> Dict[str, any]:
        """
        ボーン構造の検証を実行する。

        Args:
            bones: 検証対象のボーンデータリスト（PMDまたはPMXのボーンオブジェクト）

        Returns:
            dict: 検証結果を含む辞書
        """
        # ボーン名のリストを抽出
        bone_names = [bone.get_name() for bone in bones]

        # ボーン名の検証を実行
        missing_bones, naming_issues, bone_mapping = self.bone_validator.validate_bones(
            bone_names
        )

        # 階層構造の検証を実行
        hierarchy_issues = self.bone_validator.validate_bone_hierarchy(bones)

        # レポートを生成
        report = self.bone_validator.generate_report(bone_names)

        # 階層構造の問題をレポートに追加
        if any(hierarchy_issues.values()):
            report += "\n\n【階層構造の問題】"
            if hierarchy_issues["invalid_references"]:
                report += "\n無効な親参照:"
                for issue in hierarchy_issues["invalid_references"]:
                    report += f"\n  - {issue['bone']} (index={issue['index']}): parent_index={issue['parent_index']} (max={issue['max_index']})"
            if hierarchy_issues["circular_references"]:
                report += "\n循環参照:"
                for issue in hierarchy_issues["circular_references"]:
                    report += f"\n  - {issue['bone']} (index={issue['index']})"

        # 結果を返す
        return {
            "missing_bones": missing_bones,
            "naming_issues": naming_issues,
            "bone_mapping": bone_mapping,
            "hierarchy_issues": hierarchy_issues,
            "report": report,
            "total_bones": len(bones),
            "standard_bones_found": len(bone_mapping),
        }

    def print_validation_report(self, bones):
        """
        ボーン検証レポートをコンソールに出力する。

        Args:
            bones: 検証対象のボーンデータリスト
        """
        validation_result = self.validate_bones(bones)
        self.logger.info(validation_result["report"])

        # 警告が必要な場合はMayaの警告として表示
        if validation_result["missing_bones"]:
            cmds.warning(
                f"標準ボーンが{len(validation_result['missing_bones'])}個不足しています。詳細はスクリプトエディタを確認してください。"
            )

    def _extract_ik_chains(self, bones, bone_map, ik_data=None):
        """
        PMX/PMDボーンからIKチェーン情報を抽出する。

        Args:
            bones: ボーンデータのリスト
            bone_map (dict): ボーンインデックスからMayaジョイント名へのマッピング
            ik_data: PMDの場合のIKデータリスト（オプション）

        Returns:
            list: IKチェーン情報のリスト
        """
        ik_chains = []

        for i, bone in enumerate(bones):
            # PMXボーンの場合
            if hasattr(bone, "bone_flag") and hasattr(bone, "get_flag"):
                if bone.get_flag(PmxBoneFlag.IK):
                    ik_chain = {
                        "ik_bone": bone_map[i],
                        "ik_bone_index": i,
                        "target_bone": bone_map.get(bone.ik_target_bone_index),
                        "target_bone_index": bone.ik_target_bone_index,
                        "loop_count": bone.ik_loop_count,
                        "unit_angle": bone.ik_limit_angle,  # ラジアン単位
                        "ik_links": [],
                    }

                    # IKリンクの処理
                    if hasattr(bone, "ik_links"):
                        for link in bone.ik_links:
                            link_info = {
                                "bone": bone_map.get(link.ik_bone_index),
                                "bone_index": link.ik_bone_index,
                                "angle_limit": link.angle_limit
                                if hasattr(link, "angle_limit")
                                else False,
                                "limit_min": link.limit_min
                                if hasattr(link, "limit_min")
                                else None,
                                "limit_max": link.limit_max
                                if hasattr(link, "limit_max")
                                else None,
                            }
                            ik_chain["ik_links"].append(link_info)

                    ik_chains.append(ik_chain)

            # PMDボーンの場合（IKボーンはbone_typeで判定）
            elif hasattr(bone, "bone_type"):
                # PMDではIKボーンの判定方法が異なるため、後で実装を追加
                pass

        # PMDの場合、別途IKデータを処理
        if ik_data:
            for ik in ik_data:
                if ik.ik_bone_index < len(bone_map) and ik.target_bone_index < len(
                    bone_map
                ):
                    ik_chain = {
                        "ik_bone": bone_map.get(ik.ik_bone_index),
                        "ik_bone_index": ik.ik_bone_index,
                        "target_bone": bone_map.get(ik.target_bone_index),
                        "target_bone_index": ik.target_bone_index,
                        "loop_count": ik.iterations,
                        "unit_angle": ik.control_weight,  # PMDではcontrol_weightを使用
                        "ik_links": [],
                    }

                    # IKリンクの処理
                    for link_bone_index in ik.link_bones:
                        if link_bone_index < len(bone_map):
                            link_info = {
                                "bone": bone_map.get(link_bone_index),
                                "bone_index": link_bone_index,
                                "angle_limit": False,  # PMDは角度制限情報を持たない
                                "limit_min": None,
                                "limit_max": None,
                            }
                            ik_chain["ik_links"].append(link_info)

                    ik_chains.append(ik_chain)

        return ik_chains

    def _create_maya_ik_handles(self, ik_chains):
        """
        IKチェーン情報からMayaのikHandleを作成する。

        Args:
            ik_chains (list): IKチェーン情報のリスト

        Returns:
            list: 作成されたIKハンドル情報のリスト
        """
        ik_handles = []

        for chain in ik_chains:
            # IKチェーンの最初と最後のジョイントを特定
            if not chain["ik_links"] or not chain["target_bone"]:
                self.logger.warning(
                    f"IKチェーン '{chain['ik_bone']}' にリンクまたはターゲットがありません"
                )
                continue

            # IKリンクの最後（開始ジョイント）から最初（終了ジョイント）の順序
            start_joint = (
                chain["ik_links"][-1]["bone"]
                if chain["ik_links"]
                else chain["target_bone"]
            )
            end_joint = chain["target_bone"]

            if not start_joint or not end_joint:
                self.logger.warning(
                    f"IKチェーン '{chain['ik_bone']}' の開始または終了ジョイントが見つかりません"
                )
                continue

            try:
                # ikHandleを作成
                ik_handle, _ = maya_utils.create_ik_handle(
                    start_joint=start_joint,
                    end_joint=end_joint,
                    solver="ikRPsolver",  # MMDは通常RPソルバーを使用
                    name=f"{chain['ik_bone']}_ikHandle",
                )

                # IKハンドルをIKボーンにペアレント
                maya_utils.parent_objects(ik_handle, chain["ik_bone"])

                # IKハンドルのアトリビュートを設定
                maya_utils.set_attribute(ik_handle, "v", 0, "bool")  # 非表示

                # カスタムアトリビュートでMMDのIK情報を保存
                maya_utils.set_custom_attributes(
                    ik_handle,
                    {
                        "mmd_ik_loop_count": chain["loop_count"],
                        "mmd_ik_unit_angle": chain["unit_angle"],
                        "mmd_ik_bone": chain["ik_bone"],  # IKボーン名を追加
                    },
                )

                # 角度制限の設定
                self._set_joint_limits(chain["ik_links"])

                # 足IKの場合、PoleTargetを作成
                pole_target = None
                if self._is_leg_ik(chain["ik_bone"]):
                    pole_target = self._create_pole_target_for_leg_ik(
                        chain, ik_handle, start_joint, end_joint
                    )

                ik_handle_info = {
                    "ik_handle": ik_handle,
                    "ik_bone": chain["ik_bone"],
                    "start_joint": start_joint,
                    "end_joint": end_joint,
                    "ik_links": chain["ik_links"],
                    "pole_target": pole_target,  # PoleTarget情報を追加
                }

                ik_handles.append(ik_handle_info)
                self.logger.info(
                    f"IKハンドル '{ik_handle}' を作成しました（{start_joint} → {end_joint}）"
                )

            except Exception as e:
                self.logger.error(
                    f"IKハンドルの作成に失敗しました '{chain['ik_bone']}': {e}"
                )

        return ik_handles

    def _is_leg_ik(self, ik_bone_name):
        """
        IKボーンが足IKかどうかを判定する。

        Args:
            ik_bone_name (str): IKボーン名

        Returns:
            bool: 足IKの場合True
        """
        leg_patterns = ["足IK", "leg_ik", "LegIK", "foot_ik", "FootIK"]
        for pattern in leg_patterns:
            if pattern.lower() in ik_bone_name.lower():
                return True
        return False

    def _create_pole_target_for_leg_ik(self, chain, ik_handle, start_joint, end_joint):
        """
        足IK用のPoleTargetロケータを作成する。

        Args:
            chain (dict): IKチェーン情報
            ik_handle (str): IKハンドル名
            start_joint (str): 開始ジョイント（太もも）
            end_joint (str): 終了ジョイント（足首）

        Returns:
            str: 作成されたPoleTarget名、失敗した場合はNone
        """
        try:
            # PoleTargetロケータを作成
            pole_target = cmds.spaceLocator(name=f"{chain['ik_bone']}_poleTarget")[0]

            # IKボーンの親を取得（足IKの親）
            ik_parent = cmds.listRelatives(chain["ik_bone"], parent=True)
            if ik_parent:
                # PoleTargetを足IKの親の子として配置
                maya_utils.parent_objects(pole_target, ik_parent[0])
            else:
                # 親がない場合はワールド直下に配置
                self.logger.warning(
                    f"{chain['ik_bone']}の親が見つかりません。PoleTargetをワールド直下に配置します。"
                )

            # PoleTargetの初期位置を計算
            # 太ももと足首の位置を取得
            start_pos = cmds.xform(
                start_joint, query=True, worldSpace=True, translation=True
            )
            end_pos = cmds.xform(
                end_joint, query=True, worldSpace=True, translation=True
            )

            # 中間のジョイント（膝）を取得
            # IKリンクの最初のジョイントが膝（IKチェーンでは逆順になっている）
            knee_joint = None
            if chain["ik_links"]:
                # IKリンクの最初が膝（MMDのIKチェーンは足首→膝→太ももの順）
                knee_joint = chain["ik_links"][0]["bone"]

            if knee_joint:
                knee_pos = cmds.xform(
                    knee_joint, query=True, worldSpace=True, translation=True
                )

                # 膝の曲がり方向を計算
                # 股関節から膝へのベクトル
                hip_to_knee = [knee_pos[i] - start_pos[i] for i in range(3)]
                hip_to_knee = utils.normalize_vector(hip_to_knee)

                # 股関節から足首への直線ベクトル
                hip_to_ankle = [end_pos[i] - start_pos[i] for i in range(3)]
                hip_to_ankle = utils.normalize_vector(hip_to_ankle)

                # 膝の曲がり方向 = 膝の位置 - 股関節から足首への直線上の最近点
                # 直線上の最近点を計算
                t = sum(
                    [(knee_pos[i] - start_pos[i]) * hip_to_ankle[i] for i in range(3)]
                )
                closest_point = [start_pos[i] + t * hip_to_ankle[i] for i in range(3)]

                # 膝の曲がり方向
                knee_bend_direction = [knee_pos[i] - closest_point[i] for i in range(3)]

                # ベクトルの長さが0に近い場合（直線的な脚）は、デフォルト方向を使用
                length = (
                    knee_bend_direction[0] ** 2
                    + knee_bend_direction[1] ** 2
                    + knee_bend_direction[2] ** 2
                ) ** 0.5
                if length < 0.001:
                    # デフォルトで前方（Z軸負方向）に配置
                    knee_bend_direction = [0, 0, -1]
                else:
                    knee_bend_direction = utils.normalize_vector(knee_bend_direction)

                # PoleTargetの位置を膝の曲がり方向に配置
                offset_distance = 2.0  # デフォルトのオフセット距離
                pole_pos = [
                    knee_pos[0] + knee_bend_direction[0] * offset_distance,
                    knee_pos[1] + knee_bend_direction[1] * offset_distance,
                    knee_pos[2] + knee_bend_direction[2] * offset_distance,
                ]
            else:
                # 膝が見つからない場合は、チェーンの中点の前方に配置
                mid_pos = [(start_pos[i] + end_pos[i]) / 2 for i in range(3)]
                # デフォルトで前方（Z軸方向）に配置
                pole_pos = [mid_pos[0], mid_pos[1], mid_pos[2] + 2.0]

            # PoleTargetの位置を設定
            cmds.xform(pole_target, worldSpace=True, translation=pole_pos)

            # PoleVectorConstraintを作成
            pole_constraint = cmds.poleVectorConstraint(pole_target, ik_handle)[0]

            # PoleTargetを非表示にする
            # maya_utils.set_attribute(pole_target, "v", 0, "bool")

            # カスタムアトリビュートを追加（後でVMD変換時に使用）
            maya_utils.set_custom_attributes(
                pole_target,
                {
                    "mmd_pole_target": True,
                    "mmd_ik_handle": ik_handle,
                    "mmd_ik_bone": chain["ik_bone"],
                },
            )

            self.logger.info(
                f"PoleTarget '{pole_target}' を作成しました（{chain['ik_bone']}用）"
            )
            return pole_target

        except Exception as e:
            self.logger.error(
                f"PoleTargetの作成に失敗しました '{chain['ik_bone']}': {e}"
            )
            return None

    def _set_joint_limits(self, ik_links):
        """
        IKリンクのジョイントに角度制限を設定する。

        Args:
            ik_links (list): IKリンク情報のリスト
        """
        for link in ik_links:
            if not link["bone"]:
                continue

            if link["angle_limit"] and link["limit_min"] and link["limit_max"]:
                joint = link["bone"]

                # X軸の正負を反転（MMDとMayaの座標系の違いに対応）
                limit_min = list(link["limit_min"])
                limit_max = list(link["limit_max"])

                # X軸の値の符号を反転
                limit_min[0] = -limit_min[0]
                limit_max[0] = -limit_max[0]

                # limit_min/maxは既にラジアンで保存されている
                # maya_utils.set_joint_limitsがラジアンから度数への変換を行う
                maya_utils.set_joint_limits(
                    joint=joint,
                    limit_min=limit_min,
                    limit_max=limit_max,
                    enable_limits=True,
                )

    def _apply_bone_local_axes(self, bones, maya_joints):
        """
        全てのボーンにローカル軸を適用する。

        Args:
            bones: ボーンデータのリスト
            maya_joints (list): Mayaジョイント名のリスト
        """
        for i, bone in enumerate(bones):
            if i < len(maya_joints):
                self._set_bone_local_axis(maya_joints[i], bone)

    def _set_bone_local_axis(self, joint, bone):
        """
        PMXボーンのローカル軸情報をMayaジョイントに適用する。
        子ボーンへの影響を防ぐため、子を一時的に切り離して処理する。

        Args:
            joint (str): Mayaジョイント名
            bone: PMXボーンオブジェクト
        """
        if hasattr(bone, "get_flag") and bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
            # PMX仕様書に従った実装
            x_axis_pmx = bone.x_axis_direction
            z_axis_pmx = bone.z_axis_direction

            # PMX座標系でY軸を計算（PMX仕様書準拠）
            # Y = Z × X
            y_axis_pmx = utils.cross_product(z_axis_pmx, x_axis_pmx)
            y_axis_pmx = utils.normalize_vector(y_axis_pmx)

            # Z' = X × Y （Z軸を再計算して直交化）
            z_axis_pmx = utils.cross_product(x_axis_pmx, y_axis_pmx)
            z_axis_pmx = utils.normalize_vector(z_axis_pmx)

            # PMX座標系からMaya座標系に変換
            x_axis_maya = utils.pmx_to_maya_vector(x_axis_pmx)
            y_axis_maya = utils.pmx_to_maya_vector(y_axis_pmx)
            z_axis_maya = utils.pmx_to_maya_vector(z_axis_pmx)

            # ジョイントオリエンテーションの設定
            matrix = maya_utils.create_matrix_from_axes(
                x_axis_maya, y_axis_maya, z_axis_maya
            )
            rotation = maya_utils.matrix_to_euler(matrix)

            # 子ジョイントを取得（直接の子のみ）
            children = (
                cmds.listRelatives(joint, children=True, type=["joint", "transform"])
                or []
            )
            child_transforms = []

            # 子のワールド変換を保存して一時的に切り離す
            for child in children:
                world_pos = cmds.xform(
                    child, query=True, worldSpace=True, translation=True
                )
                world_rot = cmds.xform(
                    child, query=True, worldSpace=True, rotation=True
                )
                child_transforms.append(
                    {"joint": child, "position": world_pos, "rotation": world_rot}
                )
                # ワールドにペアレント（一時的に切り離す）
                maya_utils.parent_objects(child, world=True)

            try:
                # jointOrientを設定
                maya_utils.set_attribute(joint, "jointOrientX", rotation[0], "double")
                maya_utils.set_attribute(joint, "jointOrientY", rotation[1], "double")
                maya_utils.set_attribute(joint, "jointOrientZ", rotation[2], "double")

                # rotateを0にリセット（オプション：必要に応じて）
                maya_utils.set_attribute(joint, "rotateX", 0, "double")
                maya_utils.set_attribute(joint, "rotateY", 0, "double")
                maya_utils.set_attribute(joint, "rotateZ", 0, "double")

                self.logger.debug(f"ローカル軸を設定: {joint}")

            except Exception as e:
                self.logger.error(f"ローカル軸の設定に失敗しました {joint}: {e}")

            finally:
                # 子を再接続して位置を復元
                for transform_data in child_transforms:
                    child = transform_data["joint"]
                    maya_utils.parent_objects(child, joint)
                    cmds.xform(
                        child, worldSpace=True, translation=transform_data["position"]
                    )
                    cmds.xform(
                        child, worldSpace=True, rotation=transform_data["rotation"]
                    )

    def _add_semi_standard_bones(self, maya_joints, bone_map, skeleton_group):
        """
        準標準ボーンを追加する。

        Args:
            maya_joints (list): 作成されたMayaジョイントのリスト
            bone_map (dict): ボーンインデックスからジョイント名へのマッピング
            skeleton_group (str): スケルトングループ名

        Returns:
            dict: 追加された準標準ボーンの辞書
        """
        semi_standard_bones = {}

        # 全ての親
        # 既存の「全ての親」ボーンを日本語名でチェック
        existing_master = self._find_joint_by_japanese_name(["全ての親", "マスター"])
        # 英語名でもチェック
        if not existing_master and maya_utils.object_exists("master"):
            existing_master = "master"

        if not existing_master:
            master = cmds.group(empty=True, name="master", parent=skeleton_group)
            semi_standard_bones["master"] = master
            self.logger.info(f"全ての親ボーンを追加: {master}")
        else:
            master = existing_master
            self.logger.info(f"既存の全ての親ボーンを使用: {existing_master}")

        # スケルトングループ直下のルートジョイントを全ての親の子にする
        if "master" in semi_standard_bones or existing_master:
            # スケルトングループの子を取得（ジョイントとトランスフォームノード両方）
            children = cmds.listRelatives(skeleton_group, children=True) or []
            for child in children:
                # masterノード自体はスキップ
                if child == master:
                    continue
                # ジョイントまたはトランスフォームノードをmasterの子にする
                if cmds.nodeType(child) in ["joint", "transform"]:
                    maya_utils.parent_objects(child, master)

        # グルーブ
        # 既存のグルーブボーンを日本語名でチェック
        existing_groove = self._find_joint_by_japanese_name(["グルーブ"])
        # 英語名でもチェック
        if not existing_groove and maya_utils.object_exists("groove"):
            existing_groove = "groove"

        center_joint = self._find_joint_by_name(
            maya_joints, ["center", "センター", "centre"]
        )

        if not existing_groove and center_joint:
            # センターの位置を取得
            center_pos = cmds.xform(
                center_joint, query=True, worldSpace=True, translation=True
            )

            # グルーブを作成
            groove = cmds.group(
                empty=True,
                name="groove",
                parent=master,
            )
            cmds.xform(groove, worldSpace=True, translation=center_pos)
            semi_standard_bones["groove"] = groove

            # センターをグルーブの子にする
            maya_utils.parent_objects(center_joint, groove)
            self.logger.info(f"グルーブボーンを追加: {groove}")
        elif existing_groove:
            self.logger.info(f"既存のグルーブボーンを使用: {existing_groove}")

        # 腰ボーン（下半身と足の間）
        # 既存の腰ボーンを日本語名でチェック
        existing_waist = self._find_joint_by_japanese_name(["腰"])
        # 英語名でもチェック
        if not existing_waist:
            existing_waist = self._find_joint_by_name(
                maya_joints, ["waist", "腰", "koshi"]
            )

        lower_body_joint = self._find_joint_by_name(
            maya_joints, ["lower_body", "下半身", "lowerbody"]
        )
        left_leg_joint = self._find_joint_by_name(
            maya_joints, ["left_leg", "左足", "leftleg", "left_thigh", "左もも"]
        )

        if not existing_waist and lower_body_joint and left_leg_joint:
            # 下半身と左足の中間位置を計算
            lower_body_pos = cmds.xform(
                lower_body_joint, query=True, worldSpace=True, translation=True
            )
            left_leg_pos = cmds.xform(
                left_leg_joint, query=True, worldSpace=True, translation=True
            )

            waist_pos = [
                (lower_body_pos[0] + left_leg_pos[0]) / 2,
                (lower_body_pos[1] + left_leg_pos[1]) / 2,
                (lower_body_pos[2] + left_leg_pos[2]) / 2,
            ]

            # 腰ボーンを作成
            maya_utils.select_objects(clear=True)
            waist = cmds.joint(name="waist", position=waist_pos)
            semi_standard_bones["waist"] = waist

            # 階層を設定（下半身の子、足の親）
            maya_utils.parent_objects(waist, lower_body_joint)

            # 左右の足を腰の子にする
            right_leg_joint = self._find_joint_by_name(
                maya_joints, ["right_leg", "右足", "rightleg", "right_thigh", "右もも"]
            )

            # 左足を腰の子にする（既に存在確認済み）
            maya_utils.parent_objects(left_leg_joint, waist)
            if right_leg_joint:
                maya_utils.parent_objects(right_leg_joint, waist)

            self.logger.info(f"腰ボーンを追加: {waist}")
        elif existing_waist:
            # 既存の腰ボーンを使用（新規作成しないので辞書には追加しない）
            self.logger.info(f"既存の腰ボーンを使用: {existing_waist}")

        return semi_standard_bones

    def _find_joint_by_name(self, maya_joints, search_names):
        """
        ボーン名のリストから対応するMayaジョイントを検索する。

        Args:
            maya_joints (list): Mayaジョイント名のリスト
            search_names (list): 検索するボーン名のリスト（日本語、英語）

        Returns:
            str: 見つかったジョイント名、見つからない場合はNone
        """
        for joint in maya_joints:
            # ジョイント名を正規化して比較
            joint_lower = joint.lower()
            for search_name in search_names:
                if search_name.lower() in joint_lower:
                    return joint

        return None

    def _find_joint_by_japanese_name(self, japanese_names):
        """
        日本語名で既存のボーンを検索する。
        元のPMX/PMDボーン名を使用して正確な一致を確認する。

        Args:
            japanese_names (list): 検索する日本語ボーン名のリスト

        Returns:
            str: 見つかったMayaジョイント名、見つからない場合はNone
        """
        # self.original_bone_namesから日本語名を検索
        for bone_index, original_name in self.original_bone_names.items():
            for jp_name in japanese_names:
                if original_name == jp_name:
                    # 対応するMayaジョイントを探す
                    all_joints = maya_utils.list_objects(type="joint")
                    for joint in all_joints:
                        # カスタムアトリビュートでボーンインデックスを確認
                        if cmds.attributeQuery(
                            "mmd_bone_index", node=joint, exists=True
                        ):
                            stored_index = cmds.getAttr(f"{joint}.mmd_bone_index")
                            if stored_index == bone_index:
                                return joint

        return None

    def _setup_given_parent_bones(self, bones, maya_joints):
        """
        付与ボーンの設定を行う。
        変形階層（transform_layer）を考慮して適切な順序で処理する。

        Args:
            bones: ボーンデータのリスト
            maya_joints (list): Mayaジョイント名のリスト

        Returns:
            list: 作成されたコンストレイントのリスト
        """
        constraints = []

        # 付与ボーンの情報を収集し、変形階層でソート
        given_bones = []
        for i, bone in enumerate(bones):
            if i >= len(maya_joints):
                continue

            # PMXボーンの場合のみ付与設定をチェック
            if not hasattr(bone, "get_flag"):
                continue

            # 付与フラグをチェック
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(
                PmxBoneFlag.GIVEN_PARENT_MOVE
            ):
                given_bones.append(
                    {
                        "index": i,
                        "bone": bone,
                        "joint": maya_joints[i],
                        "transform_layer": getattr(bone, "transform_layer", 0),
                        "is_physics_after": bone.get_flag(
                            PmxBoneFlag.DEFORM_AFTER_PHYSICS
                        ),
                    }
                )

        # 変形順序でソート（物理前後 → 変形階層 → インデックス）
        given_bones.sort(
            key=lambda x: (x["is_physics_after"], x["transform_layer"], x["index"])
        )

        # 多重付与の依存関係を解決
        given_bones = self._resolve_given_dependencies(given_bones, bones)

        # ソートされた順序で付与を設定
        for given_info in given_bones:
            bone = given_info["bone"]
            joint = given_info["joint"]
            i = given_info["index"]

            # ローカル付与フラグをチェック
            is_local_given = bone.get_flag(PmxBoneFlag.LOCAL)

            # 回転付与
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE):
                parent_index = bone.given_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.given_rate

                    # 付与率が1.0かつローカル付与でない場合は通常のorientConstraint
                    if abs(given_rate - 1.0) < 0.001 and not is_local_given:
                        constraint = cmds.orientConstraint(
                            parent_joint, joint, maintainOffset=True, weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合、またはローカル付与の場合
                        constraint = self._create_given_rotation_constraint(
                            parent_joint, joint, given_rate, is_local_given
                        )

                    constraints.append(constraint)
                    given_type = "ローカル付与" if is_local_given else "グローバル付与"
                    self.logger.info(
                        f"回転付与を設定 ({given_type}): {joint} <- {parent_joint} (rate={given_rate}, layer={given_info['transform_layer']})"
                    )

            # 移動付与
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                parent_index = bone.given_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.given_rate

                    # 付与率が1.0かつローカル付与でない場合は通常のpointConstraint
                    if abs(given_rate - 1.0) < 0.001 and not is_local_given:
                        constraint = cmds.pointConstraint(
                            parent_joint, joint, maintainOffset=True, weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合、またはローカル付与の場合
                        constraint = self._create_given_position_constraint(
                            parent_joint, joint, given_rate, is_local_given
                        )

                    constraints.append(constraint)
                    given_type = "ローカル付与" if is_local_given else "グローバル付与"
                    self.logger.info(
                        f"移動付与を設定 ({given_type}): {joint} <- {parent_joint} (rate={given_rate}, layer={given_info['transform_layer']})"
                    )

        return constraints

    def _resolve_given_dependencies(self, given_bones, all_bones):
        """
        多重付与の依存関係を解決し、適切な順序で処理できるようにソートする。

        Args:
            given_bones (list): 付与ボーン情報のリスト
            all_bones: 全てのボーンデータのリスト

        Returns:
            list: 依存関係を考慮してソートされた付与ボーン情報のリスト
        """
        # 付与ボーンのインデックスセットを作成
        given_indices = {info["index"] for info in given_bones}

        # 依存関係グラフを作成
        dependencies = {}
        for info in given_bones:
            bone = info["bone"]
            dependencies[info["index"]] = []

            # 付与親が他の付与ボーンかチェック
            if hasattr(bone, "given_parent_bone_index"):
                parent_index = bone.given_parent_bone_index
                if parent_index in given_indices:
                    # 多重付与：この付与ボーンは親付与ボーンに依存
                    dependencies[info["index"]].append(parent_index)

        # トポロジカルソートで依存関係を解決
        sorted_indices = self._topological_sort(dependencies)

        # ソート結果に基づいて付与ボーンリストを再構築
        index_to_info = {info["index"]: info for info in given_bones}
        sorted_given_bones = []

        for index in sorted_indices:
            if index in index_to_info:
                sorted_given_bones.append(index_to_info[index])

        # 残りの付与ボーン（依存関係に含まれない）を追加
        for info in given_bones:
            if info["index"] not in sorted_indices:
                sorted_given_bones.append(info)

        return sorted_given_bones

    def _topological_sort(self, dependencies):
        """
        トポロジカルソートを実行して依存関係を解決する。

        Args:
            dependencies (dict): ノード -> 依存先ノードリストの辞書

        Returns:
            list: トポロジカルソートされたノードのリスト
        """
        # 入次数を計算
        in_degree = {node: 0 for node in dependencies}
        for deps in dependencies.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        # 入次数0のノードをキューに追加
        queue = [node for node, degree in in_degree.items() if degree == 0]
        sorted_nodes = []

        while queue:
            node = queue.pop(0)
            sorted_nodes.append(node)

            # このノードに依存するノードの入次数を減らす
            for other, deps in dependencies.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        # 循環依存がある場合は警告
        if len(sorted_nodes) < len(dependencies):
            remaining = set(dependencies.keys()) - set(sorted_nodes)
            self.logger.warning(f"循環依存が検出されました: {remaining}")
            # 循環依存のあるノードも含める（元の順序を保持）
            for node in dependencies:
                if node not in sorted_nodes:
                    sorted_nodes.append(node)

        return sorted_nodes

    def _create_given_rotation_constraint(
        self, parent_joint, child_joint, rate, is_local=False
    ):
        """
        回転付与を作成する。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率
            is_local (bool): ローカル付与かどうか

        Returns:
            str: コンストレイントまたはエクスプレッション名
        """
        if is_local:
            # ローカル付与の場合：親のローカル変形量を参照
            return self._create_local_rotation_constraint(
                parent_joint, child_joint, rate
            )
        else:
            # グローバル付与の場合：親のユーザー変形量を参照
            if abs(rate - 1.0) < 0.001:
                # 付与率が1.0の場合は通常のコンストレイントで十分
                constraint = cmds.orientConstraint(
                    parent_joint, child_joint, maintainOffset=True, weight=1.0
                )[0]
                return constraint
            else:
                # 付与率が1.0でない場合は、重み付きコンストレイントを使用
                return self._create_weighted_rotation_constraint(
                    parent_joint, child_joint, rate
                )

    def _create_weighted_rotation_constraint(self, parent_joint, child_joint, rate):
        """
        重み付き回転コンストレイントを作成する。
        ネイティブノードを使用して実装。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率（負の値も対応）

        Returns:
            list: 作成されたノードのリスト
        """
        created_nodes = []

        # 子の初期回転を保存
        init_locator = cmds.spaceLocator(name=f"{child_joint}_init_rot")[0]
        maya_utils.parent_objects(init_locator, child_joint)
        maya_utils.set_attribute(init_locator, "v", 0, "bool")
        created_nodes.append(init_locator)

        # 親の回転を取得するためのdecomposeMatrixノード
        parent_decompose = cmds.createNode(
            "decomposeMatrix", name=f"{parent_joint}_decompose"
        )
        cmds.connectAttr(f"{parent_joint}.matrix", f"{parent_decompose}.inputMatrix")
        created_nodes.append(parent_decompose)

        # 負の付与率の場合、回転を反転する必要がある
        if rate < 0:
            # 反転用のmultiplyDivideノード（-1を掛ける）
            invert_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_invert_rot"
            )
            cmds.connectAttr(
                f"{parent_decompose}.outputRotate", f"{invert_node}.input1"
            )
            maya_utils.set_attribute(invert_node, "input2X", -1, "double")
            maya_utils.set_attribute(invert_node, "input2Y", -1, "double")
            maya_utils.set_attribute(invert_node, "input2Z", -1, "double")
            created_nodes.append(invert_node)

            # 付与率を適用するmultiplyDivideノード（絶対値を使用）
            mult_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_given_mult"
            )
            cmds.connectAttr(f"{invert_node}.output", f"{mult_node}.input1")
            maya_utils.set_attribute(mult_node, "input2X", abs(rate), "double")
            maya_utils.set_attribute(mult_node, "input2Y", abs(rate), "double")
            maya_utils.set_attribute(mult_node, "input2Z", abs(rate), "double")
            created_nodes.append(mult_node)
        else:
            # 正の付与率の場合、直接適用
            mult_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_given_mult"
            )
            cmds.connectAttr(f"{parent_decompose}.outputRotate", f"{mult_node}.input1")
            maya_utils.set_attribute(mult_node, "input2X", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Y", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Z", rate, "double")
            created_nodes.append(mult_node)

        # 初期回転と付与回転を加算するplusMinusAverageノード
        add_node = cmds.createNode("plusMinusAverage", name=f"{child_joint}_given_add")
        cmds.connectAttr(f"{init_locator}.rotate", f"{add_node}.input3D[0]")
        cmds.connectAttr(f"{mult_node}.output", f"{add_node}.input3D[1]")
        created_nodes.append(add_node)

        # 結果を子ジョイントに接続
        cmds.connectAttr(f"{add_node}.output3D", f"{child_joint}.rotate", force=True)

        return created_nodes

    def _create_local_rotation_constraint(self, parent_joint, child_joint, rate):
        """
        ローカル回転付与を作成する（親のローカル変形量を参照）。
        ネイティブノードを使用して実装。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率

        Returns:
            list: 作成されたノードのリスト
        """
        created_nodes = []

        # 親の初期回転を保存するロケータを作成
        parent_init_locator = cmds.spaceLocator(name=f"{parent_joint}_init_local_rot")[
            0
        ]
        maya_utils.parent_objects(parent_init_locator, parent_joint)
        maya_utils.set_attribute(parent_init_locator, "v", 0, "bool")  # 非表示
        created_nodes.append(parent_init_locator)

        # 子の初期回転を保存するロケータを作成
        child_init_locator = cmds.spaceLocator(name=f"{child_joint}_init_local_rot")[0]
        maya_utils.parent_objects(child_init_locator, child_joint)
        maya_utils.set_attribute(child_init_locator, "v", 0, "bool")  # 非表示
        created_nodes.append(child_init_locator)

        # 親の現在の回転から初期回転を引くためのplusMinusAverageノード
        parent_diff_node = cmds.createNode(
            "plusMinusAverage", name=f"{parent_joint}_local_diff"
        )
        maya_utils.set_attribute(parent_diff_node, "operation", 2, "long")  # subtract
        cmds.connectAttr(f"{parent_joint}.rotate", f"{parent_diff_node}.input3D[0]")
        cmds.connectAttr(
            f"{parent_init_locator}.rotate", f"{parent_diff_node}.input3D[1]"
        )
        created_nodes.append(parent_diff_node)

        # 付与率を適用するmultiplyDivideノード
        mult_node = cmds.createNode("multiplyDivide", name=f"{child_joint}_local_mult")
        cmds.connectAttr(f"{parent_diff_node}.output3D", f"{mult_node}.input1")

        # 負の付与率の場合の処理
        if rate < 0:
            # 反転用のmultiplyDivideノード
            invert_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_local_invert"
            )
            maya_utils.set_attribute(invert_node, "input2X", -abs(rate), "double")
            maya_utils.set_attribute(invert_node, "input2Y", -abs(rate), "double")
            maya_utils.set_attribute(invert_node, "input2Z", -abs(rate), "double")
            cmds.connectAttr(f"{parent_diff_node}.output3D", f"{invert_node}.input1")
            created_nodes.append(invert_node)

            # 反転した値を使用
            cmds.connectAttr(f"{invert_node}.output", f"{mult_node}.input1", force=True)
            maya_utils.set_attribute(mult_node, "input2X", 1, "double")
            maya_utils.set_attribute(mult_node, "input2Y", 1, "double")
            maya_utils.set_attribute(mult_node, "input2Z", 1, "double")
        else:
            maya_utils.set_attribute(mult_node, "input2X", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Y", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Z", rate, "double")

        created_nodes.append(mult_node)

        # 子の初期回転と加算するplusMinusAverageノード
        add_node = cmds.createNode("plusMinusAverage", name=f"{child_joint}_local_add")
        cmds.connectAttr(f"{child_init_locator}.rotate", f"{add_node}.input3D[0]")
        cmds.connectAttr(f"{mult_node}.output", f"{add_node}.input3D[1]")
        created_nodes.append(add_node)

        # 結果を子ジョイントに接続
        cmds.connectAttr(f"{add_node}.output3D", f"{child_joint}.rotate", force=True)

        return created_nodes

    def _create_given_position_constraint(
        self, parent_joint, child_joint, rate, is_local=False
    ):
        """
        位置付与を作成する。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率
            is_local (bool): ローカル付与かどうか

        Returns:
            str: コンストレイントまたはエクスプレッション名
        """
        if is_local:
            # ローカル付与の場合：親のローカル変形量を参照
            return self._create_local_position_constraint(
                parent_joint, child_joint, rate
            )
        else:
            # グローバル付与の場合：親のユーザー変形量を参照
            if abs(rate - 1.0) < 0.001:
                # 付与率が1.0の場合は通常のコンストレイントで十分
                constraint = cmds.pointConstraint(
                    parent_joint, child_joint, maintainOffset=True, weight=1.0
                )[0]
                return constraint
            else:
                # 付与率が1.0でない場合は、重み付きコンストレイントを使用
                return self._create_weighted_position_constraint(
                    parent_joint, child_joint, rate
                )

    def _create_weighted_position_constraint(self, parent_joint, child_joint, rate):
        """
        重み付き位置コンストレイントを作成する。
        ネイティブノードを使用して実装。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率（負の値も対応）

        Returns:
            list: 作成されたノードのリスト
        """
        created_nodes = []

        # 子の初期位置を保存
        init_locator = cmds.spaceLocator(name=f"{child_joint}_init_pos")[0]
        maya_utils.parent_objects(init_locator, child_joint)
        maya_utils.set_attribute(init_locator, "v", 0, "bool")
        created_nodes.append(init_locator)

        # 親の位置を取得するためのdecomposeMatrixノード
        parent_decompose = cmds.createNode(
            "decomposeMatrix", name=f"{parent_joint}_pos_decompose"
        )
        cmds.connectAttr(f"{parent_joint}.matrix", f"{parent_decompose}.inputMatrix")
        created_nodes.append(parent_decompose)

        # 負の付与率の場合、位置を反転する必要がある
        if rate < 0:
            # 反転用のmultiplyDivideノード（-1を掛ける）
            invert_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_invert_pos"
            )
            cmds.connectAttr(
                f"{parent_decompose}.outputTranslate", f"{invert_node}.input1"
            )
            maya_utils.set_attribute(invert_node, "input2X", -1, "double")
            maya_utils.set_attribute(invert_node, "input2Y", -1, "double")
            maya_utils.set_attribute(invert_node, "input2Z", -1, "double")
            created_nodes.append(invert_node)

            # 付与率を適用するmultiplyDivideノード（絶対値を使用）
            mult_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_pos_mult"
            )
            cmds.connectAttr(f"{invert_node}.output", f"{mult_node}.input1")
            maya_utils.set_attribute(mult_node, "input2X", abs(rate), "double")
            maya_utils.set_attribute(mult_node, "input2Y", abs(rate), "double")
            maya_utils.set_attribute(mult_node, "input2Z", abs(rate), "double")
            created_nodes.append(mult_node)
        else:
            # 正の付与率の場合、直接適用
            mult_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_pos_mult"
            )
            cmds.connectAttr(
                f"{parent_decompose}.outputTranslate", f"{mult_node}.input1"
            )
            maya_utils.set_attribute(mult_node, "input2X", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Y", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Z", rate, "double")
            created_nodes.append(mult_node)

        # 初期位置と付与位置を加算するplusMinusAverageノード
        add_node = cmds.createNode("plusMinusAverage", name=f"{child_joint}_pos_add")
        cmds.connectAttr(f"{init_locator}.translate", f"{add_node}.input3D[0]")
        cmds.connectAttr(f"{mult_node}.output", f"{add_node}.input3D[1]")
        created_nodes.append(add_node)

        # 結果を子ジョイントに接続
        cmds.connectAttr(f"{add_node}.output3D", f"{child_joint}.translate", force=True)

        return created_nodes

    def _create_local_position_constraint(self, parent_joint, child_joint, rate):
        """
        ローカル位置付与を作成する（親のローカル変形量を参照）。
        ネイティブノードを使用して実装。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率

        Returns:
            list: 作成されたノードのリスト
        """
        created_nodes = []

        # 親の初期位置を保存するロケータを作成
        parent_init_locator = cmds.spaceLocator(name=f"{parent_joint}_init_local_pos")[
            0
        ]
        maya_utils.parent_objects(parent_init_locator, parent_joint)
        maya_utils.set_attribute(parent_init_locator, "v", 0, "bool")  # 非表示
        created_nodes.append(parent_init_locator)

        # 子の初期位置を保存するロケータを作成
        child_init_locator = cmds.spaceLocator(name=f"{child_joint}_init_local_pos")[0]
        maya_utils.parent_objects(child_init_locator, child_joint)
        maya_utils.set_attribute(child_init_locator, "v", 0, "bool")  # 非表示
        created_nodes.append(child_init_locator)

        # 親の現在の位置から初期位置を引くためのplusMinusAverageノード
        parent_diff_node = cmds.createNode(
            "plusMinusAverage", name=f"{parent_joint}_local_pos_diff"
        )
        maya_utils.set_attribute(parent_diff_node, "operation", 2, "long")  # subtract
        cmds.connectAttr(f"{parent_joint}.translate", f"{parent_diff_node}.input3D[0]")
        cmds.connectAttr(
            f"{parent_init_locator}.translate", f"{parent_diff_node}.input3D[1]"
        )
        created_nodes.append(parent_diff_node)

        # 付与率を適用するmultiplyDivideノード
        mult_node = cmds.createNode(
            "multiplyDivide", name=f"{child_joint}_local_pos_mult"
        )
        cmds.connectAttr(f"{parent_diff_node}.output3D", f"{mult_node}.input1")

        # 負の付与率の場合の処理
        if rate < 0:
            # 反転用のmultiplyDivideノード
            invert_node = cmds.createNode(
                "multiplyDivide", name=f"{child_joint}_local_pos_invert"
            )
            maya_utils.set_attribute(invert_node, "input2X", -abs(rate), "double")
            maya_utils.set_attribute(invert_node, "input2Y", -abs(rate), "double")
            maya_utils.set_attribute(invert_node, "input2Z", -abs(rate), "double")
            cmds.connectAttr(f"{parent_diff_node}.output3D", f"{invert_node}.input1")
            created_nodes.append(invert_node)

            # 反転した値を使用
            cmds.connectAttr(f"{invert_node}.output", f"{mult_node}.input1", force=True)
            maya_utils.set_attribute(mult_node, "input2X", 1, "double")
            maya_utils.set_attribute(mult_node, "input2Y", 1, "double")
            maya_utils.set_attribute(mult_node, "input2Z", 1, "double")
        else:
            maya_utils.set_attribute(mult_node, "input2X", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Y", rate, "double")
            maya_utils.set_attribute(mult_node, "input2Z", rate, "double")

        created_nodes.append(mult_node)

        # 子の初期位置と加算するplusMinusAverageノード
        add_node = cmds.createNode(
            "plusMinusAverage", name=f"{child_joint}_local_pos_add"
        )
        cmds.connectAttr(f"{child_init_locator}.translate", f"{add_node}.input3D[0]")
        cmds.connectAttr(f"{mult_node}.output", f"{add_node}.input3D[1]")
        created_nodes.append(add_node)

        # 結果を子ジョイントに接続
        cmds.connectAttr(f"{add_node}.output3D", f"{child_joint}.translate", force=True)

        return created_nodes
