"""Runtime morph cache tests for VMD import.

The broader VMD converter tests keep scene conversion coverage. This module
keeps runtime morph cache keying behavior isolated.
"""

from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from tests.common.maya_test_base import MayaTestBase


class TestVmdRuntimeMorphCache(MayaTestBase):
    """Runtime morph cache keying tests."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_runtime_morph_cache_uses_anim_layer_deltas(self):
        """Runtime morph cache sends deltas from existing weights to animLayer keying."""
        node = cmds.createNode("transform", name="test_runtime_morph_layer_node")
        cmds.addAttr(node, longName="weight", attributeType="double", keyable=True)
        cmds.setAttr(f"{node}.weight", 0.25)
        self.converter.use_animation_layers = True
        self.converter.anim_layer = "runtime_morph_layer"
        self.converter.morph_name_mapping = {"笑い": object()}

        captured = []

        def fake_key_scalar(node_name, channel_samples, animation_layer=None):
            captured.append((node_name, channel_samples, animation_layer))
            return True

        with patch.object(self.converter, "_iter_morph_mappings", return_value=[(node, "weight", "")]), patch.object(
            self.converter,
            "_batch_key_scalar_channels",
            side_effect=fake_key_scalar,
        ):
            self.converter._bake_morph_weight_cache_from_runtime([(3.0, [0.75])], ["笑い"])

        self.assertEqual(captured[0][0], node)
        self.assertEqual(captured[0][2], "runtime_morph_layer")
        self.assertEqual(captured[0][1], {"weight": [(3.0, 0.5)]})

        cmds.delete(node)

    def test_runtime_morph_cache_keys_blendshape_weight_on_anim_layer(self):
        """Runtime morph cache can key blendShape weight aliases on animLayers."""
        base = cmds.polyCube(name="runtime_morph_layer_base")[0]
        target = cmds.duplicate(base, name="runtime_morph_layer_target")[0]
        blend_shape = cmds.blendShape(target, base, name="runtime_morph_layer_blendShape")[0]
        cmds.aliasAttr("smile", f"{blend_shape}.weight[0]")
        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("runtime_morph_blendshape_layer", override=False, weight=1.0)
        self.converter.morph_name_mapping = {"笑い": (blend_shape, "weight[0]", "smile")}

        self.converter._bake_morph_weight_cache_from_runtime(
            [(0.0, [0.25]), (5.0, [0.75])],
            ["笑い"],
        )

        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertIn(f"{blend_shape}.smile", layer_attrs)
        self.assertEqual(cmds.keyframe(blend_shape, attribute="weight[0]", query=True, timeChange=True), [0.0, 5.0])
        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.75, places=6)

    def test_runtime_morph_cache_resolves_mappings_once_per_pmx_morph(self):
        """Runtime morph cache resolves target mappings once per PMX morph."""
        node = cmds.createNode("transform", name="test_runtime_morph_target_cache_node")
        cmds.addAttr(node, longName="weight", attributeType="double", keyable=True)
        self.converter.morph_name_mapping = {"笑い": object()}

        captured = []

        def fake_key_scalar(node_name, channel_samples, animation_layer=None):
            captured.append((node_name, channel_samples, animation_layer))
            return True

        with patch.object(self.converter, "_iter_morph_mappings", return_value=[(node, "weight", "")]) as iter_mappings:
            with patch.object(
                self.converter,
                "_batch_key_scalar_channels",
                side_effect=fake_key_scalar,
            ):
                self.converter._bake_morph_weight_cache_from_runtime(
                    [(1.0, [0.1]), (2.0, [0.2]), (3.0, [0.3])],
                    ["笑い"],
                )

        iter_mappings.assert_called_once()
        self.assertEqual(captured[0][0], node)
        self.assertEqual(captured[0][1], {"weight": [(1.0, 0.1), (2.0, 0.2), (3.0, 0.3)]})

        cmds.delete(node)
