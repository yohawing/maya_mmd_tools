import unittest
from unittest.mock import Mock, patch
import maya.cmds as cmds
import maya.api.OpenMaya as om

from mmd_tools.converters.bone_converter import BoneConverter
from mmd_tools.core import maya_attribute_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
from mmd_tools.core.pmx_data.bone import PmxBone, PmxBoneFlag
from mmd_tools.core.pmd_data.bone import PmdBone


class TestBoneConverterMaya(unittest.TestCase):
    """BoneConverterクラスのMaya環境でのユニットテスト"""

    def setUp(self):
        """テストごとのセットアップ"""
        # 新しいシーンを作成
        cmds.file(new=True, force=True)
        self.converter = BoneConverter()

        # テスト用のメッシュを作成
        self.test_mesh = cmds.polyCube(name="test_mesh")[0]

        # テスト用のグループを作成
        self.root_group = cmds.group(empty=True, name="test_root")

    def tearDown(self):
        """テストごとのクリーンアップ"""
        # シーンをクリア
        cmds.file(new=True, force=True)

    def _create_mock_pmx_bone(self, index, name="TestBone", parent_index=-1, position=(0, 0, 0), bone_flag=0):
        """PMXボーンのモックを作成"""
        bone = Mock(spec=PmxBone)
        bone.name = name
        bone.name_english = f"{name}_en"
        bone.parent_bone_index = parent_index
        bone.position = position
        bone.bone_flag = bone_flag
        bone.get_name.return_value = name
        bone.get_flag = Mock(side_effect=lambda flag: bool(bone_flag & flag))

        # デフォルト値を設定
        bone.connect_bone_index = 0
        bone.connect_position_offset = (0, 0, 0)
        bone.grant_parent_bone_index = 0
        bone.grant_rate = 1.0
        bone.axis_direction = (0, 1, 0)
        bone.x_axis_direction = (1, 0, 0)
        bone.z_axis_direction = (0, 0, 1)
        bone.key_value = 0
        bone.ik_target_bone_index = 0
        bone.ik_loop_count = 0
        bone.ik_limit_angle = 0
        bone.ik_links = []

        return bone

    def _pmx_axis_to_maya_vector(self, axis):
        """PMX軸ベクトルをMaya座標系へ変換する。"""
        vector = om.MVector(axis[0], axis[1], -axis[2])
        vector.normalize()
        return vector

    def _joint_world_axis_rows(self, joint):
        """jointのworldMatrixからMaya row-vector規約のX/Y/Z軸を返す。"""
        matrix = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
        axes = [
            om.MVector(matrix[0], matrix[1], matrix[2]),
            om.MVector(matrix[4], matrix[5], matrix[6]),
            om.MVector(matrix[8], matrix[9], matrix[10]),
        ]
        for axis in axes:
            axis.normalize()
        return axes

    def _assert_vector_dot_greater(self, actual, expected, threshold=0.999):
        dot = actual * expected
        self.assertGreater(
            dot,
            threshold,
            f"axis mismatch: dot={dot:.6f}, actual={actual}, expected={expected}",
        )

    def _create_mock_pmd_bone(
        self,
        index,
        name="TestBone",
        parent_index=-1,
        position=(0, 0, 0),
        bone_type=Mock(name="BoneType"),
    ):
        """PMDボーンのモックを作成"""
        bone = Mock(spec=PmdBone)
        bone.name = name
        bone.name_english = f"{name}_en"
        bone.parent_bone_index = parent_index
        bone.position = position
        bone.bone_type = bone_type
        bone.tail_pos_bone_index = 0
        bone.get_name.return_value = name

        return bone

    def test_create_bone_mapping_simple(self):
        """シンプルなボーン名マッピングのテスト"""
        bones = [
            self._create_mock_pmx_bone(0, "センター"),
            self._create_mock_pmx_bone(1, "上半身"),
            self._create_mock_pmx_bone(2, "頭"),
        ]

        with patch("mmd_tools.core.maya_utils.sanitize_bone_name") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x  # そのまま返す

            bone_map = self.converter._create_bone_mapping(bones)

        self.assertEqual(len(bone_map), 3)
        self.assertEqual(bone_map[0], "センター")
        self.assertEqual(bone_map[1], "上半身")
        self.assertEqual(bone_map[2], "頭")

    def test_create_bone_mapping_duplicate_names(self):
        """重複する名前のボーンマッピングのテスト"""
        bones = [
            self._create_mock_pmx_bone(0, "ボーン"),
            self._create_mock_pmx_bone(1, "ボーン"),
            self._create_mock_pmx_bone(2, "ボーン"),
        ]

        with patch("mmd_tools.core.maya_utils.sanitize_bone_name") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x  # そのまま返す

            bone_map = self.converter._create_bone_mapping(bones)

        self.assertEqual(bone_map[0], "ボーン")
        self.assertEqual(bone_map[1], "ボーン_1")
        self.assertEqual(bone_map[2], "ボーン_2")

    def test_create_bone_mapping_prefers_hardcoded_semistandard_native_names(self):
        """PMX英名より準標準ボーンのハードコード日本語名変換を優先する。"""
        cases = [
            ("左腕D", "D", "left_arm_d"),
            ("右腕捩D", "D", "right_arm_twist_d"),
            ("左ひじD", "D", "left_elbow_d"),
            ("右足IK親", "leg IKP_R", "right_leg_ik_parent"),
            ("右足先EX", "toe2_R", "right_toe_ex"),
            ("右肩P", "shoulderP_R", "right_shoulder_p"),
        ]
        bones = []
        for index, (native_name, english_name, _expected) in enumerate(cases):
            bone = self._create_mock_pmx_bone(index, native_name)
            bone.name_english = english_name
            bone.get_name.return_value = english_name
            bones.append(bone)

        bone_map = self.converter._create_bone_mapping(bones)

        self.assertEqual(bone_map, {index: expected for index, (_native, _english, expected) in enumerate(cases)})

    @patch("mmd_tools.core.maya_utils.sanitize_bone_name")
    def test_create_maya_joints_hierarchy(self, mock_sanitize):
        """ジョイント階層作成のテスト（実際のMaya環境）"""
        mock_sanitize.side_effect = lambda x: x

        bones = [
            self._create_mock_pmx_bone(0, "center", parent_index=-1, position=(0, 0, 0)),
            self._create_mock_pmx_bone(1, "upper_body", parent_index=0, position=(0, 10, 0)),
            self._create_mock_pmx_bone(2, "head", parent_index=1, position=(0, 20, 0)),
        ]

        bone_map = {0: "center", 1: "upper_body", 2: "head"}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        maya_joints = self.converter._create_maya_joints(bones, bone_map, "pmx", skeleton_group)

        # ジョイント作成の確認
        self.assertEqual(len(maya_joints), 3)
        self.assertTrue(cmds.objExists(maya_joints[0]))
        self.assertTrue(cmds.objExists(maya_joints[1]))
        self.assertTrue(cmds.objExists(maya_joints[2]))

        # 親子関係の確認
        parent_of_upper = cmds.listRelatives(maya_joints[1], parent=True, fullPath=True)[0]
        self.assertEqual(parent_of_upper, maya_joints[0])

        parent_of_head = cmds.listRelatives(maya_joints[2], parent=True, fullPath=True)[0]
        self.assertEqual(parent_of_head, maya_joints[1])

        # 位置の確認（Mayaは左手系なのでZ座標が反転）
        center_pos = cmds.xform(maya_joints[0], query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(center_pos[0], 0, places=5)
        self.assertAlmostEqual(center_pos[1], 0, places=5)
        self.assertAlmostEqual(center_pos[2], 0, places=5)

        upper_pos = cmds.xform(maya_joints[1], query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(upper_pos[0], 0, places=5)
        self.assertAlmostEqual(upper_pos[1], 10, places=5)
        self.assertAlmostEqual(upper_pos[2], 0, places=5)

    @patch("mmd_tools.core.maya_utils.sanitize_bone_name")
    def test_create_maya_joints_refreshes_paths_after_name_collision(self, mock_sanitize):
        """既存同名jointがあるシーンでもreparent後のDAG pathを返す"""
        mock_sanitize.side_effect = lambda x: x

        cmds.select(clear=True)
        existing_center = cmds.joint(name="center", position=[100, 0, 0])
        self.assertEqual(cmds.ls(existing_center, long=True)[0], "|center")

        bones = [
            self._create_mock_pmx_bone(0, "center", parent_index=-1, position=(0, 0, 0)),
            self._create_mock_pmx_bone(1, "upper_body", parent_index=0, position=(0, 10, 0)),
            self._create_mock_pmx_bone(2, "head", parent_index=1, position=(0, 20, 0)),
        ]

        skeleton_group = cmds.group(empty=True, name="skeleton_collision_grp")
        maya_joints = self.converter._create_maya_joints(
            bones,
            {0: "center", 1: "upper_body", 2: "head"},
            "pmx",
            skeleton_group,
        )

        self.assertEqual(len(maya_joints), 3)
        for joint in maya_joints:
            self.assertTrue(cmds.objExists(joint), f"stale joint path returned: {joint}")
            self.assertTrue(joint.startswith("|skeleton_collision_grp|"))

        parent_of_upper = cmds.listRelatives(maya_joints[1], parent=True, fullPath=True)[0]
        parent_of_head = cmds.listRelatives(maya_joints[2], parent=True, fullPath=True)[0]
        self.assertEqual(parent_of_upper, maya_joints[0])
        self.assertEqual(parent_of_head, maya_joints[1])
        self.assertTrue(cmds.objExists("|center"), "pre-existing root joint should not be consumed")

    def test_get_node_uuid_falls_back_to_active_selection_when_name_parser_fails(self):
        """cmds.ls(uuid=True) が名前パースで落ちても作成直後 joint の UUID を取れる。"""
        cmds.select(clear=True)
        joint = cmds.joint(name="uuid_parser_fallback_joint", position=(0, 0, 0))
        expected_uuid = cmds.ls(joint, uuid=True)[0]

        with patch("mmd_tools.converters.bone_converter.cmds.ls", side_effect=RuntimeError("parser failed")):
            actual_uuid = self.converter._get_node_uuid(joint, allow_active_selection_fallback=True)

        self.assertEqual(actual_uuid, expected_uuid)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト"""
        bone = self._create_mock_pmx_bone(0, "TestBone", bone_flag=PmxBoneFlag.ROTATABLE | PmxBoneFlag.MOVABLE)

        # 実際のジョイントを作成
        joint = cmds.joint(name="test_joint")

        self.converter._set_extra_attributes(0, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 0)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "TestBone")
        self.assertTrue(attrs[ATTR_MMD_BONE_FLAGS], PmxBoneFlag.ROTATABLE | PmxBoneFlag.MOVABLE)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_grant_rotate(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（回転付与）"""
        bone = self._create_mock_pmx_bone(5, "GrantBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
        bone.grant_parent_bone_index = 3
        bone.grant_rate = 0.5

        # 実際のジョイントを作成
        joint = cmds.joint(name="grant_joint")

        self.converter._set_extra_attributes(5, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 5)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "GrantBone")
        self.assertEqual(attrs[ATTR_MMD_BONE_FLAGS], PmxBoneFlag.GRANT_PARENT_ROTATE)

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 0.5)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 3)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_grant_move(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（移動付与）"""
        bone = self._create_mock_pmx_bone(7, "grantMoveBone", bone_flag=PmxBoneFlag.GRANT_PARENT_MOVE)
        bone.grant_parent_bone_index = 2
        bone.grant_rate = 1.0

        # 実際のジョイントを作成
        joint = cmds.joint(name="grant_move_joint")

        self.converter._set_extra_attributes(7, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 7)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "grantMoveBone")

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 1.0)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 2)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_grant_both(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（回転＋移動付与）"""
        bone = self._create_mock_pmx_bone(
            9,
            "grantBothBone",
            bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE,
        )
        bone.grant_parent_bone_index = 4
        bone.grant_rate = 0.75

        # 実際のジョイントを作成
        joint = cmds.joint(name="grant_both_joint")

        self.converter._set_extra_attributes(9, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 9)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "grantBothBone")

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 0.75)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 4)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_ik(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（IKボーン）"""
        bone = self._create_mock_pmx_bone(10, "IKBone", bone_flag=PmxBoneFlag.IK)
        bone.ik_target_bone_index = 11
        bone.ik_loop_count = 20
        bone.ik_limit_angle = 2.0

        # IKリンクのモック
        ik_link1 = Mock()
        ik_link1.ik_bone_index = 12
        ik_link1.angle_limit = True
        ik_link1.limit_min = (-1.0, -1.0, -1.0)
        ik_link1.limit_max = (1.0, 1.0, 1.0)

        bone.ik_links = [ik_link1]

        # 実際のジョイントを作成
        joint = cmds.joint(name="ik_joint")

        self.converter._set_extra_attributes(10, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # IK関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_IK_LOOP,
            ATTR_MMD_IK_LIMIT_ANGLE,
            ATTR_MMD_IK_TARGET_INDEX,
        )

        self.assertIn(ATTR_MMD_IK_LOOP, attrs)
        self.assertEqual(attrs[ATTR_MMD_IK_LOOP], 20)
        self.assertIn(ATTR_MMD_IK_LIMIT_ANGLE, attrs)
        self.assertEqual(attrs[ATTR_MMD_IK_LIMIT_ANGLE], 2.0)
        self.assertIn(ATTR_MMD_IK_TARGET_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_IK_TARGET_INDEX], 11)

    @patch("mmd_tools.converters.bone_converter.maya_attribute_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_local_axis(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（ローカル軸）"""
        bone = self._create_mock_pmx_bone(15, "LocalAxisBone", bone_flag=PmxBoneFlag.LOCAL_AXIS)
        bone.x_axis_direction = (1.0, 0.0, 0.0)
        bone.z_axis_direction = (0.0, 0.0, 1.0)

        # 実際のジョイントを作成
        joint = cmds.joint(name="local_axis_joint")

        self.converter._set_extra_attributes(15, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # ローカル軸関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_LOCAL_X_AXIS,
            ATTR_MMD_LOCAL_Z_AXIS,
            ATTR_MMD_X_AXIS_DIRECTION,
            ATTR_MMD_Z_AXIS_DIRECTION,
        )

        self.assertIn(ATTR_MMD_LOCAL_X_AXIS, attrs)
        self.assertEqual(attrs[ATTR_MMD_LOCAL_X_AXIS], (1.0, 0.0, 0.0))
        self.assertIn(ATTR_MMD_LOCAL_Z_AXIS, attrs)
        self.assertEqual(attrs[ATTR_MMD_LOCAL_Z_AXIS], (0.0, 0.0, 1.0))
        self.assertIn(ATTR_MMD_X_AXIS_DIRECTION, attrs)
        self.assertEqual(attrs[ATTR_MMD_X_AXIS_DIRECTION], (1.0, 0.0, 0.0))
        self.assertIn(ATTR_MMD_Z_AXIS_DIRECTION, attrs)
        self.assertEqual(attrs[ATTR_MMD_Z_AXIS_DIRECTION], (0.0, 0.0, 1.0))

    def test_create_skin_cluster(self):
        """スキンクラスター作成のテスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        joint1 = cmds.joint(name="joint1", position=[0, 0, 0])
        joint2 = cmds.joint(name="joint2", position=[0, 5, 0])
        joint3 = cmds.joint(name="joint3", position=[0, 10, 0])
        maya_joints = [joint1, joint2, joint3]

        # スキンクラスターを作成
        skin_cluster = self.converter._create_skin_cluster(maya_joints, self.test_mesh, max_influence=4)

        # スキンクラスターが作成されたか確認
        self.assertTrue(cmds.objExists(skin_cluster))

        # スキンクラスターのタイプを確認
        node_type = cmds.nodeType(skin_cluster)
        self.assertEqual(node_type, "skinCluster")

        # 最大影響数の確認
        max_inf = cmds.getAttr(f"{skin_cluster}.maxInfluences")
        self.assertEqual(max_inf, 4)

    def test_get_pmx_vertex_weights_bdef1(self):
        """BDEF1ウェイトの取得テスト"""
        vertex = Mock()
        vertex.weight_transform_type = 0  # BDEF1
        vertex.bone_indices = [5, 0, 0, 0]

        weights = self.converter._get_pmx_vertex_weights(vertex)

        self.assertEqual(len(weights), 1)
        self.assertEqual(weights[0], (5, 1.0))

    def test_get_pmx_vertex_weights_bdef2(self):
        """BDEF2ウェイトの取得テスト"""
        vertex = Mock()
        vertex.weight_transform_type = 1  # BDEF2
        vertex.bone_indices = [3, 7, 0, 0]
        vertex.bone_weights = [0.7, 0, 0, 0]

        weights = self.converter._get_pmx_vertex_weights(vertex)

        self.assertEqual(len(weights), 2)
        self.assertEqual(weights[0], (3, 0.7))
        self.assertEqual(weights[1][0], 7)
        self.assertAlmostEqual(weights[1][1], 0.3, places=5)

    def test_get_pmx_vertex_weights_bdef4(self):
        """BDEF4ウェイトの取得テスト"""
        vertex = Mock()
        vertex.weight_transform_type = 2  # BDEF4
        vertex.bone_indices = [1, 2, 3, 4]
        vertex.bone_weights = [0.4, 0.3, 0.2, 0.1]

        weights = self.converter._get_pmx_vertex_weights(vertex)

        self.assertEqual(len(weights), 4)
        self.assertEqual(weights[0], (1, 0.4))
        self.assertEqual(weights[1], (2, 0.3))
        self.assertEqual(weights[2], (3, 0.2))
        self.assertEqual(weights[3], (4, 0.1))

    @patch("mmd_tools.converters.bone_converter.maya_mesh_utils.apply_vertex_weights")
    def test_apply_pmx_vertex_weights_uses_source_vertex_indices_for_compact_split(self, mock_apply_weights):
        """compact material split mesh では local vertex 順に対応する元 PMX vertex の weight を適用する。"""
        pmx_data = Mock()
        vertices = []
        for bone_index in [0, 1, 0, 1]:
            vertex = Mock()
            vertex.weight_transform_type = 0
            vertex.bone_indices = [bone_index, 0, 0, 0]
            vertices.append(vertex)
        pmx_data.vertices = vertices

        maya_attribute_utils.add_typed_attribute(self.test_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
        maya_attribute_utils.set_attribute(self.test_mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, [1, 2], "longArray")

        self.converter._apply_pmx_vertex_weights(
            pmx_data,
            ["joint_0", "joint_1"],
            "skinCluster",
            self.test_mesh,
        )

        mock_apply_weights.assert_called_once()
        applied_weights = mock_apply_weights.call_args[0][2]
        self.assertEqual(applied_weights, [[0.0, 1.0], [1.0, 0.0]])

    @patch("mmd_tools.converters.bone_converter.RigConverter")
    def test_convert_pmx_bones_integration(self, mock_rig_converter_class):
        """PMXボーン変換の統合テスト（実際のMaya環境）"""
        # RigConverterのモック
        mock_rig_converter = Mock()
        mock_rig_converter_class.return_value = mock_rig_converter
        mock_rig_converter.setup_pmx_rig.return_value = {
            "ik_handles": [],
            "semi_standard_bones": {},
            "constraints": [],
            "validation_report": None,
        }

        # 新しいインスタンスを作成（モックされたRigConverterを使用）
        converter = BoneConverter()

        # PMXデータのモック
        pmx_data = Mock()
        pmx_data.bones = [
            self._create_mock_pmx_bone(0, "center"),
            self._create_mock_pmx_bone(1, "upper_body", parent_index=0),
        ]
        pmx_data.vertices = []

        with patch("mmd_tools.core.maya_utils.sanitize_bone_name") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x

            maya_joints, skin_cluster = converter.convert_pmx_bones(pmx_data, self.test_mesh, self.root_group)

        # 結果の確認
        self.assertEqual(len(maya_joints), 2)
        self.assertTrue(cmds.objExists(maya_joints[0]))
        self.assertTrue(cmds.objExists(maya_joints[1]))
        self.assertTrue(cmds.objExists(skin_cluster))

        # スケルトングループが作成されているか確認
        skeleton_groups = cmds.ls("Skeleton", type="transform")
        self.assertEqual(len(skeleton_groups), 1)

        # RigConverterが呼ばれたことを確認
        mock_rig_converter.setup_pmx_rig.assert_called_once()

    @patch("mmd_tools.converters.bone_converter.RigConverter")
    def test_convert_pmx_bones_can_skip_rig_setup(self, mock_rig_converter_class):
        """runtime bake用にPMXリグ構築をスキップできる"""
        mock_rig_converter = Mock()
        mock_rig_converter_class.return_value = mock_rig_converter

        converter = BoneConverter()

        pmx_data = Mock()
        pmx_data.bones = [
            self._create_mock_pmx_bone(0, "center"),
            self._create_mock_pmx_bone(1, "upper_body", parent_index=0),
        ]
        pmx_data.vertices = []

        with patch("mmd_tools.core.maya_utils.sanitize_bone_name") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x
            maya_joints, skin_cluster = converter.convert_pmx_bones(
                pmx_data,
                self.test_mesh,
                self.root_group,
                setup_rig=False,
            )

        self.assertEqual(len(maya_joints), 2)
        self.assertTrue(cmds.objExists(skin_cluster))
        mock_rig_converter.setup_pmx_rig.assert_not_called()

    def test_create_maya_joints_skips_local_axis_when_orientation_flag_disabled(self):
        """setup_bone_orientation=False では bake/no-rig parity のため JO を設定しない"""
        bone = self._create_mock_pmx_bone(
            0,
            "local_axis_bone",
            bone_flag=PmxBoneFlag.LOCAL_AXIS | PmxBoneFlag.AXIS_FIXED,
        )
        bone.x_axis_direction = (0.0, 1.0, 0.0)
        bone.z_axis_direction = (0.0, 0.0, 1.0)
        bone.axis_direction = (0.0, 1.0, 0.0)

        skeleton_group = cmds.group(empty=True, name="skeleton_no_orient_grp")
        maya_joints = self.converter._create_maya_joints(
            [bone],
            {0: "local_axis_bone"},
            "pmx",
            skeleton_group,
            setup_bone_orientation=False,
        )

        joint_orient = cmds.getAttr(f"{maya_joints[0]}.jointOrient")[0]
        jo_magnitude = sum(v * v for v in joint_orient) ** 0.5
        self.assertAlmostEqual(jo_magnitude, 0.0, places=6)

    def test_create_maya_joints_local_axis_matches_world_axes_under_rotated_parent(self):
        """親が回転済みでも子のLOCAL_AXIS world X/Z軸と位置を維持する"""
        parent = self._create_mock_pmx_bone(
            0,
            "parent_local_axis",
            position=(0.0, 0.0, 0.0),
            bone_flag=PmxBoneFlag.LOCAL_AXIS,
        )
        parent.x_axis_direction = (0.0, 1.0, 0.0)
        parent.z_axis_direction = (1.0, 0.0, 0.0)

        child = self._create_mock_pmx_bone(
            1,
            "child_local_axis",
            parent_index=0,
            position=(2.0, 3.0, 4.0),
            bone_flag=PmxBoneFlag.LOCAL_AXIS,
        )
        child.x_axis_direction = (1.0, 0.0, 0.0)
        child.z_axis_direction = (0.0, 1.0, 0.0)

        skeleton_group = cmds.group(empty=True, name="skeleton_local_axis_grp")
        maya_joints = self.converter._create_maya_joints(
            [parent, child],
            {0: "parent_local_axis", 1: "child_local_axis"},
            "pmx",
            skeleton_group,
        )

        child_axes = self._joint_world_axis_rows(maya_joints[1])
        self._assert_vector_dot_greater(child_axes[0], self._pmx_axis_to_maya_vector(child.x_axis_direction))
        self._assert_vector_dot_greater(child_axes[2], self._pmx_axis_to_maya_vector(child.z_axis_direction))

        child_world_pos = cmds.xform(maya_joints[1], query=True, worldSpace=True, translation=True)
        for actual, expected in zip(child_world_pos, [2.0, 3.0, -4.0]):
            self.assertAlmostEqual(actual, expected, places=5)


if __name__ == "__main__":
    unittest.main()
