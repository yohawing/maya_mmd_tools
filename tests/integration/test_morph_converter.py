from pathlib import Path

from maya import cmds

from mmd_tools.converters import MorphConverter, MeshConverter
from mmd_tools.core import maya_utils, pmd_parser, pmx_parser
from tests.common.maya_test_base import MayaTestBase


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

        # テストデータのパスを設定
        self.test_data_dir = Path(__file__).parent.parent / "data"
        self.pmd_file_path = self.test_data_dir / "miku_v2.pmd"
        self.pmx_file_path = self.test_data_dir / "Lumine" / "荧.pmx"

    def tearDown(self):
        """
        各テスト後のクリーンアップ処理。
        テスト中に作成されたノードやシーンの状態をリセット。
        """
        super().tearDown()
        # シーンをクリア
        cmds.file(new=True, force=True)

    def test_convert_pmd_morphs(self):
        """PMDモーフがMayaに正しく変換されることをテストする。"""
        # PMDファイルが存在するか確認
        self.assertTrue(
            self.pmd_file_path.exists(),
            f"テストPMDファイルが見つかりません: {self.pmd_file_path}",
        )

        # PMDファイルをパース
        parser = pmd_parser.PmdParser()
        pmd_data = parser.parse_file(str(self.pmd_file_path))

        # モーフデータが存在することを確認
        self.assertIsNotNone(pmd_data.morphs, "PMDデータにモーフがありません")

        if len(pmd_data.morphs) == 0:
            self.skipTest("PMDデータにモーフが含まれていません")

        # テスト用のメッシュを作成（簡単な四角形）
        converter = MeshConverter(self.pmd_file_path)
        mesh_group, mesh_name = converter.convert_pmd_mesh(pmd_data)

        # MorphConverterを作成して変換を実行（バリデーションをスキップ）
        morph_converter = MorphConverter()
        # テスト用の設定を適用 - バリデーションを無効化
        morph_converter.settings["validation_mode"] = "skip"
        result = morph_converter.convert_pmd_morphs(pmd_data, mesh_name)

        # 結果の検証
        self.assertIsNotNone(result, "モーフ変換の結果がNoneです")
        self.assertTrue(result.get("success", False), "モーフ変換が失敗しました")

        # 変換されたモーフ数をチェック
        morphs_converted = result.get("morphs_converted", 0)
        self.assertGreaterEqual(morphs_converted, 0, "変換されたモーフ数が負の値です")

        # 変換されたモーフ数のチェック
        self.assertEqual(
            morphs_converted,
            len(pmd_data.morphs),
            "変換されたモーフ数がPMDデータのモーフ数と一致しません",
        )

        # blendShapeノードのチェックは、実際にモーフが変換された場合のみ
        if morphs_converted > 0:
            blend_shape_nodes = result.get("blend_shape_nodes", [])
            self.assertGreater(
                len(blend_shape_nodes), 0, "blendShapeノードが作成されていません"
            )

    def test_convert_pmx_morphs(self):
        """PMXモーフがMayaに正しく変換されることをテストする。"""
        # PMXファイルが存在するか確認
        self.assertTrue(
            self.pmx_file_path.exists(),
            f"テストPMXファイルが見つかりません: {self.pmx_file_path}",
        )

        # PMXファイルをパース
        parser = pmx_parser.PmxParser()
        pmx_data = parser.parse_file(str(self.pmx_file_path))

        # モーフデータが存在することを確認
        self.assertIsNotNone(pmx_data.morphs, "PMXデータにモーフがありません")

        if len(pmx_data.morphs) == 0:
            self.skipTest("PMXデータにモーフが含まれていません")

        # メッシュを作成
        mesh_converter = MeshConverter(str(self.pmx_file_path))
        mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(pmx_data)

        # MorphConverterを作成して変換を実行（バリデーションをスキップ）
        morph_converter = MorphConverter()
        # テスト用の設定を適用 - バリデーションを無効化
        morph_converter.settings["validation_mode"] = "skip"
        result = morph_converter.convert_pmx_morphs(pmx_data, mesh_name)

        # 結果の検証
        self.assertIsNotNone(result, "モーフ変換の結果がNoneです")
        self.assertTrue(result.get("success", False), "モーフ変換が失敗しました")

        # 変換されたモーフ数をチェック
        morphs_converted = result.get("morphs_converted", 0)
        self.assertGreaterEqual(morphs_converted, 0, "変換されたモーフ数が負の値です")

        # 変換されたモーフ数のチェック
        self.assertEqual(
            morphs_converted,
            len(pmx_data.morphs),
            "変換されたモーフ数がPMXデータのモーフ数と一致しません",
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

        mesh_name = maya_utils.create_mesh_with_uvs(
            "test_mesh", vertices, face_counts, face_connects, uvs, face_uv_connects
        )

        return mesh_name
