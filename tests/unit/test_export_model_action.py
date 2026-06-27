"""ExportModelAction の Maya 非依存の実行境界を検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.export_model_action import (  # noqa: E402
    ExportModelAction,
    ExportModelRequest,
)


class TestExportModelAction(unittest.TestCase):
    """PMX/PMD model export action の最小依存境界を検証する。"""

    def test_execute_exports_pmx_from_model_data(self):
        model_data = {"vertices": [1], "faces": [[0, 1, 2]]}
        exporter = _FakePmxExporter()
        options = {"file_path": "out.pmx", "export_format": "pmx", "model_data": model_data}
        action = ExportModelAction(pmx_exporter=exporter, collector=None)

        result = action.execute(ExportModelRequest(file_path="out.pmx", options=options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, "out.pmx")
        self.assertIsNone(result.error)
        self.assertEqual(exporter.calls, [("out.pmx", model_data)])

    def test_execute_exports_pmd_from_collector_data(self):
        model_data = {"vertices": [1], "faces": [[0, 1, 2]]}
        exporter = _FakePmdExporter()
        options = {"file_path": "out.pmd", "export_format": "pmd"}
        action = ExportModelAction(pmd_exporter=exporter, collector=lambda received: model_data)

        result = action.execute(ExportModelRequest(file_path="out.pmd", options=options))

        self.assertTrue(result.succeeded)
        self.assertEqual(exporter.calls, [("out.pmd", model_data)])

    def test_execute_reports_missing_collector_or_data(self):
        options = {"file_path": "out.pmx", "export_format": "pmx"}
        action = ExportModelAction(pmx_exporter=_FakePmxExporter(), collector=None)

        result = action.execute(ExportModelRequest(file_path="out.pmx", options=options))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsInstance(result.error, ValueError)
        self.assertIn("Model export requires model_data or a collector", result.status_message)

    def test_execute_reports_unsupported_format(self):
        options = {"file_path": "out.obj", "export_format": "obj", "model_data": {"vertices": [1]}}
        action = ExportModelAction(collector=None)

        result = action.execute(ExportModelRequest(file_path="out.obj", options=options))

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ValueError)
        self.assertIn("Unsupported model export format: obj", result.status_message)

    def test_request_preserves_options_for_future_exporter_boundary(self):
        options = {"file_path": "out.pmx", "export_format": "pmx", "apply_scale": False}

        request = ExportModelRequest(file_path="out.pmx", options=options)

        self.assertIs(request.options, options)


class _FakePmxExporter:
    def __init__(self):
        self.calls = []

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))


class _FakePmdExporter:
    def __init__(self):
        self.calls = []

    def export_pmd_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))


if __name__ == "__main__":
    unittest.main()
