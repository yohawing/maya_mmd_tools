import re
from types import SimpleNamespace

from maya import cmds
from maya.api import OpenMaya as om

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.settings import settings
from mmd_tools.converters import mesh_converter as mesh_converter_module
from mmd_tools.converters import MeshConverter
from mmd_tools.core import maya_attribute_utils, maya_material_utils, maya_mesh_utils
from tests.common.maya_test_base import MayaTestBase
from tests.common.test_fixture_provider import TestFixtureProvider
from mmd_tools.core.constants import (
    ATTR_MMD_TOON_TEXTURE_INDEX,
    ATTR_MMD_MATERIAL_NAME,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MEMO,
    ATTR_MMD_DRAW_FLAGS,
    ATTR_MMD_EDGE_COLOR,
    ATTR_MMD_EDGE_SIZE,
    ATTR_MMD_TEXTURE_INDEX,
    ATTR_MMD_SPHERE_TEXTURE_INDEX,
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SHARED_TOON_FLAG,
    ATTR_MMD_MATERIAL_INDEX,
)


class TestMeshConverter(MayaTestBase):
    """
    MeshConverterクラスの統合テスト。
    Mayaのシーンに実際にメッシュを作成し、正しく変換されるかを確認する。
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
        settings.set("import.model.separate_meshes_by_material", False)

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

    def test_convert_pmx_mesh(self):
        """
        PMXメッシュがMayaに正しく変換されることをテストする。
        実際のPMXファイルを読み込み、変換処理を実行し、結果を検証する。
        """
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_file_path = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        pmx_data = parse_pmx_file(pmx_file_path)

        # モデル名を取得
        model_name = pmx_data.header.model_name
        self.assertIsNotNone(model_name, "モデル名がNoneです")

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmx_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmx_file_path)
        mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data, root_group)

        # 結果の検証
        # 1. グループが作成されているか
        self.assertTrue(
            cmds.objExists(mesh_group),
            f"メッシュグループ {mesh_group} が作成されていません",
        )

        # 2. グループの中にメッシュが作成されているか
        children = cmds.listRelatives(mesh_group, children=True)
        self.assertIsNotNone(children, f"メッシュグループ {mesh_group} の中にメッシュがありません")

        # 3. マテリアルが作成されているか
        materials = cmds.ls(materials=True)
        self.assertTrue(len(materials) > 0, "マテリアルが作成されていません")

        # 4. テクスチャが割り当てられているか
        # テクスチャノードをチェック
        # texture_nodes = cmds.ls(textures=True)
        # if len(pmx_data.textures) > 0:  # テクスチャがある場合のみチェック
        #     self.assertEqual(len(texture_nodes), len(pmx_data.textures), "テクスチャが作成されていません")

        # 5. UVが正しく設定されているか
        for child in children:
            if cmds.nodeType(child) == "mesh" or (
                cmds.listRelatives(child, shapes=True) and cmds.nodeType(cmds.listRelatives(child, shapes=True)[0]) == "mesh"
            ):
                uv_sets = cmds.polyUVSet(child, query=True, allUVSets=True)
                self.assertIsNotNone(uv_sets, f"{child} にUVセットがありません")
                self.assertGreaterEqual(len(uv_sets), 1, f"{child} には少なくとも1つのUVセットが必要です")

    def test_uv_seam_duplicates_are_welded_before_mesh_creation(self):
        """Merge safe UV-split source vertices while retaining corner UV IDs."""
        pmx_file_path = self.fixture_provider.get_pmx_file("mmt_test_model")
        pmx_data = parse_pmx_file(pmx_file_path)
        root_group = cmds.group(empty=True, name="test_uv_weld_root")

        converter = MeshConverter(pmx_file_path)
        _mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data, root_group)
        self.assertLess(
            int(cmds.polyEvaluate(mesh_name, vertex=True)),
            len(pmx_data.vertices),
            "UV-split duplicate source vertices were not welded",
        )
        self.assertEqual(
            int(cmds.polyEvaluate(mesh_name, face=True)),
            len(pmx_data.faces),
            "UV weld changed the imported polygon count",
        )
        self.assertGreater(converter.profile["uv_welded_vertex_count"], 0)

        selection = om.MSelectionList()
        selection.add(mesh_name)
        mesh_path = selection.getDagPath(0)
        mesh_path.extendToShape()
        mesh_fn = om.MFnMesh(mesh_path)
        uv_counts, uv_ids = mesh_fn.getAssignedUVs()
        self.assertEqual(len(uv_counts), len(pmx_data.faces))
        self.assertEqual(len(uv_ids), len(pmx_data.faces) * 3)

    def test_material_custom_attributes_on_pmx(self):
        """
        PMXマテリアルにカスタムアトリビュートが正しく設定されることをテストする。
        StandaloneモードだとDx11Shaderがでテスト出来ないので、StandardSurfaceでテストする。
        """
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_file_path = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        pmx_data = parse_pmx_file(pmx_file_path)

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmx_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmx_file_path)
        mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data, root_group)

        # メッシュに割り当てられているマテリアルを取得
        assigned_materials = maya_mesh_utils.get_materials_from_mesh(mesh_name)

        # 重複を除去し、mmd_material_indexを持つマテリアルのみを取得
        unique_materials = []
        seen = set()
        for material in assigned_materials:
            if material not in seen and cmds.attributeQuery("mmd_material_index", node=material, exists=True):
                unique_materials.append(material)
                seen.add(material)

        self.assertGreater(
            len(unique_materials),
            0,
            "メッシュに割り当てられたマテリアルが見つかりません",
        )

        # 各マテリアルのカスタムアトリビュートを確認
        for material in unique_materials:
            # マテリアルインデックスを取得
            material_index = maya_attribute_utils.get_attribute(material, ATTR_MMD_MATERIAL_INDEX)
            self.assertIsNotNone(
                material_index,
                f"{material}にmmd_material_indexアトリビュートが存在しません",
            )

            pmx_material = pmx_data.materials[material_index]

            with self.subTest(material=material, index=material_index):
                # PMXマテリアル特有のアトリビュート確認
                # material_nameアトリビュート
                # チェック対象の属性とpmx_materialの対応辞書
                attr_map = {
                    ATTR_MMD_MATERIAL_NAME: "name",
                    ATTR_MMD_MATERIAL_NAME_EN: "name_english",
                    ATTR_MMD_MEMO: "memo",
                    ATTR_MMD_DRAW_FLAGS: "draw_flag",
                    ATTR_MMD_EDGE_SIZE: "edge_size",
                    ATTR_MMD_TEXTURE_INDEX: "texture_index",
                    ATTR_MMD_SPHERE_TEXTURE_INDEX: "sphere_texture_index",
                    ATTR_MMD_SPHERE_MODE: "sphere_mode",
                    ATTR_MMD_SHARED_TOON_FLAG: "shared_toon_flag",
                    ATTR_MMD_TOON_TEXTURE_INDEX: "toon_texture_index",
                }

                for maya_attr, pmx_attr in attr_map.items():
                    self.assertEqual(
                        maya_attribute_utils.get_attribute(material, maya_attr),
                        getattr(pmx_material, pmx_attr),
                        f"{material}の{maya_attr}がpmx_material.{pmx_attr}と一致しません",
                    )

            # 色はRGBで返されるため、RGBAはRGBになる
            attr_map = {
                # Fileノードにつながってるので、テスト不可
                # "baseColor": "diffuse",
                "specularColor": "specular",
                ATTR_MMD_EDGE_COLOR: "edge_color",
            }

            for maya_attr, pmx_attr in attr_map.items():
                if cmds.attributeQuery(maya_attr, node=material, exists=True):
                    self.assertEqual(
                        maya_attribute_utils.get_attribute(material, maya_attr),
                        getattr(pmx_material, pmx_attr)[:3],
                        f"{material}の{maya_attr}がpmx_material.{pmx_attr}と一致しません",
                    )
                else:
                    self.fail(f"{material}に{maya_attr}アトリビュートが存在しません")

    def test_material_node_family_is_safe_unique_and_raw_names_are_preserved(self):
        """Each hazardous material name receives one deterministic node family."""
        converter = MeshConverter(str(self.fixture_provider.get_pmx_file("mmt_test_model")))
        texture_name = "diffuse.png"

        def material(name, index):
            return SimpleNamespace(
                get_name=lambda: name,
                name=name,
                name_english=f"{name}_en",
                material_index=index,
                diffuse=(0.8, 0.7, 0.6, 1.0),
                ambient=(0.1, 0.1, 0.1),
                specular=(0.2, 0.2, 0.2),
                specular_coefficient=0.5,
                toon_texture_index=-1,
                sphere_mode=0,
                sphere_texture_index=-1,
                texture_index=0,
                draw_flag=0x1F,
                edge_color=(0.0, 0.0, 0.0, 1.0),
                edge_size=1.0,
                memo="",
                shared_toon_flag=1,
            )

        materials = [
            material("1:髪", 0),
            material("2:髪+", 1),
            material("a:b", 2),
            material("ab", 3),
            material("", 4),
        ]
        mesh = cmds.polyCube(name="material_family_mesh", constructionHistory=False)[0]
        shaders = [
            converter._create_material(
                item,
                texture_path=texture_name,
                all_textures=[texture_name],
                material_index=index,
                original_texture_path=texture_name,
            )
            for index, item in enumerate(materials)
        ]

        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        self.assertEqual(len(shaders), len(set(shaders)))
        self.assertTrue(all(identifier.fullmatch(shader.rsplit("|", 1)[-1]) for shader in shaders))
        for shader, source in zip(shaders, materials):
            self.assertEqual(cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_NAME}"), source.name)
            self.assertEqual(cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL_NAME_EN}"), source.name_english)
            maya_material_utils.assign_material(mesh, shader)
            self.assertTrue(cmds.objExists(f"{shader}SG"))

        file_nodes = cmds.ls(type="file") or []
        self.assertEqual(len(file_nodes), len(materials))
        self.assertTrue(all(identifier.fullmatch(node.rsplit("|", 1)[-1]) for node in file_nodes))
        self.assertEqual(len(set(file_nodes)), len(file_nodes))
        cmds.delete(mesh)

    def test_ensure_mmd_shader_uniform_attributes_fallback_four_component_colors(self):
        """
        standalone fallback path で DiffuseColor と EdgeColor が4成分 compound 属性として作成されることを確認する。
        """
        shader_node = cmds.createNode("network", name="uniform_fallback_test")

        mesh_converter_module._ensure_mmd_shader_uniform_attributes(shader_node)

        for attr in ("DiffuseColor", "EdgeColor"):
            self.assertTrue(
                cmds.attributeQuery(attr, node=shader_node, exists=True),
                f"{attr} が作成されていません",
            )
            children = cmds.attributeQuery(attr, node=shader_node, listChildren=True)
            self.assertEqual(
                children,
                [f"{attr}0", f"{attr}1", f"{attr}2", f"{attr}3"],
            )

        self.assertTrue(cmds.attributeQuery("SpecularColor", node=shader_node, exists=True))
