"""VMD animation layer and batch keying tests."""

import math
import os
from unittest.mock import patch

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from mmd_tools.converters import vmd_scene_keying
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from tests.common.maya_test_base import MayaTestBase


def _bone_frame(bone_name, frame_number, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    frame = VmdBoneFrame()
    frame.bone_name = bone_name
    frame.frame_number = frame_number
    frame.position = position
    frame.rotation = rotation
    return frame


class TestVmdAnimLayerKeying(MayaTestBase):
    """Animation layer and batch keying behavior tests for VMD import."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_legacy_bone_anim_layer_simple_path_uses_batch_keying(self):
        """通常ボーンの animLayer keying は per-frame setKeyframe ではなく batch helper を使う。"""
        joint = cmds.joint(name="legacy_layer_batch_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        cmds.select(clear=True)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("legacy_bone_batch_layer", override=False, weight=1.0)
        self.converter.set_bone_name_mapping({"センター": joint})
        self.converter._bone_bind_poses["センター"] = (10.0, 0.0, 0.0)
        frames = [
            _bone_frame("センター", 0, (0.0, 0.0, 0.0)),
            _bone_frame("センター", 5, (2.0, 0.0, 0.0)),
        ]

        with patch.object(
            self.converter,
            "_batch_key_scalar_channels",
            wraps=self.converter._batch_key_scalar_channels,
        ) as batch_key:
            self.assertTrue(self.converter._convert_bone_animation(frames))

        joint_batch_calls = [call for call in batch_key.call_args_list if call.args[0] == joint]
        self.assertEqual(len(joint_batch_calls), 1)
        self.assertEqual(joint_batch_calls[0].kwargs.get("animation_layer"), self.converter.anim_layer)

        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertIn(f"{joint}.translateX", layer_attrs)
        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 12.0, places=6)

        cmds.delete(joint)

    def test_bone_animation_keying_error_propagates(self):
        """API keying failure is not swallowed as a missing-bone conversion error."""
        joint = cmds.joint(name="bone_keying_error_joint")
        cmds.select(clear=True)
        self.converter.set_bone_name_mapping({"センター": joint})
        frame = _bone_frame("センター", 0, (0.0, 0.0, 0.0))
        error = vmd_scene_keying.VmdKeyingError("forced keying failure")

        with patch.object(self.converter, "_set_bone_keyframes", side_effect=error):
            with self.assertRaises(vmd_scene_keying.VmdKeyingError):
                self.converter._convert_bone_animation([frame])

        self.assertNotIn("センター", self.converter._failed_bones)
        cmds.delete(joint)

    def test_live_rig_anim_layer_simple_path_uses_batch_keying(self):
        """live rig 対象の sparse import でも per-frame setKeyframe に戻さない。"""
        joint = cmds.joint(name="legacy_layer_live_rig_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        cmds.select(clear=True)
        cmds.currentTime(100, edit=True)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("legacy_bone_live_rig_layer", override=False, weight=1.0)
        self.converter._current_import_live_rig_target = True
        self.converter.set_bone_name_mapping({"センター": joint})
        self.converter._bone_bind_poses["センター"] = (10.0, 0.0, 0.0)
        frames = [
            _bone_frame("センター", 0, (0.0, 0.0, 0.0)),
            _bone_frame("センター", 5, (2.0, 0.0, 0.0)),
        ]

        with patch.object(
            self.converter,
            "_batch_key_scalar_channels",
            wraps=self.converter._batch_key_scalar_channels,
        ) as batch_key, patch(
            "mmd_tools.converters.vmd_scene_keying.cmds.setKeyframe",
            wraps=cmds.setKeyframe,
        ) as set_keyframe:
            self.assertTrue(self.converter._convert_bone_animation(frames))

        joint_batch_calls = [call for call in batch_key.call_args_list if call.args[0] == joint]
        self.assertEqual(len(joint_batch_calls), 1)
        self.assertEqual(joint_batch_calls[0].kwargs.get("animation_layer"), self.converter.anim_layer)
        self.assertLessEqual(set_keyframe.call_count, 6)

        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertIn(f"{joint}.translateX", layer_attrs)
        self.assertEqual(cmds.currentTime(query=True), 100.0)
        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 12.0, places=6)

        cmds.delete(joint)

    def test_batch_key_scalar_channels_anim_layer_rotate_uses_api_delta_units(self):
        """animLayer rotate の batch key は degree delta を API curve の radian 値へ変換する。"""
        joint = cmds.joint(name="scalar_layer_rotate_joint")
        cmds.setAttr(f"{joint}.rotate", 30.0, -20.0, 5.0, type="double3")
        cmds.currentTime(100, edit=True)
        layer = cmds.animLayer("scalar_layer_rotate_api_layer", override=False, weight=1.0)

        self.assertTrue(
            self.converter._batch_key_scalar_channels(
                joint,
                {
                    "rotateX": [(0.0, 0.0), (5.0, 15.0)],
                    "rotateY": [(0.0, 0.0), (5.0, -10.0)],
                    "rotateZ": [(0.0, 0.0), (5.0, 25.0)],
                },
                animation_layer=layer,
            )
        )

        layer_attrs = cmds.animLayer(layer, query=True, attribute=True) or []
        for attr in ("rotateX", "rotateY", "rotateZ"):
            self.assertIn(f"{joint}.{attr}", layer_attrs)

        rotate_curves = {}
        layer_curves = set(cmds.animLayer(layer, query=True, animCurves=True) or [])
        blend_nodes = cmds.listConnections(f"{joint}.rotateX", source=True, destination=False) or []
        self.assertTrue(blend_nodes)
        blend_node = blend_nodes[0]
        for attr, input_attr in {
            "rotateX": "inputBX",
            "rotateY": "inputBY",
            "rotateZ": "inputBZ",
        }.items():
            for curve_name in cmds.listConnections(f"{blend_node}.{input_attr}", source=True, type="animCurve") or []:
                if curve_name in layer_curves:
                    selection = om.MSelectionList()
                    selection.add(curve_name)
                    rotate_curves[attr] = oma.MFnAnimCurve(selection.getDependNode(0))
                    break

        self.assertEqual(set(rotate_curves), {"rotateX", "rotateY", "rotateZ"})
        for attr, expected_delta in {"rotateX": 15.0, "rotateY": -10.0, "rotateZ": 25.0}.items():
            rotate_curve = rotate_curves[attr]
            self.assertEqual(
                [rotate_curve.input(index).value for index in range(rotate_curve.numKeys)],
                [0.0, 5.0],
            )
            key_index = rotate_curve.find(om.MTime(5.0, om.MTime.uiUnit()))
            self.assertGreaterEqual(int(key_index), 0)
            self.assertAlmostEqual(rotate_curve.value(int(key_index)), math.radians(expected_delta), places=6)
        self.assertAlmostEqual(cmds.currentTime(query=True), 100.0, places=6)

        cmds.currentTime(0, edit=True)
        rx, ry, rz = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(rx, 30.0, places=6)
        self.assertAlmostEqual(ry, -20.0, places=6)
        self.assertAlmostEqual(rz, 5.0, places=6)
        cmds.currentTime(5, edit=True)
        rx, ry, rz = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(rx, 45.0, places=5)
        self.assertAlmostEqual(ry, -30.0, places=5)
        self.assertAlmostEqual(rz, 30.0, places=5)

        cmds.delete(joint)

    def test_batch_key_scalar_channels_anim_layer_rotate_xyz_uses_axis_curves(self):
        """rotateY/Z keying は seed 済み rotateX curve を再利用しない。"""
        joint = cmds.joint(name="scalar_layer_rotate_xyz_joint")
        cmds.currentTime(100, edit=True)
        layer = cmds.animLayer("scalar_layer_rotate_xyz_api_layer", override=False, weight=1.0)
        self.converter.use_animation_layers = True
        self.converter.anim_layer = layer
        self.converter._add_attrs_to_anim_layer(joint, ["rotateX"])
        cmds.setKeyframe(joint, attribute="rotateX", time=0.0, value=5.0, animLayer=layer)

        self.assertTrue(
            self.converter._batch_key_scalar_channels(
                joint,
                {
                    "rotateY": [(0.0, 0.0), (5.0, 20.0)],
                    "rotateZ": [(0.0, 0.0), (5.0, 30.0)],
                },
                animation_layer=layer,
            )
        )

        blend_nodes = cmds.listConnections(f"{joint}.rotateX", source=True, destination=False) or []
        self.assertTrue(blend_nodes)
        blend_node = blend_nodes[0]
        self.assertEqual(cmds.nodeType(blend_node), "animBlendNodeAdditiveRotation")
        axis_curves = []
        for input_attr in ("inputBX", "inputBY", "inputBZ"):
            curves = cmds.listConnections(f"{blend_node}.{input_attr}", source=True, type="animCurve") or []
            self.assertTrue(curves)
            axis_curves.append(curves[0])
        self.assertEqual(len(set(axis_curves)), 3)

        self.assertAlmostEqual(cmds.currentTime(query=True), 100.0, places=6)
        cmds.currentTime(5, edit=True)
        rx, ry, rz = cmds.getAttr(f"{joint}.rotate")[0]
        self.assertAlmostEqual(rx, 5.0, places=5)
        self.assertAlmostEqual(ry, 20.0, places=5)
        self.assertAlmostEqual(rz, 30.0, places=5)

        cmds.delete(joint)

    def test_batch_key_scalar_channels_anim_layer_api_path_does_not_build_fallback_base_values(self):
        """API keying 成功時は fallback 用の per-key base 値を作らない。"""
        joint = cmds.joint(name="scalar_layer_no_fallback_base_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        layer = cmds.animLayer("scalar_layer_no_fallback_base_layer", override=False, weight=1.0)

        with patch.object(vmd_scene_keying, "_base_ui_value_at_frame", side_effect=AssertionError):
            self.assertTrue(
                self.converter._batch_key_scalar_channels(
                    joint,
                    {"translateX": [(0.0, 5.0), (5.0, 2.0)]},
                    animation_layer=layer,
                )
            )

        cmds.currentTime(0, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 15.0, places=6)
        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 12.0, places=6)

        cmds.delete(joint)

    def test_batch_key_scalar_channels_anim_layer_failure_raises_without_fallback_opt_in(self):
        """API keying 失敗時は env opt-in なしでは詳細付きで失敗する。"""
        joint = cmds.joint(name="scalar_layer_raise_no_fallback_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        layer = cmds.animLayer("scalar_layer_raise_no_fallback_layer", override=False, weight=1.0)

        original_create = vmd_scene_keying._create_scalar_channel_curves

        class FailingCurve:
            def addKeys(self, *_args, **_kwargs):
                raise RuntimeError("forced addKeys failure")

        def create_failing_curves(*args, **kwargs):
            real_curves = original_create(*args, **kwargs)
            return {attr: FailingCurve() for attr in real_curves}

        with patch.object(vmd_scene_keying, "_create_scalar_channel_curves", side_effect=create_failing_curves):
            with self.assertRaises(vmd_scene_keying.VmdKeyingError) as raised:
                self.converter._batch_key_scalar_channels(
                    joint,
                    {"translateX": [(0.0, 5.0), (5.0, 2.0)]},
                    animation_layer=layer,
                )

        message = str(raised.exception)
        self.assertIn(f"node_attr={joint}.translateX", message)
        self.assertIn(f"animation_layer={layer}", message)
        self.assertIn("reason=addKeys failed", message)
        self.assertIn("curve_candidates=", message)

        cmds.delete(joint)

    def test_batch_key_scalar_channels_fallback_env_zero_does_not_opt_in(self):
        """MMD_TOOLS_VMD_ALLOW_SETKEYFRAME_FALLBACK=0 は fallback 許可にしない。"""
        joint = cmds.joint(name="scalar_layer_fallback_zero_env_joint")
        layer = cmds.animLayer("scalar_layer_fallback_zero_env_layer", override=False, weight=1.0)

        class FailingCurve:
            def addKeys(self, *_args, **_kwargs):
                raise RuntimeError("forced addKeys failure")

        with patch.object(vmd_scene_keying, "_create_scalar_channel_curves", return_value={"translateX": FailingCurve()}):
            with patch.dict(os.environ, {"MMD_TOOLS_VMD_ALLOW_SETKEYFRAME_FALLBACK": "0"}):
                with self.assertRaises(vmd_scene_keying.VmdKeyingError):
                    self.converter._batch_key_scalar_channels(
                        joint,
                        {"translateX": [(0.0, 5.0)]},
                        animation_layer=layer,
                    )

        cmds.delete(joint)

    def test_batch_key_scalar_channels_anim_layer_fallback_uses_base_snapshot_when_opted_in(self):
        """opt-in fallback は前の fallback key の寄与を base 値に混ぜない。"""
        joint = cmds.joint(name="scalar_layer_fallback_base_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        layer = cmds.animLayer("scalar_layer_fallback_base_layer", override=False, weight=1.0)

        original_create = vmd_scene_keying._create_scalar_channel_curves

        class FailingCurve:
            def addKeys(self, *_args, **_kwargs):
                raise RuntimeError("forced addKeys failure")

        def create_failing_curves(*args, **kwargs):
            real_curves = original_create(*args, **kwargs)
            return {attr: FailingCurve() for attr in real_curves}

        with patch.dict(os.environ, {"MMD_TOOLS_VMD_ALLOW_SETKEYFRAME_FALLBACK": "1"}):
            with patch.object(vmd_scene_keying, "_create_scalar_channel_curves", side_effect=create_failing_curves):
                self.assertTrue(
                    self.converter._batch_key_scalar_channels(
                        joint,
                        {"translateX": [(0.0, 5.0), (5.0, 2.0)]},
                        animation_layer=layer,
                    )
                )

        cmds.currentTime(0, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 15.0, places=6)
        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 12.0, places=6)

        cmds.delete(joint)

    def test_direct_anim_curve_helper_creates_keyed_values(self):
        """_batch_create_and_key_curves が MFnAnimCurve / addKeys 経由で translate/rotate にキーを登録する。"""
        joint = cmds.joint(name="test_direct_apikey_joint")
        samples = {
            "translateX": [(0.0, 0.0), (12.0, 1.0)],
            "translateY": [(0.0, 0.0), (12.0, 2.0)],
            "translateZ": [(0.0, 0.0), (12.0, -3.0)],
            "rotateX": [(0.0, 0.0), (12.0, math.radians(30.0))],
            "rotateY": [(0.0, 0.0), (12.0, 0.0)],
            "rotateZ": [(0.0, 0.0), (12.0, 0.0)],
        }
        ok = self.converter._batch_create_and_key_curves(joint, samples)
        self.assertTrue(ok, "direct animCurve helper should succeed or fallback with keys")

        for attr in ("translateX", "rotateX"):
            times = cmds.keyframe(f"{joint}.{attr}", query=True, timeChange=True) or []
            self.assertIn(0.0, times)
            self.assertIn(12.0, times)

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 1.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 2.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateZ"), -3.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 30.0, places=5)

        cmds.delete(joint)

    def test_runtime_array_keying_uses_anim_layer_deltas(self):
        """runtime bake の joint array keying は既存値を壊さず animLayer に差分値を入れる。"""
        joint = cmds.joint(name="test_runtime_layer_delta_joint")
        cmds.setAttr(f"{joint}.translateX", 10.0)
        cmds.setAttr(f"{joint}.rotateX", 30.0)
        self.converter.use_animation_layers = True
        self.converter.anim_layer = "runtime_delta_layer"

        captured = {}
        create_calls = []

        class FakeCurve:
            def __init__(self, attr):
                self.attr = attr

            def addKeys(self, _times, values, *_args):
                captured[self.attr] = [float(values[i]) for i in range(len(values))]

        def fake_create(_node, attrs, tangent_type=None, animation_layer=None):
            create_calls.append((list(attrs), animation_layer))
            return {attr: FakeCurve(attr) for attr in attrs}

        times = om.MTimeArray()
        for frame in (1.0, 2.0):
            times.append(om.MTime(frame, om.MTime.uiUnit()))
        channel_values = {
            "translateX": om.MDoubleArray([11.0, 12.0]),
            "rotateX": om.MDoubleArray([math.radians(40.0), math.radians(50.0)]),
        }

        with patch("mmd_tools.converters.vmd_scene_keying.maya_utils.create_animation_curves", side_effect=fake_create):
            keyed, skipped = self.converter._batch_create_and_key_curve_arrays(
                joint,
                channel_values,
                {"translateX": {}, "rotateX": {}},
                times,
                [1.0, 2.0],
            )

        self.assertEqual((keyed, skipped), (2, 0))
        self.assertEqual(create_calls[0][1], "runtime_delta_layer")
        self.assertListAlmostEqual(captured["translateX"], [1.0, 2.0], places=6)
        self.assertListAlmostEqual(captured["rotateX"], [math.radians(10.0), math.radians(20.0)], places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateX"), 10.0, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.rotateX"), 30.0, places=6)

        cmds.delete(joint)

    def test_runtime_static_array_keying_uses_anim_layer_constant_delta(self):
        """runtime bake の静的 channel は animLayer 使用時に base setAttr ではなく定数差分キーになる。"""
        joint = cmds.joint(name="test_runtime_layer_static_joint")
        cmds.setAttr(f"{joint}.translateY", 10.0)
        self.converter.use_animation_layers = True
        self.converter.anim_layer = "runtime_static_layer"

        captured = {}

        class FakeCurve:
            def __init__(self, attr):
                self.attr = attr

            def addKeys(self, _times, values, *_args):
                captured[self.attr] = [float(values[i]) for i in range(len(values))]

        times = om.MTimeArray()
        for frame in (1.0, 2.0):
            times.append(om.MTime(frame, om.MTime.uiUnit()))

        with patch(
            "mmd_tools.converters.vmd_scene_keying.maya_utils.create_animation_curves",
            return_value={"translateY": FakeCurve("translateY")},
        ):
            keyed, skipped = self.converter._batch_create_and_key_curve_arrays(
                joint,
                {"translateY": None},
                {"translateY": {"is_static": True, "first": 15.0}},
                times,
                [1.0, 2.0],
            )

        self.assertEqual((keyed, skipped), (1, 0))
        self.assertListAlmostEqual(captured["translateY"], [5.0, 5.0], places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{joint}.translateY"), 10.0, places=6)

        cmds.delete(joint)
