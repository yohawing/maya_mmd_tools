from typing import List, Tuple, Dict

import maya.cmds as cmds

from mmd_tools.core.pmx_data.bone import PmxBoneFlag

from ..core import maya_utils
from ..core.pmd_parser import PmdParser
from ..core.pmx_parser import PmxParser
from ..core.constants import SKELETON_GROUP
from validation.bone_validator import BoneValidator




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
        self.bone_validator = BoneValidator()

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
        
        # ボーン構造の検証を実行
        self.print_validation_report(pmx_data.bones)
        
        # IKチェーンを抽出してMayaのIKハンドルを作成
        ik_chains = self._extract_ik_chains(pmx_data.bones, bone_map)
        if ik_chains:
            print(f"\n{len(ik_chains)}個のIKチェーンを検出しました")
            ik_handles = self._create_maya_ik_handles(ik_chains)
            print(f"{len(ik_handles)}個のIKハンドルを作成しました")
        
        # ボーンのローカル軸を設定
        self._apply_bone_local_axes(pmx_data.bones, maya_joints)
        
        # 準標準ボーンを追加
        semi_standard_bones = self._add_semi_standard_bones(maya_joints, bone_map, skeleton_group)
        if semi_standard_bones:
            print(f"\n{len(semi_standard_bones)}個の準標準ボーンを追加しました")
        
        # 付与ボーンの設定
        given_constraints = self._setup_given_parent_bones(pmx_data.bones, maya_joints)
        if given_constraints:
            print(f"\n{len(given_constraints)}個の付与関係を設定しました")

        # TODO: 変形階層、表示操作などを正確に再現する。

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
        
        # ボーン構造の検証を実行
        self.print_validation_report(pmd_data.bones)
        
        # IKチェーンを抽出してMayaのIKハンドルを作成
        ik_chains = self._extract_ik_chains(pmd_data.bones, bone_map, pmd_data.ik_data)
        if ik_chains:
            print(f"\n{len(ik_chains)}個のIKチェーンを検出しました")
            ik_handles = self._create_maya_ik_handles(ik_chains)
            print(f"{len(ik_handles)}個のIKハンドルを作成しました")
        
        # 準標準ボーンを追加（PMDではローカル軸情報がないためスキップ）
        semi_standard_bones = self._add_semi_standard_bones(maya_joints, bone_map, skeleton_group)
        if semi_standard_bones:
            print(f"\n{len(semi_standard_bones)}個の準標準ボーンを追加しました")

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
    
    def validate_bones(self, bones) -> Dict[str, any]:
        """
        ボーン構造の検証を実行する。
        
        Args:
            bones: 検証対象のボーンデータリスト（PMDまたはPMXのボーンオブジェクト）
            
        Returns:
            dict: 検証結果を含む辞書
                - missing_bones: 不足している標準ボーンのリスト
                - naming_issues: 命名規則の問題リスト
                - bone_mapping: ボーン名の標準名へのマッピング
                - hierarchy_issues: 階層構造の問題
                - report: 検証レポートの文字列
        """
        # ボーン名のリストを抽出
        bone_names = [bone.get_name() for bone in bones]
        
        # ボーン名の検証を実行
        missing_bones, naming_issues, bone_mapping = self.bone_validator.validate_bones(bone_names)
        
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
            "standard_bones_found": len(bone_mapping)
        }
    
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
            if hasattr(bone, 'bone_flag') and hasattr(bone, 'get_flag'):
                if bone.get_flag(PmxBoneFlag.IK):
                    ik_chain = {
                        'ik_bone': bone_map[i],
                        'ik_bone_index': i,
                        'target_bone': bone_map.get(bone.ik_target_bone_index),
                        'target_bone_index': bone.ik_target_bone_index,
                        'loop_count': bone.ik_loop_count,
                        'unit_angle': bone.ik_limit_angle,  # ラジアン単位
                        'ik_links': []
                    }
                    
                    # IKリンクの処理
                    if hasattr(bone, 'ik_links'):
                        for link in bone.ik_links:
                            link_info = {
                                'bone': bone_map.get(link.ik_bone_index),
                                'bone_index': link.ik_bone_index,
                                'angle_limit': link.angle_limit if hasattr(link, 'angle_limit') else False,
                                'limit_min': link.limit_min if hasattr(link, 'limit_min') else None,
                                'limit_max': link.limit_max if hasattr(link, 'limit_max') else None
                            }
                            ik_chain['ik_links'].append(link_info)
                    
                    ik_chains.append(ik_chain)
            
            # PMDボーンの場合（IKボーンはbone_typeで判定）
            elif hasattr(bone, 'bone_type'):
                # PMDではIKボーンの判定方法が異なるため、後で実装を追加
                pass
        
        # PMDの場合、別途IKデータを処理
        if ik_data:
            for ik in ik_data:
                if ik.ik_bone_index < len(bone_map) and ik.target_bone_index < len(bone_map):
                    ik_chain = {
                        'ik_bone': bone_map.get(ik.ik_bone_index),
                        'ik_bone_index': ik.ik_bone_index,
                        'target_bone': bone_map.get(ik.target_bone_index),
                        'target_bone_index': ik.target_bone_index,
                        'loop_count': ik.iterations,
                        'unit_angle': ik.control_weight,  # PMDではcontrol_weightを使用
                        'ik_links': []
                    }
                    
                    # IKリンクの処理
                    for link_bone_index in ik.link_bones:
                        if link_bone_index < len(bone_map):
                            link_info = {
                                'bone': bone_map.get(link_bone_index),
                                'bone_index': link_bone_index,
                                'angle_limit': False,  # PMDは角度制限情報を持たない
                                'limit_min': None,
                                'limit_max': None
                            }
                            ik_chain['ik_links'].append(link_info)
                    
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
            if not chain['ik_links'] or not chain['target_bone']:
                print(f"警告: IKチェーン '{chain['ik_bone']}' にリンクまたはターゲットがありません")
                continue
            
            # IKリンクの最後（開始ジョイント）から最初（終了ジョイント）の順序
            start_joint = chain['ik_links'][-1]['bone'] if chain['ik_links'] else chain['target_bone']
            end_joint = chain['target_bone']
            
            if not start_joint or not end_joint:
                print(f"警告: IKチェーン '{chain['ik_bone']}' の開始または終了ジョイントが見つかりません")
                continue
            
            try:
                # ikHandleを作成
                ik_handle, _ = maya_utils.create_ik_handle(
                    start_joint=start_joint,
                    end_joint=end_joint,
                    solver='ikRPsolver',  # MMDは通常RPソルバーを使用
                    name=f"{chain['ik_bone']}_ikHandle"
                )
                
                # IKハンドルをIKボーンにペアレント
                cmds.parent(ik_handle, chain['ik_bone'])
                
                # IKハンドルのアトリビュートを設定
                cmds.setAttr(f"{ik_handle}.v", 0)  # 非表示
                
                # カスタムアトリビュートでMMDのIK情報を保存
                maya_utils.set_custom_attributes(ik_handle, {
                    "mmd_ik_loop_count": chain['loop_count'],
                    "mmd_ik_unit_angle": chain['unit_angle']
                })
                
                # 角度制限の設定
                self._set_joint_limits(chain['ik_links'])
                
                ik_handle_info = {
                    'ik_handle': ik_handle,
                    'ik_bone': chain['ik_bone'],
                    'start_joint': start_joint,
                    'end_joint': end_joint,
                    'ik_links': chain['ik_links']
                }
                
                ik_handles.append(ik_handle_info)
                print(f"IKハンドル '{ik_handle}' を作成しました（{start_joint} → {end_joint}）")
                
            except Exception as e:
                print(f"エラー: IKハンドルの作成に失敗しました '{chain['ik_bone']}': {e}")
        
        return ik_handles
    
    def _set_joint_limits(self, ik_links):
        """
        IKリンクのジョイントに角度制限を設定する。
        
        Args:
            ik_links (list): IKリンク情報のリスト
        """
        for link in ik_links:
            if not link['bone']:
                continue
                
            if link['angle_limit'] and link['limit_min'] and link['limit_max']:
                joint = link['bone']
                
                # MMDの角度制限は度数法、Mayaはラジアン
                # limit_min/maxは既にラジアンで保存されている
                maya_utils.set_joint_limits(
                    joint=joint,
                    limit_min=link['limit_min'],
                    limit_max=link['limit_max'],
                    enable_limits=True
                )
    
    def print_validation_report(self, bones):
        """
        ボーン検証レポートをコンソールに出力する。
        
        Args:
            bones: 検証対象のボーンデータリスト
        """
        validation_result = self.validate_bones(bones)
        print(validation_result["report"])
        
        # 警告が必要な場合はMayaの警告として表示
        if validation_result["missing_bones"]:
            cmds.warning(f"標準ボーンが{len(validation_result['missing_bones'])}個不足しています。詳細はスクリプトエディタを確認してください。")
    
    def _set_bone_local_axis(self, joint, bone):
        """
        PMXボーンのローカル軸情報をMayaジョイントに適用する。
        
        Args:
            joint (str): Mayaジョイント名
            bone: PMXボーンオブジェクト
        """
        if hasattr(bone, 'get_flag') and bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
            x_axis = bone.x_axis_direction
            z_axis = bone.z_axis_direction
            
            # Y軸を外積で計算
            y_axis = maya_utils.cross_product(z_axis, x_axis)
            
            # ジョイントオリエンテーションの設定
            matrix = maya_utils.create_matrix_from_axes(x_axis, y_axis, z_axis)
            rotation = maya_utils.matrix_to_euler(matrix)
            
            cmds.setAttr(f"{joint}.jointOrientX", rotation[0])
            cmds.setAttr(f"{joint}.jointOrientY", rotation[1])
            cmds.setAttr(f"{joint}.jointOrientZ", rotation[2])
            
            print(f"ローカル軸を設定: {joint}")
    
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
        parent_of_all = cmds.group(empty=True, name="parent_of_all", parent=skeleton_group)
        semi_standard_bones["parent_of_all"] = parent_of_all
        
        # スケルトングループ直下のルートジョイントを全ての親の子にする
        for child in cmds.listRelatives(skeleton_group, children=True, type="joint") or []:
            # 親を持たないルートジョイントのみを移動
            parent = cmds.listRelatives(child, parent=True)
            if parent and parent[0] == skeleton_group:
                cmds.parent(child, parent_of_all)
        
        # グルーブ
        center_joint = self._find_joint_by_name(
            maya_joints, ["center", "センター", "centre"]
        )
        if center_joint:
            # センターの位置を取得
            center_pos = cmds.xform(center_joint, query=True, worldSpace=True, translation=True)
            
            # グルーブを作成
            groove = cmds.group(empty=True, name="groove", parent=parent_of_all)
            cmds.xform(groove, worldSpace=True, translation=center_pos)
            semi_standard_bones["groove"] = groove
            
            # センターをグルーブの子にする
            cmds.parent(center_joint, groove)
            print(f"グルーブボーンを追加: {groove}")
        
        # 腰ボーン（下半身と足の間）
        lower_body_joint = self._find_joint_by_name(
            maya_joints, ["lower_body", "下半身", "lowerbody"]
        )
        left_leg_joint = self._find_joint_by_name(
            maya_joints, ["left_leg", "左足", "leftleg", "left_thigh", "左もも"]
        )
        
        if lower_body_joint and left_leg_joint:
            # 下半身と左足の中間位置を計算
            lower_body_pos = cmds.xform(lower_body_joint, query=True, worldSpace=True, translation=True)
            left_leg_pos = cmds.xform(left_leg_joint, query=True, worldSpace=True, translation=True)
            
            waist_pos = [
                (lower_body_pos[0] + left_leg_pos[0]) / 2,
                (lower_body_pos[1] + left_leg_pos[1]) / 2,
                (lower_body_pos[2] + left_leg_pos[2]) / 2
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
            
            print(f"腰ボーンを追加: {waist}")
        
        return semi_standard_bones
    
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
            if not hasattr(bone, 'get_flag'):
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
                            parent_joint, joint,
                            maintainOffset=True,
                            weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合は、エクスプレッションで制御
                        constraint = self._create_partial_rotation_constraint(
                            parent_joint, joint, given_rate
                        )
                    
                    constraints.append(constraint)
                    print(f"回転付与を設定: {joint} <- {parent_joint} (rate={given_rate})")
            
            # 移動付与
            if bone.get_flag(PmxBoneFlag.GIVEN_PARENT_MOVE):
                parent_index = bone.given_parent_bone_index
                if 0 <= parent_index < len(maya_joints):
                    parent_joint = maya_joints[parent_index]
                    given_rate = bone.given_rate
                    
                    # 付与率が1.0の場合は通常のpointConstraint
                    if abs(given_rate - 1.0) < 0.001:
                        constraint = cmds.pointConstraint(
                            parent_joint, joint,
                            maintainOffset=True,
                            weight=1.0
                        )[0]
                    else:
                        # 付与率が1.0でない場合は、エクスプレッションで制御
                        constraint = self._create_partial_position_constraint(
                            parent_joint, joint, given_rate
                        )
                    
                    constraints.append(constraint)
                    print(f"移動付与を設定: {joint} <- {parent_joint} (rate={given_rate})")
        
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
