"""ExportVmdActionのMaya非依存境界を検証するテスト。"""

import unittest

from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from mmd_tools.core.vmd_data import VmdData


class FakeVmdExporter:
    """Test double that records VMD export calls."""

    def __init__(self):
        self.calls = []

    def export_vmd_animation(self, file_path, animation_data):
        self.calls.append((file_path, animation_data))


class TestExportVmdAction(unittest.TestCase):
    """VMD export action の最小依存境界を検証する。"""

    def test_execute_exports_provided_animation_data(self):
        exporter = FakeVmdExporter()
        action = ExportVmdAction(exporter=exporter)
        vmd_data = VmdData()

        result = action.execute(
            ExportVmdRequest(file_path="out.vmd", options={"export_format": "vmd"}, animation_data=vmd_data)
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, "out.vmd")
        self.assertIsNone(result.error)
        self.assertEqual(exporter.calls, [("out.vmd", vmd_data)])

    def test_execute_uses_collector_when_animation_data_is_missing(self):
        exporter = FakeVmdExporter()
        collected = {"model_name": "CollectedModel"}
        options = {"target_model": "model_root"}
        action = ExportVmdAction(exporter=exporter, collector=lambda received: collected)

        result = action.execute(ExportVmdRequest(file_path="out.vmd", options=options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, "out.vmd")
        self.assertEqual(exporter.calls, [("out.vmd", collected)])

    def test_execute_reports_missing_collector_or_data(self):
        action = ExportVmdAction(exporter=FakeVmdExporter(), collector=None)

        result = action.execute(ExportVmdRequest(file_path="out.vmd", options={}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsInstance(result.error, ValueError)

    def test_execute_reports_exporter_error(self):
        class FailingExporter:
            def export_vmd_animation(self, file_path, animation_data):
                raise RuntimeError("boom")

        action = ExportVmdAction(exporter=FailingExporter())

        result = action.execute(
            ExportVmdRequest(file_path="out.vmd", options={}, animation_data={"model_name": "Model"})
        )

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsInstance(result.error, RuntimeError)


if __name__ == "__main__":
    unittest.main()
