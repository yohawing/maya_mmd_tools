import unittest

from tests.common.maya_test_base import MayaTestBase


class TestAnimationConverter(MayaTestBase):
    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ

    @unittest.skip("AnimationConverter未実装: animation_converter.convert_vmd_animation の実装後に有効化する")
    def test_convert_vmd_animation(self):
        """VMDアニメーションがMayaに正しく変換されることをテストする。"""
        # TODO: ダミーのVMDデータを作成し、animation_converter.convert_vmd_animationを呼び出す。
        # TODO: Mayaシーン内に期待されるキーフレームアニメーションが作成され、その属性が正しいことをアサートする。
        pass
