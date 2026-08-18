"""Mock orchestration contracts for the Authoring cross-Maya Nox session."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools import authoring_cross_maya_gate as gate
from tools.nox.authoring_sessions import (
    HEADLESS_QT_REQUIREMENT,
    run_authoring_cross_maya_matrix,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


class _Session:
    def __init__(self):
        self.messages = []

    def error(self, message):
        raise RuntimeError(message)

    def log(self, message):
        self.messages.append(message)


class _Harness:
    def __init__(self, root, matrix, monkeypatch, dirty_paths=(), ending_source=None):
        self.root = root
        self.matrix = matrix
        self.commands = []
        self.build_events = []
        self.gui_runs = []
        self.mayapy_runs = []
        self.fail_command = None
        self.omit_gui_timing = False
        self.headless_subset = False
        self.session = _Session()
        self.python = root / "python.exe"
        self.python.write_bytes(b"python")
        self.mayapy_paths = {}
        ui_manifest = root / matrix["source_manifests"]["ui_surfaces"]
        ui_manifest.parent.mkdir(parents=True, exist_ok=True)
        ui_manifest.write_bytes(
            (REPO_ROOT / matrix["source_manifests"]["ui_surfaces"]).read_bytes()
        )
        monkeypatch.setattr(gate, "load_matrix", lambda _root: matrix)
        starting_source = {"head": "a" * 40, "dirty_paths": list(dirty_paths)}
        self.source_identities = [starting_source, ending_source or starting_source]
        self.source_calls = 0

        def source_identity(_root):
            index = min(self.source_calls, len(self.source_identities) - 1)
            self.source_calls += 1
            return self.source_identities[index]

        monkeypatch.setattr(gate, "source_identity", source_identity)

    def configure(self, _session, version, config):
        self.build_events.append(("configure", version, config))

    def build(self, _session, version, config, clean_first=False):
        self.build_events.append(("build", version, config, clean_first))
        plugin = self.root / "plug-ins" / version / config / "mmd_tools_cpp.mll"
        plugin.parent.mkdir(parents=True, exist_ok=True)
        plugin.write_bytes(("plugin-" + version).encode("ascii"))

    def mayapy(self, version):
        path = self.mayapy_paths.get(version)
        if path is None:
            path = self.root / "maya" / version / "bin" / "mayapy.exe"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"mayapy")
            self.mayapy_paths[version] = path
        return path

    @staticmethod
    def mayapy_env(_path, **values):
        return dict(values)

    @staticmethod
    def mayapy_script(_path, script):
        return script

    def run_logged(self, command, *, log_path, cwd, env=None, verbose=False):
        del cwd, verbose
        command = [str(value) for value in command]
        self.commands.append({"command": command, "env": dict(env or {})})
        log_path = Path(log_path).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("PASS {}\n".format(" ".join(command)), encoding="utf-8")
        if self.fail_command and self.fail_command in command:
            return 1, log_path, (0, 0)

        if "pytest" in command:
            junit = next(value.split("=", 1)[1] for value in command if value.startswith("--junitxml="))
            identities = gate.expected_headless_test_identities(self.root, self.matrix)
            if self.headless_subset:
                identities = identities[:1]
            suites = ET.Element("testsuites", {"name": "pytest tests"})
            suite = ET.SubElement(
                suites,
                "testsuite",
                {
                    "name": "pytest",
                    "tests": str(len(identities)),
                    "errors": "0",
                    "failures": "0",
                    "skipped": "0",
                },
            )
            for classname, name in identities:
                ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
            ET.ElementTree(suites).write(junit, encoding="utf-8", xml_declaration=True)
        elif "tests/run_gui_tests.py" in command:
            version = command[command.index("--maya_version") + 1]
            self.gui_runs.append(version)
            gui_log = Path(command[command.index("--log_path") + 1])
            timing = Path(command[command.index("--timing_report") + 1])
            batch = Path(command[command.index("--batch_manifest") + 1])
            gui_log.write_text(
                "Ran GUI representatives\n//-- GUI TEST FINISHED --// status=PASS\n",
                encoding="utf-8",
            )
            if not self.omit_gui_timing:
                batch_payload = json.loads(batch.read_text(encoding="utf-8"))
                matrix_cases = {case["id"]: case for case in self.matrix["cases"]}
                cases = []
                for selected in batch_payload["cases"]:
                    case = matrix_cases[selected["id"]]
                    cases.append(
                        {
                            "id": case["id"],
                            "status": "PASS",
                            "test_counts": {"success": 1},
                            "tests": [{"id": case["test_id"], "status": "success"}],
                        }
                    )
                timing.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "runner": "maya_gui_batch",
                            "maya_version": version,
                            "status": "PASS",
                            "case_counts": {"PASS": len(cases)},
                            "cases": cases,
                        }
                    ),
                    encoding="utf-8",
                )
        elif command[0].lower().endswith("mayapy.exe"):
            self.mayapy_runs.append((command[0], command[1]))
        return 0, log_path, (0, 0)

    def run(self, posargs):
        return run_authoring_cross_maya_matrix(
            self.session,
            posargs=posargs,
            root=self.root,
            python_executable=str(self.python),
            configure=self.configure,
            build=self.build,
            mayapy=self.mayapy,
            mayapy_env=self.mayapy_env,
            mayapy_script=self.mayapy_script,
            run_logged=self.run_logged,
        )


@pytest.fixture
def matrix():
    return gate.load_matrix(REPO_ROOT)


def test_focused_uses_uvx_pytest_when_nox_interpreter_has_no_pytest(
    tmp_path, monkeypatch, matrix
):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    harness.python = tmp_path / "nox-python-without-pytest.exe"
    harness.python.write_bytes(b"no pytest installed")
    report_path = harness.run(
        ["--profile", "focused", "--domain", "info", "--out-dir", "reports"]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["selection"]["versions"] == ["2024"]
    assert report["headless"]["test_count"] == 230
    assert report["headless"]["surface_test_count"] == 229
    assert len(report["headless"]["test_identities"]) == 230
    assert report["source_verified_at_end"] == report["source"]
    assert harness.gui_runs == ["2024"]
    assert harness.build_events == []
    assert harness.mayapy_runs == []
    assert sum("pytest" in entry["command"] for entry in harness.commands) == 1
    assert sum("tests/run_gui_tests.py" in entry["command"] for entry in harness.commands) == 1
    headless = next(entry["command"] for entry in harness.commands if "pytest" in entry["command"])
    assert headless == [
        "uvx",
        "--with",
        "pytest",
        "--with",
        "PySide6==6.11.0",
        "--",
        "python",
        "-m",
        "pytest",
        "tests/unit/test_authoring_ui_surface_matrix.py",
        "-q",
        "--junitxml={}".format(tmp_path / "reports" / "headless-ui-surface-matrix.xml"),
    ]
    assert HEADLESS_QT_REQUIREMENT == "PySide6==6.11.0"


def test_sensitive_native_lane_is_sequential_dual_version_with_exact_build_and_processes(
    tmp_path, monkeypatch, matrix
):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    report_path = harness.run(
        [
            "--profile",
            "focused",
            "--domain",
            "material",
            "--change-kind",
            "native",
            "--out-dir",
            "reports",
        ]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["selection"]["effective_profile"] == "version_sensitive"
    assert harness.gui_runs == ["2024", "2026"]
    assert harness.build_events == [
        ("configure", "2024", "Debug"),
        ("build", "2024", "Debug", True),
        ("configure", "2026", "Debug"),
        ("build", "2026", "Debug", True),
    ]
    expected_scripts = [
        case["script"]
        for case in gate.build_plan(matrix, "focused", ("material",), ("native",))["cases"]
        if case["runner"] == "mayapy"
    ]
    assert [script for _exe, script in harness.mayapy_runs] == expected_scripts * 2
    assert len({id(run) for run in harness.mayapy_runs}) == len(harness.mayapy_runs)
    assert [entry["maya_version"] for entry in report["versions"]] == ["2024", "2026"]
    for evidence in report["versions"]:
        assert evidence["native_plugin"]["config"] == "Debug"
        assert len(evidence["native_plugin"]["sha256"]) == 64
        assert evidence["native_build"]["log"]["sha256"]
        assert evidence["native_build"]["report"]["sha256"]
        assert evidence["gui"]["timing_report"]["sha256"]


def test_release_candidate_rejects_dirty_worktree_before_any_command(
    tmp_path, monkeypatch, matrix
):
    harness = _Harness(tmp_path, matrix, monkeypatch, dirty_paths=("dirty.py",))
    stale_aggregate = tmp_path / "reports" / "authoring-cross-maya-report.json"
    stale_aggregate.parent.mkdir(parents=True)
    stale_aggregate.write_text('{"status":"pass","stale":true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean worktree"):
        harness.run(["--profile", "release_candidate", "--out-dir", "reports"])
    assert harness.commands == []
    assert harness.build_events == []
    assert not stale_aggregate.exists()


def test_each_standalone_case_is_a_separate_mayapy_command(tmp_path, monkeypatch, matrix):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    harness.run(
        [
            "--profile",
            "focused",
            "--domain",
            "morph",
            "--out-dir",
            "reports",
        ]
    )
    selected = gate.build_plan(matrix, "focused", ("morph",), ())["cases"]
    expected = [case["script"] for case in selected if case["runner"] == "mayapy"]
    assert [script for _exe, script in harness.mayapy_runs] == expected
    assert all(len(entry["command"]) == 2 for entry in harness.commands if entry["command"][0].lower().endswith("mayapy.exe"))


def test_failed_child_command_stops_without_pass_report(tmp_path, monkeypatch, matrix):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    harness.fail_command = "tests/run_gui_tests.py"
    old_report = tmp_path / "reports" / "authoring-cross-maya-report.json"
    old_report.parent.mkdir(parents=True)
    old_report.write_text('{"status":"pass","stale":true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="command failed"):
        harness.run(["--profile", "focused", "--domain", "info", "--out-dir", "reports"])
    assert not old_report.exists()


def test_missing_gui_timing_artifact_fails_closed(tmp_path, monkeypatch, matrix):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    harness.omit_gui_timing = True
    with pytest.raises(gate.CrossMayaGateError, match="invalid JSON artifact"):
        harness.run(["--profile", "focused", "--domain", "info", "--out-dir", "reports"])


def test_rc_zero_headless_subset_cannot_produce_aggregate_pass(tmp_path, monkeypatch, matrix):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    harness.headless_subset = True
    with pytest.raises(gate.CrossMayaGateError, match="230 PASS"):
        harness.run(["--profile", "focused", "--domain", "info", "--out-dir", "reports"])
    assert not (tmp_path / "reports" / "authoring-cross-maya-report.json").exists()


def test_source_drift_before_aggregate_pass_fails_closed(tmp_path, monkeypatch, matrix):
    ending_source = {"head": "b" * 40, "dirty_paths": ["changed.py"]}
    harness = _Harness(tmp_path, matrix, monkeypatch, ending_source=ending_source)
    with pytest.raises(gate.CrossMayaGateError, match="source identity changed"):
        harness.run(["--profile", "focused", "--domain", "info", "--out-dir", "reports"])
    assert harness.source_calls == 2
    assert not (tmp_path / "reports" / "authoring-cross-maya-report.json").exists()


def test_out_dir_cannot_escape_repository(tmp_path, monkeypatch, matrix):
    harness = _Harness(tmp_path, matrix, monkeypatch)
    with pytest.raises(gate.CrossMayaGateError, match="inside the repository"):
        harness.run(
            [
                "--profile",
                "focused",
                "--domain",
                "info",
                "--out-dir",
                str(tmp_path.parent / "escape"),
            ]
        )


def test_noxfile_registers_only_the_thin_authoring_session_boundary():
    source = (REPO_ROOT / "noxfile.py").read_text(encoding="utf-8")
    assert "def authoring_cross_maya_matrix(session: nox.Session)" in source
    assert "_run_authoring_cross_maya_matrix(" in source
    for callback in (
        "configure=_cmake_configure",
        "build=_cmake_build",
        "mayapy=_mayapy",
        "mayapy_env=_mayapy_env",
        "mayapy_script=_mayapy_script",
        "run_logged=_run_logged_subprocess",
    ):
        assert callback in source
