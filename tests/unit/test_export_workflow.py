"""ScenePreflight and shared ExportWorkflowService contracts."""

from pathlib import Path
import tempfile
import unittest

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.services.export_workflow_service import (  # noqa: E402
    ExportWorkflowRequest,
    ExportWorkflowService,
    STATE_BLOCKED,
    STATE_READY,
    STATE_SUCCEEDED,
)
from mmd_tools.validation.export_validator import (  # noqa: E402
    ExportValidationError,
    ExportValidationIssue,
    ExportValidationReport,
    validate_model_data,
)
from mmd_tools.validation.scene_preflight import ScenePreflight  # noqa: E402
from mmd_tools.actions.export_vmd_action import ExportVmdAction  # noqa: E402
from mmd_tools.actions.prepare_vmd_export_action import (  # noqa: E402
    PrepareVmdExportError,
    PrepareVmdExportAction,
    VmdExportDiscovery,
)
from mmd_tools.io.vmd_exporter import VmdExporter  # noqa: E402


def _valid_model_data():
    """Return the smallest collector-shaped PMX payload."""
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
    def __init__(self, *, exists=True):
        self.exists = exists

    def object_exists(self, _target):
        return self.exists


class _VmdPrepareBackend:
    def __init__(self, *, raw_provenance=False):
        self.discover_calls = 0
        self.collect_calls = 0
        self.seen_requests = []
        self.raw_provenance = raw_provenance

    def discover(self, request):
        self.discover_calls += 1
        self.seen_requests.append(request)
        return VmdExportDiscovery(
            scene_session_id="scene-1",
            target_uuid="model-uuid",
            target_identity="model_ROOT",
            dependency_closure_fingerprint="deps-1",
            model_name="WorkflowFixture",
        )

    def supports_streaming(self):
        return True

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
            "raw_provenance": self.raw_provenance,
            "section_counts": {
                "bones": 1,
                "morphs": 0,
                "cameras": 0,
                "lights": 0,
                "shadows": 0,
                "ik": 0,
            },
        }


class _VmdRevisions:
    def __init__(self):
        self.calls = 0

    def arm(self, _request, _discovery):
        return None

    def current_revision(self, _request, _discovery):
        self.calls += 1
        return "revision-1"


class _RecordingVmdExporter(VmdExporter):
    def __init__(self):
        super().__init__(native_exporter=None)
        self.written_payload = None
        self.write_calls = 0

    def export_vmd_animation(self, file_path, maya_data):
        self.write_calls += 1
        self.written_payload = maya_data
        maya_data.header.model_name = "writer-mutated"
        return super().export_vmd_animation(file_path, maya_data)


