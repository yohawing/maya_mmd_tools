"""VMD runtime matrix conversion and local channel tests."""

import ctypes
import math
from types import SimpleNamespace
from unittest.mock import patch

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.maya_test_base import MayaTestBase


def _determinant3(matrix):
    """Return the determinant of the upper-left 3x3 of a flat 4x4 matrix."""
    a, b, c = matrix[0], matrix[1], matrix[2]
    d, e, f = matrix[4], matrix[5], matrix[6]
    g, h, i = matrix[8], matrix[9], matrix[10]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


class TestVmdRuntimeMatrix(MayaTestBase):
    """Runtime matrix and local channel conversion tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_runtime_matrix_coordinate_conversion_identity_and_translation(self):
        """runtime world matrix の座標変換で identity を壊さず Z translation だけ反転する"""
        identity = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        self.assertListAlmostEqual(
            self.converter._convert_mmd_world_matrix_to_maya(identity),
            identity,
        )

        translated = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]
        expected = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, -3.0, 1.0,
        ]
        self.assertListAlmostEqual(
            self.converter._convert_mmd_world_matrix_to_maya(translated),
            expected,
        )

    def test_runtime_matrix_coordinate_conversion_rotations_keep_proper_basis(self):
        """runtime world matrix の Z 反転が回転行列を反射行列にしない"""
        cases = [
            (
                "rotate_x_90",
                [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, -1.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    1.0, 0.0, -0.0, 0.0,
                    0.0, 0.0, -1.0, 0.0,
                    -0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
            (
                "rotate_y_90",
                [
                    0.0, 0.0, -1.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 1.0, -0.0, 0.0,
                    -1.0, -0.0, 0.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
            (
                "rotate_z_90",
                [
                    0.0, 1.0, 0.0, 0.0,
                    -1.0, 0.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
                [
                    0.0, 1.0, -0.0, 0.0,
                    -1.0, 0.0, -0.0, 0.0,
                    -0.0, -0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            ),
        ]

        for name, source, expected in cases:
            converted = self.converter._convert_mmd_world_matrix_to_maya(source)
            self.assertListAlmostEqual(converted, expected, places=6, msg=name)
            self.assertAlmostEqual(
                _determinant3(converted),
                1.0,
                places=6,
                msg=f"{name} determinant",
            )

    def test_runtime_matrix_coordinate_conversion_applies_to_maya_joint(self):
        """変換済み runtime world matrix を Maya joint に適用した最終座標を確認する"""
        joint = cmds.joint(name="runtime_matrix_joint")
        mmd_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]

        maya_matrix = self.converter._convert_mmd_world_matrix_to_maya(mmd_matrix)
        cmds.xform(joint, worldSpace=True, matrix=maya_matrix)

        translation = cmds.xform(joint, query=True, worldSpace=True, translation=True)
        self.assertListAlmostEqual(translation, [1.0, 2.0, -3.0], places=6)

    def test_runtime_matrix_bake_sets_animation_curve_values_in_maya_space(self):
        """runtime world matrix bake 後のアニメーションカーブ値が Maya 座標系になる"""
        joint = cmds.joint(name="runtime_bake_joint")
        self.converter.bone_name_mapping = {"センター": joint}
        self.converter.bone_name_to_index = {"センター": 0}
        self.converter.bone_index_to_joint = {0: joint}
        self.converter.anim_layer = None

        mmd_matrix = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        ]

        self.converter._bake_bone_poses_from_world_matrices(
            frame=12,
            world_matrices=[mmd_matrix],
            model_bone_count=1,
        )

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 2.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), -3.0, places=6)

        keyed_times = cmds.keyframe(f"{joint}.translateZ", query=True, timeChange=True)
        self.assertIn(12.0, keyed_times)

    def test_compute_bone_locals_matches_xform_for_root_and_child(self):
        """_compute_all_bone_locals が xform(ws) 後の .translate / .rotate と等価な値を返すことを確認（キャッシュの正確性）"""
        parent = cmds.joint(name="test_parent_bone")
        cmds.select(parent, replace=True)
        child = cmds.joint(name="test_child_bone")
        cmds.select(clear=True)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}

        parent_mmd = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        child_mmd = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 0.0, 0.0, 1.0,
        ]

        locals_map = self.converter._compute_all_bone_locals([parent_mmd, child_mmd])
        self.assertIn(0, locals_map)
        self.assertIn(1, locals_map)

        p_tx, p_ty, p_tz, p_rx, _p_ry, _p_rz = locals_map[0]
        self.assertAlmostEqual(p_tx, 0.0, places=6)
        self.assertAlmostEqual(p_ty, 0.0, places=6)
        self.assertAlmostEqual(p_tz, -0.0, places=6)
        self.assertAlmostEqual(p_rx, 0.0, places=6)

        c_tx, _c_ty, c_tz, _c_rx, _c_ry, _c_rz = locals_map[1]
        self.assertAlmostEqual(c_tx, 1.0, places=6)
        self.assertAlmostEqual(_c_ty, 0.0, places=6)
        self.assertAlmostEqual(c_tz, 0.0, places=6)

        maya_p = self.converter._convert_mmd_world_matrix_to_maya(parent_mmd)
        maya_c = self.converter._convert_mmd_world_matrix_to_maya(child_mmd)
        cmds.xform(parent, worldSpace=True, matrix=maya_p)
        cmds.xform(child, worldSpace=True, matrix=maya_c)
        self.assertAlmostEqual(cmds.getAttr(f"{child}.translateX"), c_tx, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{child}.translateZ"), c_tz, places=6)

        cmds.delete(parent, child)

    def test_compute_bone_locals_uses_native_local_channel_abi_when_available(self):
        """native local decomposition ABI がある場合は Maya API 分解をスキップする。"""
        parent = cmds.joint(name="test_native_local_parent")
        cmds.select(parent, replace=True)
        child = cmds.joint(name="test_native_local_child")
        cmds.select(clear=True)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        world_mats = [[1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                       0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]] * 2

        with patch(
            "mmd_tools.converters.vmd_converter.compute_maya_local_channels",
            return_value=[
                (1.0, 2.0, 3.0, 10.0, 20.0, 30.0),
                (4.0, 5.0, 6.0, 40.0, 50.0, 60.0),
            ],
        ) as compute_mock:
            locals_map = self.converter._compute_all_bone_locals(world_mats)

        self.assertEqual(locals_map[0], (1.0, 2.0, 3.0, 10.0, 20.0, 30.0))
        self.assertEqual(locals_map[1], (4.0, 5.0, 6.0, 40.0, 50.0, 60.0))
        self.assertEqual(compute_mock.call_count, 1)

        cmds.delete(parent)

    def test_compute_native_local_channel_batch_uses_runtime_bone_order(self):
        """batch local decomposition は dict 挿入順ではなく runtime bone index 順で入力を作る。"""
        parent = cmds.joint(name="test_native_batch_parent")
        cmds.select(parent, replace=True)
        child = cmds.joint(name="test_native_batch_child")
        cmds.select(clear=True)

        self.converter.bone_index_to_joint = {1: child, 0: parent}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        batch_result = SimpleNamespace(
            frame_count=1,
            bone_count=2,
            world_matrices=(ctypes.c_float * 32)(*([1.0, 0.0, 0.0, 0.0,
                                                    0.0, 1.0, 0.0, 0.0,
                                                    0.0, 0.0, 1.0, 0.0,
                                                    0.0, 0.0, 0.0, 1.0] * 2)),
        )
        native_result = SimpleNamespace(
            frame_count=1,
            bone_count=2,
            local_channels=(ctypes.c_float * 12)(*range(12)),
        )

        with patch(
            "mmd_tools.converters.vmd_converter.compute_maya_local_channels_batch",
            return_value=native_result,
        ) as compute_mock:
            result = self.converter._compute_native_local_channel_batch(batch_result)

        self.assertEqual(result["ordered_bone_indices"], (0, 1))
        self.assertEqual(compute_mock.call_args.args[3], [-1, 0])

        cmds.delete(parent)

    def test_compute_native_local_channel_batch_matches_bind_space_with_joint_orient(self):
        """batch local decomposition は JO 付き bind 補正後の skinning matrix と一致する。"""
        joint = cmds.joint(name="test_native_batch_bind_space_jo_bone")
        cmds.select(clear=True)
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 0.0, 45.0)
        cmds.setAttr(f"{joint}.rotate", 0.0, 0.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        bind_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        bind_no_orient = om.MMatrix()
        self.converter.bone_index_to_joint = {0: joint}
        self.converter._bone_parent_map = {0: None}
        self.converter._bone_rotate_orders = {0: 0}
        self.converter._runtime_bind_world_matrices = {0: bind_world}
        self.converter._runtime_no_orient_bind_world_matrices = {0: bind_no_orient}

        runtime_world_tm = om.MTransformationMatrix()
        runtime_world_tm.setTranslation(om.MVector(1.0, 2.0, -0.5), om.MSpace.kTransform)
        runtime_world_tm.setRotation(om.MEulerRotation(0.0, 0.0, math.radians(90.0)))
        runtime_world = runtime_world_tm.asMatrix()
        runtime_mmd = self.converter._convert_mmd_world_matrix_to_maya(list(runtime_world))
        batch_result = SimpleNamespace(
            frame_count=1,
            bone_count=1,
            world_matrices=(ctypes.c_float * 16)(*runtime_mmd),
        )

        native_batch = self.converter._compute_native_local_channel_batch(batch_result)
        if native_batch is None:
            self.skipTest("mmd-anim native batch local channel ABI is unavailable")
        locals_map = self.converter._native_local_channel_batch_for_frame(native_batch, 0)
        self.assertIn(0, locals_map)
        tx, ty, tz, rx, ry, rz = locals_map[0]
        cmds.setAttr(f"{joint}.translate", tx, ty, tz, type="double3")
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

        corrected_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        actual_skinning = bind_world.inverse() * corrected_world
        expected_skinning = bind_no_orient.inverse() * runtime_world
        for i in range(16):
            self.assertAlmostEqual(actual_skinning[i], expected_skinning[i], places=5)

        cmds.delete(joint)

    def test_compute_native_local_channel_batch_matches_python_fallback_with_parent_rotation(self):
        """native batch local decomposition は親回転を含む Python fallback と同じ Euler を返す。"""
        parent = cmds.joint(name="test_native_batch_parent_rotation")
        cmds.select(parent, replace=True)
        child = cmds.joint(name="test_native_batch_child_rotation")
        cmds.select(clear=True)

        cmds.setAttr(f"{parent}.translate", 1.5, 2.0, -3.0)
        cmds.setAttr(f"{parent}.rotate", 0.0, 35.0, 10.0)
        cmds.setAttr(f"{child}.translate", 2.0, -0.5, 1.25)
        cmds.setAttr(f"{child}.rotate", 15.0, 0.0, -20.0)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}

        world_mats = [
            self.converter._convert_mmd_world_matrix_to_maya(cmds.xform(parent, query=True, worldSpace=True, matrix=True)),
            self.converter._convert_mmd_world_matrix_to_maya(cmds.xform(child, query=True, worldSpace=True, matrix=True)),
        ]
        expected = self.converter._compute_all_bone_locals(world_mats)
        world_flat = [value for matrix in world_mats for value in matrix]
        batch_result = SimpleNamespace(
            frame_count=1,
            bone_count=2,
            world_matrices=(ctypes.c_float * len(world_flat))(*world_flat),
        )

        native_batch = self.converter._compute_native_local_channel_batch(batch_result)
        if native_batch is None:
            self.skipTest("mmd-anim native batch local channel ABI is unavailable")
        actual = self.converter._native_local_channel_batch_for_frame(native_batch, 0)

        for bidx in (0, 1):
            for actual_value, expected_value in zip(actual[bidx], expected[bidx]):
                self.assertAlmostEqual(actual_value, expected_value, places=5)

        cmds.delete(parent)

    def test_compute_bone_locals_matches_maya_with_parent_rotation(self):
        """親が回転している階層でも runtime world 行列から Maya local 値を再構成できることを確認"""
        parent = cmds.joint(name="test_parent_rot_bone")
        cmds.select(clear=True)
        child = cmds.joint(name="test_child_rot_bone")
        cmds.parent(child, parent)
        cmds.select(clear=True)

        cmds.setAttr(f"{parent}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{child}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{parent}.translate", 1.5, 2.0, -3.0)
        cmds.setAttr(f"{parent}.rotate", 0.0, 35.0, 10.0)
        cmds.setAttr(f"{child}.translate", 2.0, -0.5, 1.25)
        cmds.setAttr(f"{child}.rotate", 15.0, 0.0, -20.0)

        self.converter.bone_index_to_joint = {0: parent, 1: child}
        self.converter._bone_parent_map = {0: None, 1: 0}
        self.converter._bone_rotate_orders = {0: 0, 1: 0}
        self.converter._runtime_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}
        self.converter._runtime_no_orient_bind_world_matrices = {0: om.MMatrix(), 1: om.MMatrix()}

        parent_maya_world = cmds.xform(parent, query=True, worldSpace=True, matrix=True)
        child_maya_world = cmds.xform(child, query=True, worldSpace=True, matrix=True)
        parent_mmd_world = self.converter._convert_mmd_world_matrix_to_maya(parent_maya_world)
        child_mmd_world = self.converter._convert_mmd_world_matrix_to_maya(child_maya_world)

        locals_map = self.converter._compute_all_bone_locals([parent_mmd_world, child_mmd_world])
        self.assertIn(0, locals_map)
        self.assertIn(1, locals_map)

        for bidx, joint in ((0, parent), (1, child)):
            tx, ty, tz, rx, ry, rz = locals_map[bidx]
            self.assertAlmostEqual(tx, cmds.getAttr(f"{joint}.translateX"), places=5)
            self.assertAlmostEqual(ty, cmds.getAttr(f"{joint}.translateY"), places=5)
            self.assertAlmostEqual(tz, cmds.getAttr(f"{joint}.translateZ"), places=5)
            self.assertAlmostEqual(rx, cmds.getAttr(f"{joint}.rotateX"), delta=1e-4)
            self.assertAlmostEqual(ry, cmds.getAttr(f"{joint}.rotateY"), delta=1e-4)
            self.assertAlmostEqual(rz, cmds.getAttr(f"{joint}.rotateZ"), delta=1e-4)

        cmds.delete(parent)

    def test_compute_bone_locals_with_joint_orient_matches_no_jo_skinning_matrix(self):
        """runtime bake は JO 付き bind で no-JO runtime と同じ skinning matrix を作る"""
        joint = cmds.joint(name="test_runtime_bind_space_jo_bone")
        cmds.select(clear=True)
        cmds.setAttr(f"{joint}.jointOrient", 0.0, 0.0, 45.0)
        cmds.setAttr(f"{joint}.rotate", 0.0, 0.0, 0.0)
        cmds.setAttr(f"{joint}.rotateOrder", 0)

        bind_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        bind_no_orient = om.MMatrix()

        self.converter.bone_index_to_joint = {0: joint}
        self.converter._bone_parent_map = {0: None}
        self.converter._bone_rotate_orders = {0: 0}
        self.converter._runtime_bind_world_matrices = {0: bind_world}
        self.converter._runtime_no_orient_bind_world_matrices = {0: bind_no_orient}

        runtime_world_maya = om.MTransformationMatrix()
        runtime_world_maya.setRotation(om.MEulerRotation(0.0, 0.0, math.radians(90.0)))
        runtime_world = runtime_world_maya.asMatrix()
        runtime_mmd = self.converter._convert_mmd_world_matrix_to_maya(list(runtime_world))

        locals_map = self.converter._compute_all_bone_locals([runtime_mmd])
        self.assertIn(0, locals_map)
        tx, ty, tz, rx, ry, rz = locals_map[0]
        cmds.setAttr(f"{joint}.translate", tx, ty, tz, type="double3")
        cmds.setAttr(f"{joint}.rotate", rx, ry, rz, type="double3")

        corrected_world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        actual_skinning = bind_world.inverse() * corrected_world
        expected_skinning = bind_no_orient.inverse() * runtime_world

        for i in range(16):
            self.assertAlmostEqual(actual_skinning[i], expected_skinning[i], places=5)

        cmds.delete(joint)

    def test_runtime_bind_world_maps_use_recorded_bind_pose_not_current_pose(self):
        """live rig が動いた状態でも runtime bind 補正は記録済み bind pose を使う"""
        root = cmds.joint(name="runtime_pose_root")
        cmds.setAttr(f"{root}.translate", 1.0, 2.0, 3.0)
        child = cmds.joint(name="runtime_pose_child")
        cmds.setAttr(f"{child}.translate", 0.0, 4.0, 0.0)
        cmds.setAttr(f"{child}.jointOrient", 0.0, 0.0, 30.0)

        self.converter.bone_name_mapping = {"root": root, "child": child}
        self.converter.bone_name_to_index = {"root": 0, "child": 1}
        self.converter.bone_index_to_joint = {0: root, 1: child}
        self.converter._record_bind_poses()
        self.converter._build_bone_hierarchy_and_order_maps()
        self.converter._build_runtime_bind_world_maps()
        bind_child_before = self.converter._runtime_bind_world_matrices[1]

        cmds.setAttr(f"{root}.rotate", 0.0, 45.0, 0.0)
        cmds.setAttr(f"{child}.translate", 3.0, 4.0, 5.0)
        delattr(self.converter, "_runtime_bind_world_matrices")
        delattr(self.converter, "_runtime_no_orient_bind_world_matrices")

        self.converter._build_runtime_bind_world_maps()
        bind_child_after = self.converter._runtime_bind_world_matrices[1]

        for i in range(16):
            self.assertAlmostEqual(bind_child_after[i], bind_child_before[i], places=6)

        cmds.delete(root)
