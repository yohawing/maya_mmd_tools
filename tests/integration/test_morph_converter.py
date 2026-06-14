import json
import os

from maya import cmds

from mmd_tools.core.pmx_data import PmxData
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.converters import MorphConverter, MeshConverter
from mmd_tools.core import maya_utils
from mmd_tools.core.constants import ATTR_MMD_SOURCE_VERTEX_INDICES
from mmd_tools.core.settings import settings
from mmd_tools.core.pmx_data.morph import PmxMorphType
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider


class TestMorphConverter(MayaTestBase):
    """
    MorphConverterクラスの統合テスト。
    Mayaのシーンに実際にモーフを作成し、正しく変換されるかを確認する。
    """

    def setUp(self):
        """
        各テストの前に実行される設定。
        テストに必要なMayaシーンのセットアップとテストデータのパスを準備。
        """
        super().setUp()
        # 新しいMayaシーンを作成
        cmds.file(new=True, force=True)

        # テスト環境ではdx11Shaderを無効にする
        settings.set("import.model.create_mmd_shaders", False)

        # TestFixtureProviderを初期化
        self.fixture_provider = TestFixtureProvider()

    def tearDown(self):
        """
        各テスト後のクリーンアップ処理。
        テスト中に作成されたノードやシーンの状態をリセット。
        """
        super().tearDown()
        # シーンをクリア
        cmds.file(new=True, force=True)
        # 一時ファイルをクリーンアップ
        self.fixture_provider.cleanup_temp_files()

    def test_convert_pmd_morphs(self):
        """PMDモーフがMayaに正しく変換されることをテストする。"""
        # TestFixtureProviderからPMDファイルパスを取得
        pmd_data, pmd_path = self.fixture_provider.load_pmd_data("miku_v2")

        # モーフデータが存在することを確認
        self.assertIsNotNone(pmd_data.morphs, "PMDデータにモーフがありません")

        if len(pmd_data.morphs) == 0:
            self.skipTest("PMDデータにモーフが含まれていません")

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmd_root")

        # テスト用のメッシュを作成（簡単な四角形）
        converter = MeshConverter(pmd_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data, root_group)

        # MorphConverterを作成して変換を実行
        morph_converter = MorphConverter()
        result = morph_converter.convert_pmd_morphs(pmd_data, mesh_name)

        # 結果の検証
        self.assertIsNotNone(result, "モーフ変換の結果がNoneです")
        self.assertTrue(result.get("success", False), "モーフ変換が失敗しました")

        # 変換されたモーフ数をチェック
        morphs_converted = result.get("morphs_converted", 0)
        self.assertGreaterEqual(morphs_converted, 0, "変換されたモーフ数が負の値です")

        # PMDの場合、ベースモーフを除いた数と比較
        expected_morphs = len([m for m in pmd_data.morphs if m.morph_type != 0])
        self.assertLessEqual(
            morphs_converted,
            expected_morphs,
            f"変換されたモーフ数({morphs_converted})が期待値({expected_morphs})を超えています",
        )

        # blendShapeノードのチェックは、実際にモーフが変換された場合のみ
        if morphs_converted > 0:
            blend_shape_nodes = result.get("blend_shape_nodes", [])
            self.assertGreater(len(blend_shape_nodes), 0, "blendShapeノードが作成されていません")

    def test_convert_pmx_morphs(self):
        """PMXモーフがMayaに正しく変換されることをテストする。"""
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_data, pmx_file_path = self.fixture_provider.load_pmx_data("mmt_test_model")

        # モーフデータが存在することを確認
        self.assertIsNotNone(pmx_data.morphs, "PMXデータにモーフがありません")

        if len(pmx_data.morphs) == 0:
            self.skipTest("PMXデータにモーフが含まれていません")

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmx_root")

        # メッシュを作成
        mesh_converter = MeshConverter(pmx_file_path)
        mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(pmx_data, root_group)

        # MorphConverterを作成して変換を実行
        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(pmx_data, mesh_name)

        # 結果の検証
        self.assertIsNotNone(result, "モーフ変換の結果がNoneです")
        self.assertTrue(result.get("success", False), "モーフ変換が失敗しました")

        # 変換されたモーフ数をチェック
        morphs_converted = result.get("morphs_converted", 0)
        self.assertGreaterEqual(morphs_converted, 0, "変換されたモーフ数が負の値です")

        # PMXの場合、頂点モーフのみがサポートされているため、頂点モーフの数と比較
        from mmd_tools.core.pmx_data.morph import PmxMorphType

        vertex_morphs = [m for m in pmx_data.morphs if m.morph_type == PmxMorphType.VertexMorph]
        self.assertLessEqual(
            morphs_converted,
            len(vertex_morphs),
            f"変換されたモーフ数({morphs_converted})が頂点モーフ数({len(vertex_morphs)})を超えています",
        )

    def test_convert_pmx_bone_morph_metadata(self):
        """PMX BoneMorph が network node として import されることをテストする。"""
        mesh_name = self._create_test_mesh()

        class FakeBoneMorph:
            name = "ボーン笑い"
            name_english = "bone_smile"
            panel = 4
            morph_type = PmxMorphType.BoneMorph
            offsets = [
                {
                    "bone_index": 3,
                    "translation": (1.0, 2.0, 3.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type("FakePmxData", (), {"morphs": [FakeBoneMorph()]})()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        bone_nodes = result.get("bone_morph_nodes", [])
        self.assertEqual(len(bone_nodes), 1)

        morph_node = bone_nodes[0]
        self.assertTrue(cmds.objExists(morph_node))
        self.assertTrue(cmds.attributeQuery("weight", node=morph_node, exists=True))
        self.assertTrue(cmds.getAttr(f"{morph_node}.weight", keyable=True))
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_name"), "ボーン笑い")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_type"), "bone")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_bone_morph_offset_count"), 1)

        offsets = json.loads(cmds.getAttr(f"{morph_node}.mmd_bone_morph_offsets_json"))
        self.assertEqual(offsets[0]["bone_index"], 3)
        self.assertEqual(offsets[0]["translation"], [1.0, 2.0, 3.0])

        cmds.delete(mesh_name, morph_node)

    def test_convert_pmx_material_morph_metadata(self):
        """PMX MaterialMorph が network node として import されることをテストする。"""
        mesh_name = self._create_test_mesh()

        class FakeMaterialMorph:
            name = "材質点滅"
            name_english = "material_flash"
            panel = 4
            morph_type = PmxMorphType.MaterialMorph
            offsets = [
                {
                    "material_index": 2,
                    "operation_type": 0,
                    "diffuse": (0.1, 0.2, 0.3, 0.4),
                    "specular": (0.5, 0.6, 0.7),
                    "specular_coefficient": 0.8,
                    "ambient": (0.9, 1.0, 1.1),
                    "edge_color": (0.2, 0.3, 0.4, 0.5),
                    "edge_size": 1.2,
                    "texture_factor": (1.0, 1.0, 1.0, 1.0),
                    "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                    "toon_texture_factor": (0.5, 0.5, 0.5, 0.5),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type("FakePmxData", (), {"morphs": [FakeMaterialMorph()]})()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        material_nodes = result.get("material_morph_nodes", [])
        self.assertEqual(len(material_nodes), 1)

        morph_node = material_nodes[0]
        self.assertTrue(cmds.objExists(morph_node))
        self.assertTrue(cmds.attributeQuery("weight", node=morph_node, exists=True))
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_name"), "材質点滅")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_type"), "material")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_material_morph_offset_count"), 1)

        offsets = json.loads(cmds.getAttr(f"{morph_node}.mmd_material_morph_offsets_json"))
        self.assertEqual(offsets[0]["material_index"], 2)
        self.assertEqual(offsets[0]["operation_type"], 0)
        self.assertEqual(offsets[0]["diffuse"], [0.1, 0.2, 0.3, 0.4])

        cmds.delete(mesh_name, morph_node)

    def test_material_split_mesh_skips_unaffected_vertex_morphs(self):
        """material split mesh では表示 material に関係しない vertex morph を作らない。"""
        mesh_a = self._create_test_mesh()
        mesh_b = self._create_test_mesh()
        maya_utils.set_custom_attributes(
            mesh_a,
            {
                "mmd_material_split_mesh": True,
                "mmd_material_index": 0,
            },
        )
        maya_utils.set_custom_attributes(
            mesh_b,
            {
                "mmd_material_split_mesh": True,
                "mmd_material_index": 1,
            },
        )

        class FakeFace:
            def __init__(self, indices):
                self.indices = indices

        class FakeMaterial:
            face_count = 3

        class FakeVertexMorph:
            morph_type = PmxMorphType.VertexMorph
            panel = 1

            def __init__(self, name, vertex_index):
                self.name = name
                self.offsets = [
                    {
                        "vertex_index": vertex_index,
                        "position_offset": (0.1, 0.0, 0.0),
                    }
                ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [FakeFace([0, 1, 2]), FakeFace([0, 2, 3])],
                "materials": [FakeMaterial(), FakeMaterial()],
                "morphs": [
                    FakeVertexMorph("mat0_only", 1),
                    FakeVertexMorph("mat1_only", 3),
                ],
            },
        )()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, [mesh_a, mesh_b])

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 2)
        self.assertEqual(result.get("vertex_morphs_skipped_by_material"), 2)
        self.assertEqual(len(result.get("blend_shape_nodes", [])), 2)

        mesh_a_aliases = cmds.aliasAttr(result["blend_shape_nodes"][0], query=True) or []
        mesh_b_aliases = cmds.aliasAttr(result["blend_shape_nodes"][1], query=True) or []
        self.assertIn("mat0_only", mesh_a_aliases)
        self.assertNotIn("mat1_only", mesh_a_aliases)
        self.assertIn("mat1_only", mesh_b_aliases)
        self.assertNotIn("mat0_only", mesh_b_aliases)

    def test_compact_material_split_mesh_maps_vertex_morph_source_indices(self):
        """compact split mesh では PMX source vertex index を local vertex index に写して morph を適用する。"""
        mesh = maya_utils.create_mesh_with_uvs(
            "compact_split_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
            [3],
            [0, 1, 2],
            [0, 0, 1, 0, 1, 1],
            [0, 1, 2],
        )
        maya_utils.set_custom_attributes(
            mesh,
            {
                "mmd_material_split_mesh": True,
                "mmd_material_index": 0,
            },
        )
        maya_utils.add_typed_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
        maya_utils.set_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, [0, 2, 3], "longArray")

        class FakeFace:
            indices = [0, 2, 3]

        class FakeMaterial:
            face_count = 3

        class FakeVertexMorph:
            name = "source2_move"
            morph_type = PmxMorphType.VertexMorph
            panel = 1
            offsets = [
                {
                    "vertex_index": 2,
                    "position_offset": (0.25, 0.0, 0.0),
                },
                {
                    "vertex_index": 1,
                    "position_offset": (10.0, 0.0, 0.0),
                },
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [FakeFace()],
                "materials": [FakeMaterial()],
                "morphs": [FakeVertexMorph()],
            },
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        self.assertTrue(cmds.objExists("source2_move_target"))
        moved_position = cmds.pointPosition("source2_move_target.vtx[1]", local=True)
        unchanged_position = cmds.pointPosition("source2_move_target.vtx[0]", local=True)
        self.assertAlmostEqual(moved_position[0], 1.25, places=5)
        self.assertAlmostEqual(unchanged_position[0], 0.0, places=5)

    def test_collect_morphs_from_scene_for_export(self):
        """シーン内の network metadata から exporter 用 morph dict を復元して PMX を再生成する。"""
        mesh_name = self._create_test_mesh()

        class FakeBoneMorph:
            name = "ボーン笑い"
            name_english = "bone_smile"
            panel = 4
            morph_type = PmxMorphType.BoneMorph
            offsets = [
                {
                    "bone_index": 0,
                    "translation": (1.0, 2.0, 3.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            ]

            def get_name(self):
                return self.name

        class FakeMaterialMorph:
            name = "材質点滅"
            name_english = "material_flash"
            panel = 5
            morph_type = PmxMorphType.MaterialMorph
            offsets = [
                {
                    "material_index": 0,
                    "operation_type": 0,
                    "diffuse": (0.1, 0.2, 0.3, 0.4),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"morphs": [FakeBoneMorph(), FakeMaterialMorph()]},
        )()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)
        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 2)
        self.assertEqual(len(result.get("bone_morph_nodes", [])), 1)
        self.assertEqual(len(result.get("material_morph_nodes", [])), 1)

        collected_morphs = morph_converter.collect_morphs_from_scene_for_export()
        self.assertEqual(len(collected_morphs), 2)
        self.assertTrue(any(m["type"] == "bone" and m["name"] == "ボーン笑い" for m in collected_morphs))
        self.assertTrue(
            any(
                m["type"] == "material"
                and m["name"] == "材質点滅"
                and m["offsets"][0]["material_index"] == 0
                for m in collected_morphs
            )
        )

        exporter = PmxExporter()
        out_pmx = os.path.join(self.temp_dir, "scene_morph_export.pmx")
        exporter.export_pmx_model(
            out_pmx,
            {
                "model_name": "SceneMorphRoundtrip",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
                ],
                "faces": [[0, 1, 2]],
                "bones": [{"name": "root", "position": [0.0, 0.0, 0.0]}],
                "materials": [{"name": "material"}],
                "morphs": collected_morphs,
            },
        )

        pmx = PmxData()
        pmx.parse_file(out_pmx)
        self.assertEqual(len(pmx.morphs), 2)
        self.assertTrue(any(int(m.morph_type) == 2 for m in pmx.morphs))
        self.assertTrue(any(int(m.morph_type) == 8 for m in pmx.morphs))

        cmds.delete(
            mesh_name,
            *(result.get("bone_morph_nodes", [])),
            *(result.get("material_morph_nodes", [])),
        )

    def test_simple_blendshape_creation(self):
        """シンプルなblendShape作成のテスト（Mayaの基本機能確認）"""
        # ベースメッシュを作成
        base_mesh = self._create_test_mesh()

        # ターゲットメッシュを作成（少し変形させる）
        target_mesh = cmds.duplicate(base_mesh)[0]  # type: ignore

        # ターゲットメッシュの頂点を少し移動
        cmds.select(f"{target_mesh}.vtx[0]")
        cmds.move(1, 0, 0, r=True)

        # blendShapeノードを作成
        blend_shape_node = cmds.blendShape(target_mesh, base_mesh)[0]  # type: ignore

        # 結果の検証
        self.assertTrue(cmds.objExists(blend_shape_node))  # type: ignore

        # ターゲットが追加されているか確認
        targets = cmds.listAttr(blend_shape_node + ".weight", m=True)  # type: ignore
        if targets:
            self.assertGreater(len(targets), 0)

        # ウェイトを変更してテスト
        cmds.setAttr(blend_shape_node + ".weight[0]", 0.5)  # type: ignore
        weight_value = cmds.getAttr(blend_shape_node + ".weight[0]")
        self.assertAlmostEqual(float(weight_value), 0.5, places=5)  # type: ignore

    def _create_test_mesh(self):
        """テスト用の簡単なメッシュを作成"""
        # 簡単な四角形のメッシュを作成
        vertices = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
        face_counts = [4]
        face_connects = [0, 1, 2, 3]
        uvs = [0, 0, 1, 0, 1, 1, 0, 1]
        face_uv_connects = [0, 1, 2, 3]

        mesh_name = maya_utils.create_mesh_with_uvs("test_mesh", vertices, face_counts, face_connects, uvs, face_uv_connects)

        return mesh_name
