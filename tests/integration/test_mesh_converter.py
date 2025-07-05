import os

from maya import cmds

from mmd_tools import settings
from mmd_tools.converters import MeshConverter, mesh_converter
from mmd_tools.core import maya_utils, pmd_parser, pmx_parser
from tests.common.maya_test_base import MayaTestBase


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

        # テストデータのパスを設定
        self.test_data_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data"
        )
        self.pmd_file_path = os.path.join(self.test_data_dir, "miku_v2.pmd")
        self.pmx_file_path = os.path.join(self.test_data_dir, "Lumine", "荧.pmx")

    def tearDown(self):
        """
        各テスト後のクリーンアップ処理。
        テスト中に作成されたノードやシーンの状態をリセット。
        """
        super().tearDown()
        # シーンをクリア
        cmds.file(new=True, force=True)

    def test_convert_pmd_mesh(self):
        """
        PMDメッシュがMayaに正しく変換されることをテストする。
        実際のPMDファイルを読み込み、変換処理を実行し、結果を検証する。
        """
        # PMDファイルが存在するか確認
        self.assertTrue(
            os.path.exists(self.pmd_file_path),
            f"テストPMDファイルが見つかりません: {self.pmd_file_path}",
        )

        # PMDファイルをパース
        parser = pmd_parser.PmdParser()
        pmd_data = parser.parse_file(self.pmd_file_path)

        # モデル名を取得
        model_name = pmd_data.header.model_name
        self.assertIsNotNone(model_name, "モデル名がNoneです")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(self.pmd_file_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data)

        settings.get("import.model.separate_meshes_by_material")

        # 結果の検証
        # 1. グループが作成されているか
        self.assertTrue(
            cmds.objExists(mesh_group),
            f"メッシュグループ {mesh_group} が作成されていません",
        )

        # 2. グループの中にメッシュが作成されているか
        children = cmds.listRelatives(mesh_group, children=True)
        self.assertIsNotNone(
            children, f"メッシュグループ {mesh_group} の中にメッシュがありません"
        )

        # 3. マテリアルが作成されているか
        materials = cmds.ls(materials=True)
        self.assertTrue(len(materials) > 0, "マテリアルが作成されていません")

        # 4. UVが正しく設定されているか
        for child in children:
            if (
                cmds.nodeType(child) == "mesh"
                or cmds.nodeType(cmds.listRelatives(child, shapes=True)[0]) == "mesh"
            ):
                uv_sets = cmds.polyUVSet(child, query=True, allUVSets=True)
                self.assertIsNotNone(uv_sets, f"{child} にUVセットがありません")
                self.assertGreaterEqual(
                    len(uv_sets), 1, f"{child} には少なくとも1つのUVセットが必要です"
                )

    def test_convert_pmx_mesh(self):
        """
        PMXメッシュがMayaに正しく変換されることをテストする。
        実際のPMXファイルを読み込み、変換処理を実行し、結果を検証する。
        """
        # PMXファイルが存在するか確認
        self.assertTrue(
            os.path.exists(self.pmx_file_path),
            f"テストPMXファイルが見つかりません: {self.pmx_file_path}",
        )

        # PMXファイルをパース
        parser = pmx_parser.PmxParser()
        pmx_data = parser.parse_file(self.pmx_file_path)

        # モデル名を取得
        model_name = pmx_data.header.model_name
        self.assertIsNotNone(model_name, "モデル名がNoneです")

        # MeshConverterを作成して変換を実行
        converter = MeshConverter(self.pmx_file_path)
        mesh_group, mesh_name = converter.convert_pmx_mesh(pmx_data)

        # 結果の検証
        # 1. グループが作成されているか
        self.assertTrue(
            cmds.objExists(mesh_group),
            f"メッシュグループ {mesh_group} が作成されていません",
        )

        # 2. グループの中にメッシュが作成されているか
        children = cmds.listRelatives(mesh_group, children=True)
        self.assertIsNotNone(
            children, f"メッシュグループ {mesh_group} の中にメッシュがありません"
        )

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
                cmds.listRelatives(child, shapes=True)
                and cmds.nodeType(cmds.listRelatives(child, shapes=True)[0]) == "mesh"
            ):
                uv_sets = cmds.polyUVSet(child, query=True, allUVSets=True)
                self.assertIsNotNone(uv_sets, f"{child} にUVセットがありません")
                self.assertGreaterEqual(
                    len(uv_sets), 1, f"{child} には少なくとも1つのUVセットが必要です"
                )

    # def test_separated_by_material(self):
    #     """
    #     マテリアルごとにメッシュが分割されるオプションが正しく機能するかテストする。
    #     """
    #     # 設定を一時的にオーバーライドしてマテリアルごとに分割するように設定
    #     original_setting = settings.get("import.model.separate_meshes_by_material", False)
    #     settings.set("import.model.separate_meshes_by_material", True)

    #     try:
    #         # PMXファイルをパース
    #         parser = pmx_parser.PmxParser()
    #         pmx_data = parser.parse_file(self.pmx_file_path)

    #         # 変換を実行
    #         converter = MeshConverter(self.pmx_file_path)
    #         mesh_group = converter.convert_pmx_mesh(pmx_data)

    #         # マテリアル数と同じ数のメッシュが作成されていることを確認
    #         children = cmds.listRelatives(mesh_group, children=True, type="transform")
    #         material_count = len([mat for mat in pmx_data.materials if mat.face_count > 0])

    #         # 注: マテリアルによっては面を持たない場合があるため、実際のメッシュ数は
    #         # 面を持つマテリアルの数と一致する必要がある
    #         self.assertEqual(len(children), material_count,
    #                         f"マテリアル数 {material_count} と一致するメッシュが作成されるべきですが、{len(children)} が作成されました")
    #     finally:
    #         # 設定を元に戻す
    #         maya_utils.settings.set("import.model.separate_meshes_by_material", original_setting)
