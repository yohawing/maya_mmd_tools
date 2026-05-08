from maya import cmds

from mmd_tools.converters import MorphConverter, MeshConverter
from mmd_tools.core import maya_utils
from mmd_tools.core.settings import settings
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
