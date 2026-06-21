"""ExportModelActionのMaya非依存の未実装契約を検証するテスト。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.export_model_action import (  # noqa: E402
    ExportModelAction,
    ExportModelRequest,
)


class TestExportModelAction(unittest.TestCase):
    """PMX/PMD model export action の最小依存境界を検証する。"""

    def test_execute_reports_not_implemented_without_exporter(self):
        options = {"file_path": "out.pmx", "export_format": "pmx", "apply_scale": True}
        action = ExportModelAction()

        result = action.execute(ExportModelRequest(file_path="out.pmx", options=options))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsNone(result.error)
        self.assertEqual(
            result.status_message,
            "PMX export is not implemented yet (scene data collection is unsupported)",
        )

    def test_request_preserves_options_for_future_exporter_boundary(self):
        options = {"file_path": "out.pmx", "export_format": "pmx", "apply_scale": False}

        request = ExportModelRequest(file_path="out.pmx", options=options)

        self.assertIs(request.options, options)


if __name__ == "__main__":
    unittest.main()
