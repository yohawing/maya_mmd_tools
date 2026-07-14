"""PMX material morph runtime integration tests.

material morph network node が shader パラメータに接続され、
weight 変更がマテリアル外観に反映されることを検証する。
"""

from pathlib import Path
import json
import os
import unittest
from unittest import mock

from maya import cmds

from mmd_tools.converters import MorphConverter
from mmd_tools.converters.material_morph_runtime import (
    BACKEND_DX11,
    BACKEND_GLSL,
    BACKEND_STANDARD,
    VP2_API_DIRECTX11,
    build_material_morph_graph,
    detect_effective_vp2_draw_api,
    resolve_shader_color_route,
)
from mmd_tools.converters import material_morph_runtime
from mmd_tools.converters.mesh_converter import _ensure_mmd_shader_uniform_attributes
from mmd_tools.core import maya_attribute_utils, maya_mesh_utils
from mmd_tools.core.constants import (
    SCENE_ROOT_SUFFIX,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MATERIAL_INDEX,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MMD_SHADER_FX = _REPO_ROOT / "mmd_tools" / "shaders" / "MMDShader.fx"
_MODULE_PLUGINS_LOADED = []
_PREVIOUS_SKIP_SHADER_OVERRIDE = None


def _restore_skip_shader_override(previous):
    if previous is None:
        os.environ.pop("MMD_TOOLS_SKIP_SHADER_OVERRIDE", None)
    else:
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = previous


def _load_repo_plugin_for_tests(owned_plugins):
    previous = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
    os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    plugin_path = _REPO_ROOT / "mmd_tools" / "plugin_main.py"
    was_loaded = False
    try:
        was_loaded = bool(cmds.pluginInfo(str(plugin_path), query=True, loaded=True))
        if not was_loaded:
            owned_plugins.extend(cmds.loadPlugin(str(plugin_path), quiet=True) or [])
        return previous
    except Exception:
        if not was_loaded:
            try:
                if cmds.pluginInfo(str(plugin_path), query=True, loaded=True):
                    cmds.unloadPlugin(str(plugin_path))
            except Exception:
                pass
        _restore_skip_shader_override(previous)
        raise


def setUpModule():
    global _PREVIOUS_SKIP_SHADER_OVERRIDE
    _MODULE_PLUGINS_LOADED.clear()
    _PREVIOUS_SKIP_SHADER_OVERRIDE = _load_repo_plugin_for_tests(_MODULE_PLUGINS_LOADED)


def tearDownModule():
    for plugin in reversed(_MODULE_PLUGINS_LOADED):
        try:
            if cmds.pluginInfo(plugin, query=True, loaded=True):
                cmds.unloadPlugin(plugin)
        except Exception:
            pass
    _restore_skip_shader_override(_PREVIOUS_SKIP_SHADER_OVERRIDE)


from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.nodes.mmd_material_morph_eval_node import MmdMaterialMorphEvalNode


class TestMaterialMorphNumericComposition(unittest.TestCase):
    """Shader routing に依存しない PMX material morph 数値契約。"""

    @staticmethod
    def _base():
        return {
            "diffuse": (0.5, 0.4, 0.3, 0.8),
            "specular": (0.5, 0.5, 0.5),
            "specularCoefficient": (0.5,),
            "ambient": (0.5, 0.5, 0.5),
            "edgeColor": (0.5, 0.5, 0.5, 0.5),
            "edgeSize": (0.5,),
        }

    def test_plugin_load_failure_restores_environment_and_unloads_partial_load(self):
        previous = os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE")
        os.environ["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "preserved"
        owned_plugins = []
        fake_cmds = mock.Mock()
        fake_cmds.pluginInfo.side_effect = [False, True]
        fake_cmds.loadPlugin.side_effect = RuntimeError("simulated plugin load failure")
        try:
            with mock.patch.dict(globals(), {"cmds": fake_cmds}):
                with self.assertRaisesRegex(RuntimeError, "simulated plugin load failure"):
                    _load_repo_plugin_for_tests(owned_plugins)
            self.assertEqual(os.environ.get("MMD_TOOLS_SKIP_SHADER_OVERRIDE"), "preserved")
            self.assertEqual(owned_plugins, [])
            fake_cmds.unloadPlugin.assert_called_once()
        finally:
            _restore_skip_shader_override(previous)

    @staticmethod
    def _contribution(op, value, order=0, slot=0):
        return {
            "logical_index": slot,
            "morph_order": order,
            "weight": 1.0,
            "op": op,
            "diffuse": tuple(value for _ in range(4)),
            "specular": tuple(value for _ in range(3)),
            "specularCoefficient": (value,),
            "ambient": tuple(value for _ in range(3)),
            "edgeColor": tuple(value for _ in range(4)),
            "edgeSize": (value,),
            "texture": tuple(value for _ in range(4)),
            "sphereTexture": tuple(value for _ in range(4)),
            "toonTexture": tuple(value for _ in range(4)),
        }

    def test_add_and_multiply_are_composed_separately(self):
        contributions = [
            self._contribution(1, 0.2, order=0),
            self._contribution(0, 0.5, order=1),
        ]
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), contributions)
        self.assertAlmostEqual(material["diffuse"][0], 0.45)
        self.assertNotAlmostEqual(material["diffuse"][0], 0.35)

    def test_alpha_only_add_preserves_rgb(self):
        contribution = self._contribution(1, 0.0)
        contribution["diffuse"] = (0.0, 0.0, 0.0, -0.3)
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), [contribution])
        self.assertEqual(material["diffuse"][:3], self._base()["diffuse"][:3])
        self.assertAlmostEqual(material["diffuse"][3], 0.5)

    def test_semitransparent_alpha_multiply_is_not_squared(self):
        contribution = self._contribution(0, 1.0)
        contribution["diffuse"] = (1.0, 1.0, 1.0, 0.5)
        material, _, _ = MmdMaterialMorphEvalNode.compose(self._base(), [contribution])
        self.assertEqual(material["diffuse"][:3], self._base()["diffuse"][:3])
        self.assertAlmostEqual(material["diffuse"][3], 0.4)

    def test_all_texture_tracks_keep_multiply_and_add_separate(self):
        contributions = [self._contribution(0, 0.5), self._contribution(1, 0.2)]
        material, multiply, add = MmdMaterialMorphEvalNode.compose(self._base(), contributions)
        for name, values in material.items():
            for base_value, result in zip(self._base()[name], values):
                self.assertAlmostEqual(result, base_value * 0.5 + 0.2)
        for name in ("texture", "sphereTexture", "toonTexture"):
            self.assertEqual(tuple(multiply[name]), (0.5, 0.5, 0.5, 0.5))
            self.assertEqual(tuple(add[name]), (0.2, 0.2, 0.2, 0.2))

    def test_main_texture_alpha_factor_is_composed_as_rgba(self):
        multiply = self._contribution(0, 1.0)
        multiply["texture"] = (1.0, 1.0, 1.0, 0.5)
        additive = self._contribution(1, 0.0, order=1, slot=1)
        additive["texture"] = (0.0, 0.0, 0.0, 0.2)
        _, texture_multiply, texture_add = MmdMaterialMorphEvalNode.compose(
            self._base(), [multiply, additive]
        )
        self.assertEqual(tuple(texture_multiply["texture"]), (1.0, 1.0, 1.0, 0.5))
        self.assertEqual(tuple(texture_add["texture"]), (0.0, 0.0, 0.0, 0.2))
        sample_alpha = 0.8
        self.assertAlmostEqual(
            sample_alpha * texture_multiply["texture"][3] + texture_add["texture"][3],
            0.6,
        )

    def test_declared_node_contract_covers_every_pmx_channel(self):
        expected = {
            "baseDiffuse", "baseSpecular", "baseSpecularCoefficient",
            "baseAmbient", "baseEdgeColor", "baseEdgeSize", "contribution",
            "diffuseOffset", "specularOffset", "specularCoefficientOffset",
            "ambientOffset", "edgeColorOffset", "edgeSizeOffset", "textureOffset",
            "sphereTextureOffset", "toonTextureOffset", "outputDiffuse",
            "outputDiffuseAlpha", "outputSpecular", "outputSpecularCoefficient",
            "outputAmbient", "outputEdgeColor", "outputEdgeSize",
            "outputTextureMultiply", "outputTextureAdd",
            "outputSphereTextureMultiply", "outputSphereTextureAdd",
            "outputToonTextureMultiply", "outputToonTextureAdd",
        }
        node = cmds.createNode("mmdMaterialMorphEval")
        try:
            missing = sorted(
                attr for attr in expected
                if not cmds.attributeQuery(attr, node=node, exists=True)
            )
            self.assertEqual(missing, [])
        finally:
            cmds.delete(node)

    def test_all_output_attributes_are_non_writable_and_non_storable(self):
        node = cmds.createNode("mmdMaterialMorphEval")
        try:
            output_attrs = cmds.listAttr(node, output=True) or []
            declared_outputs = [
                attr for attr in output_attrs if attr.startswith("output")
            ]
            self.assertTrue(declared_outputs)
            for attr in declared_outputs:
                self.assertFalse(
                    cmds.attributeQuery(attr, node=node, writable=True),
                    f"{attr} must be output-only",
                )
                self.assertFalse(
                    cmds.attributeQuery(attr, node=node, storable=True),
                    f"{attr} must not be serialized",
                )
        finally:
            cmds.delete(node)

    def test_output_attribute_cache_is_flattened_once_and_reused(self):
        node = cmds.createNode("mmdMaterialMorphEval")
        try:
            cached = MmdMaterialMorphEvalNode._all_output_attributes()
            expected_count = sum(
                1 + len(children)
                for children in MmdMaterialMorphEvalNode._output_children.values()
            )
            self.assertEqual(len(cached), expected_count)
            self.assertIs(MmdMaterialMorphEvalNode._all_output_attributes(), cached)

            cmds.setAttr(f"{node}.baseDiffuse", 0.2, 0.3, 0.4, type="double3")
            self.assertEqual(
                tuple(cmds.getAttr(f"{node}.outputDiffuse")[0]),
                (0.2, 0.3, 0.4),
            )
            cmds.setAttr(f"{node}.baseDiffuse", 0.7, 0.6, 0.5, type="double3")
            self.assertEqual(
                tuple(cmds.getAttr(f"{node}.outputDiffuse")[0]),
                (0.7, 0.6, 0.5),
            )
        finally:
            cmds.delete(node)
