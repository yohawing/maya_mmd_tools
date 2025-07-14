import unittest
from unittest.mock import Mock, patch
import maya.cmds as cmds

from mmd_tools.converters.bone_converter import BoneConverter
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

        self.assertEqual(attrs["pmx_bone_index"], 0)
        self.assertEqual(attrs["pmx_bone_name"], "TestBone")
        self.assertTrue(attrs["pmx_bone_rotatable"])
        self.assertTrue(attrs["pmx_bone_movable"])

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
