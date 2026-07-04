"""VMD runtime/live-rig interaction tests."""

import json
import math
from contextlib import ExitStack
from unittest.mock import patch

import maya.api.OpenMaya as om
import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdRuntimeLiveRig(MayaTestBase):
    """Runtime bake and sparse VMD import behavior around live MMD rigs."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        super().tearDown()
        self.fixture_provider.cleanup_temp_files()

    def test_mmd_ik_passthrough_keys_chain_bone_slot(self):
        """runtime live apply 中は mmdCcdIk output link の入力 slot へ final rotation を焼く"""
        joint = cmds.joint(name="runtime_live_toe_link")
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_live_toe_ik")
        chain_json = {
            "bones": [{"rest_position": [0, 0, 0], "parent_slot": -1} for _ in range(4)],
            "controllerBoneSlot": -1,
            "targetBoneSlot": 0,
            "links": [{"bone_slot": 3}],
            "iterationCount": 1,
            "limitAngle": 0.1,
        }
        cmds.setAttr(f"{ik_node}.chainJson", json.dumps(chain_json), type="string")
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{joint}.rotate", force=True)

        info = self.converter._collect_mmd_ik_passthrough_info()[joint]
        self.assertEqual(info["link_index"], 0)
        self.assertEqual(info["input_slot"], 3)

        times = om.MTimeArray()
        frames = [0, 5]
        for frame in frames:
            times.append(om.MTime(float(frame), om.MTime.uiUnit()))
        channels = {
            "rotateX": om.MDoubleArray([math.radians(10.0), math.radians(20.0)]),
            "rotateY": om.MDoubleArray([0.0, 0.0]),
            "rotateZ": om.MDoubleArray([0.0, 0.0]),
        }
        keyed = self.converter._key_mmd_ik_passthrough_rotation(info, channels, {}, times, frames)

        self.assertEqual(keyed, 4)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertEqual(
            cmds.keyframe(
                f"{ik_node}.inputRotate[3].inputRotateElementX",
                query=True,
                time=(5, 5),
                valueChange=True,
            ),
            [20.0],
        )
        self.assertIsNone(cmds.keyframe(f"{ik_node}.inputRotate[0].inputRotateElementX", query=True))

        cmds.delete(ik_node, joint)

    def test_ik_link_input_rotate_stores_correct_radian_values(self):
        """Rig+JO の IK link pre-rotation が solver.inputRotate に正しい角度単位で保存される"""
        pmx_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        vmd_path = self.fixture_provider.get_vmd_file("mmt_test_model_test_motion")

        root = import_mmd_file(
            pmx_path,
            options={"setup_rig": True, "setup_bone_orientation": True},
        )
        self.assertIsNotNone(root, "PMX import failed")
        visual_controller_joints = [
            joint for joint in (cmds.ls(type="joint") or [])
            if cmds.attributeQuery("mmd_ik_controller_visual", node=joint, exists=True)
            and cmds.getAttr(f"{joint}.mmd_ik_controller_visual")
        ]
        self.assertGreater(len(visual_controller_joints), 0, "IK controller visual が作成されていません")
        self.assertTrue(
            any(cmds.listRelatives(joint, shapes=True, type="nurbsCurve") for joint in visual_controller_joints),
            "IK controller visual の NURBS curve shape が見つかりません",
        )
        self.assertTrue(
            import_mmd_file(vmd_path, options={"target_model": root, "pmx_path": pmx_path}),
            "VMD import failed",
        )

        solver_node = None
        for node in cmds.ls(type="mmdCcdIk") or []:
            if not cmds.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
                continue
            ik_name = cmds.getAttr(f"{node}.mmd_ik_bone_name") or ""
            if "左足" in ik_name and "つま先" not in ik_name:
                solver_node = node
                break

        self.assertIsNotNone(solver_node, "左足 IK の mmdCcdIk solver が見つかりません")

        chain = json.loads(cmds.getAttr(f"{solver_node}.chainJson") or "{}")
        slots = [int(link["bone_slot"]) for link in chain.get("links", [])]
        self.assertGreater(len(slots), 0, "左足 IK の chainJson に links がありません")

        selection = om.MSelectionList()
        selection.add(solver_node)
        fn_dep = om.MFnDependencyNode(selection.getDependNode(0))
        input_rotate = fn_dep.findPlug("inputRotate", False)

        cmds.currentTime(10, edit=True)

        non_zero_radians = []
        for slot in slots:
            elem = input_rotate.elementByLogicalIndex(slot)
            for axis_index, axis in enumerate("XYZ"):
                attr = f"{solver_node}.inputRotate[{slot}].inputRotateElement{axis}"
                ui_degrees = cmds.getAttr(attr)
                plug_radians = elem.child(axis_index).asDouble()
                self.assertAlmostEqual(
                    plug_radians,
                    math.radians(ui_degrees),
                    delta=1e-6,
                    msg=f"{attr} の getAttr 度数値と MPlug ラジアン値が一致しません",
                )
                non_zero_radians.append(abs(plug_radians))

        self.assertGreater(
            max(non_zero_radians),
            0.01,
            "IK link inputRotate がほぼゼロで、二重ラジアン変換の再発が疑われます",
        )

    def test_disable_mmd_rig_constraints_for_runtime_bake_only_marked_constraints(self):
        """runtime bakeではMMD付与constraintとlive IK solverを無効化する"""
        source = cmds.spaceLocator(name="grant_source")[0]
        target = cmds.spaceLocator(name="grant_target")[0]
        other_source = cmds.spaceLocator(name="other_source")[0]
        other_target = cmds.spaceLocator(name="other_target")[0]
        ik_node = cmds.createNode("mmdCcdIk", name="runtime_disabled_ik_solver")
        ik_link = cmds.joint(name="runtime_disabled_ik_link")
        other_ik_node = cmds.createNode("mmdCcdIk", name="runtime_other_ik_solver")
        other_ik_link = cmds.joint(name="runtime_other_ik_link")

        marked = cmds.orientConstraint(source, target)[0]
        unmarked = cmds.orientConstraint(other_source, other_target)[0]
        cmds.addAttr(marked, longName="mmd_grant_constraint", attributeType="bool")
        cmds.setAttr(f"{marked}.mmd_grant_constraint", True)
        cmds.setAttr(f"{ik_node}.enabled", True)
        cmds.setAttr(f"{other_ik_node}.enabled", True)
        cmds.connectAttr(f"{ik_node}.outputRotate[0]", f"{ik_link}.rotate", force=True)
        cmds.connectAttr(f"{other_ik_node}.outputRotate[0]", f"{other_ik_link}.rotate", force=True)
        self.converter.bone_name_mapping = {
            "grant_target": target,
            "ik_link": ik_link,
        }

        self.converter._disable_mmd_rig_constraints_for_runtime_bake()

        self.assertEqual(cmds.getAttr(f"{marked}.nodeState"), 2)
        self.assertEqual(cmds.getAttr(f"{unmarked}.nodeState"), 0)
        self.assertFalse(cmds.getAttr(f"{ik_node}.enabled"))
        self.assertFalse(cmds.listConnections(f"{ik_link}.rotate", s=True, d=False, p=True) or [])
        self.assertTrue(cmds.getAttr(f"{other_ik_node}.enabled"))
        self.assertTrue(cmds.listConnections(f"{other_ik_link}.rotate", s=True, d=False, p=True) or [])

        cmds.delete(source, target, other_source, other_target, ik_node, ik_link, other_ik_node, other_ik_link)

    def test_restore_joints_to_bind_pose_for_runtime_bake_clears_live_values(self):
        """runtime bake 前にlive rig由来の残り値を消してbind姿勢へ戻す"""
        joint = cmds.joint(name="runtime_restore_bind_joint")
        cmds.setAttr(f"{joint}.translate", 1.0, 2.0, 3.0)
        cmds.setAttr(f"{joint}.rotate", 10.0, 20.0, 30.0)
        driver = cmds.createNode("animCurveTA", name="runtime_restore_rotate_driver")
        cmds.connectAttr(f"{driver}.output", f"{joint}.rotateX", force=True)

        self.converter.bone_name_mapping = {"センター": joint}
        self.converter._bone_bind_poses = {"センター": (4.0, 5.0, 6.0)}

        self.converter._restore_joints_to_bind_pose_for_runtime_bake()

        self.assertFalse(cmds.listConnections(f"{joint}.rotateX", s=True, d=False, p=True) or [])
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.translate")[0]), (4.0, 5.0, 6.0))
        self.assertEqual(tuple(round(v, 6) for v in cmds.getAttr(f"{joint}.rotate")[0]), (0.0, 0.0, 0.0))

        cmds.delete(joint, driver)

    def test_convert_exposes_live_rig_target_during_sparse_import_only(self):
        """convert 中の live rig 判定は legacy bone keying 中だけ状態として見える。"""
        frame = _bone_frame("センター", 0, (0.0, 0.0, 0.0))
        vmd_data = type("FakeVmdData", (), {})()
        vmd_data.bone_frames = [frame]
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = []
        vmd_data.ik_show_hide_frames = []

        def assert_live_flag(_frames):
            self.assertTrue(self.converter._current_import_live_rig_target)
            return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_has_live_mmd_rig_for_runtime_target", return_value=True))
            stack.enter_context(patch.object(self.converter, "_build_bone_hierarchy_and_order_maps"))
            stack.enter_context(patch.object(self.converter, "_build_runtime_bind_world_maps"))
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=False))
            stack.enter_context(patch.object(self.converter, "_apply_ik_enabled_animation"))
            stack.enter_context(patch.object(self.converter, "_convert_bone_animation", side_effect=assert_live_flag))

            self.assertTrue(self.converter.convert(vmd_data))

        self.assertFalse(self.converter._current_import_live_rig_target)
