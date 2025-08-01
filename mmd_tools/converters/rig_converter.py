from tkinter import W
from typing import List, Dict, Optional

import maya.cmds as cmds

from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core import maya_utils
from mmd_tools.core import utils
from mmd_tools.core.logger import get_logger
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

        self.logger.info("PMXリグセットアップを開始")
        result = {"ik_handles": [], "semi_standard_bones": {}, "constraints": []}

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
        result["constraints"] = self._setup_grant_bones(pmx_data.bones, maya_joints)
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
        result = {"ik_handles": [], "semi_standard_bones": {}, "constraints": []}

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
        MMDモデルの特性を考慮し、以下の優先順位で膝の屈曲方向を決定：
        1. 膝ジョイントのローカル軸情報（PMXのLOCAL_AXIS）
        2. 膝ジョイントの初期姿勢（jointOrient）
        3. 実際の膝の位置から計算した屈曲方向
        4. デフォルトの前方向（-Z）

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

            # PoleTargetを太もも（start_joint）の子として配置
            maya_utils.parent_objects(pole_target, start_joint)
            self.logger.debug(f"PoleTargetを太もも '{start_joint}' の子として配置")

            # PoleTargetの初期位置を計算
            # 太ももと足首の位置を取得
            start_pos = cmds.xform(
                start_joint, query=True, worldSpace=True, translation=True
            )
            end_pos = cmds.xform(
                end_joint, query=True, worldSpace=True, translation=True
            )

            # 中間のジョイント（膝）を取得
            knee_joint = None
            if chain["ik_links"]:
                # IKリンクの最初が膝（MMDのIKチェーンは足首→膝→太ももの順）
                knee_joint = chain["ik_links"][0]["bone"]

            # 膝の屈曲方向を決定
            # 常にZ+方向を使用
            knee_bend_direction = [0, 0, 1]  # Z+方向
            method_used = "fixed_z_positive"

            if knee_joint:
                knee_pos = cmds.xform(
                    knee_joint, query=True, worldSpace=True, translation=True
                )
                self.logger.debug("Pole vectorの向きをZ+方向に固定")

                # 脚の長さに基づいてオフセット距離を計算
                leg_length = (
                    (end_pos[0] - start_pos[0]) ** 2
                    + (end_pos[1] - start_pos[1]) ** 2
                    + (end_pos[2] - start_pos[2]) ** 2
                ) ** 0.5
                offset_distance = max(
                    leg_length * 0.3, 2.0
                )  # 脚の長さの30%または最小2ユニット

                # PoleTargetの位置を計算
                pole_pos = [
                    knee_pos[0] + knee_bend_direction[0] * offset_distance,
                    knee_pos[1] + knee_bend_direction[1] * offset_distance,
                    knee_pos[2] + knee_bend_direction[2] * offset_distance,
                ]
            else:
                # 膝が見つからない場合は、チェーンの中点のZ+方向に配置
                # MMD-37: IKのPole Vectorの向きを常にZ+方向に固定
                mid_pos = [(start_pos[i] + end_pos[i]) / 2 for i in range(3)]
                pole_pos = [mid_pos[0], mid_pos[1], mid_pos[2] + 2.0]  # Z+方向に配置
                method_used = "default_midpoint"

            # PoleTargetの位置を設定
            cmds.xform(pole_target, worldSpace=True, translation=pole_pos)

            # PoleVectorConstraintを作成
            pole_constraint = cmds.poleVectorConstraint(pole_target, ik_handle)[0]

            # PoleTargetのコントロール性を向上
            # ロケータのサイズを調整
            maya_utils.set_attribute(pole_target, "localScaleX", 0.5, "double")
            maya_utils.set_attribute(pole_target, "localScaleY", 0.5, "double")
            maya_utils.set_attribute(pole_target, "localScaleZ", 0.5, "double")

            # カスタムアトリビュートを追加
            maya_utils.set_custom_attributes(
                pole_target,
                {
                    "mmd_pole_target": True,
                    "mmd_ik_handle": ik_handle,
                    "mmd_ik_bone": chain["ik_bone"],
                    "mmd_pole_method": method_used,
                },
            )

            self.logger.info(
                f"PoleTarget '{pole_target}' を作成しました（{chain['ik_bone']}用、方法: {method_used}）"
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

    def _setup_grant_bones(self, bones, maya_joints):
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
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE) or bone.get_flag(
                PmxBoneFlag.GRANT_PARENT_MOVE
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
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE):
                parent_index = bone.grant_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.grant_rate

                    constraint = cmds.orientConstraint(
                        parent_joint, joint, maintainOffset=True, weight=given_rate
                    )[0]

                    constraints.append(constraint)
                    given_type = "ローカル付与" if is_local_given else "グローバル付与"
                    self.logger.info(
                        f"回転付与を設定 ({given_type}): {joint} <- {parent_joint} (rate={given_rate}, layer={given_info['transform_layer']})"
                    )

            # 移動付与
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_MOVE):
                parent_index = bone.grant_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.grant_rate

                    constraint = cmds.pointConstraint(
                        parent_joint, joint, maintainOffset=True, weight=given_rate
                    )[0]

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
                parent_index = bone.grant_parent_bone_index
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
