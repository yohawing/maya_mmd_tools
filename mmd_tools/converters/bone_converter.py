from typing import List, Tuple

import maya.cmds as cmds

from mmd_tools.core.pmx_data.bone import PmxBoneFlag

from ..core import maya_utils
from ..core.pmd_data import PmdData
from ..core.pmx_data import PmxData
from ..core.constants import (
    SKELETON_GROUP,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_BONE_OFFSET,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    # 詳細アトリビュート
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_PARENT_INDEX,
    ATTR_MMD_CONNECT_TYPE,
    ATTR_MMD_CONNECT_INDEX,
    ATTR_MMD_CONNECT_BONE_INDEX,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
    ATTR_MMD_IK_TARGET_INDEX,
    # PMD固有
    ATTR_MMD_BONE_TYPE,
    ATTR_MMD_TAIL_POS_INDEX,
)
from ..core.logger import get_logger
from .rig_converter import RigConverter


class BoneConverter:
    """
    MMDのボーンデータをMayaのジョイントに変換するクラス。
    PMDとPMXの両方のフォーマットに対応。
    ボーンの階層構造、位置、スキニング情報をMayaのジョイントに変換し、
    メッシュにスキニングを適用する。

    リグのセットアップ（IK、付与ボーンなど）はRigConverterが担当する。
    """

    def __init__(self):
        """
        コンストラクタ。
        """
        self.logger = get_logger(__name__)
        self.rig_converter = RigConverter()

    def convert_pmx_bones(self, pmx_data: PmxData, mesh_node, root_group):
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
        maya_utils.select_objects(clear=True)

        # スケルトングループを作成
        skeleton_group = cmds.group(empty=True, name=SKELETON_GROUP, parent=root_group)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmx_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmx_data.bones, bone_map, "pmx", skeleton_group)

        # リグのセットアップはRigConverterに委譲
        rig_result = self.rig_converter.setup_pmx_rig(pmx_data, maya_joints, bone_map, skeleton_group)

        # スキンクラスターを作成
        skin_cluster = self._create_skin_cluster(maya_joints, mesh_node, max_influence=4)

        # 頂点ウェイトを設定
        if mesh_node and skin_cluster:
            self._apply_pmx_vertex_weights(pmx_data, maya_joints, skin_cluster, mesh_node)

        # TODO: 変形階層、表示操作などを正確に再現する。

        return maya_joints, skin_cluster

    def convert_pmd_bones(self, pmd_data: PmdData, mesh_node, root_group):
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
        maya_utils.select_objects(clear=True)

        # スケルトングループを作成
        skeleton_group = cmds.group(empty=True, name=SKELETON_GROUP, parent=root_group)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmd_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmd_data.bones, bone_map, "pmd", skeleton_group)

        # スキンクラスターを作成
        skin_cluster = self._create_skin_cluster(maya_joints, mesh_node, max_influence=2)

        # 頂点ウェイトを設定
        if mesh_node and skin_cluster:
            self._apply_pmd_vertex_weights(pmd_data, maya_joints, skin_cluster, mesh_node)

        # リグのセットアップはRigConverterに委譲
        rig_result = self.rig_converter.setup_pmd_rig(pmd_data, maya_joints, bone_map, skeleton_group)

        # TODO: ボーンのローカル軸を正確に再現する。

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

        # まず、すべてのジョイントを親なしで作成
        for i, bone in enumerate(bones):
            # bone_mapから既にユニークな名前を取得
            joint_name = bone_map[i]

            # 選択をクリアしてルートにジョイントを作成
            maya_utils.select_objects(clear=True)

            # ジョイントを作成
            position = bone.position
            joint = cmds.joint(
                name=joint_name,
                position=[
                    position[0],
                    position[1],
                    -position[2],
                ],  # Z軸の向きを反転（MMD: +Z手前, Maya: +Z奥）
            )

            self._set_extra_attributes(i, joint, bone, format_type)
            maya_joints.append(joint)

        # 次に、親子関係を設定
        for i, bone in enumerate(bones):
            if bone.parent_bone_index != -1 and bone.parent_bone_index < len(bones):
                child_joint = maya_joints[i]
                parent_joint = maya_joints[bone.parent_bone_index]

                try:
                    # 親子関係を設定
                    cmds.parent(child_joint, parent_joint)
                except Exception as e:
                    self.logger.error(f"Failed to parent {child_joint} to {parent_joint}: {e}")

        # ルートジョイントをスケルトングループにペアレント
        # 親を持たないジョイントを探す
        root_joints = []
        for i, bone in enumerate(bones):
            if bone.parent_bone_index == -1:
                # 実際に作成されたジョイント名を使用
                root_joints.append(maya_joints[i])

        self.logger.debug(f"Root joints to parent: {root_joints}")
        self.logger.debug(f"Skeleton group: {skeleton_group}")

        # ルートジョイントをスケルトングループにペアレント
        # absolute=Trueを使用してワールド座標を維持
        for root_joint in root_joints:
            if cmds.objExists(root_joint):
                cmds.parent(root_joint, skeleton_group, absolute=True)
            else:
                self.logger.error(f"Root joint '{root_joint}' does not exist in scene")

        return maya_joints

    def _set_extra_attributes(self, i, joint, bone, format_type):
        # フォーマットに応じたカスタム属性を設定
        if format_type == "pmx":
            # 共通アトリビュートを設定
            attrs = {
                ATTR_MMD_BONE_NAME: bone.name,
                ATTR_MMD_BONE_NAME_EN: bone.name_english,
                ATTR_MMD_BONE_FLAGS: bone.bone_flag,
                ATTR_MMD_DEFORM_LAYER: bone.deform_layer if hasattr(bone, "deform_layer") else 0,
            }

            # 詳細アトリビュート
            attrs.update(
                {
                    ATTR_MMD_BONE_INDEX: i,
                    ATTR_MMD_BONE_PARENT_INDEX: bone.parent_bone_index,
                }
            )

            # 接続先ボーンの属性を設定
            if not bone.get_flag(PmxBoneFlag.CONNECT_BONE):
                # 座標オフセットの場合
                attrs[ATTR_MMD_BONE_OFFSET] = bone.connect_position_offset

            # 接続先属性
            attrs[ATTR_MMD_CONNECT_TYPE] = "BONE_INDEX" if bone.get_flag(PmxBoneFlag.CONNECT_BONE) else "RELATIVE"
            if bone.get_flag(PmxBoneFlag.CONNECT_BONE):
                attrs[ATTR_MMD_CONNECT_INDEX] = bone.connect_bone_index

            # 付与ボーンの属性を設定
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GRANT_PARENT_MOVE):
                attrs[ATTR_MMD_GRANT_RATE] = bone.grant_rate

            # 付与属性
            if bone.get_flag(PmxBoneFlag.GRANT_PARENT_ROTATE) or bone.get_flag(PmxBoneFlag.GRANT_PARENT_MOVE):
                attrs[ATTR_MMD_GRANT_PARENT_INDEX] = bone.grant_parent_bone_index

            # 軸固定の属性を設定
            if bone.get_flag(PmxBoneFlag.AXIS_FIXED):
                attrs[ATTR_MMD_FIXED_AXIS] = bone.axis_direction

            # ローカル軸の属性を設定
            if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
                attrs[ATTR_MMD_LOCAL_X_AXIS] = bone.x_axis_direction
                attrs[ATTR_MMD_LOCAL_Z_AXIS] = bone.z_axis_direction

            # 軸属性
            if bone.get_flag(PmxBoneFlag.AXIS_FIXED):
                attrs[ATTR_MMD_AXIS_DIRECTION] = bone.axis_direction

            if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
                attrs[ATTR_MMD_X_AXIS_DIRECTION] = bone.x_axis_direction
                attrs[ATTR_MMD_Z_AXIS_DIRECTION] = bone.z_axis_direction

            # 外部親変形の属性を設定
            if bone.get_flag(PmxBoneFlag.EXTERNAL_PARENT_DEFORM):
                attrs[ATTR_MMD_EXTERNAL_PARENT_KEY] = bone.key_value

            # 外部親属性

            # IK関連の属性を設定
            if bone.get_flag(PmxBoneFlag.IK):
                attrs[ATTR_MMD_IK_LOOP] = bone.ik_loop_count
                attrs[ATTR_MMD_IK_LIMIT_ANGLE] = bone.ik_limit_angle

            # IK属性
            if bone.get_flag(PmxBoneFlag.IK):
                attrs[ATTR_MMD_IK_TARGET_INDEX] = bone.ik_target_bone_index

                # IKリンクをJSON形式で保存
                import json

                ik_links_data = []
                for ik_link in bone.ik_links:
                    link_data = {
                        "bone": ik_link.ik_bone_index,
                        "limit_enabled": bool(ik_link.angle_limit),
                        "lower_limit": list(ik_link.limit_min) if ik_link.angle_limit else [0.0, 0.0, 0.0],
                        "upper_limit": list(ik_link.limit_max) if ik_link.angle_limit else [0.0, 0.0, 0.0],
                    }
                    ik_links_data.append(link_data)
                attrs[ATTR_MMD_IK_LINKS] = json.dumps(ik_links_data)

            if bone.get_flag(PmxBoneFlag.CONNECT_BONE):
                attrs[ATTR_MMD_CONNECT_BONE_INDEX] = bone.connect_bone_index

            maya_utils.set_custom_attributes(joint, attrs)
        elif format_type == "pmd":
            # 共通アトリビュートを設定
            attrs = {
                ATTR_MMD_BONE_NAME: bone.name,
                ATTR_MMD_BONE_NAME_EN: bone.name_english,
                # PMDはフラグを持たないのでデフォルト値を設定
                ATTR_MMD_BONE_FLAGS: 0x0005,  # 回転可能 + 表示
                ATTR_MMD_DEFORM_LAYER: 0,
            }

            # 詳細アトリビュート
            attrs.update(
                {
                    ATTR_MMD_BONE_INDEX: i,
                    ATTR_MMD_BONE_TYPE: bone.bone_type.name,  # Enumの名前（文字列）を取得
                    ATTR_MMD_TAIL_POS_INDEX: bone.tail_pos_bone_index,
                    ATTR_MMD_BONE_PARENT_INDEX: bone.parent_bone_index,
                }
            )
            maya_utils.set_custom_attributes(joint, attrs)

    def _create_skin_cluster(self, maya_joints, mesh_node, max_influence=4):
        """
        スキンクラスターを作成する。

        Args:
            maya_joints (list): Mayaジョイントノードの名前のリスト。
            mesh_node (str): メッシュノードの名前。

        Returns:
            str: 作成されたスキンクラスターの名前。
        """
        # 選択をクリアしてジョイントとメッシュのみを選択
        maya_utils.select_objects(clear=True)

        # メッシュノードが存在しない場合はNoneを返す
        if not mesh_node or not cmds.objExists(mesh_node):
            self.logger.warning(f"メッシュノード '{mesh_node}' が存在しません。スキンクラスターを作成しません。")
            return None

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
                    self.logger.warning(f"無効なボーンインデックス {joint_index}, max={len(maya_joints) - 1}")
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
                self.logger.warning(
                    f"無効なボーンインデックス bone1={bone1_index}, bone2={bone2_index}, max={len(maya_joints) - 1}"
                )
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

        # vertexの要素が存在することをチェック
        if not weights:
            self.logger.warning("頂点ウェイトが空です。メッシュに頂点が存在するか確認してください。")
            return
        maya_utils.apply_vertex_weights(
            skin_cluster,
            mesh_node,
            weights,
        )
