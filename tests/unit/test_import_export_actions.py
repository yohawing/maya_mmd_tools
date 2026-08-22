"""Import/export action の Maya 非依存の実行境界をまとめて検証する。"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.actions.export_model_action import (  # noqa: E402
    ExportModelAction,
    ExportModelRequest,
    _default_collect_model_data,
)
from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest  # noqa: E402
from mmd_tools.actions.import_model_action import (  # noqa: E402
    OUTCOME_FATAL,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    ImportModelAction,
    ImportModelRequest,
)
from mmd_tools.actions.import_vmd_action import (  # noqa: E402
    ImportVmdAction,
    ImportVmdRequest,
)
from mmd_tools.core.vmd_data import VmdData  # noqa: E402
from mmd_tools.converters.authoring_export_bridge import (  # noqa: E402
    AuthoringExportIntegrationError,
)
from mmd_tools.validation.export_validator import ExportValidationError  # noqa: E402


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
        self.assertEqual(result.outcome, OUTCOME_SUCCESS)
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
        options = {"scene_animation_only": True} if self.action_cls is ImportVmdAction else {}
        request = self._make_request(options, create_new_scene=True)
        result = action.execute(request)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            calls,
            [("adapter_new_scene", True), ("importer", request.options)],
        )

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
        options = {"scene_animation_only": True} if self.action_cls is ImportVmdAction else {}
        result = action.execute(self._make_request(options, create_new_scene=True))

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
        self.assertEqual(result.outcome, OUTCOME_FATAL)
        self.assertIsNone(result.root_node)
        self.assertIsNone(result.error)

    def test_execute_converts_importer_exception_to_result_error(self):
        error = RuntimeError("boom")

        def importer(_file_path, options=None):
            raise error

        action = self.action_cls(importer=importer, new_scene=lambda: None)
        result = action.execute(self._make_request({}))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.outcome, OUTCOME_FATAL)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)

    def test_execute_converts_new_scene_exception_to_result_error(self):
        error = RuntimeError("new scene failed")

        def new_scene():
            raise error

        action = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=new_scene)
        options = {"scene_animation_only": True} if self.action_cls is ImportVmdAction else {}
        result = action.execute(self._make_request(options, create_new_scene=True))

        self.assertFalse(result.succeeded)
        self.assertEqual(result.outcome, OUTCOME_FATAL)
        self.assertIsNone(result.root_node)
        self.assertIs(result.error, error)

    def test_execute_returns_profile_warnings(self):
        warning = {"message": "partial import warning"}
        vmd_warning = {"message": "runtime fallback"}
        bone_warning = {"message": "bone warning"}
        rig_warning = {"message": "native rig fallback"}
        bone_morph_warning = {"code": "node_type_unavailable", "reason": "node_type_unavailable"}
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
                "bone_morph_runtime": {"warnings": [bone_morph_warning]},
                "texture_issues": [texture_issue],
                "mesh_converter": {"unresolved_textures": [nested_issue]},
            }
        }

        action = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None)
        result = action.execute(self._make_request(options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.outcome, OUTCOME_PARTIAL)
        self.assertEqual(
            result.warnings,
            [
                warning,
                vmd_warning,
                bone_warning,
                rig_warning,
                bone_morph_warning,
                texture_issue,
                nested_issue,
            ],
        )

    def test_execute_classifies_bone_morph_node_type_unavailable_as_partial(self):
        bone_morph_warning = {
            "code": "node_type_unavailable",
            "reason": "node_type_unavailable",
            "node_type": "mmdBoneMorphAccum",
        }
        options = {
            "profile": {
                "bone_morph_runtime": {
                    "success": False,
                    "skipped": ["node_type_unavailable"],
                    "warnings": [bone_morph_warning],
                }
            }
        }

        action = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None)
        result = action.execute(self._make_request(options))

        self.assertTrue(result.succeeded)
        self.assertEqual(result.outcome, OUTCOME_PARTIAL)
        self.assertEqual(result.root_node, self.root_node)
        self.assertEqual(result.warnings, [bone_morph_warning])
        self.assertEqual(result.warnings[0]["code"], "node_type_unavailable")

    def test_result_warning_lists_are_not_shared(self):
        first = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None).execute(
            self._make_request({})
        )
        second = self.action_cls(importer=lambda _path, options=None: self.root_node, new_scene=lambda: None).execute(
            self._make_request({})
        )

        first.warnings.append("first")

        self.assertEqual(second.warnings, [])
        self.assertEqual(first.outcome, OUTCOME_SUCCESS)
        self.assertEqual(second.outcome, OUTCOME_SUCCESS)


class TestImportModelAction(_ImportActionContract, unittest.TestCase):
    """PMX/PMD model import action の依存境界を検証する。"""

    action_cls = ImportModelAction
    request_cls = ImportModelRequest
    file_path = "model.pmx"
    options = {"scale": 1.0}
    root_node = "root"

    def test_execute_forwards_policy_scale_from_options_for_pmx(self):
        """Presenter/Settings が組み立てた scale を importer へそのまま渡す。"""
        calls = []
        options = {"scale": 1.0, "use_namespace": False}

        def importer(file_path, options=None):
            calls.append((file_path, dict(options or {})))
            return self.root_node

        action = ImportModelAction(importer=importer, new_scene=lambda: None)
        result = action.execute(ImportModelRequest("model.pmx", options))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, [("model.pmx", options)])
        self.assertEqual(calls[0][1]["scale"], 1.0)

    def test_execute_forwards_dev_scale_from_options_for_pmd(self):
        """PMD 経路でも options.scale（dev の永続値を含む）を上書きしない。"""
        calls = []
        options = {"scale": 2.5}

        def importer(file_path, options=None):
            calls.append((file_path, dict(options or {})))
            return self.root_node

        action = ImportModelAction(importer=importer, new_scene=lambda: None)
        result = action.execute(ImportModelRequest("model.pmd", options))

        self.assertTrue(result.succeeded)
        self.assertEqual(calls, [("model.pmd", options)])
        self.assertEqual(calls[0][1]["scale"], 2.5)


class TestImportVmdAction(_ImportActionContract, unittest.TestCase):
    """VMD import action の依存境界を検証する。"""

    action_cls = ImportVmdAction
    request_cls = ImportVmdRequest
    file_path = "motion.vmd"
    options = {"target_model": "model_root"}
    root_node = "motion_root"

    def _make_request(self, options=None, *, create_new_scene=False):
        resolved = dict(options or {})
        if not resolved.get("scene_animation_only"):
            resolved.setdefault("target_model", "model_root")
        return super()._make_request(resolved, create_new_scene=create_new_scene)


class TestExportModelAction(unittest.TestCase):
    """PMX model export action の最小依存境界を検証する。"""

    def test_execute_exports_pmx_from_model_data(self):
        model_data = {
            "vertices": [{"position": [0.0, 0.0, 0.0]}],
            "faces": [[0, 0, 0]],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "out.pmx"
            exporter = _FakePmxExporter()
            options = {
                "file_path": str(output_path),
                "export_format": "pmx",
                "model_data": model_data,
            }
            action = ExportModelAction(
                pmx_exporter=exporter,
                collector=None,
                output_verifier=None,
            )

            result = action.execute(ExportModelRequest(file_path=str(output_path), options=options))

            self.assertTrue(result.succeeded)
            self.assertEqual(result.exported_path, str(output_path))
            self.assertIsNone(result.error)
            self.assertEqual(output_path.read_bytes(), b"fake pmx bytes")
            writer_path = Path(exporter.calls[0][0])
            self.assertEqual(writer_path.parent, output_path.parent)
            self.assertEqual(writer_path.suffix, ".pmx")
            self.assertNotEqual(writer_path, output_path)
            self.assertFalse(writer_path.exists())

    def test_blocking_model_data_skips_injected_writer_without_maya_defaults(self):
        """Injected plain-Python writers remain usable on a blocking payload."""
        model_data = {
            "vertices": [{"position": [0.0, 0.0, 0.0], "bone_indices": [0]}],
            "faces": [[0, 0, 0]],
            "materials": [{"name": "mat", "face_count": 3}],
            "bones": None,
            "morphs": [{"type": "flip", "offsets": []}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "blocked.pmx"
            exporter = _FakePmxExporter()
            action = ExportModelAction(
                pmx_exporter=exporter,
                collector=None,
                output_verifier=None,
            )

            result = action.execute(
                ExportModelRequest(
                    file_path=str(output_path),
                    options={"export_format": "pmx", "model_data": model_data},
                )
            )

            self.assertFalse(result.succeeded)
            self.assertIsInstance(result.error, ExportValidationError)
            self.assertTrue(result.validation_report.is_blocking)
            self.assertEqual(exporter.calls, [])
            self.assertFalse(output_path.exists())

    def test_default_collector_projects_authoring_spec_for_registry_root(self):
        oracle = {"vertices": [{"position": [0, 0, 0]}], "faces": [[0, 0, 0]], "marker": "oracle"}
        calls = []

        class FakeCollector:
            def collect(self, options):
                calls.append(("collect", options))
                return oracle

        class FakeAdapter:
            def attribute_exists(self, attr, node):
                return attr == "mmd_model_registry" and node == "|root"

            def list_relatives(self, node, **kwargs):
                return []

        class FakeBackend:
            def __init__(self, adapter):
                calls.append(("backend", adapter))

        class FakeSceneAdapter:
            def __init__(self, backend):
                calls.append(("reader", backend))

            def read_spec(self, root):
                calls.append(("read", root))
                return "spec"

        def bridge(spec, payload):
            calls.append(("bridge", spec, payload))
            return {"projected": True}

        with (
            mock.patch("mmd_tools.converters.export_scene_collector.ExportSceneCollector", FakeCollector),
            mock.patch("mmd_tools.adapters.maya_cmds_adapter.MayaCmdsAdapter", FakeAdapter),
            mock.patch("mmd_tools.adapters.maya_scene_metadata_backend.MayaSceneMetadataBackend", FakeBackend),
            mock.patch("mmd_tools.adapters.scene_metadata_adapter.SceneMetadataAdapter", FakeSceneAdapter),
            mock.patch("mmd_tools.converters.authoring_export_bridge.project_authoring_spec", bridge),
        ):
            projected = _default_collect_model_data(
                {"target_model": "|root", "export_format": "pmx"}
            )

        self.assertEqual(projected, {"projected": True})
        self.assertEqual([entry[0] for entry in calls], ["collect", "backend", "reader", "read", "bridge"])
        self.assertIs(calls[-1][2], oracle)

    def test_default_collector_explicit_legacy_skips_authoring_route(self):
        oracle = {"vertices": [], "faces": [], "marker": "oracle"}

        class FakeCollector:
            def collect(self, options):
                return oracle

        with (
            mock.patch("mmd_tools.converters.export_scene_collector.ExportSceneCollector", FakeCollector),
            mock.patch(
                "mmd_tools.adapters.maya_cmds_adapter.MayaCmdsAdapter",
                side_effect=AssertionError("legacy must not construct authoring adapter"),
            ),
        ):
            result = _default_collect_model_data(
                {"target_model": "|root", "export_format": "pmx", "authoring_semantics": "legacy"}
            )

        self.assertIs(result, oracle)

    def test_default_collector_registry_failure_does_not_fallback_to_oracle(self):
        oracle = {"vertices": [], "faces": []}

        class FakeCollector:
            def collect(self, options):
                return oracle

        class FakeAdapter:
            def attribute_exists(self, attr, node):
                return attr == "mmd_model_registry"

            def list_relatives(self, node, **kwargs):
                return []

        class FailingBackend:
            def __init__(self, adapter):
                pass

        class FailingSceneAdapter:
            def __init__(self, backend):
                pass

            def read_spec(self, root):
                raise ValueError("broken registry metadata")

        with (
            mock.patch("mmd_tools.converters.export_scene_collector.ExportSceneCollector", FakeCollector),
            mock.patch("mmd_tools.adapters.maya_cmds_adapter.MayaCmdsAdapter", FakeAdapter),
            mock.patch("mmd_tools.adapters.maya_scene_metadata_backend.MayaSceneMetadataBackend", FailingBackend),
            mock.patch("mmd_tools.adapters.scene_metadata_adapter.SceneMetadataAdapter", FailingSceneAdapter),
        ):
            with self.assertRaises(ValueError):
                _default_collect_model_data({"target_model": "|root", "export_format": "pmx"})

    def test_default_collector_mesh_only_root_keeps_oracle_payload(self):
        oracle = {"vertices": [], "faces": [], "marker": "oracle"}

        class FakeCollector:
            def collect(self, options):
                return oracle

        class MeshOnlyAdapter:
            def attribute_exists(self, attr, node):
                return False

            def list_relatives(self, node, **kwargs):
                return []

        with (
            mock.patch("mmd_tools.converters.export_scene_collector.ExportSceneCollector", FakeCollector),
            mock.patch("mmd_tools.adapters.maya_cmds_adapter.MayaCmdsAdapter", MeshOnlyAdapter),
        ):
            result = _default_collect_model_data({"target_model": "|root", "export_format": "pmx"})

        self.assertIs(result, oracle)

    def test_default_collector_uses_current_model_root_as_authority(self):
        oracle = {"vertices": [], "faces": []}
        collect_options = []

        class FakeCollector:
            def collect(self, options):
                collect_options.append(options)
                return oracle

        with (
            mock.patch("maya.cmds.ls", side_effect=AssertionError("selection fallback called")),
            mock.patch("mmd_tools.converters.export_scene_collector.ExportSceneCollector", FakeCollector),
        ):
            result = _default_collect_model_data(
                {
                    "current_model_root": "|root",
                    "export_format": "pmx",
                    "authoring_semantics": "legacy",
                }
            )

        self.assertIs(result, oracle)
        self.assertEqual(collect_options[0]["target_model"], "|root")

    def test_default_collector_rejects_missing_target_without_selection_fallback(self):
        with mock.patch("maya.cmds.ls", side_effect=AssertionError("selection fallback called")):
            with self.assertRaisesRegex(ValueError, "current_model_root"):
                _default_collect_model_data({"export_format": "pmx"})

    def test_execute_reports_missing_collector_or_data(self):
        options = {"file_path": "out.pmx", "export_format": "pmx"}
        action = ExportModelAction(pmx_exporter=_FakePmxExporter(), collector=None)

        result = action.execute(ExportModelRequest(file_path="out.pmx", options=options))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsInstance(result.error, ValueError)
        self.assertIn("Model export requires model_data or a collector", result.status_message)

    def test_execute_preserves_authoring_integration_report(self):
        def failing_collector(_options):
            raise AuthoringExportIntegrationError(
                "semantic data differs from the scene oracle",
                code="AUTHORING_ORACLE_MISMATCH",
                path="morphs[0].offsets",
            )

        action = ExportModelAction(
            pmx_exporter=_FakePmxExporter(),
            collector=failing_collector,
            output_verifier=None,
        )
        result = action.execute(
            ExportModelRequest(file_path="out.pmx", options={"export_format": "pmx"})
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, AuthoringExportIntegrationError)
        self.assertIsNotNone(result.validation_report)
        self.assertEqual(result.validation_report.issues[0].code, "AUTHORING_ORACLE_MISMATCH")

    def test_execute_reports_unsupported_format(self):
        options = {"file_path": "out.obj", "export_format": "obj", "model_data": {"vertices": [1]}}
        action = ExportModelAction(collector=None)

        result = action.execute(ExportModelRequest(file_path="out.obj", options=options))

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ValueError)
        self.assertIn("model export format obj is not supported", result.status_message)

    def test_request_preserves_options_for_future_exporter_boundary(self):
        options = {"file_path": "out.pmx", "export_format": "pmx", "apply_scale": False}

        request = ExportModelRequest(file_path="out.pmx", options=options)

        self.assertIs(request.options, options)


class _FakePmxExporter:
    def __init__(self):
        self.calls = []

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"fake pmx bytes")


class TestExportVmdAction(unittest.TestCase):
    """VMD export action の最小依存境界を検証する。"""

    def test_execute_exports_provided_animation_data(self):
        exporter = _FakeVmdExporter()
        action = ExportVmdAction(exporter=exporter)
        vmd_data = VmdData()

        with tempfile.TemporaryDirectory() as directory:
            file_path = str(Path(directory) / "out.vmd")
            result = action.execute(
                ExportVmdRequest(
                    file_path=file_path,
                    options={"export_format": "vmd"},
                    animation_data=vmd_data,
                )
            )

            self.assertTrue(result.succeeded)
            self.assertEqual(result.exported_path, file_path)
            self.assertIsNone(result.error)
            self.assertEqual(len(exporter.calls), 1)
            self.assertEqual(exporter.calls[0][1], vmd_data)
            self.assertTrue(Path(file_path).is_file())

    def test_execute_reports_missing_collector_or_data(self):
        action = ExportVmdAction(exporter=_FakeVmdExporter(), collector=None)

        result = action.execute(ExportVmdRequest(file_path="out.vmd", options={}))

        self.assertFalse(result.succeeded)
        self.assertIsNone(result.exported_path)
        self.assertIsInstance(result.error, ValueError)

    def test_bake_timeline_without_prepared_animation_data_never_calls_collector_or_writer(self):
        exporter = _FakeVmdExporter()
        collector_calls = []
        action = ExportVmdAction(
            exporter=exporter,
            collector=lambda options: collector_calls.append(options),
        )

        result = action.execute(
            ExportVmdRequest(
                file_path="out.vmd",
                options={"export_format": "vmd", "export_strategy": "bake_timeline"},
            )
        )

        self.assertFalse(result.succeeded)
        self.assertIsInstance(result.error, ValueError)
        self.assertIn("prepared animation_data", str(result.error))
        self.assertEqual(collector_calls, [])
        self.assertEqual(exporter.calls, [])

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
        VmdData().write_file(file_path)


if __name__ == "__main__":
    unittest.main()
