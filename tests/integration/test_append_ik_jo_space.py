"""mmdAppend / mmdCcdIk ノードの JointOrient 空間変換テスト

非可換 JO + 回転の組み合わせで、Maya-space → MMD-space → Maya-space の
round-trip が正しく行われるかを検証する。

mmdAppend compute の核心:
  1. source_mmd = src_jo.inv() * src_quat * src_jo    (Maya → MMD)
  2. grant = solver.solve(source_mmd)                 (MMD 空間で slerp)
  3. target_grant = target_jo * grant * target_jo.inv() (MMD → Maya)
  4. output = base * target_grant
"""

import math
import unittest

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.core.native.mmd_anim_runtime import is_rig_primitive_available
from mmd_tools.nodes.mmd_append_node import MmdAppendNode
from tests.common.maya_test_base import MayaTestBase


def _degrees_to_quat(x_deg, y_deg, z_deg):
    return om.MEulerRotation(
        math.radians(x_deg), math.radians(y_deg), math.radians(z_deg)
    ).asQuaternion()


def _quat_to_degrees(q):
    e = q.asEulerRotation()
    return (math.degrees(e.x), math.degrees(e.y), math.degrees(e.z))


def _compute_expected_append_output(
    source_deg, base_deg, src_jo_deg, target_jo_deg, ratio,
):
    """Python で mmdAppend の期待出力を計算する。"""
    src_q = _degrees_to_quat(*source_deg)
    base_q = _degrees_to_quat(*base_deg)
    src_jo = _degrees_to_quat(*src_jo_deg)
    target_jo = _degrees_to_quat(*target_jo_deg)

    # Maya → MMD
    source_mmd = src_jo.inverse() * src_q * src_jo

    # slerp(identity, source_mmd, ratio)
    identity = om.MQuaternion()
    grant_mmd = om.MQuaternion.slerp(identity, source_mmd, ratio)

    # MMD → Maya (target space)
    target_grant = target_jo * grant_mmd * target_jo.inverse()

    # output = base * target_grant
    output_q = base_q * target_grant
    return _quat_to_degrees(output_q)


def _assert_angles_close(test_case, actual, expected, tol=0.01, msg=""):
    for i, (a, e) in enumerate(zip(actual, expected)):
        axis = "XYZ"[i]
        test_case.assertAlmostEqual(
            a, e, delta=tol,
            msg=f"{msg} axis {axis}: got {a:.4f}, expected {e:.4f}"
        )


