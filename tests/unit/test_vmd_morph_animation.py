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
