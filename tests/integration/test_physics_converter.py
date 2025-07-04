import os

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.converters import physics_converter

class TestPhysicsConverter(MayaTestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    def test_convert_pmd_physics(self):
        """PMD物理演算がMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMDデータを作成し、physics_converter.convert_pmd_physicsを呼び出す。
        # TODO: Mayaシーン内に期待されるリジッドボディとコンストレインが作成され、その属性が正しいことをアサートする。
        pass

    def test_convert_pmx_physics(self):
        """PMX物理演算がMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのPMXデータを作成し、physics_converter.convert_pmx_physicsを呼び出す。
        # TODO: Mayaシーン内に期待されるリジッドボディとコンストレインが作成され、その属性が正しいことをアサートする。
        pass
