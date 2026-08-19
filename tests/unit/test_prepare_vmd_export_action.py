"""Unit coverage for the immutable Mode C VMD preparation seam."""

from dataclasses import FrozenInstanceError, replace
import unittest

from mmd_tools.actions.prepare_vmd_export_action import (
    PrepareVmdExportAction,
    PrepareVmdExportRequest,
    VmdExportDiscovery,
    request_fingerprint,
)
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame


class _Backend:
    def __init__(self, discoveries):
        self.discoveries = list(discoveries)
        self.discover_calls = 0
        self.collect_calls = 0

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
        data.bone_frames.append(frame)
        return data


class _Revisions:
    def __init__(self, revisions):
        self.revisions = iter(revisions)
        self.arm_calls = 0

    def arm(self, request, discovery):
        del request, discovery
        self.arm_calls += 1

    def current_revision(self, request, discovery):
        del request, discovery
        return next(self.revisions)


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

        result = PrepareVmdExportAction(backend, revisions).execute(_request())

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

        mutable = result.token.copy_for_export()
        mutable.bone_frames[0].frame_number = 99
        self.assertEqual(result.token.payload.bone_frames[0].frame_number, 4)

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

    def test_validate_token_rejects_payload_fingerprint_tampering(self):
        backend = _Backend([_discovery(), _discovery(), _discovery()])
        revisions = _Revisions(["r1", "r1", "r1"])
        action = PrepareVmdExportAction(backend, revisions)
        token = action.prepare(_request())

        with self.assertRaisesRegex(
            ValueError,
            r"^prepared VMD export token is stale: payload fingerprint does not match$",
        ):
            action.validate_token(_request(), replace(token, payload_fingerprint="sha256:stale"))


if __name__ == "__main__":
    unittest.main()
