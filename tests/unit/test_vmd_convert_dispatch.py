"""VmdConverter.convert dispatch policy tests."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from mmd_tools.converters.vmd_context import (
    VmdCameraAnimationContext,
    VmdImportContext,
    VmdLightAnimationContext,
    VmdMorphAnimationContext,
    VmdRuntimeCacheCollectContext,
    VmdRuntimeSceneApplyContext,
)
from mmd_tools.converters.vmd_converter import VmdConverter


def _fake_vmd_data(**overrides):
    defaults = {
        "bone_frames": [],
        "morph_frames": [],
        "camera_frames": [],
        "light_frames": [],
    }
    defaults.update(overrides)
    return type("FakeVmdData", (), defaults)()


class TestVmdConvertDispatch(unittest.TestCase):
    """Tests for convert() routing that do not need real scene conversion."""

    def setUp(self):
        self.converter = VmdConverter()

    def test_import_context_captures_convert_options_and_current_channel_flags(self):
        """convert() dispatch state is bundled before split helpers consume it."""
        vmd_data = _fake_vmd_data()
        profile = {}

        def progress_callback(_value):
            return None

        self.converter.import_camera_animation = False
        self.converter.import_light_animation = True

        context = self.converter._import_context(
            vmd_data,
            target_namespace="model_ns",
            layer_name="Layer_A",
            bake_mode=True,
            clear_existing_motion=True,
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            pmx_path="model.pmx",
            profile=profile,
            progress_callback=progress_callback,
        )

        self.assertIsInstance(context, VmdImportContext)
        self.assertIs(context.vmd_data, vmd_data)
        self.assertEqual(context.target_namespace, "model_ns")
        self.assertEqual(context.layer_name, "Layer_A")
        self.assertTrue(context.bake_mode)
        self.assertTrue(context.clear_existing_motion)
        self.assertEqual(context.vmd_bytes, b"vmd")
        self.assertEqual(context.pmx_bytes, b"pmx")
        self.assertEqual(context.pmx_path, "model.pmx")
        self.assertIs(context.profile, profile)
        self.assertIs(context.progress_callback, progress_callback)
        self.assertFalse(context.import_camera_animation)
        self.assertTrue(context.import_light_animation)

    def test_split_helper_context_factories_bind_current_converter_state(self):
        """VMD helper contexts expose explicit state and callables for split modules."""
        self.converter.anim_layer = "VMD_Layer"
        self.converter.use_animation_layers = True
        self.converter._vmd_import_refresh_suspended = True

        cache_context = self.converter._runtime_cache_collect_context()
        apply_context = self.converter._runtime_scene_apply_context()
        camera_context = self.converter._camera_animation_context()
        light_context = self.converter._light_animation_context()
        morph_context = self.converter._morph_animation_context()

        self.assertIsInstance(cache_context, VmdRuntimeCacheCollectContext)
        self.assertIsInstance(apply_context, VmdRuntimeSceneApplyContext)
        self.assertIsInstance(camera_context, VmdCameraAnimationContext)
        self.assertIsInstance(light_context, VmdLightAnimationContext)
        self.assertIsInstance(morph_context, VmdMorphAnimationContext)
        self.assertEqual(cache_context.get_anim_layer(), "VMD_Layer")
        self.assertTrue(cache_context.outer_refresh_suspended)
        self.assertTrue(apply_context.outer_refresh_suspended)
        self.assertEqual(camera_context.anim_layer, "VMD_Layer")
        self.assertEqual(light_context.anim_layer, "VMD_Layer")
        self.assertEqual(morph_context.anim_layer, "VMD_Layer")
        self.assertIs(camera_context.get_or_create_camera.__self__, self.converter)
        self.assertIs(light_context.get_or_create_light.__self__, self.converter)
        self.assertIs(morph_context.batch_key_scalar_channels.__self__, self.converter)
        self.assertIs(cache_context.compute_all_bone_locals.__self__, self.converter)
        self.assertIs(apply_context.batch_create_and_key_curve_arrays.__self__, self.converter)

    def test_runtime_bake_success_still_converts_camera_and_light(self):
        """Normal runtime bake leaves camera/light on the sparse scene path."""
        frame = type("FrameStub", (), {"frame_number": 1})()
        vmd_data = _fake_vmd_data(
            bone_frames=[frame],
            morph_frames=[frame],
            camera_frames=[frame],
            light_frames=[frame],
        )

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=True))
            stack.enter_context(patch.object(self.converter, "_convert_using_mmd_runtime", return_value=True))
            apply_ik = stack.enter_context(patch.object(self.converter, "_apply_ik_enabled_animation"))
            convert_bone = stack.enter_context(patch.object(self.converter, "_convert_bone_animation"))
            convert_morph = stack.enter_context(patch.object(self.converter, "_convert_morph_animation"))
            convert_camera = stack.enter_context(
                patch.object(self.converter, "_convert_camera_animation", return_value=True)
            )
            convert_light = stack.enter_context(patch.object(self.converter, "_convert_light_animation", return_value=True))
            result = self.converter.convert(vmd_data, vmd_bytes=b"vmd", pmx_bytes=b"pmx")

        self.assertTrue(result)
        apply_ik.assert_not_called()
        convert_bone.assert_not_called()
        convert_morph.assert_not_called()
        convert_camera.assert_called_once_with(vmd_data.camera_frames, vmd_bytes=None)
        convert_light.assert_called_once_with(vmd_data.light_frames, vmd_bytes=None)

    def test_runtime_bake_failure_records_profile_warning_before_legacy_fallback(self):
        """Runtime bake failure is visible to the action boundary via profile warnings."""
        vmd_data = _fake_vmd_data()
        profile = {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=True))
            stack.enter_context(patch.object(self.converter, "_convert_using_mmd_runtime", return_value=False))
            apply_ik = stack.enter_context(patch.object(self.converter, "_apply_ik_enabled_animation"))
            result = self.converter.convert(
                vmd_data,
                bake_mode=True,
                vmd_bytes=b"vmd",
                pmx_bytes=b"pmx",
                profile=profile,
            )

        self.assertTrue(result)
        apply_ik.assert_called_once()
        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(warning["source"], "vmd_converter")
        self.assertEqual(warning["code"], "runtime_bake_failed_fallback")
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(warning["fallback"], "legacy")
        self.assertTrue(warning["bake_mode"])
        self.assertTrue(warning["has_vmd_bytes"])
        self.assertTrue(warning["has_pmx_bytes"])

    def test_bake_mode_passes_vmd_bytes_to_camera_and_light_samplers(self):
        """Bake mode passes raw VMD bytes to camera/light native samplers."""
        frame = type("FrameStub", (), {"frame_number": 1})()
        vmd_data = _fake_vmd_data(
            bone_frames=[frame],
            morph_frames=[],
            camera_frames=[frame],
            light_frames=[frame],
        )

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=True))
            stack.enter_context(patch.object(self.converter, "_convert_using_mmd_runtime", return_value=True))
            convert_camera = stack.enter_context(
                patch.object(self.converter, "_convert_camera_animation", return_value=True)
            )
            convert_light = stack.enter_context(patch.object(self.converter, "_convert_light_animation", return_value=True))
            result = self.converter.convert(vmd_data, bake_mode=True, vmd_bytes=b"vmd", pmx_bytes=b"pmx")

        self.assertTrue(result)
        convert_camera.assert_called_once_with(vmd_data.camera_frames, vmd_bytes=b"vmd")
        convert_light.assert_called_once_with(vmd_data.light_frames, vmd_bytes=b"vmd")

    def test_convert_clears_camera_and_light_before_scene_motion_conversion(self):
        """Camera/light keys are cleared immediately before each channel conversion."""
        frame = type("FrameStub", (), {"frame_number": 1})()
        vmd_data = _fake_vmd_data(camera_frames=[frame], light_frames=[frame])
        order = []

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=False))
            stack.enter_context(
                patch.object(self.converter, "_clear_existing_camera_motion", side_effect=lambda: order.append("clear_camera"))
            )
            stack.enter_context(
                patch.object(
                    self.converter,
                    "_convert_camera_animation",
                    side_effect=lambda *_args, **_kwargs: order.append("convert_camera") or True,
                )
            )
            stack.enter_context(
                patch.object(self.converter, "_clear_existing_light_motion", side_effect=lambda: order.append("clear_light"))
            )
            stack.enter_context(
                patch.object(
                    self.converter,
                    "_convert_light_animation",
                    side_effect=lambda *_args, **_kwargs: order.append("convert_light") or True,
                )
            )
            result = self.converter.convert(vmd_data)

        self.assertTrue(result)
        self.assertEqual(order, ["clear_camera", "convert_camera", "clear_light", "convert_light"])

    def test_convert_clear_existing_motion_does_not_clear_model_keys_for_camera_only_vmd(self):
        """Camera-only VMD does not run model motion clearing."""
        frame = type("FrameStub", (), {"frame_number": 1})()
        vmd_data = _fake_vmd_data(camera_frames=[frame], ik_show_hide_frames=[])

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=False))
            model_clear = stack.enter_context(patch.object(self.converter, "_clear_existing_motion"))
            camera_clear = stack.enter_context(patch.object(self.converter, "_clear_existing_camera_motion"))
            stack.enter_context(patch.object(self.converter, "_convert_camera_animation", return_value=True))
            result = self.converter.convert(vmd_data, clear_existing_motion=True)

        self.assertTrue(result)
        model_clear.assert_not_called()
        camera_clear.assert_called_once()

    def test_convert_does_not_clear_missing_camera_or_light_channels(self):
        """Missing camera/light frames do not trigger channel clear or conversion."""
        vmd_data = _fake_vmd_data()

        with ExitStack() as stack:
            clear_camera = stack.enter_context(patch.object(self.converter, "_clear_existing_camera_motion"))
            convert_camera = stack.enter_context(
                patch.object(self.converter, "_convert_camera_animation", return_value=True)
            )
            clear_light = stack.enter_context(patch.object(self.converter, "_clear_existing_light_motion"))
            convert_light = stack.enter_context(patch.object(self.converter, "_convert_light_animation", return_value=True))
            result = self.converter.convert(vmd_data)

        self.assertTrue(result)
        clear_camera.assert_not_called()
        convert_camera.assert_not_called()
        clear_light.assert_not_called()
        convert_light.assert_not_called()

    def test_convert_suppresses_undo_and_refresh_for_full_import(self):
        """Full VMD import suppresses undo and viewport refresh, then restores them."""
        vmd_data = _fake_vmd_data()
        undo_calls = []
        refresh_calls = []

        def fake_undo_info(*_args, **kwargs):
            undo_calls.append(kwargs)
            if kwargs.get("q") and kwargs.get("state"):
                return True
            return None

        def fake_refresh(*_args, **kwargs):
            refresh_calls.append(kwargs.get("suspend"))

        with patch("mmd_tools.converters.vmd_converter.cmds.undoInfo", side_effect=fake_undo_info), patch(
            "mmd_tools.converters.vmd_converter.cmds.refresh",
            side_effect=fake_refresh,
        ):
            self.assertTrue(self.converter.convert(vmd_data))

        self.assertEqual(refresh_calls, [True, False])
        self.assertIn({"stateWithoutFlush": False}, undo_calls)
        self.assertIn({"stateWithoutFlush": True}, undo_calls)
        self.assertFalse(self.converter._vmd_import_refresh_suspended)

    def test_camera_and_light_import_flags_skip_channels(self):
        """Camera/light import flags prevent channel clear and conversion."""
        vmd_data = _fake_vmd_data(camera_frames=[object()], light_frames=[object()])
        self.converter.import_camera_animation = False
        self.converter.import_light_animation = False

        with ExitStack() as stack:
            clear_camera = stack.enter_context(patch.object(self.converter, "_clear_existing_camera_motion"))
            convert_camera = stack.enter_context(
                patch.object(self.converter, "_convert_camera_animation", return_value=True)
            )
            clear_light = stack.enter_context(patch.object(self.converter, "_clear_existing_light_motion"))
            convert_light = stack.enter_context(patch.object(self.converter, "_convert_light_animation", return_value=True))
            result = self.converter.convert(vmd_data)

        self.assertTrue(result)
        clear_camera.assert_not_called()
        convert_camera.assert_not_called()
        clear_light.assert_not_called()
        convert_light.assert_not_called()


if __name__ == "__main__":
    unittest.main()
