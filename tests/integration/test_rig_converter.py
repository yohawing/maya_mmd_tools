import json
import math
import unittest
from unittest.mock import MagicMock, Mock
import maya.cmds as cmds

from mmd_tools.converters.rig_converter import RigConverter
from mmd_tools.converters.native_rig_builder import RigManifest
from mmd_tools.core.constants import (
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.settings import settings
from mmd_tools.io.pmx_importer import import_pmx_file
from tests.common.test_fixture_provider import TestFixtureProvider


class TestRigConverterMaya(unittest.TestCase):
    """RigConverterクラスのMaya環境でのユニットテスト"""

    def setUp(self):
        """テストごとのセットアップ"""
        # 新しいシーンを作成
        cmds.file(new=True, force=True)
        self.converter = RigConverter()
        # 設定を保存しておく
        self.original_settings = settings.get("import.rig.add_semi_standard_bones", False)
        # TestFixtureProviderを初期化
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """テストごとのクリーンアップ"""
        # シーンをクリア
        cmds.file(new=True, force=True)
        # 設定を元に戻す
        settings.set("import.rig.add_semi_standard_bones", self.original_settings)
        self.fixture_provider.cleanup_temp_files()

    def _create_mock_pmx_bone(self, index, name="TestBone", parent_index=-1, position=(0, 0, 0), bone_flag=0):
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
        bone.grant_parent_bone_index = 0
        bone.grant_rate = 1.0
        bone.x_axis_direction = (1, 0, 0)
        bone.z_axis_direction = (0, 0, 1)
        bone.ik_target_bone_index = 0
        bone.ik_loop_count = 10
        bone.ik_limit_angle = 1.0
        bone.ik_links = []
        bone.transform_layer = 0  # デフォルトの変形階層

        return bone

    def _create_mock_ik_link(self, bone_index, angle_limit=False, limit_min=None, limit_max=None):
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

    def test_native_ik_node_uses_parent_first_slots_for_out_of_order_manifest(self):
        """out-of-order PMX index でも mmdCcdIk node が cycle なしで評価できる。"""
        cmds.select(clear=True)
        root = cmds.joint(name="out_of_order_root", position=[0.0, 0.0, 0.0])
        cmds.select(root)
        parent = cmds.joint(name="out_of_order_parent", position=[1.0, 0.0, 0.0])
        cmds.select(parent)
        link = cmds.joint(name="out_of_order_link", position=[2.0, 0.0, 0.0])
        cmds.select(parent)
        controller = cmds.joint(name="out_of_order_controller", position=[1.0, 1.0, 0.0])
        maya_joints = [root, link, controller, parent]
        manifest = RigManifest({
            "boneCount": 4,
            "bones": [
                {"parentIndex": -1, "restPosition": [0.0, 0.0, 0.0]},
                {"parentIndex": 3, "restPosition": [2.0, 0.0, 0.0]},
                {"parentIndex": 3, "restPosition": [1.0, 1.0, 0.0]},
                {"parentIndex": 0, "restPosition": [1.0, 0.0, 0.0]},
            ],
            "ikChains": [{
                "controllerBoneIndex": 2,
                "targetBoneIndex": 1,
                "links": [{"boneIndex": 1}],
                "iterationCount": 4,
                "limitAngle": 1.0,
            }],
        })

        self.converter.logger = MagicMock()
        nodes = self.converter._create_ik_nodes_from_manifest(manifest, maya_joints)

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        chain = json.loads(cmds.getAttr(f"{node}.chainJson"))
        self.assertEqual(
            [bone["parent_slot"] for bone in chain["bones"]], [-1, 0, 1, 1]
        )
        self.assertEqual(chain["targetBoneSlot"], 2)
        self.assertEqual(chain["links"][0]["bone_slot"], 2)
        self.assertEqual(
            cmds.listConnections(f"{link}.rotate", source=True, destination=False, plugs=True),
            [f"{node}.outputRotate[0]"],
        )
        self.assertEqual(
            cmds.listConnections(f"{node}.goalWorldMatrix", source=True, destination=False),
            [controller],
        )
        output_rotate = cmds.getAttr(f"{node}.outputRotate[0]")
        self.assertEqual(len(output_rotate[0]), 3)
        self.assertFalse(
            cmds.attributeQuery("mmd_ik_controller_visual", node=controller, exists=True)
        )
        self.assertEqual(
            cmds.listRelatives(controller, shapes=True, type="nurbsCurve") or [], []
        )

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_ik_detail = f"mmdCcdIk node '{node}': controller={controller}, 1 links"
        self.assertIn(expected_ik_detail, debug_messages)
        self.assertNotIn(expected_ik_detail, info_messages)

    def test_native_ik_link_preserves_rotation_append_as_solver_input(self):
        """回転付与先が IK link でも append を pre-IK 入力へ引き継ぐ。"""
        cmds.select(clear=True)
        root = cmds.joint(name="append_ik_root", position=[0.0, 0.0, 0.0])
        cmds.select(root)
        source = cmds.joint(name="append_ik_source", position=[0.0, 1.0, 0.0])
        cmds.select(source)
        link = cmds.joint(name="append_ik_link", position=[1.0, 1.0, 0.0])
        cmds.select(link)
        target = cmds.joint(name="append_ik_target", position=[2.0, 1.0, 0.0])
        cmds.select(root)
        controller = cmds.joint(name="append_ik_controller", position=[2.0, 2.0, 0.0])
        maya_joints = [root, source, link, target, controller]
        manifest = RigManifest({
            "boneCount": 5,
            "bones": [
                {"parentIndex": -1, "restPosition": [0.0, 0.0, 0.0]},
                {"parentIndex": 0, "restPosition": [0.0, 1.0, 0.0]},
                {"parentIndex": 1, "restPosition": [1.0, 1.0, 0.0]},
                {"parentIndex": 2, "restPosition": [2.0, 1.0, 0.0]},
                {"parentIndex": 0, "restPosition": [2.0, 2.0, 0.0]},
            ],
            "grants": [{
                "targetBoneIndex": 2,
                "sourceBoneIndex": 1,
                "ratio": 0.5,
                "affectRotation": True,
                "affectTranslation": False,
                "local": False,
            }],
            "ikChains": [{
                "controllerBoneIndex": 4,
                "targetBoneIndex": 3,
                "links": [{"boneIndex": 2}],
                "iterationCount": 4,
                "limitAngle": 1.0,
            }],
        })

        accepted_grants = self.converter._accepted_native_grants(manifest, maya_joints)
        append_nodes = self.converter._create_append_nodes_from_manifest(
            manifest, maya_joints, accepted_grants=accepted_grants
        )
        ik_nodes = self.converter._create_ik_nodes_from_manifest(
            manifest, maya_joints, accepted_grants=accepted_grants
        )

        self.assertEqual(len(append_nodes), 1)
        self.assertEqual(len(ik_nodes), 1)
        append_node = append_nodes[0]
        ik_node = ik_nodes[0]
        chain = json.loads(cmds.getAttr(f"{ik_node}.chainJson"))
        link_slot = chain["links"][0]["bone_slot"]
        self.assertEqual(
            cmds.listConnections(
                f"{ik_node}.inputRotate[{link_slot}]",
                source=True,
                destination=False,
                plugs=True,
            ),
            [f"{append_node}.outputRotate"],
        )
        self.assertEqual(
            cmds.listConnections(f"{link}.rotate", source=True, destination=False, plugs=True),
            [f"{ik_node}.outputRotate[0]"],
        )

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

        self.converter.logger = MagicMock()
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

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_handle_detail = (
            f"Created IK handle '{handle_info['ik_handle']}' "
            f"({joints[1]} -> {joints[3]})"
        )
        self.assertIn(expected_handle_detail, debug_messages)
        self.assertNotIn(expected_handle_detail, info_messages)

    def test_import_fix_axis_bones(self):
        """fix_axis fixture のボーンをインポートできることを確認する。"""
        # テスト用のジョイントロード
        pmx_data, pmx_path = self.fixture_provider.load_pmx_data("test_fix_axis")

        import_pmx_file(pmx_data, pmx_path, scale=1.0)

        # インポートできているか確認
        self.assertTrue(cmds.objExists("test_fix_axis_root"))

        # ボーンの数を確認
        maya_joints = cmds.ls(type="joint")
        self.assertEqual(len(maya_joints), len(pmx_data.bones))

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
        result = self.converter._find_joint_by_name(maya_joints, ["存在しない", "notfound"])
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

        self.converter.logger = MagicMock()
        result = self.converter._add_semi_standard_bones(maya_joints, bone_map, skeleton_group)

        # 追加されたボーンの確認
        self.assertIn("master", result)
        self.assertIn("groove", result)
        self.assertIn("waist", result)

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

        # creation-path detail logs at DEBUG, not INFO
        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_details = [
            f"Added master bone: {result['master']}",
            f"Added groove bone: {result['groove']}",
            f"Added waist bone: {result['waist']}",
        ]
        for detail in expected_details:
            self.assertIn(detail, debug_messages)
            self.assertNotIn(detail, info_messages)

    def test_given_bones_with_pmx(self):
        """実際のPMXデータを使用した付与ボーンのテスト（構造検証）。

        rest pose の位置・付与属性・付与 constraint の設定を検証する。
        VMD 適用後のフレーム単位の厳密な変換値は MMD モーションオラクルでの
        検証が必要なため、本テストでは扱わない（follow-up）。
        """

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
                self.assertTrue(cmds.attributeQuery(ATTR_MMD_GRANT_PARENT_INDEX, node=joint, exists=True))
                self.assertTrue(cmds.attributeQuery(ATTR_MMD_GRANT_RATE, node=joint, exists=True))

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

        # 付与ボーン B/C/D の joint に MMD 付与設定が構造的に作られていることを確認する。
        # PMXファイル "test_given_bone" には以下のボーンがある想定:
        #   A: 通常ボーン / B: Aから付与 / C: ローカル付与 / D: 多重付与
        # （フレーム単位の厳密な変換値は MMD モーションオラクルでの検証が必要なため、
        #   ここでは付与関係が constraint または native DG ノードとして作られていることを確認する。）
        for grant_bone_name in ["B", "C", "D"]:
            joints = cmds.ls(grant_bone_name, type="joint")
            self.assertTrue(joints, f"付与ボーン {grant_bone_name} が見つかりません")
            connected = cmds.listConnections(joints[0], type="constraint") or []
            grant_marked = [
                c
                for c in connected
                if cmds.attributeQuery("mmd_grant_constraint", node=c, exists=True)
                and cmds.getAttr(f"{c}.mmd_grant_constraint")
            ]
            native_append_nodes = cmds.listConnections(
                f"{joints[0]}.rotate",
                source=True,
                destination=False,
                type="mmdAppend",
            ) or []
            native_grant_nodes = [
                n
                for n in native_append_nodes
                if cmds.attributeQuery("mmd_grant_node", node=n, exists=True) and cmds.getAttr(f"{n}.mmd_grant_node")
            ]
            self.assertTrue(
                grant_marked or native_grant_nodes,
                f"付与ボーン {grant_bone_name} に MMD 付与設定が作られていません",
            )

    def test_setup_given_parent_bones_rotation(self):
        """回転付与ボーン(部分付与)の設定テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        bone = self._create_mock_pmx_bone(0, "TestBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
        bone.grant_parent_bone_index = 1
        bone.grant_rate = 0.5

        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        bones = [bone, parent_bone]
        maya_joints = [child_joint, parent_joint]

        self.converter.logger = MagicMock()
        constraints = self.converter._setup_grant_bones(bones, maya_joints)

        self.assertEqual(len(constraints), 1)
        constraint = constraints[0]
        self.assertEqual(cmds.nodeType(constraint), "orientConstraint")
        self.assertTrue(cmds.getAttr(f"{constraint}.mmd_grant_constraint"))
        # 部分付与(rate=0.5)では中立リファレンスノードが作成される
        self.assertTrue(cmds.objExists("mmd_grant_reference"))

        # reference-path detail logs at DEBUG, not INFO
        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_ref_detail = "Added reference node for rotation append: mmd_grant_reference"
        self.assertIn(expected_ref_detail, debug_messages)
        self.assertNotIn(expected_ref_detail, info_messages)

    def test_setup_grant_bones_without_master_reference(self):
        """masterが存在しないPMXでも部分回転付与を設定できることを確認"""
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        child_bone = self._create_mock_pmx_bone(
            0,
            "ChildBone",
            bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE,
        )
        child_bone.grant_parent_bone_index = 1
        child_bone.grant_rate = 0.5

        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        constraints = self.converter._setup_grant_bones(
            [child_bone, parent_bone],
            [child_joint, parent_joint],
        )

        self.assertEqual(len(constraints), 1)
        self.assertFalse(cmds.objExists("master"))
        self.assertTrue(cmds.objExists("mmd_grant_reference"))

    def test_setup_given_parent_bones_local_given(self):
        """ローカル付与ボーンの設定テスト（実際のMaya環境）"""
        # テスト用のジョイントを作成
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        # ローカル付与フラグを含むボーンを作成
        bone = self._create_mock_pmx_bone(0, "TestBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.LOCAL)
        bone.grant_parent_bone_index = 1
        bone.grant_rate = 0.5

        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        bones = [bone, parent_bone]
        maya_joints = [child_joint, parent_joint]

        constraints = self.converter._setup_grant_bones(bones, maya_joints)

        self.assertEqual(len(constraints), 1)
        constraint = constraints[0]
        self.assertEqual(cmds.nodeType(constraint), "orientConstraint")
        self.assertTrue(cmds.getAttr(f"{constraint}.mmd_grant_constraint"))
        # ローカル+部分付与でも中立リファレンスノードが作成される
        self.assertTrue(cmds.objExists("mmd_grant_reference"))

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
                bone = self._create_mock_pmx_bone(i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
                bone.grant_parent_bone_index = 3  # joint3を親にする
                bone.grant_rate = 0.5
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
                bone = self._create_mock_pmx_bone(i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
                bone.grant_parent_bone_index = 1
                bone.grant_rate = 0.5
            elif i == 1:
                # joint1: joint2から付与を受ける（多重付与）
                bone = self._create_mock_pmx_bone(i, f"Bone{i}", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
                bone.grant_parent_bone_index = 2
                bone.grant_rate = 0.7
            else:
                # 通常のボーン
                bone = Mock()
                bone.get_flag = Mock(return_value=False)
                bone.transform_layer = 0  # Mockオブジェクトに属性を追加
            bones.append(bone)

        constraints = self.converter._setup_grant_bones(bones, joints)

        # 2つの付与関係が設定される
        self.assertEqual(len(constraints), 2)

    def test_partial_rotation_grant_uses_weighted_orient_constraint(self):
        """部分回転付与(rate=0.5)が重み付き orientConstraint で作成されることを確認。

        旧実装の decomposeMatrix/multiplyDivide ノードネットワークは撤去され、
        現在は [中立リファレンス, 付与親] を重み [1-rate, rate] で合成する
        orientConstraint を使う。
        """
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        child_bone = self._create_mock_pmx_bone(0, "ChildBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
        child_bone.grant_parent_bone_index = 1
        child_bone.grant_rate = 0.5
        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        self.converter.logger = MagicMock()
        constraints = self.converter._setup_grant_bones([child_bone, parent_bone], [child_joint, parent_joint])

        self.assertEqual(len(constraints), 1)
        constraint = constraints[0]
        self.assertEqual(cmds.nodeType(constraint), "orientConstraint")
        # MMD付与constraintとして印付けされている
        self.assertTrue(cmds.attributeQuery("mmd_grant_constraint", node=constraint, exists=True))
        self.assertTrue(cmds.getAttr(f"{constraint}.mmd_grant_constraint"))
        # 部分付与は2ターゲット、重みの合計は 1.0
        weights = cmds.orientConstraint(constraint, query=True, weightAliasList=True)
        self.assertEqual(len(weights), 2)
        weight_sum = sum(cmds.getAttr(f"{constraint}.{w}") for w in weights)
        self.assertAlmostEqual(weight_sum, 1.0, places=4)

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_rotation_detail = (
            f"Configured rotation append (global append): "
            f"{child_joint} <- {parent_joint} (rate=0.5, layer=0)"
        )
        self.assertIn(expected_rotation_detail, debug_messages)
        self.assertNotIn(expected_rotation_detail, info_messages)

    def test_translation_grant_detail_logs_at_debug_not_info(self):
        """移動付与の成功詳細は debug に出し、info には出さない。"""
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        child_bone = self._create_mock_pmx_bone(0, "ChildBone", bone_flag=PmxBoneFlag.GRANT_PARENT_MOVE)
        child_bone.grant_parent_bone_index = 1
        child_bone.grant_rate = 0.5
        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        self.converter.logger = MagicMock()
        constraints = self.converter._setup_grant_bones(
            [child_bone, parent_bone],
            [child_joint, parent_joint],
        )

        self.assertEqual(len(constraints), 1)
        self.assertEqual(cmds.nodeType(constraints[0]), "pointConstraint")

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_translation_detail = (
            f"Configured translation append (global append): "
            f"{child_joint} <- {parent_joint} (rate=0.5, layer=0)"
        )
        self.assertIn(expected_translation_detail, debug_messages)
        self.assertNotIn(expected_translation_detail, info_messages)

    def test_negative_rate_rotation_grant_uses_negative_weight(self):
        """負の付与率(-0.5)が単一ターゲットの負ウェイト orientConstraint になることを確認。

        rate==-1 以外の負値は付与親1ターゲットに負の weight を与える経路を通る。
        """
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="parent_joint", position=[0, 0, 0])
        cmds.select(clear=True)
        child_joint = cmds.joint(name="child_joint", position=[5, 0, 0])

        child_bone = self._create_mock_pmx_bone(0, "ChildBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
        child_bone.grant_parent_bone_index = 1
        child_bone.grant_rate = -0.5
        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        constraints = self.converter._setup_grant_bones([child_bone, parent_bone], [child_joint, parent_joint])

        self.assertEqual(len(constraints), 1)
        constraint = constraints[0]
        self.assertEqual(cmds.nodeType(constraint), "orientConstraint")
        self.assertTrue(cmds.getAttr(f"{constraint}.mmd_grant_constraint"))
        weights = cmds.orientConstraint(constraint, query=True, weightAliasList=True)
        self.assertEqual(len(weights), 1)
        self.assertAlmostEqual(cmds.getAttr(f"{constraint}.{weights[0]}"), -0.5, places=4)

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

        result = self.converter.setup_pmx_rig(pmx_data, maya_joints, bone_map, skeleton_group)

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

        result = self.converter.setup_pmx_rig(pmx_data, maya_joints, bone_map, skeleton_group)

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

        self.converter.logger = MagicMock()
        result = self.converter._add_semi_standard_bones(maya_joints, bone_map, skeleton_group)

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

        self.converter.logger = MagicMock()
        result = self.converter._add_semi_standard_bones(maya_joints, bone_map, skeleton_group)

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

        debug_messages = [
            call[0][0] for call in self.converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in self.converter.logger.info.call_args_list if call[0]
        ]
        expected_details = [
            f"Using existing master bone: {master_existing}",
            f"Using existing groove bone: {groove_existing}",
            f"Using existing waist bone: {waist_existing}",
        ]
        for detail in expected_details:
            self.assertIn(detail, debug_messages)
            self.assertNotIn(detail, info_messages)

    def test_setup_pmx_rig_integration(self):
        """PMXリグセットアップの統合テスト（現行 setup_pmx_rig の挙動）。

        現行の setup_pmx_rig は付与ボーンの constraint 設定を行い、PMX の IK ハンドル
        生成は無効化されている（IK は別経路で扱う）。戻り値の構造と付与 constraint の
        作成を検証する。
        """
        # テスト用のジョイントとグループを作成（index0=child, index1=parent）
        cmds.select(clear=True)
        child_joint = cmds.joint(name="joint0", position=[5, 0, 0])
        cmds.select(clear=True)
        parent_joint = cmds.joint(name="joint1", position=[0, 0, 0])
        maya_joints = [child_joint, parent_joint]

        # 付与ボーン1つを含む PMX データ
        grant_bone = self._create_mock_pmx_bone(0, "ChildBone", bone_flag=PmxBoneFlag.GRANT_PARENT_ROTATE)
        grant_bone.grant_parent_bone_index = 1
        grant_bone.grant_rate = 1.0
        parent_bone = self._create_mock_pmx_bone(1, "ParentBone")

        pmx_data = Mock()
        pmx_data.bones = [grant_bone, parent_bone]

        bone_map = {0: child_joint, 1: parent_joint}
        skeleton_group = cmds.group(empty=True, name="skeleton_grp")

        result = self.converter.setup_pmx_rig(pmx_data, maya_joints, bone_map, skeleton_group)

        # 戻り値の構造を確認
        self.assertIn("constraints", result)
        self.assertIn("ik_handles", result)
        self.assertIn("semi_standard_bones", result)
        # 付与関係が1つ設定され、PMX IK ハンドルは作成されない
        self.assertEqual(len(result["constraints"]), 1)
        self.assertEqual(result["ik_handles"], [])

if __name__ == "__main__":
    unittest.main()
