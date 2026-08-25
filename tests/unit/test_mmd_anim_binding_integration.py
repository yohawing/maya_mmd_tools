"""Action-level tests for the explicit mmd-anim binding validation opt-in."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
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


def _blocking_binding_report():
    """Return a catalog-registered blocking report for failure-path tests."""
    return ExportValidationReport(
        "pmx",
        (
            ExportValidationIssue(
                "EXTERNAL_TOOL_FAILED",
                "fatal",
                True,
                "binding.runtime",
                "binding failed",
                details={"tool": "mmd-anim-binding", "phase": "evaluate"},
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
        self.assertEqual(result.validation_report.issues[-1].code, "EXTERNAL_TOOL_FAILED")

if __name__ == "__main__":
    unittest.main()
