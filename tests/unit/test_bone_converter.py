import os

from ..common.test_base import TestBase
from ...src.converters import bone_converter

class TestBoneConverter(TestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    def test_convert_pmd_bones(self):
        """PMDボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # TODO: ダミーのPMDデータとメッシュノードを作成し、bone_converter.convert_pmd_bonesを呼び出す。
        # TODO: Mayaシーン内に期待されるジョイントノードが作成され、階層とスキニングが正しいことをアサートする。
        pass

    def test_convert_pmx_bones(self):
        """PMXボーンがMayaに正しく変換され、スキニングが適用されることをテストする。"""
        # TODO: ダミーのPMXデータとメッシュノードを作成し、bone_converter.convert_pmx_bonesを呼び出す。
        # TODO: Mayaシーン内に期待されるジョイントノードが作成され、階層とスキニングが正しいことをアサートする。
        pass
