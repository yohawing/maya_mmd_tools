import time
from typing import List, Optional, Tuple, Union

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

from mmd_tools.core.pmx_data.bone import PmxBoneFlag

from ..core import maya_mesh_utils, maya_utils
from ..core.mmd_bone_names import has_semistandard_mmd_bone_name
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
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    # PMD固有
    ATTR_MMD_BONE_TYPE,
    ATTR_MMD_TAIL_POS_INDEX,
)
from ..core.coordinate_transform import mmd_point_to_maya
from types import SimpleNamespace

from ..core.logger import get_logger, safe_log_error
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
        self.profile = {}

    def _add_profile_time(self, key: str, start: float) -> None:
        """Accumulate timing in the converter profile."""
        self.profile[key] = round(float(self.profile.get(key, 0.0)) + time.perf_counter() - start, 6)

    def convert_pmx_bones(
        self,
        pmx_data: PmxData,
        mesh_node: Union[str, List[str]],
        root_group,
        setup_rig=True,
        setup_bone_orientation=True,
        pmx_filepath: str = None,
        scale: float = 1.0,
    ):
        """
        PMXのボーンデータをMayaのジョイントに変換し、メッシュにスキニングを設定する。

        Args:
            pmx_data (PmxParser): 解析されたPMXデータオブジェクト。
            mesh_node (str or list): スキニングを適用するMayaのメッシュノード名、またはそのリスト。
            root_group (str): ルートグループの名前。
            setup_rig (bool): Trueの場合、PMXの付与/IKなどのMayaリグを構築する。
            setup_bone_orientation (bool): Trueの場合、PMXローカル軸/軸固定をjointOrient等へ反映する。
            scale (float): PMX座標からMaya座標へ変換する際のインポートスケール。

        Returns:
            tuple: (作成されたMayaジョイントノードの名前のリスト,
                   スキンクラスターの名前、またはスキンクラスター名のリスト)
        """
        # PMXのボーン階層をMayaのjointノードに変換する
        maya_utils.select_objects(clear=True)

        # スケルトングループを作成
        skeleton_group = cmds.group(empty=True, name=SKELETON_GROUP, parent=root_group)

        # ボーン名とインデックスのマッピングを作成
        bone_map = self._create_bone_mapping(pmx_data.bones)

        # Mayaジョイントを作成 (1回だけ)
        maya_joints = self._create_maya_joints(
            pmx_data.bones,
            bone_map,
            "pmx",
            skeleton_group,
            setup_bone_orientation=setup_bone_orientation,
            scale=scale,
        )

        # skinCluster をリグセットアップの前に作成する。
        # rig ノード（mmdCcdIk/mmdAppend）接続後は joint.rotate が駆動され
        # bind pose に R≠0 が含まれてしまうため、R=0 の状態でバインドする。
        mesh_nodes = [mesh_node] if isinstance(mesh_node, str) else (mesh_node or [])
        skin_clusters = []

        for mn in mesh_nodes:
            if not mn or not cmds.objExists(mn):
                continue
            sc = self._create_skin_cluster(maya_joints, mn, max_influence=4)
            if sc:
                self._apply_pmx_vertex_weights(pmx_data, maya_joints, sc, mn)
                skin_clusters.append(sc)

        # リグのセットアップはRigConverterに委譲。
        # runtime bake のように最終姿勢を直接焼く用途では、Maya側リグを作らないことで二重評価を避ける。
        if setup_rig:
            rig_result = self.rig_converter.setup_pmx_rig(
                pmx_data, maya_joints, bone_map, skeleton_group,
                pmx_filepath=pmx_filepath,
            )
            self.profile["rig_converter"] = {
                "used_native_rig": rig_result.get("native_rig") is not None,
                "constraint_count": len(rig_result.get("constraints") or []),
                "warnings": list(rig_result.get("warnings") or []),
            }

        result_cluster = skin_clusters[0] if len(skin_clusters) == 1 else skin_clusters

        return maya_joints, result_cluster

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
            joint_name = maya_utils.sanitize_bone_name(self._bone_node_name_source(bone))

            # 重複する名前がある場合はサフィックスを追加
            original_name = joint_name
            counter = 1
            while joint_name in used_names:
                joint_name = f"{original_name}_{counter}"
                counter += 1

            used_names.add(joint_name)
            bone_map[i] = joint_name

        return bone_map

    def _bone_node_name_source(self, bone) -> str:
        """Return the source PMX/PMD name to use for the Maya joint node name."""
        native_name = str(getattr(bone, "name", "") or "")
        if native_name and has_semistandard_mmd_bone_name(native_name):
            return native_name
        return bone.get_name()

    def _get_active_selection_uuid(self) -> Optional[str]:
        """Return the first active selection UUID without parsing its Maya name."""
        selection = om.MGlobal.getActiveSelectionList()
        if selection.length() == 0:
            return None
        node_obj = selection.getDependNode(0)
        return om.MFnDependencyNode(node_obj).uuid().asString()

    def _get_node_uuid(self, node: str, allow_active_selection_fallback: bool = False) -> Optional[str]:
        """Return the Maya UUID for a node when available.

        Some real-world PMX bone names can produce Maya node names that make
        ``cmds.ls(node, uuid=True)`` fail in Maya's name parser.  Immediately
        after ``cmds.joint`` creation the new joint is selected, so the importer
        can safely recover its UUID through the API selection list without
        reparsing the problematic string.
        """
        try:
            uuids = cmds.ls(node, uuid=True) or []
            if uuids:
                return uuids[0]
        except RuntimeError as exc:
            if not allow_active_selection_fallback:
                raise
            self.logger.debug("cmds.ls(uuid=True) failed for node %r; using active selection UUID: %s", node, exc)

        if allow_active_selection_fallback:
            return self._get_active_selection_uuid()
        return None

    def _resolve_node_long_path(self, node_uuid: Optional[str], fallback: Optional[str] = None) -> str:
        """Resolve a node UUID to the current long DAG path.

        Maya DAG paths change when an ancestor is reparented. UUID lookup keeps
        the converter from using stale path strings after hierarchy edits.
        """
        if node_uuid:
            matches = cmds.ls(node_uuid, long=True) or []
            if matches:
                return matches[0]

        if fallback and cmds.objExists(fallback):
            matches = cmds.ls(fallback, long=True) or []
            if matches:
                return matches[0]
            return fallback

        raise RuntimeError(f"Unable to resolve Maya node path for uuid={node_uuid!r}, fallback={fallback!r}")

    def _refresh_joint_paths(self, maya_joints: List[str], joint_uuids: List[Optional[str]]) -> None:
        """Refresh all stored joint paths from UUIDs after DAG parenting."""
        for i, joint_uuid in enumerate(joint_uuids):
            maya_joints[i] = self._resolve_node_long_path(joint_uuid, maya_joints[i])

    def _parent_first_bone_indices(self, bones) -> List[int]:
        """Return bone indices with parents before children."""
        order = []
        visiting = set()
        visited = set()
        bone_count = len(bones)

        def _valid_parent_index(index):
            parent_index = getattr(bones[index], "parent_bone_index", -1)
            if isinstance(parent_index, int) and 0 <= parent_index < bone_count and parent_index != index:
                return parent_index
            return -1

        def _visit(index):
            if index in visited:
                return
            if index in visiting:
                self.logger.warning("Cycle detected in bone hierarchy at index %s; using best-effort order", index)
                return
            visiting.add(index)
            parent_index = _valid_parent_index(index)
            if parent_index != -1:
                _visit(parent_index)
            visiting.remove(index)
            visited.add(index)
            order.append(index)

        for index in range(bone_count):
            _visit(index)

        return order

    def _create_maya_joints(
        self,
        bones,
        bone_map,
        format_type,
        skeleton_group,
        setup_bone_orientation=True,
        scale: float = 1.0,
    ):
        """
        Mayaジョイントを作成する。

        Args:
            bones: ボーンデータのリスト。
            bone_map (dict): ボーン名のマッピング。
            format_type (str): フォーマットタイプ（'pmx' または 'pmd'）。
            skeleton_group (str): スケルトングループの名前。
            setup_bone_orientation (bool): PMXローカル軸/軸固定をMaya jointへ反映するか。
            scale (float): PMX座標からMaya座標へ変換する際のインポートスケール。

        Returns:
            list: 作成されたMayaジョイントノードの名前のリスト。
        """
        maya_joints = []
        joint_uuids = []

        # まず、すべてのジョイントを親なしで作成
        for i, bone in enumerate(bones):
            joint_create_start = time.perf_counter()
            # bone_mapから既にユニークな名前を取得
            joint_name = bone_map[i]

            # 選択をクリアしてルートにジョイントを作成
            maya_utils.select_objects(clear=True)

            # ジョイントを作成
            position = bone.position
            joint = cmds.joint(
                name=joint_name,
                position=list(mmd_point_to_maya(position, scale)),  # MMD: +Z手前, Maya: +Z奥
            )
            joint_uuid = self._get_node_uuid(joint, allow_active_selection_fallback=True)
            joint = self._resolve_node_long_path(joint_uuid, joint)

            # セグメントスケール補償を無効化
            maya_utils.set_attribute(joint, "segmentScaleCompensate", False, "bool")
            self._add_profile_time("joint_create_sec", joint_create_start)

            joint_attr_start = time.perf_counter()
            self._set_extra_attributes(i, joint, bone, format_type)
            self._add_profile_time("joint_attr_sec", joint_attr_start)
            maya_joints.append(joint)
            joint_uuids.append(joint_uuid)

        # 次に、親子関係を設定
        for i in self._parent_first_bone_indices(bones):
            bone = bones[i]
            if 0 <= bone.parent_bone_index < len(bones):
                child_joint = maya_joints[i]
                parent_joint = maya_joints[bone.parent_bone_index]

                try:
                    # 親子関係を設定
                    parent_result = cmds.parent(child_joint, parent_joint, absolute=True) or []
                    maya_joints[i] = self._resolve_node_long_path(
                        joint_uuids[i],
                        parent_result[0] if parent_result else child_joint,
                    )
                    self._refresh_joint_paths(maya_joints, joint_uuids)
                except Exception as e:
                    safe_log_error(
                        self.logger,
                        "Failed to parent",
                        SimpleNamespace(name=f"{child_joint} to {parent_joint}"),
                        e,
                    )

        # ルートジョイントをスケルトングループにペアレント
        # 親を持たないジョイントを探す
        root_joints = []
        for i, bone in enumerate(bones):
            if bone.parent_bone_index == -1:
                # 実際に作成されたジョイント名を使用
                root_joints.append((i, maya_joints[i]))

        self.logger.debug(f"Root joints to parent: {root_joints}")
        self.logger.debug(f"Skeleton group: {skeleton_group}")

        # ルートジョイントをスケルトングループにペアレント
        # absolute=Trueを使用してワールド座標を維持
        for i, root_joint in root_joints:
            current_root_joint = self._resolve_node_long_path(joint_uuids[i], root_joint)
            if cmds.objExists(current_root_joint):
                parent_result = cmds.parent(current_root_joint, skeleton_group, absolute=True) or []
                maya_joints[i] = self._resolve_node_long_path(
                    joint_uuids[i],
                    parent_result[0] if parent_result else current_root_joint,
                )
                self._refresh_joint_paths(maya_joints, joint_uuids)
            else:
                self.logger.error(f"Root joint '{current_root_joint}' does not exist in scene")

        self._refresh_joint_paths(maya_joints, joint_uuids)

        # parenting 完了後に LOCAL_AXIS ボーンの JO を設定し translate を補正する。
        if setup_bone_orientation:
            self._apply_joint_orient_all(maya_joints, bones, format_type)

        return maya_joints

    def _set_extra_attributes(self, i, joint, bone, format_type):
        # フォーマットに応じたカスタム属性を設定
        if format_type == "pmx":
            attrs = {
                ATTR_MMD_BONE_NAME: bone.name,
                ATTR_MMD_BONE_NAME_EN: bone.name_english,
                ATTR_MMD_BONE_FLAGS: bone.bone_flag,
                ATTR_MMD_DEFORM_LAYER: bone.deform_layer if hasattr(bone, "deform_layer") else 0,
                ATTR_MMD_BONE_INDEX: i,
                ATTR_MMD_BONE_PARENT_INDEX: bone.parent_bone_index,
            }

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

        elif format_type == "pmd":
            attrs = {
                ATTR_MMD_BONE_NAME: bone.name,
                ATTR_MMD_BONE_NAME_EN: bone.name_english,
                # PMDはフラグを持たないのでデフォルト値を設定
                ATTR_MMD_BONE_FLAGS: 0x0005,  # 回転可能 + 表示
                ATTR_MMD_DEFORM_LAYER: 0,
                ATTR_MMD_BONE_INDEX: i,
                ATTR_MMD_BONE_TYPE: bone.bone_type.name,  # Enumの名前（文字列）を取得
                ATTR_MMD_TAIL_POS_INDEX: bone.tail_pos_bone_index,
                ATTR_MMD_BONE_PARENT_INDEX: bone.parent_bone_index,
            }
        else:
            return

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
            self.logger.warning(f"Mesh node '{mesh_node}' does not exist. Skin cluster will not be created.")
            return None

        # skin_cluster = skin_cluster_result[0] if skin_cluster_result else None
        skin_cluster_create_start = time.perf_counter()
        skin_cluster = cmds.skinCluster(
            maya_joints,
            mesh_node,
            toSelectedBones=True,
            normalizeWeights=2,
            maximumInfluences=max_influence,  # PMXは最大4つのボーンに制限されているため
            name="skinCluster",
        )[0]
        self._add_profile_time("skin_cluster_create_sec", skin_cluster_create_start)

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

    def _apply_flat_vertex_weights(self, skin_cluster, mesh_node, flat_weights, influence_count):
        """Apply already-packed vertex weights directly through MFnSkinCluster."""
        selection_list = om.MSelectionList()
        selection_list.add(skin_cluster)
        skin_cluster_obj = selection_list.getDependNode(0)
        skin_fn = oma.MFnSkinCluster(skin_cluster_obj)

        influence_paths = skin_fn.influenceObjects()
        actual_influence_count = len(influence_paths)
        if actual_influence_count != influence_count:
            raise ValueError(
                "SkinCluster influence count mismatch: "
                f"packed={influence_count}, actual={actual_influence_count}"
            )

        mesh_selection_list = om.MSelectionList()
        mesh_selection_list.add(mesh_node)
        shape_dag_path = mesh_selection_list.getDagPath(0)
        mesh_fn = om.MFnMesh(shape_dag_path)
        vertex_count = mesh_fn.numVertices
        expected_weight_count = vertex_count * influence_count
        if len(flat_weights) != expected_weight_count:
            raise ValueError(
                "Flat skin weight count mismatch: "
                f"packed={len(flat_weights)}, expected={expected_weight_count}"
            )

        vertex_component = om.MFnSingleIndexedComponent()
        vertex_component_obj = vertex_component.create(om.MFn.kMeshVertComponent)
        vertex_component.addElements(list(range(vertex_count)))

        influence_indices = om.MIntArray(influence_count, 0)
        for influence_index in range(influence_count):
            influence_indices[influence_index] = influence_index

        skin_fn.setWeights(
            shape_dag_path,
            vertex_component_obj,
            influence_indices,
            om.MDoubleArray(flat_weights),
            False,
        )

    def _apply_pmx_vertex_weights(self, pmx_data, maya_joints, skin_cluster, mesh_node):
        """
        PMX頂点ウェイトをスキンクラスターに適用する。

        Args:
            pmx_data: PMXデータオブジェクト。
            maya_joints (list): Mayaジョイントノードの名前のリスト。
            skin_cluster (str): スキンクラスターの名前。
            mesh_node (str): メッシュノードの名前。
        """
        weight_pack_start = time.perf_counter()
        joint_count = len(maya_joints)
        flat_weights = []
        flat_extend = flat_weights.extend
        zero_row = [0.0] * joint_count
        source_vertex_indices = self._get_mesh_source_vertex_indices(mesh_node)
        vertices = pmx_data.vertices
        vertex_indices = source_vertex_indices if source_vertex_indices is not None else range(len(vertices))

        for source_vertex_index in vertex_indices:
            if source_vertex_index < 0 or source_vertex_index >= len(vertices):
                flat_extend(zero_row)
                continue

            row_start = len(flat_weights)
            flat_extend(zero_row)
            vertex = vertices[source_vertex_index]
            # PMX頂点の重み情報を取得
            weight_maps = self._get_pmx_vertex_weights(vertex)

            for joint_index, weight in weight_maps:
                # ボーンインデックスの境界チェック
                if joint_index < 0 or joint_index >= joint_count:
                    self.logger.warning(f"Invalid bone index {joint_index}, max={joint_count - 1}")
                    continue
                flat_weights[row_start + joint_index] = weight
        self._add_profile_time("weight_pack_sec", weight_pack_start)

        set_weights_start = time.perf_counter()
        try:
            self._apply_flat_vertex_weights(skin_cluster, mesh_node, flat_weights, joint_count)
        except Exception as exc:
            self.logger.warning(
                "Direct skin weight application failed for '%s'; falling back to apply_vertex_weights(): %s",
                mesh_node,
                exc,
            )
            weights = [
                flat_weights[row_start : row_start + joint_count]
                for row_start in range(0, len(flat_weights), joint_count)
            ]
            maya_mesh_utils.apply_vertex_weights(skin_cluster, mesh_node, weights)
        self._add_profile_time("set_weights_sec", set_weights_start)

    def _get_mesh_source_vertex_indices(self, mesh_node):
        """compact material split mesh の元 PMX vertex index 配列を取得する。"""
        try:
            if not mesh_node or not cmds.objExists(mesh_node):
                return None
            if not cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh_node, exists=True):
                return None
            source_indices = maya_utils.get_int_array_attribute(mesh_node, ATTR_MMD_SOURCE_VERTEX_INDICES)
            return source_indices or None
        except Exception:
            return None

    def _compute_pmx_world_rotation_matrix(self, bone):
        """PMX LOCAL_AXIS の軸方向からワールド空間回転行列を計算する。"""
        x_axis = om.MVector(bone.x_axis_direction[0], bone.x_axis_direction[1], -bone.x_axis_direction[2])
        x_axis.normalize()
        z_axis = om.MVector(bone.z_axis_direction[0], bone.z_axis_direction[1], -bone.z_axis_direction[2])
        z_axis.normalize()
        y_axis = z_axis ^ x_axis
        y_axis.normalize()
        return om.MMatrix([
            [x_axis.x, x_axis.y, x_axis.z, 0],
            [y_axis.x, y_axis.y, y_axis.z, 0],
            [z_axis.x, z_axis.y, z_axis.z, 0],
            [0, 0, 0, 1],
        ])

    def _apply_joint_orient_all(self, maya_joints, bones, format_type):
        """LOCAL_AXIS ボーンに JO を設定し、全ボーンの translate を再計算する。

        parenting (cmds.parent absolute=True) 完了後に呼ぶ。
        トポロジカル順に処理し、Maya の実 worldMatrix を読み取って translate を計算
        するため、Python/Maya 間の Euler round-trip 誤差が蓄積しない。
        """
        if format_type != "pmx":
            return

        n = len(bones)

        def _extract_rotation_matrix(world_matrix):
            """4x4 worldMatrix から回転部分(3x3)だけの MMatrix を返す。"""
            return om.MMatrix([
                [world_matrix[0], world_matrix[1], world_matrix[2], 0],
                [world_matrix[4], world_matrix[5], world_matrix[6], 0],
                [world_matrix[8], world_matrix[9], world_matrix[10], 0],
                [0, 0, 0, 1],
            ])

        for i, bone in enumerate(bones):
            pidx = bone.parent_bone_index

            # --- JO 設定 (LOCAL_AXIS ボーンのみ) ---
            if bone.get_flag(PmxBoneFlag.LOCAL_AXIS):
                desired_world = self._compute_pmx_world_rotation_matrix(bone)
                if 0 <= pidx < n:
                    parent_wm = cmds.getAttr(f"{maya_joints[pidx]}.worldMatrix[0]")
                    parent_rot = _extract_rotation_matrix(parent_wm)
                else:
                    parent_rot = om.MMatrix()
                jo_mat = desired_world * parent_rot.inverse()
                jo_euler = om.MTransformationMatrix(jo_mat).rotation(asQuaternion=False)
                maya_utils.set_attribute(
                    maya_joints[i], "jointOrient",
                    (jo_euler.x, jo_euler.y, jo_euler.z), "double3",
                )

            # --- translate 再計算 (Maya 実 worldMatrix ベース) ---
            if 0 <= pidx < n:
                parent_wm_flat = cmds.getAttr(f"{maya_joints[pidx]}.worldMatrix[0]")
                parent_wm = om.MMatrix(parent_wm_flat)
                parent_wm_inv = parent_wm.inverse()
                my_pos = om.MPoint(
                    bone.position[0], bone.position[1], -bone.position[2],
                )
                local_pos = my_pos * parent_wm_inv
                maya_utils.set_attribute(
                    maya_joints[i], "translate",
                    (local_pos.x, local_pos.y, local_pos.z), "double3",
                )

            maya_utils.set_attribute(maya_joints[i], "rotate", (0, 0, 0), "double3")
