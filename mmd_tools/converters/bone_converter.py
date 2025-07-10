from typing import Type
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
        
        # 子ボーンのマッピングを事前に作成（向き計算で必要）
        children_map = self._create_children_map(bones)
        
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
            )

            # ジョイント作成直後にJointOrientを設定
            orient = self._calculate_joint_orient(bone, i, bones, children_map, format_type)
            self._set_joint_orient(joint, orient)

            # フォーマットに応じたカスタム属性を設定
            if format_type == "pmx":
                attrs = {
                    "pmx_index": i,
                    "pmx_flag": bone.bone_flag,
                    "pmx_name": bone.name,
                    "pmx_name_english": bone.name_english,
                    "pmx_parent_bone_index": bone.parent_bone_index,
                    "pmx_rotatable": bool(bone.get_flag(PmxBoneFlag.ROTATABLE)),
                    "pmx_movable": bool(bone.get_flag(PmxBoneFlag.MOVABLE)),
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
                    attrs["pmx_ik_links"] = bone.ik_links

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

            maya_joints.append(joint)

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

    def _calculate_joint_orient(self, bone, bone_index, bones, children_map, format_type):
        """
        フォーマットに応じてJointOrientを計算する。
        
        Args:
            bone: ボーンデータ
            bone_index: ボーンインデックス
            bones: 全ボーンデータのリスト
            children_map: 子ボーンマッピング
            format_type: 'pmx' または 'pmd'
        
        Returns:
            list: [x, y, z] オイラー角（度）
        """
        # 一時的に簡素化：基本的な子ボーン方向のみ計算
        try:
            # 子ボーンが存在する場合のみ簡単な計算を行う
            if bone_index in children_map:
                child_index = children_map[bone_index][0]
                return self._calculate_simple_aim_orient(bone, bones[child_index])
            
            # PMX特有の計算
            if format_type == 'pmx':
                # ローカル軸フラグがある場合のみ処理
                if hasattr(bone, 'bone_flag') and bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
                    return self._calculate_simple_local_axis_orient(bone)
                
                # 接続先ボーンが指定されている場合
                if hasattr(bone, 'connect_bone_index') and bone.connect_bone_index != -1:
                    if bone.connect_bone_index < len(bones):
                        return self._calculate_simple_aim_orient(bone, bones[bone.connect_bone_index])
            
            # PMD特有の計算
            if format_type == 'pmd':
                # tail_pos_bone_indexが有効な場合
                if hasattr(bone, 'tail_pos_bone_index') and bone.tail_pos_bone_index != -1:
                    if bone.tail_pos_bone_index < len(bones):
                        return self._calculate_simple_aim_orient(bone, bones[bone.tail_pos_bone_index])
            
            # デフォルトは回転なし
            return [0.0, 0.0, 0.0]
            
        except Exception as e:
            print(f"JointOrient計算でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _calculate_pmx_joint_orient(self, bone, bone_index, bones, children_map):
        """PMXボーンのJointOrientを計算"""
        # ローカル軸フラグがある場合
        if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
            return self._calculate_local_axis_orient(bone)
        
        # 軸固定フラグがある場合
        if bone.get_flag(PmxBoneFlag.AXIS_FIXED):
            return self._calculate_axis_fixed_orient(bone)
        
        # 子ボーンが存在する場合
        if bone_index in children_map:
            child_index = children_map[bone_index][0]  # 最初の子ボーンを使用
            return self._calculate_aim_orient(bone, bones[child_index])
        
        # 接続先ボーンが指定されている場合
        if bone.get_flag(PmxBoneFlag.CONNECT_BONE) and bone.connect_bone_index != -1:
            if bone.connect_bone_index < len(bones):
                target_bone = bones[bone.connect_bone_index]
                return self._calculate_aim_orient(bone, target_bone)
        
        # デフォルトの向き（親ボーンと同じまたはワールド座標系）
        return self._calculate_default_orient(bone, bone_index, bones)

    def _calculate_pmd_joint_orient(self, bone, bone_index, bones, children_map):
        """PMDボーンのJointOrientを計算"""
        # tail_pos_bone_indexが有効な場合
        if bone.tail_pos_bone_index != -1 and bone.tail_pos_bone_index < len(bones):
            target_bone = bones[bone.tail_pos_bone_index]
            return self._calculate_aim_orient(bone, target_bone)
        
        # 子ボーンが存在する場合
        if bone_index in children_map:
            child_index = children_map[bone_index][0]  # 最初の子ボーンを使用
            return self._calculate_aim_orient(bone, bones[child_index])
        
        # デフォルトの向き
        return self._calculate_default_orient(bone, bone_index, bones)

    def _calculate_local_axis_orient(self, bone):
        """ローカル軸からJointOrientを計算"""
        try:
            # PMXのローカル軸からMayaの座標系に変換
            x_axis = om.MVector(bone.x_axis_direction[0], bone.x_axis_direction[1], -bone.x_axis_direction[2])
            z_axis = om.MVector(bone.z_axis_direction[0], bone.z_axis_direction[1], -bone.z_axis_direction[2])
            
            # 正規化（外積計算前に）
            x_axis.normalize()
            z_axis.normalize()
            
            # Y軸を外積で計算（Maya左手座標系に合わせて順序を調整）
            y_axis = x_axis ^ z_axis
            y_axis.normalize()
            
            # 正確な直交座標系を確保するためにZ軸を再計算
            z_axis = x_axis ^ y_axis
            z_axis.normalize()
            
            # 回転行列を作成
            matrix = self._create_rotation_matrix(x_axis, y_axis, z_axis)
            return self._matrix_to_euler(matrix)
            
        except Exception as e:
            print(f"ローカル軸の計算でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _calculate_axis_fixed_orient(self, bone):
        """軸固定からJointOrientを計算"""
        try:
            # 軸固定方向をX軸とする
            aim_vector = om.MVector(bone.axis_direction[0], bone.axis_direction[1], -bone.axis_direction[2])
            aim_vector.normalize()
            
            # Y軸を上方向に近づける
            up_vector = om.MVector(0.0, 1.0, 0.0)
            
            matrix = self._calculate_aim_matrix(aim_vector, up_vector)
            return self._matrix_to_euler(matrix)
            
        except Exception as e:
            print(f"軸固定の計算でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _calculate_simple_aim_orient(self, bone, target_bone):
        """簡素化された子ボーンへの方向計算"""
        try:
            # 座標系変換：MMD（右手系）→ Maya（左手系）
            bone_pos = [bone.position[0], bone.position[1], -bone.position[2]]
            target_pos = [target_bone.position[0], target_bone.position[1], -target_bone.position[2]]
            
            # 方向ベクトルを計算
            dx = target_pos[0] - bone_pos[0]
            dy = target_pos[1] - bone_pos[1]
            dz = target_pos[2] - bone_pos[2]
            
            # 距離が小さい場合は回転なし
            distance = math.sqrt(dx*dx + dy*dy + dz*dz)
            if distance < 1e-6:
                return [0.0, 0.0, 0.0]
            
            # 正規化
            dx /= distance
            dy /= distance
            dz /= distance
            
            # 簡単なオイラー角計算（XZY順）
            # Y回転：XZ平面での角度
            y_rot = math.atan2(dx, dz)
            
            # X回転：Y方向の角度
            x_rot = -math.asin(dy)
            
            # Z回転は一旦0とする（単純化のため）
            z_rot = 0.0
            
            # ラジアンから度に変換
            return [math.degrees(x_rot), math.degrees(y_rot), math.degrees(z_rot)]
            
        except Exception as e:
            print(f"簡素化エイム計算でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _calculate_simple_local_axis_orient(self, bone):
        """簡素化されたローカル軸計算"""
        try:
            if not hasattr(bone, 'x_axis_direction') or not hasattr(bone, 'z_axis_direction'):
                return [0.0, 0.0, 0.0]
            
            # 座標系変換：MMD → Maya
            x_dir = [bone.x_axis_direction[0], bone.x_axis_direction[1], -bone.x_axis_direction[2]]
            z_dir = [bone.z_axis_direction[0], bone.z_axis_direction[1], -bone.z_axis_direction[2]]
            
            # X軸からY回転を計算
            y_rot = math.atan2(x_dir[0], x_dir[2])
            
            # Z軸の情報から追加の回転を計算（簡素化）
            x_rot = math.atan2(-x_dir[1], math.sqrt(x_dir[0]*x_dir[0] + x_dir[2]*x_dir[2]))
            
            # Z回転は一旦0とする
            z_rot = 0.0
            
            return [math.degrees(x_rot), math.degrees(y_rot), math.degrees(z_rot)]
            
        except Exception as e:
            print(f"簡素化ローカル軸計算でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _calculate_default_orient(self, bone, bone_index, bones):
        """デフォルトの向きを計算"""
        # 親ボーンがある場合は親と同じ向きを継承
        if bone.parent_bone_index != -1 and bone.parent_bone_index < len(bones):
            # 親ボーンの向きを参考にするが、今回は簡単にワールド座標系を使用
            return [0.0, 0.0, 0.0]
        
        # ルートボーンの場合はワールド座標系
        return [0.0, 0.0, 0.0]

    def _create_children_map(self, bones):
        """親子関係のマッピングを作成"""
        children = {}
        for i, bone in enumerate(bones):
            if bone.parent_bone_index != -1:
                if bone.parent_bone_index not in children:
                    children[bone.parent_bone_index] = []
                children[bone.parent_bone_index].append(i)
        return children

    def _set_joint_orient(self, joint, orient):
        """ジョイントの向きを設定"""
        try:
            cmds.setAttr(f"{joint}.jointOrientX", orient[0])
            cmds.setAttr(f"{joint}.jointOrientY", orient[1])
            cmds.setAttr(f"{joint}.jointOrientZ", orient[2])
        except Exception as e:
            print(f"ジョイントの向き設定でエラー {joint}: {e}")

    def _matrix_to_euler(self, matrix):
        """3x3回転行列をオイラー角（度）に変換"""
        try:
            # Maya Python API2.0を使用してMEulerRotationオブジェクトを取得
            transform_matrix = om.MTransformationMatrix(matrix)
            euler_rotation = transform_matrix.rotation()  # MEulerRotationオブジェクト
            
            # ラジアンから度に変換（XYZ順）
            return [math.degrees(euler_rotation.x), 
                    math.degrees(euler_rotation.y), 
                    math.degrees(euler_rotation.z)]
            
        except Exception as e:
            print(f"行列からオイラー角の変換でエラー: {e}")
            return [0.0, 0.0, 0.0]

    def _create_rotation_matrix(self, x_axis, y_axis, z_axis):
        """3つの軸ベクトルから回転行列を作成"""
        try:
            # OpenMayaのMMatrixを使用
            matrix_list = [
                x_axis.x, x_axis.y, x_axis.z, 0.0,
                y_axis.x, y_axis.y, y_axis.z, 0.0,
                z_axis.x, z_axis.y, z_axis.z, 0.0,
                0.0, 0.0, 0.0, 1.0
            ]
            return om.MMatrix(matrix_list)
            
        except Exception as e:
            print(f"回転行列の作成でエラー: {e}")
            return om.MMatrix()

    def _calculate_aim_matrix(self, aim_vector, up_vector):
        """エイムベクトルとアップベクトルから回転行列を計算"""
        try:
            # Mayaの標準：X軸を子ボーンへの方向に設定
            x_axis = aim_vector
            x_axis.normalize()
            
            # Z軸を外積で計算（右手系→左手系変換考慮）
            z_axis = up_vector ^ x_axis  # Maya左手座標系に合わせて順序変更
            if z_axis.length() < 1e-6:
                # エイムベクトルとアップベクトルが平行な場合の対処
                if abs(aim_vector.y) < 0.9:
                    up_vector = om.MVector(0.0, 1.0, 0.0)
                else:
                    up_vector = om.MVector(1.0, 0.0, 0.0)
                z_axis = up_vector ^ x_axis
            z_axis.normalize()
            
            # Y軸を外積で計算（正確な直交座標系のため）
            y_axis = z_axis ^ x_axis
            y_axis.normalize()
            
            return self._create_rotation_matrix(x_axis, y_axis, z_axis)
            
        except Exception as e:
            print(f"エイム行列の計算でエラー: {e}")
            return om.MMatrix()

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
