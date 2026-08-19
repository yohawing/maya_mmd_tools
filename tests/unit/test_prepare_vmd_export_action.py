"""Unit coverage for the immutable Mode C VMD preparation seam."""

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mmd_tools.actions.prepare_vmd_export_action import (
    PrepareVmdExportAction,
    PrepareVmdExportRequest,
    VmdExportDiscovery,
    request_fingerprint,
)
from mmd_tools.actions.prepared_vmd_artifact import PreparedVmdArtifactReceipt
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

    def supports_streaming(self):
        return True

    def collect_to_sink(self, request, sink):
        del request
        self.collect_calls += 1
        sink.write_frame(
            "bones",
            {
                "bone_name": "center",
                "frame": 4,
                "position": (1.0, 2.0, 3.0),
                "rotation": (0.0, 0.0, 0.0, 1.0),
            },
        )
        return {
            "validation_frame_range": (0, 30),
            "section_counts": {
                "bones": 1,
                "morphs": 0,
                "cameras": 0,
                "lights": 0,
                "shadows": 0,
                "ik": 0,
            },
        }

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


class _StreamingSession:
    instances = []

    def __init__(self, model_name, *, mode, output_verifier, expected_frame_range=None):
        self.model_name = model_name
        self.mode = mode
        self.output_verifier = output_verifier
        self.expected_frame_range = expected_frame_range
        self.finished = False
        self.cleaned = False
        self.promote_warning = None
        self.directory = Path(tempfile.mkdtemp(prefix="mmd-test-stream-"))
        self.path = self.directory / "prepared.vmd"
        self.path.write_bytes(b"stream-stage")
        type(self).instances.append(self)

    def begin_section(self, section):
        del section

    def write_frame(self, section, frame):
        del section, frame

    def set_expected_frame_range(self, frame_range):
        self.expected_frame_range = frame_range

    def finish_collection(self):
        self.finished = True
        return SimpleNamespace(
            counts={
                "bones": 0,
                "morphs": 0,
                "cameras": 0,
                "lights": 0,
                "shadows": 0,
                "ik": 0,
            }
        )

    def promote(self, *, raw_loss_warning_required=False):
        self.promote_warning = raw_loss_warning_required
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        report = ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "RAW_LOSS_WARNING",
                    "warning",
                    False,
                    "output",
                    "raw provenance was omitted",
                ),
            ),
            mode="C",
        )
        return PreparedVmdArtifactReceipt(
            schema_version=1,
            stage_directory=str(self.directory),
            file_path=str(self.path),
            sha256=digest,
            size=self.path.stat().st_size,
            section_counts={
                "bone_frames": 1,
                "morph_frames": 0,
                "camera_frames": 0,
                "light_frames": 0,
                "shadow_frames": 0,
                "ik_show_hide_frames": 0,
            },
            frame_bounds=(4, 4),
            output_validation_report=report,
        )

    def cleanup(self):
        self.cleaned = True
        shutil.rmtree(self.directory, ignore_errors=True)


