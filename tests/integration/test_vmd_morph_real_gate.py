"""Real PMX/VMD outcome gate for model-scoped all-type morph import."""

import json
import os
import time
from pathlib import Path

import maya.cmds as cmds

from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.morph_scene_metadata import iter_morph_network_metadata
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.settings import settings
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.io.mmd_importer import import_mmd_file
from tests.common.maya_test_base import MayaTestBase


FIXTURES = Path(__file__).resolve().parents[1] / "data" / "for_unit_test"
PMX = FIXTURES / "test_vmd_morph_real_gate.pmx"
VMD = FIXTURES / "test_vmd_morph_real_gate.vmd"
BASE_ORACLE_SEC = 0.1283
LAYER_ORACLE_SEC = 0.8781
PERF_TOLERANCE = 3.0


def _restore_skip_shader_override(previous):
    if previous is None:
        os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
    else:
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous


class TestVmdMorphRealGate(MayaTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plugins_loaded = []
        cls._previous_skip_shader_override = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        if not cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
            cls.plugins_loaded.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            _restore_skip_shader_override(cls._previous_skip_shader_override)

    def setUp(self):
        super().setUp()
        settings.set("import.model.create_mmd_shaders", False)

    @staticmethod
    def _motion():
        return VmdData().parse_file(str(VMD))

    @staticmethod
    def _weight_plugs(converter):
        plugs = {}
        for name in ("vertex_leaf", "bone_leaf", "material_leaf", "outer_group"):
            mapping = converter._iter_morph_mappings(converter.morph_name_mapping[name])[0]
            plugs[name] = f"{mapping[0]}.{mapping[1]}"
        return plugs

    @staticmethod
    def _morph_nodes(root):
        return {
            (metadata.morph_type, metadata.name): metadata.node
            for metadata in iter_morph_network_metadata(root_group=root)
        }

    @staticmethod
    def _joint_by_index(root, index):
        for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
            if cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                if cmds.getAttr(f"{joint}.mmd_bone_index") == index:
                    return joint
        raise AssertionError(f"bone index {index} was not imported below {root}")

    @staticmethod
    def _mesh_vertex(root, index):
        meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
        if not meshes:
            raise AssertionError(f"no mesh below {root}")
        return f"{meshes[0]}.vtx[{index}]"

    @staticmethod
    def _material_evaluator(root):
        shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
        shading_groups = set()
        for shape in shapes:
            shading_groups.update(cmds.listConnections(shape, type="shadingEngine") or [])
        shaders = set()
        for shading_group in shading_groups:
            shaders.update(cmds.ls(cmds.listConnections(shading_group) or [], materials=True) or [])
        for evaluator in cmds.ls(type="mmdMaterialMorphEval") or []:
            if cmds.getAttr(f"{evaluator}.mmd_target_shader") in shaders:
                return evaluator
        raise AssertionError(f"no material morph evaluator below {root}")

    def test_real_all_type_morphs_are_scoped_persistent_and_fast(self):
        pmx = parse_pmx_file(str(PMX))
        types = {m.morph_type for m in pmx.morphs}
        self.assertTrue({
            PmxMorphType.VertexMorph,
            PmxMorphType.BoneMorph,
            PmxMorphType.GroupMorph,
            PmxMorphType.MaterialMorph,
        }.issubset(types))
        groups = {m.name: m for m in pmx.morphs if m.morph_type == PmxMorphType.GroupMorph}
        self.assertEqual(len(groups["inner_group"].offsets), 2)
        self.assertEqual(len(groups["outer_group"].offsets), 2)

        motion = self._motion()
        self.assertEqual(len(motion.morph_frames), 8)
        self.assertEqual(sorted({frame.frame_number for frame in motion.morph_frames}), [0, 10])

        import_options = {"create_mmd_shaders": False, "import_morphs": True, "use_cpp_fast_load": False}
        root_a = import_mmd_file(str(PMX), options=import_options)
        root_b = import_mmd_file(str(PMX), options=import_options)
        self.assertTrue(root_a)
        self.assertTrue(root_b)
        self.assertNotEqual(root_a, root_b)

        controllers = {}
        for root in (root_a, root_b):
            connected = set(cmds.listConnections(
                f"{root}.mmd_morph_controller",
                source=True,
                destination=False,
                type="mmdMorphController",
            ) or [])
            self.assertEqual(len(connected), 1)
            controllers[root] = connected.pop()
        self.assertNotEqual(controllers[root_a], controllers[root_b])
        expected_topology = {
            "0": [[4, 0.25]],
            "1": [[3, 0.5], [4, 0.4]],
            "2": [[3, 0.5], [4, 0.4]],
            "3": [[4, 0.8]],
        }
        for controller in controllers.values():
            actual_topology = json.loads(cmds.getAttr(f"{controller}.groupTopology"))
            self.assertEqual(actual_topology.keys(), expected_topology.keys())
            for target_index, expected_sources in expected_topology.items():
                actual_sources = actual_topology[target_index]
                self.assertEqual(
                    [source_index for source_index, _rate in actual_sources],
                    [source_index for source_index, _rate in expected_sources],
                )
                for actual_source, expected_source in zip(actual_sources, expected_sources):
                    self.assertAlmostEqual(actual_source[1], expected_source[1], places=6)

        nodes_a = self._morph_nodes(root_a)
        nodes_b = self._morph_nodes(root_b)
        for key in (("bone", "bone_leaf"), ("material", "material_leaf"),
                    ("group", "inner_group"), ("group", "outer_group")):
            self.assertIn(key, nodes_a)
            self.assertIn(key, nodes_b)
            self.assertNotEqual(nodes_a[key], nodes_b[key])
        inner_offsets = json.loads(cmds.getAttr(
            f"{nodes_a[('group', 'inner_group')]}.mmd_group_morph_offsets_json"
        ))
        outer_offsets = json.loads(cmds.getAttr(
            f"{nodes_a[('group', 'outer_group')]}.mmd_group_morph_offsets_json"
        ))
        self.assertEqual([item["morph_index"] for item in inner_offsets], [1, 2])
        self.assertEqual([item["morph_index"] for item in outer_offsets], [3, 0])

        base = VmdConverter()
        base.use_animation_layers = False
        started = time.perf_counter()
        self.assertTrue(base.convert(motion, target_model=root_a, pmx_path=str(PMX)))
        base_sec = time.perf_counter() - started
        plugs_a = self._weight_plugs(base)

        expected_effective_weights = {
            0: [0.3125, 0.35, 0.35, 0.2, 0.25],
            5.5: [0.65625, 0.735, 0.735, 0.42, 0.525],
            10: [0.9375, 1.05, 1.05, 0.6, 0.75],
        }
        for frame, expected in expected_effective_weights.items():
            cmds.currentTime(frame, edit=True)
            actual = [
                cmds.getAttr(f"{controllers[root_a]}.outputWeight[{index}]")
                for index in range(5)
            ]
            self.assertListAlmostEqual(actual, expected, places=6)

        for plug in plugs_a.values():
            self.assertEqual(cmds.keyframe(plug, query=True, timeChange=True), [0.0, 10.0])
            self.assertEqual(cmds.keyframe(plug, query=True, valueChange=True), [0.25, 0.75])

        untouched_b = {
            key: cmds.getAttr(f"{node}.weight")
            for key, node in nodes_b.items()
            if key[0] in {"bone", "material", "group"}
        }
        cmds.currentTime(0, edit=True)
        joint_a = self._joint_by_index(root_a, 1)
        bone_at_zero = cmds.getAttr(f"{joint_a}.translateX")
        vertex_at_zero = cmds.pointPosition(self._mesh_vertex(root_a, 2), local=True)
        evaluator_a = self._material_evaluator(root_a)
        material_at_zero = cmds.getAttr(f"{evaluator_a}.outputDiffuse")[0]
        cmds.currentTime(10, edit=True)
        bone_at_ten = cmds.getAttr(f"{joint_a}.translateX")
        vertex_at_ten = cmds.pointPosition(self._mesh_vertex(root_a, 2), local=True)
        material_at_ten = cmds.getAttr(f"{evaluator_a}.outputDiffuse")[0]
        self.assertGreater(bone_at_ten, bone_at_zero)
        self.assertGreater(vertex_at_ten[1], vertex_at_zero[1])
        self.assertGreater(material_at_ten[0], material_at_zero[0])
        for key, value in untouched_b.items():
            self.assertEqual(cmds.getAttr(f"{nodes_b[key]}.weight"), value)

        layered = VmdConverter()
        started = time.perf_counter()
        self.assertTrue(layered.convert(
            motion, target_model=root_b, pmx_path=str(PMX), layer_name="MorphGateLayer"
        ))
        layer_sec = time.perf_counter() - started
        plugs_b = self._weight_plugs(layered)
        for name, plug in plugs_b.items():
            self.assertEqual(cmds.keyframe(plug, query=True, timeChange=True), [0.0, 10.0])
            self.assertEqual(cmds.keyframe(plug, query=True, valueChange=True), [0.25, 0.75])
            self.assertEqual(cmds.keyframe(plugs_a[name], query=True, valueChange=True), [0.25, 0.75])

        repeated = VmdConverter()
        self.assertTrue(repeated.convert(
            motion, target_model=root_b, pmx_path=str(PMX), layer_name="MorphGateLayerAgain"
        ))
        for plug in plugs_b.values():
            direct_curves = cmds.listConnections(
                plug, source=True, destination=False, type="animCurve"
            ) or []
            self.assertEqual(len(direct_curves), 1)
            self.assertEqual(cmds.keyframe(plug, query=True, timeChange=True), [0.0, 10.0])

        print(
            "VMD_MORPH_REAL_GATE_PERF "
            f"base={base_sec:.6f}s oracle={BASE_ORACLE_SEC:.4f}s "
            f"layer={layer_sec:.6f}s oracle={LAYER_ORACLE_SEC:.4f}s "
            f"tolerance={PERF_TOLERANCE:.1f}x"
        )
        self.assertLessEqual(base_sec, BASE_ORACLE_SEC * PERF_TOLERANCE)
        self.assertLessEqual(layer_sec, LAYER_ORACLE_SEC * PERF_TOLERANCE)

        scene_path = self.get_temp_filename("vmd_morph_real_gate.ma")
        cmds.file(rename=scene_path)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(scene_path, open=True, force=True)
        for plug in list(plugs_a.values()) + list(plugs_b.values()):
            self.assertTrue(cmds.objExists(plug))
            self.assertEqual(cmds.keyframe(plug, query=True, timeChange=True), [0.0, 10.0])
        self.assertGreaterEqual(len(cmds.ls(type="mmdBoneMorphAccum") or []), 2)
        self.assertGreaterEqual(len(cmds.ls(type="mmdMaterialMorphEval") or []), 2)
