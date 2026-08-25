"""Headless tests for the opt-in MMD-Anim CLI validation adapter."""

import json
import subprocess
import unittest

from mmd_tools.validation.mmd_anim_verifier import verify_mmd_anim_asset


def _model_data():
    """Return counts matching the fake MMD-Anim roundtrip report."""
    return {
        "vertices": [{}, {}, {}],
        "faces": [[0, 1, 2]],
        "materials": [],
        "bones": None,
        "morphs": [],
        "rigid_bodies": [],
        "joints": [],
    }


class _FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _completed(payload, returncode=0):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(["mmd-anim"], returncode, stdout=stdout, stderr="")


class MmdAnimVerifierTests(unittest.TestCase):
    """Verify subprocess failures and machine-report normalization."""

    def test_inspect_and_roundtrip_success_is_valid(self):
        runner = _FakeRunner(
            [
                _completed({"diagnostics": []}),
                _completed(
                    {
                        "status": "ok",
                        "counts": {
                            "vertices": 3,
                            "faces": 1,
                            "materials": 1,
                            "bones": 1,
                            "morphs": 0,
                            "rigidBodies": 0,
                            "joints": 0,
                        },
                    }
                ),
            ]
        )

        report = verify_mmd_anim_asset(
            "fixture.pmx",
            model_data=_model_data(),
            cli_path="mmd-anim-test",
            runner=runner,
        )

        self.assertTrue(report.valid)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.calls[0][0], ["mmd-anim-test", "inspect", "fixture.pmx", "--json"])
        self.assertEqual(runner.calls[1][0], ["mmd-anim-test", "roundtrip", "fixture.pmx", "--json"])

    def test_command_failure_is_blocking(self):
        runner = _FakeRunner([_completed({"error": "bad"}, returncode=1)])

        report = verify_mmd_anim_asset("fixture.pmx", runner=runner)

        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")

    def test_timeout_and_unavailable_cli_are_blocking(self):
        timeout_runner = _FakeRunner([subprocess.TimeoutExpired("mmd-anim", 1.0)])
        missing_runner = _FakeRunner([FileNotFoundError("missing")])

        timeout_report = verify_mmd_anim_asset("fixture.pmx", runner=timeout_runner)
        missing_report = verify_mmd_anim_asset("fixture.pmx", runner=missing_runner)

        self.assertEqual(timeout_report.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertEqual(missing_report.issues[0].code, "EXTERNAL_TOOL_FAILED")

    def test_invalid_json_and_diagnostics_are_blocking(self):
        invalid_runner = _FakeRunner([_completed("not-json")])
        diagnostic_runner = _FakeRunner(
            [
                _completed({"diagnostics": [{"code": "TRAILING_BYTES"}]}),
                _completed({"status": "ok", "counts": {}}),
            ]
        )

        invalid_report = verify_mmd_anim_asset("fixture.pmx", runner=invalid_runner)
        diagnostic_report = verify_mmd_anim_asset("fixture.pmx", runner=diagnostic_runner)

        self.assertEqual(invalid_report.issues[0].code, "EXTERNAL_TOOL_FAILED")
        self.assertEqual(diagnostic_report.issues[0].code, "EXTERNAL_TOOL_FAILED")

    def test_roundtrip_status_and_count_mismatch_are_blocking(self):
        runner = _FakeRunner(
            [
                _completed({"diagnostics": []}),
                _completed({"status": "failed", "counts": {"vertices": 2}}),
            ]
        )

        report = verify_mmd_anim_asset("fixture.pmx", model_data=_model_data(), runner=runner)

        self.assertEqual(
            [issue.code for issue in report.issues],
            ["EXTERNAL_TOOL_FAILED", "EXTERNAL_TOOL_FAILED"],
        )

    def test_roundtrip_invalid_json_uses_roundtrip_code(self):
        runner = _FakeRunner([_completed({"diagnostics": []}), _completed("not-json")])

        report = verify_mmd_anim_asset("fixture.pmx", runner=runner)

        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")

    def test_roundtrip_without_counts_is_invalid(self):
        runner = _FakeRunner([_completed({"diagnostics": []}), _completed({"status": "ok"})])

        report = verify_mmd_anim_asset("fixture.pmx", runner=runner)

        self.assertEqual(report.issues[0].code, "EXTERNAL_TOOL_FAILED")


if __name__ == "__main__":
    unittest.main()
