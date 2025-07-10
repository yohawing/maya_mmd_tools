import maya.cmds as cmds
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.converters.physics_converter import PhysicsConverter
from mmd_tools.core.pmd_data.rigid_body import PmdRigidBody
from mmd_tools.core.pmd_data.joint import PmdJoint


class TestPhysicsConverter(MayaTestBase):
    def setUp(self):
        super().setUp()
        # 毎回新しいシーンを作成
        cmds.file(new=True, f=True)
        self.converter = PhysicsConverter()

    def tearDown(self):
        super().tearDown()
        # シーンのクリーンアップ
        cmds.file(new=True, f=True)

    def test_init(self):
        """PhysicsConverterが正しく初期化されることをテストする。"""
        # デフォルト設定の確認
        self.assertTrue(self.converter.settings["enable_hair_physics"])
        self.assertTrue(self.converter.settings["enable_cloth_physics"])
        self.assertEqual(self.converter.settings["simulation_quality"], "medium")
        self.assertTrue(self.converter.settings["auto_detect_type"])

        # 初期状態の確認
        self.assertIsNone(self.converter.nucleus_solver)
        self.assertEqual(len(self.converter.created_ncloth_nodes), 0)
        self.assertEqual(len(self.converter.created_nrigid_nodes), 0)
        self.assertEqual(len(self.converter.created_constraint_nodes), 0)

    def test_init_with_custom_settings(self):
        """カスタム設定でPhysicsConverterが初期化されることをテストする。"""
        custom_settings = {
            "enable_hair_physics": False,
            "simulation_quality": "high",
            "time_scale": 2.0,
        }
        converter = PhysicsConverter(settings=custom_settings)

        # カスタム設定が反映されていることを確認
        self.assertFalse(converter.settings["enable_hair_physics"])
        self.assertEqual(converter.settings["simulation_quality"], "high")
        self.assertEqual(converter.settings["time_scale"], 2.0)

        # デフォルト設定が保持されていることを確認
        self.assertTrue(converter.settings["enable_cloth_physics"])
        self.assertTrue(converter.settings["auto_detect_type"])

    def test_convert_pmd_physics_empty_data(self):
        """空のPMDデータでconvert_pmd_physicsが動作することをテストする。"""

        # 空のPMDデータを作成（rigid_bodies、joints、bonesを持つオブジェクト）
        class MockPmdData:
            def __init__(self):
                self.rigid_bodies = []
                self.joints = []
                self.bones = []

        pmd_data = MockPmdData()
        bone_joints = {}

        # 変換を実行
        ncloth_nodes, constraint_nodes = self.converter.convert_pmd_physics(
            pmd_data, bone_joints
        )

        # 結果の確認
        self.assertEqual(len(ncloth_nodes), 0)
        self.assertEqual(len(constraint_nodes), 0)

        # Nucleusソルバーが作成されていることを確認
        self.assertIsNotNone(self.converter.nucleus_solver)
        self.assertTrue(cmds.objExists(self.converter.nucleus_solver))

    def test_nucleus_solver_creation(self):
        """Nucleusソルバーが正しく作成されることをテストする。"""
        # ソルバーがまだ存在しないことを確認
        self.assertIsNone(self.converter.nucleus_solver)

        # ソルバーを作成
        solver_name = self.converter._ensure_nucleus_solver()

        # ソルバーが作成されたことを確認
        self.assertIsNotNone(solver_name)
        self.assertTrue(cmds.objExists(solver_name))
        self.assertEqual(cmds.nodeType(solver_name), "nucleus")

        # 利用可能な属性を確認（デバッグ用）
        all_attrs = cmds.listAttr(solver_name)
        gravity_attrs = [attr for attr in all_attrs if "gravity" in attr.lower()]
        print(f"Gravity related attributes: {gravity_attrs}")

        # 設定が適用されていることを確認
        # 重力方向の属性をチェック
        gravity_direction = cmds.getAttr(f"{solver_name}.gravityDirection")
        self.assertEqual(gravity_direction[0][0], 0)
        self.assertEqual(gravity_direction[0][1], -1)  # Y軸負方向（下向き）
        self.assertEqual(gravity_direction[0][2], 0)

        # 重力の大きさをチェック
        self.assertAlmostEqual(cmds.getAttr(f"{solver_name}.gravity"), 9.8, places=5)

        self.assertEqual(cmds.getAttr(f"{solver_name}.startFrame"), 1)
        self.assertAlmostEqual(cmds.getAttr(f"{solver_name}.timeScale"), 1.0, places=5)

    def test_analyze_physics_type(self):
        """剛体の物理タイプ分析が正しく動作することをテストする。"""
        # 髪タイプの剛体を作成
        hair_rb = PmdRigidBody()
        hair_rb.name = "前髪"
        hair_rb.shape_type = 1  # カプセル
        self.assertEqual(
            self.converter._analyze_physics_type(hair_rb),
            PhysicsConverter.PHYSICS_TYPE_HAIR,
        )

        # 布タイプの剛体を作成
        cloth_rb = PmdRigidBody()
        cloth_rb.name = "スカート"
        cloth_rb.shape_type = 0  # 箱
        self.assertEqual(
            self.converter._analyze_physics_type(cloth_rb),
            PhysicsConverter.PHYSICS_TYPE_CLOTH,
        )

        # デフォルト（剛体）タイプの剛体を作成
        rigid_rb = PmdRigidBody()
        rigid_rb.name = "その他"
        rigid_rb.shape_type = 1
        self.assertEqual(
            self.converter._analyze_physics_type(rigid_rb),
            PhysicsConverter.PHYSICS_TYPE_HAIR,
        )  # カプセルなので髪と判定

    def test_map_physics_parameters(self):
        """MMDパラメータがnClothパラメータに正しくマッピングされることをテストする。"""
        mmd_params = {
            "mass": 1.0,
            "velocity_attenuation": 0.5,
            "rotation_attenuation": 0.3,
            "friction": 0.2,
            "elasticity": 0.8,
        }

        ncloth_params = self.converter._map_physics_parameters(mmd_params)

        # マッピングの確認
        self.assertAlmostEqual(ncloth_params["thickness"], 0.1)  # mass * 0.1
        self.assertAlmostEqual(ncloth_params["damp"], 0.5)
        self.assertAlmostEqual(
            ncloth_params["bendResistance"], 3.0
        )  # rotation_attenuation * 10.0
        self.assertAlmostEqual(ncloth_params["friction"], 0.2)
        self.assertAlmostEqual(ncloth_params["bounce"], 0.8)
