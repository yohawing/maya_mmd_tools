"""Tests for shared VMD contexts and frame values."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mmd_tools.converters.vmd_context import (
    VmdMorphAnimationContext,
    resolve_morph_animation_context,
    vmd_frame_value,
)


class TestVmdMorphAnimationContext(unittest.TestCase):
    """Preserve supported inputs and their resolution order."""

    def setUp(self):
        self.context = VmdMorphAnimationContext(
            logger=Mock(),
            morph_name_mapping={"smile": object()},
            anim_layer="morph_layer",
            use_animation_layers=True,
            iter_morph_mappings=Mock(),
            vmd_frame_to_maya_time=Mock(),
            samples_as_anim_layer_deltas=Mock(),
            batch_key_scalar_channels=Mock(),
        )

    def test_direct_context_precedes_factory(self):
        with patch.object(VmdMorphAnimationContext, "_morph_animation_context", create=True) as factory:
            self.assertIs(resolve_morph_animation_context(self.context), self.context)
            factory.assert_not_called()

    def test_factory_precedes_legacy_attributes(self):
        factory = Mock(return_value=self.context)
        converter = SimpleNamespace(_morph_animation_context=factory)
        self.assertIs(resolve_morph_animation_context(converter), self.context)
        factory.assert_called_once_with()

    def test_legacy_converter_preserves_all_fields(self):
        converter = SimpleNamespace(
            logger=self.context.logger,
            morph_name_mapping=self.context.morph_name_mapping,
            anim_layer=self.context.anim_layer,
            use_animation_layers=self.context.use_animation_layers,
            _iter_morph_mappings=self.context.iter_morph_mappings,
            vmd_frame_to_maya_time=self.context.vmd_frame_to_maya_time,
            _samples_as_anim_layer_deltas=self.context.samples_as_anim_layer_deltas,
            _batch_key_scalar_channels=self.context.batch_key_scalar_channels,
        )
        for factory_present in (False, True):
            with self.subTest(factory_present=factory_present):
                if factory_present:
                    converter._morph_animation_context = None
                resolved = resolve_morph_animation_context(converter)
                for name, value in vars(self.context).items():
                    self.assertIs(getattr(resolved, name), value)


class TestVmdFrameValue(unittest.TestCase):
    """Preserve attribute, alias, dictionary-key, and default precedence."""

    def test_attributes_precede_dictionary_keys(self):
        class Frame(dict):
            pass

        frame = Frame(frame_number=30, frame=40)
        frame.frame_number = 10
        frame.frame = 20
        self.assertEqual(vmd_frame_value(frame, "frame_number", "frame", -1), 10)
        del frame.frame_number
        self.assertEqual(vmd_frame_value(frame, "frame_number", "frame", -1), 20)
        del frame.frame
        self.assertEqual(vmd_frame_value(frame, "frame_number", "frame", -1), 40)
        del frame["frame"]
        self.assertEqual(vmd_frame_value(frame, "frame_number", "frame", -1), 30)
        del frame["frame_number"]
        self.assertEqual(vmd_frame_value(frame, "frame_number", "frame", -1), -1)

    def test_missing_attributes_return_default(self):
        self.assertEqual(vmd_frame_value(object(), "frame_number", "frame", -1), -1)

    def test_present_none_is_not_replaced_by_fallback(self):
        frames = (
            SimpleNamespace(frame_number=None, frame=20),
            SimpleNamespace(frame=None),
            {"frame": None, "frame_number": 30},
            {"frame_number": None},
        )
        for frame in frames:
            with self.subTest(frame=frame):
                self.assertIsNone(vmd_frame_value(frame, "frame_number", "frame", -1))
