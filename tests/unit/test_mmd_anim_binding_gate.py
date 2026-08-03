"""Headless contract tests for the binding integration evidence runner."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport
from tools.mmd_anim_binding_gate import run_gate


class MmdAnimBindingGateTest(unittest.TestCase):
    def test_gate_writes_pass_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.pmx"
            motion = root / "motion.vmd"
            output = root / "build" / "binding.json"
            model.write_bytes(b"pmx")
            motion.write_bytes(b"vmd")
            with patch(
                "tools.mmd_anim_binding_gate.verify_mmd_anim_binding_asset",
                return_value=ExportValidationReport("pmx", (), mode="binding"),
            ) as verifier:
                status = run_gate(
                    model=model,
                    motion=motion,
                    binding_root=root / "binding",
                    runtime_library=root / "runtime.dll",
                    frame=12.0,
                    output=output,
                )

            self.assertEqual(status, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            verifier.assert_called_once_with(
                str(model),
                motion_path=str(motion),
                binding_root=str(root / "binding"),
                runtime_library=str(root / "runtime.dll"),
                frame=12.0,
            )

    def test_gate_returns_fail_for_blocking_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.pmx"
            model.write_bytes(b"pmx")
            output = root / "build" / "binding.json"
            report = ExportValidationReport(
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
            with patch(
                "tools.mmd_anim_binding_gate.verify_mmd_anim_binding_asset",
                return_value=report,
            ):
                status = run_gate(model=model, motion=None, output=output)

            self.assertEqual(status, 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "fail")


if __name__ == "__main__":
    unittest.main()
