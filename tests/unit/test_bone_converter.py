import unittest
from unittest.mock import Mock, patch
import maya.cmds as cmds

from mmd_tools.converters.bone_converter import BoneConverter
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
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

    def _create_mock_pmx_bone(
        self, index, name="TestBone", parent_index=-1, position=(0, 0, 0), bone_flag=0
    ):
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
        bone.given_parent_bone_index = 0
        bone.given_rate = 1.0
        bone.axis_direction = (0, 1, 0)
        bone.x_axis_direction = (1, 0, 0)
        bone.z_axis_direction = (0, 0, 1)
        bone.key_value = 0
        bone.ik_target_bone_index = 0
        bone.ik_loop_count = 0
        bone.ik_limit_angle = 0
        bone.ik_links = []

        return bone

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

        with patch("mmd_tools.core.maya_utils.sanitize_text") as mock_sanitize:
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

        with patch("mmd_tools.core.maya_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x  # そのまま返す

            bone_map = self.converter._create_bone_mapping(bones)

        self.assertEqual(bone_map[0], "ボーン")
        self.assertEqual(bone_map[1], "ボーン_1")
        self.assertEqual(bone_map[2], "ボーン_2")

    @patch("mmd_tools.core.maya_utils.sanitize_text")
    def test_create_maya_joints_hierarchy(self, mock_sanitize):
        """ジョイント階層作成のテスト（実際のMaya環境）"""
        mock_sanitize.side_effect = lambda x: x

        bones = [
            self._create_mock_pmx_bone(
                0, "center", parent_index=-1, position=(0, 0, 0)
            ),
            self._create_mock_pmx_bone(
                1, "upper_body", parent_index=0, position=(0, 10, 0)
            ),
            self._create_mock_pmx_bone(2, "head", parent_index=1, position=(0, 20, 0)),
        ]

        bone_map = {0: "center", 1: "upper_body", 2: "head"}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        maya_joints = self.converter._create_maya_joints(
            bones, bone_map, "pmx", skeleton_group
        )

        # ジョイント作成の確認
        self.assertEqual(len(maya_joints), 3)
        self.assertTrue(cmds.objExists(maya_joints[0]))
        self.assertTrue(cmds.objExists(maya_joints[1]))
        self.assertTrue(cmds.objExists(maya_joints[2]))

        # 親子関係の確認
        parent_of_upper = cmds.listRelatives(maya_joints[1], parent=True)[0]
        self.assertEqual(parent_of_upper, maya_joints[0])

        parent_of_head = cmds.listRelatives(maya_joints[2], parent=True)[0]
        self.assertEqual(parent_of_head, maya_joints[1])

        # 位置の確認（Mayaは左手系なのでZ座標が反転）
        center_pos = cmds.xform(
            maya_joints[0], query=True, worldSpace=True, translation=True
        )
        self.assertAlmostEqual(center_pos[0], 0, places=5)
        self.assertAlmostEqual(center_pos[1], 0, places=5)
        self.assertAlmostEqual(center_pos[2], 0, places=5)

        upper_pos = cmds.xform(
            maya_joints[1], query=True, worldSpace=True, translation=True
        )
        self.assertAlmostEqual(upper_pos[0], 0, places=5)
        self.assertAlmostEqual(upper_pos[1], 10, places=5)
        self.assertAlmostEqual(upper_pos[2], 0, places=5)

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト"""
        bone = self._create_mock_pmx_bone(
            0, "TestBone", bone_flag=PmxBoneFlag.ROTATABLE | PmxBoneFlag.MOVABLE
        )

        # 実際のジョイントを作成
        joint = cmds.joint(name="test_joint")

        self.converter._set_extra_attributes(0, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 0)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "TestBone")
        self.assertTrue(
            attrs[ATTR_MMD_BONE_FLAGS], PmxBoneFlag.ROTATABLE | PmxBoneFlag.MOVABLE
        )

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_given_rotate(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（回転付与）"""
        bone = self._create_mock_pmx_bone(
            5, "GivenBone", bone_flag=PmxBoneFlag.GIVEN_PARENT_ROTATE
        )
        bone.given_parent_bone_index = 3
        bone.given_rate = 0.5

        # 実際のジョイントを作成
        joint = cmds.joint(name="given_joint")

        self.converter._set_extra_attributes(5, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 5)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "GivenBone")
        self.assertEqual(attrs[ATTR_MMD_BONE_FLAGS], PmxBoneFlag.GIVEN_PARENT_ROTATE)

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 0.5)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 3)

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_given_move(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（移動付与）"""
        bone = self._create_mock_pmx_bone(
            7, "GivenMoveBone", bone_flag=PmxBoneFlag.GIVEN_PARENT_MOVE
        )
        bone.given_parent_bone_index = 2
        bone.given_rate = 1.0

        # 実際のジョイントを作成
        joint = cmds.joint(name="given_move_joint")

        self.converter._set_extra_attributes(7, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 7)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "GivenMoveBone")

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 1.0)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 2)

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_given_both(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（回転＋移動付与）"""
        bone = self._create_mock_pmx_bone(
            9,
            "GivenBothBone",
            bone_flag=PmxBoneFlag.GIVEN_PARENT_ROTATE | PmxBoneFlag.GIVEN_PARENT_MOVE,
        )
        bone.given_parent_bone_index = 4
        bone.given_rate = 0.75

        # 実際のジョイントを作成
        joint = cmds.joint(name="given_both_joint")

        self.converter._set_extra_attributes(9, joint, bone, "pmx")

        # カスタムアトリビュートが設定されているか確認
        mock_set_attrs.assert_called_once()
        attrs = mock_set_attrs.call_args[0][1]

        # 基本属性
        self.assertEqual(attrs[ATTR_MMD_BONE_INDEX], 9)
        self.assertEqual(attrs[ATTR_MMD_BONE_NAME], "GivenBothBone")

        # 付与関連の属性を確認
        from mmd_tools.core.constants import (
            ATTR_MMD_GRANT_RATE,
            ATTR_MMD_GRANT_PARENT_INDEX,
        )

        self.assertIn(ATTR_MMD_GRANT_RATE, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_RATE], 0.75)
        self.assertIn(ATTR_MMD_GRANT_PARENT_INDEX, attrs)
        self.assertEqual(attrs[ATTR_MMD_GRANT_PARENT_INDEX], 4)

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
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

    @patch("mmd_tools.core.maya_utils.set_custom_attributes")
    def test_set_extra_attributes_pmx_local_axis(self, mock_set_attrs):
        """PMXボーンのカスタムアトリビュート設定テスト（ローカル軸）"""
        bone = self._create_mock_pmx_bone(
            15, "LocalAxisBone", bone_flag=PmxBoneFlag.LOCAL_AXIS
        )
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
        skin_cluster = self.converter._create_skin_cluster(
            maya_joints, self.test_mesh, max_influence=4
        )

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

        with patch("mmd_tools.core.maya_utils.sanitize_text") as mock_sanitize:
            mock_sanitize.side_effect = lambda x: x

            maya_joints, skin_cluster = converter.convert_pmx_bones(
                pmx_data, self.test_mesh, self.root_group
            )

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


if __name__ == "__main__":
    unittest.main()
