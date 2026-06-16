import os
import unittest

from tests.common.maya_test_base import MayaTestBase

# from mmd_tools.io import export_vmd_file


class TestVmdExporter(MayaTestBase):
    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備
        self.output_file = os.path.join(self.temp_dir, "exported_motion.vmd")

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ
        if os.path.exists(self.output_file):
            os.remove(self.output_file)

    @unittest.skip("VMDエクスポーター未実装: export_vmd_file の実装後に有効化する")
    def test_export_vmd_animation(self):
        """MayaシーンからVMDアニメーションが正しくエクスポートされることをテストする。"""
        # TODO: Mayaシーンにダミーのボーンアニメーション、ブレンドシェイプアニメーション、カメラ、照明などを設定する。
        # TODO: export_vmd_fileを呼び出す。
        # TODO: エクスポートされたVMDファイルが存在し、その内容が期待通りであることをアサートする。
        pass
