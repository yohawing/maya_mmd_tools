"""Focused single-operation export workflow contracts."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.actions.bake_timeline_vmd_export_action import (  # noqa: E402
    BakeTimelineVmdExportAction,
    VmdExportDiscovery,
    _vmd_model_name_with_fallback,
)
from mmd_tools.core.vmd_data import VmdData  # noqa: E402
from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowRequest,
    ExportWorkflowService,
    STATE_BLOCKED,
    STATE_FAILED,
    STATE_SUCCEEDED,
)
from mmd_tools.validation.export_validator import (  # noqa: E402
    ExportValidationAcknowledgementRequired,
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from mmd_tools.validation.scene_preflight import (  # noqa: E402
    ScenePreflight,
    ScenePreflightResult,
)


def _model_payload():
    return {
        "model_name": "WorkflowFixture",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
            },
            {
                "position": [1.0, 0.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [1.0, 0.0],
                "bone_indices": [0],
            },
            {
                "position": [0.0, 1.0, 0.0],
                "normal": [0.0, 0.0, 1.0],
                "uv": [0.0, 1.0],
                "bone_indices": [0],
            },
        ],
        "faces": [[0, 1, 2]],
        "materials": [{"name": "Default", "diffuse": [0.8, 0.8, 0.8, 1.0], "face_count": 3}],
        "bones": None,
    }


class _SceneService:
    def object_exists(self, _target):
        return True


class _WarningScenePreflight:
    """Return a non-blocking scene warning without changing the collector."""

    def __init__(self, report):
        self.report = report

    def run(self, _options):
        return ScenePreflightResult(
            self.report,
            {
                "format": "vmd",
                "export_strategy": "bake_timeline",
                "target_identity": "model_ROOT",
            },
        )


class _VmdBoundary:
    def __init__(self):
        self.collect_calls = 0
        self.discover_calls = 0
        self.close_calls = 0

    def supports_streaming(self):
        return True

    def discover(self, _request):
        self.discover_calls += 1
        return VmdExportDiscovery(
            scene_session_id="scene",
            target_uuid="uuid",
            target_identity="model_ROOT",
            dependency_closure_fingerprint="deps",
            model_name="WorkflowFixture",
        )

    def arm(self, _request, _discovery):
        return None

    def current_revision(self, _request, _discovery):
        return "revision"

    def close(self):
        self.close_calls += 1

    def collect_to_sink(self, _request, sink):
        self.collect_calls += 1
        sink.write_frame(
            "bones",
            {
                "bone_name": "center",
                "frame": 1,
                "position": (0.0, 0.0, 0.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            },
        )
        return {
            "validation_frame_range": (0, 1),
            "section_counts": {
                "bones": 1,
                "morphs": 0,
                "cameras": 0,
                "lights": 0,
                "shadows": 0,
                "ik": 0,
            },
        }


class _TemporaryVmdBoundary(_VmdBoundary):
    """Exercise the short-lived Control Rig restoration path."""

    def __init__(self):
        super().__init__()
        self.restore_calls = 0

    def prepare_for_collection(self, _request):
        return object()

    def restore_after_collection(self, _context):
        self.restore_calls += 1


class _DirectControlRigVmdBoundary(_VmdBoundary):
    """Accept direct collection only with the complete Current Model route."""

    def __init__(self):
        super().__init__()
        self.capability_options = None

    def can_prepare_for_collection(self, request):
        self.capability_options = dict(request)
        return (
            request.get("current_model_root") == "model_ROOT"
            and request.get("target_model") == "model_ROOT"
        )

    def prepare_for_collection(self, _request):
        return None


class _ModelAction:
    def __init__(self):
        self.collect_calls = 0
        self.execute_calls = 0
        self._validator = validate_model_data

    def _collector(self, _options):
        self.collect_calls += 1
        return _model_payload()

    def execute(self, request):
        self.execute_calls += 1
        self.request = request
        return type(
            "Result",
            (),
            {
                "succeeded": True,
                "error": None,
                "validation_report": validate_model_data(request.options["model_data"], "pmx"),
            },
        )()


class ExportWorkflowTests(unittest.TestCase):
    def _service(self, *, model_action=None, vmd_action=None, scene_preflight=None):
        return ExportWorkflowService(
            scene_preflight=scene_preflight
            or ScenePreflight(scene_service=_SceneService(), ownership_checker=lambda _target: {}),
            model_action=model_action,
            vmd_action=vmd_action,
        )

    def _vmd_request(self, target, **extra_options):
        options = {"export_format": "vmd", "target_model": "model_ROOT"}
        options.update(extra_options)
        return ExportWorkflowRequest(str(target), options)

    def _assert_vmd_boundary_failure(self, result, boundary, target):
        self.assertEqual(result.state, STATE_FAILED)
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(target.parent.glob(".motion.*.vmd")), [])
        self.assertEqual(boundary.restore_calls, 1)
        self.assertGreaterEqual(boundary.close_calls, 1)
        self.assertIsNone(result.active_phase)
        self.assertIn("cleanup", result.completed_phases)
        self.assertIn("total", result.phase_timings)
        self.assertFalse(any("token" in name for name in vars(result)))
        self.assertIsNotNone(result.action_result, result.error)
        self.assertFalse(any("token" in name for name in vars(result.action_result)))

    def test_pmx_collects_once_and_delegates_one_action(self):
        action = _ModelAction()
        result = self._service(model_action=action).execute(
            ExportWorkflowRequest(
                "model.pmx", {"export_format": "pmx", "target_model": "model_ROOT"}
            )
        )

        self.assertEqual(result.state, STATE_SUCCEEDED)
        self.assertEqual(action.collect_calls, 1)
        self.assertEqual(action.execute_calls, 1)
        self.assertEqual(result.completed_phases, ["collect"])

    def test_vmd_collects_and_publishes_one_private_sibling(self):
        boundary = _VmdBoundary()
        service = self._service(vmd_action=BakeTimelineVmdExportAction(boundary))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"old target")
            result = service.execute(
                ExportWorkflowRequest(
                    str(target),
                    {
                        "export_format": "vmd",
                        "current_model_root": "model_ROOT",
                        "require_current_model": True,
                        "require_target": True,
                    },
                )
            )

            self.assertEqual(result.state, STATE_SUCCEEDED, result.error)
            self.assertNotEqual(target.read_bytes(), b"old target")
            self.assertEqual(boundary.collect_calls, 1)
            self.assertGreaterEqual(boundary.close_calls, 1)
            self.assertEqual(
                result.completed_phases,
                ["collect", "encode", "flush", "output_verify", "cleanup", "replace"],
            )
            self.assertEqual(list(Path(directory).glob(".motion.*.vmd")), [])

    def test_vmd_unencodable_model_name_uses_cp932_fallback_and_warning(self):
        class UnicodeModelBoundary(_VmdBoundary):
            def discover(self, request):
                result = super().discover(request)
                return VmdExportDiscovery(
                    scene_session_id=result.scene_session_id,
                    target_uuid=result.target_uuid,
                    target_identity=result.target_identity,
                    dependency_closure_fingerprint=result.dependency_closure_fingerprint,
                    model_name="桃川うさぴ🍑",
                )

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            result = BakeTimelineVmdExportAction(
                UnicodeModelBoundary()
            ).execute_one_shot(
                self._vmd_request(target),
                acknowledge_warnings=True,
                write_report=False,
            )

            self.assertTrue(result.succeeded, result.error)
            self.assertTrue(target.is_file())
            self.assertEqual(
                VmdData().parse_file(str(target)).header.model_name,
                "桃川うさぴ?",
            )
            issue = next(
                item
                for item in result.validation_report.issues
                if item.path == "scene.model.vmd_name_encoding"
            )
            self.assertEqual(issue.severity, "warning")
            self.assertEqual(issue.details["original_name"], "桃川うさぴ🍑")
            self.assertEqual(issue.details["exported_name"], "桃川うさぴ?")
            self.assertEqual(
                issue.details["aggregation_discriminator"], "unsupported_feature"
            )

    def test_vmd_model_name_fallback_preserves_supported_characters_and_is_nonempty(self):
        self.assertEqual(_vmd_model_name_with_fallback("桃川うさぴ"), ("桃川うさぴ", None))
        fallback, details = _vmd_model_name_with_fallback("腹显")
        self.assertEqual(fallback, "腹?")
        self.assertEqual(details["replacement"], "question_mark")
        self.assertEqual(_vmd_model_name_with_fallback("")[0], "Model")
        self.assertEqual(_vmd_model_name_with_fallback("\x00")[0], "?")

    def test_prepare_vmd_is_synchronous_one_shot_headless_seam(self):
        boundary = _VmdBoundary()
        service = self._service(vmd_action=BakeTimelineVmdExportAction(boundary))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            result = service.prepare_vmd(
                ExportWorkflowRequest(
                    str(target), {"export_format": "vmd", "target_model": "model_ROOT"}
                )
            )

            self.assertEqual(result.state, STATE_SUCCEEDED, result.error)
            self.assertEqual(boundary.collect_calls, 1)
            self.assertGreaterEqual(boundary.close_calls, 1)
            self.assertTrue(target.is_file())
            self.assertEqual(list(Path(directory).glob(".motion.*.vmd")), [])

    def test_gui_shaped_control_rig_request_uses_direct_vmd_collection(self):
        boundary = _DirectControlRigVmdBoundary()
        service = self._service(
            vmd_action=BakeTimelineVmdExportAction(boundary),
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {
                    "control_rig": {"state": "EDIT", "owner": "CONTROL_OWNED"}
                },
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            result = service.execute(
                ExportWorkflowRequest(
                    str(target),
                    {
                        "export_format": "vmd",
                        "export_strategy": "bake_timeline",
                        "current_model_root": "model_ROOT",
                        "require_current_model": True,
                        "require_target": True,
                    },
                )
            )

        self.assertEqual(result.state, STATE_SUCCEEDED, result.error)
        self.assertEqual(boundary.collect_calls, 1)
        self.assertEqual(boundary.capability_options["target_model"], "model_ROOT")
        self.assertFalse(result.report.is_blocking)

    def test_vmd_failure_keeps_existing_target_and_cleans_stage(self):
        class FailingBoundary(_TemporaryVmdBoundary):
            def collect_to_sink(self, request, sink):
                super().collect_to_sink(request, sink)
                raise RuntimeError("collector failed")

        boundary = FailingBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            result = self._service(
                vmd_action=BakeTimelineVmdExportAction(boundary)
            ).execute(
                self._vmd_request(target)
            )

            self._assert_vmd_boundary_failure(result, boundary, target)
            self.assertNotIn("collect", result.completed_phases)

    def test_vmd_encode_failure_preserves_target_and_restores_temporary_rig(self):
        boundary = _TemporaryVmdBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            with mock.patch(
                "mmd_tools.actions.vmd_sibling_stage.export_vmd_from_parts",
                side_effect=RuntimeError("native encode failed"),
            ):
                result = self._service(
                    vmd_action=BakeTimelineVmdExportAction(boundary)
                ).execute(self._vmd_request(target))

            self._assert_vmd_boundary_failure(result, boundary, target)
            self.assertIn("collect", result.completed_phases)
            self.assertNotIn("encode", result.completed_phases)

    def test_vmd_flush_failure_preserves_target_and_restores_temporary_rig(self):
        boundary = _TemporaryVmdBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            with mock.patch(
                "mmd_tools.actions.vmd_sibling_stage.os.fsync",
                side_effect=OSError("flush failed"),
            ):
                result = self._service(
                    vmd_action=BakeTimelineVmdExportAction(boundary)
                ).execute(self._vmd_request(target))

            self._assert_vmd_boundary_failure(result, boundary, target)
            self.assertIn("encode", result.completed_phases)
            self.assertNotIn("flush", result.completed_phases)

    def test_vmd_output_verifier_failure_preserves_target_and_restores_temporary_rig(self):
        import mmd_tools.actions.bake_timeline_vmd_export_action as action_module

        boundary = _TemporaryVmdBoundary()
        blocking_report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "OUTPUT_VERIFY_FAILED", "fatal", True, "output", "parser rejected sibling"
                ),
            ),
            mode="bake_timeline",
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            with mock.patch.object(
                action_module,
                "verify_vmd_output_streaming",
                return_value=blocking_report,
            ):
                result = self._service(
                    vmd_action=BakeTimelineVmdExportAction(boundary)
                ).execute(self._vmd_request(target))

            self._assert_vmd_boundary_failure(result, boundary, target)
            self.assertIn("flush", result.completed_phases)
            self.assertNotIn("output_verify", result.completed_phases)

    def test_vmd_replace_failure_preserves_target_and_restores_temporary_rig(self):
        boundary = _TemporaryVmdBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            with mock.patch(
                "mmd_tools.actions.bake_timeline_vmd_export_action.os.replace",
                side_effect=PermissionError("target locked"),
            ):
                result = self._service(
                    vmd_action=BakeTimelineVmdExportAction(boundary)
                ).execute(self._vmd_request(target))

            self._assert_vmd_boundary_failure(result, boundary, target)
            self.assertIn("output_verify", result.completed_phases)
            self.assertNotIn("replace", result.completed_phases)

    def test_vmd_report_artifact_failures_prevent_replace_and_return_a_terminal_result(self):
        import mmd_tools.actions.bake_timeline_vmd_export_action as action_module

        cases = (
            (
                "invalid_evidence",
                {"validation_report_evidence": ["not", "a", "mapping"]},
                None,
                TypeError,
            ),
            (
                "write_error",
                {},
                mock.patch.object(
                    action_module,
                    "write_validation_report_artifacts",
                    side_effect=OSError("report directory denied"),
                ),
                OSError,
            ),
        )
        for name, extra_options, patcher, error_type in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                boundary = _TemporaryVmdBoundary()
                target = Path(directory) / "motion.vmd"
                target.write_bytes(b"original")
                request = self._vmd_request(
                    target,
                    validation_report_dir=str(Path(directory) / "report"),
                    **extra_options,
                )
                if patcher is None:
                    result = self._service(
                        vmd_action=BakeTimelineVmdExportAction(boundary)
                    ).execute(request)
                else:
                    with patcher:
                        result = self._service(
                            vmd_action=BakeTimelineVmdExportAction(boundary)
                        ).execute(request)

                self._assert_vmd_boundary_failure(result, boundary, target)
                self.assertIsInstance(result.error, error_type)
                self.assertIsNone(result.validation_report_artifacts)
                self.assertNotIn("replace", result.completed_phases)

    def test_vmd_success_writes_report_before_replacing_target(self):
        import mmd_tools.actions.bake_timeline_vmd_export_action as action_module

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            report_directory = Path(directory) / "report"
            action = BakeTimelineVmdExportAction(_VmdBoundary())
            original_write_report = action._write_requested_report
            original_replace = action_module.os.replace
            events = []

            def write_report(*args, **kwargs):
                events.append("report")
                return original_write_report(*args, **kwargs)

            def replace(source, destination):
                if Path(destination).resolve(strict=False) == target.resolve(strict=False):
                    events.append("target_replace")
                return original_replace(source, destination)

            with mock.patch.object(action, "_write_requested_report", side_effect=write_report), mock.patch.object(
                action_module.os, "replace", side_effect=replace
            ):
                result = action.execute_one_shot(
                    self._vmd_request(target, validation_report_dir=str(report_directory))
                )

            self.assertTrue(result.succeeded, result.error)
            self.assertLess(events.index("report"), events.index("target_replace"))

    def test_direct_one_shot_artifact_write_failure_returns_a_failure_result(self):
        import mmd_tools.actions.bake_timeline_vmd_export_action as action_module

        boundary = _TemporaryVmdBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            with mock.patch.object(
                action_module,
                "write_validation_report_artifacts",
                side_effect=OSError("report directory denied"),
            ):
                result = BakeTimelineVmdExportAction(boundary).execute_one_shot(
                    self._vmd_request(
                        target,
                        validation_report_dir=str(Path(directory) / "report"),
                    )
                )

            self.assertFalse(result.succeeded)
            self.assertIsInstance(result.error, OSError)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).glob(".motion.*.vmd")), [])
            self.assertGreaterEqual(boundary.close_calls, 1)
            self.assertEqual(boundary.restore_calls, 1)

    def test_vmd_warning_cancel_keeps_target_and_cleans_watch(self):
        import mmd_tools.actions.bake_timeline_vmd_export_action as action_module

        boundary = _VmdBoundary()
        warning = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "OUTPUT_VERIFY_FAILED", "warning", False, "output", "confirm export"
                ),
            ),
            mode="bake_timeline",
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            report_directory = Path(directory) / "cancel-report"
            target.write_bytes(b"original")
            with mock.patch.object(
                action_module, "verify_vmd_output_streaming", return_value=warning
            ):
                result = self._service(
                    vmd_action=BakeTimelineVmdExportAction(boundary)
                ).execute(
                    self._vmd_request(target, validation_report_dir=str(report_directory)),
                    warning_callback=lambda _report: False,
                )

            self.assertEqual(result.state, STATE_BLOCKED)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertGreaterEqual(boundary.close_calls, 1)
            self.assertEqual(list(Path(directory).glob(".motion.*.vmd")), [])
            self.assertIn("warning_decision", result.completed_phases)
            self.assertIsInstance(
                result.action_result.error, ExportValidationAcknowledgementRequired
            )
            self.assertEqual(
                [issue.code for issue in result.report.issues], ["OUTPUT_VERIFY_FAILED"]
            )
            self.assertEqual(result.report.issues[0].reason, "confirm export")
            self.assertIsNotNone(result.validation_report_artifacts)
            self.assertTrue(result.validation_report_artifacts.json_path.is_file())
            self.assertTrue(result.validation_report_artifacts.markdown_path.is_file())
            self.assertEqual(
                json.loads(result.validation_report_artifacts.json_path.read_text("utf-8"))["status"],
                "warning",
            )

    def test_preflight_warning_cancel_stays_a_warning_without_output_write_failure(self):
        warning = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "EXPORT_OPTIONS_INVALID", "warning", False, "scene", "confirm preflight"
                ),
            ),
            mode="bake_timeline",
        )
        boundary = _TemporaryVmdBoundary()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"original")
            result = self._service(
                vmd_action=BakeTimelineVmdExportAction(boundary),
                scene_preflight=_WarningScenePreflight(warning),
            ).execute(self._vmd_request(target), warning_callback=lambda _report: False)

            self.assertEqual(result.state, STATE_BLOCKED)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(Path(directory).glob(".motion.*.vmd")), [])
            self.assertGreaterEqual(boundary.close_calls, 1)
            self.assertEqual(boundary.restore_calls, 1)
            self.assertIsInstance(
                result.action_result.error, ExportValidationAcknowledgementRequired
            )
            self.assertEqual(
                [issue.code for issue in result.report.issues], ["EXPORT_OPTIONS_INVALID"]
            )
            self.assertEqual(result.report.issues[0].reason, "confirm preflight")

    def test_one_shot_vmd_writes_final_report_artifacts_for_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            success_target = Path(directory) / "success.vmd"
            success_report_directory = Path(directory) / "success-report"
            success_boundary = _VmdBoundary()
            success = self._service(
                vmd_action=BakeTimelineVmdExportAction(success_boundary)
            ).execute(
                self._vmd_request(
                    success_target,
                    validation_report_dir=str(success_report_directory),
                    validation_report_evidence={"fixture": "one-shot-success"},
                )
            )

            self.assertEqual(success.state, STATE_SUCCEEDED, success.error)
            self.assertIsNotNone(success.validation_report_artifacts)
            self.assertEqual(
                success.action_result.validation_report_artifacts,
                success.validation_report_artifacts,
            )
            self.assertTrue(success.validation_report_artifacts.json_path.is_file())
            self.assertTrue(success.validation_report_artifacts.markdown_path.is_file())
            payload = json.loads(success.validation_report_artifacts.json_path.read_text("utf-8"))
            self.assertEqual(payload["evidence"]["fixture"], "one-shot-success")

            failure_target = Path(directory) / "failure.vmd"
            failure_target.write_bytes(b"original")
            failure_report_directory = Path(directory) / "failure-report"
            with mock.patch(
                "mmd_tools.actions.vmd_sibling_stage.export_vmd_from_parts",
                side_effect=RuntimeError("native encode failed"),
            ):
                failure = self._service(
                    vmd_action=BakeTimelineVmdExportAction(_VmdBoundary())
                ).execute(
                    self._vmd_request(
                        failure_target,
                        validation_report_dir=str(failure_report_directory),
                    )
                )

            self.assertEqual(failure.state, STATE_FAILED)
            self.assertEqual(failure_target.read_bytes(), b"original")
            self.assertIsNotNone(failure.validation_report_artifacts)
            self.assertEqual(
                failure.action_result.validation_report_artifacts,
                failure.validation_report_artifacts,
            )
            self.assertTrue(failure.validation_report_artifacts.json_path.is_file())
            self.assertTrue(failure.validation_report_artifacts.markdown_path.is_file())
            self.assertEqual(
                json.loads(failure.validation_report_artifacts.json_path.read_text("utf-8"))["status"],
                "blocked",
            )

    def test_direct_one_shot_vmd_action_exposes_requested_report_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "direct.vmd"
            report_directory = Path(directory) / "direct-report"
            result = BakeTimelineVmdExportAction(_VmdBoundary()).execute_one_shot(
                self._vmd_request(
                    target,
                    validation_report_dir=str(report_directory),
                    validation_report_evidence={"fixture": "direct-one-shot"},
                )
            )

            self.assertTrue(result.succeeded, result.error)
            self.assertIsNotNone(result.validation_report_artifacts)
            self.assertEqual(result.validation_report_artifacts.json_path, report_directory / "report.json")
            self.assertEqual(result.validation_report_artifacts.markdown_path, report_directory / "report.md")
            self.assertEqual(
                json.loads(result.validation_report_artifacts.json_path.read_text("utf-8"))["status"],
                "ready",
            )

    def test_scene_block_stops_before_action(self):
        boundary = _VmdBoundary()
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(scene_service=_SceneService()),
            vmd_action=BakeTimelineVmdExportAction(boundary),
        )
        result = service.execute(
            ExportWorkflowRequest("motion.vmd", {"export_format": "vmd"})
        )
        self.assertEqual(result.state, STATE_BLOCKED)
        self.assertEqual(boundary.collect_calls, 0)

    def test_action_error_adds_output_failure_once(self):
        class FailingModelAction(_ModelAction):
            def execute(self, _request):
                return type(
                    "Result",
                    (),
                    {
                        "succeeded": False,
                        "error": PermissionError("locked"),
                        "validation_report": ExportValidationReport("pmx", ()),
                    },
                )()

        result = self._service(model_action=FailingModelAction()).execute(
            ExportWorkflowRequest(
                "model.pmx", {"export_format": "pmx", "target_model": "model_ROOT"}
            )
        )
        self.assertEqual(result.state, STATE_FAILED)
        self.assertEqual([issue.code for issue in result.report.issues], ["OUTPUT_WRITE_FAILED"])


if __name__ == "__main__":
    unittest.main()
