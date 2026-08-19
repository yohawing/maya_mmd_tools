"""Unit coverage for the immutable Mode C VMD preparation seam."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest

from mmd_tools.actions.prepare_vmd_export_action import (
    PrepareVmdExportAction,
    PrepareVmdExportRequest,
    VmdExportDiscovery,
    request_fingerprint,
)
from mmd_tools.actions.prepared_vmd_artifact import stage_vmd_artifact
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport


class _Backend:
    def __init__(self, discoveries):
        self.discoveries = list(discoveries)
        self.discover_calls = 0
        self.collect_calls = 0
        self.close_calls = 0

    def discover(self, request):
        del request
        self.discover_calls += 1
        return self.discoveries[min(self.discover_calls - 1, len(self.discoveries) - 1)]

    def collect(self, request):
        del request
        self.collect_calls += 1
        data = VmdData()
        data.header.model_name = "fixture"
        frame = VmdBoneFrame()
        frame.bone_name = "center"
        frame.frame_number = 4
        frame.position = (1.0, 2.0, 3.0)
        frame.rotation = (0.0, 0.0, 0.0, 1.0)
        data.bone_frames.append(frame)
        return data

    def close(self):
        self.close_calls += 1


class _Revisions:
    def __init__(self, revisions):
        self.revisions = iter(revisions)
        self.arm_calls = 0
        self.current_revision_calls = 0

    def arm(self, request, discovery):
        del request, discovery
        self.arm_calls += 1

    def current_revision(self, request, discovery):
        del request, discovery
        self.current_revision_calls += 1
        return next(self.revisions)


class _FailingExporter:
    def __init__(self):
        self.paths = []

    def export_vmd_animation(self, file_path, vmd_data):
        del vmd_data
        self.paths.append(file_path)
        raise RuntimeError("writer failed")


class _HeadlessExporter:
    @staticmethod
    def export_vmd_animation(file_path, vmd_data):
        vmd_data.write_file(file_path)
        return vmd_data


def _prepare_action(*args, **kwargs):
    kwargs.setdefault("exporter", _HeadlessExporter())
    return PrepareVmdExportAction(*args, **kwargs)


def _legacy_stage_factory(payload, *, exporter, output_verifier, mode):
    """Compatibility fake omitting the optional warning argument."""

    return stage_vmd_artifact(
        payload,
        exporter=exporter,
        output_verifier=output_verifier,
        mode=mode,
    )


class _BlockingVerifier:
    def __call__(self, file_path, mode, *, expected_counts):
        del file_path, expected_counts
        issue = ExportValidationIssue("OUTPUT_PARSE_FAILED", "fatal", True, "output", "bad")
        return ExportValidationReport("vmd", (issue,), mode=mode)


class _WarningVerifier:
    def __call__(self, file_path, mode, *, expected_counts):
        del file_path, expected_counts
        issue = ExportValidationIssue(
            "OUTPUT_WARNING",
            "warning",
            False,
            "output",
            "output requires acknowledgement",
        )
        return ExportValidationReport("vmd", (issue,), mode=mode)


def _blocking_validator(*args, **kwargs):
    del args, kwargs
    issue = ExportValidationIssue("VMD_FRAME_RANGE", "fatal", True, "frame_range", "bad")
    return ExportValidationReport("vmd", (issue,), mode="C")


def _warning_validator(*args, **kwargs):
    del args, kwargs
    issue = ExportValidationIssue(
        "PAYLOAD_WARNING",
        "warning",
        False,
        "payload",
        "payload requires acknowledgement",
    )
    return ExportValidationReport("vmd", (issue,), mode="C")


def _request(**options):
    values = {
        "target_uuid": "model-uuid",
        "target_identity": "|modelRoot",
        "scene_session_id": "scene-1",
        "mode": "C",
        "frame_range": (0, 30),
        "frame_step": 1,
        "scale": 0.1,
        "options": {},
    }
    values.update(options)
    return PrepareVmdExportRequest(**values)


def _discovery(**changes):
    values = {
        "scene_session_id": "scene-1",
        "target_uuid": "model-uuid",
        "target_identity": "|modelRoot",
        "dependency_closure_fingerprint": "sha256:deps-1",
        "cache_id": "cache-1",
    }
    values.update(changes)
    return VmdExportDiscovery(**values)


class PrepareVmdExportActionTests(unittest.TestCase):
    def test_collects_once_and_publishes_immutable_token(self):
        backend = _Backend([_discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1"])

        action = _prepare_action(backend, revisions)
        result = action.execute(_request())

        self.assertTrue(result.succeeded)
        self.assertEqual(backend.discover_calls, 2)
        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(revisions.arm_calls, 1)
        self.assertEqual(result.token.mode, "C")
        self.assertEqual(result.token.revision, "r1")
        with self.assertRaises(FrozenInstanceError):
            result.token.revision = "r2"
        with self.assertRaises(AttributeError):
            result.token.payload.bone_frames[0].frame_number = 99
        self.assertEqual(result.token.payload.bone_frames[0].frame_number, 4)
        self.assertTrue(result.token.staged_artifact.validate_identity())
        self.assertIsInstance(result.token.validation_report, ExportValidationReport)
        self.assertEqual(result.token.validation_report.issues, ())
        self.assertEqual(
            result.token.staged_artifact.output_validation_report.issues,
            result.token.validation_report.issues,
        )
        stage_path = result.token.staged_artifact.file_path
        action.invalidate()
        self.assertFalse(Path(stage_path).exists())

    def test_validation_and_writer_failures_never_publish_a_stage(self):
        backend = _Backend([_discovery(), _discovery()])
        action = _prepare_action(
            backend,
            _Revisions(["r1", "r1"]),
            validator=_blocking_validator,
            exporter=_FailingExporter(),
            output_verifier=_BlockingVerifier(),
        )
        result = action.execute(_request())
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertIn("validation blocked", str(result.error))

        exporter = _FailingExporter()
        action = _prepare_action(
            _Backend([_discovery(), _discovery()]),
            _Revisions(["r1", "r1"]),
            exporter=exporter,
            output_verifier=_BlockingVerifier(),
        )
        result = action.execute(_request())
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertFalse(Path(exporter.paths[0]).parent.exists())

    def test_non_blocking_payload_and_output_warnings_publish_one_verified_stage(self):
        action = _prepare_action(
            _Backend([_discovery(), _discovery()]),
            _Revisions(["r1", "r1"]),
            validator=_warning_validator,
            output_verifier=_WarningVerifier(),
        )

        result = action.execute(_request())

        self.assertTrue(result.succeeded)
        self.assertIsNotNone(result.token)
        self.assertTrue(result.token.validation_report.requires_warning_ack)
        self.assertEqual(
            [issue.code for issue in result.token.validation_report.issues],
            ["PAYLOAD_WARNING", "OUTPUT_WARNING"],
        )
        stage_path = Path(result.token.staged_artifact.file_path)
        self.assertTrue(stage_path.exists())
        action.invalidate()
        self.assertFalse(stage_path.exists())

    def test_legacy_stage_factory_receives_cached_reports(self):
        action = _prepare_action(
            _Backend([_discovery(), _discovery()]),
            _Revisions(["r1", "r1"]),
            stage_factory=_legacy_stage_factory,
        )
        result = action.execute(_request())

        self.assertTrue(result.succeeded)
        self.assertIsInstance(result.token.validation_report, ExportValidationReport)
        self.assertEqual(
            result.token.validation_report.issues,
            result.token.staged_artifact.output_validation_report.issues,
        )
        action.invalidate()

    def test_diagnostics_keep_prepare_phase_evidence_on_success_and_failure(self):
        backend = _Backend([_discovery(), _discovery()])
        action = _prepare_action(backend, _Revisions(["r1", "r1"]))

        result = action.execute(_request())
        diagnostics = action.diagnostics
        self.assertEqual(diagnostics.status, "published")
        self.assertEqual(
            set(
                (
                    "request_fingerprint",
                    "first_discovery",
                    "watcher_arm",
                    "revision_before",
                    "backend_collect",
                    "second_discovery",
                    "revision_after",
                    "payload_freeze_fingerprint",
                    "payload_validate",
                    "artifact_stage_verify",
                    "total",
                )
            ),
            set(diagnostics.phase_timing),
        )
        self.assertEqual(diagnostics.payload_fingerprint, result.token.payload_fingerprint)
        copied = action.diagnostics_copy
        copied["phase_timing"]["total"] = -1
        self.assertGreaterEqual(diagnostics.phase_timing["total"], 0.0)

        failing = _prepare_action(_Backend([_discovery()]), _Revisions([None]))
        failure = failing.execute(_request())
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failing.diagnostics.status, "failed")
        self.assertIn("revision_before", failing.diagnostics.error)
        self.assertIn("total", failing.diagnostics.phase_timing)

        mutable = result.token.copy_for_export()
        mutable.bone_frames[0].frame_number = 99
        self.assertEqual(result.token.payload.bone_frames[0].frame_number, 4)

    def test_revision_race_is_partial_and_never_publishes(self):
        backend = _Backend([_discovery(), _discovery()])
        result = _prepare_action(backend, _Revisions(["r1", "r2"])).execute(_request())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.token)
        self.assertIn("revision changed", str(result.error))

    def test_dependency_closure_change_is_partial_and_never_publishes(self):
        backend = _Backend([_discovery(), _discovery(dependency_closure_fingerprint="sha256:deps-2")])
        result = _prepare_action(backend, _Revisions(["r1", "r1"])).execute(_request())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.token)
        self.assertIn("closure changed", str(result.error))

    def test_missing_revision_fails_before_collection(self):
        backend = _Backend([_discovery()])
        result = _prepare_action(backend, _Revisions([None])).execute(_request())

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertEqual(backend.collect_calls, 0)
        self.assertIn("revision_before", str(result.error))

    def test_non_mode_c_is_rejected_before_discovery(self):
        backend = _Backend([_discovery()])
        result = _prepare_action(backend, _Revisions(["r1"])).execute(_request(mode="A"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(backend.discover_calls, 0)
        self.assertIn("Mode C", str(result.error))

    def test_request_fingerprint_excludes_output_report_and_ack(self):
        base = {
            "target_uuid": "model-uuid",
            "target_identity": "|modelRoot",
            "mode": "C",
            "frame_range": (10, 20),
            "frame_step": 2,
            "scale": 0.25,
            "output_path": "first.vmd",
            "validation_report_dir": "reports/one",
            "ack_warnings": False,
        }
        changed_outputs = dict(base)
        changed_outputs.update(
            {
                "output_path": "second.vmd",
                "validation_report_dir": "reports/two",
                "ack_warnings": True,
            }
        )
        self.assertEqual(request_fingerprint(base), request_fingerprint(changed_outputs))

        changed_semantics = dict(changed_outputs, frame_range=(10, 21))
        self.assertNotEqual(request_fingerprint(base), request_fingerprint(changed_semantics))

    def test_validate_token_rediscoveres_without_collecting_and_allows_output_change(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r1"])
        action = _prepare_action(backend, revisions)
        token = action.prepare(_request())

        action.validate_token(_request(options={"output_path": "other.vmd"}), token)

        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(backend.discover_calls, 3)
        self.assertEqual(revisions.arm_calls, 1)

    def test_validate_token_rejects_stale_revision_with_stable_error(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r2"])
        action = _prepare_action(backend, revisions)
        token = action.prepare(_request())

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: scene revision does not match$",
        ):
            action.validate_token(_request(), token)
        self.assertEqual(backend.collect_calls, 1)
        self.assertIsNone(action.active_token)

    def test_validate_token_rejects_copied_payload_fingerprint_tampering(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r1"])
        action = _prepare_action(backend, revisions)
        token = action.prepare(_request())

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: token is not active$",
        ):
            action.validate_token(_request(), replace(token, payload_fingerprint="sha256:stale"))

    def test_invalidate_closes_boundary_and_is_idempotent(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        action = _prepare_action(backend, _Revisions(["r1", "r1", "r1"]))
        token = action.prepare(_request())

        self.assertTrue(action.invalidate(token))
        self.assertIsNone(action.active_token)
        close_calls = backend.close_calls
        self.assertFalse(action.invalidate(token))
        self.assertFalse(action.invalidate())
        self.assertEqual(backend.close_calls, close_calls)

    def test_discarded_token_cannot_be_reused_at_same_revision(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r1"])
        action = _prepare_action(backend, revisions)
        token = action.prepare(_request())
        action.invalidate(token)
        discover_calls = backend.discover_calls
        revision_calls = revisions.current_revision_calls

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: token is not active$",
        ):
            action.validate_token(_request(), token)
        self.assertEqual(backend.discover_calls, discover_calls)
        self.assertEqual(revisions.current_revision_calls, revision_calls)

    def test_validate_token_rejects_tampered_staged_artifact(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        action = _prepare_action(backend, _Revisions(["r1", "r1", "r1"]))
        token = action.prepare(_request())
        stage_path = Path(token.staged_artifact.file_path)
        stage_path.write_bytes(stage_path.read_bytes() + b"tamper")

        with self.assertRaisesRegex(ValueError, "staged artifact identity is invalid"):
            action.validate_token(_request(), token)
        self.assertIsNone(action.active_token)
        self.assertFalse(stage_path.exists())


if __name__ == "__main__":
    unittest.main()
