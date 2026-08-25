"""VMD morph animation keying tests."""

import maya.cmds as cmds

from mmd_tools.converters.vmd_context import VmdMorphAnimationContext
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_morph_animation import convert_morph_animation
from mmd_tools.core.vmd_data.morph_frame import VmdMorphFrame
from tests.common.maya_test_base import MayaTestBase


def _morph_frame(name: str, frame_number: int, value: float) -> VmdMorphFrame:
    frame = VmdMorphFrame()
    frame.frame_number = frame_number
    frame.morph_name = name
    frame.value = value
    return frame


def _add_blendshape_alias(mesh: str, blend_shape: str, index: int, alias: str, offset) -> None:
    target = cmds.duplicate(mesh)[0]
    cmds.move(*offset, f"{target}.vtx[*]", relative=True)
    cmds.blendShape(blend_shape, edit=True, target=(mesh, index, target, 1.0))
    cmds.aliasAttr(alias, f"{blend_shape}.weight[{index}]")
    cmds.delete(target)


class TestVmdMorphAnimation(MayaTestBase):
    """Morph animation keying tests for VMD import."""

    def setUp(self):
        super().setUp()
        self.converter = VmdConverter()

    def test_convert_morph_animation(self):
        """BlendShape morph frames create weight keys."""
        morph_frames = [_morph_frame("mabataki", i * 10, i * 0.5) for i in range(3)]
        cube = cmds.polyCube(name="test_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_blendShape")[0]
        _add_blendshape_alias(cube, blend_shape, 0, "mabataki", (1, 0, 0))

        self.converter.morph_name_mapping["mabataki"] = (blend_shape, "weight[0]", "mabataki")

        result = self.converter._convert_morph_animation(morph_frames)
        self.assertTrue(result)

        keyframes = cmds.keyframe(f"{blend_shape}.weight[0]", query=True)
        self.assertIsNotNone(keyframes)
        self.assertEqual(len(keyframes), 3)

        cmds.delete(cube)

    def test_convert_morph_animation_accepts_direct_context(self):
        """Direct morph contexts key through their provided mapping and keying hooks."""
        captured = []
        mapping = object()

        def iter_morph_mappings(mapping_value):
            self.assertIs(mapping_value, mapping)
            return [("direct_context_morph", "weight", "smile")]

        def fake_batch_key_scalar_channels(node_name, channel_samples, animation_layer):
            captured.append((node_name, channel_samples, animation_layer))
            return True

        context = VmdMorphAnimationContext(
            logger=self.converter.logger,
            morph_name_mapping={"smile": mapping},
            anim_layer=None,
            use_animation_layers=False,
            iter_morph_mappings=iter_morph_mappings,
            vmd_frame_to_maya_time=self.converter.vmd_frame_to_maya_time,
            samples_as_anim_layer_deltas=self.converter._samples_as_anim_layer_deltas,
            batch_key_scalar_channels=fake_batch_key_scalar_channels,
        )

        self.assertTrue(convert_morph_animation(context, [_morph_frame("smile", 5, 0.75)]))
        self.assertEqual(captured, [("direct_context_morph", {"weight": [(5.0, 0.75)]}, None)])

    def test_convert_morph_animation_uses_anim_layer_deltas(self):
        """Layered morph import converts absolute samples to additive deltas."""
        mapping = object()
        captured_deltas = []
        captured_keys = []

        def fake_samples_as_anim_layer_deltas(node_name, channel_samples):
            captured_deltas.append((node_name, channel_samples))
            return {"weight": [(5.0, 0.5)]}

        def fake_batch_key_scalar_channels(node_name, channel_samples, animation_layer):
            captured_keys.append((node_name, channel_samples, animation_layer))
            return True

        def iter_morph_mappings(mapping_value):
            self.assertIs(mapping_value, mapping)
            return [("layered_context_morph", "weight", "smile")]

        context = VmdMorphAnimationContext(
            logger=self.converter.logger,
            morph_name_mapping={"smile": mapping},
            anim_layer="VMD_Motion",
            use_animation_layers=True,
            iter_morph_mappings=iter_morph_mappings,
            vmd_frame_to_maya_time=self.converter.vmd_frame_to_maya_time,
            samples_as_anim_layer_deltas=fake_samples_as_anim_layer_deltas,
            batch_key_scalar_channels=fake_batch_key_scalar_channels,
        )

        self.assertTrue(convert_morph_animation(context, [_morph_frame("smile", 5, 0.75)]))
        self.assertEqual(
            captured_deltas,
            [("layered_context_morph", {"weight": [(5.0, 0.75)]})],
        )
        self.assertEqual(
            captured_keys,
            [("layered_context_morph", {"weight": [(5.0, 0.5)]}, "VMD_Motion")],
        )

    def test_convert_morph_animation_blendshape_layer_weight_controls_result(self):
        """BlendShape morph keys are additive and respond to layer weight."""
        base = cmds.polyCube(name="normal_morph_layer_base")[0]
        target = cmds.duplicate(base, name="normal_morph_layer_target")[0]
        blend_shape = cmds.blendShape(target, base, name="normal_morph_layer_blendShape")[0]
        cmds.aliasAttr("smile", f"{blend_shape}.weight[0]")
        cmds.setAttr(f"{blend_shape}.weight[0]", 0.25)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("normal_morph_layer", override=False, weight=1.0)
        self.converter.morph_name_mapping = {"smile": (blend_shape, "weight[0]", "smile")}

        self.assertTrue(self.converter._convert_morph_animation([_morph_frame("smile", 0, 0.75)]))
        layer_attrs = cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or []
        self.assertIn(f"{blend_shape}.smile", layer_attrs)

        cmds.currentTime(0, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.75, places=6)
        cmds.animLayer(self.converter.anim_layer, edit=True, weight=0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.25, places=6)

    def test_convert_morph_animation_keys_morph_controller_input_on_layer(self):
        """Morph-controller authoring inputWeight keys stay on the selected layer."""
        controller = cmds.createNode("network", name="normal_morph_controller")
        cmds.addAttr(controller, longName="inputWeight", attributeType="double", multi=True, keyable=True)
        cmds.setAttr(f"{controller}.inputWeight[3]", 0.2)

        self.converter.use_animation_layers = True
        self.converter.anim_layer = cmds.animLayer("normal_controller_morph_layer", override=False, weight=1.0)
        self.converter.morph_name_mapping = {
            "smile": (controller, "inputWeight[3]", "smile"),
        }

        self.assertTrue(self.converter._convert_morph_animation([_morph_frame("smile", 4, 0.8)]))
        self.assertIn(
            f"{controller}.inputWeight[3]",
            cmds.animLayer(self.converter.anim_layer, query=True, attribute=True) or [],
        )
        cmds.currentTime(4, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[3]"), 0.8, places=6)
        cmds.animLayer(self.converter.anim_layer, edit=True, weight=0.0)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.inputWeight[3]"), 0.2, places=6)

    def test_convert_morph_animation_with_split_mesh_aliases(self):
        """The same morph alias on multiple meshes keys every mapping."""
        frame = _morph_frame("morph_split", 5, 0.75)

        mesh_a = cmds.polyCube(name="morph_split_mesh_a")[0]
        blend_shape_a = cmds.blendShape(mesh_a, name="morph_split_bs_a")[0]
        _add_blendshape_alias(mesh_a, blend_shape_a, 0, "morph_split", (1, 0, 0))

        mesh_b = cmds.polyCube(name="morph_split_mesh_b")[0]
        blend_shape_b = cmds.blendShape(mesh_b, name="morph_split_bs_b")[0]
        _add_blendshape_alias(mesh_b, blend_shape_b, 0, "morph_split", (0, 1, 0))

        self.converter._build_morph_mappings()
        self.assertEqual(len(self.converter._iter_morph_mappings(self.converter.morph_name_mapping["morph_split"])), 2)
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        keys_a = cmds.keyframe(f"{blend_shape_a}.weight[0]", query=True, timeChange=True)
        keys_b = cmds.keyframe(f"{blend_shape_b}.weight[0]", query=True, timeChange=True)
        self.assertIn(5.0, keys_a)
        self.assertIn(5.0, keys_b)

        cmds.delete(mesh_a, blend_shape_a, mesh_b, blend_shape_b)

    def test_namespace_less_duplicate_morphs_key_only_explicit_target_root(self):
        """Same alias/index on two namespace-less roots cannot cross-key model A."""
        root_a = cmds.group(empty=True, name="morph_model_a_root")
        root_b = cmds.group(empty=True, name="morph_model_b_root")
        mesh_a = cmds.polyCube(name="morph_model_a_mesh")[0]
        mesh_b = cmds.polyCube(name="morph_model_b_mesh")[0]
        cmds.parent(mesh_a, root_a)
        cmds.parent(mesh_b, root_b)
        blend_shape_a = cmds.blendShape(mesh_a, name="morph_model_a_bs")[0]
        blend_shape_b = cmds.blendShape(mesh_b, name="morph_model_b_bs")[0]
        _add_blendshape_alias(mesh_a, blend_shape_a, 0, "shared_morph", (1, 0, 0))
        _add_blendshape_alias(mesh_b, blend_shape_b, 0, "shared_morph", (0, 1, 0))

        self.converter._build_morph_mappings(target_model=root_b)
        mappings = self.converter._iter_morph_mappings(
            self.converter.morph_name_mapping["shared_morph"]
        )
        self.assertEqual([mapping[0] for mapping in mappings], [blend_shape_b])

        self.assertTrue(self.converter._convert_morph_animation([_morph_frame("shared_morph", 7, 0.8)]))
        self.assertIsNone(cmds.keyframe(f"{blend_shape_a}.weight[0]", query=True))
        self.assertIn(7.0, cmds.keyframe(f"{blend_shape_b}.weight[0]", query=True))

    def test_root_scoped_network_morph_mapping_fails_closed_without_ownership(self):
        """Only explicitly root-connected network morphs enter a scoped mapping."""
        root_a = cmds.group(empty=True, name="network_model_a_root")
        root_b = cmds.group(empty=True, name="network_model_b_root")
        morph_a = self._create_network_morph("network_a_boneMorph", "bone", "同名モーフ")
        morph_b = self._create_network_morph("network_b_boneMorph", "bone", "同名モーフ")
        unowned = self._create_network_morph("network_legacy_boneMorph", "bone", "同名モーフ")
        for root, morph in ((root_a, morph_a), (root_b, morph_b)):
            cmds.addAttr(morph, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{morph}.mmd_model_root")

        self.converter._build_morph_mappings(target_model=root_b)

        mappings = self.converter._iter_morph_mappings(
            self.converter.morph_name_mapping["同名モーフ"]
        )
        self.assertEqual([mapping[0] for mapping in mappings], [morph_b])
        self.assertNotIn(morph_a, [mapping[0] for mapping in mappings])
        self.assertNotIn(unowned, [mapping[0] for mapping in mappings])

    def test_convert_morph_animation_legacy_mapping_uses_weight_index_tuple(self):
        """Legacy mapping tuples with integer weight indices still key correctly."""
        frame = _morph_frame("mabataki", 5, 0.6)
        cube = cmds.polyCube(name="legacy_mapping_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="legacy_mapping_blendShape")[0]
        _add_blendshape_alias(cube, blend_shape, 0, "mabataki", (1, 0, 0))
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, 0, "mabataki")

        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(5, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.6, places=6)
        self.assertIn(5.0, cmds.keyframe(f"{blend_shape}.weight[0]", query=True))

        cmds.delete(cube)

    def _create_network_morph(self, node_name: str, morph_type: str, morph_name: str) -> str:
        morph_node = cmds.createNode("network", name=node_name)
        cmds.addAttr(
            morph_node,
            longName="weight",
            attributeType="double",
            minValue=0.0,
            maxValue=1.0,
            defaultValue=0.0,
            keyable=True,
        )
        cmds.addAttr(morph_node, longName="mmd_morph_type", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_type", morph_type, type="string")
        cmds.addAttr(morph_node, longName="mmd_morph_name", dataType="string")
        cmds.setAttr(f"{morph_node}.mmd_morph_name", morph_name, type="string")
        return morph_node

    def test_convert_bone_morph_network_weight_animation(self):
        """Bone morph network weights can be keyed by VMD morph frames."""
        morph_node = self._create_network_morph("boneSmile_boneMorph", "bone", "ボーン笑い")
        frame = _morph_frame("ボーン笑い", 12, 0.8)

        self.converter._build_morph_mappings()
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(12, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{morph_node}.weight"), 0.8, places=6)
        self.assertIn(12.0, cmds.keyframe(f"{morph_node}.weight", query=True))

        cmds.delete(morph_node)

    def test_convert_material_morph_network_weight_animation(self):
        """Material morph network weights can be keyed by VMD morph frames."""
        morph_node = self._create_network_morph("materialFlash_materialMorph", "material", "材質点滅")
        frame = _morph_frame("材質点滅", 18, 0.35)

        self.converter._build_morph_mappings()
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(18, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{morph_node}.weight"), 0.35, places=6)
        self.assertIn(18.0, cmds.keyframe(f"{morph_node}.weight", query=True))

        cmds.delete(morph_node)

    def test_convert_group_morph_network_weight_animation(self):
        """Group morph network weights can be keyed by VMD morph frames."""
        morph_node = self._create_network_morph("groupSmile_groupMorph", "group", "グループ笑い")
        frame = _morph_frame("グループ笑い", 24, 0.65)

        self.converter._build_morph_mappings()
        result = self.converter._convert_morph_animation([frame])
        self.assertTrue(result)

        cmds.currentTime(24, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{morph_node}.weight"), 0.65, places=6)
        self.assertIn(24.0, cmds.keyframe(f"{morph_node}.weight", query=True))

        cmds.delete(morph_node)

    def test_bake_morph_weights_from_runtime_uses_pmx_morph_order(self):
        """Runtime morph weights are mapped by PMX morph order and Japanese name."""
        cube = cmds.polyCube(name="test_runtime_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="test_runtime_morph_blendShape")[0]
        _add_blendshape_alias(cube, blend_shape, 0, "blink", (1, 0, 0))

        self.converter._build_morph_mappings()
        self.converter._bake_morph_weights_from_runtime(
            frame=7,
            morph_weights=[0.75],
            pmx_morph_names=["まばたき"],
        )

        cmds.currentTime(7, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.75, places=6)
        self.assertIn(7.0, cmds.keyframe(f"{blend_shape}.weight[0]", query=True))

        cmds.delete(cube)

    def test_bake_morph_weights_from_runtime_with_split_mesh_aliases(self):
        """Runtime morph weights key every blendShape that shares an alias."""
        mesh_a = cmds.polyCube(name="runtime_split_mesh_a")[0]
        blend_shape_a = cmds.blendShape(mesh_a, name="runtime_split_bs_a")[0]
        _add_blendshape_alias(mesh_a, blend_shape_a, 0, "morph_split", (1, 0, 0))

        mesh_b = cmds.polyCube(name="runtime_split_mesh_b")[0]
        blend_shape_b = cmds.blendShape(mesh_b, name="runtime_split_bs_b")[0]
        _add_blendshape_alias(mesh_b, blend_shape_b, 0, "morph_split", (0, 1, 0))

        self.converter._build_morph_mappings()
        self.assertEqual(len(self.converter._iter_morph_mappings(self.converter.morph_name_mapping["morph_split"])), 2)

        self.converter._bake_morph_weights_from_runtime(
            frame=11,
            morph_weights=[0.4],
            pmx_morph_names=["morph_split"],
        )

        cmds.currentTime(11, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape_a}.weight[0]"), 0.4, places=6)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape_b}.weight[0]"), 0.4, places=6)
        self.assertIn(11.0, cmds.keyframe(f"{blend_shape_a}.weight[0]", query=True, timeChange=True))
        self.assertIn(11.0, cmds.keyframe(f"{blend_shape_b}.weight[0]", query=True, timeChange=True))

        cmds.delete(mesh_a, blend_shape_a, mesh_b, blend_shape_b)

    def test_bake_morph_weights_from_runtime_with_legacy_mapping(self):
        """Runtime morph bake accepts legacy integer weight-index mappings."""
        cube = cmds.polyCube(name="legacy_runtime_morph_mesh")[0]
        blend_shape = cmds.blendShape(cube, name="legacy_runtime_morph_blendShape")[0]
        _add_blendshape_alias(cube, blend_shape, 0, "mabataki", (1, 0, 0))
        self.converter.morph_name_mapping["mabataki"] = (blend_shape, 0, "mabataki")

        self.converter._bake_morph_weights_from_runtime(
            frame=19,
            morph_weights=[0.55],
            pmx_morph_names=["mabataki"],
        )

        cmds.currentTime(19, edit=True)
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.55, places=6)
        self.assertIn(19.0, cmds.keyframe(f"{blend_shape}.weight[0]", query=True))

        cmds.delete(cube)

    def test_fps_60_morph_keys_vmd_frame_30_at_maya_time_60(self):
        """60fps import maps VMD frame 30 morph keys to Maya time 60."""
        mesh = cmds.polyCube(name="fps_60_morph_mesh")[0]
        blend_shape = cmds.blendShape(mesh, name="fps_60_morph_blendShape")[0]
        cmds.aliasAttr("smile", f"{blend_shape}.weight[0]")
        frame = _morph_frame("smile", 30, 0.75)
        self.converter.fps = 60.0
        self.converter.morph_name_mapping = {"smile": (blend_shape, "weight[0]", "smile")}

        self.assertTrue(self.converter._convert_morph_animation([frame]))
        self.assertEqual(cmds.keyframe(blend_shape, attribute="weight[0]", query=True, timeChange=True), [60.0])

        cmds.delete(mesh)
