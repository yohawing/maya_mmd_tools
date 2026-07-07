import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import maya.cmds as cmds

from mmd_tools.config.bone_aliases import get_bone_aliases, get_original_bone_name_aliases
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core import maya_rig_utils, maya_scene_utils, maya_utils
from types import SimpleNamespace

from mmd_tools.core.logger import get_logger, safe_log_error
from mmd_tools.core.native.mmd_anim_runtime import is_rig_primitive_available

MMD_APPEND_SCHEMA_MODE_COMPAT = 2


def _node_leaf_name(node_name: str) -> str:
    """Return the DAG leaf name, preserving namespace but dropping parent paths."""
    return str(node_name).rsplit("|", 1)[-1] if node_name else ""


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

    def _append_node_type(self) -> str:
        """Return the unified mmdAppend node type name (C++ / Python share the same typeName)."""
        return "mmdAppend"

    def _ccd_ik_node_type(self) -> str:
        """Return the unified mmdCcdIk node type name (C++ / Python share the same typeName)."""
        return "mmdCcdIk"

    def setup_pmx_rig(
        self,
        pmx_data,
        maya_joints: List[str],
        bone_map: Dict[int, str],
        skeleton_group: str,
        pmx_filepath: Optional[str] = None,
    ) -> Dict:
        """
        PMXボーンデータを元にMayaのリグシステムをセットアップする。

        native rig primitive DLL が利用可能で pmx_filepath が渡された場合は
        mmd-anim の C ソルバーを使用し、それ以外は Python constraint にフォールバック。

        Args:
            pmx_data: PMXパーサーデータ
            maya_joints: 作成されたMayaジョイントのリスト
            bone_map: ボーンインデックスからジョイント名へのマッピング
            skeleton_group: スケルトングループ名
            pmx_filepath: PMX ファイルのパス (native rig builder 用)

        Returns:
            dict: セットアップ結果の情報
        """

        self.logger.info("Starting PMX rig setup")
        result = {
            "ik_handles": [],
            "semi_standard_bones": {},
            "constraints": [],
            "native_rig": None,
            "warnings": [],
        }

        # 元のボーン名を保存（日本語名での重複チェック用）
        for i, bone in enumerate(pmx_data.bones):
            self.original_bone_names[i] = bone.get_name()

        # native rig primitive が利用可能なら使用
        native_rig = None
        if pmx_filepath:
            if is_rig_primitive_available():
                native_rig, warning = self._try_setup_native_rig(pmx_filepath, maya_joints, bone_map)
                if warning:
                    result["warnings"].append(warning)
            else:
                result["warnings"].append(
                    self._native_rig_warning(
                        "native_rig_unavailable",
                        "Native rig primitives are unavailable; falling back to Python constraints",
                        fallback="python_constraints",
                    )
                )

        if native_rig is not None:
            result["native_rig"] = native_rig
            self.logger.info(
                f"Built with native rig primitives: "
                f"IK {len(native_rig.ik_chains)} chains, "
                f"append {len(native_rig.append_solvers)} solvers"
            )
        else:
            # Python constraint フォールバック
            result["constraints"] = self._setup_grant_bones(pmx_data.bones, maya_joints)
            if result["constraints"]:
                self.logger.info(f"Configured {len(result['constraints'])} append relationships (Python constraint)")

        return result

    def _try_setup_native_rig(
        self,
        pmx_filepath: str,
        maya_joints: List[str],
        bone_map: Dict[int, str],
    ):
        """native rig builder で IK/付与を構築し、mmdAppend/mmdCcdIk DG ノードを作成する。"""
        try:
            from mmd_tools.converters.native_rig_builder import NativeRigPrimitives

            pmx_path = Path(pmx_filepath)
            if not pmx_path.exists():
                self.logger.warning(f"PMX file not found for native rig: {pmx_filepath}")
                return None, self._native_rig_warning(
                    "native_rig_pmx_missing",
                    "PMX file not found for native rig; falling back to Python constraints",
                    fallback="python_constraints",
                    pmx_path=pmx_filepath,
                )

            pmx_bytes = pmx_path.read_bytes()
            prims = NativeRigPrimitives.from_pmx_bytes(pmx_bytes)
            if prims is None:
                self.logger.warning("NativeRigPrimitives.from_pmx_bytes returned None, falling back to Python")
                return None, self._native_rig_warning(
                    "native_rig_build_unavailable",
                    "Native rig primitive build returned no result; falling back to Python constraints",
                    fallback="python_constraints",
                    pmx_path=pmx_filepath,
                )

            self._store_native_rig_metadata(prims, maya_joints, bone_map)

            append_nodes = self._create_append_nodes_from_manifest(prims.manifest, maya_joints)
            ik_nodes = self._create_ik_nodes_from_manifest(prims.manifest, maya_joints)

            self.logger.info(
                f"native rig DG nodes: {len(append_nodes)} mmdAppend, "
                f"{len(ik_nodes)} mmdCcdIk"
            )

            return prims, None

        except Exception as e:
            self.logger.warning(f"native rig setup failed, falling back to Python: {e}")
            return None, self._native_rig_warning(
                "native_rig_setup_failed",
                "Native rig setup failed; falling back to Python constraints",
                fallback="python_constraints",
                exception_type=type(e).__name__,
                error=str(e),
                pmx_path=pmx_filepath,
            )

    @staticmethod
    def _native_rig_warning(code: str, message: str, **extra) -> Dict[str, object]:
        warning = {
            "source": "rig_converter",
            "code": code,
            "message": message,
            "severity": "warning",
        }
        warning.update(extra)
        return warning

    def _store_native_rig_metadata(self, prims, maya_joints, bone_map):
        """native rig の情報を Maya ジョイントのカスタムアトリビュートに保存する。"""
        for chain, mapping in prims.ik_chains:
            ctrl_idx = mapping["controller_pmx_index"]
            target_idx = mapping["target_pmx_index"]
            ctrl_joint = bone_map.get(ctrl_idx)
            target_joint = bone_map.get(target_idx)

            if ctrl_joint and cmds.objExists(ctrl_joint):
                if not cmds.attributeQuery("mmd_ik_native", node=ctrl_joint, exists=True):
                    cmds.addAttr(ctrl_joint, longName="mmd_ik_native", attributeType="bool")
                cmds.setAttr(f"{ctrl_joint}.mmd_ik_native", True)

            if target_joint and cmds.objExists(target_joint):
                if not cmds.attributeQuery("mmd_ik_target_native", node=target_joint, exists=True):
                    cmds.addAttr(target_joint, longName="mmd_ik_target_native", attributeType="bool")
                cmds.setAttr(f"{target_joint}.mmd_ik_target_native", True)

            self.logger.debug(
                f"IK chain metadata: controller={ctrl_joint}(#{ctrl_idx}), "
                f"target={target_joint}(#{target_idx}), "
                f"links={len(mapping.get('link_slots', []))}"
            )

        for solver, info in prims.append_solvers:
            target_idx = info["target_pmx_index"]
            target_joint = bone_map.get(target_idx)
            if target_joint and cmds.objExists(target_joint):
                if not cmds.attributeQuery("mmd_append_native", node=target_joint, exists=True):
                    cmds.addAttr(target_joint, longName="mmd_append_native", attributeType="bool")
                cmds.setAttr(f"{target_joint}.mmd_append_native", True)

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
                                "angle_limit": link.angle_limit if hasattr(link, "angle_limit") else False,
                                "limit_min": link.limit_min if hasattr(link, "limit_min") else None,
                                "limit_max": link.limit_max if hasattr(link, "limit_max") else None,
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
                if ik.ik_bone_index < len(bone_map) and ik.target_bone_index < len(bone_map):
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
                self.logger.warning(f"IK chain '{chain['ik_bone']}' has no links or target")
                continue

            # IKリンクの最後（開始ジョイント）から最初（終了ジョイント）の順序
            start_joint = chain["ik_links"][-1]["bone"] if chain["ik_links"] else chain["target_bone"]
            end_joint = chain["target_bone"]

            if not start_joint or not end_joint:
                self.logger.warning(f"IK chain '{chain['ik_bone']}' start or end joint not found")
                continue

            try:
                # ikHandleを作成
                ik_handle, _ = maya_rig_utils.create_ik_handle(
                    start_joint=start_joint,
                    end_joint=end_joint,
                    solver="ikRPsolver",  # MMDは通常RPソルバーを使用
                    name=f"{chain['ik_bone']}_ikHandle",
                )

                # IKハンドルをIKボーンにペアレント
                maya_scene_utils.parent_objects(ik_handle, chain["ik_bone"])

                # IKハンドルのアトリビュートを設定
                maya_utils.set_attribute(ik_handle, "v", 0, "bool")  # 非表示

                # 足IKの場合、PoleTargetを作成
                pole_target = None

                ik_handle_info = {
                    "ik_handle": ik_handle,
                    "ik_bone": chain["ik_bone"],
                    "start_joint": start_joint,
                    "end_joint": end_joint,
                    "ik_links": chain["ik_links"],
                    "pole_target": pole_target,  # PoleTarget情報を追加
                }

                ik_handles.append(ik_handle_info)
                self.logger.info(f"Created IK handle '{ik_handle}' ({start_joint} -> {end_joint})")

            except Exception as e:
                safe_log_error(
                    self.logger,
                    "Failed to create IK handle",
                    SimpleNamespace(name=chain["ik_bone"]),
                    e,
                )

        return ik_handles

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
        existing_master = self._find_joint_by_japanese_name(get_original_bone_name_aliases("全ての親"))
        # 英語名でもチェック
        if not existing_master and maya_scene_utils.object_exists("master"):
            existing_master = "master"

        if not existing_master:
            master = cmds.group(empty=True, name="master", parent=skeleton_group)
            semi_standard_bones["master"] = master
            self.logger.info(f"Added master bone: {master}")
        else:
            master = existing_master
            self.logger.info(f"Using existing master bone: {existing_master}")

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
                    maya_scene_utils.parent_objects(child, master)

        # グルーブ
        # 既存のグルーブボーンを日本語名でチェック
        existing_groove = self._find_joint_by_japanese_name(get_original_bone_name_aliases("グルーブ"))
        # 英語名でもチェック
        if not existing_groove and maya_scene_utils.object_exists("groove"):
            existing_groove = "groove"

        center_joint = self._find_joint_by_name(maya_joints, get_bone_aliases("センター"))

        if not existing_groove and center_joint:
            # センターの位置を取得
            center_pos = cmds.xform(center_joint, query=True, worldSpace=True, translation=True)

            # グルーブを作成
            groove = cmds.group(
                empty=True,
                name="groove",
                parent=master,
            )
            cmds.xform(groove, worldSpace=True, translation=center_pos)
            semi_standard_bones["groove"] = groove

            # センターをグルーブの子にする
            maya_scene_utils.parent_objects(center_joint, groove)
            self.logger.info(f"Added groove bone: {groove}")
        elif existing_groove:
            self.logger.info(f"Using existing groove bone: {existing_groove}")

        # 腰ボーン（下半身と足の間）
        # 既存の腰ボーンを日本語名でチェック
        existing_waist = self._find_joint_by_japanese_name(get_original_bone_name_aliases("腰"))
        # 英語名でもチェック
        if not existing_waist:
            existing_waist = self._find_joint_by_name(maya_joints, get_bone_aliases("腰"))

        lower_body_joint = self._find_joint_by_name(maya_joints, get_bone_aliases("下半身"))
        left_leg_joint = self._find_joint_by_name(maya_joints, get_bone_aliases("左足"))

        if not existing_waist and lower_body_joint and left_leg_joint:
            # 下半身と左足の中間位置を計算
            lower_body_pos = cmds.xform(lower_body_joint, query=True, worldSpace=True, translation=True)
            left_leg_pos = cmds.xform(left_leg_joint, query=True, worldSpace=True, translation=True)

            waist_pos = [
                (lower_body_pos[0] + left_leg_pos[0]) / 2,
                (lower_body_pos[1] + left_leg_pos[1]) / 2,
                (lower_body_pos[2] + left_leg_pos[2]) / 2,
            ]

            # 腰ボーンを作成
            maya_scene_utils.select_objects(clear=True)
            waist = cmds.joint(name="waist", position=waist_pos)
            semi_standard_bones["waist"] = waist

            # 階層を設定（下半身の子、足の親）
            maya_scene_utils.parent_objects(waist, lower_body_joint)

            # 左右の足を腰の子にする
            right_leg_joint = self._find_joint_by_name(maya_joints, get_bone_aliases("右足"))

            # 左足を腰の子にする（既に存在確認済み）
            maya_scene_utils.parent_objects(left_leg_joint, waist)
            if right_leg_joint:
                maya_scene_utils.parent_objects(right_leg_joint, waist)

            self.logger.info(f"Added waist bone: {waist}")
        elif existing_waist:
            # 既存の腰ボーンを使用（新規作成しないので辞書には追加しない）
            self.logger.info(f"Using existing waist bone: {existing_waist}")

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
                    all_joints = maya_scene_utils.list_objects(type="joint")
                    for joint in all_joints:
                        # カスタムアトリビュートでボーンインデックスを確認
                        if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
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
        grant_reference = None

        # 付与ボーンの情報を収集し、変形階層でソート
        given_bones = []
        for i, bone in enumerate(bones):
            if i >= len(maya_joints):
                continue

            # PMXボーンの場合のみ付与設定をチェック
            if not hasattr(bone, "get_flag"):
                continue

            # 付与フラグをチェック
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GRANT_PARENT_MOVE):
                given_bones.append(
                    {
                        "index": i,
                        "bone": bone,
                        "joint": maya_joints[i],
                        "transform_layer": getattr(bone, "transform_layer", 0),
                        "is_physics_after": bone.get_flag(PmxBoneFlag.DEFORM_AFTER_PHYSICS),
                    }
                )

        # 変形順序でソート（物理前後 → 変形階層 → インデックス）
        given_bones.sort(key=lambda x: (x["is_physics_after"], x["transform_layer"], x["index"]))

        # 多重付与の依存関係を解決
        given_bones = self._resolve_given_dependencies(given_bones, bones)

        # ソートされた順序で付与を設定
        for given_info in given_bones:
            bone = given_info["bone"]
            joint = given_info["joint"]
            i = given_info["index"]

            # ローカル付与フラグをチェック
            is_local_given = bone.get_flag(PmxBoneFlag.LOCAL)

            # 自己参照ガード: 付与親が自分自身のジョイントを指す場合、Maya の
            # orient/pointConstraint は「自分自身にはコンストレイントできない」と例外を
            # 投げるため、warning を出してこのボーンの付与をスキップする。
            grant_parent_index = getattr(bone, "grant_parent_bone_index", -1)
            if 0 <= grant_parent_index < len(maya_joints) and maya_joints[grant_parent_index] == joint:
                self.logger.warning(f"Append parent points to itself; skipping append: {joint}")
                continue

            # 回転付与
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE):
                parent_index = bone.grant_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    grant_rate = bone.grant_rate
                    offset_flag = not is_local_given  # ローカル付与の場合はオフセットを維持しない

                    if grant_rate == -1:
                        # -1は付与親の回転の逆を適用する。 （一時的にUpベクトルに固定。）
                        grant_reference = self._get_grant_reference_node(maya_joints, grant_reference)
                        constraint = cmds.orientConstraint(
                            [grant_reference, parent_joint],
                            joint,
                            maintainOffset=offset_flag,
                        )[0]
                        self._mark_mmd_grant_constraint(constraint)
                        self._set_constraint_target_weights(constraint, [1.0, 0.0])
                    elif 0 <= grant_rate < 1:
                        # ０～１のときは、付与親の回転を部分的に適用する。
                        grant_reference = self._get_grant_reference_node(maya_joints, grant_reference)
                        constraint = cmds.orientConstraint(
                            [grant_reference, parent_joint],
                            joint,
                            maintainOffset=offset_flag,
                        )[0]
                        self._mark_mmd_grant_constraint(constraint)
                        self._set_constraint_target_weights(constraint, [1.0 - grant_rate, grant_rate])
                    elif grant_rate == 1:
                        constraint = cmds.orientConstraint(parent_joint, joint, maintainOffset=offset_flag, weight=1.0)[0]
                        self._mark_mmd_grant_constraint(constraint)
                    else:
                        constraint = cmds.orientConstraint(
                            parent_joint,
                            joint,
                            maintainOffset=offset_flag,
                            weight=grant_rate,
                        )[0]
                        self._mark_mmd_grant_constraint(constraint)

                    constraints.append(constraint)
                    given_type = "local append" if is_local_given else "global append"
                    self.logger.info(
                        f"Configured rotation append ({given_type}): {joint} <- {parent_joint} (rate={grant_rate}, layer={given_info['transform_layer']})"
                    )

            # 移動付与
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_MOVE):
                parent_index = bone.grant_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    grant_rate = bone.grant_rate

                    constraint = cmds.pointConstraint(parent_joint, joint, maintainOffset=True, weight=grant_rate)[0]
                    self._mark_mmd_grant_constraint(constraint)

                    constraints.append(constraint)
                    given_type = "local append" if is_local_given else "global append"
                    self.logger.info(
                        f"Configured translation append ({given_type}): {joint} <- {parent_joint} (rate={grant_rate}, layer={given_info['transform_layer']})"
                    )

        return constraints

    def _mark_mmd_grant_constraint(self, constraint):
        """runtime bake時に無効化できるMMD付与constraintとして印を付ける。"""
        try:
            if not cmds.attributeQuery("mmd_grant_constraint", node=constraint, exists=True):
                cmds.addAttr(constraint, longName="mmd_grant_constraint", attributeType="bool")
            cmds.setAttr(f"{constraint}.mmd_grant_constraint", True)
        except Exception:
            pass

    def _set_constraint_target_weights(self, constraint, weights):
        """
        constraintターゲットのweight aliasへ値を設定する。

        Args:
            constraint (str): constraintノード名
            weights (list[float]): 各ターゲットのウェイト
        """
        weight_aliases = cmds.orientConstraint(constraint, query=True, weightAliasList=True) or []
        for alias, weight in zip(weight_aliases, weights):
            cmds.setAttr(f"{constraint}.{alias}", weight)

    def _get_grant_reference_node(self, maya_joints, cached_reference=None):
        """
        部分的な回転付与で使用する中立ターゲットを取得する。

        Args:
            maya_joints (list): Mayaジョイント名のリスト
            cached_reference (str): すでに解決済みの参照ノード名

        Returns:
            str: 実在する参照ノード名
        """
        if cached_reference and maya_scene_utils.object_exists(cached_reference):
            return cached_reference

        master_joint = self._find_joint_by_japanese_name(["全ての親", "マスター"])
        if master_joint and maya_scene_utils.object_exists(master_joint):
            return master_joint

        master_joint = self._find_joint_by_name(maya_joints, ["master", "全ての親", "マスター"])
        if master_joint and maya_scene_utils.object_exists(master_joint):
            return master_joint

        reference = "mmd_grant_reference"
        if not maya_scene_utils.object_exists(reference):
            reference = cmds.group(empty=True, name=reference)
            maya_utils.set_attribute(reference, "visibility", False, "bool")
            self.logger.info(f"Added reference node for rotation append: {reference}")

        return reference

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
            if hasattr(bone, "grant_parent_bone_index"):
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
        # 入次数と依存先から依存元への隣接リストを計算
        in_degree = {node: len(deps) for node, deps in dependencies.items()}
        dependents = {node: [] for node in dependencies}
        for node, deps in dependencies.items():
            for dep in deps:
                if dep in dependents:
                    dependents[dep].append(node)

        # 入次数0のノードをキューに追加
        queue = [node for node, degree in in_degree.items() if degree == 0]
        sorted_nodes = []

        while queue:
            node = queue.pop(0)
            sorted_nodes.append(node)

            # このノードに依存するノードの入次数を減らす
            for dependent in dependents.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # 循環依存がある場合は警告
        if len(sorted_nodes) < len(dependencies):
            remaining = set(dependencies.keys()) - set(sorted_nodes)
            self.logger.warning(f"Cyclic dependency detected: {remaining}")
            # 循環依存のあるノードも含める（元の順序を保持）
            for node in dependencies:
                if node not in sorted_nodes:
                    sorted_nodes.append(node)

        return sorted_nodes

    # ------------------------------------------------------------------
    # Native rig: manifest → mmdAppend / mmdCcdIk DG nodes
    # ------------------------------------------------------------------

    def _create_append_nodes_from_manifest(
        self,
        manifest,
        maya_joints: List[str],
    ) -> List[str]:
        """マニフェストの付与情報から mmdAppend DG ノードを作成・接続する。"""

        nodes: List[str] = []
        grants = list(manifest.grants)
        if not grants:
            return nodes

        bones = manifest.bones
        grants.sort(key=lambda g: (
            bones[g["targetBoneIndex"]].get("transformAfterPhysics", False),
            bones[g["targetBoneIndex"]].get("deformLayer", 0),
            g["targetBoneIndex"],
        ))
        grants = self._resolve_grant_dependencies_from_manifest(grants)

        append_nodes_by_target: Dict[str, str] = {}
        for grant in grants:
            target_idx = grant["targetBoneIndex"]
            source_idx = grant["sourceBoneIndex"]
            ratio = grant.get("ratio", 0.0)
            affect_rotation = grant.get("affectRotation", False)
            affect_translation = grant.get("affectTranslation", False)
            local_append = grant.get("local", False)

            if target_idx < 0 or target_idx >= len(maya_joints):
                continue
            if source_idx < 0 or source_idx >= len(maya_joints):
                continue

            target_joint = maya_joints[target_idx]
            source_joint = maya_joints[source_idx]
            if target_joint == source_joint:
                continue

            if not maya_scene_utils.object_exists(target_joint) or not maya_scene_utils.object_exists(source_joint):
                continue

            try:
                node_name = f"{_node_leaf_name(target_joint)}_mmdAppend"
                node = cmds.createNode(self._append_node_type(), name=node_name)

                cmds.setAttr(f"{node}.ratio", ratio)
                cmds.setAttr(f"{node}.affectRotation", affect_rotation)
                cmds.setAttr(f"{node}.affectTranslation", affect_translation)
                if cmds.attributeQuery("localAppend", node=node, exists=True):
                    cmds.setAttr(f"{node}.localAppend", bool(local_append))
                if cmds.attributeQuery("schemaMode", node=node, exists=True):
                    cmds.setAttr(f"{node}.schemaMode", MMD_APPEND_SCHEMA_MODE_COMPAT)

                source_append_node = append_nodes_by_target.get(source_joint)

                # MMD runtime uses the source append contribution for non-local append chains.
                if affect_rotation and source_append_node and not local_append:
                    cmds.connectAttr(f"{source_append_node}.appendRotate", f"{node}.sourceRotate")
                    source_jo = (0.0, 0.0, 0.0)
                else:
                    cmds.connectAttr(f"{source_joint}.rotate", f"{node}.sourceRotate")
                    source_jo = cmds.getAttr(f"{source_joint}.jointOrient")[0]
                if affect_rotation and cmds.attributeQuery("sourceJointOrient", node=node, exists=True):
                    cmds.setAttr(
                        f"{node}.sourceJointOrient",
                        source_jo[0],
                        source_jo[1],
                        source_jo[2],
                        type="double3",
                    )
                if affect_rotation and cmds.attributeQuery("targetJointOrient", node=node, exists=True):
                    target_jo = cmds.getAttr(f"{target_joint}.jointOrient")[0]
                    cmds.setAttr(
                        f"{node}.targetJointOrient",
                        target_jo[0],
                        target_jo[1],
                        target_jo[2],
                        type="double3",
                    )
                if affect_translation:
                    if source_append_node and not local_append:
                        cmds.connectAttr(f"{source_append_node}.appendTranslate", f"{node}.sourceTranslate")
                    else:
                        source_rest_translate = cmds.getAttr(f"{source_joint}.translate")[0]
                        source_delta = cmds.createNode(
                            "plusMinusAverage",
                            name=f"{node_name}_sourceTranslateDelta",
                        )
                        cmds.setAttr(f"{source_delta}.operation", 2)  # subtract
                        cmds.connectAttr(f"{source_joint}.translate", f"{source_delta}.input3D[0]")
                        cmds.setAttr(
                            f"{source_delta}.input3D[1]",
                            source_rest_translate[0],
                            source_rest_translate[1],
                            source_rest_translate[2],
                            type="double3",
                        )
                        cmds.connectAttr(f"{source_delta}.output3D", f"{node}.sourceTranslate")

                if affect_rotation:
                    # Disconnect existing connections to target.rotate if any
                    existing = cmds.listConnections(f"{target_joint}.rotate", s=True, d=False, p=True) or []
                    for conn in existing:
                        try:
                            cmds.disconnectAttr(conn, f"{target_joint}.rotate")
                        except Exception:
                            pass
                    # Wire: baseRotate from animation (if curves exist),
                    # then node.outputRotate → joint.rotate.
                    for axis in ("X", "Y", "Z"):
                        anim_src = cmds.listConnections(
                            f"{target_joint}.rotate{axis}", s=True, d=False, p=True
                        )
                        if anim_src:
                            cmds.disconnectAttr(anim_src[0], f"{target_joint}.rotate{axis}")
                            cmds.connectAttr(anim_src[0], f"{node}.baseRotate{axis}")

                    cmds.connectAttr(f"{node}.outputRotate", f"{target_joint}.rotate")

                if affect_translation:
                    target_rest_translate = cmds.getAttr(f"{target_joint}.translate")[0]
                    cmds.setAttr(
                        f"{node}.baseTranslate",
                        target_rest_translate[0],
                        target_rest_translate[1],
                        target_rest_translate[2],
                        type="double3",
                    )
                    for axis in ("X", "Y", "Z"):
                        anim_src = cmds.listConnections(
                            f"{target_joint}.translate{axis}", s=True, d=False, p=True
                        )
                        if anim_src:
                            cmds.disconnectAttr(anim_src[0], f"{target_joint}.translate{axis}")
                            cmds.connectAttr(anim_src[0], f"{node}.baseTranslate{axis}")

                    cmds.connectAttr(f"{node}.outputTranslate", f"{target_joint}.translate")

                if not cmds.attributeQuery("mmd_grant_node", node=node, exists=True):
                    cmds.addAttr(node, longName="mmd_grant_node", attributeType="bool")
                cmds.setAttr(f"{node}.mmd_grant_node", True)

                nodes.append(node)
                append_nodes_by_target[target_joint] = node
                self.logger.info(
                    f"{self._append_node_type()} node '{node}': {source_joint} -> {target_joint} (ratio={ratio})"
                )
            except Exception as e:
                safe_log_error(
                    self.logger,
                    "Failed to create mmdAppend node",
                    SimpleNamespace(name=target_joint),
                    e,
                )

        return nodes

    def _build_ik_chain_json(
        self,
        manifest,
        chain_def: Dict[str, Any],
        mapping: Dict[str, Any],
        maya_joints: List[str],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Build the JSON payload consumed by the native IK node."""
        controller_idx = chain_def.get("controllerBoneIndex", -1)
        target_idx = chain_def.get("targetBoneIndex", -1)
        links = chain_def.get("links", [])
        pmx_to_slot = mapping["pmx_to_slot"]
        slot_to_pmx = mapping["slot_to_pmx"]
        bone_count = len(pmx_to_slot)

        # Collect absolute positions first, then convert to local (parent-relative).
        # The rig primitive solver expects local rest positions.
        abs_positions = []
        bones_for_json = []
        for slot in range(bone_count):
            pmx_idx = slot_to_pmx[slot]
            bone_data = manifest.bones[pmx_idx]
            parent_pmx = bone_data.get("parentIndex", -1)
            parent_slot = pmx_to_slot.get(parent_pmx, -1)
            abs_pos = bone_data.get("restPosition", [0, 0, 0])
            abs_positions.append(abs_pos)
            flags = 0
            fixed_axis = bone_data.get("fixedAxis")
            fa = [0, 0, 0]
            if fixed_axis is not None:
                flags = 0x1  # MMD_RUNTIME_RIG_BONE_FIXED_AXIS (1 << 0)
                fa = list(fixed_axis)
            jo_deg = [0.0, 0.0, 0.0]
            if pmx_idx < len(maya_joints):
                jnt = maya_joints[pmx_idx]
                if maya_scene_utils.object_exists(jnt):
                    try:
                        jo = cmds.getAttr(f"{jnt}.jointOrient")[0]
                        jo_deg = [jo[0], jo[1], jo[2]]
                    except Exception:
                        pass
            bones_for_json.append({
                "parent_slot": parent_slot,
                "rest_position": None,
                "maya_rest_translate": None,
                "maya_bind_world_matrix": None,
                "no_orient_bind_world_matrix": None,
                "flags": flags,
                "fixed_axis": fa,
                "joint_orient_deg": jo_deg,
            })

        for slot, bone in enumerate(bones_for_json):
            abs_pos = abs_positions[slot]
            parent_slot = bone["parent_slot"]
            if parent_slot >= 0:
                parent_abs = abs_positions[parent_slot]
                bone["rest_position"] = [abs_pos[j] - parent_abs[j] for j in range(3)]
            else:
                bone["rest_position"] = list(abs_pos)
            pmx_idx = slot_to_pmx[slot]
            if pmx_idx < len(maya_joints):
                jnt = maya_joints[pmx_idx]
                if maya_scene_utils.object_exists(jnt):
                    try:
                        bone["maya_rest_translate"] = list(cmds.getAttr(f"{jnt}.translate")[0])
                    except Exception:
                        bone["maya_rest_translate"] = list(bone["rest_position"])
                    try:
                        bind_world = [float(v) for v in cmds.getAttr(f"{jnt}.worldMatrix[0]")]
                        no_orient = [0.0] * 16
                        no_orient[0] = no_orient[5] = no_orient[10] = no_orient[15] = 1.0
                        no_orient[12] = bind_world[12]
                        no_orient[13] = bind_world[13]
                        no_orient[14] = bind_world[14]
                        bone["maya_bind_world_matrix"] = bind_world
                        bone["no_orient_bind_world_matrix"] = no_orient
                    except Exception:
                        pass
            if bone["maya_rest_translate"] is None:
                bone["maya_rest_translate"] = list(bone["rest_position"])

        links_for_json = []
        for lk in links:
            bone_slot = pmx_to_slot.get(lk["boneIndex"], -1)
            if bone_slot < 0:
                continue
            lmin = lk.get("angleLimitMin", [0, 0, 0])
            lmax = lk.get("angleLimitMax", [0, 0, 0])
            links_for_json.append({
                "bone_slot": bone_slot,
                "has_angle_limit": lk.get("hasAngleLimit", False),
                "angle_limit_min": list(lmin),
                "angle_limit_max": list(lmax),
            })

        target_slot = pmx_to_slot.get(target_idx, 0)
        controller_slot = pmx_to_slot.get(controller_idx, target_slot)
        chain_json = json.dumps({
            "bones": bones_for_json,
            "controllerBoneSlot": controller_slot,
            "targetBoneSlot": target_slot,
            "links": links_for_json,
            "iterationCount": chain_def.get("iterationCount", 40),
            "limitAngle": chain_def.get("limitAngle", 2.0),
        })
        return chain_json, links_for_json

    def _existing_ik_link_joints(
        self,
        link_slots: List[int],
        slot_to_pmx: Dict[int, int],
        maya_joints: List[str],
    ) -> List[str]:
        """Return existing Maya joints that correspond to IK link slots."""
        link_joints = []
        for link_slot in link_slots:
            pmx_idx = slot_to_pmx.get(link_slot, -1)
            if 0 <= pmx_idx < len(maya_joints):
                link_joint = maya_joints[pmx_idx]
                if maya_scene_utils.object_exists(link_joint):
                    link_joints.append(link_joint)
        return link_joints

    def _connect_ik_goal_world_matrix_if_safe(
        self,
        node: str,
        controller_joint: str,
        link_slots: List[int],
        slot_to_pmx: Dict[int, int],
        maya_joints: List[str],
    ) -> None:
        """Connect the controller world matrix when it will not create a DG cycle."""
        link_joints = self._existing_ik_link_joints(link_slots, slot_to_pmx, maya_joints)
        if not self._can_connect_live_ik_goal_world_matrix(controller_joint, link_joints):
            return
        try:
            cmds.connectAttr(
                f"{controller_joint}.worldMatrix[0]",
                f"{node}.goalWorldMatrix",
                force=True,
            )
        except Exception as exc:
            self.logger.debug(f"failed to connect IK goalWorldMatrix for {node}: {exc}")

    @staticmethod
    def _excluded_ik_input_bones(
        links: List[Dict[str, Any]],
        all_chain_links: List[Tuple[int, set]],
        controller_idx: int,
    ) -> set:
        """Return PMX bone indices excluded from inputRotate to avoid DG cycles."""
        excluded_bones = {lk["boneIndex"] for lk in links}
        for other_ctrl, other_links in all_chain_links:
            if other_ctrl > controller_idx:
                excluded_bones.update(other_links)
        return excluded_bones

    def _connect_ik_input_channels(
        self,
        node: str,
        *,
        bone_count: int,
        slot_to_pmx: Dict[int, int],
        maya_joints: List[str],
        links: List[Dict[str, Any]],
        all_chain_links: List[Tuple[int, set]],
        controller_idx: int,
    ) -> None:
        """Connect rotate/translate inputs for a native IK node."""
        excluded_bones = self._excluded_ik_input_bones(links, all_chain_links, controller_idx)

        for slot in range(bone_count):
            pmx_idx = slot_to_pmx[slot]
            if pmx_idx in excluded_bones:
                continue
            if pmx_idx < len(maya_joints):
                jnt = maya_joints[pmx_idx]
                if maya_scene_utils.object_exists(jnt):
                    cmds.connectAttr(f"{jnt}.rotate", f"{node}.inputRotate[{slot}]")

        # inputTranslate: ALL bones for position offset computation.
        for slot in range(bone_count):
            pmx_idx = slot_to_pmx[slot]
            if pmx_idx < len(maya_joints):
                jnt = maya_joints[pmx_idx]
                if maya_scene_utils.object_exists(jnt):
                    cmds.connectAttr(f"{jnt}.translate", f"{node}.inputTranslate[{slot}]")

    def _connect_ik_output_rotates(
        self,
        node: str,
        link_slots: List[int],
        slot_to_pmx: Dict[int, int],
        maya_joints: List[str],
    ) -> None:
        """Connect native IK output rotations to link joints."""
        for link_i, link_slot in enumerate(link_slots):
            pmx_idx = slot_to_pmx.get(link_slot, -1)
            if pmx_idx < 0 or pmx_idx >= len(maya_joints):
                continue
            link_joint = maya_joints[pmx_idx]
            if not maya_scene_utils.object_exists(link_joint):
                continue

            for axis in ("X", "Y", "Z"):
                src = cmds.listConnections(f"{link_joint}.rotate{axis}", s=True, d=False, p=True)
                if src:
                    try:
                        cmds.disconnectAttr(src[0], f"{link_joint}.rotate{axis}")
                    except Exception:
                        pass

            cmds.connectAttr(f"{node}.outputRotate[{link_i}]", f"{link_joint}.rotate")

    def _create_ik_nodes_from_manifest(
        self,
        manifest,
        maya_joints: List[str],
    ) -> List[str]:
        """マニフェストの IK チェーン情報から mmdCcdIk DG ノードを作成・接続する。

        Rig import 直後の手動 IK 操作も mmdCcdIk で扱う。VMD import 時は
        runtime final pose を inputRotate に焼き、mmdCcdIk を pass-through にする。
        """
        from mmd_tools.converters.native_rig_builder import build_ik_mini_chain

        # Collect all chains' link bones keyed by controller index.
        # Downstream chains (higher controller index) must NOT be read by
        # upstream chains — doing so creates a DG evaluation cycle
        # (e.g. leg_ik reads left_ankle -> toe_ik reads left_leg -> leg_ik).
        all_chain_links: list[tuple[int, set[int]]] = []
        for _cd in manifest.ik_chains:
            _ctrl = _cd.get("controllerBoneIndex", -1)
            _lk_set = {lk["boneIndex"] for lk in _cd.get("links", [])}
            all_chain_links.append((_ctrl, _lk_set))

        nodes: List[str] = []
        for chain_def in manifest.ik_chains:
            controller_idx = chain_def.get("controllerBoneIndex", -1)
            target_idx = chain_def.get("targetBoneIndex", -1)
            links = chain_def.get("links", [])

            if not links or controller_idx < 0 or target_idx < 0:
                continue
            if controller_idx >= len(maya_joints) or target_idx >= len(maya_joints):
                continue

            controller_joint = maya_joints[controller_idx]
            if not maya_scene_utils.object_exists(controller_joint):
                continue

            result = build_ik_mini_chain(manifest, chain_def)
            if result is None:
                self.logger.warning(f"IK mini-chain build failed for controller={controller_idx}")
                continue

            ik_chain_obj, mapping = result

            pmx_to_slot = mapping["pmx_to_slot"]
            slot_to_pmx = mapping["slot_to_pmx"]
            link_slots = mapping["link_slots"]
            bone_count = len(pmx_to_slot)

            chain_json, links_for_json = self._build_ik_chain_json(
                manifest,
                chain_def,
                mapping,
                maya_joints,
            )

            ik_chain_obj.free()

            try:
                controller_leaf_name = _node_leaf_name(controller_joint)
                node_name = f"{controller_leaf_name}_mmdCcdIk"
                node = cmds.createNode(self._ccd_ik_node_type(), name=node_name)
                cmds.setAttr(f"{node}.chainJson", chain_json, type="string")
                self._attach_ik_controller_shape(controller_joint)
                if cmds.attributeQuery("enabled", node=node, exists=True):
                    cmds.setAttr(f"{node}.enabled", False)
                try:
                    ik_bone_name = cmds.getAttr(f"{controller_joint}.mmd_bone_name")
                    if ik_bone_name and not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                        cmds.addAttr(node, longName="mmd_ik_bone_name", dataType="string")
                    if ik_bone_name:
                        cmds.setAttr(f"{node}.mmd_ik_bone_name", ik_bone_name, type="string")
                except Exception:
                    pass

                # Leave the public goal input unconnected for generated rigs.
                # mmdCcdIk reconstructs the pre-IK controller position from
                # controllerBoneSlot + inputTranslate.  External goal connections
                # remain supported and override that internal controller goal.
                self._connect_ik_goal_world_matrix_if_safe(
                    node,
                    controller_joint,
                    link_slots,
                    slot_to_pmx,
                    maya_joints,
                )

                # inputRotate: exclude own links AND downstream chains' links.
                # Downstream = higher controllerBoneIndex (evaluated later in MMD).
                # This prevents DG cycles (leg_ik <-> toe_ik) while still
                # allowing downstream chains to read upstream results.
                self._connect_ik_input_channels(
                    node,
                    bone_count=bone_count,
                    slot_to_pmx=slot_to_pmx,
                    maya_joints=maya_joints,
                    links=links,
                    all_chain_links=all_chain_links,
                    controller_idx=controller_idx,
                )
                self._connect_ik_output_rotates(node, link_slots, slot_to_pmx, maya_joints)

                if not cmds.attributeQuery("mmd_ik_native_node", node=node, exists=True):
                    cmds.addAttr(node, longName="mmd_ik_native_node", attributeType="bool")
                cmds.setAttr(f"{node}.mmd_ik_native_node", True)
                if cmds.attributeQuery("enabled", node=node, exists=True):
                    cmds.setAttr(f"{node}.enabled", True)
                    try:
                        cmds.dgdirty(node)
                    except Exception:
                        pass

                nodes.append(node)
                self.logger.info(
                    f"{self._ccd_ik_node_type()} node '{node}': controller={controller_joint}, "
                    f"{len(links_for_json)} links"
                )
            except Exception as e:
                safe_log_error(
                    self.logger,
                    "Failed to create mmdCcdIk node",
                    SimpleNamespace(name=controller_joint),
                    e,
                )

        return nodes

    def _attach_ik_controller_shape(self, controller_joint: str) -> Optional[str]:
        """Attach a lightweight NURBS control shape to the IK controller joint.

        The controller joint already drives generated ``mmdCcdIk`` goals in Rig
        mode.  To avoid adding constraints or evaluation cycles, this method only
        parents a curve shape under that joint transform so the existing joint is
        easier to select in the viewport.
        """
        if not controller_joint or not maya_scene_utils.object_exists(controller_joint):
            return None
        if cmds.attributeQuery("mmd_ik_controller_visual", node=controller_joint, exists=True):
            return None

        try:
            radius = 0.6
            children = cmds.listRelatives(controller_joint, children=True, type="joint") or []
            if children:
                child_positions = [
                    cmds.xform(child, query=True, worldSpace=True, translation=True)
                    for child in children
                ]
                own_pos = cmds.xform(controller_joint, query=True, worldSpace=True, translation=True)
                distances = [
                    sum((child_pos[i] - own_pos[i]) ** 2 for i in range(3)) ** 0.5
                    for child_pos in child_positions
                ]
                if distances:
                    radius = max(0.2, min(2.0, min(distances) * 0.25))

            controller_leaf_name = _node_leaf_name(controller_joint)
            ctrl_name = f"{controller_leaf_name}_ctrlShape#"
            curve_transform = cmds.circle(
                name=f"{controller_leaf_name}_ctrl#",
                normal=(0.0, 1.0, 0.0),
                radius=radius,
                constructionHistory=False,
            )[0]
            curve_shape = (cmds.listRelatives(curve_transform, shapes=True, fullPath=True) or [None])[0]
            if not curve_shape:
                cmds.delete(curve_transform)
                return None
            curve_shape = cmds.rename(curve_shape, ctrl_name)
            curve_shape_short = curve_shape.rsplit("|", 1)[-1]
            cmds.parent(curve_shape, controller_joint, shape=True, relative=True)
            cmds.delete(curve_transform)
            shapes = cmds.listRelatives(controller_joint, shapes=True, fullPath=True) or []
            attached = next((shape for shape in shapes if shape.endswith(curve_shape_short)), None)
            if attached is None and shapes:
                attached = shapes[-1]

            if attached:
                try:
                    cmds.setAttr(f"{attached}.overrideEnabled", True)
                    cmds.setAttr(f"{attached}.overrideColor", 17)  # yellow
                except Exception:
                    pass

            if not cmds.attributeQuery("mmd_ik_controller_visual", node=controller_joint, exists=True):
                cmds.addAttr(controller_joint, longName="mmd_ik_controller_visual", attributeType="bool")
            cmds.setAttr(f"{controller_joint}.mmd_ik_controller_visual", True)
            return attached
        except Exception as exc:
            self.logger.debug(f"failed to attach IK controller shape for {controller_joint}: {exc}")
            return None

    def _can_connect_live_ik_goal_world_matrix(self, controller_joint: str, link_joints: List[str]) -> bool:
        """Return True when controller.worldMatrix will not depend on IK output links."""
        ancestors = set()
        current = controller_joint
        while True:
            parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
            if not parents:
                break
            current = parents[0]
            ancestors.add(current)
        return not any(link_joint in ancestors for link_joint in link_joints)


    def _resolve_grant_dependencies_from_manifest(
        self,
        grants: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """マニフェストの付与リストを多重付与の依存関係を考慮してトポロジカルソートする。"""
        target_set = {g["targetBoneIndex"] for g in grants}
        index_to_grant = {g["targetBoneIndex"]: g for g in grants}

        dependencies: Dict[int, List[int]] = {}
        for g in grants:
            target = g["targetBoneIndex"]
            source = g["sourceBoneIndex"]
            dependencies[target] = []
            if source in target_set:
                dependencies[target].append(source)

        sorted_indices = self._topological_sort(dependencies)

        sorted_grants: List[Dict[str, Any]] = []
        for idx in sorted_indices:
            if idx in index_to_grant:
                sorted_grants.append(index_to_grant[idx])

        for g in grants:
            if g["targetBoneIndex"] not in set(sorted_indices):
                sorted_grants.append(g)

        return sorted_grants
