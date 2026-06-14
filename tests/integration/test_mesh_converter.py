import json

from maya import cmds

from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.pmd_data import PmdData
from mmd_tools.core.pmx_data.morph import PmxMorphType
from mmd_tools.core.settings import settings
from mmd_tools.converters import mesh_converter as mesh_converter_module
from mmd_tools.converters import MeshConverter
from mmd_tools.core import maya_utils
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
    ATTR_MMD_MORPH_GROUP_SPLIT_MESH,
    ATTR_MMD_SOURCE_VERTEX_INDICES,
    ATTR_MMD_VERTEX_MORPH_NAMES_JSON,
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
        settings.set("import.model.split_meshes_by_morph_groups", False)

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

    def test_convert_pmd_mesh(self):
        """
        PMDメッシュがMayaに正しく変換されることをテストする。
        実際のPMDファイルを読み込み、変換処理を実行し、結果を検証する。
        """
        # TestFixtureProviderからPMDファイルパスを取得
        pmd_file_path = self.fixture_provider.get_pmd_file("miku_v2")

        # PMDファイルをパース
        pmd_data = PmdData()
        pmd_data = pmd_data.parse_file(pmd_file_path)

        # モデル名を取得
        model_name = pmd_data.header.model_name
        self.assertIsNotNone(model_name, "モデル名がNoneです")

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmd_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmd_file_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data, root_group)

        settings.get("import.model.separate_meshes_by_material")

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

        # 4. UVが正しく設定されているか
        for child in children:
            if cmds.nodeType(child) == "mesh" or cmds.nodeType(cmds.listRelatives(child, shapes=True)[0]) == "mesh":
                uv_sets = cmds.polyUVSet(child, query=True, allUVSets=True)
                self.assertIsNotNone(uv_sets, f"{child} にUVセットがありません")
                self.assertGreaterEqual(len(uv_sets), 1, f"{child} には少なくとも1つのUVセットが必要です")

    def test_convert_pmx_mesh(self):
        """
        PMXメッシュがMayaに正しく変換されることをテストする。
        実際のPMXファイルを読み込み、変換処理を実行し、結果を検証する。
        """
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_file_path = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        pmx_data = PmxData()
        pmx_data = pmx_data.parse_file(pmx_file_path)

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

    def test_material_custom_attributes_on_pmd(self):
        """
        PMDマテリアルにカスタムアトリビュートが正しく設定されることをテストする。
        現在の実装では失敗することが期待される。
        """
        # TestFixtureProviderからPMDファイルパスを取得
        pmd_file_path = self.fixture_provider.get_pmd_file("miku_v2")

        # PMDファイルをパース
        pmd_data = PmdData()
        pmd_data = pmd_data.parse_file(pmd_file_path)

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmd_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmd_file_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data, root_group)

        # メッシュに割り当てられているマテリアルを取得
        cmds.listRelatives(mesh_name, shapes=True, type="mesh") or []
        assigned_materials = set()

        assigned_materials = maya_utils.get_materials_from_mesh(mesh_name)

        # 各マテリアルのカスタムアトリビュートを確認
        for i, material in enumerate(sorted(assigned_materials)):
            with self.subTest(material=material, index=i):
                # 標準のMayaマテリアルをスキップ（例：lambert1, standardSurface1など）
                if material in ["lambert1", "standardSurface1", "particleCloud1"]:
                    continue

                # mmd_materialアトリビュートの存在確認
                self.assertTrue(
                    cmds.attributeQuery("mmd_material", node=material, exists=True),
                    f"{material}にmmd_materialアトリビュートが存在しません",
                )

                # PMDマテリアル特有のアトリビュート確認
                # diffuse_colorアトリビュート
                self.assertTrue(
                    cmds.attributeQuery("diffuse_color", node=material, exists=True),
                    f"{material}にdiffuse_colorアトリビュートが存在しません",
                )

                # specular_colorアトリビュート
                self.assertTrue(
                    cmds.attributeQuery("specular_color", node=material, exists=True),
                    f"{material}にspecular_colorアトリビュートが存在しません",
                )

                # ambient_colorアトリビュート
                self.assertTrue(
                    cmds.attributeQuery("ambient_color", node=material, exists=True),
                    f"{material}にambient_colorアトリビュートが存在しません",
                )

                # shininessアトリビュート
                self.assertTrue(
                    cmds.attributeQuery("shininess", node=material, exists=True),
                    f"{material}にshininessアトリビュートが存在しません",
                )

                # edge_flagアトリビュート
                self.assertTrue(
                    cmds.attributeQuery("edge_flag", node=material, exists=True),
                    f"{material}にedge_flagアトリビュートが存在しません",
                )

    def test_material_custom_attributes_on_pmx(self):
        """
        PMXマテリアルにカスタムアトリビュートが正しく設定されることをテストする。
        StandaloneモードだとDx11Shaderがでテスト出来ないので、StandardSurfaceでテストする。
        """
        # TestFixtureProviderからPMXファイルパスを取得
        pmx_file_path = self.fixture_provider.get_pmx_file("mmt_test_model")

        # PMXファイルをパース
        pmx_data = PmxData()
        pmx_data = pmx_data.parse_file(pmx_file_path)

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmx_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmx_file_path)
        mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data, root_group)

        # メッシュに割り当てられているマテリアルを取得
        assigned_materials = maya_utils.get_materials_from_mesh(mesh_name)

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
            material_index = maya_utils.get_attribute(material, ATTR_MMD_MATERIAL_INDEX)
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
                        maya_utils.get_attribute(material, maya_attr),
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
                        maya_utils.get_attribute(material, maya_attr),
                        getattr(pmx_material, pmx_attr)[:3],
                        f"{material}の{maya_attr}がpmx_material.{pmx_attr}と一致しません",
                    )
                else:
                    self.fail(f"{material}に{maya_attr}アトリビュートが存在しません")

    def test_convert_pmx_mesh_with_material_split(self):
        """
        separate_meshes_by_material=True で PMX メッシュがマテリアルごとに分割されることをテストする。
        """
        # 設定を一時的に有効化
        settings.set("import.model.separate_meshes_by_material", True)

        try:
            # 複数マテリアルを持つPMX fixtureを使用
            pmx_file_path = self.fixture_provider.get_pmx_file("Lumine")

            # PMXファイルをパース
            pmx_data = PmxData()
            pmx_data = pmx_data.parse_file(pmx_file_path)

            # マテリアル数を記録
            expected_material_count = len(
                [m for m in pmx_data.materials if m.face_count > 0]
            )
            self.assertGreater(expected_material_count, 1, "split test には複数 material fixture が必要です")

            # ルートグループを作成
            root_group = cmds.group(empty=True, name="test_pmx_split_root")

            # MeshConverterを作成して変換を実行
            converter = MeshConverter(pmx_file_path)
            mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data, root_group)

            # 結果の検証: mesh_name がリストであることを確認
            self.assertIsInstance(
                mesh_name,
                list,
                f"split mode では mesh_name は list であるべき: {type(mesh_name)}",
            )

            # material 数と同数の mesh transform が GEOMETRY_GROUP 直下にある
            self.assertEqual(
                len(mesh_name),
                expected_material_count,
                f"メッシュ数 ({len(mesh_name)}) が material 数 ({expected_material_count}) と一致しません",
            )

            # 各 mesh に mesh shape / UV / material がある
            split_vertex_counts = []
            for mn in mesh_name:
                self.assertTrue(
                    cmds.objExists(mn),
                    f"メッシュ '{mn}' が存在しません",
                )

                # shape node がある
                shapes = cmds.listRelatives(mn, shapes=True, type="mesh") or []
                self.assertGreater(
                    len(shapes),
                    0,
                    f"'{mn}' に mesh shape がありません",
                )

                # UV がある
                uv_sets = cmds.polyUVSet(mn, query=True, allUVSets=True)
                self.assertIsNotNone(uv_sets, f"'{mn}' に UV がありません")
                self.assertGreaterEqual(
                    len(uv_sets),
                    1,
                    f"'{mn}' に UV セットがありません",
                )

                # マテリアルが割り当てられている
                materials = maya_utils.get_materials_from_mesh(mn)
                self.assertGreater(
                    len(materials),
                    0,
                    f"'{mn}' にマテリアルがありません",
                )

                self.assertTrue(
                    cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mn, exists=True),
                    f"'{mn}' に compact split source index attribute がありません",
                )
                source_indices = maya_utils.get_int_array_attribute(mn, ATTR_MMD_SOURCE_VERTEX_INDICES)
                vertex_count = cmds.polyEvaluate(mn, vertex=True)
                split_vertex_counts.append(vertex_count)
                self.assertEqual(
                    len(source_indices),
                    vertex_count,
                    f"'{mn}' の source index 数と local vertex 数が一致しません",
                )

            self.assertLess(
                min(split_vertex_counts),
                len(pmx_data.vertices),
                "compact split mesh が全頂点保持のままです",
            )

        finally:
            # 設定を元に戻す
            settings.set("import.model.separate_meshes_by_material", False)

    def test_convert_pmx_mesh_with_morph_group_split(self):
        """split_meshes_by_morph_groups=True で vertex morph 影響 material set ごとの mesh を作る。"""
        settings.set("import.model.split_meshes_by_morph_groups", True)

        class FakeHeader:
            def get_name(self):
                return "MorphGroupModel"

        class FakeVertex:
            def __init__(self, position, uv):
                self.position = position
                self.normal = (0.0, 0.0, 1.0)
                self.uv = uv

        class FakeFace:
            def __init__(self, indices):
                self.indices = indices

        class FakeMaterial:
            def __init__(self, index, face_count):
                self.material_index = index
                self.name = f"mat_{index}"
                self.name_english = f"mat_{index}_en"
                self.face_count = face_count
                self.diffuse = (0.8, 0.7, 0.6, 1.0)
                self.ambient = (0.1, 0.1, 0.1)
                self.specular = (0.2, 0.2, 0.2)
                self.specular_coefficient = 0.5
                self.texture_index = -1
                self.sphere_texture_index = -1
                self.sphere_mode = 0
                self.toon_texture_index = -1
                self.shared_toon_flag = 0
                self.draw_flag = 0
                self.edge_color = (0.0, 0.0, 0.0, 1.0)
                self.edge_size = 1.0
                self.memo = ""

            def get_name(self):
                return self.name

        class FakeVertexMorph:
            morph_type = PmxMorphType.VertexMorph

            def __init__(self, name, vertex_index):
                self.name = name
                self.offsets = [{"vertex_index": vertex_index, "position_offset": (0.1, 0.0, 0.0)}]

            def get_name(self):
                return self.name

        fake_data = type(
            "FakePmxData",
            (),
            {
                "header": FakeHeader(),
                "vertices": [
                    FakeVertex((0.0, 0.0, 0.0), (0.0, 0.0)),
                    FakeVertex((1.0, 0.0, 0.0), (1.0, 0.0)),
                    FakeVertex((1.0, 1.0, 0.0), (1.0, 1.0)),
                    FakeVertex((0.0, 1.0, 0.0), (0.0, 1.0)),
                    FakeVertex((0.5, 1.5, 0.0), (0.5, 1.0)),
                ],
                "faces": [FakeFace([0, 1, 2]), FakeFace([2, 3, 4]), FakeFace([0, 2, 4])],
                "materials": [FakeMaterial(0, 3), FakeMaterial(1, 3), FakeMaterial(2, 3)],
                "textures": [],
                "morphs": [
                    FakeVertexMorph("mat0_morph", 1),
                    FakeVertexMorph("mat1_morph", 3),
                    FakeVertexMorph("mat12_morph", 4),
                ],
            },
        )()

        try:
            root_group = cmds.group(empty=True, name="test_pmx_morph_group_root")
            converter = MeshConverter("")
            _mesh_group, mesh_names = converter.convert_pmx_mesh(fake_data, root_group)

            self.assertIsInstance(mesh_names, list)
            self.assertEqual(len(mesh_names), 3)
            self.assertEqual(converter.profile["created_mesh_count"], 3)

            morph_name_sets = []
            for mesh_name in mesh_names:
                self.assertTrue(cmds.attributeQuery(ATTR_MMD_MORPH_GROUP_SPLIT_MESH, node=mesh_name, exists=True))
                self.assertTrue(cmds.getAttr(f"{mesh_name}.{ATTR_MMD_MORPH_GROUP_SPLIT_MESH}"))
                source_indices = maya_utils.get_int_array_attribute(mesh_name, ATTR_MMD_SOURCE_VERTEX_INDICES)
                self.assertEqual(len(source_indices), cmds.polyEvaluate(mesh_name, vertex=True))
                raw_names = cmds.getAttr(f"{mesh_name}.{ATTR_MMD_VERTEX_MORPH_NAMES_JSON}")
                morph_name_sets.append(tuple(json.loads(raw_names)))

            self.assertIn(("mat0_morph",), morph_name_sets)
            self.assertIn(("mat1_morph",), morph_name_sets)
            self.assertIn(("mat12_morph",), morph_name_sets)
        finally:
            settings.set("import.model.split_meshes_by_morph_groups", False)

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
