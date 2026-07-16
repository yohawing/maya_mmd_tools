"""Outcome gate for model-scoped legacy VMD morph import."""

import copy
from pathlib import Path

import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.settings import settings
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


DATA = Path(__file__).resolve().parents[1] / "data"
PMX = DATA / "test_morph_model.pmx"
VMD = DATA / "vmd" / "morph_only.vmd"


class TestVmdMorphRealGate(MayaTestBase):
    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)

    @staticmethod
    def _two_frame_motion(morph_name):
        motion = VmdData().parse_file(str(VMD))
        first = motion.morph_frames[0]
        first.morph_name = morph_name
        first.frame_number = 0
        first.value = 0.25
        second = copy.copy(first)
        second.frame_number = 10
        second.value = 0.75
        motion.morph_frames = [first, second]
        return motion

    @staticmethod
    def _weight_plug(converter, morph_name):
        mapping = converter._iter_morph_mappings(converter.morph_name_mapping[morph_name])[0]
        return f"{mapping[0]}.{mapping[1]}"

    def test_real_vertex_morph_is_model_scoped_and_survives_reopen(self):
        pmx = parse_pmx_file(str(PMX))
        self.assertGreater(len(pmx.morphs), 0)
        morph_name = pmx.morphs[0].name
        motion = self._two_frame_motion(morph_name)

        root_a = import_mmd_file(str(PMX), options={"create_mmd_shaders": False})
        root_b = import_mmd_file(str(PMX), options={"create_mmd_shaders": False})
        self.assertTrue(root_a)
        self.assertTrue(root_b)
        self.assertNotEqual(root_a, root_b)

        base = VmdConverter()
        base.use_animation_layers = False
        self.assertTrue(base.convert(motion, target_model=root_a, pmx_path=str(PMX)))
        plug_a = self._weight_plug(base, morph_name)
        keys_a_before = cmds.keyframe(plug_a, query=True, timeChange=True)
        values_a_before = cmds.keyframe(plug_a, query=True, valueChange=True)
        self.assertEqual(keys_a_before, [0.0, 10.0])
        self.assertEqual(values_a_before, [0.25, 0.75])

        layered = VmdConverter()
        self.assertTrue(layered.convert(
            motion,
            target_model=root_b,
            pmx_path=str(PMX),
            layer_name="MorphGateLayerA",
        ))
        plug_b = self._weight_plug(layered, morph_name)
        self.assertEqual(cmds.keyframe(plug_a, query=True, timeChange=True), keys_a_before)
        self.assertEqual(cmds.keyframe(plug_a, query=True, valueChange=True), values_a_before)
        self.assertEqual(cmds.keyframe(plug_b, query=True, timeChange=True), [0.0, 10.0])

        second_layer = VmdConverter()
        self.assertTrue(second_layer.convert(
            motion,
            target_model=root_b,
            pmx_path=str(PMX),
            layer_name="MorphGateLayerB",
        ))
        direct_curves = cmds.listConnections(
            plug_b, source=True, destination=False, type="animCurve"
        ) or []
        self.assertEqual(len(direct_curves), 1)
        self.assertEqual(cmds.keyframe(plug_b, query=True, timeChange=True), [0.0, 10.0])
        self.assertEqual(cmds.keyframe(plug_b, query=True, valueChange=True), [0.25, 0.75])

        scene_path = self.get_temp_filename("vmd_morph_real_gate.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        self.assertEqual(cmds.keyframe(plug_a, query=True, timeChange=True), [0.0, 10.0])
        self.assertEqual(cmds.keyframe(plug_a, query=True, valueChange=True), [0.25, 0.75])
        self.assertEqual(
            len(cmds.listConnections(plug_b, source=True, destination=False, type="animCurve") or []),
            1,
        )
