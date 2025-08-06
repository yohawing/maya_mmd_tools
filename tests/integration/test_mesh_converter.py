from maya import cmds

from mmd_tools.core import pmd_data
from mmd_tools.core import pmx_data
from mmd_tools.core.settings import settings
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
        parser = pmd_data.PmdData()
        pmd_data = parser.parse_file(pmd_file_path)

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
        parser = pmx_data.PmxData()
        pmx_data = parser.parse_file(pmx_file_path)

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
        parser = pmd_data.PmdData()
        pmd_data = parser.parse_file(pmd_file_path)

        # ルートグループを作成
        root_group = cmds.group(empty=True, name="test_pmd_root")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(pmd_file_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data, root_group)

        # メッシュに割り当てられているマテリアルを取得
        mesh_shapes = cmds.listRelatives(mesh_name, shapes=True, type="mesh") or []
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
        parser = pmx_data.PmxData()
        pmx_data = parser.parse_file(pmx_file_path)

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

    # def test_separated_by_material(self):
    #     """
    #     マテリアルごとにメッシュが分割されるオプションが正しく機能するかテストする。
    #     """
