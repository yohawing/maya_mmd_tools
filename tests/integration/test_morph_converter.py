import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

from maya import cmds

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.converters import MorphConverter, MeshConverter
from mmd_tools.core import maya_attribute_utils, maya_mesh_utils
from mmd_tools.core.constants import (
    ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON,
    ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_FLIP_MORPH_OFFSETS_JSON,
    ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON,
    ATTR_MMD_UV_MORPH_OFFSETS_JSON,
)
from mmd_tools.core.logger import get_logger
from mmd_tools.core.settings import settings
from mmd_tools.core.pmx_data.morph import PmxMorphType
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.io.model_import_pipeline import ModelImportPipeline


class TestMorphConverter(MayaTestBase):
    """
    MorphConverterクラスの統合テスト。
    Mayaのシーンに実際にモーフを作成し、正しく変換されるかを確認する。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin_path = Path(__file__).resolve().parents[2] / "mmd_tools" / "plugin_main.py"
        cls.load_plugin(str(plugin_path))

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

        morph_converter = MorphConverter(scale=2.0)
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
        self.assertEqual(offsets[0]["translation"], [2.0, 4.0, 6.0])
        raw_offsets = json.loads(cmds.getAttr(f"{morph_node}.{ATTR_MMD_BONE_MORPH_OFFSETS_RAW_JSON}"))
        self.assertEqual(raw_offsets[0]["translation"], [1.0, 2.0, 3.0])

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

    def test_flip_morph_expands_target_weight_through_controller(self):
        """PMX Flip は Group と同じ rate 展開で頂点 morph を実際に駆動する。"""
        mesh_name = self._create_test_mesh()

        class FakeVertexMorph:
            name = "vertex_target"
            name_english = "vertex_target"
            panel = 4
            morph_type = PmxMorphType.VertexMorph
            offsets = [{"vertex_index": 0, "position_offset": (0.8, 0.0, 0.0)}]

            def get_name(self):
                return self.name

        class FakeFlipMorph:
            name = "flip_target"
            name_english = "flip_target"
            panel = 4
            morph_type = PmxMorphType.FlipMorph
            offsets = [{"morph_index": 0, "flip_rate": 0.25}]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"morphs": [FakeVertexMorph(), FakeFlipMorph()]},
        )()

        converter = MorphConverter()
        result = converter.convert_pmx_morphs(fake_data, mesh_name)
        controller_root = cmds.group(empty=True, name="flip_controller_root")
        controller = converter.build_morph_controller(fake_data, controller_root, result)

        self.assertTrue(controller)
        self.assertEqual(
            json.loads(cmds.getAttr(f"{controller}.groupTopology")),
            {"0": [[1, 0.25]]},
        )

        cmds.setAttr(f"{controller}.inputWeight[1]", 1.0)
        self.assertAlmostEqual(cmds.getAttr(f"{controller}.outputWeight[0]"), 0.25, places=6)
        blend_shape = result["blend_shape_nodes"][0]
        self.assertAlmostEqual(cmds.getAttr(f"{blend_shape}.weight[0]"), 0.25, places=6)
        shape = (cmds.listRelatives(mesh_name, shapes=True, fullPath=True) or [])[0]
        self.assertAlmostEqual(cmds.pointPosition(f"{shape}.vtx[0]", local=True)[0], 0.2, places=6)

        cmds.delete(mesh_name, controller_root, *result["flip_impulse_morph_nodes"], *result["blend_shape_nodes"])

    def test_flip_morph_roundtrip_drives_vertex_after_fresh_import(self):
        """Exported PMX Flip morphs must drive a vertex after a fresh import."""
        base_position = (0.25, -0.5, 0.75)
        position_offset = (0.8, 0.1, 0.2)
        flip_rate = 0.5
        out_pmx = self.get_temp_filename("flip_vertex_roundtrip.pmx")

        PmxExporter().export_pmx_model(
            out_pmx,
            {
                "model_name": "FlipVertexRoundtrip",
                "vertices": [
                    {"position": list(base_position), "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
                ],
                "faces": [[0, 1, 2]],
                "morphs": [
                    {
                        "type": "vertex",
                        "name": "vertex_target",
                        "name_english": "vertex_target",
                        "panel": 4,
                        "offsets": [{"vertex_index": 0, "position_offset": list(position_offset)}],
                    },
                    {
                        "type": "flip",
                        "name": "flip_target",
                        "name_english": "flip_target",
                        "panel": 4,
                        "offsets": [{"morph_index": 0, "flip_rate": flip_rate}],
                    },
                ],
            },
        )

        exported = parse_pmx_file(
            out_pmx,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
        self.assertEqual(
            [int(morph.morph_type) for morph in exported.morphs],
            [int(PmxMorphType.VertexMorph), int(PmxMorphType.FlipMorph)],
        )
        self.assertEqual(exported.morphs[1].offsets, [{"morph_index": 0, "flip_rate": flip_rate}])

        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(
            out_pmx,
            options={
                "create_mmd_shaders": False,
                "import_morphs": True,
                "import_physics": False,
                "setup_rig": False,
                "setup_bone_orientation": False,
                "use_cpp_fast_load": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        self.assertIsNotNone(fresh_root)

        controllers = cmds.listConnections(
            f"{fresh_root}.mmd_morph_controller",
            source=True,
            destination=False,
        ) or []
        self.assertEqual(len(controllers), 1)
        controller = controllers[0]
        self.assertEqual(
            json.loads(cmds.getAttr(f"{controller}.groupTopology")),
            {"0": [[1, flip_rate]]},
        )

        mesh_shapes = [
            shape
            for shape in (
                cmds.listRelatives(
                    fresh_root,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                )
                or []
            )
            if not cmds.getAttr(f"{shape}.intermediateObject")
        ]
        self.assertEqual(len(mesh_shapes), 1)

        cmds.setAttr(f"{controller}.inputWeight[1]", 1.0)
        expected_position = (
            base_position[0] + position_offset[0] * flip_rate,
            base_position[1] + position_offset[1] * flip_rate,
            -base_position[2] - position_offset[2] * flip_rate,
        )
        actual_position = cmds.pointPosition(f"{mesh_shapes[0]}.vtx[0]", local=True)
        for actual, expected in zip(actual_position, expected_position):
            self.assertAlmostEqual(actual, expected, places=6)

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

    def test_convert_pmx_uv_morph_metadata_and_collect_for_export(self):
        """PMX UV morph offsets survive Maya metadata storage and root-scoped collection."""
        mesh_name = self._create_test_mesh()
        root_group = cmds.group(empty=True, name="uv_morph_root")

        class FakeUVMorph:
            name = "追加UV笑い"
            name_english = "additional_uv_smile"
            panel = 4
            morph_type = PmxMorphType.AdditionalUVMorph2
            offsets = [
                {
                    "vertex_index": 2,
                    "uv_offset": (0.1, -0.2, 0.3, -0.4),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type("FakePmxData", (), {"morphs": [FakeUVMorph()]})()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 1)
        uv_nodes = result.get("uv_morph_nodes", [])
        self.assertEqual(len(uv_nodes), 1)
        morph_node = uv_nodes[0]
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_morph_type"), "additional_uv2")
        self.assertEqual(cmds.getAttr(f"{morph_node}.mmd_uv_morph_offset_count"), 1)
        self.assertEqual(
            json.loads(cmds.getAttr(f"{morph_node}.{ATTR_MMD_UV_MORPH_OFFSETS_JSON}")),
            [{"vertex_index": 2, "uv_offset": [0.1, -0.2, 0.3, -0.4]}],
        )

        pipeline = ModelImportPipeline(
            logger=get_logger(__name__),
            filepath="<test fixture>",
            scale=1.0,
            options={},
        )
        model_registry = pipeline.create_model_registry(root_group)
        pipeline.connect_morph_nodes_to_root(
            root_group,
            result,
            model_registry=model_registry,
        )
        collected = MorphConverter().collect_morphs_from_scene_for_export(root_group=root_group)
        self.assertEqual(
            collected,
            [
                {
                    "type": "additional_uv2",
                    "name": "追加UV笑い",
                    "name_english": "additional_uv_smile",
                    "panel": 4,
                    "offsets": [{"vertex_index": 2, "uv_offset": [0.1, -0.2, 0.3, -0.4]}],
                    "index": 0,
                }
            ],
        )

        out_pmx = self.get_temp_filename("uv_morph_roundtrip.pmx")
        PmxExporter().export_pmx_model(
            out_pmx,
            {
                "model_name": "UvMorphRoundtrip",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
                ],
                "faces": [[0, 1, 2]],
                "morphs": collected,
            },
        )
        roundtripped = parse_pmx_file(
            out_pmx,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
        self.assertEqual(len(roundtripped.morphs), 1)
        self.assertEqual(int(roundtripped.morphs[0].morph_type), int(PmxMorphType.AdditionalUVMorph2))
        self.assertEqual(roundtripped.morphs[0].offsets[0]["vertex_index"], 2)
        for actual, expected in zip(
            roundtripped.morphs[0].offsets[0]["uv_offset"],
            (0.1, -0.2, 0.3, -0.4),
        ):
            self.assertAlmostEqual(actual, expected)

        cmds.delete(mesh_name, morph_node, root_group)

    def test_convert_pmx21_flip_impulse_metadata_and_collect_for_export(self):
        """PMX 2.1 morph metadata survives network storage and PMX export."""
        mesh_name = self._create_test_mesh()
        root_group = cmds.group(empty=True, name="pmx21_morph_root")

        class FakeFlipMorph:
            name = "Flip metadata"
            name_english = "flip_metadata"
            panel = 4
            morph_type = PmxMorphType.FlipMorph
            offsets = [{"morph_index": 1, "flip_rate": 0.25}]

            def get_name(self):
                return self.name

        class FakeImpulseMorph:
            name = "Impulse metadata"
            name_english = "impulse_metadata"
            panel = 4
            morph_type = PmxMorphType.ImpulseMorph
            offsets = [
                {
                    "rigid_body_index": 0,
                    "impulse": (0.1, -0.2, 0.3),
                    "torque": (-0.4, 0.5, -0.6),
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"morphs": [FakeFlipMorph(), FakeImpulseMorph()]},
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh_name)

        self.assertTrue(result.get("success", False))
        nodes = result.get("flip_impulse_morph_nodes", [])
        self.assertEqual(len(nodes), 2)
        flip_node, impulse_node = nodes
        self.assertEqual(cmds.getAttr(f"{flip_node}.mmd_morph_type"), "flip")
        self.assertEqual(cmds.getAttr(f"{flip_node}.mmd_flip_morph_offset_count"), 1)
        self.assertEqual(
            json.loads(cmds.getAttr(f"{flip_node}.{ATTR_MMD_FLIP_MORPH_OFFSETS_JSON}")),
            [{"morph_index": 1, "flip_rate": 0.25}],
        )
        self.assertEqual(cmds.getAttr(f"{impulse_node}.mmd_morph_type"), "impulse")
        self.assertEqual(cmds.getAttr(f"{impulse_node}.mmd_impulse_morph_offset_count"), 1)
        self.assertEqual(
            json.loads(cmds.getAttr(f"{impulse_node}.{ATTR_MMD_IMPULSE_MORPH_OFFSETS_JSON}")),
            [
                {
                    "rigid_body_index": 0,
                    "impulse": [0.1, -0.2, 0.3],
                    "torque": [-0.4, 0.5, -0.6],
                }
            ],
        )

        pipeline = ModelImportPipeline(
            logger=get_logger(__name__),
            filepath="<test fixture>",
            scale=1.0,
            options={},
        )
        model_registry = pipeline.create_model_registry(root_group)
        pipeline.connect_morph_nodes_to_root(root_group, result, model_registry=model_registry)
        collected = MorphConverter().collect_morphs_from_scene_for_export(root_group=root_group)
        self.assertEqual(
            collected,
            [
                {
                    "type": "flip",
                    "name": "Flip metadata",
                    "name_english": "flip_metadata",
                    "panel": 4,
                    "offsets": [{"morph_index": 1, "flip_rate": 0.25}],
                    "index": 0,
                },
                {
                    "type": "impulse",
                    "name": "Impulse metadata",
                    "name_english": "impulse_metadata",
                    "panel": 4,
                    "offsets": [
                        {
                            "rigid_body_index": 0,
                            "impulse": [0.1, -0.2, 0.3],
                            "torque": [-0.4, 0.5, -0.6],
                        }
                    ],
                    "index": 1,
                },
            ],
        )

        out_pmx = self.get_temp_filename("pmx21_morph_roundtrip.pmx")
        PmxExporter().export_pmx_model(
            out_pmx,
            {
                "model_name": "Pmx21MorphRoundtrip",
                "vertices": [
                    {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0]},
                    {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0]},
                    {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0]},
                ],
                "faces": [[0, 1, 2]],
                "rigid_bodies": [{"name": "ImpulseBody"}],
                "morphs": collected,
            },
        )
        roundtripped = parse_pmx_file(
            out_pmx,
            use_native_pmx_parse=False,
            require_native_pmx_parse=False,
        )
        self.assertAlmostEqual(roundtripped.header.version, 2.1, places=6)
        self.assertEqual([int(morph.morph_type) for morph in roundtripped.morphs], [9, 10])
        self.assertEqual(roundtripped.morphs[0].offsets[0]["morph_index"], 1)
        self.assertEqual(roundtripped.morphs[1].offsets[0]["rigid_body_index"], 0)

        cmds.file(new=True, force=True)
        fresh_root = import_mmd_file(
            str(out_pmx),
            options={
                "create_mmd_shaders": False,
                "import_physics": False,
                "setup_rig": False,
                "use_native_pmx_parse": False,
                "require_native_pmx_parse": False,
            },
        )
        self.assertIsNotNone(fresh_root)
        fresh_collected = MorphConverter().collect_morphs_from_scene_for_export(
            root_group=fresh_root
        )
        self.assertEqual(len(fresh_collected), len(collected))
        for actual, expected in zip(fresh_collected, collected):
            self.assertEqual(
                {key: actual[key] for key in ("type", "name", "name_english", "panel", "index")},
                {key: expected[key] for key in ("type", "name", "name_english", "panel", "index")},
            )
            self.assertEqual(len(actual["offsets"]), len(expected["offsets"]))
            for actual_offset, expected_offset in zip(actual["offsets"], expected["offsets"]):
                self.assertEqual(
                    {key: actual_offset[key] for key in actual_offset if key not in ("flip_rate", "impulse", "torque")},
                    {key: expected_offset[key] for key in expected_offset if key not in ("flip_rate", "impulse", "torque")},
                )
                if "flip_rate" in expected_offset:
                    self.assertAlmostEqual(actual_offset["flip_rate"], expected_offset["flip_rate"], places=6)
                for vector_key in ("impulse", "torque"):
                    if vector_key in expected_offset:
                        for actual_component, expected_component in zip(
                            actual_offset[vector_key], expected_offset[vector_key]
                        ):
                            self.assertAlmostEqual(actual_component, expected_component, places=6)

        output_path = self.get_temp_filename("pmx21_morph_public_rejected.pmx")
        sentinel = b"existing output must survive"
        Path(output_path).write_bytes(sentinel)
        writer = MagicMock()
        result = ExportModelAction(pmx_exporter=writer).execute(
            ExportModelRequest(
                file_path=output_path,
                options={"export_format": "pmx", "target_model": fresh_root},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIn(
            "MORPH_TYPE_UNSUPPORTED",
            [issue.code for issue in result.validation_report.issues],
        )
        writer.export_pmx_model.assert_not_called()
        self.assertEqual(Path(output_path).read_bytes(), sentinel)


    def test_hazardous_network_names_and_controller_aliases_are_safe_and_unique(self):
        """Material/morph names with namespaces and punctuation stay addressable."""
        mesh_name = self._create_test_mesh()

        class FakeMaterialMorph:
            morph_type = PmxMorphType.MaterialMorph
            name_english = ""
            panel = 4
            offsets = [{"material_index": 0, "operation_type": 0}]

            def __init__(self, name):
                self.name = name

            def get_name(self):
                return self.name

        morphs = [
            FakeMaterialMorph("1:髪"),
            FakeMaterialMorph("2:髪+"),
            FakeMaterialMorph("にっこり"),
            FakeMaterialMorph("にやり"),
        ]
        fake_data = type("FakePmxData", (), {"morphs": morphs, "faces": [], "materials": []})()

        converter = MorphConverter()
        result = converter.convert_pmx_morphs(fake_data, mesh_name)
        self.assertTrue(result.get("success", False))
        nodes = result.get("material_morph_nodes", [])
        self.assertEqual(len(nodes), len(morphs))
        self.assertEqual(len(set(nodes)), len(nodes))
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        self.assertTrue(all(identifier.fullmatch(node.rsplit("|", 1)[-1]) for node in nodes))
        self.assertEqual(
            [cmds.getAttr(f"{node}.mmd_morph_name") for node in nodes],
            [morph.name for morph in morphs],
        )

        root = cmds.group(empty=True, name="hazardous_morph_root")
        controller = converter.build_morph_controller(fake_data, root, result)
        aliases = set((cmds.aliasAttr(controller, query=True) or [])[0::2])
        self.assertEqual(len(aliases), len(morphs))
        self.assertTrue(all(identifier.fullmatch(alias) for alias in aliases))

        cmds.delete(mesh_name, root, *nodes, controller)

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
        vertex_nodes = result.get("vertex_morph_nodes", [])
        self.assertEqual(len(vertex_nodes), 2)
        self.assertEqual(len(set(vertex_nodes)), 2)
        self.assertEqual(
            [cmds.getAttr(f"{node}.mmd_morph_index") for node in vertex_nodes],
            [0, 1],
        )
        self.assertEqual(
            json.loads(cmds.getAttr(f"{vertex_nodes[0]}.{ATTR_MMD_VERTEX_MORPH_OFFSETS_RAW_JSON}")),
            [{"vertex_index": 1, "position_offset": [0.1, 0.0, 0.0]}],
        )

        mesh_a_aliases = cmds.aliasAttr(result["blend_shape_nodes"][0], query=True) or []
        mesh_b_aliases = cmds.aliasAttr(result["blend_shape_nodes"][1], query=True) or []
        self.assertIn("mat0_only", mesh_a_aliases)
        self.assertNotIn("mat1_only", mesh_a_aliases)
        self.assertIn("mat1_only", mesh_b_aliases)
        self.assertNotIn("mat0_only", mesh_b_aliases)

    def test_vertex_morph_metadata_rejects_malformed_offsets(self):
        """Malformed source offsets fail before the per-mesh preview path can skip them."""
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            name = "malformed_vertex"
            name_english = ""
            panel = 1
            morph_type = PmxMorphType.VertexMorph
            offsets = [{"vertex_index": True, "position_offset": (0.1, 0.0, 0.0)}]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"faces": [], "materials": [], "morphs": [FakeVertexMorph()]},
        )()

        with self.assertRaises(ValueError):
            MorphConverter().convert_pmx_morphs(fake_data, mesh)

    def test_vertex_morph_metadata_rejects_unknown_offset_fields(self):
        """Unknown fields are not silently discarded from the semantic record."""
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            name = "unknown_field_vertex"
            name_english = ""
            panel = 1
            morph_type = PmxMorphType.VertexMorph
            offsets = [
                {
                    "vertex_index": 0,
                    "position_offset": (0.0, 0.0, 0.0),
                    "unexpected": 1,
                }
            ]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"faces": [], "materials": [], "morphs": [FakeVertexMorph()]},
        )()

        with self.assertRaises(ValueError):
            MorphConverter().convert_pmx_morphs(fake_data, mesh)

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

        converter = MorphConverter()
        result = converter.convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 2)

        bs_node = result["blend_shape_nodes"][0]

        # alias は衝突しても一意化される（grin / grin_1）
        alias_names = set((cmds.aliasAttr(bs_node, query=True) or [])[0::2])
        self.assertEqual(len(alias_names), 2)
        self.assertIn("grin", alias_names)
        self.assertIn("grin_1", alias_names)

        reserved_names = [
            "inputWeight",
            "outputWeight",
            "message",
            "groupTopology",
            "topologyVersion",
        ]
        controller_data = type(
            "ControllerPmxData",
            (),
            {
                "morphs": fake_data.morphs
                + [FakeVertexMorph(name, 1) for name in reserved_names]
                + [FakeVertexMorph("inputWeight", 2)]
            },
        )()
        controller_result = dict(result)
        controller_result["total_morphs"] = len(controller_data.morphs)
        root = cmds.group(empty=True, name="controller_alias_root")
        controller = converter.build_morph_controller(controller_data, root, controller_result)
        expected_indices = list(range(len(controller_data.morphs)))
        self.assertEqual(
            cmds.getAttr(f"{controller}.inputWeight", multiIndices=True),
            expected_indices,
        )
        self.assertTrue(cmds.getAttr(f"{controller}.inputWeight[0]", keyable=True))
        self.assertTrue(cmds.getAttr(f"{controller}.inputWeight[1]", keyable=True))
        controller_aliases = set((cmds.aliasAttr(controller, query=True) or [])[0::2])
        self.assertEqual(len(controller_aliases), len(controller_data.morphs))
        self.assertTrue({"grin", "grin_1"}.issubset(controller_aliases))
        self.assertTrue(controller_aliases.isdisjoint(reserved_names))

        # 生のモーフ名が weight index 対応で保存されている（権威キー）
        self.assertTrue(
            cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=bs_node, exists=True)
        )
        stored = json.loads(cmds.getAttr(f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}"))
        self.assertEqual(stored.get("0"), {"name": "にっこり", "index": 0})
        self.assertEqual(stored.get("1"), {"name": "にやり", "index": 1})

    def test_vertex_morph_does_not_store_empty_raw_name(self):
        """An unnamed PMX morph may have a usable alias but is not a VMD key."""
        mesh = self._create_test_mesh()

        class FakeVertexMorph:
            name = ""
            name_english = ""
            morph_type = PmxMorphType.VertexMorph
            panel = 4
            offsets = [{"vertex_index": 1, "position_offset": (0.1, 0.0, 0.0)}]

            def get_name(self):
                return ""

        fake_data = type(
            "FakePmxData",
            (),
            {"faces": [], "materials": [], "morphs": [FakeVertexMorph()]},
        )()

        result = MorphConverter().convert_pmx_morphs(fake_data, mesh)

        self.assertTrue(result.get("success", False))
        bs_node = result["blend_shape_nodes"][0]
        if cmds.attributeQuery(ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=bs_node, exists=True):
            stored = json.loads(
                cmds.getAttr(f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
            )
            self.assertEqual(stored, {})

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

        class FakeGroupMorph:
            name = "グループ笑い"
            name_english = "group_smile"
            panel = 4
            morph_type = PmxMorphType.GroupMorph
            offsets = [{"morph_index": 0, "morph_rate": 0.5}]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {"morphs": [FakeBoneMorph(), FakeGroupMorph(), FakeMaterialMorph()]},
        )()

        morph_converter = MorphConverter()
        result = morph_converter.convert_pmx_morphs(fake_data, mesh_name)
        self.assertTrue(result.get("success", False))
        self.assertEqual(result.get("morphs_converted"), 3)
        self.assertEqual(len(result.get("bone_morph_nodes", [])), 1)
        self.assertEqual(len(result.get("group_morph_nodes", [])), 1)
        self.assertEqual(len(result.get("material_morph_nodes", [])), 1)

        collected_morphs = morph_converter.collect_morphs_from_scene_for_export()
        self.assertEqual(len(collected_morphs), 3)
        self.assertTrue(any(m["type"] == "bone" and m["name"] == "ボーン笑い" for m in collected_morphs))
        self.assertTrue(
            any(
                m["type"] == "group"
                and m["name"] == "グループ笑い"
                and m["name_english"] == "group_smile"
                and m["panel"] == 4
                and m["offsets"] == [{"morph_index": 0, "morph_rate": 0.5}]
                for m in collected_morphs
            )
        )
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
        self.assertEqual(len(pmx.morphs), 3)
        self.assertTrue(any(int(m.morph_type) == 2 for m in pmx.morphs))
        group_morph = next(m for m in pmx.morphs if m.name == "グループ笑い")
        self.assertEqual(int(group_morph.morph_type), int(PmxMorphType.GroupMorph))
        self.assertEqual(group_morph.offsets[0]["morph_index"], 0)
        self.assertAlmostEqual(group_morph.offsets[0]["morph_rate"], 0.5)
        self.assertTrue(any(int(m.morph_type) == 8 for m in pmx.morphs))

        cmds.delete(
            mesh_name,
            *(result.get("bone_morph_nodes", [])),
            *(result.get("group_morph_nodes", [])),
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
