"""VMD light animation tests.

These tests exercise scene-editing light conversion behavior separately from
the broad VmdConverter test module.
"""

from unittest.mock import patch

import maya.cmds as cmds

import mmd_tools.converters.vmd_light_animation as vmd_light_animation_module
from mmd_tools.converters.light_converter import create_mmd_light_controller
from mmd_tools.converters.vmd_context import VmdLightAnimationContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_light_animation import convert_light_animation
from mmd_tools.core.constants import DEFAULT_LIGHT_NAME
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.light_frame import VmdLightFrame
from tests.common.maya_test_base import MayaTestBase


def _light_frame(frame_number: int, position, color) -> VmdLightFrame:
    frame = VmdLightFrame()
    frame.frame_number = frame_number
    frame.position = position
    frame.color = color
    return frame


class TestVmdLightAnimation(MayaTestBase):
    """VMD light conversion tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def tearDown(self):
        if cmds.objExists(DEFAULT_LIGHT_NAME):
            cmds.delete(DEFAULT_LIGHT_NAME)
        for layer in cmds.ls(type="animLayer"):
            if layer != "BaseAnimation":
                try:
                    cmds.delete(layer)
                except Exception:
                    pass
        super().tearDown()

    def test_convert_light_animation(self):
        """Light conversion creates color and rotate keys."""
        light_frames = [
            _light_frame(i * 10, (0.5, -1.0, 1.0), (1.0 - i * 0.1, 1.0 - i * 0.1, 1.0 - i * 0.1))
            for i in range(3)
        ]

        result = self.converter._convert_light_animation(light_frames)
        self.assertTrue(result)
        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        for attr in ("rotateX", "rotateY", "rotateZ"):
            keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.{attr}", query=True, timeChange=True)
            self.assertIsNotNone(keys, f"{attr} に keyframe がありません")
            self.assertEqual(len(keys), 3, f"{attr} の keyframe 数が期待と異なります")

    def test_convert_light_animation_zero_vector_skips_rotation(self):
        """Zero-vector light frames keep color keys but skip rotation keys."""
        light_frames = [
            _light_frame(0, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
            _light_frame(10, (0.5, -0.5, 1.0), (0.5, 0.5, 0.5)),
        ]

        result = self.converter._convert_light_animation(light_frames)
        self.assertTrue(result)
        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        color_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.colorR", query=True, timeChange=True)
        self.assertEqual(len(color_keys), 2)
        self.assertIn(0.0, color_keys)
        self.assertIn(10.0, color_keys)

        rot_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.rotateX", query=True, timeChange=True)
        self.assertEqual(len(rot_keys), 1)
        self.assertIn(10.0, rot_keys)
        self.assertNotIn(0.0, rot_keys)

    def test_runtime_light_sampling_dense_keys_maya_frames(self):
        """Runtime light samples are keyed on dense Maya frames."""
        frame0 = _light_frame(0, (0.0, -1.0, 0.0), (1.0, 1.0, 1.0))
        frame1 = _light_frame(2, (1.0, -1.0, 0.0), (0.0, 0.5, 1.0))
        samples = [
            {"color": [1.0, 1.0, 1.0], "position": [0.0, -1.0, 0.0]},
            {"color": [0.5, 0.75, 1.0], "position": [0.5, -1.0, 0.0]},
            {"color": [0.0, 0.5, 1.0], "position": [1.0, -1.0, 0.0]},
        ]

        with patch.object(vmd_light_animation_module, "sample_vmd_light_frames", return_value=samples) as sampler:
            self.assertTrue(self.converter._convert_light_animation([frame0, frame1], vmd_bytes=b"vmd"))

        sampler.assert_called_once_with(b"vmd", 0.0, 1.0, 3)
        light_shape = cmds.listRelatives(DEFAULT_LIGHT_NAME, shapes=True, type="directionalLight")[0]
        self.assertEqual(cmds.keyframe(f"{light_shape}.colorR", query=True, timeChange=True), [0.0, 1.0, 2.0])
        self.assertEqual(cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.rotateX", query=True, timeChange=True), [0.0, 1.0, 2.0])
        cmds.currentTime(1, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{light_shape}.colorR"), 0.5, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{light_shape}.colorG"), 0.75, places=6)

    def test_convert_light_animation_uses_batch_keying_with_anim_layer(self):
        """Light color and rotation channels use batch keying on animLayers."""
        frames = [
            _light_frame(frame_number, (0.5, -1.0, 1.0), (color_r, color_r, color_r))
            for frame_number, color_r in ((0, 1.0), (10, 0.5))
        ]

        self.converter.anim_layer = cmds.animLayer("light_batch_layer", override=False, weight=1.0)

        with patch.object(
            self.converter,
            "_batch_key_scalar_channels",
            wraps=self.converter._batch_key_scalar_channels,
        ) as batch_key:
            self.assertTrue(self.converter._convert_light_animation(frames))

        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))
        light_shape = cmds.listRelatives(DEFAULT_LIGHT_NAME, shapes=True, type="directionalLight")[0]
        batch_nodes = [call.args[0] for call in batch_key.call_args_list]
        self.assertIn(light_shape, batch_nodes)
        self.assertIn(DEFAULT_LIGHT_NAME, batch_nodes)

        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertIn(f"{light_shape}.colorR", layer_attrs)
        self.assertIn(f"{DEFAULT_LIGHT_NAME}.rotateX", layer_attrs)

        cmds.currentTime(10, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{light_shape}.colorR"), 0.5, places=6)

    def test_convert_light_animation_accepts_direct_context(self):
        """Direct light contexts key through their provided collaborators."""
        frames = [_light_frame(0, (0.5, -1.0, 1.0), (0.25, 0.5, 0.75))]
        self.converter.anim_layer = cmds.animLayer("direct_light_context_layer", override=False, weight=1.0)
        captured = []

        def fake_batch_key_scalar_channels(node_name, channel_samples, animation_layer):
            captured.append((node_name, channel_samples, animation_layer))
            return True

        context = VmdLightAnimationContext(
            logger=self.converter.logger,
            anim_layer=self.converter.anim_layer,
            use_animation_layers=True,
            get_or_create_light=self.converter._get_or_create_light,
            vmd_frame_to_maya_time=self.converter.vmd_frame_to_maya_time,
            maya_time_to_vmd_frame=self.converter.maya_time_to_vmd_frame,
            add_attrs_to_anim_layer=self.converter._add_attrs_to_anim_layer,
            samples_as_anim_layer_deltas=self.converter._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=fake_batch_key_scalar_channels,
        )

        self.assertTrue(convert_light_animation(context, frames))

        self.assertEqual(len(captured), 2)
        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))
        self.assertEqual(captured[0][2], self.converter.anim_layer)
        self.assertEqual(captured[1][2], self.converter.anim_layer)

    def test_convert_light_animation_drives_mmd_light_controller_color(self):
        """PMX light controllers receive shader-facing color keys."""
        controller = create_mmd_light_controller()
        frame = _light_frame(10, (0.5, -1.0, 1.0), (0.25, 0.5, 0.75))

        self.assertTrue(self.converter._convert_light_animation([frame]))

        self.assertEqual(
            cmds.keyframe(f"{controller}.mmd_light_colorR", query=True, timeChange=True),
            [10.0],
        )
        cmds.currentTime(10, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.mmd_light_colorR"), 0.25, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.mmd_light_colorG"), 0.5, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.mmd_light_colorB"), 0.75, places=6)

    def test_clear_existing_light_motion_clears_controller_and_shape_color_keys(self):
        """Clearing light motion removes controller and legacy shape color keys."""
        controller = create_mmd_light_controller()
        light_shape = cmds.listRelatives(controller, shapes=True, type="directionalLight")[0]

        for source_plug in cmds.listConnections(f"{light_shape}.color", source=True, destination=False, plugs=True) or []:
            cmds.disconnectAttr(source_plug, f"{light_shape}.color")

        cmds.setKeyframe(controller, attribute="rotateX", time=3, value=20.0)
        cmds.setKeyframe(controller, attribute="mmd_light_colorR", time=3, value=0.4)
        cmds.setKeyframe(light_shape, attribute="colorR", time=3, value=0.8)

        self.converter._clear_existing_light_motion()

        self.assertIsNone(cmds.keyframe(controller, attribute="rotateX", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(controller, attribute="mmd_light_colorR", query=True, timeChange=True))
        self.assertIsNone(cmds.keyframe(light_shape, attribute="colorR", query=True, timeChange=True))

    def test_convert_light_animation_via_convert(self):
        """convert() dispatches VMD light frames to light conversion."""
        vmd_data = VmdData()
        vmd_data.bone_frames = []
        vmd_data.morph_frames = []
        vmd_data.camera_frames = []
        vmd_data.light_frames = [_light_frame(0, (0.5, -1.0, 0.5), (0.8, 0.8, 0.8))]
        vmd_data.shadow_frames = []
        vmd_data.ik_show_hide_frames = []
        vmd_data.header.model_name = "TestLight"

        result = self.converter.convert(vmd_data, scene_animation_only=True)
        self.assertTrue(result)
        self.assertTrue(cmds.objExists(DEFAULT_LIGHT_NAME))

        color_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.colorR", query=True, timeChange=True)
        self.assertIsNotNone(color_keys)
        self.assertGreater(len(color_keys), 0)
        rot_keys = cmds.keyframe(f"{DEFAULT_LIGHT_NAME}.rotateX", query=True, timeChange=True)
        self.assertIsNotNone(rot_keys)
        self.assertGreater(len(rot_keys), 0)
