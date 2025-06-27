import os

from tests.common.test_base import TestBase
from src.io import pmx_exporter

class TestPmxExporter(TestBase):

    def setUp(self):
        super().setUp()
        # TODO: テストに必要なMayaシーンのセットアップやダミーデータの準備
        self.output_file = os.path.join(self.temp_dir, "exported_model.pmx")

    def tearDown(self):
        super().tearDown()
        # TODO: テスト後にMayaシーンのクリーンアップ
        if os.path.exists(self.output_file):
            os.remove(self.output_file)

    def test_export_pmx_model(self):
        """MayaシーンからPMXモデルが正しくエクスポートされることをテストする。"""
        # TODO: Mayaシーンにダミーのメッシュ、ボーン、モーフ、物理演算、材質などを設定する。
        # TODO: pmx_exporter.export_pmx_modelを呼び出す。
        # TODO: エクスポートされたPMXファイルが存在し、その内容が期待通りであることをアサートする。
        pass
