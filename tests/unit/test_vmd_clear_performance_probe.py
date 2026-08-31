"""Pure-Python checks for the VMD clear-performance probe contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import tools.probes.vmd_clear_performance_probe as probe

from tools.probes.vmd_clear_performance_probe import (
    ProbeConfigurationError,
    SCHEMA_VERSION,
    _Instrumentation,
    _action_diagnostics,
    _apply_git_provenance,
    _apply_mayapy_returncode,
    _apply_mayapy_warnings,
    _git_worktree_provenance,
    _stream_git_status,
    _mayapy_warning_entry,
    load_config,
    summarize_timings,
    threshold_decision,
)


def _run(index: int, clear_ns: int = 300_000_000, total_ns: int = 1_000_000_000) -> dict:
    return {
        "index": index,
        "warmup": False,
        "status": "measured",
        "timings": {
            "route_resolution_wall_ns": 10_000_000,
            "clear_route_resolution_wall_ns": 1_000_000,
            "clear_wall_ns": clear_ns,
            "replacement_total_wall_ns": total_ns,
            "post_route_clear_residual_wall_ns": total_ns - clear_ns - 11_000_000,
        },
    }


def test_threshold_proceeds_when_clear_median_meets_absolute_threshold():
    result = threshold_decision([_run(index) for index in range(3)])

    assert result["status"] == "pass"
    assert result["decision"] == "proceed"
    assert result["median_clear_wall_ns"] == 300_000_000
    assert result["median_clear_total_ratio"] == pytest.approx(0.3)


def test_threshold_proceeds_on_ratio_when_absolute_clear_is_small():
    result = threshold_decision(
        [_run(index, clear_ns=210_000_000, total_ns=1_000_000_000) for index in range(3)]
    )

    assert result["decision"] == "proceed"
    assert result["threshold_met"] is True


def test_threshold_is_fail_closed_for_warnings_and_incomplete_runs():
    result = threshold_decision(
        [_run(index) for index in range(2)] + [{**_run(2), "status": "failed"}],
        warnings=[{"code": "partial"}],
    )

    assert result["status"] == "not_run"
    assert result["decision"] == "no_proceed"
    assert result["threshold_met"] is True
    assert "fewer than 3" in result["reason"]
    assert "warnings" in result["reason"]


def test_threshold_rejects_a_complete_but_below_threshold_measurement():
    result = threshold_decision(
        [_run(index, clear_ns=100_000_000, total_ns=1_000_000_000) for index in range(3)]
    )

    assert result["status"] == "fail"
    assert result["decision"] == "no_proceed"
    assert result["threshold_met"] is False
    assert "threshold was not met" in result["reason"]


def test_summarize_timings_marks_missing_metrics_without_inventing_values():
    result = summarize_timings([_run(0), {"warmup": False, "status": "measured", "timings": {}}])

    assert result["measured_run_count"] == 2
    assert result["valid_timing_count"] == 1
    assert result["median_clear_wall_ns"] == 300_000_000
    assert result["missing_metrics"] == ["run[1].timings"]


def test_adjusted_timing_math_excludes_inventory_overhead_from_total_and_residual():
    instrumentation = _Instrumentation(None)
    instrumentation.route_plan_wall_ns = 20
    instrumentation.clear_route_wall_ns = 10
    instrumentation.clear_wall_ns = 300
    instrumentation.clear_wall_raw_ns = 340
    instrumentation._pre_clear_inventory_overhead_ns = 40
    instrumentation._in_clear_inventory_overhead_ns = 60

    result = instrumentation.timings(1_000)

    assert result["replacement_total_raw_wall_ns"] == 1_000
    assert result["instrumentation_overhead_wall_ns"] == 100
    assert result["replacement_total_wall_ns"] == 900
    assert result["clear_wall_ns"] == 300
    assert result["post_route_clear_residual_wall_ns"] == 570


def test_scope_plug_inventory_overhead_covers_curve_key_counts(monkeypatch):
    instrumentation = _Instrumentation(object())
    instrumentation.active = True
    ticks = iter((100, 250))
    monkeypatch.setattr(probe.time, "perf_counter_ns", lambda: next(ticks))

    observed_overhead = []

    def fake_key_count(_cmds, plug):
        observed_overhead.append((plug, instrumentation._in_clear_inventory_overhead_ns))
        return 3

    monkeypatch.setattr(probe, "_key_count", fake_key_count)
    monkeypatch.setattr(probe, "_anim_curves_for_plug", lambda _cmds, _plug: ["curve1"])

    instrumentation._record_scope_plug("node", "translateX")

    assert observed_overhead == [("node.translateX", 0), ("curve1", 0)]
    assert instrumentation._in_clear_inventory_overhead_ns == 150


def test_action_diagnostics_excludes_nested_or_large_profile_payloads():
    huge_marker = "profile-frame-payload-" + ("x" * 100_000)
    profile = {
        "vmd_converter": {
            "runtime_registration": {"status": "success", "frame_count": 42},
            "baked_frames": [huge_marker],
            "node_list": ["|model|joint"] * 50_000,
        },
        "texture_issues": [{"path": huge_marker}],
        "arbitrary_nested": {"payload": huge_marker},
    }
    result = SimpleNamespace(
        succeeded=True,
        outcome="success",
        root_node="|model",
        error=None,
        warnings=[{"code": "partial", "details": [huge_marker]}],
    )

    diagnostics = _action_diagnostics(result, {"profile": profile})
    encoded = json.dumps(diagnostics)

    assert diagnostics["profile_status"] == {
        "vmd_converter.runtime_registration.status": "success",
        "vmd_converter.runtime_registration.frame_count": 42,
    }
    assert diagnostics["warning_count"] == 1
    assert diagnostics["warnings"][0].startswith("{'code': 'partial'")
    assert diagnostics["warnings"][0].endswith("... [truncated]")
    assert "baked_frames" not in encoded
    assert "node_list" not in encoded
    assert "texture_issues" not in encoded
    assert "arbitrary_nested" not in encoded
    assert huge_marker not in encoded
    assert len(encoded) < 5_000


def test_mayapy_warning_lines_are_bounded_and_fail_the_threshold_closed():
    long_warning = "[MMD] WARNING: unsupported morph " + ("x" * 10_000)
    warning = _mayapy_warning_entry(long_warning, "mayapy_stderr")

    assert warning is not None
    assert warning["source"] == "mayapy_stderr"
    assert warning["warning"].endswith("... [truncated]")
    assert len(warning["warning"]) < 1_100
    assert _mayapy_warning_entry("Initialized VP2.0 renderer", "mayapy_stdout") is None
    assert _mayapy_warning_entry("normal import progress: 50%", "mayapy_stdout") is None

    report = {"runs": [_run(index) for index in range(3)], "warnings": [], "errors": [], "not_run": []}
    _apply_mayapy_warnings(
        report,
        {"warnings": [warning], "warning_count": 1, "warnings_truncated": False},
    )

    assert report["warnings"] == [warning]
    assert report["mayapy_output"] == {"warning_count": 1, "warnings_truncated": False}
    assert report["threshold_decision"]["decision"] == "no_proceed"
    assert report["threshold_decision"]["status"] == "fail"
    assert report["status"] == "fail"


def test_git_worktree_provenance_has_bounded_dirty_identity(monkeypatch):
    def fake_status(command):
        assert "-z" in command
        return {
            "sha256": "status-hash",
            "status_entries": [" M tools/probes/vmd_clear_performance_probe.py"],
            "status_entries_truncated": False,
            "target_paths": ["tools/probes/vmd_clear_performance_probe.py"],
            "target_paths_truncated": False,
            "status_entry_count": 1,
            "untracked_sha256": "untracked-hash",
        }

    def fake_stream(command, consume=None):
        assert command[1] == "diff"
        return "diff-stream-hash"

    def fake_run(command, **kwargs):
        if command[1] == "rev-parse":
            return SimpleNamespace(stdout="candidate-sha\n")
        raise AssertionError(command)

    monkeypatch.setattr(probe, "_stream_git_status", fake_status)
    monkeypatch.setattr(probe, "_stream_git_stdout", fake_stream)
    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = _git_worktree_provenance()

    assert result["available"] is True
    assert result["status"] == "dirty"
    assert result["target_paths"] == ["tools/probes/vmd_clear_performance_probe.py"]
    assert result["head_sha"] == "candidate-sha"
    assert len(result["diff_sha256"]) == 64


def test_stream_git_status_handles_nul_records_without_retaining_all_entries(monkeypatch):
    payload = b"".join(f" M file{index}.py\0".encode("ascii") for index in range(130))

    class FakeStdout:
        def __init__(self):
            self.chunks = [payload[:7], payload[7:]]

        def read(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()

        def wait(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(probe.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    result = _stream_git_status(["git", "status", "-z"])

    assert result["status_entry_count"] == 130
    assert len(result["status_entries"]) == 128
    assert result["status_entries"][0] == " M file0.py"
    assert result["status_entries"][-1] == " M file127.py"
    assert len(result["target_paths"]) == 128
    assert result["status_entries_truncated"] is True


def test_nonzero_worker_exit_is_reported_and_blocks_proceed():
    report = {"runs": [_run(index) for index in range(3)], "warnings": [], "errors": [], "not_run": []}

    _apply_mayapy_returncode(report, "2024", 7)

    assert any("exited with code 7" in error for error in report["errors"])
    assert report["not_run"] == ["mayapy worker exited nonzero"]
    assert report["threshold_decision"]["decision"] == "no_proceed"
    assert report["status"] == "fail"


def test_changed_git_provenance_blocks_proceed():
    start = {
        "available": True,
        "status": "dirty",
        "status_entry_count": 1,
        "target_paths": ["tools/probes/vmd_clear_performance_probe.py"],
        "diff_sha256": "before",
        "head_sha": "candidate",
    }
    end = dict(start, diff_sha256="after")
    report = {"runs": [_run(index) for index in range(3)], "warnings": [], "errors": [], "not_run": []}

    _apply_git_provenance(report, start, end)

    assert report["git_provenance"]["changed"] is True
    assert report["threshold_decision"]["decision"] == "no_proceed"
    assert report["status"] == "fail"


def test_host_returns_nonzero_when_worker_report_is_not_run(tmp_path, monkeypatch):
    pmx = tmp_path / "fixture.pmx"
    vmd = tmp_path / "fixture.vmd"
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")
    provenance = {
        "available": True,
        "status": "clean",
        "status_entry_count": 0,
        "status_entries": [],
        "target_paths": [],
        "diff_sha256": "same",
        "head_sha": "candidate",
    }
    monkeypatch.setattr(probe, "_git_worktree_provenance", lambda: provenance)
    monkeypatch.setattr(probe, "_candidate_sha", lambda: "candidate")
    monkeypatch.setattr(probe, "_maya_executable", lambda _version: tmp_path / "mayapy.exe")
    monkeypatch.setattr(probe, "_run_mayapy_worker", lambda *_args, **_kwargs: (7, {"warnings": []}))
    args = SimpleNamespace(
        maya_versions=("2024",),
        pmx=pmx,
        vmd=vmd,
        out_dir=tmp_path / "reports",
        runs=3,
        warmup=1,
        timeout=1.0,
    )

    exit_code = probe.run_host(args)

    assert exit_code == 1
    summary = json.loads((args.out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] != "pass"
    assert "exited with code 7" in summary["reports"]["2024"]["errors"][-1]


def test_final_provenance_change_blocks_summary_after_report_validation(tmp_path, monkeypatch):
    pmx = tmp_path / "fixture.pmx"
    vmd = tmp_path / "fixture.vmd"
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")
    start = {
        "available": True,
        "status": "clean",
        "status_entry_count": 0,
        "status_entries": [],
        "target_paths": [],
        "diff_sha256": "same",
        "head_sha": "candidate",
    }
    end = dict(start, diff_sha256="changed")
    provenance_values = iter((start, start, end))
    monkeypatch.setattr(probe, "_git_worktree_provenance", lambda: next(provenance_values))
    monkeypatch.setattr(probe, "_candidate_sha", lambda: "should-not-authorize")
    monkeypatch.setattr(probe, "_maya_executable", lambda _version: tmp_path / "mayapy.exe")

    def fake_worker(command, **_kwargs):
        config = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        probe._write_json(
            Path(config["out_path"]),
            {"status": "pass", "runs": [_run(index) for index in range(3)], "warnings": [], "errors": [], "not_run": []},
        )
        return 0, {"warnings": []}

    monkeypatch.setattr(probe, "_run_mayapy_worker", fake_worker)
    args = SimpleNamespace(
        maya_versions=("2024",),
        pmx=pmx,
        vmd=vmd,
        out_dir=tmp_path / "reports",
        runs=3,
        warmup=1,
        timeout=1.0,
    )

    exit_code = probe.run_host(args)

    summary = json.loads((args.out_dir / "summary.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert summary["candidate_sha"] == "candidate"
    assert summary["git_provenance"]["changed"] is True
    assert summary["status"] == "not_run"
    assert "after per-report validation" in summary["errors"][0]


def test_load_config_requires_warmup_and_three_measured_runs(tmp_path):
    pmx = tmp_path / "fixture.pmx"
    vmd = tmp_path / "fixture.vmd"
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "maya_version": "2024",
                "pmx_path": str(pmx),
                "vmd_path": str(vmd),
                "out_path": str(tmp_path / "report.json"),
                "maya_app_dir": str(tmp_path / "maya-app"),
                "runs": 3,
                "warmup": 1,
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["maya_version"] == "2024"
    assert config["runs"] == 3
    assert config["warmup"] == 1


def test_load_config_rejects_insufficient_runs(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "maya_version": "2024",
                "pmx_path": str(tmp_path / "fixture.pmx"),
                "vmd_path": str(tmp_path / "fixture.vmd"),
                "out_path": str(tmp_path / "report.json"),
                "maya_app_dir": str(tmp_path / "maya-app"),
                "runs": 2,
                "warmup": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProbeConfigurationError, match="at least 3"):
        load_config(config_path, require_assets=False)
