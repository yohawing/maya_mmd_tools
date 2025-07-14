import unittest
from unittest.mock import Mock, patch
import maya.cmds as cmds

from mmd_tools.converters.rig_converter import RigConverter
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


class TestRigConverterMaya(unittest.TestCase):
    """RigConverterクラスのMaya環境でのユニットテスト"""

    def setUp(self):
        """テストごとのセットアップ"""
        # 新しいシーンを作成
        cmds.file(new=True, force=True)
        self.converter = RigConverter()

    def tearDown(self):
        """テストごとのクリーンアップ"""
        # シーンをクリア
        cmds.file(new=True, force=True)

    def _create_mock_pmx_bone(
        self, index, name="TestBone", parent_index=-1, position=(0, 0, 0), bone_flag=0
    ):
        """PMXボーンのモックを作成"""
        bone = Mock()
        bone.name = name
        bone.name_english = f"{name}_en"
        bone.parent_bone_index = parent_index
        bone.position = position
        bone.bone_flag = bone_flag
        bone.get_name.return_value = name
        bone.get_flag = Mock(side_effect=lambda flag: bool(bone_flag & flag))

        # デフォルト値を設定
        bone.given_parent_bone_index = 0
        bone.given_rate = 1.0
        bone.x_axis_direction = (1, 0, 0)
        bone.z_axis_direction = (0, 0, 1)
        bone.ik_target_bone_index = 0
        bone.ik_loop_count = 10
        bone.ik_limit_angle = 1.0
        bone.ik_links = []

        return bone

    def _create_mock_ik_link(
        self, bone_index, angle_limit=False, limit_min=None, limit_max=None
    ):
        """IKリンクのモックを作成"""
        link = Mock()
        link.ik_bone_index = bone_index
        link.angle_limit = angle_limit
        link.limit_min = limit_min if limit_min else (-3.14, -3.14, -3.14)
        link.limit_max = limit_max if limit_max else (3.14, 3.14, 3.14)
        return link

    def _create_test_joints(self):
        """テスト用のジョイント階層を作成"""
        cmds.select(clear=True)
        joints = []

        # ジョイントチェーンを作成
        joints.append(cmds.joint(name="root", position=[0, 0, 0]))
        joints.append(cmds.joint(name="joint1", position=[0, 5, 0]))
        joints.append(cmds.joint(name="joint2", position=[0, 10, 0]))
        joints.append(cmds.joint(name="joint3", position=[0, 15, 0]))

        return joints

    def test_extract_ik_chains_pmx(self):
        """PMXボーンからのIKチェーン抽出テスト"""
        # IKボーンの作成
        ik_bone = self._create_mock_pmx_bone(0, "left_leg_ik", bone_flag=PmxBoneFlag.IK)
        ik_bone.ik_target_bone_index = 1
        ik_bone.ik_links = [
            self._create_mock_ik_link(2, angle_limit=True),
            self._create_mock_ik_link(3, angle_limit=False),
        ]

        # 他のボーンも適切なMockを作成
        other_bones = []
        for i in range(1, 4):
            bone = Mock()
            bone.get_flag = Mock(return_value=False)
            bone.ik_links = []  # 空のリストを設定
            other_bones.append(bone)

        bones = [ik_bone] + other_bones
        bone_map = {0: "left_leg_ik", 1: "left_ankle", 2: "left_knee", 3: "left_leg"}

        ik_chains = self.converter._extract_ik_chains(bones, bone_map)

        self.assertEqual(len(ik_chains), 1)
        chain = ik_chains[0]
        self.assertEqual(chain["ik_bone"], "left_leg_ik")
        self.assertEqual(chain["target_bone"], "left_ankle")
        self.assertEqual(len(chain["ik_links"]), 2)
        self.assertTrue(chain["ik_links"][0]["angle_limit"])
        self.assertFalse(chain["ik_links"][1]["angle_limit"])

    def test_create_maya_ik_handles(self):
        """IKハンドル作成のテスト（実際のMaya環境）"""
        # テスト用のジョイントチェーンを作成
        joints = self._create_test_joints()

        # IKボーン用の追加ジョイント
        cmds.select(clear=True)
        ik_bone = cmds.joint(name="leg_ik", position=[5, 0, 0])

        # IKチェーンの設定
        # 正しい階層: root -> joint1 -> joint2 -> joint3
        # IKは joint1（開始）から joint3（ターゲット）へ
        ik_chains = [
            {
                "ik_bone": ik_bone,
                "target_bone": joints[3],  # joint3 (ターゲット)
                "loop_count": 10,
                "unit_angle": 1.0,
                "ik_links": [
                    {
                        "bone": joints[3],
                        "angle_limit": False,
                        "limit_min": None,
                        "limit_max": None,
                    },
                    {
                        "bone": joints[2],
                        "angle_limit": True,
                        "limit_min": (-1.57, 0, 0),
                        "limit_max": (0, 0, 0),
                    },
                    {
                        "bone": joints[1],
                        "angle_limit": False,
                        "limit_min": None,
                        "limit_max": None,
                    },  # これが最後（開始ジョイント）
                ],
            }
        ]

        ik_handles = self.converter._create_maya_ik_handles(ik_chains)

        self.assertEqual(len(ik_handles), 1)
        handle_info = ik_handles[0]

        # IKハンドルが作成されたか確認
        self.assertTrue(cmds.objExists(handle_info["ik_handle"]))

        # IKハンドルがIKボーンの子になっているか確認
        parent = cmds.listRelatives(handle_info["ik_handle"], parent=True)
        self.assertEqual(parent[0], ik_bone)

        # IKハンドルが非表示になっているか確認
        visibility = cmds.getAttr(f"{handle_info['ik_handle']}.visibility")
        self.assertEqual(visibility, 0)

    def test_set_joint_limits(self):
        """ジョイント角度制限の設定テスト（実際のMaya環境）"""
        joints = self._create_test_joints()

        ik_links = [
            {
                "bone": joints[1],
                "angle_limit": True,
                "limit_min": (-1.57, 0, 0),
                "limit_max": (0, 0, 0),
            },
            {
                "bone": joints[2],
                "angle_limit": False,
                "limit_min": None,
                "limit_max": None,
            },
        ]

        self.converter._set_joint_limits(ik_links)

        # 角度制限が設定されたか確認
        # joints[2]のみ制限が有効になっているはず
        # Mayaではジョイントの制限は.limitSwitchX等のアトリビュートで確認
        if cmds.objExists(f"{joints[2]}.limitSwitchX"):
            limitEnabled = cmds.getAttr(f"{joints[2]}.limitSwitchX")
            self.assertTrue(limitEnabled)  # True = 制限が有効

    def test_find_joint_by_name(self):
        """ジョイント名検索のテスト"""
        maya_joints = ["center", "upper_body", "head", "left_arm", "right_arm"]

        # 日本語名で検索
        result = self.converter._find_joint_by_name(maya_joints, ["センター", "center"])
        self.assertEqual(result, "center")

        # 部分一致で検索
        result = self.converter._find_joint_by_name(maya_joints, ["left", "左"])
        self.assertEqual(result, "left_arm")

        # 見つからない場合
        result = self.converter._find_joint_by_name(
            maya_joints, ["存在しない", "notfound"]
        )
        self.assertIsNone(result)

    def test_add_semi_standard_bones(self):
        """準標準ボーン追加のテスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        center = cmds.joint(name="center", position=[0, 10, 0])
        lower_body = cmds.joint(name="lower_body", position=[0, 8, 0])
        cmds.select(clear=True)
        left_leg = cmds.joint(name="left_leg", position=[-2, 6, 0])
        cmds.parent(left_leg, lower_body)
        cmds.select(clear=True)
        right_leg = cmds.joint(name="right_leg", position=[2, 6, 0])
        cmds.parent(right_leg, lower_body)

        maya_joints = [center, lower_body, left_leg, right_leg]
        bone_map = {0: center, 1: lower_body, 2: left_leg, 3: right_leg}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        # centerをスケルトングループの子にする
        cmds.parent(center, skeleton_group)

        result = self.converter._add_semi_standard_bones(
            maya_joints, bone_map, skeleton_group
        )

        # 追加されたボーンの確認
        self.assertIn("parent_of_all", result)
        self.assertIn("groove", result)

        # parent_of_allが存在するか
        self.assertTrue(cmds.objExists(result["parent_of_all"]))

        # grooveが存在し、centerの親になっているか
        self.assertTrue(cmds.objExists(result["groove"]))
        parent_of_center = cmds.listRelatives(center, parent=True)[0]
        self.assertEqual(parent_of_center, result["groove"])

        # waistが追加されたか（既存の腰がない場合）
        if "waist" in result:
            self.assertTrue(cmds.objExists(result["waist"]))
            # 腰が下半身の子、足の親になっているか
            parent_of_waist = cmds.listRelatives(result["waist"], parent=True)[0]
            self.assertEqual(parent_of_waist, lower_body)

    def test_setup_given_parent_bones_rotation(self):
        """回転付与ボーンの設定テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        bone = self._create_mock_pmx_bone(
            0, "TestBone", bone_flag=PmxBoneFlag.GIVEN_PARENT_ROTATE
        )
        bone.given_parent_bone_index = 1
        bone.given_rate = 0.5

        # Mockの問題を回避するため、完全なMockボーンを作成
        parent_bone = Mock()
        parent_bone.get_flag = Mock(return_value=False)

        bones = [bone, parent_bone]
        maya_joints = [child_joint, parent_joint]

        constraints = self.converter._setup_given_parent_bones(bones, maya_joints)

        self.assertEqual(len(constraints), 1)

        # エクスプレッションが作成されたか確認
        expressions = cmds.ls(type="expression")
        self.assertTrue(any("given_rotation_expr" in expr for expr in expressions))

    def test_create_partial_rotation_constraint(self):
        """部分的な回転付与の作成テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        rate = 0.5

        expr_name = self.converter._create_partial_rotation_constraint(
            parent_joint, child_joint, rate
        )

        self.assertEqual(expr_name, "child_joint_given_rotation_expr")

        # エクスプレッションが作成されたか確認
        self.assertTrue(cmds.objExists(expr_name))

        # ロケータが作成されたか確認
        locators = cmds.ls("*_base_rotation*", type="transform")
        self.assertEqual(len(locators), 1)

        # ロケータが非表示か確認
        visibility = cmds.getAttr(f"{locators[0]}.visibility")
        self.assertEqual(visibility, 0)

    @patch.object(RigConverter, "_extract_ik_chains")
    @patch.object(RigConverter, "_create_maya_ik_handles")
    def test_setup_pmx_rig_integration(self, mock_ik_handles, mock_extract_ik):
        """PMXリグセットアップの統合テスト（実際のMaya環境）"""
        # モックの戻り値設定
        mock_extract_ik.return_value = [{"ik_bone": "test_ik"}]
        mock_ik_handles.return_value = [{"ik_handle": "test_ikHandle"}]

        # テストデータ
        pmx_data = Mock()
        pmx_data.bones = []

        # テスト用のジョイントとグループを作成
        cmds.select(clear=True)
        joint1 = cmds.joint(name="joint1", position=[0, 0, 0])
        joint2 = cmds.joint(name="joint2", position=[0, 5, 0])
        maya_joints = [joint1, joint2]

        bone_map = {0: joint1, 1: joint2}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        result = self.converter.setup_pmx_rig(
            pmx_data, maya_joints, bone_map, skeleton_group
        )

        # 各メソッドが呼ばれたか確認
        mock_extract_ik.assert_called_once()
        mock_ik_handles.assert_called_once()

        # 結果の確認
        self.assertEqual(len(result["ik_handles"]), 1)
        self.assertTrue("semi_standard_bones" in result)
        self.assertTrue("validation_report" in result)


if __name__ == "__main__":
    unittest.main()
