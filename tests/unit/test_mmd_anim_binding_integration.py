"""Action-level tests for the explicit mmd-anim binding validation opt-in."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport


def _valid_model_data():
    """Return the smallest model payload accepted by the pure model validator."""
    return {
        "model_name": "BindingIntegration",
        "vertices": [
            {"position": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 0.0], "bone_indices": [0]},
            {"position": [1.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [1.0, 0.0], "bone_indices": [0]},
            {"position": [0.0, 1.0, 0.0], "normal": [0.0, 0.0, 1.0], "uv": [0.0, 1.0], "bone_indices": [0]},
        ],
        "faces": [[0, 1, 2]],
        "bones": None,
    }


class _ModelExporter:
    def __init__(self):
        self.calls = []

    def export_pmx_model(self, file_path, model_data):
        self.calls.append((file_path, model_data))
        Path(file_path).write_bytes(b"pmx bytes")


class _VmdExporter:
    def __init__(self):
        self.calls = []

    def export_vmd_animation(self, file_path, animation_data):
        self.calls.append((file_path, animation_data))
        VmdData().write_file(file_path)


def _blocking_binding_report():
    """Return a catalog-registered blocking report for failure-path tests."""
    return ExportValidationReport(
        "pmx",
        (
            ExportValidationIssue(
                "MMD_ANIM_BINDING_RUNTIME_FAILED",
                "fatal",
                True,
                "binding.runtime",
                "binding failed",
            ),
        ),
        mode="binding",
    )


class MmdAnimBindingIntegrationTest(unittest.TestCase):
    def test_model_binding_opt_in_receives_temporary_pmx_and_counts(self):
        exporter = _ModelExporter()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.pmx"
            with patch(
                "mmd_tools.actions.export_model_action.verify_mmd_anim_binding_asset",
                return_value=ExportValidationReport("pmx", (), mode="binding"),
            ) as verifier:
                result = ExportModelAction(
                    pmx_exporter=exporter,
                    collector=None,
                    output_verifier=None,
                ).execute(
                    ExportModelRequest(
                        str(target),
                        {
                            "export_format": "pmx",
                            "model_data": _valid_model_data(),
                            "verify_mmd_anim_binding": True,
                            "mmd_anim_binding_motion_path": "motion.vmd",
                            "mmd_anim_binding_frame": 7,
                        },
                    )
                )

        self.assertTrue(result.succeeded)
        self.assertEqual(verifier.call_count, 1)
        args, kwargs = verifier.call_args
        self.assertEqual(Path(args[0]).suffix, ".pmx")
        self.assertEqual(kwargs["motion_path"], "motion.vmd")
        self.assertEqual(kwargs["frame"], 7)
        self.assertEqual(kwargs["expected_counts"], {"bones": 1})

    def test_model_binding_failure_preserves_existing_target(self):
        exporter = _ModelExporter()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "model.pmx"
            target.write_bytes(b"old")
            with patch(
                "mmd_tools.actions.export_model_action.verify_mmd_anim_binding_asset",
                return_value=_blocking_binding_report(),
            ):
                result = ExportModelAction(
                    pmx_exporter=exporter,
                    collector=None,
                    output_verifier=None,
                ).execute(
                    ExportModelRequest(
                        str(target),
                        {
                            "export_format": "pmx",
                            "model_data": _valid_model_data(),
                            "verify_mmd_anim_binding": True,
                        },
                    )
                )
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(target.parent.glob(".*.pmx")), [])

        self.assertFalse(result.succeeded)
        self.assertEqual(result.validation_report.issues[-1].code, "MMD_ANIM_BINDING_RUNTIME_FAILED")

    def test_vmd_binding_opt_in_passes_model_path_and_temporary_motion(self):
        exporter = _VmdExporter()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "motion.vmd"
            with patch(
                "mmd_tools.actions.export_vmd_action.verify_mmd_anim_binding_asset",
                return_value=ExportValidationReport("pmx", (), mode="binding"),
            ) as verifier:
                result = ExportVmdAction(
                    exporter=exporter,
                    output_verifier=None,
                ).execute(
                    ExportVmdRequest(
                        str(target),
                        {
                            "verify_mmd_anim_binding": True,
                            "mmd_anim_binding_model_path": "model.pmx",
                            "mmd_anim_binding_expected_counts": {"bones": 1},
                        },
                        animation_data=VmdData(),
                    )
                )

        self.assertTrue(result.succeeded)
        args, kwargs = verifier.call_args
        self.assertEqual(args[0], "model.pmx")
        self.assertEqual(Path(kwargs["motion_path"]).suffix, ".vmd")
        self.assertEqual(kwargs["expected_counts"], {"bones": 1})


if __name__ == "__main__":
    unittest.main()