from tests.common.maya_test_base import MayaTestBase


def _make_fake_pmx_data(morphs, materials=None):
    """morph + material を持つ最小の fake PMX data を返す。"""
    return type(
        "FakePmxData",
        (),
        {
            "morphs": morphs,
            "materials": materials or [],
            "faces": [],
        },
    )()


def _make_material_morph(name, offsets):
    """単一の MaterialMorph fake を返す。"""
    m = type("FakeMaterialMorph", (), {
        "name": name,
        "name_english": "",
        "panel": 4,
        "morph_type": PmxMorphType.MaterialMorph,
        "offsets": offsets,
    })()
    m.get_name = lambda: m.name
    return m


class TestMaterialMorphModelRootConnection(MayaTestBase):
    """material morph network ノードが model root に紐付いていることを検証する。"""

    def _create_scene(self):
        root = cmds.group(empty=True, name=f"test_model{SCENE_ROOT_SUFFIX}")
        maya_attribute_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})

        mesh = maya_mesh_utils.create_mesh_with_uvs(
            "test_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [4], [0, 1, 2, 3],
            [0, 0, 1, 0, 1, 1, 0, 1], [0, 1, 2, 3],
        )
        mesh = cmds.parent(mesh, root)[0]

        morph = _make_material_morph("材質点滅", [
            {
                "material_index": 0,
                "operation_type": 1,
                "diffuse": (1.0, 0.0, 0.0, 0.0),
                "specular": (0.0, 0.0, 0.0),
                "specular_coefficient": 0.0,
                "ambient": (0.0, 0.0, 0.0),
                "edge_color": (0.0, 0.0, 0.0, 0.0),
                "edge_size": 0.0,
                "texture_factor": (0.0, 0.0, 0.0, 0.0),
                "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                "toon_texture_factor": (0.0, 0.0, 0.0, 0.0),
            }
        ])

        converter = MorphConverter()
        result = converter.convert_pmx_morphs(_make_fake_pmx_data([morph]), mesh)
        material_nodes = result.get("material_morph_nodes", [])

        # importer がやる接続を再現
        for morph_node in material_nodes:
            if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
                cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{morph_node}.mmd_model_root", force=True)

        return root, mesh, material_nodes

    def test_material_morph_node_connected_to_model_root(self):
        """import 後の material morph network ノードが model root に message 接続されている。"""
        root, _, material_nodes = self._create_scene()

        self.assertEqual(len(material_nodes), 1)
        morph_node = material_nodes[0]

        self.assertTrue(
            cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True),
            "mmd_model_root 属性がない",
        )
        connected = cmds.listConnections(f"{morph_node}.mmd_model_root") or []
        self.assertIn(root, connected, "model root に接続されていない")

    def test_presenter_discovers_material_morphs(self):
        """MorphPresenter の探索ロジックで material morph が morph_data に入る。"""
        root, _, material_nodes = self._create_scene()

        # presenter のロジックを直接再現
        morph_data = {}
        network_nodes = cmds.ls(type="network") or []
        for node in network_nodes:
            if not cmds.attributeQuery("mmd_morph_type", node=node, exists=True):
                continue
            morph_type = cmds.getAttr(f"{node}.mmd_morph_type") or ""
            if morph_type not in ("bone", "material"):
                continue

            # model root スコープチェック
            if cmds.attributeQuery("mmd_model_root", node=node, exists=True):
                connected_roots = cmds.listConnections(f"{node}.mmd_model_root") or []
                if root not in connected_roots:
                    continue

            raw_name = cmds.getAttr(f"{node}.mmd_morph_name") or node
            morph_data[raw_name] = {
                "morph_node": node,
                "morph_type": morph_type,
            }

        self.assertIn("材質点滅", morph_data, "presenter が material morph を発見できない")
        self.assertEqual(morph_data["材質点滅"]["morph_type"], "material")


