"""
PhysicsConverter の統合テスト。

Bullet プラグインがロード可能な環境では bulletRigidBodyShape /
bulletRigidBodyConstraintShape の作成とカスタムアトリビュートを検証する。
Bullet が利用不可の場合はテストをスキップする。
"""

from maya import cmds

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.converters import PhysicsConverter
from mmd_tools.io.pmx_exporter import PmxExporter
from tests.common.maya_test_base import MayaTestBase


class TestPhysicsConverter(MayaTestBase):
    """PhysicsConverter 統合テスト"""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.converter = PhysicsConverter()

    def tearDown(self):
        cmds.file(new=True, force=True)
        super().tearDown()

    # ------------------------------------------------------------------
    # ヘルパー: テスト用の fake PMD / PMX rigid body / joint
    # ------------------------------------------------------------------

    @staticmethod
    def _make_fake_pmd_rigid_body(
        name="test_rb",
        bone_index=0,
        shape_type=0,
        size=(1.0, 1.0, 1.0),
        position=(0.0, 5.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        mass=1.0,
        velocity_attenuation=0.5,
        rotation_attenuation=0.5,
        friction=0.5,
        elasticity=0.3,
        physics_mode=1,
        collision_group=0,
        collision_mask=0xFFFF,
    ):
        """簡易 PMD 剛体モックを返す。"""
        rb = type("FakePmdRigidBody", (), {})()
        rb.name = name
        rb.bone_index = bone_index
        rb.shape_type = shape_type
        rb.size = size
        rb.position = position
        rb.rotation = rotation
        rb.mass = mass
        rb.velocity_attenuation = velocity_attenuation
        rb.rotation_attenuation = rotation_attenuation
        rb.friction = friction
        rb.elasticity = elasticity
        rb.physics_mode = physics_mode
        rb.collision_group = collision_group
        rb.collision_mask = collision_mask
        return rb

    @staticmethod
    def _make_fake_pmx_rigid_body(
        name="test_rb",
        related_bone_index=0,
        shape_type=0,
        size=(1.0, 1.0, 1.0),
        position=(0.0, 5.0, 0.0),
        rotation=(0.0, 0.0, 0.0),
        mass=1.0,
        velocity_attenuation=0.5,
        rotation_attenuation=0.5,
        friction=0.5,
        elasticity=0.3,
        physics_mode=1,
        group=0,
        collision_mask=0xFFFF,
    ):
        """簡易 PMX 剛体モックを返す。"""
        rb = type("FakePmxRigidBody", (), {})()
        rb.name = name
        rb.name_english = name
        rb.related_bone_index = related_bone_index
        rb.shape_type = shape_type
        rb.size = size
        rb.position = position
        rb.rotation = rotation
        rb.mass = mass
        rb.velocity_attenuation = velocity_attenuation
        rb.rotation_attenuation = rotation_attenuation
        rb.friction = friction
        rb.elasticity = elasticity
        rb.physics_mode = physics_mode
        rb.group = group
        rb.collision_mask = collision_mask
        return rb

    @staticmethod
    def _make_fake_pmd_joint(
        name="test_joint",
        rigid_body_index_a=0,
        rigid_body_index_b=1,
        position=(0.0, 5.0, 5.0),
        rotation=(0.0, 0.0, 0.0),
        translation_limit_min=(-1.0, -1.0, -1.0),
        translation_limit_max=(1.0, 1.0, 1.0),
        rotation_limit_min=(-15.0, -15.0, -15.0),
        rotation_limit_max=(15.0, 15.0, 15.0),
        spring_translation=(0.0, 0.0, 0.0),
        spring_rotation=(0.0, 0.0, 0.0),
    ):
        """簡易 PMD ジョイントモックを返す。"""
        j = type("FakePmdJoint", (), {})()
        j.name = name
        j.rigid_body_index_a = rigid_body_index_a
        j.rigid_body_index_b = rigid_body_index_b
        j.position = position
        j.rotation = rotation
        j.translation_limit_min = translation_limit_min
        j.translation_limit_max = translation_limit_max
        j.rotation_limit_min = rotation_limit_min
        j.rotation_limit_max = rotation_limit_max
        j.spring_translation = spring_translation
        j.spring_rotation = spring_rotation
        return j

    @staticmethod
    def _make_fake_pmx_joint(
        name="test_joint",
        joint_type=0,
        rigid_body_a_index=0,
        rigid_body_b_index=1,
        position=(0.0, 5.0, 5.0),
        rotation=(0.0, 0.0, 0.0),
        translation_limit_min=(-1.0, -1.0, -1.0),
        translation_limit_max=(1.0, 1.0, 1.0),
        rotation_limit_min=(-0.26, -0.26, -0.26),
        rotation_limit_max=(0.26, 0.26, 0.26),
        spring_translation=(0.0, 0.0, 0.0),
        spring_rotation=(0.0, 0.0, 0.0),
    ):
        """簡易 PMX ジョイントモックを返す。"""
        j = type("FakePmxJoint", (), {})()
        j.name = name
        j.name_english = name
        j.joint_type = joint_type
        j.rigid_body_a_index = rigid_body_a_index
        j.rigid_body_b_index = rigid_body_b_index
        j.position = position
        j.rotation = rotation
        j.translation_limit_min = translation_limit_min
        j.translation_limit_max = translation_limit_max
        j.rotation_limit_min = rotation_limit_min
        j.rotation_limit_max = rotation_limit_max
        j.spring_translation = spring_translation
        j.spring_rotation = spring_rotation
        return j

    @staticmethod
    def _make_fake_pmd_data(rigid_bodies, joints=None, bones=None):
        data = type("FakePmdData", (), {})()
        data.rigid_bodies = rigid_bodies
        data.joints = joints or []
        data.bones = bones or []
        return data

    @staticmethod
    def _make_fake_pmx_data(rigid_bodies, joints=None, bones=None):
        data = type("FakePmxData", (), {})()
        data.rigid_bodies = rigid_bodies
        data.joints = joints or []
        data.bones = bones or []
        return data

    @staticmethod
    def _make_fake_bone(name):
        """_create_bone_index_mapping() 用の簡易ボーンモックを返す。"""
        bone = type("FakeBone", (), {})()
        bone.get_name = lambda: name
        return bone

    # ------------------------------------------------------------------
    # テスト本体
    # ------------------------------------------------------------------

    def test_bullet_available_check(self):
        """is_bullet_available() が例外を出さず bool を返す。"""
        result = PhysicsConverter.is_bullet_available()
        self.assertIsInstance(result, bool)

    def _run_bullet_rigid_body_test(self, model_type, shape_type, expected_bullet_shape):
        """Bullet 剛体作成の共通テスト。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        bone_joints: dict = {}

        if model_type == "pmd":
            rb = self._make_fake_pmd_rigid_body(
                name="test_head", bone_index=0, shape_type=shape_type,
                size=(2.0, 2.0, 2.0), position=(0.0, 10.0, 0.0),
                physics_mode=1, mass=0.5,
            )
            data = self._make_fake_pmd_data(rigid_bodies=[rb])
        else:
            rb = self._make_fake_pmx_rigid_body(
                name="test_head", related_bone_index=0, shape_type=shape_type,
                size=(2.0, 2.0, 2.0), position=(0.0, 10.0, 0.0),
                physics_mode=1, mass=0.5,
            )
            data = self._make_fake_pmx_data(rigid_bodies=[rb])

        converter = PhysicsConverter({"create_physics_joints": False})
        if model_type == "pmd":
            rbs, cons = converter.convert_pmd_physics(data, bone_joints, root)
        else:
            rbs, cons = converter.convert_pmx_physics(data, bone_joints, root)

        # Bullet 経路が使われたか
        self.assertGreaterEqual(len(rbs), 1, "Bullet rigid body が作成されていない")
        rb_transform = rbs[0]

        # bulletRigidBodyShape の存在確認
        shapes = cmds.listRelatives(rb_transform, shapes=True, type="bulletRigidBodyShape") or []
        self.assertGreaterEqual(len(shapes), 1, "bulletRigidBodyShape が存在しない")
        shape = shapes[0]

        # colliderShapeType
        cst = cmds.getAttr(f"{shape}.colliderShapeType")
        self.assertEqual(cst, expected_bullet_shape,
                         f"colliderShapeType mismatch: expected {expected_bullet_shape}, got {cst}")

        # bodyType
        bt = cmds.getAttr(f"{shape}.bodyType")
        self.assertEqual(bt, 2, "PMD physics_mode=1 は bodyType=2(dynamic) のはず")

        # mass
        mass = cmds.getAttr(f"{shape}.mass")
        self.assertAlmostEqual(mass, 0.5, places=4, msg="mass mismatch")

        solver_connections = cmds.listConnections(f"{shape}.solverInitialized", source=True, destination=False) or []
        self.assertTrue(
            solver_connections,
            "bulletRigidBodyShape が bulletSolverShape に接続されていない",
        )

        solver_shapes = cmds.ls(type="bulletSolverShape") or []
        self.assertGreaterEqual(len(solver_shapes), 1, "bulletSolverShape が作成されていない")

        # カスタムアトリビュート
        self.assertTrue(cmds.attributeQuery("mmd_rigid_body_name", node=rb_transform, exists=True),
                        "mmd_rigid_body_name が存在しない")
        self.assertTrue(cmds.attributeQuery("mmd_rigid_body_index", node=rb_transform, exists=True),
                        "mmd_rigid_body_index が存在しない")
        self.assertTrue(cmds.attributeQuery("mmd_physics_mode", node=rb_transform, exists=True),
                        "mmd_physics_mode が存在しない")
        self.assertTrue(cmds.attributeQuery("mmd_collision_group", node=rb_transform, exists=True),
                        "mmd_collision_group が存在しない")
        self.assertTrue(cmds.attributeQuery("mmd_collision_mask", node=rb_transform, exists=True),
                        "mmd_collision_mask が存在しない")

    def test_pmx_mode2_rigid_body_stays_dynamic_bullet(self):
        """PMX physics_mode=2 は Bullet 上でも dynamic body として作成する。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb = self._make_fake_pmx_rigid_body(
            name="mode2_rb",
            related_bone_index=0,
            shape_type=0,
            size=(1.0, 1.0, 1.0),
            position=(0.0, 5.0, 0.0),
            physics_mode=2,
            mass=0.5,
        )
        data = self._make_fake_pmx_data(rigid_bodies=[rb])

        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, _ = converter.convert_pmx_physics(data, {}, root)

        self.assertGreaterEqual(len(rbs), 1, "Bullet rigid body が作成されていない")
        shapes = cmds.listRelatives(rbs[0], shapes=True, type="bulletRigidBodyShape") or []
        self.assertGreaterEqual(len(shapes), 1, "bulletRigidBodyShape が存在しない")
        self.assertEqual(
            cmds.getAttr(f"{shapes[0]}.bodyType"),
            2,
            "PMX physics_mode=2 は bodyType=2(dynamic) のはず",
        )
        self.assertEqual(cmds.getAttr(f"{rbs[0]}.mmd_physics_mode"), 2)

    def test_pmx_dynamic_rigid_body_drives_transform_from_bullet_preview(self):
        """PMX dynamic 剛体は Bullet solved output で transform が動く。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb = self._make_fake_pmx_rigid_body(
            name="falling_preview_rb",
            related_bone_index=-1,
            shape_type=0,
            size=(0.5, 0.5, 0.5),
            position=(0.0, 10.0, 0.0),
            physics_mode=1,
            mass=1.0,
            velocity_attenuation=0.0,
            rotation_attenuation=0.0,
        )
        data = self._make_fake_pmx_data(rigid_bodies=[rb])

        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, _ = converter.convert_pmx_physics(data, {}, root)
        self.assertGreaterEqual(len(rbs), 1)

        self.assertTrue(cmds.attributeQuery("isDrivenBySimulation", node=rbs[0], exists=True))
        self.assertTrue(cmds.getAttr(f"{rbs[0]}.isDrivenBySimulation"))
        pair_blends = cmds.listConnections(f"{rbs[0]}.translateY", source=True, destination=False, type="pairBlend") or []
        self.assertTrue(pair_blends, "Bullet solved output が transform.translate に接続されていない")

        cmds.playbackOptions(min=1, max=30, animationStartTime=1, animationEndTime=30)
        cmds.currentTime(1, edit=True)
        start_y = cmds.xform(rbs[0], query=True, worldSpace=True, translation=True)[1]
        cmds.currentTime(30, edit=True)
        end_y = cmds.xform(rbs[0], query=True, worldSpace=True, translation=True)[1]
        self.assertLess(end_y, start_y - 0.1)

    def test_pmx_static_rigid_body_follows_related_bone(self):
        """PMX physics_mode=0 は関連ボーンの transform に追従する。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        cmds.select(clear=True)
        joint = cmds.joint(name="follow_bone", position=(0.0, 5.0, 0.0))
        cmds.parent(joint, root)

        rb = self._make_fake_pmx_rigid_body(
            name="static_follow",
            related_bone_index=0,
            shape_type=0,
            position=(0.0, 5.0, 0.0),
            physics_mode=0,
        )
        data = self._make_fake_pmx_data(
            rigid_bodies=[rb],
            bones=[self._make_fake_bone("follow_bone")],
        )

        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, _ = converter.convert_pmx_physics(data, {"follow_bone": joint}, root)

        self.assertGreaterEqual(len(rbs), 1)
        constraints = cmds.listConnections(rbs[0], source=True, destination=False, type="parentConstraint") or []
        self.assertTrue(constraints, "physics_mode=0 剛体に parentConstraint が作成されていない")

        before = cmds.xform(rbs[0], query=True, worldSpace=True, translation=True)
        cmds.xform(joint, worldSpace=True, translation=(0.0, 8.0, 0.0))
        after = cmds.xform(rbs[0], query=True, worldSpace=True, translation=True)
        self.assertAlmostEqual(after[1] - before[1], 3.0, places=4)

    def test_pmd_rigid_body_sphere_bullet(self):
        """PMD sphere (shape_type=0) → Bullet colliderShapeType=2(sphere)"""
        self._run_bullet_rigid_body_test("pmd", 0, 2)

    def test_pmd_rigid_body_box_bullet(self):
        """PMD box (shape_type=1) → Bullet colliderShapeType=1(box)"""
        self._run_bullet_rigid_body_test("pmd", 1, 1)

    def test_pmd_rigid_body_capsule_bullet(self):
        """PMD capsule (shape_type=2) → Bullet colliderShapeType=3(capsule)"""
        self._run_bullet_rigid_body_test("pmd", 2, 3)

    def test_pmx_rigid_body_sphere_bullet(self):
        """PMX sphere (shape_type=0) → Bullet colliderShapeType=2(sphere)"""
        self._run_bullet_rigid_body_test("pmx", 0, 2)

    def test_pmx_rigid_body_box_bullet(self):
        """PMX box (shape_type=1) → Bullet colliderShapeType=1(box)"""
        self._run_bullet_rigid_body_test("pmx", 1, 1)

    def test_pmx_rigid_body_capsule_bullet(self):
        """PMX capsule (shape_type=2) → Bullet colliderShapeType=3(capsule)"""
        self._run_bullet_rigid_body_test("pmx", 2, 3)

    def test_pmd_rigid_body_static_mode(self):
        """PMD physics_mode=0 → bodyType=1(Kinematic)"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb = self._make_fake_pmd_rigid_body(name="static_rb", bone_index=0,
                                            shape_type=0, physics_mode=0)
        data = self._make_fake_pmd_data(rigid_bodies=[rb])
        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, _ = converter.convert_pmd_physics(data, {}, root)
        self.assertGreaterEqual(len(rbs), 1)
        shape = cmds.listRelatives(rbs[0], shapes=True, type="bulletRigidBodyShape")[0]
        self.assertEqual(cmds.getAttr(f"{shape}.bodyType"), 1,
                         "physics_mode=0 は bodyType=1(kinematic) のはず")

    def test_pmd_rigid_body_mode2_stays_dynamic_bullet(self):
        """PMD physics_mode=2 は Bullet 上でも dynamic body として作成する。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb = self._make_fake_pmd_rigid_body(name="mode2_rb", bone_index=0,
                                            shape_type=0, physics_mode=2)
        data = self._make_fake_pmd_data(rigid_bodies=[rb])
        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, _ = converter.convert_pmd_physics(data, {}, root)
        self.assertGreaterEqual(len(rbs), 1)
        shape = cmds.listRelatives(rbs[0], shapes=True, type="bulletRigidBodyShape")[0]
        self.assertEqual(cmds.getAttr(f"{shape}.bodyType"), 2,
                         "physics_mode=2 は bodyType=2(dynamic) のはず")
        self.assertEqual(cmds.getAttr(f"{rbs[0]}.mmd_physics_mode"), 2)

    def test_pmd_joint_bullet(self):
        """PMD joint → bulletRigidBodyConstraintShape (constraintType=4=SixDOF)"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb_a = self._make_fake_pmd_rigid_body(name="rb_a", bone_index=0,
                                              shape_type=0, position=(0, 5, 0))
        rb_b = self._make_fake_pmd_rigid_body(name="rb_b", bone_index=1,
                                              shape_type=0, position=(0, 10, 0))
        joint = self._make_fake_pmd_joint(
            name="test_joint", rigid_body_index_a=0, rigid_body_index_b=1,
            position=(0, 7.5, 0),
            translation_limit_min=(-1, -1, -1), translation_limit_max=(1, 1, 1),
        )
        data = self._make_fake_pmd_data(rigid_bodies=[rb_a, rb_b], joints=[joint])

        converter = PhysicsConverter({"create_physics_joints": True})
        rbs, cons = converter.convert_pmd_physics(data, {}, root)

        self.assertGreaterEqual(len(rbs), 2, "2 つの rigid body が必要")
        self.assertGreaterEqual(len(cons), 1, "constraint が作成されていない")

        constr_shape = cmds.listRelatives(cons[0], shapes=True, type="bulletRigidBodyConstraintShape") or []
        self.assertGreaterEqual(len(constr_shape), 1,
                                "bulletRigidBodyConstraintShape が存在しない")

        # constraintType = SixDOF (4)
        ctype = cmds.getAttr(f"{constr_shape[0]}.constraintType")
        self.assertEqual(ctype, 4, "PMD joint は constraintType=4(SixDOF) のはず")

        source_a = cmds.listConnections(
            f"{constr_shape[0]}.rigidBodyA",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        source_b = cmds.listConnections(
            f"{constr_shape[0]}.rigidBodyB",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertTrue(source_a and source_a[0].endswith(".outRigidBodyData"))
        self.assertTrue(source_b and source_b[0].endswith(".outRigidBodyData"))

        # カスタムアトリビュート
        self.assertTrue(cmds.attributeQuery("mmd_joint_name", node=cons[0], exists=True))
        self.assertTrue(cmds.attributeQuery("mmd_joint_type", node=cons[0], exists=True))

    def test_pmx_duplicate_rigid_body_names_keep_constraint_indices(self):
        """同名剛体でも PMX joint の index 通りに Bullet constraint を接続する。"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb_a = self._make_fake_pmx_rigid_body(
            name="dup", related_bone_index=0, shape_type=0, position=(0, 5, 0)
        )
        rb_b = self._make_fake_pmx_rigid_body(
            name="dup", related_bone_index=1, shape_type=0, position=(0, 10, 0)
        )
        joint = self._make_fake_pmx_joint(
            name="dup_joint",
            joint_type=0,
            rigid_body_a_index=0,
            rigid_body_b_index=1,
            position=(0, 7.5, 0),
        )
        data = self._make_fake_pmx_data(rigid_bodies=[rb_a, rb_b], joints=[joint])

        converter = PhysicsConverter({"create_physics_joints": True})
        rbs, cons = converter.convert_pmx_physics(data, {}, root)

        self.assertGreaterEqual(len(rbs), 2)
        self.assertGreaterEqual(len(cons), 1)
        shape_a = cmds.listRelatives(rbs[0], shapes=True, type="bulletRigidBodyShape", fullPath=True)[0]
        shape_b = cmds.listRelatives(rbs[1], shapes=True, type="bulletRigidBodyShape", fullPath=True)[0]
        constr_shape = cmds.listRelatives(cons[0], shapes=True, type="bulletRigidBodyConstraintShape")[0]

        source_a = cmds.listConnections(
            f"{constr_shape}.rigidBodyA",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        source_b = cmds.listConnections(
            f"{constr_shape}.rigidBodyB",
            source=True,
            destination=False,
            plugs=True,
        ) or []

        source_a_node, source_a_attr = source_a[0].split(".", 1)
        source_b_node, source_b_attr = source_b[0].split(".", 1)

        self.assertEqual(cmds.ls(source_a_node, long=True)[0], shape_a)
        self.assertEqual(cmds.ls(source_b_node, long=True)[0], shape_b)
        self.assertEqual(source_a_attr, "outRigidBodyData")
        self.assertEqual(source_b_attr, "outRigidBodyData")

    def test_pmx_joint_types_bullet(self):
        """PMX joint_type の各値を Bullet constraintType に写す"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        # joint_type → expected constraintType のマッピング
        cases = {
            0: 4,  # sixDOF → SixDOF
            1: 5,  # spring6DOF → SpringSixDOF
            2: 0,  # point → Point
            3: 3,  # coneTwist → ConeTwist
            4: 2,  # slider → Slider
            5: 1,  # hinge → Hinge
        }

        for jtype, expected in cases.items():
            cmds.file(new=True, force=True)
            root = cmds.group(name="test_root", empty=True)
            rb_a = self._make_fake_pmx_rigid_body(
                name="rb_a", related_bone_index=0, shape_type=0,
                position=(0, 5, 0))
            rb_b = self._make_fake_pmx_rigid_body(
                name="rb_b", related_bone_index=1, shape_type=0,
                position=(0, 10, 0))
            joint = self._make_fake_pmx_joint(
                name=f"j_{jtype}", joint_type=jtype,
                rigid_body_a_index=0, rigid_body_b_index=1,
                position=(0, 7.5, 0),
            )
            data = self._make_fake_pmx_data(
                rigid_bodies=[rb_a, rb_b], joints=[joint])
            converter = PhysicsConverter({"create_physics_joints": True})
            rbs, cons = converter.convert_pmx_physics(data, {}, root)

            self.assertGreaterEqual(
                len(cons), 1,
                f"joint_type={jtype} で constraint が作成されていない")
            constr_shape = cmds.listRelatives(
                cons[0], shapes=True, type="bulletRigidBodyConstraintShape")[0]
            actual = cmds.getAttr(f"{constr_shape}.constraintType")
            self.assertEqual(
                actual, expected,
                f"joint_type={jtype}: expected constraintType={expected}, got {actual}")
            source_a = cmds.listConnections(
                f"{constr_shape}.rigidBodyA",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            self.assertTrue(
                source_a and source_a[0].endswith(".outRigidBodyData"),
                f"joint_type={jtype}: rigidBodyA が outRigidBodyData から接続されていない",
            )

    def test_bullet_unavailable_fallback(self):
        """Bullet が不可の場合に Nucleus fallback が例外を出さない"""
        root = cmds.group(name="test_root", empty=True)
        converter = PhysicsConverter({"create_physics_joints": False})
        converter._bullet_available = False
        rb = self._make_fake_pmd_rigid_body(name="fallback_test")
        data = self._make_fake_pmd_data(rigid_bodies=[rb])
        try:
            rbs, cons = converter.convert_pmd_physics(data, {}, root)
            self.assertIsInstance(rbs, list)
            self.assertIsInstance(cons, list)
        except Exception as e:
            self.fail(f"Bullet unavailable fallback で例外: {e}")

    def test_create_physics_joints_false(self):
        """create_physics_joints=False で joint が作成されない"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb_a = self._make_fake_pmd_rigid_body(name="rb_a", bone_index=0,
                                              shape_type=0, position=(0, 5, 0))
        rb_b = self._make_fake_pmd_rigid_body(name="rb_b", bone_index=1,
                                              shape_type=0, position=(0, 10, 0))
        joint = self._make_fake_pmd_joint(
            name="skip_joint", rigid_body_index_a=0, rigid_body_index_b=1)
        data = self._make_fake_pmd_data(
            rigid_bodies=[rb_a, rb_b], joints=[joint])

        converter = PhysicsConverter({"create_physics_joints": False})
        rbs, cons = converter.convert_pmd_physics(data, {}, root)

        self.assertGreaterEqual(len(rbs), 2, "rigid body は作成されるはず")
        self.assertEqual(len(cons), 0,
                         "create_physics_joints=False なら joint は 0 のはず")

    def test_collect_physics_from_scene_for_export_smoke(self):
        """Maya Bullet ノードから physics dict を収集し、PmxExporter へ渡してPMXに復元できる"""
        if not PhysicsConverter.is_bullet_available():
            self.skipTest("Bullet プラグインが利用できません")

        root = cmds.group(name="test_root", empty=True)
        rb_a = self._make_fake_pmx_rigid_body(
            name="rb_a",
            related_bone_index=3,
            shape_type=0,
            position=(0.0, 1.0, 0.0),
            size=(1.2, 1.2, 1.2),
            physics_mode=2,
            mass=1.0,
        )
        rb_a.name_english = "rb_a_en"
        rb_b = self._make_fake_pmx_rigid_body(
            name="rb_b",
            related_bone_index=7,
            shape_type=1,
            position=(2.0, 1.0, 0.0),
            size=(1.0, 2.0, 3.0),
            physics_mode=0,
            mass=2.0,
        )
        rb_b.name_english = "rb_b_en"
        joint = self._make_fake_pmx_joint(
            name="test_joint",
            joint_type=2,
            rigid_body_a_index=0,
            rigid_body_b_index=1,
            position=(1.0, 1.0, 0.0),
            rotation=(0.0, 0.0, 0.0),
            translation_limit_min=(-1.0, -1.0, -1.0),
            translation_limit_max=(1.0, 1.0, 1.0),
            rotation_limit_min=(-0.5, -0.5, -0.5),
            rotation_limit_max=(0.5, 0.5, 0.5),
            spring_translation=(0.1, 0.0, 0.2),
            spring_rotation=(0.0, 0.1, 0.0),
        )
        joint.name_english = "test_joint_en"
        data = self._make_fake_pmx_data(rigid_bodies=[rb_a, rb_b], joints=[joint])

        converter = PhysicsConverter()
        rbs, _ = converter.convert_pmx_physics(data, {}, root)
        cmds.setAttr(f"{rbs[0]}.mmd_rigid_body_index", 2)
        cmds.setAttr(f"{rbs[1]}.mmd_rigid_body_index", 5)

        collected = converter.collect_physics_from_scene_for_export(root)
        self.assertEqual(len(collected["rigid_bodies"]), 2)
        self.assertEqual(len(collected["joints"]), 1)

        rigid_bodies = collected["rigid_bodies"]
        self.assertEqual(rigid_bodies[0]["name"], "rb_a")
        self.assertEqual(rigid_bodies[0]["name_english"], "rb_a_en")
        self.assertEqual(rigid_bodies[1]["name"], "rb_b")
        self.assertEqual(rigid_bodies[1]["name_english"], "rb_b_en")
        self.assertEqual(rigid_bodies[0]["shape_type"], 0)
        self.assertEqual(rigid_bodies[1]["shape_type"], 1)
        self.assertEqual(rigid_bodies[0]["physics_mode"], 2)
        self.assertEqual(rigid_bodies[1]["physics_mode"], 0)

        joints = collected["joints"]
        self.assertEqual(joints[0]["rigid_body_a_index"], 0)
        self.assertEqual(joints[0]["rigid_body_b_index"], 1)
        self.assertEqual(joints[0]["name_english"], "test_joint_en")
        self.assertEqual(rigid_bodies[0]["related_bone_index"], 3)
        self.assertEqual(rigid_bodies[1]["related_bone_index"], 7)
        self.assertEqual(joints[0]["joint_type"], 2)

        exporter = PmxExporter()
        out_path = self.get_temp_filename("physics_exporter_roundtrip.pmx")
        exporter.export_pmx_model(
            out_path,
            {
                "model_name": "PhysicsCollectRoundtrip",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 1.0, 0.0], "uv": [0.0, 0.0]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 1.0, 0.0], "uv": [1.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 1.0, 0.0], "uv": [0.0, 1.0]},
                ],
                "faces": [[0, 1, 2]],
                "bones": [{"name": f"bone_{i}", "position": [0.0, float(i), 0.0]} for i in range(8)],
                "materials": [{"name": "mat"}],
                "rigid_bodies": rigid_bodies,
                "joints": joints,
            },
        )

        pmx = parse_pmx_file(
            out_path,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
        self.assertEqual(len(pmx.rigid_bodies), 2, "収集した rigid_bodies が PMX に書き出されていない")
        self.assertEqual(len(pmx.joints), 1, "収集した joints が PMX に書き出されていない")
        self.assertEqual(pmx.rigid_bodies[0].related_bone_index, 3)
        self.assertEqual(pmx.rigid_bodies[1].related_bone_index, 7)
        self.assertEqual(pmx.rigid_bodies[0].physics_mode, 2)
        self.assertEqual(pmx.rigid_bodies[1].physics_mode, 0)
        self.assertEqual(pmx.joints[0].joint_type, 2)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_min[0], -0.5, places=6)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_min[1], -0.5, places=6)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_min[2], -0.5, places=6)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_max[0], 0.5, places=6)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_max[1], 0.5, places=6)
        self.assertAlmostEqual(pmx.joints[0].rotation_limit_max[2], 0.5, places=6)
