"""ボーンモーフ parity テスト: mmd-anim oracle vs Maya rig mode.

mmd-anim CLI で生成した bone morph 付きテストモデル (test_bone_morph.pmx) と
モーション (test_bone_morph_motion.vmd) を Maya にインポートし、
rig mode (bone morph accumulator DG graph) のワールド位置が
mmd-anim runtime oracle と一致することを検証する。
"""

import os
import unittest

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.native.mmd_anim_runtime import (
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    is_mmd_runtime_available,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.vmd_data import VmdData
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
    """mmd-anim oracle と Maya rig mode bone morph の world position parity."""

    def setUp(self):
        super().setUp()
        from mmd_tools.core import settings
        settings.set("import.model.create_mmd_shaders", False)

    @unittest.skipUnless(os.path.exists(PMX_FILE), "test_bone_morph.pmx not found")
    def test_bone_morph_world_positions_match_oracle(self):
        """Rig mode bone morph 適用後の joint world position が mmd-anim oracle と一致。"""
        if not is_mmd_runtime_available():
            self.skipTest("mmd-anim runtime not available")

        oracle = _get_runtime_oracle(PMX_FILE, VMD_FILE, FRAMES)

        pmx_data = PmxData()
        pmx_data.parse_file(PMX_FILE)
        root = import_pmx_file(pmx_data, PMX_FILE, scale=1.0)
        self.assertTrue(root, "PMX import failed")

        vmd_data = VmdData()
        vmd_data.parse_file(VMD_FILE)
        converter = VmdConverter()
        converter.convert(vmd_data, pmx_path=PMX_FILE)

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

        if failures:
            msg = f"{len(failures)} position mismatches (epsilon={EPSILON}):\n" + "\n".join(failures)
            self.fail(msg)