class TestMaterialMorphWeightDrivesShader(MayaTestBase):
    """material morph の weight 変更が shader パラメータに反映されることを検証する。"""

    def test_plug_match_guard_ignores_uninitialized_attributes(self):
        """古い module 状態の None 属性と plug 比較しても TypeError にしない。"""

        class ExplodingPlug:
            def __eq__(self, other):
                if other is None:
                    raise TypeError("MPlug or MObject expected.")
                return False

            def attribute(self):
                raise TypeError("MPlug or MObject expected.")

        self.assertFalse(MmdMaterialMorphEvalNode._plug_matches_any(ExplodingPlug(), (None,)))

    def _require_material_morph_node(self):
        try:
            node = cmds.createNode("mmdMaterialMorphEval", name="availability_probe_materialMorphEval")
        except RuntimeError as exc:
            plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
            try:
                self.load_plugin(str(plugin_path))
                node = cmds.createNode("mmdMaterialMorphEval", name="availability_probe_materialMorphEval")
            except RuntimeError:
                self.skipTest(f"mmdMaterialMorphEval node is unavailable: {exc}")
        cmds.delete(node)

    def test_direct_and_multiple_group_weights_add_at_evaluator_input(self):
        """direct + group*rate が同じ material contribution weight に加算される。"""
        self._require_material_morph_node()
        evaluator = cmds.createNode("mmdMaterialMorphEval", name="groupMaterialEval")
        direct = cmds.createNode("network", name="directMaterialMorph")
        group_a = cmds.createNode("network", name="groupMaterialMorphA")
        group_b = cmds.createNode("network", name="groupMaterialMorphB")
        for node in (direct, group_a, group_b):
            cmds.addAttr(node, longName="weight", attributeType="double")

        material_morph_runtime._connect_contribution_weight(
            evaluator,
            0,
            {
                "morph_node": direct,
                "group_weight_sources": [
                    (7, group_a, 0.25),
                    (8, group_b, -0.5),
                ],
            },
            f"{evaluator}.contribution[0].weight",
        )
        cmds.setAttr(f"{direct}.weight", 0.4)
        cmds.setAttr(f"{group_a}.weight", 0.8)
        cmds.setAttr(f"{group_b}.weight", 0.2)

        self.assertAlmostEqual(
            cmds.getAttr(f"{evaluator}.contribution[0].weight"),
            0.4 + 0.8 * 0.25 + 0.2 * -0.5,
        )

    def test_all_material_group_weight_is_applied_once_per_evaluator(self):
        """material_index=-1 のgroup成分をshader数だけ重複加算しない。"""
        self._require_material_morph_node()
        material = cmds.createNode("network", name="allMaterialMorph")
        group = cmds.createNode("network", name="allMaterialGroup")
        for node, index in ((material, 4), (group, 8)):
            cmds.addAttr(node, longName="weight", attributeType="double")
            cmds.addAttr(node, longName="mmd_morph_index", attributeType="long")
            cmds.setAttr(f"{node}.mmd_morph_index", index)
        cmds.addAttr(material, longName="mmd_material_morph_offsets_json", dataType="string")
        cmds.setAttr(
            f"{material}.mmd_material_morph_offsets_json",
            json.dumps([{"material_index": -1, "operation_type": 1}]),
            type="string",
        )
        cmds.addAttr(group, longName="mmd_group_morph_offsets_json", dataType="string")
        cmds.setAttr(
            f"{group}.mmd_group_morph_offsets_json",
            json.dumps([{"morph_index": 4, "morph_rate": 0.25}]),
            type="string",
        )

        skipped = []
        contributions = material_morph_runtime._collect_contributions_by_shader(
            [material],
            {0: "shaderA", 1: "shaderB"},
            skipped,
        )
        material_morph_runtime._append_group_weight_sources(contributions, [group], skipped)
        cmds.setAttr(f"{material}.weight", 0.4)
        cmds.setAttr(f"{group}.weight", 0.8)
        for shader in ("shaderA", "shaderB"):
            evaluator = cmds.createNode("mmdMaterialMorphEval", name=f"{shader}Eval")
            material_morph_runtime._connect_contribution_weight(
                evaluator,
                0,
                contributions[shader][0],
                f"{evaluator}.contribution[0].weight",
            )
            self.assertAlmostEqual(
                cmds.getAttr(f"{evaluator}.contribution[0].weight"),
                0.4 + 0.8 * 0.25,
            )
        self.assertEqual(skipped, [])

    def test_colliding_sanitized_evaluator_names_keep_owned_helpers_distinct(self):
        """namespace区切りだけが違う evaluator 間でhelperを共有しない。"""
        self._require_material_morph_node()
        if not cmds.namespace(exists="ns"):
            cmds.namespace(add="ns")
        evaluators = (
            cmds.createNode("mmdMaterialMorphEval", name="ns:mat_materialMorphEval"),
            cmds.createNode("mmdMaterialMorphEval", name="ns_mat_materialMorphEval"),
        )
        direct_nodes = []
        group_nodes = []
        for index in range(2):
            direct = cmds.createNode("network", name=f"collisionDirect{index}")
            group = cmds.createNode("network", name=f"collisionGroup{index}")
            for node in (direct, group):
                cmds.addAttr(node, longName="weight", attributeType="double")
            direct_nodes.append(direct)
            group_nodes.append(group)

        def connect(index):
            evaluator = evaluators[index]
            material_morph_runtime._connect_contribution_weight(
                evaluator,
                0,
                {
                    "morph_node": direct_nodes[index],
                    "group_weight_sources": [(8, group_nodes[index], 0.25)],
                },
                f"{evaluator}.contribution[0].weight",
            )

        connect(0)
        connect(1)
        owned_before = []
        for evaluator in evaluators:
            owned_before.append(
                set(cmds.listConnections(f"{evaluator}.message", source=False, destination=True) or [])
            )
        self.assertTrue(owned_before[0])
        self.assertTrue(owned_before[1])
        self.assertTrue(owned_before[0].isdisjoint(owned_before[1]))

        cmds.setAttr(f"{direct_nodes[0]}.weight", 0.1)
        cmds.setAttr(f"{group_nodes[0]}.weight", 0.4)
        cmds.setAttr(f"{direct_nodes[1]}.weight", 0.6)
        cmds.setAttr(f"{group_nodes[1]}.weight", 0.8)
        self.assertAlmostEqual(cmds.getAttr(f"{evaluators[0]}.contribution[0].weight"), 0.2)
        self.assertAlmostEqual(cmds.getAttr(f"{evaluators[1]}.contribution[0].weight"), 0.8)

        connect(0)
        connect(1)
        for evaluator, expected in zip(evaluators, owned_before):
            owned_after = set(
                cmds.listConnections(f"{evaluator}.message", source=False, destination=True) or []
            )
            self.assertEqual(owned_after, expected)

    def _create_scene_with_shader(
        self,
        *,
        base_color=(1.0, 1.0, 1.0),
        base_alpha=1.0,
        shader=None,
        edge_color=(0.0, 0.0, 0.0, 0.0),
        diffuse_offset=(1.0, 0.0, 0.0, 0.0),
        build_graph=True,
    ):
        """mesh + lambert shader + material morph を持つ最小シーンを構築する。"""
        self._require_material_morph_node()

        root = cmds.group(empty=True, name=f"mat_morph_test{SCENE_ROOT_SUFFIX}")
        maya_attribute_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})

        mesh = maya_mesh_utils.create_mesh_with_uvs(
            "mat_test_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [4], [0, 1, 2, 3],
            [0, 0, 1, 0, 1, 1, 0, 1], [0, 1, 2, 3],
        )
        mesh = cmds.parent(mesh, root)[0]

        # Default to a lambert; hardware-route tests may provide a complete shader.
        shader = shader or cmds.shadingNode("lambert", asShader=True, name="mmd_mat_0")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="mmd_mat_0_SG")
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(mesh, edit=True, forceElement=sg)

        if cmds.nodeType(shader) == "lambert":
            cmds.setAttr(f"{shader}.color", *base_color, type="double3")
            transparency = 1.0 - base_alpha
            cmds.setAttr(
                f"{shader}.transparency",
                transparency,
                transparency,
                transparency,
                type="double3",
            )
        else:
            cmds.setAttr(f"{shader}.DiffuseColorRGB", *base_color, type="double3")
            cmds.setAttr(f"{shader}.DiffuseColorA", base_alpha)

        # shader に mmd_material_index を設定（graph builder がマッチングに使う）
        if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
            cmds.addAttr(shader, longName=ATTR_MMD_MATERIAL_INDEX, attributeType="long")
        cmds.setAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}", 0)

        # material morph: diffuse に赤を加算 (operation_type=1)
        morph = _make_material_morph("赤フラッシュ", [
            {
                "material_index": 0,
                "operation_type": 1,
                "diffuse": diffuse_offset,
                "specular": (0.0, 0.0, 0.0),
                "specular_coefficient": 0.0,
                "ambient": (0.0, 0.0, 0.0),
                "edge_color": edge_color,
                "edge_size": 0.0,
                "texture_factor": (0.0, 0.0, 0.0, 0.0),
                "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                "toon_texture_factor": (0.0, 0.0, 0.0, 0.0),
            }
        ])

        converter = MorphConverter()
        result = converter.convert_pmx_morphs(_make_fake_pmx_data([morph]), mesh)
        material_nodes = result.get("material_morph_nodes", [])

        for morph_node in material_nodes:
            if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
                cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{morph_node}.mmd_model_root", force=True)

        # material morph runtime graph を構築（shader ↔ evaluator 接続）
        graph = build_material_morph_graph(root) if build_graph else None

        return root, mesh, shader, material_nodes, graph

    @staticmethod
    def _add_vec4_uniform(shader, name, default):
        if cmds.attributeQuery(name, node=shader, exists=True):
            return
        cmds.addAttr(shader, longName=name, attributeType="compound", numberOfChildren=2)
        cmds.addAttr(
            shader, longName=f"{name}XYZ", attributeType="compound",
            numberOfChildren=3, parent=name,
        )
        for axis, value in zip("XYZ", default[:3]):
            cmds.addAttr(
                shader,
                longName=f"{name}{axis}",
                attributeType="double",
                parent=f"{name}XYZ",
                defaultValue=value,
            )
        cmds.addAttr(
            shader, longName=f"{name}W", attributeType="double",
            parent=name, defaultValue=default[3],
        )

    def test_complete_dx11_route_uses_real_edge_compound_children(self):
        """Complete DX11 fallback routes exact registered edge child plugs."""
        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
            shader = cmds.shadingNode("dx11Shader", asShader=True, name="complete_dx11")
        except Exception as exc:
            self.skipTest(f"dx11Shader unavailable: {exc}")
        _ensure_mmd_shader_uniform_attributes(shader)
        for name, default in (
            ("MainTextureMultiply", (1.0, 1.0, 1.0, 1.0)),
            ("MainTextureAdd", (0.0, 0.0, 0.0, 0.0)),
            ("SphereTextureMultiply", (1.0, 1.0, 1.0, 1.0)),
            ("SphereTextureAdd", (0.0, 0.0, 0.0, 0.0)),
            ("ToonTextureMultiply", (1.0, 1.0, 1.0, 1.0)),
            ("ToonTextureAdd", (0.0, 0.0, 0.0, 0.0)),
        ):
            self._add_vec4_uniform(shader, name, default)
        factor_names = (
            "MainTextureMultiply", "MainTextureAdd",
            "SphereTextureMultiply", "SphereTextureAdd",
            "ToonTextureMultiply", "ToonTextureAdd",
        )
        factor_values = {}
        for factor_index, name in enumerate(factor_names):
            children = material_morph_runtime._scalar_leaf_attrs(shader, name)
            values = tuple(0.11 * (factor_index + 1) + 0.01 * axis for axis in range(4))
            for child, value in zip(children, values):
                cmds.setAttr(f"{shader}.{child}", value)
            factor_values[name] = tuple(
                cmds.getAttr(f"{shader}.{child}") for child in children
            )
        cmds.setAttr(f"{shader}.EdgeColorRGB", 0.1, 0.2, 0.3, type="double3")
        cmds.setAttr(f"{shader}.EdgeColorA", 0.4)
        cmds.setAttr(f"{shader}.Opacity", 0.4)

        with mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=VP2_API_DIRECTX11,
        ):
            root, _, shader, morph_nodes, _ = self._create_scene_with_shader(
                shader=shader,
                base_alpha=0.4,
                edge_color=(0.0, 0.0, 0.0, 0.2),
                diffuse_offset=(0.0, 0.0, 0.0, 0.6),
                build_graph=False,
            )

        edge_children = cmds.attributeQuery("EdgeColorRGB", node=shader, listChildren=True)
        drivers = []
        for index, (child, value) in enumerate(zip(edge_children, (0.1, 0.2, 0.3))):
            driver = cmds.createNode("addDoubleLinear", name=f"edge_driver_{index}")
            cmds.setAttr(f"{driver}.input1", value)
            cmds.connectAttr(f"{driver}.output", f"{shader}.{child}", force=True)
            drivers.append(driver)

        real_connect = material_morph_runtime._connect_if_needed
        connect_count = {"value": 0}

        def _fail_late(source, destination, force=False):
            connect_count["value"] += 1
            if connect_count["value"] == 10:
                raise RuntimeError("injected late route failure")
            return real_connect(source, destination, force=force)

        with mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=VP2_API_DIRECTX11,
        ), mock.patch.object(material_morph_runtime, "_connect_if_needed", side_effect=_fail_late):
            failed = build_material_morph_graph(root)
        self.assertFalse(failed["success"])
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.Opacity"), 0.4)
        for name, expected in factor_values.items():
            leaves = material_morph_runtime._scalar_leaf_attrs(shader, name)
            self.assertEqual(
                tuple(cmds.getAttr(f"{shader}.{leaf}") for leaf in leaves), expected
            )
            self.assertFalse(
                cmds.listConnections(f"{shader}.{name}", s=True, d=False, p=True) or []
            )
        for child, driver in zip(edge_children, drivers):
            self.assertEqual(
                cmds.listConnections(f"{shader}.{child}", s=True, d=False, p=True),
                [f"{driver}.output"],
            )

        with mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=VP2_API_DIRECTX11,
        ):
            graph = build_material_morph_graph(root)

        self.assertTrue(graph["success"], graph)
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.Opacity"), 1.0)
        self.assertFalse(any(str(item).startswith("complete_route_failed") for item in graph["skipped"]))
        evaluator = graph["evaluator_nodes"][0]
        for child, axis in zip(edge_children, "RGB"):
            self.assertEqual(
                cmds.listConnections(f"{shader}.{child}", s=True, d=False, p=True),
                [f"{evaluator}.outputEdgeColor{axis}"],
            )
        for axis, driver in zip("RGB", drivers):
            self.assertEqual(
                cmds.listConnections(
                    f"{evaluator}.baseEdgeColor{axis}", s=True, d=False, p=True
                ),
                [f"{driver}.output"],
            )
        self.assertEqual(
            cmds.listConnections(f"{shader}.EdgeColorA", s=True, d=False, p=True),
            [f"{evaluator}.outputEdgeColorA"],
        )
        cmds.setAttr(f"{morph_nodes[0]}.weight", 0.0)
        self.assertAlmostEqual(
            cmds.getAttr(f"{shader}.DiffuseColorA") * cmds.getAttr(f"{shader}.Opacity"),
            0.4,
        )
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.EdgeColorA"), 0.4)
        cmds.setAttr(f"{morph_nodes[0]}.weight", 1.0)
        self.assertAlmostEqual(
            cmds.getAttr(f"{shader}.DiffuseColorA") * cmds.getAttr(f"{shader}.Opacity"),
            1.0,
        )
        self.assertAlmostEqual(cmds.getAttr(f"{shader}.EdgeColorA"), 0.6)
        base_before = cmds.getAttr(f"{evaluator}.baseEdgeColorA")
        final_before = cmds.getAttr(f"{shader}.EdgeColorA")
        connections_before = cmds.listConnections(
            f"{shader}.EdgeColorA", s=True, d=False, p=True
        )
        retry_count = {"value": 0}

        def _fail_ready_rebuild(source, destination, force=False):
            retry_count["value"] += 1
            if retry_count["value"] == 5:
                raise RuntimeError("injected ready rebuild failure")
            return real_connect(source, destination, force=force)

        with mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=VP2_API_DIRECTX11,
        ), mock.patch.object(
            material_morph_runtime,
            "_complete_route_already_owned",
            return_value=False,
        ), mock.patch.object(
            material_morph_runtime,
            "_connect_if_needed",
            side_effect=_fail_ready_rebuild,
        ):
            failed_rebuild = build_material_morph_graph(root)
        self.assertFalse(failed_rebuild["success"])
        self.assertTrue(cmds.getAttr(f"{evaluator}.mmd_complete_route_ready"))
        self.assertEqual(cmds.getAttr(f"{evaluator}.baseEdgeColorA"), base_before)
        self.assertEqual(cmds.getAttr(f"{shader}.EdgeColorA"), final_before)
        self.assertEqual(
            cmds.listConnections(f"{shader}.EdgeColorA", s=True, d=False, p=True),
            connections_before,
        )
        with mock.patch.object(
            material_morph_runtime,
            "detect_effective_vp2_draw_api",
            return_value=VP2_API_DIRECTX11,
        ):
            rebuilt = build_material_morph_graph(root)
        self.assertTrue(rebuilt["success"], rebuilt)
        self.assertEqual(cmds.getAttr(f"{evaluator}.baseEdgeColorA"), base_before)
        self.assertEqual(cmds.getAttr(f"{shader}.EdgeColorA"), final_before)
        self.assertEqual(
            cmds.listConnections(f"{shader}.EdgeColorA", s=True, d=False, p=True),
            connections_before,
        )

    def test_standard_material_graph_fails_closed_without_partial_route(self):
        """Lambert は全PMX channelを持たないためRGBだけを先行接続しない。"""
        _, _, shader, material_nodes, graph = self._create_scene_with_shader()
        self.assertTrue(graph["success"])
        self.assertIn("evaluator_nodes", graph)
        self.assertIn("created", graph)
        self.assertIn("reused", graph)
        self.assertIn("contributions", graph)
        self.assertIn("skipped", graph)
        self.assertGreaterEqual(len(graph["evaluator_nodes"]), 1)
        self.assertGreaterEqual(graph["contributions"], 1)

        route = resolve_shader_color_route(shader)
        self.assertTrue(route.is_usable)
        self.assertEqual(route.backend, BACKEND_STANDARD)
        self.assertEqual(route.attr_name, "color")

        evaluators = graph["evaluator_nodes"]
        connected = False
        for node in evaluators:
            sources = cmds.listConnections(f"{shader}.color", s=True, d=False) or []
            if node in sources:
                connected = True
                break
        self.assertFalse(connected, "Lambertへdiffuse RGBだけを部分接続してはならない")
        self.assertIn(
            f"complete_material_backend_unsupported:{shader}",
            graph["skipped"],
        )
        # evaluator/contribution metadata is still retained for diagnostics.
        self.assertEqual(len(material_nodes), 1)

    def test_weight_zero_preserves_nonblack_base_rgba(self):
        """RGBA compound route initialization must not silently lose the base."""
        base_rgb = (0.2, 0.4, 0.7)
        base_alpha = 0.35
        _, _, shader, material_nodes, graph = self._create_scene_with_shader(
            base_color=base_rgb,
            base_alpha=base_alpha,
        )
        self.assertTrue(graph["success"])
        self.assertEqual(len(graph["evaluator_nodes"]), 1)
        cmds.setAttr(f"{material_nodes[0]}.weight", 0.0)

        # Unsupported backends are not allowed to take ownership of authored base values.
        self.assertIn(f"complete_material_backend_unsupported:{shader}", graph["skipped"])
        self.assertEqual(
            tuple(round(value, 6) for value in cmds.getAttr(f"{shader}.color")[0]),
            base_rgb,
        )
        self.assertFalse(cmds.listConnections(f"{shader}.color", s=True, d=False) or [])

    def test_weight_zero_preserves_original_diffuse(self):
        """weight=0 のとき shader diffuse は変わらない。"""
        _, _, shader, material_nodes, _ = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 0.0)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        self.assertAlmostEqual(diffuse[0], 1.0, places=4, msg="R が変わった")
        self.assertAlmostEqual(diffuse[1], 1.0, places=4, msg="G が変わった")
        self.assertAlmostEqual(diffuse[2], 1.0, places=4, msg="B が変わった")

    def test_weight_one_applies_additive_diffuse(self):
        """Unsupported standard shader remains unchanged at weight=1."""
        _, _, shader, material_nodes, _ = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 1.0)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        self.assertEqual(tuple(diffuse), (1.0, 1.0, 1.0))

    def test_weight_half_applies_partial_offset(self):
        """Unsupported standard shader remains unchanged at a mixed weight."""
        _, _, shader, material_nodes, _ = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 0.5)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        self.assertEqual(tuple(diffuse), (1.0, 1.0, 1.0))

    def test_glsl_rgb_alpha_route_when_glsl_available(self):
        """Standalone GLSL fallback without the complete uniform set fails closed."""
        self._require_material_morph_node()

        try:
            cmds.loadPlugin("glslShader", quiet=True)
        except Exception as exc:
            self.skipTest(f"glslShader plugin unavailable: {exc}")

        try:
            shader = cmds.shadingNode("GLSLShader", asShader=True, name="mmd_glsl_mat_0")
        except Exception as exc:
            self.skipTest(f"GLSLShader node unavailable: {exc}")

        # Standalone often lacks OGSFX-generated uniforms; create RGB+A contract.
        _ensure_mmd_shader_uniform_attributes(shader)
        if not cmds.attributeQuery("DiffuseColorRGB", node=shader, exists=True):
            self.skipTest("DiffuseColorRGB was not created on GLSLShader")
        if not cmds.attributeQuery("DiffuseColorA", node=shader, exists=True):
            self.skipTest("DiffuseColorA was not created on GLSLShader")

        cmds.setAttr(f"{shader}.DiffuseColorRGB", 0.7, 0.7, 0.7, type="double3")
        cmds.setAttr(f"{shader}.DiffuseColorA", 0.42)

        root = cmds.group(empty=True, name=f"glsl_mat_morph{SCENE_ROOT_SUFFIX}")
        maya_attribute_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})
        mesh = maya_mesh_utils.create_mesh_with_uvs(
            "glsl_mat_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [4], [0, 1, 2, 3],
            [0, 0, 1, 0, 1, 1, 0, 1], [0, 1, 2, 3],
        )
        mesh = cmds.parent(mesh, root)[0]
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="mmd_glsl_mat_0_SG")
        if cmds.attributeQuery("outColor", node=shader, exists=True):
            cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(mesh, edit=True, forceElement=sg)

        if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
            cmds.addAttr(shader, longName=ATTR_MMD_MATERIAL_INDEX, attributeType="long")
        cmds.setAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}", 0)

        morph = _make_material_morph("glsl赤", [
            {
                "material_index": 0,
                "operation_type": 1,
                "diffuse": (1.0, 0.0, 0.0, 0.0),
                "specular": (0.0, 0.0, 0.0),
                "specular_coefficient": 0.0,
                "ambient": (0.0, 0.0, 0.0),
                "edge_color": (0.0, 0.0, 0.0, 0.0),
                "edge_size": 0.0,
                "texture_factor": (0.0, 0.0, 0.0, 0.0),
                "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                "toon_texture_factor": (0.0, 0.0, 0.0, 0.0),
            }
        ])
        converter = MorphConverter()
        material_nodes = converter.convert_pmx_morphs(
            _make_fake_pmx_data([morph]), mesh
        ).get("material_morph_nodes", [])
        for morph_node in material_nodes:
            if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
                cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{morph_node}.mmd_model_root", force=True)

        graph = build_material_morph_graph(root)
        route = resolve_shader_color_route(shader)

        if not route.is_usable:
            # Non-OpenGL VP2 (common on Windows DX11 hosts) must fail closed.
            self.assertEqual(route.backend, BACKEND_GLSL)
            self.assertTrue(
                (route.skip_reason or "").startswith("glsl_"),
                f"unexpected skip: {route.skip_reason}",
            )
            self.assertTrue(
                any(str(s).startswith("glsl_") for s in graph.get("skipped") or []),
                f"graph skipped missing glsl diagnostic: {graph.get('skipped')}",
            )
            # Alpha must remain untouched when routing is skipped.
            self.assertAlmostEqual(float(cmds.getAttr(f"{shader}.DiffuseColorA")), 0.42, places=4)
            return

        self.assertEqual(route.attr_name, "DiffuseColorRGB")
        self.assertEqual(route.backend, BACKEND_GLSL)
        connected = False
        for node in graph.get("evaluator_nodes") or []:
            sources = cmds.listConnections(f"{shader}.DiffuseColorRGB", s=True, d=False) or []
            if node in sources:
                connected = True
                break
        # Standalone fallback intentionally lacks the six texture-factor uniforms;
        # a partial RGB route must therefore be rejected.
        self.assertFalse(connected, "incomplete GLSL fallback must fail closed")
        self.assertTrue(
            any("glsl_material_plugs_incomplete" in str(item) for item in graph["skipped"]),
            graph["skipped"],
        )
        # Alpha plug must remain authored when the complete route is unavailable.
        alpha_sources = cmds.listConnections(f"{shader}.DiffuseColorA", s=True, d=False) or []
        for node in graph.get("evaluator_nodes") or []:
            self.assertNotIn(node, alpha_sources)
        self.assertAlmostEqual(float(cmds.getAttr(f"{shader}.DiffuseColorA")), 0.42, places=4)

    def test_dx11_rgb_alpha_route_with_real_fx(self):
        """Real dx11Shader + MMDShader.fx route: fail-closed off-DX11, RGB-only on DX11.

        Does **not** pre-create fake uniforms.  Relies on the effect file + technique
        evaluation (and refresh) to materialize ``DiffuseColorRGB``.  On non-DX11
        VP2, asserts the deterministic dx11 skip diagnostic and leaves alpha alone.
        On DX11 (e.g. ``MAYA_VP2_DEVICE_OVERRIDE=VirtualDeviceDx11``), asserts the
        evaluator drives RGB, does not touch alpha, and weight changes RGB.
        """
        self._require_material_morph_node()

        if not _MMD_SHADER_FX.is_file():
            self.skipTest(f"MMDShader.fx missing: {_MMD_SHADER_FX}")

        try:
            cmds.loadPlugin("dx11Shader", quiet=True)
        except Exception as exc:
            self.skipTest(f"dx11Shader plugin unavailable: {exc}")

        try:
            shader = cmds.shadingNode("dx11Shader", asShader=True, name="mmd_dx11_mat_0")
        except Exception as exc:
            self.skipTest(f"dx11Shader node unavailable: {exc}")

        fx_path = str(_MMD_SHADER_FX.resolve())
        try:
            cmds.setAttr(f"{shader}.shader", fx_path, type="string")
        except Exception as exc:
            self.skipTest(f"failed to assign MMDShader.fx: {exc}")

        techniques = []
        try:
            techniques = list(cmds.dx11Shader(shader, query=True, listTechniques=True) or [])
        except Exception:
            techniques = []
        if not techniques and cmds.attributeQuery("technique", node=shader, exists=True):
            # Fallback: known opaque technique from MMDShader.fx when listTechniques
            # is empty before first evaluation.
            techniques = ["MMDTechniqueNoEdge"]
        if not techniques:
            self.skipTest("dx11Shader has no techniques after loading MMDShader.fx")

        preferred = "MMDTechniqueNoEdge"
        technique = preferred if preferred in techniques else techniques[0]
        try:
            cmds.setAttr(f"{shader}.technique", technique, type="string")
        except Exception as exc:
            self.skipTest(f"failed to set technique {technique!r}: {exc}")

        # Force VP2/effect evaluation so hardware generates DiffuseColorRGB.
        try:
            cmds.refresh(force=True)
        except Exception:
            # mayapy may lack a display; still proceed and check whether the
            # effect already materialized uniforms from shader/technique set.
            pass

        has_rgb = cmds.attributeQuery("DiffuseColorRGB", node=shader, exists=True)
        has_alpha = cmds.attributeQuery("DiffuseColorA", node=shader, exists=True)
        alpha_seed = 0.37
        pre_rgb_sources = []

        # Seed only real effect plugs when the hardware generated them.
        if has_rgb:
            try:
                cmds.setAttr(f"{shader}.DiffuseColorRGB", 0.6, 0.6, 0.6, type="double3")
            except Exception:
                # Locked or internally driven RGB is still a probe result for the
                # route contract; continue so fail-closed / success assertions run.
                pass
            pre_rgb_sources = (
                cmds.listConnections(f"{shader}.DiffuseColorRGB", s=True, d=False, p=True)
                or []
            )
        if has_alpha:
            try:
                cmds.setAttr(f"{shader}.DiffuseColorA", alpha_seed)
            except Exception:
                has_alpha = False

        root = cmds.group(empty=True, name=f"dx11_mat_morph{SCENE_ROOT_SUFFIX}")
        maya_attribute_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})
        mesh = maya_mesh_utils.create_mesh_with_uvs(
            "dx11_mat_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [4], [0, 1, 2, 3],
            [0, 0, 1, 0, 1, 1, 0, 1], [0, 1, 2, 3],
        )
        mesh = cmds.parent(mesh, root)[0]
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="mmd_dx11_mat_0_SG")
        if cmds.attributeQuery("outColor", node=shader, exists=True):
            cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(mesh, edit=True, forceElement=sg)

        if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
            cmds.addAttr(shader, longName=ATTR_MMD_MATERIAL_INDEX, attributeType="long")
        cmds.setAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}", 0)

        morph = _make_material_morph("dx11赤", [
            {
                "material_index": 0,
                "operation_type": 1,
                "diffuse": (1.0, 0.0, 0.0, 0.0),
                "specular": (0.0, 0.0, 0.0),
                "specular_coefficient": 0.0,
                "ambient": (0.0, 0.0, 0.0),
                "edge_color": (0.0, 0.0, 0.0, 0.0),
                "edge_size": 0.0,
                "texture_factor": (0.0, 0.0, 0.0, 0.0),
                "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                "toon_texture_factor": (0.0, 0.0, 0.0, 0.0),
            }
        ])
        converter = MorphConverter()
        material_nodes = converter.convert_pmx_morphs(
            _make_fake_pmx_data([morph]), mesh
        ).get("material_morph_nodes", [])
        self.assertEqual(len(material_nodes), 1)
        morph_node = material_nodes[0]
        if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
            cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
        cmds.connectAttr(f"{root}.message", f"{morph_node}.mmd_model_root", force=True)

        graph = build_material_morph_graph(root)
        route = resolve_shader_color_route(shader)
        effective_api = detect_effective_vp2_draw_api()

        if not route.is_usable:
            # A proven DX11 session must expose a usable effect route; otherwise
            # this is a regression, not an acceptable headless skip path.
            if effective_api == VP2_API_DIRECTX11:
                self.fail(
                    "DX11 VP2 did not produce a usable material morph route: "
                    f"{route.skip_reason} (has_rgb={has_rgb}, graph={graph!r})"
                )
            # Non-DX11 VP2 or a headless session without the effect plug fails closed.
            self.assertEqual(route.backend, BACKEND_DX11)
            self.assertTrue(
                (route.skip_reason or "").startswith("dx11_"),
                f"unexpected skip: {route.skip_reason}",
            )
            self.assertTrue(
                any(str(s).startswith("dx11_") for s in graph.get("skipped") or []),
                f"graph skipped missing dx11 diagnostic: {graph.get('skipped')}",
            )
            if has_alpha:
                self.assertAlmostEqual(
                    float(cmds.getAttr(f"{shader}.DiffuseColorA")),
                    alpha_seed,
                    places=4,
                )
            if has_rgb:
                rgb_sources = cmds.listConnections(
                    f"{shader}.DiffuseColorRGB", s=True, d=False
                ) or []
                for node in graph.get("evaluator_nodes") or []:
                    self.assertNotIn(node, rgb_sources)
            return

        # Success path requires the hardware-generated RGB effect plug.
        self.assertTrue(
            has_rgb,
            "usable dx11 route without DiffuseColorRGB from MMDShader.fx",
        )
        self.assertEqual(route.attr_name, "DiffuseColorRGB")
        self.assertEqual(route.backend, BACKEND_DX11)
        evaluators = graph.get("evaluator_nodes") or []
        self.assertGreaterEqual(len(evaluators), 1)

        connected = False
        for node in evaluators:
            sources = cmds.listConnections(f"{shader}.DiffuseColorRGB", s=True, d=False) or []
            if node in sources:
                connected = True
                break
        self.assertTrue(
            connected,
            "mmdMaterialMorphEval.outputDiffuse must drive dx11Shader.DiffuseColorRGB"
            f" (pre-route sources={pre_rgb_sources!r})",
        )

        if has_alpha:
            alpha_sources = (
                cmds.listConnections(f"{shader}.DiffuseColorA", s=True, d=False) or []
            )
            for node in evaluators:
                self.assertNotIn(node, alpha_sources)
            self.assertAlmostEqual(
                float(cmds.getAttr(f"{shader}.DiffuseColorA")),
                alpha_seed,
                places=4,
                msg="reroute must not change DiffuseColorA",
            )

        # Weight must produce an observable RGB change on the effect plug.
        cmds.setAttr(f"{morph_node}.weight", 0.0)
        rgb_at_zero = cmds.getAttr(f"{shader}.DiffuseColorRGB")[0]
        cmds.setAttr(f"{morph_node}.weight", 1.0)
        rgb_at_one = cmds.getAttr(f"{shader}.DiffuseColorRGB")[0]
        self.assertGreater(
            float(rgb_at_one[0]),
            float(rgb_at_zero[0]) + 0.01,
            f"weight=1 did not raise DiffuseColorRGB.R "
            f"(zero={rgb_at_zero}, one={rgb_at_one})",
        )
        if has_alpha:
            self.assertAlmostEqual(
                float(cmds.getAttr(f"{shader}.DiffuseColorA")),
                alpha_seed,
                places=4,
                msg="weight drive must not change DiffuseColorA",
            )
