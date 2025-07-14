from typing import Type, List, Tuple
import math

import maya
import maya.cmds as cmds
import maya.api.OpenMaya as om

from mmd_tools.core import utils
from mmd_tools.core.pmd_data import bone
from mmd_tools.core.pmx_data.bone import PmxBoneFlag

from ..core import maya_utils
from ..core.pmd_parser import PmdParser
from ..core.pmx_parser import PmxParser
from ..core.constants import SKELETON_GROUP




class BoneConverter:
    """
    MMDのボーンデータをMayaのジョイントに変換するクラス。
    PMDとPMXの両方のフォーマットに対応。
    ボーンの階層構造、位置、スキニング情報をMayaのジョイントに変換し、
    メッシュにスキニングを適用する。
    """

    def __init__(self):
        """
        コンストラクタ。
        """

    def convert_pmx_bones(self, pmx_data: PmxParser, mesh_node, root_group):
        """
        PMXのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmx_data (PmxParser): 解析されたPMXデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。
            root_group (str): ルートグループの名前。

        Returns:
            tuple: (作成されたMayaジョイントノードの名前のリスト,
                   スキンクラスターの名前)
        """
        # PMXのボーン階層をMayaのjointノードに変換する
        cmds.select(cl=True)
        
        # スケルトングループを作成
        skeleton_group = cmds.group(empty=True, name=SKELETON_GROUP, parent=root_group)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmx_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmx_data.bones, bone_map, "pmx", skeleton_group)

        # スキンクラスターを作成
        skin_cluster = self._create_skin_cluster(
            maya_joints, mesh_node, max_influence=4
        )

        # 頂点ウェイトを設定
        self._apply_pmx_vertex_weights(pmx_data, maya_joints, skin_cluster, mesh_node)

        # TODO: ボーンのローカル軸、変形階層、表示操作などを正確に再現する。
        # TODO: IKボーンが存在する場合は、MayaのikHandleを作成し、適切な設定を行う。

        return maya_joints, skin_cluster

    def convert_pmd_bones(self, pmd_data: PmdParser, mesh_node, root_group):
        """
        PMDのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmd_data (PmdParser): 解析されたPMDデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。
            root_group (str): ルートグループの名前。

        Returns:
            tuple: (作成されたMayaジョイントノードの名前のリスト,
                   スキンクラスターの名前)
        """
        # PMDのボーン階層をMayaのjointノードに変換する
        cmds.select(cl=True)
        
        # スケルトングループを作成
        skeleton_group = cmds.group(empty=True, name=SKELETON_GROUP, parent=root_group)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmd_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmd_data.bones, bone_map, "pmd", skeleton_group)

        # スキンクラスターを作成
        skin_cluster = self._create_skin_cluster(
            maya_joints, mesh_node, max_influence=2
        )

        # 頂点ウェイトを設定
        self._apply_pmd_vertex_weights(pmd_data, maya_joints, skin_cluster, mesh_node)

        # TODO: ボーンのローカル軸を正確に再現する。
        # TODO: IKボーンが存在する場合は、MayaのikHandleを作成し、適切な設定を行う。

        return maya_joints, skin_cluster

    def _create_bone_mapping(self, bones):
        """
        ボーン名とインデックスのマッピングを作成する。

        Args:
            bones: ボーンデータのリスト。

        Returns:
            dict: インデックスからサニタイズされたボーン名へのマッピング。
        """
        bone_map = {}
        used_names = set()

        for i, bone in enumerate(bones):
            joint_name = maya_utils.sanitize_text(bone.get_name())

            # 重複する名前がある場合はサフィックスを追加
            original_name = joint_name
            counter = 1
            while joint_name in used_names:
                joint_name = f"{original_name}_{counter}"
                counter += 1

            used_names.add(joint_name)
            bone_map[i] = joint_name

        return bone_map

    def _create_maya_joints(self, bones, bone_map, format_type, skeleton_group):
        """
        Mayaジョイントを作成する。

        Args:
            bones: ボーンデータのリスト。
            bone_map (dict): ボーン名のマッピング。
            format_type (str): フォーマットタイプ（'pmx' または 'pmd'）。
            skeleton_group (str): スケルトングループの名前。

        Returns:
            list: 作成されたMayaジョイントノードの名前のリスト。
        """
        maya_joints = []
        
        for i, bone in enumerate(bones):
            # bone_mapから既にユニークな名前を取得
            joint_name = bone_map[i]

            # 親ジョイントが存在する場合
            parent_name = None
            try:
                if bone.parent_bone_index != -1:
                    parent_name = bone_map[bone.parent_bone_index]
                    cmds.select(parent_name, r=True)
                else:
                    cmds.select(clear=True)
            except (TypeError, KeyError):
                # 親ボーンが存在しない場合は、選択をクリア
                cmds.select(clear=True)
                if parent_name:
                    print(f"警告: {parent_name} の選択でエラーが起きています。")

            # ジョイントを作成
            position = bone.position
            joint = cmds.joint(
                name=joint_name,
                position=[position[0], position[1], -position[2]],  # Mayaは左手系
                # Maya 2024以降では軸指定オプションは deprecated
                # 代わりにjointOrientで後から設定する
            )

            self._set_extra_attributes(i, joint, bone, format_type)
            
            maya_joints.append(joint)
        
        # ルートジョイントをスケルトングループにペアレント
        # 親を持たないジョイントを探す
        root_joints = []
        for i, bone in enumerate(bones):
            if bone.parent_bone_index == -1:
                root_joints.append(bone_map[i])
        
        # ルートジョイントをスケルトングループにペアレント
        for root_joint in root_joints:
            cmds.parent(root_joint, skeleton_group)

        return maya_joints
    
    def _set_extra_attributes(self, i, joint, bone, format_type):
        # フォーマットに応じたカスタム属性を設定
            if format_type == "pmx":
                attrs = {
                    "pmx_bone_index": i,
                    "pmx_bone_flag": bone.bone_flag,
                    "pmx_bone_name": bone.name,
                    "pmx_bone_name_english": bone.name_english,
                    "pmx_bone_parent_bone_index": bone.parent_bone_index,
                    "pmx_bone_rotatable": bool(bone.get_flag(PmxBoneFlag.ROTATABLE)),
                    "pmx_bone_movable": bool(bone.get_flag(PmxBoneFlag.MOVABLE)),
                }

                # 接続先ボーンの属性を設定
                attrs["pmx_connect_bone_type"] = "BONE_INDEX" if bone.get_flag(PmxBoneFlag.CONNECT_BONE) else "RELATIVE"
                if bone.get_flag(PmxBoneFlag.CONNECT_BONE):
                    attrs["pmx_connect_position_index"] = bone.connect_bone_index
                else:
                    attrs["pmx_connect_bone_offset"] = bone.connect_position_offset

                # 付与ボーンの属性を設定
                attrs["pmx_given_parent_rotate"] = bool(bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE))
                attrs["pmx_given_parent_move"] = bool(bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE))
                if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                    attrs["pmx_given_parent_bone_index"] = bone.given_parent_bone_index
                    attrs["pmx_given_rate"] = bone.given_rate
               
                # 軸固定の属性を設定
                attrs["pmx_axis_fixed"] = bool(bone.get_flag(PmxBoneFlag.AXIS_FIXED))
                if bone.get_flag(PmxBoneFlag.AXIS_FIXED):
                    attrs["pmx_axis_direction"] = bone.axis_direction
                
                # ローカル軸の属性を設定
                attrs["pmx_local_axis"] = bool(bone.get_flag(PmxBoneFlag.LOCAL_AXIS))
                if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
                    attrs["pmx_x_axis_direction"] = bone.x_axis_direction
                    attrs["pmx_z_axis_direction"] = bone.z_axis_direction

                # 外部親変形の属性を設定
                attrs["pmx_external_parent_deform"] = bool(bone.get_flag(PmxBoneFlag.EXTERNAL_PARENT_DEFORM))
                if bone.get_flag(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
                    attrs["pmx_key_value"] = bone.key_value

                # IK関連の属性を設定
                attrs["pmx_ik"] = bool(bone.get_flag(PmxBoneFlag.IK))
                if bone.get_flag(PmxBoneFlag.IK):
                    attrs["pmx_ik_target_bone_index"] = bone.ik_target_bone_index
                    attrs["pmx_ik_loop_count"] = bone.ik_loop_count
                    attrs["pmx_ik_limit_angle"] = bone.ik_limit_angle
                    # attrs["pmx_ik_links"] = bone.ik_links

                if bone.get_flag(PmxBoneFlag.CONNECT_BONE):
                    attrs["pmx_connect_bone_index"] = bone.connect_bone_index
                
                maya_utils.set_custom_attributes(
                    joint,
                    attrs
                )
            elif format_type == "pmd":
                attrs = {
                    "pmd_index": i,
                    "pmd_type": bone.bone_type.name,  # Enumの名前（文字列）を取得
                    "pmd_name": bone.name,
                    "pmd_name_english": bone.name_english,
                    "pmd_tail_pos_bone_index": bone.tail_pos_bone_index,
                    "pmd_parent_bone_index": bone.parent_bone_index,
                }
                maya_utils.set_custom_attributes(
                    joint,
                    attrs
                )

    def _create_skin_cluster(self, maya_joints, mesh_node, max_influence=4):
        """
        スキンクラスターを作成する。

        Args:
            maya_joints (list): Mayaジョイントノードの名前のリスト。
            mesh_node (str): メッシュノードの名前。

        Returns:
            str: 作成されたスキンクラスターの名前。
        """

        # skin_cluster = skin_cluster_result[0] if skin_cluster_result else None
        skin_cluster = cmds.skinCluster(
            maya_joints,
            mesh_node,
            toSelectedBones=True,
            normalizeWeights=2,
            maximumInfluences=max_influence,  # PMXは最大4つのボーンに制限されているため
            name="skinCluster",
        )[0]

        return skin_cluster

    def _get_pmx_vertex_weights(self, vertex) -> List[Tuple[int, float]]:
        """
        PMX頂点の重み情報をtransform_listに変換する。

        Args:
            vertex: PMX頂点データ。
            maya_joints (list): Mayaジョイントノードの名前のリスト。

        Returns:
            list: (joint_name, weight)のタプルのリスト。
        """
        weights = []

        if vertex.weight_transform_type == 0:  # BDEF1
            weights = self._get_bdef1_weights(vertex)
        elif vertex.weight_transform_type == 1:  # BDEF2
            weights = self._get_bdef2_weights(vertex)
        elif vertex.weight_transform_type == 2:  # BDEF4
            weights = self._get_bdef4_weights(vertex)
        elif vertex.weight_transform_type == 3:  # SDEF
            weights = self._get_sdef_weights(vertex)
        elif vertex.weight_transform_type == 4:  # QDEF
            weights = self._get_qdef_weights(vertex)

        return weights

    def _get_bdef1_weights(self, vertex) -> List[Tuple[int, float]]:
        """BDEF1の重み情報を取得する。"""
        bone_index = vertex.bone_indices[0]
        return [(bone_index, 1.0)]

    def _get_bdef2_weights(self, vertex) -> List[Tuple[int, float]]:
        """BDEF2の重み情報を取得する。"""
        bone1_index = vertex.bone_indices[0]
        bone2_index = vertex.bone_indices[1]
        weight1 = vertex.bone_weights[0]
        weight2 = 1.0 - weight1

        transform_list = []
        if weight1 > 0:
            transform_list.append((bone1_index, weight1))
        if weight2 > 0:
            transform_list.append((bone2_index, weight2))
        return transform_list

    def _get_bdef4_weights(self, vertex) -> List[Tuple[int, float]]:
        """BDEF4の重み情報を取得する。"""
        transform_list = []
        for j in range(4):
            bone_index = vertex.bone_indices[j]
            weight = vertex.bone_weights[j]
            if weight > 0:
                transform_list.append((bone_index, weight))
        return transform_list

    def _get_sdef_weights(self, vertex) -> List[Tuple[int, float]]:
        """SDEFの重み情報を取得する。"""
        return self._get_bdef2_weights(vertex)

    def _get_qdef_weights(self, vertex) -> List[Tuple[int, float]]:
        """QDEFの重み情報を取得する。"""
        return self._get_bdef4_weights(vertex)

    def _apply_pmx_vertex_weights(self, pmx_data, maya_joints, skin_cluster, mesh_node):
        """
        PMX頂点ウェイトをスキンクラスターに適用する。

        Args:
            pmx_data: PMXデータオブジェクト。
            maya_joints (list): Mayaジョイントノードの名前のリスト。
            skin_cluster (str): スキンクラスターの名前。
            mesh_node (str): メッシュノードの名前。
        """

        weights = []
        for vertex in pmx_data.vertices:
            # PMX頂点の重み情報を取得
            weight_maps = self._get_pmx_vertex_weights(vertex)
            # ボーンの数でリストを初期化
            vertex_weights = [0.0] * len(maya_joints)

            for joint_index, weight in weight_maps:
                # ボーンインデックスの境界チェック
                if joint_index >= len(maya_joints):
                    print(f"警告: 無効なボーンインデックス {joint_index}, max={len(maya_joints)-1}")
                    continue
                vertex_weights[joint_index] = weight

            weights.append(vertex_weights)

        maya_utils.apply_vertex_weights(
            skin_cluster,
            mesh_node,
            weights,
        )

    def _apply_pmd_vertex_weights(self, pmd_data, maya_joints, skin_cluster, mesh_node):
        """
        PMD頂点ウェイトをスキンクラスターに適用する。

        Args:
            pmd_data: PMDデータオブジェクト。
            maya_joints (list): Mayaジョイントノードの名前のリスト。
            skin_cluster (str): スキンクラスターの名前。
            mesh_node (str): メッシュノードの名前。
        """

        weights = []
        for vertex in pmd_data.vertices:
            # ボーンの数でリストを初期化
            vertex_weights = [0.0] * len(maya_joints)
            
            # PMD頂点の重み情報を取得
            bone1_index = vertex.bone_indices[0]
            bone2_index = vertex.bone_indices[1]
            
            # ボーンインデックスの境界チェック
            if bone1_index >= len(maya_joints) or bone2_index >= len(maya_joints):
                print(f"警告: 無効なボーンインデックス bone1={bone1_index}, bone2={bone2_index}, max={len(maya_joints)-1}")
                weights.append(vertex_weights)
                continue
            
            if bone1_index == bone2_index:
                # 同一ボーンの場合は100%の重み
                vertex_weights[bone1_index] = 1.0
            else:
                # 2つのボーンに分割
                weight1 = vertex.bone_weight / 100.0
                weight2 = 1.0 - weight1
                vertex_weights[bone1_index] = weight1
                vertex_weights[bone2_index] = weight2
            
            weights.append(vertex_weights)

        maya_utils.apply_vertex_weights(
            skin_cluster,
            mesh_node,
            weights,
        )
