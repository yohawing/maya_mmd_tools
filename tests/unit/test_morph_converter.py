import os

from ..common.test_base import TestBase
from ...src.converters import morph_converter

class TestMorphConverter(TestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    def test_convert_pmd_morphs(self):
        """PMDモーフがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMDデータとメッシュノードを作成し、morph_converter.convert_pmd_morphsを呼び出す。
        # TODO: Mayaシーン内に期待されるブレンドシェイプノードが作成され、モーフターゲットが正しいことをアサートする。
        pass

    def test_convert_pmx_morphs(self):
        """PMXモーフがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMXデータとメッシュノードを作成し、morph_converter.convert_pmx_morphsを呼び出す。
        # TODO: Mayaシーン内に期待されるブレンドシェイプノードが作成され、モーフターゲットが正しいことをアサートする。
        pass