class TestScenePreflight(unittest.TestCase):
    """Scene facts are checked before any collector or writer call."""

    def test_missing_target_and_extension_are_blocking(self):
        result = ScenePreflight().run(
            {"file_path": "motion.pmx", "export_format": "pmx"}
        )

        self.assertTrue(result.report.is_blocking)
        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_TARGET_MISSING"],
        )

        extension_result = ScenePreflight(scene_service=_SceneService()).run(
            {"file_path": "motion.vmd", "export_format": "pmx", "target_model": "model_ROOT"}
        )
        self.assertIn(
            "SCENE_OUTPUT_EXTENSION_MISMATCH",
            [issue.code for issue in extension_result.report.issues],
        )

    def test_current_model_does_not_fallback_to_maya_selection(self):
        class SelectionService(_SceneService):
            def get_selected_nodes(self):
                return ["selected_ROOT"]

        result = ScenePreflight(scene_service=SelectionService()).run(
            {
                "file_path": "model.pmx",
                "export_format": "pmx",
                "require_target": True,
                "require_current_model": True,
                "current_model_root": None,
            }
        )

        self.assertEqual([issue.code for issue in result.report.issues], ["SCENE_TARGET_MISSING"])
        self.assertIsNone(result.metadata["target_identity"])

    def test_current_model_stale_is_blocking_before_collection(self):
        result = ScenePreflight(
            scene_service=_SceneService(exists=False),
        ).run(
            {
                "file_path": "model.pmx",
                "export_format": "pmx",
                "require_current_model": True,
                "current_model_root": "stale_ROOT",
            }
        )

        self.assertEqual([issue.code for issue in result.report.issues], ["SCENE_TARGET_STALE"])

    def test_stale_target_and_owner_state_are_fail_closed(self):
        def ownership(_target):
            return {
                "control_rig": {"state": "EDIT", "owner": "CONTROL_OWNED"},
                "humanik": {"blocked": "target_preview", "character": "HIKCharacter1"},
            }

        result = ScenePreflight(
            scene_service=_SceneService(exists=False),
            ownership_checker=ownership,
        ).run({"file_path": "motion.vmd", "export_format": "vmd", "target_model": "model_ROOT"})

        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_TARGET_STALE", "SCENE_OWNER_CONTROL_RIG", "SCENE_OWNER_HUMANIK"],
        )

    def test_valid_scene_metadata_is_provenance_ready(self):
        result = ScenePreflight(
            scene_service=_SceneService(),
            ownership_checker=lambda _target: {"control_rig": None, "humanik": None},
            scene_revision_getter=lambda: "revision-7",
            source_scene_getter=lambda: "C:/scene/source.ma",
        ).run(
            {
                "file_path": "motion.vmd",
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "target_model": "ns:model_ROOT",
                "frame_range": (0, 120),
                "frame_step": 1,
                "apply_scale": True,
            }
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.metadata["target_identity"], "ns:model_ROOT")
        self.assertEqual(result.metadata["namespace"], "ns")
        self.assertEqual(result.metadata["scene_revision"], "revision-7")
        self.assertEqual(result.metadata["frame_range"], [0, 120])

    def test_invalid_range_scale_and_source_path_are_deterministic(self):
        result = ScenePreflight(scene_service=_SceneService()).run(
            {
                "file_path": "source.pmx",
                "source_path": str(Path("source.pmx").absolute()),
                "export_format": "pmx",
                "target_model": "model_ROOT",
                "frame_range": (20, 10),
                "frame_step": 0,
                "scale": float("nan"),
            }
        )
        codes = [issue.code for issue in result.report.issues]
        self.assertEqual(
            codes,
            [
                "SCENE_FRAME_RANGE_INVALID",
                "SCENE_FRAME_STEP_INVALID",
                "SCENE_SCALE_INVALID",
                "SCENE_OUTPUT_SAME_AS_SOURCE",
            ],
        )


class _FakeModelAction:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self._collector = lambda _options: payload
        self._validator = validate_model_data

    def execute(self, request):
        self.calls.append(request)
        return type(
            "Result",
            (),
            {
                "succeeded": True,
                "error": None,
                "validation_report": validate_model_data(self.payload, "pmx"),
            },
        )()


