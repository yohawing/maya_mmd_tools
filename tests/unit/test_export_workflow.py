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
from mmd_tools.validation.export_validator import validate_model_data  # noqa: E402
from mmd_tools.validation.scene_preflight import ScenePreflight  # noqa: E402


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
                "vmd_mode": "C",
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


if __name__ == "__main__":
    unittest.main()