class _StreamingBackend(_Backend):
    def __init__(self, discoveries, *, metadata=None, fail=False):
        super().__init__(discoveries)
        self.metadata = (
            {
                "validation_frame_range": (4, 8),
                "section_counts": {
                    "bones": 0,
                    "morphs": 0,
                    "cameras": 0,
                    "lights": 0,
                    "shadows": 0,
                    "ik": 0,
                },
            }
            if metadata is None
            else metadata
        )
        self.fail = fail
        self.stream_calls = 0

    def supports_streaming(self):
        return True

    def collect_to_sink(self, request, sink):
        del request
        self.stream_calls += 1
        if self.fail:
            raise RuntimeError("sink failed")
        for section in ("bones", "morphs", "cameras", "lights", "shadows", "ik"):
            sink.begin_section(section)
        return dict(self.metadata)

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
    def test_streaming_prepare_uses_sink_and_retains_warning(self):
        _StreamingSession.instances.clear()
        backend = _StreamingBackend(
            [_discovery(model_name="stream-model"), _discovery(model_name="stream-model")],
            metadata={
                "validation_frame_range": (4, 8),
                "section_counts": {
                    "bones": 0,
                    "morphs": 0,
                    "cameras": 0,
                    "lights": 0,
                    "shadows": 0,
                    "ik": 0,
                },
                "raw_provenance": {"source": "fixture"},
            },
        )
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r1"]))

        with patch(
            "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
            _StreamingSession,
        ):
            result = action.execute(_request(options={"ack_warnings": True}))

        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(backend.stream_calls, 1)
        self.assertEqual(backend.collect_calls, 0)
        session = _StreamingSession.instances[0]
        self.assertEqual(session.model_name, "stream-model")
        self.assertEqual(session.expected_frame_range, (4, 8))
        self.assertTrue(session.finished)
        self.assertTrue(session.promote_warning)
        self.assertTrue(result.token.validation_report.requires_warning_ack)
        self.assertEqual(
            result.token.combined_validation_report,
            result.token.staged_artifact.output_validation_report,
        )
        action.invalidate()

    def test_streaming_sink_failure_cleans_pending_session(self):
        _StreamingSession.instances.clear()
        backend = _StreamingBackend(
            [_discovery(model_name="stream-model"), _discovery(model_name="stream-model")],
            fail=True,
        )
        action = PrepareVmdExportAction(backend, _Revisions(["r1"]))
        with patch(
            "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
            _StreamingSession,
        ):
            result = action.execute(_request())
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertTrue(_StreamingSession.instances[0].cleaned)

    def test_streaming_revision_race_cleans_pending_session(self):
        _StreamingSession.instances.clear()
        backend = _StreamingBackend(
            [_discovery(model_name="stream-model"), _discovery(model_name="stream-model")]
        )
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r2"]))
        with patch(
            "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
            _StreamingSession,
        ):
            result = action.execute(_request())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.token)
        self.assertTrue(_StreamingSession.instances[0].cleaned)

    def test_streaming_keyboard_interrupt_cleans_stage_and_is_preserved(self):
        class CancelBackend(_StreamingBackend):
            def collect_to_sink(self, request, sink):
                del request, sink
                raise KeyboardInterrupt("cancelled")

        _StreamingSession.instances.clear()
        backend = CancelBackend([_discovery(model_name="stream-model")])
        action = PrepareVmdExportAction(backend, _Revisions(["r1"]))
        with patch(
            "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
            _StreamingSession,
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "cancelled"):
                action.execute(_request())

        self.assertTrue(_StreamingSession.instances[0].cleaned)

    def test_streaming_prepare_rejects_missing_or_malformed_bounded_metadata(self):
        malformed = (
            {},
            {"validation_frame_range": (0, 1)},
            {
                "validation_frame_range": (True, 1),
                "section_counts": {},
            },
            {
                "validation_frame_range": (0, 0x1_0000_0000),
                "section_counts": {},
            },
            {
                "validation_frame_range": (4, 8),
                "section_counts": {"bones": 1},
            },
        )
        for metadata in malformed:
            with self.subTest(metadata=metadata):
                _StreamingSession.instances.clear()
                backend = _StreamingBackend(
                    [
                        _discovery(model_name="stream-model"),
                        _discovery(model_name="stream-model"),
                    ],
                    metadata=metadata,
                )
                action = PrepareVmdExportAction(backend, _Revisions(["r1"]))
                with patch(
                    "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
                    _StreamingSession,
                ):
                    result = action.execute(_request())
                self.assertEqual(result.status, "failed")
                self.assertIsNone(result.token)
                self.assertTrue(_StreamingSession.instances[0].cleaned)

    def test_streaming_identity_failure_cleans_promoted_receipt_stage(self):
        class TamperedSession(_StreamingSession):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.promoted = False

            def promote(self, **kwargs):
                receipt = super().promote(**kwargs)
                self.promoted = True
                Path(receipt.file_path).unlink()
                return receipt

            def cleanup(self):
                if not self.promoted:
                    super().cleanup()

        TamperedSession.instances.clear()
        backend = _StreamingBackend(
            [_discovery(model_name="stream-model"), _discovery(model_name="stream-model")]
        )
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r1"]))
        with patch(
            "mmd_tools.actions.prepare_vmd_export_action.PreparedVmdStageSession",
            TamperedSession,
        ):
            result = action.execute(_request())

        self.assertEqual(result.status, "failed")
        self.assertFalse(TamperedSession.instances[0].directory.exists())

    def test_legacy_backend_is_rejected_at_construction(self):
        class LegacyBackend:
            def discover(self, request):
                del request

            def collect(self, request):
                del request

        with self.assertRaisesRegex(TypeError, "supports_streaming"):
            PrepareVmdExportAction(LegacyBackend(), _Revisions(["r1"]))

        class DisabledBackend(_Backend):
            def supports_streaming(self):
                return False

        with self.assertRaisesRegex(TypeError, "must support streaming"):
            PrepareVmdExportAction(
                DisabledBackend([_discovery()]),
                _Revisions(["r1"]),
            )

    def test_collects_once_and_publishes_immutable_token(self):
        backend = _Backend([_discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1"])

        action = PrepareVmdExportAction(backend, revisions)
        result = action.execute(_request())

        self.assertTrue(result.succeeded)
        self.assertEqual(backend.discover_calls, 2)
        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(revisions.arm_calls, 1)
        self.assertEqual(result.token.mode, "C")
        self.assertEqual(result.token.revision, "r1")
        with self.assertRaises(FrozenInstanceError):
            result.token.revision = "r2"
        self.assertFalse(hasattr(result.token, "prepared_payload"))
        self.assertFalse(hasattr(result.token, "copy_for_export"))
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

    def test_diagnostics_keep_prepare_phase_evidence_on_success_and_failure(self):
        backend = _Backend([_discovery(), _discovery()])
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r1"]))

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

        failing = PrepareVmdExportAction(_Backend([_discovery()]), _Revisions([None]))
        failure = failing.execute(_request())
        self.assertEqual(failure.status, "failed")
        self.assertEqual(failing.diagnostics.status, "failed")
        self.assertIn("revision_before", failing.diagnostics.error)
        self.assertIn("total", failing.diagnostics.phase_timing)

        self.assertFalse(hasattr(result.token, "prepared_payload"))

    def test_revision_race_is_partial_and_never_publishes(self):
        backend = _Backend([_discovery(), _discovery()])
        result = PrepareVmdExportAction(backend, _Revisions(["r1", "r2"])).execute(_request())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.token)
        self.assertIn("revision changed", str(result.error))

    def test_dependency_closure_change_is_partial_and_never_publishes(self):
        backend = _Backend([_discovery(), _discovery(dependency_closure_fingerprint="sha256:deps-2")])
        result = PrepareVmdExportAction(backend, _Revisions(["r1", "r1"])).execute(_request())

        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.token)
        self.assertIn("closure changed", str(result.error))

    def test_missing_revision_fails_before_collection(self):
        backend = _Backend([_discovery()])
        result = PrepareVmdExportAction(backend, _Revisions([None])).execute(_request())

        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertEqual(backend.collect_calls, 0)
        self.assertIn("revision_before", str(result.error))

    def test_non_mode_c_is_rejected_before_discovery(self):
        backend = _Backend([_discovery()])
        result = PrepareVmdExportAction(backend, _Revisions(["r1"])).execute(_request(mode="A"))

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
        action = PrepareVmdExportAction(backend, revisions)
        token = action.prepare(_request())

        action.validate_token(_request(options={"output_path": "other.vmd"}), token)

        self.assertEqual(backend.collect_calls, 1)
        self.assertEqual(backend.discover_calls, 3)
        self.assertEqual(revisions.arm_calls, 1)

    def test_validate_token_rejects_stale_revision_with_stable_error(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r2"])
        action = PrepareVmdExportAction(backend, revisions)
        token = action.prepare(_request())

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: scene revision does not match$",
        ):
            action.validate_token(_request(), token)
        self.assertEqual(backend.collect_calls, 1)
        self.assertIsNone(action.active_token)

    def test_validate_token_rejects_receipt_payload_fingerprint_tampering(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r1"])
        action = PrepareVmdExportAction(backend, revisions)
        token = action.prepare(_request())
        tampered = replace(token, payload_fingerprint="sha256:stale")
        action._active_token = tampered

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: payload fingerprint does not match staged artifact$",
        ):
            action.validate_token(_request(), tampered)

    def test_invalidate_closes_boundary_and_is_idempotent(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r1", "r1"]))
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
        action = PrepareVmdExportAction(backend, revisions)
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
        action = PrepareVmdExportAction(backend, _Revisions(["r1", "r1", "r1"]))
        token = action.prepare(_request())
        stage_path = Path(token.staged_artifact.file_path)
        stage_path.write_bytes(stage_path.read_bytes() + b"tamper")

        with self.assertRaisesRegex(ValueError, "staged artifact identity is invalid"):
            action.validate_token(_request(), token)
        self.assertIsNone(action.active_token)
        self.assertFalse(stage_path.exists())


if __name__ == "__main__":
    unittest.main()