class TestExportWorkflowService(unittest.TestCase):
    """UI and headless callers share one validation/action boundary."""

    def test_prepared_vmd_token_publishes_cached_artifact_without_rewriting(self):
        backend = _VmdPrepareBackend()
        revisions = _VmdRevisions()
        exporter = _RecordingVmdExporter()
        prepare_action = PrepareVmdExportAction(backend, revisions)
        vmd_action = ExportVmdAction(
            exporter=exporter,
            output_verifier=None,
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=vmd_action,
            prepare_vmd_action=prepare_action,
        )
        request = ExportWorkflowRequest(
            "motion.vmd",
            {
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "current_model_root": "model_ROOT",
                "require_current_model": True,
                "require_target": True,
                "target_uuid": "model-uuid",
                "target_identity": "model_ROOT",
                "scene_session_id": "scene-1",
                "dependency_closure_fingerprint": "deps-1",
            },
        )

        prepared = service.prepare_vmd(request)
        self.assertTrue(prepared.succeeded)
        token = prepared.token
        self.assertEqual(exporter.write_calls, 0)
        vmd_action._validator = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prepared workflow called VMD validator")
        )
        validation = service.validate(
            ExportWorkflowRequest(
                "other-motion.vmd",
                dict(request.options, validation_report_dir="reports/other"),
                prepared_vmd_token=token,
            )
        )

        self.assertEqual(validation.state, STATE_READY)
        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(backend.discover_calls, 3)
        self.assertTrue(
            all(
                request.options["target_model"] == "model_ROOT"
                for request in backend.seen_requests
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            output_request = ExportWorkflowRequest(
                str(target),
                dict(request.options),
                prepared_vmd_token=token,
            )
            result = service.execute(output_request)
            self.assertEqual(target.read_bytes(), Path(token.staged_artifact.file_path).read_bytes())

        self.assertEqual(result.state, STATE_SUCCEEDED)
        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(exporter.write_calls, 0)
        self.assertIsNone(validation.payload)
        self.assertIsNone(result.payload)
        self.assertEqual(result.action_result.payload_fingerprint, token.staged_artifact.sha256)

    def test_prepared_vmd_raw_loss_info_does_not_require_final_ack(self):
        backend = _VmdPrepareBackend(raw_provenance=True)
        revisions = _VmdRevisions()
        exporter = _RecordingVmdExporter()
        prepare_action = PrepareVmdExportAction(backend, revisions)
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=ExportVmdAction(exporter=exporter, output_verifier=None),
            prepare_vmd_action=prepare_action,
        )
        request = ExportWorkflowRequest(
            "motion.vmd",
            {
                "export_format": "vmd",
                "export_strategy": "bake_timeline",
                "current_model_root": "model_ROOT",
                "require_current_model": True,
                "require_target": True,
                "target_uuid": "model-uuid",
                "target_identity": "model_ROOT",
                "scene_session_id": "scene-1",
                "dependency_closure_fingerprint": "deps-1",
            },
        )

        prepared = service.prepare_vmd(request)
        token = prepared.token
        self.assertTrue(prepared.succeeded)
        self.assertFalse(token.validation_report.requires_warning_ack)
        self.assertEqual(exporter.write_calls, 0)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            target.write_bytes(b"existing-output")
            result = service.execute(
                ExportWorkflowRequest(
                    str(target), dict(request.options), prepared_vmd_token=token
                )
            )
            self.assertEqual(result.state, STATE_SUCCEEDED)
            self.assertNotEqual(target.read_bytes(), b"existing-output")
            self.assertEqual(backend.collect_calls, 1)
            self.assertEqual(exporter.write_calls, 0)

        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(exporter.write_calls, 0)
        prepare_action.invalidate(token)

    def test_prepare_vmd_preflight_blocks_before_discovery_or_collection(self):
        backend = _VmdPrepareBackend()
        prepare_action = PrepareVmdExportAction(backend, _VmdRevisions())
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(exists=False),
                ownership_checker=lambda _target: {},
            ),
            prepare_vmd_action=prepare_action,
        )

        result = service.prepare_vmd(
            ExportWorkflowRequest(
                "motion.vmd",
                {
                    "export_format": "vmd",
                    "export_strategy": "bake_timeline",
                    "require_target": True,
                    "target_model": "stale_ROOT",
                },
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertIn("SCENE_TARGET_STALE", str(result.error))
        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_TARGET_STALE"],
        )
        self.assertEqual(backend.discover_calls, 0)
        self.assertEqual(backend.collect_calls, 0)

    def test_current_model_is_forwarded_as_explicit_collector_target(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        observed = []

        def collector(options):
            observed.append(dict(options))
            return payload

        action._collector = collector
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )

        result = service.validate(
            ExportWorkflowRequest(
                "model.pmx",
                {
                    "export_format": "pmx",
                    "require_target": True,
                    "require_current_model": True,
                    "current_model_root": "model_ROOT",
                },
            )
        )

        self.assertEqual(result.state, STATE_READY)
        self.assertEqual(observed[0]["target_model"], "model_ROOT")

    def test_current_model_is_forwarded_as_vmd_model_track_target(self):
        options = ExportWorkflowService._target_options(
            {
                "export_format": "vmd",
                "current_model_root": "model_ROOT",
                "cameras": ["scene_camera"],
                "lights": ["scene_light"],
            },
            {"format": "vmd"},
        )

        self.assertEqual(options["target_model"], "model_ROOT")
        self.assertEqual(options["cameras"], ["scene_camera"])
        self.assertEqual(options["lights"], ["scene_light"])

    def test_vmd_current_model_switch_scopes_collector_boundary(self):
        observed = []

        def collector(options):
            observed.append(dict(options))
            return {
                "model_name": "MotionFixture",
                "bone_frames": [],
                "raw_provenance": {
                    "raw_bone_interpolation_complete": True,
                    "raw_bone_key_count": 0,
                    "raw_bone_interpolation": [],
                },
            }

        vmd_action = ExportVmdAction(
            exporter=VmdExporter(native_exporter=None),
            collector=collector,
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=vmd_action,
        )

        for current_model_root in ("|model_A|root", "|model_B|root"):
            result = service.validate(
                ExportWorkflowRequest(
                    "motion.vmd",
                    {
                        "export_format": "vmd",
                        "export_strategy": "preserve_keys",
                        "require_target": True,
                        "require_current_model": True,
                        "current_model_root": current_model_root,
                    },
                )
            )
            self.assertEqual(result.state, STATE_READY)

        self.assertEqual(
            [options["target_model"] for options in observed],
            ["|model_A|root", "|model_B|root"],
        )

    def test_validate_does_not_call_writer_and_execute_reuses_snapshot(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / "model.pmx")
            request = ExportWorkflowRequest(
                target,
                {"export_format": "pmx", "target_model": "model_ROOT"},
            )
            validation = service.validate(request)
            self.assertEqual(validation.state, STATE_READY)
            self.assertEqual(action.calls, [])
            self.assertIsNotNone(validation.snapshot)

            result = service.execute(request)

        self.assertEqual(result.state, STATE_SUCCEEDED)
        self.assertEqual(len(action.calls), 1)
        self.assertEqual(
            action.calls[0].options["validation_snapshot"].payload_fingerprint,
            result.snapshot.payload_fingerprint,
        )

    def test_progress_callback_reports_validation_boundaries_and_writer_transition(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )
        request = ExportWorkflowRequest(
            "model.pmx",
            {"export_format": "pmx", "target_model": "model_ROOT"},
        )
        stages = []

        result = service.execute(request, progress_callback=stages.append)

        self.assertEqual(result.state, STATE_SUCCEEDED)
        self.assertEqual(
            stages,
            [
                "scene_preflight",
                "payload_collection",
                "payload_validation",
                "report_ready",
                "writer",
            ],
        )

    def test_vmd_raw_provenance_survives_workflow_validate_and_execute(self):
        raw_provenance = {
            "raw_bone_interpolation_complete": True,
            "raw_bone_key_count": 0,
            "raw_bone_interpolation": [],
        }

        def collector(_options):
            return {
                "model_name": "ImportedMotion",
                "raw_provenance": raw_provenance,
                "bone_frames": [],
            }

        vmd_action = ExportVmdAction(
            exporter=VmdExporter(native_exporter=None),
            collector=collector,
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=vmd_action,
        )
        with tempfile.TemporaryDirectory() as directory:
            request = ExportWorkflowRequest(
                str(Path(directory) / "motion.vmd"),
                {
                    "export_format": "vmd",
                    "export_strategy": "preserve_keys",
                    "target_model": "model_ROOT",
                },
            )

            validation = service.validate(request)
            result = service.execute(request)

        self.assertEqual(validation.state, STATE_READY)
        self.assertEqual(result.state, STATE_SUCCEEDED)
        self.assertEqual(result.action_result.validation_report.mode, "preserve_keys")

    def test_vmd_workflow_default_bake_timeline_requires_prepared_token(self):
        observed = []
        exporter = _RecordingVmdExporter()

        def collector(options):
            observed.append(dict(options))
            return {"model_name": "BakeTimelineFixture", "bone_frames": []}

        vmd_action = ExportVmdAction(
            exporter=exporter,
            collector=collector,
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=vmd_action,
        )

        result = service.validate(
            ExportWorkflowRequest(
                "motion.vmd",
                {
                    "export_format": "vmd",
                    "target_model": "model_ROOT",
                },
            )
        )

        self.assertEqual(result.state, STATE_BLOCKED)
        self.assertIsInstance(result.error, PrepareVmdExportError)
        self.assertIn("prepared VMD export token", str(result.error))
        self.assertEqual(observed, [])
        execute_result = service.execute(
            ExportWorkflowRequest(
                "motion.vmd",
                {
                    "export_format": "vmd",
                    "target_model": "model_ROOT",
                },
            )
        )
        self.assertEqual(execute_result.state, STATE_BLOCKED)
        self.assertEqual(observed, [])
        self.assertIsNone(exporter.written_payload)

    def test_vmd_workflow_preserves_explicit_raw_provenance(self):
        explicit_provenance = {"source": "explicit"}
        collected_provenance = {"source": "collector"}
        observed = []

        def collector(_options):
            return {
                "model_name": "ImportedMotion",
                "raw_provenance": collected_provenance,
                "bone_frames": [],
            }

        def validator(_payload, export_strategy, raw_provenance=None, **_kwargs):
            observed.append(raw_provenance)
            return ExportValidationReport("vmd", (), mode=export_strategy)

        vmd_action = ExportVmdAction(
            exporter=VmdExporter(native_exporter=None),
            collector=collector,
            validator=validator,
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            vmd_action=vmd_action,
        )
        request = ExportWorkflowRequest(
            "motion.vmd",
            {
                "export_format": "vmd",
                "export_strategy": "preserve_keys",
                "target_model": "model_ROOT",
                "raw_provenance": explicit_provenance,
            },
        )

        validation = service.validate(request)

        self.assertEqual(validation.state, STATE_READY)
        self.assertEqual(observed, [explicit_provenance])

    def test_scene_blocking_stops_before_collector(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        action._collector = lambda _options: (_ for _ in ()).throw(AssertionError("collector called"))
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(),
            model_action=action,
            vmd_action=object(),
        )

        result = service.validate(
            ExportWorkflowRequest("model.pmx", {"export_format": "pmx"})
        )

        self.assertEqual(result.state, STATE_BLOCKED)
        self.assertEqual(result.report.issues[0].code, "SCENE_TARGET_MISSING")

    def test_collector_validation_error_preserves_report_and_wrapper(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        lower_report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue(
                    "MODEL_DATA_NOT_MAPPING",
                    "fatal",
                    True,
                    "model_data",
                    "model data must be a mapping",
                ),
                ExportValidationIssue(
                    "VERTICES_EMPTY",
                    "fatal",
                    True,
                    "vertices",
                    "vertices must not be empty",
                ),
            ),
        )
        error = ExportValidationError(lower_report)
        action._collector = lambda _options: (_ for _ in ()).throw(error)
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )

        result = service.validate(
            ExportWorkflowRequest(
                "model.pmx",
                {"export_format": "pmx", "target_model": "model_ROOT"},
            )
        )

        self.assertEqual(result.state, STATE_BLOCKED)
        self.assertIs(result.error, error)
        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_COLLECT_FAILED", "MODEL_DATA_NOT_MAPPING", "VERTICES_EMPTY"],
        )
        self.assertEqual(
            [issue.message for issue in result.report.issues[1:]],
            [
                "model data must be a mapping",
                "vertices must not be empty",
            ],
        )
        self.assertEqual(action.calls, [])

    def test_collector_report_does_not_duplicate_existing_wrapper(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        lower_report = ExportValidationReport(
            "pmx",
            (
                ExportValidationIssue(
                    "SCENE_COLLECT_FAILED",
                    "fatal",
                    True,
                    "collector",
                    "pre-existing collector failure",
                ),
                ExportValidationIssue(
                    "MODEL_DATA_NOT_MAPPING",
                    "fatal",
                    True,
                    "model_data",
                    "model data must be a mapping",
                ),
            ),
        )
        action._collector = lambda _options: (_ for _ in ()).throw(
            ExportValidationError(lower_report)
        )
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )

        result = service.validate(
            ExportWorkflowRequest(
                "model.pmx",
                {"export_format": "pmx", "target_model": "model_ROOT"},
            )
        )

        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_COLLECT_FAILED", "MODEL_DATA_NOT_MAPPING"],
        )

    def test_generic_collector_error_includes_detail_and_blocks_writer(self):
        payload = _valid_model_data()
        action = _FakeModelAction(payload)
        error = RuntimeError("collector detail: missing scene mesh")
        action._collector = lambda _options: (_ for _ in ()).throw(error)
        service = ExportWorkflowService(
            scene_preflight=ScenePreflight(
                scene_service=_SceneService(),
                ownership_checker=lambda _target: {},
            ),
            model_action=action,
            vmd_action=object(),
        )

        result = service.execute(
            ExportWorkflowRequest(
                "model.pmx",
                {"export_format": "pmx", "target_model": "model_ROOT"},
            )
        )

        self.assertEqual(result.state, STATE_BLOCKED)
        self.assertIs(result.error, error)
        self.assertEqual(
            [issue.code for issue in result.report.issues],
            ["SCENE_COLLECT_FAILED"],
        )
        self.assertIn("collector detail: missing scene mesh", result.report.issues[0].message)
        self.assertEqual(action.calls, [])


if __name__ == "__main__":
    unittest.main()
