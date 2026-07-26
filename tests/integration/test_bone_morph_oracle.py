"""ボーンモーフ parity テスト: mmd-anim oracle vs Maya import paths.

mmd-anim CLI で生成した bone morph 付きテストモデル (test_bone_morph.pmx) と
モーション (test_bone_morph_motion.vmd) を Maya にインポートし、
rig mode / bake mode のワールド位置が mmd-anim runtime oracle と一致することを検証する。
"""

import math
import os
import unittest

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core.coordinate_transform import mmd_matrix_to_maya
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    is_mmd_runtime_available,
)
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.io.pmx_importer import import_pmx_file
from tests.common.maya_test_base import MayaTestBase

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FIXTURE_DIR = os.path.join(DATA_DIR, "for_unit_test")
PMX_FILE = os.path.join(FIXTURE_DIR, "test_bone_morph.pmx")
VMD_FILE = os.path.join(FIXTURE_DIR, "test_bone_morph_motion.vmd")
FRAMES = [0, 15, 30]
EPSILON = 0.01


def _get_runtime_oracle(pmx_path, vmd_path, frames):
    """mmd-anim FFI で各フレームの bone world position を Maya 座標系で返す。"""
    pmx_bytes = open(pmx_path, "rb").read()
    vmd_bytes = open(vmd_path, "rb").read()

    model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
    clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
    instance = MmdRuntimeInstance.for_model(model)

    oracle = {}
    try:
        for frame in frames:
            instance.evaluate_clip_frame(clip, float(frame))
            matrices = instance.get_world_matrices() or []
            for bone_idx, mmd_mat in enumerate(matrices):
                maya_mat = VmdConverter._convert_mmd_world_matrix_to_maya(list(mmd_mat))
                mat = om.MMatrix(maya_mat)
                tfm = om.MTransformationMatrix(mat)
                pos = tfm.translation(om.MSpace.kWorld)
                oracle.setdefault(bone_idx, {})[frame] = (pos.x, pos.y, pos.z)
    finally:
        instance.free()
        clip.free()
        model.free()
    return oracle


def _get_runtime_world_matrices(pmx_path, vmd_path, frames):
    """Return reflected mmd-anim bind/frame world matrices by PMX bone index."""
    with open(pmx_path, "rb") as file:
        model = MmdRuntimeModel.from_pmx_bytes(file.read())
    with open(vmd_path, "rb") as file:
        clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, file.read())
    instance = MmdRuntimeInstance.for_model(model)
    try:
        if not instance.evaluate_rest_pose():
            raise RuntimeError("mmd-anim rest-pose evaluation failed")
        bind = [om.MMatrix(mmd_matrix_to_maya(matrix)) for matrix in instance.get_world_matrices()]
        evaluated = {}
        for frame in frames:
            if not instance.evaluate_clip_frame(clip, float(frame)):
                raise RuntimeError(f"mmd-anim frame evaluation failed: {frame}")
            evaluated[frame] = [
                om.MMatrix(mmd_matrix_to_maya(matrix))
                for matrix in instance.get_world_matrices()
            ]
        return bind, evaluated
    finally:
        instance.free()
        clip.free()
        model.free()


def _get_maya_joint_positions(root, frames):
    """Maya シーンの各フレームでの joint world position を bone index 順で返す。"""
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    indexed = {}
    for j in joints:
        if cmds.attributeQuery("mmd_bone_index", node=j, exists=True):
            idx = cmds.getAttr(f"{j}.mmd_bone_index")
            indexed[idx] = j

    result = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        for bone_idx, joint in indexed.items():
            pos = cmds.xform(joint, query=True, worldSpace=True, translation=True)
            result.setdefault(bone_idx, {})[frame] = tuple(pos)
    return result


