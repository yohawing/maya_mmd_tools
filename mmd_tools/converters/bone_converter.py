from mmd_tools.core import maya_utils
from ..core.pmd_parser import PmdParser
from ..core.pmx_parser import PmxParser
import maya.cmds as cmds

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

    def convert_pmd_bones(self, pmd_data: PmdParser, mesh_node):
        """
        PMDのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmd_data (PmdParser): 解析されたPMDデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。

        Returns:
            list: 作成されたMayaジョイントノードの名前のリスト。
        """
        # PMDのボーン階層をMayaのjointノードに変換する
        cmds.select(clear=True)

        # ボーン名とインデックスのマッピングを作成
        bone_map = {i: maya_utils.sanitize_text(bone.name) for i, bone in enumerate(pmd_data.bones)}

        # Mayaジョイントを作成
        maya_joints = []
        for i, bone in enumerate(pmd_data.bones):
            joint_name = maya_utils.sanitize_text(bone.name)

            # 親ジョイントが存在する場合
            if bone.parent_bone_index != -1:
                parent_name = bone_map[bone.parent_bone_index]
                cmds.select(parent_name, noExpand=True)
            else:
                cmds.select(clear=True)

            # ジョイントを作成
            joint = cmds.joint(name=joint_name, position=bone.position)
            maya_utils.set_custom_attributes(joint, {
                'pmd_bone_index': i,
                'pmd_bone_type': bone.bone_type,
                'pmd_bone_name': bone.name,
                'pmd_bone_name_english': bone.name_english,
            })
            maya_joints.append(joint)

        # スキンクラスターを作成
        skin_cluster = cmds.skinCluster(maya_joints, mesh_node, toSelectedBones=True, removeUnusedInfluence=False)[0]

        # 頂点ウェイトを設定
        for i, vertex in enumerate(pmd_data.vertices):
            vertex_name = f'{mesh_node}.vtx[{i}]'
            bone1_index = vertex.bone_indices[0]
            bone2_index = vertex.bone_indices[1]
            weight1 = vertex.bone_weight / 100.0
            weight2 = 1.0 - weight1

            transform_list = []
            if weight1 > 0:
                transform_list.append((maya_joints[bone1_index], weight1))
            if weight2 > 0:
                transform_list.append((maya_joints[bone2_index], weight2))
            
            if transform_list:
                cmds.skinPercent(skin_cluster, vertex_name, transformValue=transform_list)

        # TODO: ボーンのローカル軸を正確に再現する。
        # TODO: IKボーンが存在する場合は、MayaのikHandleを作成し、適切な設定を行う。

        return maya_joints, skin_cluster

    def convert_pmx_bones(self, pmx_data: PmxParser, mesh_node):
        """
        PMXのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmx_data (PmxParser): 解析されたPMXデータオブジェクト。
            mesh_node (str): スキニングを適用するMayaのメッシュノードの名前。

        Returns:
            list: 作成されたMayaジョイントノードの名前のリスト。
        """
        # PMXのボーン階層をMayaのjointノードに変換する
        cmds.select(clear=True)
        
        # ボーン名とインデックスのマッピングを作成
        bone_map = {i: maya_utils.sanitize_text(bone.name) for i, bone in enumerate(pmx_data.bones)}

        # Mayaジョイントを作成
        maya_joints = []
        for i, bone in enumerate(pmx_data.bones):
            joint_name = maya_utils.sanitize_text(bone.name)

            # 親ジョイントが存在する場合
            if bone.parent_bone_index != -1:
                parent_name = bone_map[bone.parent_bone_index]
                cmds.select(parent_name, noExpand=True)
            else:
                cmds.select(clear=True)

            # ジョイントを作成
            joint = cmds.joint(name=joint_name, position=bone.position)
            maya_utils.set_custom_attributes(joint, {
                'pmx_bone_index': i,
                'pmx_bone_flag': bone.bone_flag,
                'pmx_bone_name': bone.name,
                'pmx_bone_name_english': bone.name_english,
            })
            #print bone.name and joint_name
            print(f"Creating joint: {joint_name} from {bone.name}")
            maya_joints.append(joint)
            
        # スキンクラスターを作成
        skin_cluster = cmds.skinCluster(maya_joints, mesh_node, toSelectedBones=True, removeUnusedInfluence=False)[0]

        # 頂点ウェイトを設定
        for i, vertex in enumerate(pmx_data.vertices):
            vertex_name = f'{mesh_node}.vtx[{i}]'
            transform_list = []
            if vertex.weight_transform_type == 0:  # BDEF1
                bone_index = vertex.bone_indices[0]
                transform_list.append((maya_joints[bone_index], 1.0))
            elif vertex.weight_transform_type == 1:  # BDEF2
                bone1_index = vertex.bone_indices[0]
                bone2_index = vertex.bone_indices[1]
                weight1 = vertex.bone_weights[0]
                weight2 = 1.0 - weight1
                if weight1 > 0:
                    transform_list.append((maya_joints[bone1_index], weight1))
                if weight2 > 0:
                    transform_list.append((maya_joints[bone2_index], weight2))
            elif vertex.weight_transform_type == 2:  # BDEF4
                for j in range(4):
                    bone_index = vertex.bone_indices[j]
                    weight = vertex.bone_weights[j]
                    if weight > 0:
                        transform_list.append((maya_joints[bone_index], weight))
            elif vertex.weight_transform_type == 3:  # SDEF
                bone1_index = vertex.bone_indices[0]
                bone2_index = vertex.bone_indices[1]
                weight1 = vertex.bone_weights[0]
                weight2 = 1.0 - weight1
                if weight1 > 0:
                    transform_list.append((maya_joints[bone1_index], weight1))
                if weight2 > 0:
                    transform_list.append((maya_joints[bone2_index], weight2))
            elif vertex.weight_transform_type == 4:  # QDEF
                for j in range(4):
                    bone_index = vertex.bone_indices[j]
                    weight = vertex.bone_weights[j]
                    if weight > 0:
                        transform_list.append((maya_joints[bone_index], weight))
            
            if transform_list:
                cmds.skinPercent(skin_cluster, vertex_name, transformValue=transform_list)

        # TODO: ボーンのローカル軸、変形階層、表示操作などを正確に再現する。
        # TODO: IKボーンが存在する場合は、MayaのikHandleを作成し、適切な設定を行う。
        
        return maya_joints, skin_cluster
