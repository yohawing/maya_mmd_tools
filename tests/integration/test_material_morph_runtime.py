"""PMX material morph runtime integration tests.

material morph network node が shader パラメータに接続され、
weight 変更がマテリアル外観に反映されることを検証する。
"""

from pathlib import Path

from maya import cmds

from mmd_tools.converters import MorphConverter
from mmd_tools.converters.material_morph_runtime import build_material_morph_graph
from mmd_tools.core import maya_utils
from mmd_tools.core.constants import (
    SCENE_ROOT_SUFFIX,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MATERIAL_INDEX,
)
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.nodes.mmd_material_morph_eval_node import MmdMaterialMorphEvalNode
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
        maya_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})

        mesh = maya_utils.create_mesh_with_uvs(
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

    def _create_scene_with_shader(self):
        """mesh + lambert shader + material morph を持つ最小シーンを構築する。"""
        self._require_material_morph_node()

        root = cmds.group(empty=True, name=f"mat_morph_test{SCENE_ROOT_SUFFIX}")
        maya_utils.set_custom_attributes(root, {ATTR_MMD_MODEL_NAME: "test"})

        mesh = maya_utils.create_mesh_with_uvs(
            "mat_test_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [4], [0, 1, 2, 3],
            [0, 0, 1, 0, 1, 1, 0, 1], [0, 1, 2, 3],
        )
        mesh = cmds.parent(mesh, root)[0]

        # lambert shader を作成して割り当て
        shader = cmds.shadingNode("lambert", asShader=True, name="mmd_mat_0")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="mmd_mat_0_SG")
        cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
        cmds.sets(mesh, edit=True, forceElement=sg)

        # 初期 diffuse を白に
        cmds.setAttr(f"{shader}.color", 1.0, 1.0, 1.0, type="double3")

        # shader に mmd_material_index を設定（graph builder がマッチングに使う）
        if not cmds.attributeQuery(ATTR_MMD_MATERIAL_INDEX, node=shader, exists=True):
            cmds.addAttr(shader, longName=ATTR_MMD_MATERIAL_INDEX, attributeType="long")
        cmds.setAttr(f"{shader}.{ATTR_MMD_MATERIAL_INDEX}", 0)

        # material morph: diffuse に赤を加算 (operation_type=1)
        morph = _make_material_morph("赤フラッシュ", [
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

        for morph_node in material_nodes:
            if not cmds.attributeQuery("mmd_model_root", node=morph_node, exists=True):
                cmds.addAttr(morph_node, longName="mmd_model_root", attributeType="message")
            cmds.connectAttr(f"{root}.message", f"{morph_node}.mmd_model_root", force=True)

        # material morph runtime graph を構築（shader ↔ evaluator 接続）
        build_material_morph_graph(root)

        return root, mesh, shader, material_nodes

    def test_weight_zero_preserves_original_diffuse(self):
        """weight=0 のとき shader diffuse は変わらない。"""
        _, _, shader, material_nodes = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 0.0)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        self.assertAlmostEqual(diffuse[0], 1.0, places=4, msg="R が変わった")
        self.assertAlmostEqual(diffuse[1], 1.0, places=4, msg="G が変わった")
        self.assertAlmostEqual(diffuse[2], 1.0, places=4, msg="B が変わった")

    def test_weight_one_applies_additive_diffuse(self):
        """weight=1 で加算モーフが shader diffuse に反映される。

        operation_type=1 (加算), diffuse=(1,0,0,0) なので
        base(1,1,1) + weight*offset → (2,1,1) — clamp で (1,1,1) のまま等ではなく
        DG 接続で中間値が流れることを期待する。
        """
        _, _, shader, material_nodes = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 1.0)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        self.assertGreater(
            diffuse[0], 1.0 + 0.01,
            "weight=1 でも diffuse.R が変化しない",
        )

    def test_weight_half_applies_partial_offset(self):
        """weight=0.5 で加算量が半分になる。"""
        _, _, shader, material_nodes = self._create_scene_with_shader()
        morph_node = material_nodes[0]

        cmds.setAttr(f"{morph_node}.weight", 0.5)

        diffuse = cmds.getAttr(f"{shader}.color")[0]
        expected_r = 1.0 + 0.5 * 1.0  # base + weight * offset
        self.assertAlmostEqual(
            diffuse[0], expected_r, places=4,
            msg="weight=0.5 の diffuse.R が期待値と違う",
        )
