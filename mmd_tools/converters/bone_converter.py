from typing import Type

import maya
import maya.cmds as cmds

from mmd_tools.core import utils
from mmd_tools.core.pmd_data import bone

from ..core import maya_utils
from ..core.pmd_parser import PmdParser
from ..core.pmx_parser import PmxParser


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
        特に初期化は必要ないが、将来の拡張のために残しておく。
        """
        pass

    def convert_pmx_bones(self, pmx_data: PmxParser, mesh_node):
        """
        PMXのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmx_data (PmxParser): 解析されたPMXデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。

        Returns:
            tuple: (作成されたMayaジョイントノードの名前のリスト,
                   スキンクラスターの名前)
        """
        # PMXのボーン階層をMayaのjointノードに変換する
        cmds.select(cl=True)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmx_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmx_data.bones, bone_map, "pmx")

        # スキンクラスターを作成
        skin_cluster = self._create_skin_cluster(
            maya_joints, mesh_node, max_influence=4
        )

        # 頂点ウェイトを設定
        self._apply_pmx_vertex_weights(pmx_data, maya_joints, skin_cluster, mesh_node)

        # TODO: ボーンのローカル軸、変形階層、表示操作などを正確に再現する。
        # TODO: IKボーンが存在する場合は、MayaのikHandleを作成し、適切な設定を行う。

        return maya_joints, skin_cluster

    def convert_pmd_bones(self, pmd_data: PmdParser, mesh_node):
        """
        PMDのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmd_data (PmdParser): 解析されたPMDデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。

        Returns:
            tuple: (作成されたMayaジョイントノードの名前のリスト,
                   スキンクラスターの名前)
        """
        # PMDのボーン階層をMayaのjointノードに変換する
        cmds.select(cl=True)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmd_data.bones)

        # Mayaジョイントを作成
        maya_joints = self._create_maya_joints(pmd_data.bones, bone_map, "pmd")

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

    def _create_maya_joints(self, bones, bone_map, format_type):
        """
        Mayaジョイントを作成する。

        Args:
            bones: ボーンデータのリスト。
            bone_map (dict): ボーン名のマッピング。
            format_type (str): フォーマットタイプ（'pmx' または 'pmd'）。

        Returns:
            list: 作成されたMayaジョイントノードの名前のリスト。
        """
        maya_joints = []
        for i, bone in enumerate(bones):
            joint_name = maya_utils.sanitize_text(bone.get_name())

            # おなじ名前のジョイントがある場合はサフィックスを追加
            if len(cmds.ls(joint_name, type="joint")) > 0:
                original_name = joint_name
                counter = 1
                while len(cmds.ls(joint_name, type="joint")) > 0:
                    joint_name = f"{original_name}_{counter}"
                    counter += 1

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
            )

            # フォーマットに応じたカスタム属性を設定
            if format_type == "pmx":
                maya_utils.set_custom_attributes(
                    joint,
                    {
                        "pmx_bone_index": i,
                        "pmx_bone_flag": bone.bone_flag,
                        "pmx_bone_name": bone.name,
                        "pmx_bone_name_english": bone.name_english,
                    },
                )
            elif format_type == "pmd":
                maya_utils.set_custom_attributes(
                    joint,
                    {
                        "pmd_bone_index": i,
                        "pmd_bone_type": bone.bone_type,
                        "pmd_bone_name": bone.name,
                        "pmd_bone_name_english": bone.name_english,
                    },
                )

            maya_joints.append(joint)

        # TODO: JointOritentを適応
        # for joint in maya_joints:
        #     cmds.joint(
        #         joint, edit=True, orientJoint="xyz",
        #         secondaryAxisOrient="yup", children=True
        #     )

        return maya_joints

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

    def _get_pmx_vertex_weights(self, vertex) -> list[tuple[int, float]]:
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

    def _get_bdef1_weights(self, vertex) -> list[tuple[int, float]]:
        """BDEF1の重み情報を取得する。"""
        bone_index = vertex.bone_indices[0]
        return [(bone_index, 1.0)]

    def _get_bdef2_weights(self, vertex) -> list[tuple[int, float]]:
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

    def _get_bdef4_weights(self, vertex) -> list[tuple[int, float]]:
        """BDEF4の重み情報を取得する。"""
        transform_list = []
        for j in range(4):
            bone_index = vertex.bone_indices[j]
            weight = vertex.bone_weights[j]
            if weight > 0:
                transform_list.append((bone_index, weight))
        return transform_list

    def _get_sdef_weights(self, vertex) -> list[tuple[int, float]]:
        """SDEFの重み情報を取得する。"""
        return self._get_bdef2_weights(vertex)

    def _get_qdef_weights(self, vertex) -> list[tuple[int, float]]:
        """QDEFの重み情報を取得する。"""
        return self._get_bdef4_weights(vertex, maya_joints)

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
        influences = []
        for vertex in pmx_data.vertices:
            # PMX頂点の重み情報を取得
            weight_maps = self._get_pmx_vertex_weights(vertex)
            # ボーンの数でリストを初期化
            vertex_weights = [0.0] * len(maya_joints)

            for joint_index, weight in weight_maps:
                vertex_weights[joint_index] = weight

            weights.append(vertex_weights)
            influences.append([joint for joint, _ in weight_maps])

        maya_utils.apply_vertex_weights(
            pmx_data.vertices,
            maya_joints,
            skin_cluster,
            mesh_node,
            weights,
            influences,
            max_influences=4,  # PMXは最大4つのボーンに制限されているため
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
        influences = []
        for vertex in pmd_data.vertices:
            bone1_index = vertex.bone_indices[0]
            bone2_index = vertex.bone_indices[1]
            weight1 = vertex.bone_weight / 100.0
            weight2 = 1.0 - weight1

            weights.append([weight1, weight2])
            influences.append([bone1_index, bone2_index])

        maya_utils.apply_vertex_weights(
            pmd_data.vertices,
            maya_joints,
            skin_cluster,
            mesh_node,
            weights,
            influences,
            max_influences=2,  # PMDは最大2つのボーンに制限されているため
        )
