from typing import List, Dict, Optional

import maya.cmds as cmds

from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core import maya_utils
from mmd_tools.core.logger import get_logger
from mmd_tools.validation.bone_validator import BoneValidator
from mmd_tools.settings import settings


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
        # result["constraints"] = self._setup_given_parent_bones(
        #     pmx_data.bones, maya_joints
        # )
        # if result["constraints"]:
        #     self.logger.info(f"{len(result['constraints'])}個の付与関係を設定しました")

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
                cmds.parent(ik_handle, chain["ik_bone"])

                # IKハンドルのアトリビュートを設定
                cmds.setAttr(f"{ik_handle}.v", 0)  # 非表示

                # カスタムアトリビュートでMMDのIK情報を保存
                maya_utils.set_custom_attributes(
                    ik_handle,
                    {
                        "mmd_ik_loop_count": chain["loop_count"],
                        "mmd_ik_unit_angle": chain["unit_angle"],
                    },
                )

                # 角度制限の設定
                self._set_joint_limits(chain["ik_links"])

                ik_handle_info = {
                    "ik_handle": ik_handle,
                    "ik_bone": chain["ik_bone"],
                    "start_joint": start_joint,
                    "end_joint": end_joint,
                    "ik_links": chain["ik_links"],
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

                # MMDの角度制限は度数法、Mayaはラジアン
                # limit_min/maxは既にラジアンで保存されている
                maya_utils.set_joint_limits(
                    joint=joint,
                    limit_min=link["limit_min"],
                    limit_max=link["limit_max"],
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
            # PMX座標系からMaya座標系に変換
            x_axis_pmx = bone.x_axis_direction
            z_axis_pmx = bone.z_axis_direction

            # 座標系変換（Z軸反転）
            x_axis_maya = maya_utils.pmx_to_maya_vector(x_axis_pmx)
            z_axis_maya = maya_utils.pmx_to_maya_vector(z_axis_pmx)

            # ベクトルを正規化
            x_axis_maya = maya_utils.normalize_vector(x_axis_maya)
            z_axis_maya = maya_utils.normalize_vector(z_axis_maya)

            # グラムシュミットの正規直交化
            # Y = Z × X (外積の順序に注意)
            y_axis_maya = maya_utils.cross_product(z_axis_maya, x_axis_maya)
            y_axis_maya = maya_utils.normalize_vector(y_axis_maya)

            # Z軸を再計算して完全に直交化
            z_axis_maya = maya_utils.cross_product(x_axis_maya, y_axis_maya)
            z_axis_maya = maya_utils.normalize_vector(z_axis_maya)

            # ジョイントオリエンテーションの設定
            matrix = maya_utils.create_matrix_from_axes(
                x_axis_maya, y_axis_maya, z_axis_maya
            )
            rotation = maya_utils.matrix_to_euler(matrix)

            self.logger.debug(f"ローカル軸設定 {joint}:")
            self.logger.debug(f"  PMX X軸: {x_axis_pmx} → Maya: {x_axis_maya}")
            self.logger.debug(f"  PMX Z軸: {z_axis_pmx} → Maya: {z_axis_maya}")
            self.logger.debug(f"  計算Y軸: {y_axis_maya}")
            self.logger.debug(f"  回転: {rotation}")

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
                cmds.parent(child, world=True)

            try:
                # jointOrientを設定
                cmds.setAttr(f"{joint}.jointOrientX", rotation[0])
                cmds.setAttr(f"{joint}.jointOrientY", rotation[1])
                cmds.setAttr(f"{joint}.jointOrientZ", rotation[2])

                # rotateを0にリセット（オプション：必要に応じて）
                cmds.setAttr(f"{joint}.rotateX", 0)
                cmds.setAttr(f"{joint}.rotateY", 0)
                cmds.setAttr(f"{joint}.rotateZ", 0)

                self.logger.debug(f"ローカル軸を設定: {joint}")

            except Exception as e:
                self.logger.error(f"ローカル軸の設定に失敗しました {joint}: {e}")

            finally:
                # 子を再接続して位置を復元
                for transform_data in child_transforms:
                    child = transform_data["joint"]
                    cmds.parent(child, joint)
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
        if not existing_master and cmds.objExists("master"):
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
                    cmds.parent(child, master)

        # グルーブ
        # 既存のグルーブボーンを日本語名でチェック
        existing_groove = self._find_joint_by_japanese_name(["グルーブ"])
        # 英語名でもチェック
        if not existing_groove and cmds.objExists("groove"):
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
            cmds.parent(center_joint, groove)
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
            cmds.select(clear=True)
            waist = cmds.joint(name="waist", position=waist_pos)
            semi_standard_bones["waist"] = waist

            # 階層を設定（下半身の子、足の親）
            cmds.parent(waist, lower_body_joint)

            # 左右の足を腰の子にする
            right_leg_joint = self._find_joint_by_name(
                maya_joints, ["right_leg", "右足", "rightleg", "right_thigh", "右もも"]
            )

            # 左足を腰の子にする（既に存在確認済み）
            cmds.parent(left_leg_joint, waist)
            if right_leg_joint:
                cmds.parent(right_leg_joint, waist)

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
                    all_joints = cmds.ls(type="joint")
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

        Args:
            bones: ボーンデータのリスト
            maya_joints (list): Mayaジョイント名のリスト

        Returns:
            list: 作成されたコンストレイントのリスト
        """
        constraints = []

        for i, bone in enumerate(bones):
            if i >= len(maya_joints):
                continue

            joint = maya_joints[i]

            # PMXボーンの場合のみ付与設定をチェック
            if not hasattr(bone, "get_flag"):
                continue

            # 回転付与
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE):
                parent_index = bone.given_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.given_rate

                    # 付与率が1.0の場合は通常のorientConstraint
                    if abs(given_rate - 1.0) < 0.001:
                        constraint = cmds.orientConstraint(
                            parent_joint, joint, maintainOffset=True, weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合は、エクスプレッションで制御
                        constraint = self._create_partial_rotation_constraint(
                            parent_joint, joint, given_rate
                        )

                    constraints.append(constraint)
                    self.logger.info(
                        f"回転付与を設定: {joint} <- {parent_joint} (rate={given_rate})"
                    )

            # 移動付与
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                parent_index = bone.given_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.given_rate

                    # 付与率が1.0の場合は通常のpointConstraint
                    if abs(given_rate - 1.0) < 0.001:
                        constraint = cmds.pointConstraint(
                            parent_joint, joint, maintainOffset=True, weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合は、エクスプレッションで制御
                        constraint = self._create_partial_position_constraint(
                            parent_joint, joint, given_rate
                        )

                    constraints.append(constraint)
                    self.logger.info(
                        f"移動付与を設定: {joint} <- {parent_joint} (rate={given_rate})"
                    )

        return constraints

    def _create_partial_rotation_constraint(self, parent_joint, child_joint, rate):
        """
        部分的な回転付与を作成する（エクスプレッション使用）。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率

        Returns:
            str: エクスプレッション名
        """
        # ベース回転を保存するためのロケータを作成
        base_locator = cmds.spaceLocator(name=f"{child_joint}_base_rotation")[0]
        cmds.parent(base_locator, child_joint)
        cmds.setAttr(f"{base_locator}.v", 0)  # 非表示

        # エクスプレッションを作成
        expr_name = f"{child_joint}_given_rotation_expr"
        expression = f"""
// 親の回転を取得
float $parentRotX = `getAttr {parent_joint}.rotateX`;
float $parentRotY = `getAttr {parent_joint}.rotateY`;
float $parentRotZ = `getAttr {parent_joint}.rotateZ`;

// ベース回転を取得
float $baseRotX = `getAttr {base_locator}.rotateX`;
float $baseRotY = `getAttr {base_locator}.rotateY`;
float $baseRotZ = `getAttr {base_locator}.rotateZ`;

// 付与率を適用
{child_joint}.rotateX = $baseRotX + ($parentRotX * {rate});
{child_joint}.rotateY = $baseRotY + ($parentRotY * {rate});
{child_joint}.rotateZ = $baseRotZ + ($parentRotZ * {rate});
"""

        cmds.expression(name=expr_name, string=expression)

        return expr_name

    def _create_partial_position_constraint(self, parent_joint, child_joint, rate):
        """
        部分的な位置付与を作成する（エクスプレッション使用）。

        Args:
            parent_joint (str): 親ジョイント名
            child_joint (str): 子ジョイント名
            rate (float): 付与率

        Returns:
            str: エクスプレッション名
        """
        # ベース位置を保存するためのロケータを作成
        base_locator = cmds.spaceLocator(name=f"{child_joint}_base_position")[0]
        cmds.parent(base_locator, child_joint)
        cmds.setAttr(f"{base_locator}.v", 0)  # 非表示

        # エクスプレッションを作成
        expr_name = f"{child_joint}_given_position_expr"
        expression = f"""
// 親の移動を取得
float $parentTX = `getAttr {parent_joint}.translateX`;
float $parentTY = `getAttr {parent_joint}.translateY`;
float $parentTZ = `getAttr {parent_joint}.translateZ`;

// ベース位置を取得
float $baseTX = `getAttr {base_locator}.translateX`;
float $baseTY = `getAttr {base_locator}.translateY`;
float $baseTZ = `getAttr {base_locator}.translateZ`;

// 付与率を適用
{child_joint}.translateX = $baseTX + ($parentTX * {rate});
{child_joint}.translateY = $baseTY + ($parentTY * {rate});
{child_joint}.translateZ = $baseTZ + ($parentTZ * {rate});
"""

        cmds.expression(name=expr_name, string=expression)

        return expr_name