class TestBoneMorphOracle(MayaTestBase):
    """mmd-anim oracle と Maya bone morph import path の world position parity."""

    def setUp(self):
        super().setUp()
        from mmd_tools.core import settings
        settings.set("import.model.create_mmd_shaders", False)

    @unittest.skipUnless(os.path.exists(PMX_FILE), "test_bone_morph.pmx not found")
    def test_bone_morph_world_positions_match_oracle(self):
        """Rig mode bone morph 適用後の joint world position が mmd-anim oracle と一致。"""
        self._assert_bone_morph_world_positions_match_oracle(bake_mode=False)

    @unittest.skipUnless(os.path.exists(PMX_FILE), "test_bone_morph.pmx not found")
    def test_bake_mode_bone_morph_world_positions_match_oracle(self):
        """Bake mode の runtime final pose に bone morph が反映される。"""
        self._assert_bone_morph_world_positions_match_oracle(bake_mode=True)

    def _assert_bone_morph_world_positions_match_oracle(self, bake_mode=False):
        if not is_mmd_runtime_available():
            self.skipTest("mmd-anim runtime not available")

        oracle = _get_runtime_oracle(PMX_FILE, VMD_FILE, FRAMES)
        runtime_bind, runtime_frames = _get_runtime_world_matrices(PMX_FILE, VMD_FILE, FRAMES)

        pmx_data = parse_pmx_file(PMX_FILE)
        root = import_pmx_file(pmx_data, PMX_FILE, scale=1.0)
        self.assertTrue(root, "PMX import failed")
        indexed_joints = _indexed_joints(root)
        maya_bind = {
            index: om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
            for index, joint in indexed_joints.items()
        }

        vmd_data = VmdData()
        vmd_data.parse_file(VMD_FILE)
        converter = VmdConverter()
        kwargs = {"pmx_path": PMX_FILE, "bake_mode": bake_mode, "target_model": root}
        if bake_mode:
            with open(VMD_FILE, "rb") as file:
                kwargs["vmd_bytes"] = file.read()
        self.assertTrue(converter.convert(vmd_data, **kwargs))

        maya_positions = _get_maya_joint_positions(root, FRAMES)

        bone_names = {i: b.name for i, b in enumerate(pmx_data.bones)}
        failures = []
        for bone_idx in sorted(oracle.keys()):
            name = bone_names.get(bone_idx, f"bone_{bone_idx}")
            for frame in FRAMES:
                if frame not in oracle.get(bone_idx, {}):
                    continue
                if frame not in maya_positions.get(bone_idx, {}):
                    failures.append(f"{name} frame={frame}: missing in Maya")
                    continue
                o = oracle[bone_idx][frame]
                m = maya_positions[bone_idx][frame]
                for axis, ov, mv in zip("XYZ", o, m):
                    if abs(ov - mv) > EPSILON:
                        failures.append(
                            f"{name} frame={frame} {axis}: oracle={ov:.6f} maya={mv:.6f} diff={abs(ov-mv):.6f}"
                        )
                joint = indexed_joints.get(bone_idx)
                if joint:
                    cmds.currentTime(frame, edit=True)
                    correction = maya_bind[bone_idx] * runtime_bind[bone_idx].inverse()
                    expected_matrix = correction * runtime_frames[frame][bone_idx]
                    actual_matrix = om.MMatrix(
                        cmds.xform(joint, query=True, worldSpace=True, matrix=True)
                    )
                    matrix_error = max(
                        abs(float(actual_matrix[index]) - float(expected_matrix[index]))
                        for index in range(16)
                    )
                    if matrix_error > EPSILON:
                        failures.append(
                            f"{name} frame={frame}: world matrix max_error={matrix_error:.6f}"
                        )

        if failures:
            msg = f"{len(failures)} position mismatches (epsilon={EPSILON}):\n" + "\n".join(failures)
            self.fail(msg)

    def test_asymmetric_rotation_matches_runtime_in_joint_orient_basis(self):
        """Non-axis-aligned PMX rotation matches mmd-anim with rotated bind axes."""
        if not is_mmd_runtime_available():
            self.skipTest("mmd-anim runtime not available")

        pmx_path = self.get_temp_filename("bone_morph_asymmetric.pmx")
        axis = om.MVector(0.31, -0.57, 0.76).normal()
        angle = math.radians(73.0)
        sin_half = math.sin(angle * 0.5)
        raw_quat = (
            axis.x * sin_half,
            axis.y * sin_half,
            axis.z * sin_half,
            math.cos(angle * 0.5),
        )
        connected = int(
            PmxBoneFlag.DISPLAY
            | PmxBoneFlag.OPERATABLE
            | PmxBoneFlag.ROTATABLE
            | PmxBoneFlag.MOVABLE
            | PmxBoneFlag.CONNECT_BONE
            | PmxBoneFlag.LOCAL_AXIS
        )
        PmxExporter().export_pmx_model(
            pmx_path,
            {
                "model_name": "bone_morph_asymmetric",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0], "bone_indices": [0]},
                    {"position": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0], "bone_indices": [1]},
                    {"position": [4.0, 6.0, 8.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0], "bone_indices": [2]},
                ],
                "faces": [[0, 1, 2]],
                "materials": [{"name": "material"}],
                "bones": [
                    {"name": "root", "position": [0.0, 0.0, 0.0], "parent_index": -1, "bone_flag": connected, "connect_bone_index": 1, "x_axis_direction": [0.70710678, 0.0, 0.70710678], "z_axis_direction": [0.0, 1.0, 0.0]},
                    {"name": "arm", "position": [1.0, 2.0, 3.0], "parent_index": 0, "bone_flag": connected, "connect_bone_index": 2, "x_axis_direction": [0.36, 0.48, 0.8], "z_axis_direction": [-0.8, 0.6, 0.0]},
                    {"name": "tip", "position": [4.0, 6.0, 8.0], "parent_index": 1, "connect_position_offset": [1.0, -2.0, 0.5]},
                ],
                "morphs": [
                    {
                        "name": "rotate_arm",
                        "type": "bone",
                        "offsets": [
                            {
                                "bone_index": 1,
                                "translation": [0.0, 0.0, 0.0],
                                "rotation": raw_quat,
                            }
                        ],
                    }
                ],
            },
        )

        frames = (0.0, 7.5, 15.0, 22.5, 30.0)
        runtime_bind, runtime_frames = _get_runtime_world_matrices(pmx_path, VMD_FILE, frames)
        pmx_data = parse_pmx_file(pmx_path)
        root = import_pmx_file(pmx_data, pmx_path, scale=1.0)
        joints = _indexed_joints(root)
        maya_bind = {index: om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True)) for index, joint in joints.items()}
        self.assertTrue(any(abs(value) > 1.0e-4 for value in cmds.getAttr(f"{joints[1]}.jointOrient")[0]))

        vmd_data = VmdData()
        vmd_data.parse_file(VMD_FILE)
        self.assertTrue(VmdConverter().convert(vmd_data, pmx_path=pmx_path, bake_mode=False, target_model=root))

        failures = []
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            cmds.refresh(force=True)
            for index, joint in joints.items():
                correction = maya_bind[index] * runtime_bind[index].inverse()
                expected = correction * runtime_frames[frame][index]
                actual = om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
                max_error = max(abs(float(actual[index]) - float(expected[index])) for index in range(16))
                if max_error > EPSILON:
                    failures.append(f"bone={index} frame={frame:g} matrix max_error={max_error:.6f}")

        if failures:
            self.fail("mmd-anim world-matrix mismatches:\n" + "\n".join(failures))

        cmds.currentTime(0.0, edit=True)
        roundtrip_path = self.get_temp_filename("bone_morph_asymmetric_roundtrip.pmx")
        PmxExporter().export_pmx_model(
            roundtrip_path,
            ExportSceneCollector().collect_from_model_root(root),
        )
        reparsed = parse_pmx_file(roundtrip_path)
        rotation = tuple(float(value) for value in reparsed.morphs[0].offsets[0]["rotation"])
        dot = abs(sum(actual * expected for actual, expected in zip(rotation, raw_quat)))
        self.assertAlmostEqual(dot, 1.0, places=5, msg="PMX round-trip changed raw morph quaternion")

        scene_path = self.get_temp_filename("bone_morph_asymmetric.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        reopened_root = (cmds.ls("*bone_morph_asymmetric*", type="transform", long=True) or [root])[0]
        reopened_joints = _indexed_joints(reopened_root)
        cmds.currentTime(30.0, edit=True)
        for index, joint in reopened_joints.items():
            correction = maya_bind[index] * runtime_bind[index].inverse()
            expected = correction * runtime_frames[30.0][index]
            actual = om.MMatrix(cmds.xform(joint, query=True, worldSpace=True, matrix=True))
            self.assertLessEqual(
                max(abs(float(actual[element]) - float(expected[element])) for element in range(16)),
                EPSILON,
                f"scene reopen changed bone {index} world matrix",
            )


def _indexed_joints(root):
    result = {}
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
            result[int(cmds.getAttr(f"{joint}.mmd_bone_index"))] = joint
    return result
