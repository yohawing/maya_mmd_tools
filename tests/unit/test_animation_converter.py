import os

from ..common.test_base import TestBase
from ...src.converters import animation_converter

class TestAnimationConverter(TestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    def test_convert_vmd_animation(self):
        """VMDアニメーションがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのVMDデータを作成し、animation_converter.convert_vmd_animationを呼び出す。
        # TODO: Mayaシーン内に期待されるキーフレームアニメーションが作成され、その属性が正しいことをアサートする。
        pass
