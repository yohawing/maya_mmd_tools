"""Import/export action の Maya 非依存の実行境界をまとめて検証する。"""

import unittest

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.export_model_action import (  # noqa: E402
    ExportModelAction,
    ExportModelRequest,
)
from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest  # noqa: E402
from mmd_tools.actions.import_model_action import (  # noqa: E402
    ImportModelAction,
    ImportModelRequest,
)
from mmd_tools.actions.import_vmd_action import (  # noqa: E402
    ImportVmdAction,
    ImportVmdRequest,
)
from mmd_tools.core.vmd_data import VmdData  # noqa: E402


class _FakeMayaAdapter:
    def __init__(self, calls):
        self.calls = calls

    def new_scene(self, force=True):
        self.calls.append(("adapter_new_scene", force))


class _ImportActionContract:
    """Import action 共通の orchestration contract。"""

    action_cls = None
    request_cls = None
    file_path = ""
    options = {}
    root_node = ""

    def _make_request(self, options=None, *, create_new_scene=False):
        return self.request_cls(
            self.file_path,
            dict(self.options if options is None else options),
            create_new_scene=create_new_scene,
        )

    def test_execute_calls_importer_with_file_path_and_options(self):
        calls = []
        options = dict(self.options)

        def importer(file_path, options=None):
            calls.append((file_path, options))
            return self.root_node

        action = self.action_cls(importer=importer, new_scene=lambda: None)
        result = action.execute(self.request_cls(self.file_path, options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.root_node, self.root_node)
        self.assertIsNone(result.error)
        self.assertEqual(calls, [(self.file_path, options)])
        self.assertIs(calls[0][1], options)

    def test_execute_forwards_progress_callback_when_present(self):
        calls = []
        options = dict(self.options)

        def progress_callback(_value):
            pass

        def importer(file_path, options=None, progress_callback=None):
            calls.append((file_path, options, progress_callback))
            return self.root_node

        action = self.action_cls(importer=importer, new_scene=lambda: None)
        request = self.request_cls(
            self.file_path,
            options,
            progress_callback=progress_callback,
        )
        result = action.execute(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, [(self.file_path, options, progress_callback)])

    def test_execute_calls_new_scene_before_import_when_requested(self):
        calls = []

        def new_scene():
            calls.append("new_scene")

        def importer(_file_path, options=None):
            calls.append("importer")
            return self.root_node

        action = self.action_cls(importer=importer, new_scene=new_scene)
        result = action.execute(self._make_request({}, create_new_scene=True))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["new_scene", "importer"])

    def test_execute_uses_adapter_new_scene_before_import_when_requested(self):
        calls = []

        def importer(_file_path, options=None):
            calls.append(("importer", options))
            return self.root_node

        action = self.action_cls(importer=importer, maya_adapter=_FakeMayaAdapter(calls))
        result = action.execute(self._make_request({}, create_new_scene=True))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, [("adapter_new_scene", True), ("importer", {})])

    def test_execute_prefers_explicit_new_scene_callable_over_adapter(self):
        calls = []

        def new_scene():
            calls.append("callable_new_scene")

        def importer(_file_path, options=None):
            calls.append("importer")
            return self.root_node

        action = self.action_cls(
            importer=importer,
            new_scene=new_scene,
            maya_adapter=_FakeMayaAdapter(calls),
        )
        result = action.execute(self._make_request({}, create_new_scene=True))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["callable_new_scene", "importer"])

    def test_execute_does_not_call_new_scene_when_not_requested(self):
        calls = []

        def new_scene():
            calls.append("new_scene")

        def importer(_file_path, options=None):
            calls.append("importer")
            return self.root_node

        action = self.action_cls(importer=importer, new_scene=new_scene)
        result = action.execute(self._make_request({}))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, ["importer"])

    def test_execute_returns_failure_when_importer_returns_none(self):
        action = self.action_cls(importer=lambda _path, options=None: None, new_scene=lambda: None)

        result = action.execute(self._make_request({}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIsNone(result.error)

    def test_execute_converts_importer_exception_to_result_error(self):
        error = RuntimeError("boom")

        def importer(_file_path, options=None):
            raise error

        action = self.action_cls(importer=importer, new_scene=lambda: None)
        result = action.execute(self._make_request({}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)

    def test_execute_converts_new_scene_exception_to_result_error(self):
        error = RuntimeError("new scene failed")

        def new_scene():
            raise error

        action = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=new_scene)
        result = action.execute(self._make_request({}, create_new_scene=True))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)

    def test_execute_returns_profile_warnings(self):
        warning = {"message": "partial import warning"}
        vmd_warning = {"message": "runtime fallback"}
        bone_warning = {"message": "bone warning"}
        rig_warning = {"message": "native rig fallback"}
        texture_issue = {"file_node": "file1"}
        nested_issue = {"file_node": "file2"}
        options = {
            "profile": {
                "warnings": [warning],
                "vmd_converter": {"warnings": [vmd_warning]},
                "bone_converter": {
                    "warnings": [bone_warning],
                    "rig_converter": {"warnings": [rig_warning]},
                },
                "texture_issues": [texture_issue],
                "mesh_converter": {"unresolved_textures": [nested_issue]},
            }
        }

        action = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None)
        result = action.execute(self._make_request(options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.warnings, [warning, vmd_warning, bone_warning, rig_warning, texture_issue, nested_issue])

    def test_result_warning_lists_are_not_shared(self):
        first = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None).execute(
            self._make_request({})
        )
        second = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None).execute(
            self._make_request({})
        )

        first.warnings.append("first")

        self.assertEqual(second.warnings, [])


class TestImportModelAction(_ImportActionContract, unittest.TestCase):
    """PMX/PMD model import action の依存境界を検証する。"""

    action_cls = ImportModelAction
    request_cls = ImportModelRequest
    file_path = "model.pmx"
    options = {"scale": 1.0}
    root_node = "root"


class TestImportVmdAction(_ImportActionContract, unittest.TestCase):
    """VMD import action の依存境界を検証する。"""

    action_cls = ImportVmdAction
    request_cls = ImportVmdRequest
    file_path = "motion.vmd"
    options = {"target_model": "model_root"}
    root_node = "motion_root"


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


class TestExportVmdAction(unittest.TestCase):
    """VMD export action の最小依存境界を検証する。"""

    def test_execute_exports_provided_animation_data(self):
        exporter = _FakeVmdExporter()
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
        exporter = _FakeVmdExporter()
        collected = {"model_name": "CollectedModel"}
        options = {"target_model": "model_root"}
        action = ExportVmdAction(exporter=exporter, collector=lambda received: collected)

        result = action.execute(ExportVmdRequest(file_path="out.vmd", options=options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.exported_path, "out.vmd")
        self.assertEqual(exporter.calls, [("out.vmd", collected)])

    def test_execute_reports_missing_collector_or_data(self):
        action = ExportVmdAction(exporter=_FakeVmdExporter(), collector=None)

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


class _FakeVmdExporter:
    """Test double that records VMD export calls."""

    def __init__(self):
        self.calls = []

    def export_vmd_animation(self, file_path, animation_data):
        self.calls.append((file_path, animation_data))


if __name__ == "__main__":
    unittest.main()
