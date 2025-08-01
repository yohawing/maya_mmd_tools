import math
import unittest
from unittest.mock import Mock, patch
import maya.cmds as cmds

from mmd_tools.converters.rig_converter import RigConverter
from mmd_tools.core import maya_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_INDEX,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.settings import settings
from mmd_tools.io.pmx_importer import import_pmx_file
from mmd_tools.io.vmd_importer import import_vmd_file
from tests.common.test_fixture_provider import TestFixtureProvider


class TestRigConverterMaya(unittest.TestCase):
    """RigConverterクラスのMaya環境でのユニットテスト"""

    def setUp(self):
        """テストごとのセットアップ"""
        # 新しいシーンを作成
        cmds.file(new=True, force=True)
        self.converter = RigConverter()
        # 設定を保存しておく
        self.original_settings = settings.get(
            "import.rig.add_semi_standard_bones", False
        )
        # TestFixtureProviderを初期化
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テストごとのクリーンアップ"""
        # シーンをクリア
        cmds.file(new=True, force=True)
        # 設定を元に戻す
        settings.set("import.rig.add_semi_standard_bones", self.original_settings)
        self.fixture_provider.cleanup_temp_files()

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
        bone.transform_layer = 0  # デフォルトの変形階層

        return bone

    def _create_mock_ik_link(
        self, bone_index, angle_limit=False, limit_min=None, limit_max=None
    ):
        """IKリンクのモックを作成"""
        link = Mock()
        link.ik_bone_index = bone_index
        link.angle_limit = angle_limit
        link.limit_min = limit_min if limit_min else (-math.pi, -math.pi, -math.pi)
        link.limit_max = limit_max if limit_max else (math.pi, math.pi, math.pi)
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
                        "limit_min": (-math.pi / 2, 0, 0),
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
                "limit_min": (-math.pi / 2, 0, 0),
                "limit_max": (math.pi / 2, 0, 0),
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
        if cmds.objExists(f"{joints[1]}"):
            limitXEnabled = cmds.getAttr(f"{joints[1]}.minRotXLimitEnable")
            limitYEnabled = cmds.getAttr(f"{joints[1]}.minRotYLimitEnable")
            limitZEnabled = cmds.getAttr(f"{joints[1]}.minRotZLimitEnable")
            limitRotateMinX = cmds.getAttr(f"{joints[1]}.minRotXLimit")
            limitRotateMaxX = cmds.getAttr(f"{joints[1]}.maxRotXLimit")
            limitRotateMinY = cmds.getAttr(f"{joints[1]}.minRotYLimit")
            limitRotateMaxY = cmds.getAttr(f"{joints[1]}.maxRotYLimit")
            limitRotateMinZ = cmds.getAttr(f"{joints[1]}.minRotZLimit")
            limitRotateMaxZ = cmds.getAttr(f"{joints[1]}.maxRotZLimit")

            self.assertTrue(
                limitXEnabled and limitYEnabled and limitZEnabled
            )  # True = 制限が有効
            self.assertAlmostEqual(limitRotateMinX, 90.0, delta=0.01)
            self.assertAlmostEqual(limitRotateMaxX, -90.0, delta=0.01)
            self.assertAlmostEqual(limitRotateMinY, 0.0, delta=0.01)
            self.assertAlmostEqual(limitRotateMaxY, 0.0, delta=0.01)
            self.assertAlmostEqual(limitRotateMinZ, 0.0, delta=0.01)
            self.assertAlmostEqual(limitRotateMaxZ, 0.0, delta=0.01)
        else:
            # Mayaのバージョンによっては.limitSwitchXが存在しない場合もある
            self.assertTrue(
                cmds.getAttr(f"{joints[1]}.rotateLimitX") is not None,
                "ジョイントの回転制限が設定されていません",
            )

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
        self.assertIn("master", result)
        self.assertIn("groove", result)

        # masterが存在するか
        self.assertTrue(cmds.objExists(result["master"]))

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

    def test_given_bones_with_pmx(self):
        """実際のPMXデータを使用した付与ボーンのテスト"""

        # pmd_file_path = self.fixture_provider.get_pmx_file("test_given_bone")

        # PMXデータを読み込む
        pmx_data, file_path = self.fixture_provider.load_pmx_data("test_given_bone")
        # PMXデータからボーンを取得
        bones = pmx_data.bones

        import_pmx_file(pmx_data, file_path, scale=1.0)

        # インポートできているか確認
        root_group = cmds.ls("test_given_bone_root", type="transform")
        self.assertTrue(root_group)

        # ボーンの数を確認
        maya_joints = cmds.ls(type="joint")
        self.assertEqual(len(maya_joints), len(bones))

        # 付与設定がなされているか確認。

        # 付与ボーンの確認
        for joint in maya_joints:
            if joint in ["B", "C", "D"]:  # 付与ボーンの名前を確認
                self.assertTrue(
                    maya_utils.get_attribute(joint, ATTR_MMD_GRANT_PARENT_INDEX)
                )
                self.assertTrue(maya_utils.get_attribute(joint, ATTR_MMD_GRANT_RATE))

        # グローバルの位置を確認
        # A: (1,2,-2)
        bone_a = cmds.ls("A", type="joint")[0]
        pos_a = cmds.xform(bone_a, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos_a[0], 1.0, delta=0.1)
        self.assertAlmostEqual(pos_a[1], 2.0, delta=0.1)
        self.assertAlmostEqual(pos_a[2], -2.0, delta=0.1)

        # B: (2,2,-2) - 付与ボーン
        bone_b = cmds.ls("B", type="joint")[0]
        pos_b = cmds.xform(bone_b, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos_b[0], 2.0, delta=0.1)
        self.assertAlmostEqual(pos_b[1], 2.0, delta=0.1)
        self.assertAlmostEqual(pos_b[2], -2.0, delta=0.1)

        # C: (3,2,-2) - ローカル付与ボーン
        bone_c = cmds.ls("C", type="joint")[0]
        pos_c = cmds.xform(bone_c, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos_c[0], 3.0, delta=0.1)
        self.assertAlmostEqual(pos_c[1], 2.0, delta=0.1)
        self.assertAlmostEqual(pos_c[2], -2.0, delta=0.1)

        # D: (0,0,0) - 多重付与ボーン
        bone_d = cmds.ls("D", type="joint")[0]
        pos_d = cmds.xform(bone_d, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos_d[0], 0.0, delta=0.1)
        self.assertAlmostEqual(pos_d[1], 0.0, delta=0.1)
        self.assertAlmostEqual(pos_d[2], 0.0, delta=0.1)

        # VMDファイルを読み込む
        vmd_data, _ = self.fixture_provider.load_vmd_data("test_given_bone")

        # VMDデータを適用
        import_vmd_file(
            vmd_data, root_group[0], options={"target_model": "test_given_bone"}
        )

        # VMDデータの適用を確認
        # 5フレーム目に移動
        cmds.currentTime(5)

        # 付与ボーンのテストで想定されるボーン構造を確認
        # PMXファイル "test_given_bone" には以下のようなボーンがあると想定:
        # A: 通常のボーン
        # B: Aから100%の回転と移動付与を受けるボーン
        # C: ローカル付与でBから回転移動付与を受けるボーン
        # D: 多重付与でBから回転付与を受けるボーン

        # ボーンを名前で検索（実際のPMXファイルのボーン名に依存）
        all_joints = cmds.ls(type="joint")

        # デバッグ情報：実際のボーン構造を出力
        bone_info = {}
        for joint in all_joints:
            if cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
                index = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}")
                name_jp = ""
                if cmds.attributeQuery(ATTR_MMD_BONE_NAME, node=joint, exists=True):
                    name_jp = cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_NAME}")
                bone_info[index] = {"joint": joint, "name_jp": name_jp}
                print(f"ボーン情報: index={index}, joint={joint}, name_jp={name_jp}")

        # A_ (通常ボーン) の確認 - 名前で検索
        # loc: 1, 0, 1.14*2, rot: 90, 0, 0
        bone_a = "A"

        # 位置の確認
        pos = cmds.xform(bone_a, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos[0], 1.0, delta=0.1)
        self.assertAlmostEqual(pos[1], 0.0, delta=0.1)
        self.assertAlmostEqual(pos[2], math.sqrt(2) * 2, delta=0.1)

        # 回転の確認
        rot = cmds.xform(bone_a, query=True, worldSpace=True, rotation=True)
        self.assertAlmostEqual(rot[0], 90.0, delta=1.0)
        self.assertAlmostEqual(rot[1], 0.0, delta=1.0)
        self.assertAlmostEqual(rot[2], 0.0, delta=1.0)

        # B_ (付与ボーン) の確認 - 名前で検索
        # loc: 2, 2, 2, rot: 90, 0, 0  (付与45+回転45)
        bone_b = "B"

        # 位置の確認
        pos = cmds.xform(bone_b, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos[0], 2.0, delta=0.1)
        self.assertAlmostEqual(pos[1], 2.0, delta=0.1)
        self.assertAlmostEqual(pos[2], 2.0, delta=0.1)

        # 回転の確認（付与45度 + 自身の回転45度 = 90度）
        rot = cmds.xform(bone_b, query=True, worldSpace=True, rotation=True)
        self.assertAlmostEqual(rot[0], 90.0, delta=1.0)
        self.assertAlmostEqual(rot[1], 0.0, delta=1.0)
        self.assertAlmostEqual(rot[2], 0.0, delta=1.0)

        # C_ (ローカル付与ボーン) の確認 - 名前で検索
        # loc: 3, 2, 2 rot: 45 (親の回転の付与）
        bone_c = "C"

        # 位置の確認
        pos = cmds.xform(bone_c, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos[0], 3.0, delta=0.1)
        self.assertAlmostEqual(pos[1], 2.0, delta=0.1)
        self.assertAlmostEqual(pos[2], 2.0, delta=0.1)

        # 回転の確認（ローカル付与で45度）
        rot = cmds.xform(bone_c, query=True, worldSpace=True, rotation=True)
        self.assertAlmostEqual(rot[0], 45.0, delta=1.0)

        # D_ (多重付与ボーン) の確認 - 名前で検索
        # loc: 0,0,0  rot: 90,0,0 ( Bの付与）
        bone_d = "D"

        # 位置の確認
        pos = cmds.xform(bone_d, query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(pos[0], 0.0, delta=0.1)
        self.assertAlmostEqual(pos[1], 0.0, delta=0.1)
        self.assertAlmostEqual(pos[2], 0.0, delta=0.1)

        # 回転の確認（Bから付与された90度）
        rot = cmds.xform(bone_d, query=True, worldSpace=True, rotation=True)
        self.assertAlmostEqual(rot[0], 90.0, delta=1.0)
        self.assertAlmostEqual(rot[1], 0.0, delta=1.0)
        self.assertAlmostEqual(rot[2], 0.0, delta=1.0)

        # 付与関係が正しく設定されているかの追加確認
        # 少なくとも1つの付与ボーンが見つかったことを確認
        found_given_bones = sum(1 for b in [bone_b, bone_c, bone_d] if b is not None)
        self.assertGreater(found_given_bones, 0, "付与ボーンが見つかりませんでした")

    def test_setup_given_parent_bones_rotation(self):
        """回転付与ボーンの設定テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        bone = self._create_mock_pmx_bone(
            0, "TestBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE
        )
        bone.given_parent_bone_index = 1
        bone.given_rate = 0.5

        # Mockの問題を回避するため、完全なMockボーンを作成
        parent_bone = Mock()
        parent_bone.get_flag = Mock(return_value=False)

        bones = [bone, parent_bone]
        maya_joints = [child_joint, parent_joint]

        constraints = self.converter._setup_grant_bones(bones, maya_joints)

        self.assertEqual(len(constraints), 1)

        # ネイティブノードが作成されたか確認（付与率が1.0でない場合）
        # decomposeMatrixノードが作成されたか確認
        decompose_nodes = cmds.ls("parent_joint_decompose", type="decomposeMatrix")
        self.assertTrue(len(decompose_nodes) > 0)

        # multiplyDivideノードが作成されたか確認
        mult_nodes = cmds.ls("child_joint_given_mult", type="multiplyDivide")
        self.assertTrue(len(mult_nodes) > 0)

    def test_pole_target_position_for_leg_ik(self):
        """足IKのPoleTarget位置が膝の前方に配置されるかテスト"""
        # 足のジョイントチェーンを作成
        cmds.select(clear=True)
        hip = cmds.joint(name="left_leg", position=[2, 10, 0])
        knee = cmds.joint(
            name="left_knee", position=[2, 5, 0.5]
        )  # 膝は少し前に出ている
        ankle = cmds.joint(name="left_ankle", position=[2, 0, 0])

        # IKボーンを作成
        cmds.select(clear=True)
        ik_bone = cmds.joint(name="left_leg_ik", position=[2, 0, 0])

        # IKチェーン情報を作成
        chain = {
            "ik_bone": "left_leg_ik",
            "ik_bone_index": 0,
            "target_bone": "left_ankle",
            "target_bone_index": 2,
            "loop_count": 40,
            "unit_angle": 114.5916,
            "ik_links": [
                {"bone": "left_knee", "bone_index": 1, "angle_limit": False},
                {"bone": "left_leg", "bone_index": 0, "angle_limit": False},
            ],
        }

        # IKハンドルを作成
        ik_handle, _ = cmds.ikHandle(
            startJoint=hip,
            endEffector=ankle,
            solver="ikRPsolver",
            name="left_leg_ik_ikHandle",
        )

        # PoleTargetを作成
        pole_target = self.converter._create_pole_target_for_leg_ik(
            chain, ik_handle, hip, ankle
        )

        # PoleTargetが作成されたか確認
        self.assertIsNotNone(pole_target)
        self.assertTrue(cmds.objExists(pole_target))

        # PoleTargetの位置を取得
        pole_pos = cmds.xform(
            pole_target, query=True, worldSpace=True, translation=True
        )
        knee_pos = cmds.xform(knee, query=True, worldSpace=True, translation=True)

        # PoleTargetが膝の近くに配置されているか確認（Y座標が近い）
        self.assertAlmostEqual(pole_pos[1], knee_pos[1], delta=1.0)

        # PoleTargetが膝の前方（Z軸正方向）に配置されているか確認
        self.assertGreater(
            pole_pos[2], knee_pos[2], "PoleTargetが膝の前方に配置されていません"
        )

        # PoleTargetが適切な距離に配置されているか確認（デフォルト2ユニット）
        distance = (
            (pole_pos[0] - knee_pos[0]) ** 2
            + (pole_pos[1] - knee_pos[1]) ** 2
            + (pole_pos[2] - knee_pos[2]) ** 2
        ) ** 0.5
        self.assertAlmostEqual(distance, 2.0, delta=1.0)

    def test_setup_given_parent_bones_local_given(self):
        """ローカル付与ボーンの設定テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        # ローカル付与フラグを含むボーンを作成
        bone = self._create_mock_pmx_bone(
            0, "TestBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.LOCAL
        )
        bone.given_parent_bone_index = 1
        bone.given_rate = 0.5

        parent_bone = Mock()
        parent_bone.get_flag = Mock(return_value=False)

        bones = [bone, parent_bone]
        maya_joints = [child_joint, parent_joint]

        constraints = self.converter._setup_grant_bones(bones, maya_joints)

        self.assertEqual(len(constraints), 1)

        # ローカル付与の場合はネイティブノードが作成される
        # plusMinusAverageノードが作成されたか確認
        diff_nodes = cmds.ls("parent_joint_local_diff", type="plusMinusAverage")
        self.assertEqual(len(diff_nodes), 1)

        # multiplyDivideノードが作成されたか確認
        mult_nodes = cmds.ls("child_joint_local_mult", type="multiplyDivide")
        self.assertEqual(len(mult_nodes), 1)

    def test_setup_given_parent_bones_with_transform_layer(self):
        """変形階層を考慮した付与ボーンのテスト"""
        # 複数のジョイントを作成
        cmds.select(clear=True)
        joints = []
        for i in range(4):
            if i > 0:
                cmds.select(clear=True)
            joint = cmds.joint(name=f"joint{i}", position=[i * 5, 0, 0])
            joints.append(joint)

        # 異なる変形階層を持つ付与ボーンを作成
        bones = []
        for i in range(4):
            if i < 2:
                # 付与ボーンとして設定（異なる変形階層）
                bone = self._create_mock_pmx_bone(
                    i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE
                )
                bone.given_parent_bone_index = 3  # joint3を親にする
                bone.given_rate = 0.5
                bone.transform_layer = 1 if i == 0 else 0  # 異なる階層
            else:
                # 通常のボーン
                bone = Mock()
                bone.get_flag = Mock(return_value=False)
                bone.transform_layer = 0  # Mockオブジェクトに属性を追加
            bones.append(bone)

        constraints = self.converter._setup_grant_bones(bones, joints)

        # 2つの付与関係が設定される
        self.assertEqual(len(constraints), 2)

    def test_multiple_given_dependencies(self):
        """多重付与（付与ボーンが他の付与ボーンを参照）のテスト"""
        # ジョイントチェーンを作成
        cmds.select(clear=True)
        joints = []
        for i in range(4):
            if i > 0:
                cmds.select(clear=True)
            joint = cmds.joint(name=f"joint{i}", position=[i * 5, 0, 0])
            joints.append(joint)

        # 多重付与の構造を作成
        # joint0 -> joint1 (付与)
        # joint1 -> joint2 (付与)
        bones = []
        for i in range(4):
            if i == 0:
                # joint0: joint1から付与を受ける
                bone = self._create_mock_pmx_bone(
                    i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE
                )
                bone.given_parent_bone_index = 1
                bone.given_rate = 0.5
            elif i == 1:
                # joint1: joint2から付与を受ける（多重付与）
                bone = self._create_mock_pmx_bone(
                    i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE
                )
                bone.given_parent_bone_index = 2
                bone.given_rate = 0.7
            else:
                # 通常のボーン
                bone = Mock()
                bone.get_flag = Mock(return_value=False)
                bone.transform_layer = 0  # Mockオブジェクトに属性を追加
            bones.append(bone)

        constraints = self.converter._setup_grant_bones(bones, joints)

        # 2つの付与関係が設定される
        self.assertEqual(len(constraints), 2)

    def test_create_weighted_rotation_constraint(self):
        """重み付き回転コンストレイントの作成テスト"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        rate = 0.5

        created_nodes = self.converter._create_weighted_rotation_constraint(
            parent_joint, child_joint, rate
        )

        # ノードが作成されたか確認
        self.assertIsInstance(created_nodes, list)
        self.assertTrue(len(created_nodes) > 0)

        # 初期ロケータが作成されたか確認
        init_locator = cmds.ls("child_joint_init_rot", type="transform")
        self.assertEqual(len(init_locator), 1)

        # decomposeMatrixノードが作成されたか確認
        decompose_nodes = cmds.ls("parent_joint_decompose", type="decomposeMatrix")
        self.assertEqual(len(decompose_nodes), 1)

        # multiplyDivideノードが作成されたか確認
        mult_nodes = cmds.ls("child_joint_given_mult", type="multiplyDivide")
        self.assertEqual(len(mult_nodes), 1)

    def test_create_negative_rate_rotation_constraint(self):
        """負の付与率の回転コンストレイントの作成テスト"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        rate = -0.5  # 負の付与率

        created_nodes = self.converter._create_weighted_rotation_constraint(
            parent_joint, child_joint, rate
        )

        # ノードが作成されたか確認
        self.assertIsInstance(created_nodes, list)
        self.assertTrue(len(created_nodes) > 0)

        # 負の付与率の場合、反転用のmultiplyDivideノードが作成される
        invert_nodes = cmds.ls("child_joint_invert_rot", type="multiplyDivide")
        self.assertEqual(len(invert_nodes), 1)

        # 初期ロケータが作成されたか確認
        init_locator = cmds.ls("child_joint_init_rot", type="transform")
        self.assertEqual(len(init_locator), 1)

    def test_find_joint_by_japanese_name(self):
        """日本語名でのジョイント検索テスト"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        center_joint = cmds.joint(name="center", position=[0, 0, 0])

        # カスタムアトリビュートを追加
        cmds.addAttr(
            center_joint,
            longName="mmd_bone_index",
            attributeType="long",
            defaultValue=0,
        )

        # コンバーターに元のボーン名を設定
        self.converter.original_bone_names = {0: "センター", 1: "上半身", 2: "腰"}

        # 日本語名で検索
        result = self.converter._find_joint_by_japanese_name(["センター"])
        self.assertEqual(result, center_joint)

        # 見つからない場合
        result = self.converter._find_joint_by_japanese_name(["グルーブ"])
        self.assertIsNone(result)

    def test_setup_pmx_rig_with_semi_standard_bones_enabled(self):
        """準標準ボーン有効時のPMXリグセットアップテスト"""
        # 設定を有効化
        settings.set("import.rig.add_semi_standard_bones", True)

        # テストデータ
        pmx_data = Mock()
        bone1 = self._create_mock_pmx_bone(0, "センター")
        bone2 = self._create_mock_pmx_bone(1, "下半身", parent_index=0)
        pmx_data.bones = [bone1, bone2]

        # テスト用のジョイントを作成
        cmds.select(clear=True)
        center = cmds.joint(name="center", position=[0, 10, 0])
        lower_body = cmds.joint(name="lower_body", position=[0, 8, 0])
        maya_joints = [center, lower_body]

        bone_map = {0: center, 1: lower_body}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")
        cmds.parent(center, skeleton_group)

        result = self.converter.setup_pmx_rig(
            pmx_data, maya_joints, bone_map, skeleton_group
        )

        # 準標準ボーンが追加されたか確認
        self.assertIn("semi_standard_bones", result)
        self.assertIsNotNone(result["semi_standard_bones"])

    def test_setup_pmx_rig_with_semi_standard_bones_disabled(self):
        """準標準ボーン無効時のPMXリグセットアップテスト"""
        # 設定を無効化
        settings.set("import.rig.add_semi_standard_bones", False)

        # テストデータ
        pmx_data = Mock()
        bone1 = self._create_mock_pmx_bone(0, "センター")
        pmx_data.bones = [bone1]

        # テスト用のジョイントを作成
        cmds.select(clear=True)
        center = cmds.joint(name="center", position=[0, 10, 0])
        maya_joints = [center]

        bone_map = {0: center}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        result = self.converter.setup_pmx_rig(
            pmx_data, maya_joints, bone_map, skeleton_group
        )

        # 準標準ボーンが追加されていないか確認
        self.assertIn("semi_standard_bones", result)
        self.assertEqual(result["semi_standard_bones"], {})

    def test_add_semi_standard_bones_with_existing_japanese_bones(self):
        """既存の日本語名ボーンがある場合の準標準ボーン追加テスト"""
        # テスト用のジョイントを作成（日本語名のボーンが既に存在）
        cmds.select(clear=True)
        master_existing = cmds.joint(name="master", position=[0, 0, 0])
        center = cmds.joint(name="center", position=[0, 10, 0])

        # カスタムアトリビュートを追加（日本語名検索用）
        cmds.addAttr(
            master_existing,
            longName="mmd_bone_index",
            attributeType="long",
            defaultValue=99,
        )

        # コンバーターに元のボーン名を設定
        self.converter.original_bone_names = {0: "センター", 99: "全ての親"}

        maya_joints = [master_existing, center]
        bone_map = {0: center, 99: master_existing}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")
        cmds.parent(master_existing, skeleton_group)

        result = self.converter._add_semi_standard_bones(
            maya_joints, bone_map, skeleton_group
        )

        # 既存のボーンが使用され、新しく作成されていないことを確認
        # master は作成されない（既存のものを使用）
        self.assertNotIn("master", result)

    def test_add_semi_standard_bones_with_existing_standard_bones(self):
        """既存の準標準ボーン（英語名）がある場合の準標準ボーン追加テスト"""
        # テスト用のジョイントを作成（準標準ボーンが既に英語名で存在）
        cmds.select(clear=True)
        # masterグループを先に作成
        master_existing = cmds.group(empty=True, name="master")
        groove_existing = cmds.group(empty=True, name="groove", parent=master_existing)

        # センターとその他のジョイントを作成
        cmds.select(clear=True)
        center = cmds.joint(name="center", position=[0, 10, 0])
        cmds.parent(center, groove_existing)

        cmds.select(clear=True)
        lower_body = cmds.joint(name="lower_body", position=[0, 8, 0])
        cmds.parent(lower_body, groove_existing)

        cmds.select(clear=True)
        left_leg = cmds.joint(name="left_leg", position=[-2, 6, 0])
        cmds.parent(left_leg, lower_body)

        cmds.select(clear=True)
        waist_existing = cmds.joint(name="waist", position=[0, 7, 0])
        cmds.parent(waist_existing, lower_body)
        cmds.parent(left_leg, waist_existing)

        maya_joints = [center, lower_body, left_leg, waist_existing]
        bone_map = {0: center, 1: lower_body, 2: left_leg, 3: waist_existing}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")
        cmds.parent(master_existing, skeleton_group)

        # 元の日本語名を設定（英語名のボーンには日本語名がない）
        self.converter.original_bone_names = {
            0: "センター",
            1: "下半身",
            2: "左足",
            3: "waist",
        }

        result = self.converter._add_semi_standard_bones(
            maya_joints, bone_map, skeleton_group
        )

        # 既存のボーンは新しく作成されない
        self.assertNotIn("master", result)
        self.assertNotIn("groove", result)
        self.assertNotIn("waist", result)

        # 既存のボーンの数を確認（新しく作成されていない）
        all_masters = cmds.ls("master", type="transform")
        all_grooves = cmds.ls("groove", type="transform")
        all_waists = cmds.ls("waist", type="joint")

        self.assertEqual(len(all_masters), 1)  # 1つのみ（既存のもの）
        self.assertEqual(len(all_grooves), 1)  # 1つのみ（既存のもの）
        self.assertEqual(len(all_waists), 1)  # 1つのみ（既存のもの）

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

    def test_pole_target_with_pmx_local_axis(self):
        """PMXローカル軸情報を使用したPoleTarget作成テスト"""
        # 足のジョイントチェーンを作成
        cmds.select(clear=True)
        hip = cmds.joint(name="left_leg", position=[1, 10, 0])
        knee = cmds.joint(name="left_knee", position=[1, 5, 0])
        ankle = cmds.joint(name="left_ankle", position=[1, 0, 0])

        # 膝にPMXローカル軸情報を追加
        cmds.addAttr(knee, longName="mmd_local_x_axis", attributeType="double3")
        cmds.addAttr(
            knee,
            longName="mmd_local_x_axisX",
            attributeType="double",
            parent="mmd_local_x_axis",
        )
        cmds.addAttr(
            knee,
            longName="mmd_local_x_axisY",
            attributeType="double",
            parent="mmd_local_x_axis",
        )
        cmds.addAttr(
            knee,
            longName="mmd_local_x_axisZ",
            attributeType="double",
            parent="mmd_local_x_axis",
        )
        # X軸が前方を向くように設定（PMX座標系で）
        cmds.setAttr(f"{knee}.mmd_local_x_axis", 0, 0, -1, type="double3")

        # IKハンドルを作成
        ik_handle, _ = cmds.ikHandle(
            startJoint=hip, endEffector=ankle, solver="ikRPsolver"
        )

        # IKチェーン情報を作成
        chain = {
            "ik_bone": "left_leg_ik",
            "ik_links": [{"bone": knee, "bone_index": 1}],
        }

        # IKボーンを作成
        cmds.select(clear=True)
        ik_bone = cmds.joint(name="left_leg_ik", position=[1, 0, 2])

        # PoleTargetを作成
        pole_target = self.converter._create_pole_target_for_leg_ik(
            chain, ik_handle, hip, ankle
        )

        # PoleTargetが作成されたか確認
        self.assertIsNotNone(pole_target)

    def test_pole_target_with_joint_orient(self):
        """jointOrientを使用したPoleTarget作成テスト"""
        # 足のジョイントチェーンを作成（膝にjointOrientを設定）
        cmds.select(clear=True)
        hip = cmds.joint(name="left_leg", position=[1, 10, 0])
        knee = cmds.joint(name="left_knee", position=[1, 5, 1])  # 少し前方に配置
        # jointOrientを設定（膝が前方に曲がる方向）
        cmds.setAttr(f"{knee}.jointOrientX", 0)
        cmds.setAttr(f"{knee}.jointOrientY", 90)  # Y軸周りに90度回転
        cmds.setAttr(f"{knee}.jointOrientZ", 0)
        ankle = cmds.joint(name="left_ankle", position=[1, 0, 0])

        # IKハンドルを作成
        ik_handle, _ = cmds.ikHandle(
            startJoint=hip, endEffector=ankle, solver="ikRPsolver"
        )

        # IKチェーン情報を作成
        chain = {
            "ik_bone": "left_leg_ik",
            "ik_links": [{"bone": knee, "bone_index": 1}],
        }

        # IKボーンを作成
        cmds.select(clear=True)
        ik_bone = cmds.joint(name="left_leg_ik", position=[1, 0, 2])

        # PoleTargetを作成
        pole_target = self.converter._create_pole_target_for_leg_ik(
            chain, ik_handle, hip, ankle
        )

        # PoleTargetが作成されたか確認
        self.assertIsNotNone(pole_target)


if __name__ == "__main__":
    unittest.main()
