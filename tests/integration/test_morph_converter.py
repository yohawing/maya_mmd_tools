import json
import os
from unittest.mock import MagicMock

from maya import cmds

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.converters import MorphConverter, MeshConverter
from mmd_tools.core import maya_attribute_utils, maya_mesh_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
)
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

    def test_convert_pmx_morphs(self):
        """PMXモーフがMayaに正しく変換されることをテストする。"""
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_data, pmx_file_path = self.fixture_provider.load_pmx_data("test_morph_model")

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
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_index"), 0)
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_bone_morph_offset_count"), 1)

        offsets = json.loads(cmds.getAttr(f"{morph_node}.mmd_bone_morph_offsets_json"))
        self.assertEqual(offsets[0]["bone_index"], 3)
        self.assertEqual(offsets[0]["translation"], [1.0, 2.0, 3.0])

        cmds.delete(mesh_name, morph_node)

    def test_convert_pmx_group_morph_metadata(self):
        """PMX GroupMorph が network node として import されることをテストする。"""
        mesh_name = self._create_test_mesh()

        class FakeGroupMorph:
            name = "グループ笑い"
            name_english = "group_smile"
            panel = 4
            morph_type = PmxMorphType.GroupMorph
            offsets = [
                {
                    "morph_index": 3,
                    "morph_rate": 0.25,
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type("FakePmxData", (), {"morphs": [FakeGroupMorph()]})()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        group_nodes = result.get("group_morph_nodes", [])
        self.assertEqual(len(group_nodes), 1)

        morph_node = group_nodes[0]
        self.assertTrue(cmds.objExists(morph_node))
        self.assertTrue(cmds.attributeQuery("weight", node=morph_node, exists=True))
        self.assertTrue(cmds.getAttr(f"{morph_node}.weight", keyable=True))
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_name"), "グループ笑い")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_type"), "group")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_index"), 0)
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_group_morph_offset_count"), 1)

        offsets = json.loads(cmds.getAttr(f"{morph_node}.mmd_group_morph_offsets_json"))
        self.assertEqual(offsets[0]["morph_index"], 3)
        self.assertEqual(offsets[0]["morph_rate"], 0.25)

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

    def test_successful_per_morph_conversion_logs_at_debug_not_info(self):
        """成功した per-item 変換詳細は debug に出し、info には出さない。"""
        mesh_name = self._create_test_mesh()

        class FakeVertexMorph:
            name = "vertex_success"
            name_english = "vertex_success"
            panel = 1
            morph_type = PmxMorphType.VertexMorph
            offsets = [{"vertex_index": 0, "position_offset": (0.1, 0.0, 0.0)}]

            def get_name(self):
                return self.name

        class FakeBoneMorph:
            name = "bone_success"
            name_english = "bone_success"
            panel = 4
            morph_type = PmxMorphType.BoneMorph
            offsets = [
                {
                    "bone_index": 0,
                    "translation": (0.0, 0.0, 0.0),
                    "rotation": (0.0, 0.0, 0.0, 1.0),
                }
            ]

            def get_name(self):
                return self.name

        class FakeGroupMorph:
            name = "group_success"
            name_english = "group_success"
            panel = 4
            morph_type = PmxMorphType.GroupMorph
            offsets = [{"morph_index": 0, "morph_rate": 1.0}]

            def get_name(self):
                return self.name

        class FakeMaterialMorph:
            name = "material_success"
            name_english = "material_success"
            panel = 4
            morph_type = PmxMorphType.MaterialMorph
            offsets = [
                {
                    "material_index": 0,
                    "operation_type": 0,
                    "diffuse": (0.0, 0.0, 0.0, 0.0),
                    "specular": (0.0, 0.0, 0.0),
                    "specular_coefficient": 0.0,
                    "ambient": (0.0, 0.0, 0.0),
                    "edge_color": (0.0, 0.0, 0.0, 0.0),
                    "edge_size": 0.0,
                    "texture_factor": (0.0, 0.0, 0.0, 0.0),
                    "sphere_texture_factor": (0.0, 0.0, 0.0, 0.0),
                    "toon_texture_factor": (0.0, 0.0, 0.0, 0.0),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [],
                "materials": [],
                "morphs": [
                    FakeVertexMorph(),
                    FakeBoneMorph(),
                    FakeGroupMorph(),
                    FakeMaterialMorph(),
                ],
            },
        )()

        expected_success_messages = [
            "Successfully converted morph: vertex_success",
            "Successfully imported bone morph metadata: bone_success",
            "Successfully imported group morph metadata: group_success",
            "Successfully imported material morph metadata: material_success",
        ]

        morph_converter = MorphConverter()
        morph_converter.logger = MagicMock()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 4)

        # call.args is Python 3.8+; use tuple indexing for 3.7 compatibility
        debug_messages = [
            call[0][0] for call in morph_converter.logger.debug.call_args_list if call[0]
        ]
        info_messages = [
            call[0][0] for call in morph_converter.logger.info.call_args_list if call[0]
        ]

        for message in expected_success_messages:
            self.assertIn(message, debug_messages)
            self.assertNotIn(message, info_messages)

    def test_material_split_mesh_skips_unaffected_vertex_morphs(self):
        """material split mesh では表示 material に関係しない vertex morph を作らない。"""
        mesh_a = self._create_test_mesh()
        mesh_b = self._create_test_mesh()
        maya_attribute_utils.set_custom_attributes(
            mesh_a,
            {
                "mmd_material_split_mesh": True,
                "mmd_material_index": 0,
            },
        )
        maya_attribute_utils.set_custom_attributes(
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
        mesh = maya_mesh_utils.create_mesh_with_uvs(
            "compact_split_mesh",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
            [3],
            [0, 1, 2],
            [0, 0, 1, 0, 1, 1],
            [0, 1, 2],
        )
        maya_attribute_utils.set_custom_attributes(
            mesh,
            {
                "mmd_material_split_mesh": True,
                "mmd_material_index": 0,
            },
        )
        maya_attribute_utils.add_typed_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, "longArray")
        maya_attribute_utils.set_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES, [0, 2, 3], "longArray")

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
        # テンプレートメッシュは削除される — blendShape 経由でデルタを検証
        bs_node = result["results"][0]["blend_shape_node"]
        alias = result["results"][0]["alias"]
        cmds.setAttr(f"{bs_node}.{alias}", 1.0)
        moved_position = cmds.pointPosition(f"{mesh}.vtx[1]", local=True)
        unchanged_position = cmds.pointPosition(f"{mesh}.vtx[0]", local=True)
        self.assertAlmostEqual(moved_position[0], 1.25, places=5)
        self.assertAlmostEqual(unchanged_position[0], 0.0, places=5)
        cmds.setAttr(f"{bs_node}.{alias}", 0.0)

    def test_vertex_morph_stores_raw_name_and_uniquifies_colliding_alias(self):
        """sanitize が衝突する別モーフでも一意 alias を割り当て、生名を JSON に保存する。

        「にっこり」と「にやり」はどちらも sanitize_text で "grin" に化けるため、
        従来は aliasAttr 衝突で片方が到達不能になり、辞書逆引きでも取り違えが起きた。
        """
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            morph_type = PmxMorphType.VertexMorph
            panel = 1
            name_english = ""

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
                "faces": [],
                "materials": [],
                "morphs": [
                    FakeVertexMorph("にっこり", 1),
                    FakeVertexMorph("にやり", 2),
                ],
            },
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 2)

        bs_node = result["blend_shape_nodes"][0]

        # alias は衝突しても一意化される（grin / grin_1）
        alias_names = set((cmds.aliasAttr(bs_node, query=True) or [])[0::2])
        self.assertEqual(len(alias_names), 2)
        self.assertIn("grin", alias_names)
        self.assertIn("grin_1", alias_names)

        # 生のモーフ名が weight index 対応で保存されている（権威キー）
        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=bs_node, exists=True)
        )
        stored = json.loads(cmds.getAttr(f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"))
        self.assertEqual(stored.get("0"), {"name": "にっこり", "index": 0})
        self.assertEqual(stored.get("1"), {"name": "にやり", "index": 1})

    def test_vertex_morph_targets_keep_independent_offsets(self):
        """複数 vertex morph target が最後の target geometry に潰れないことを確認する。"""
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            morph_type = PmxMorphType.VertexMorph
            panel = 1
            name_english = ""

            def __init__(self, name, vertex_index, position_offset):
                self.name = name
                self.offsets = [
                    {
                        "vertex_index": vertex_index,
                        "position_offset": position_offset,
                    }
                ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [],
                "materials": [],
                "morphs": [
                    FakeVertexMorph("move_vertex_1", 1, (0.25, 0.0, 0.0)),
                    FakeVertexMorph("move_vertex_2", 2, (0.0, 0.5, 0.0)),
                ],
            },
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 2)
        bs_node = result["blend_shape_nodes"][0]
        alias_a = result["results"][0]["alias"]
        alias_b = result["results"][1]["alias"]

        cmds.setAttr(f"{bs_node}.{alias_a}", 1.0)
        cmds.setAttr(f"{bs_node}.{alias_b}", 0.0)
        vertex_1_with_a = cmds.pointPosition(f"{mesh}.vtx[1]", local=True)
        vertex_2_with_a = cmds.pointPosition(f"{mesh}.vtx[2]", local=True)
        self.assertAlmostEqual(vertex_1_with_a[0], 1.25, places=5)
        self.assertAlmostEqual(vertex_2_with_a[1], 1.0, places=5)

        cmds.setAttr(f"{bs_node}.{alias_a}", 0.0)
        cmds.setAttr(f"{bs_node}.{alias_b}", 1.0)
        vertex_1_with_b = cmds.pointPosition(f"{mesh}.vtx[1]", local=True)
        vertex_2_with_b = cmds.pointPosition(f"{mesh}.vtx[2]", local=True)
        self.assertAlmostEqual(vertex_1_with_b[0], 1.0, places=5)
        self.assertAlmostEqual(vertex_2_with_b[1], 1.5, places=5)

    def test_vertex_morph_z_offset_is_flipped_to_maya_space(self):
        """PMX vertex morph の z offset は Maya mesh space では反転される。"""
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            name = "move_z"
            morph_type = PmxMorphType.VertexMorph
            panel = 1
            name_english = ""
            offsets = [
                {
                    "vertex_index": 1,
                    "position_offset": (0.0, 0.0, 2.0),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "faces": [],
                "materials": [],
                "morphs": [FakeVertexMorph()],
            },
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        bs_node = result["blend_shape_nodes"][0]
        alias = result["results"][0]["alias"]

        cmds.setAttr(f"{bs_node}.{alias}", 1.0)
        moved_position = cmds.pointPosition(f"{mesh}.vtx[1]", local=True)
        self.assertAlmostEqual(moved_position[0], 1.0, places=5)
        self.assertAlmostEqual(moved_position[1], 0.0, places=5)
        self.assertAlmostEqual(moved_position[2], -2.0, places=5)

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

        pmx = parse_pmx_file(
            out_pmx,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
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

        mesh_name = maya_mesh_utils.create_mesh_with_uvs(
            "test_mesh",
            vertices,
            face_counts,
            face_connects,
            uvs,
            face_uv_connects,
        )

        return mesh_name
