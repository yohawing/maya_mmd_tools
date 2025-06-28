import os

from tests.common.test_base import TestBase
from mmd_tools.converters import mesh_converter

class TestMeshConverter(TestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    def test_convert_pmd_mesh(self):
        """PMDメッシュがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMDデータを作成し、mesh_converter.convert_pmd_meshを呼び出す。
        # TODO: Mayaシーン内に期待されるメッシュノードが作成され、その属性が正しいことをアサートする。
        pass

    def test_convert_pmx_mesh(self):
        """PMXメッシュがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMXデータを作成し、mesh_converter.convert_pmx_meshを呼び出す。
        # TODO: Mayaシーン内に期待されるメッシュノードが作成され、その属性が正しいことをアサートする。
        pass
