"""Focused tests for HumanIK Maya session delegation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from noxlib.common import _option
from noxlib.maya_sessions import (
    run_humanik_citlali_stance_smoke,
    run_humanik_definition_smoke,
    run_humanik_roundtrip_smoke,
    run_humanik_vmd_parity_smoke,
)


class _FakeSession:
    def __init__(self, posargs=None):
        self.posargs = list(posargs or [])
        self.runs = []
        self.logs = []

    def run(self, *args, **kwargs):
        self.runs.append((args, kwargs))

    def log(self, message):
        self.logs.append(message)

    def error(self, message):
        raise AssertionError(message)


class HumanikSessionsTest(unittest.TestCase):
    def test_definition_smoke_keeps_control_rig_flag_and_path_conversion(self):
        session = _FakeSession(["--maya", "2024", "--create-control-rig", "--out", "build/hik.json"])
        mayapy = mock.Mock()
        run_humanik_definition_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            mayapy=lambda _version: mayapy,
            probe_passthrough=lambda args, _values, _flags: args[2:],
            convert_mayapy_path_options=lambda _mayapy, args, _options: [*args, "converted"],
            mayapy_script=lambda _mayapy, script: script,
            mayapy_env=lambda _mayapy: {"MAYA_VERSION": "2024"},
        )
        args, kwargs = session.runs[0]
        self.assertEqual(args[1], "tests/viewport/humanik_definition_smoke.py")
        self.assertIn("--create-control-rig", args)
        self.assertEqual(args[-1], "converted")
        self.assertTrue(kwargs["external"])

    def test_roundtrip_smoke_runs_default_matrix_and_validates_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "roundtrip.json"
            for mode in ("off", "serial", "parallel"):
                report = out.with_name(f"{out.stem}.{mode}{out.suffix}")
                report.write_text(json.dumps({"evaluationMode": mode, "status": "pass"}), encoding="utf-8")
            session = _FakeSession(["--out", str(out)])
            calls = []
            run_humanik_roundtrip_smoke(
                session,
                posargs=session.posargs,
                option=_option,
                mayapy=lambda version: f"mayapy-{version}",
                root=root,
                probe_passthrough=lambda _args, _values: [],
                clear_probe_report=lambda *_args: None,
                run_mayapy_probe=lambda *args, **kwargs: calls.append((args, kwargs)),
            )
        self.assertEqual(len(calls), 3)
        self.assertEqual([call[0][2] for call in calls], ["tests/viewport/humanik_roundtrip_smoke.py"] * 3)
        self.assertTrue(all(call[1]["success_codes"] == (0, 1) for call in calls))

    def test_vmd_parity_allows_stop_as_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "parity.json"
            for mode in ("off", "serial", "parallel"):
                report = out.with_name(f"{out.stem}.{mode}{out.suffix}")
                report.write_text(json.dumps({"status": "stop"}), encoding="utf-8")
            session = _FakeSession(["--out", str(out), "--allow-stop"])
            run_humanik_vmd_parity_smoke(
                session,
                posargs=session.posargs,
                option=_option,
                mayapy=lambda version: f"mayapy-{version}",
                root=root,
                probe_passthrough=lambda _args, _values, _flags: [],
                clear_probe_report=lambda *_args: None,
                run_mayapy_probe=lambda *_args, **_kwargs: None,
            )
        self.assertEqual(len(session.logs), 1)
        self.assertIn("evidence captured", session.logs[0])

    def test_citlali_gate_checks_restore_evidence(self):
        session = _FakeSession(["--maya", "2024"])
        run_humanik_citlali_stance_smoke(
            session,
            posargs=session.posargs,
            option=_option,
            mayapy=lambda version: f"mayapy-{version}",
            root=Path("F:/repo"),
            clear_probe_report=lambda *_args: None,
            run_mayapy_probe=lambda *_args, **_kwargs: None,
            read_probe_report=lambda *_args: {
                "status": "pass",
                "stance": {
                    "restore": {
                        "passed": True,
                        "topologyRestored": True,
                        "maxRotateResidual": 0.0,
                        "maxJointOrientResidual": 0.0,
                        "maxSkinMatrixResidual": 0.0,
                        "maxAllSkinMatrixResidual": 0.0,
                        "tolerance": 1.0e-4,
                    }
                },
                "transformDiffs": [],
            },
        )
        self.assertEqual(session.runs, [])


if __name__ == "__main__":
    unittest.main()