@unittest.skipUnless(is_rig_primitive_available(), "mmd-anim runtime not available")
class TestMmdAppendJointOrient(MayaTestBase):
    """mmdAppend ノードの JO 空間変換テスト"""

    def test_plug_match_guard_ignores_uninitialized_attributes(self):
        class FakePlug:
            def __eq__(self, other):
                if other is None:
                    raise TypeError("MPlug or MObject expected.")
                return False

            def attribute(self):
                raise RuntimeError("no attribute")

        self.assertFalse(MmdAppendNode._plug_matches_any(FakePlug(), (None,)))

    def _create_append_node(self, source_deg, base_deg, src_jo_deg, target_jo_deg, ratio):
        node = cmds.createNode("mmdAppend")
        cmds.setAttr(f"{node}.ratio", ratio)
        cmds.setAttr(f"{node}.affectRotation", True)
        cmds.setAttr(f"{node}.sourceRotate", *source_deg, type="double3")
        cmds.setAttr(f"{node}.baseRotate", *base_deg, type="double3")
        cmds.setAttr(f"{node}.sourceJointOrient", *src_jo_deg, type="double3")
        cmds.setAttr(f"{node}.targetJointOrient", *target_jo_deg, type="double3")
        return node

    def test_identity_jo_passthrough(self):
        """JO=(0,0,0) なら JO 変換は identity で結果に影響しない"""
        source = (30.0, 0.0, 0.0)
        base = (0.0, 0.0, 0.0)
        jo = (0.0, 0.0, 0.0)

        node = self._create_append_node(source, base, jo, jo, ratio=1.0)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(source, base, jo, jo, 1.0)
        _assert_angles_close(self, actual, expected, msg="identity JO")

    def test_source_jo_nonzero_z(self):
        """source JO が非零（Z 軸 45°）→ source が MMD 空間に変換されてから slerp"""
        source = (30.0, 0.0, 0.0)
        base = (0.0, 0.0, 0.0)
        src_jo = (0.0, 0.0, 45.0)
        target_jo = (0.0, 0.0, 0.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=1.0)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(source, base, src_jo, target_jo, 1.0)
        _assert_angles_close(self, actual, expected, msg="source JO Z=45")

        # JO 変換により出力は素の (30,0,0) とは異なるはず
        self.assertFalse(
            all(abs(a - s) < 0.01 for a, s in zip(actual, source)),
            "source JO should change the output when non-commutative",
        )

    def test_target_jo_nonzero_y(self):
        """target JO が非零（Y 軸 30°）→ grant が target Maya 空間に戻される"""
        source = (30.0, 15.0, 0.0)
        base = (0.0, 0.0, 0.0)
        src_jo = (0.0, 0.0, 0.0)
        target_jo = (0.0, 30.0, 0.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=1.0)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(source, base, src_jo, target_jo, 1.0)
        _assert_angles_close(self, actual, expected, msg="target JO Y=30")

    def test_both_jo_noncommutative(self):
        """source と target の JO が異なる非零値 → 非可換変換の完全 round-trip"""
        source = (15.0, 20.0, 10.0)
        base = (5.0, 10.0, 15.0)
        src_jo = (0.0, 45.0, 0.0)
        target_jo = (0.0, 0.0, 30.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=0.5)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(source, base, src_jo, target_jo, 0.5)
        _assert_angles_close(self, actual, expected, msg="both JO non-zero")

    def test_fractional_ratio_with_jo(self):
        """ratio=0.25 + 非零 JO"""
        source = (60.0, 0.0, 0.0)
        base = (10.0, 0.0, 0.0)
        src_jo = (30.0, 0.0, 0.0)
        target_jo = (15.0, 0.0, 0.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=0.25)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(source, base, src_jo, target_jo, 0.25)
        _assert_angles_close(self, actual, expected, msg="ratio=0.25 with JO")

    def test_append_rotate_is_mmd_space(self):
        """appendRotate 出力は MMD 空間（JO なし）の grant contribution"""
        source = (30.0, 0.0, 0.0)
        base = (0.0, 0.0, 0.0)
        src_jo = (0.0, 0.0, 45.0)
        target_jo = (0.0, 30.0, 0.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=1.0)

        # appendRotate = grant in MMD space (before target JO)
        append_rot = cmds.getAttr(f"{node}.appendRotate")[0]

        # Python reference: slerp(identity, src_jo.inv()*src*src_jo, ratio) → degrees
        src_q = _degrees_to_quat(*source)
        src_jo_q = _degrees_to_quat(*src_jo)
        source_mmd = src_jo_q.inverse() * src_q * src_jo_q
        grant_mmd = source_mmd  # ratio=1.0
        expected_mmd = _quat_to_degrees(grant_mmd)

        _assert_angles_close(self, append_rot, expected_mmd, msg="appendRotate MMD space")

    def test_matching_source_target_jo_round_trip(self):
        """source JO = target JO → MMD-space 往復が打ち消し合い、JO なしと同じ結果"""
        source = (30.0, 20.0, 10.0)
        base = (0.0, 0.0, 0.0)
        jo = (25.0, 15.0, 5.0)

        node_with_jo = self._create_append_node(source, base, jo, jo, ratio=1.0)
        actual_with_jo = cmds.getAttr(f"{node_with_jo}.outputRotate")[0]

        node_no_jo = self._create_append_node(
            source, base, (0, 0, 0), (0, 0, 0), ratio=1.0
        )
        actual_no_jo = cmds.getAttr(f"{node_no_jo}.outputRotate")[0]

        _assert_angles_close(
            self, actual_with_jo, actual_no_jo,
            msg="matching source/target JO should cancel out",
        )

    def test_large_multiaxis_jo(self):
        """大きな多軸 JO (45,30,60) で非可換性を最大化"""
        source = (45.0, -30.0, 20.0)
        base = (-10.0, 15.0, -5.0)
        src_jo = (45.0, 30.0, 60.0)
        target_jo = (20.0, -15.0, 40.0)

        node = self._create_append_node(source, base, src_jo, target_jo, ratio=0.7)
        actual = cmds.getAttr(f"{node}.outputRotate")[0]
        expected = _compute_expected_append_output(
            source, base, src_jo, target_jo, 0.7
        )
        _assert_angles_close(self, actual, expected, msg="large multi-axis JO")


@unittest.skipUnless(is_rig_primitive_available(), "mmd-anim runtime not available")
class TestMmdCcdIkJointOrient(MayaTestBase):
    """mmdCcdIk ノードの JO 空間変換テスト

    IK ノードは chainJson に joint_orient_deg を埋め込み、
    inputRotate (Maya space) → solver (MMD space) → outputRotate (Maya space)
    の round-trip を行う。
    """

    def _build_chain_json(self, bones_config, link_slots, target_slot=0):
        """chainJson 用の JSON 文字列を構築する。

        bones_config: list of dicts with keys:
            rest_position, parent_slot, joint_orient_deg
        link_slots: list of int — IK リンクとなるボーンスロット
        """
        import json
        bones = []
        for i, cfg in enumerate(bones_config):
            bones.append({
                "rest_position": cfg.get("rest_position", [0, 0, 0]),
                "parent_slot": cfg.get("parent_slot", i - 1 if i > 0 else -1),
                "joint_orient_deg": cfg.get("joint_orient_deg", [0, 0, 0]),
            })
        links = [{"bone_slot": s} for s in link_slots]
        chain = {
            "targetBoneSlot": target_slot,
            "iterationCount": 40,
            "limitAngle": 0.0628,
            "bones": bones,
            "links": links,
        }
        return json.dumps(chain)

    def test_disabled_ik_preserves_input_with_jo(self):
        """IK 無効時、outputRotate = inputRotate（JO 付きでも pass-through）"""
        jo = [25.0, 15.0, 0.0]
        chain_json = self._build_chain_json(
            bones_config=[
                {"rest_position": [0, 0, 0], "parent_slot": -1, "joint_orient_deg": jo},
                {"rest_position": [0, 5, 0], "parent_slot": 0, "joint_orient_deg": jo},
            ],
            link_slots=[1],
            target_slot=0,
        )

        node = cmds.createNode("mmdCcdIk")
        cmds.setAttr(f"{node}.chainJson", chain_json, type="string")
        cmds.setAttr(f"{node}.enabled", False)

        input_rot = (30.0, 15.0, 10.0)
        cmds.setAttr(f"{node}.inputRotate[1]", *input_rot, type="double3")

        out0 = cmds.getAttr(f"{node}.outputRotate[0]")[0]
        _assert_angles_close(self, out0, input_rot, msg="disabled IK link pass-through")
