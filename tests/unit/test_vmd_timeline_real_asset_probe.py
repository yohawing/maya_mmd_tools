"""Focused non-Maya tests for the real-asset timeline sampler probe."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.vmd_timeline_real_asset_probe import (
    PREFIX_FRAMES,
    ProbeConfigurationError,
    _console_summary,
    _compare_third_oracle_values,
    _run_controller,
    _third_oracle_frames,
    _witness_category_status,
    estimate_full_wall,
    load_config,
)


def _write_config(path: Path, out_dir: Path, **updates) -> Path:
    payload = {
        "schema_version": 1,
        "pmx_path": "C:/assets/モデル.pmx",
        "vmd_path": "C:/assets/モーション.vmd",
        "prefix_frames": list(PREFIX_FRAMES),
        "out_dir": str(out_dir),
    }
    payload.update(updates)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_utf8_config_keeps_asset_paths_off_argv(tmp_path):
    config_path = _write_config(tmp_path / "probe.json", tmp_path / "result")

    config = load_config(config_path, require_assets=False)

    assert str(config["pmx_path"]).endswith("モデル.pmx")
    assert str(config["vmd_path"]).endswith("モーション.vmd")
    assert config["prefix_frames"] == PREFIX_FRAMES
    assert config["full_frame_count"] == 6786
    assert config["worker_timeout_sec"] == 900.0


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"schema_version": 2}, "schema_version"),
        ({"prefix_frames": [120, 600]}, "prefix_frames"),
        ({"full_frame_count": 600}, "full_frame_count"),
        ({"extra": True}, "unknown config fields"),
        ({"vmd_path": "C:/assets/not-vmd.pmx"}, "vmd_path"),
        ({"worker_timeout_sec": 0}, "worker_timeout_sec"),
    ],
)
def test_config_rejects_noncanonical_inputs(tmp_path, updates, message):
    config_path = _write_config(tmp_path / "probe.json", tmp_path / "result", **updates)

    with pytest.raises(ProbeConfigurationError, match=message):
        load_config(config_path, require_assets=False)


def test_config_path_itself_must_be_ascii_safe(tmp_path):
    config_path = _write_config(tmp_path / "設定.json", tmp_path / "result")

    with pytest.raises(ProbeConfigurationError, match="ASCII-safe"):
        load_config(config_path, require_assets=False)


def test_full_wall_estimate_uses_all_prefixes_through_origin():
    results = [
        {"prefix_frames": 120, "wall_sec": 1.2},
        {"prefix_frames": 300, "wall_sec": 3.0},
        {"prefix_frames": 600, "wall_sec": 6.0},
    ]

    assert estimate_full_wall(results, 6786) == 67.86


def test_controller_launches_one_production_worker_per_prefix(tmp_path, monkeypatch):
    config_path = tmp_path / "probe.json"
    out_dir = tmp_path / "results"
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        prefix = int(command[command.index("--prefix") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"production-{prefix:04d}.json").write_text(
            json.dumps(
                {
                    "status": "pass",
                    "prefix_frames": prefix,
                    "wall_sec": prefix / 100.0,
                    "third_oracle": {"status": "pass"},
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("tools.vmd_timeline_real_asset_probe.subprocess.run", fake_run)
    result = _run_controller(
        config_path,
        {
            "out_dir": out_dir,
            "mayapy": "mayapy",
            "prefix_frames": PREFIX_FRAMES,
            "full_frame_count": 6786,
            "worker_timeout_sec": 30.0,
        },
    )

    assert result["status"] == "pass"
    assert result["fresh_process_per_prefix"] is True
    assert len(commands) == 3
    assert all("--strategy" not in command for command in commands)


def test_console_summary_omits_large_route_inventory():
    summary = _console_summary(
        {
            "schema_version": 1,
            "status": "pass",
            "evaluation_policy": "maya_timeline_bake_v1",
            "prefix_frames": 120,
            "route_inventory": {"channels": [object()] * 100},
            "sampled_values": {
                "float_count": 10,
                "sha256": "abc",
                "artifact": "large.bin",
            },
        }
    )

    assert "route_inventory" not in summary
    assert "artifact" not in summary["sampled_values"]
    assert summary["evaluation_policy"] == "maya_timeline_bake_v1"


def test_third_oracle_accepts_normal_timeline_values_with_unit_tolerances():
    channels = [
        SimpleNamespace(plug="|model|bone.rotateX", unit="angle"),
        SimpleNamespace(plug="|model|bone.translateX", unit="distance"),
        SimpleNamespace(plug="|model|bone.weight", unit="scalar"),
    ]
    native = ((10.0, 2.0, 0.25),)
    normal = ((10.0 + 5.0e-8, 2.0 - 5.0e-8, 0.25 + 5.0e-10),)

    result = _compare_third_oracle_values(channels, (100.0,), native, normal)

    assert result["status"] == "pass"
    assert result["sample_count"] == 3
    assert result["mismatch_count"] == 0
    assert result["max_abs_error"] == pytest.approx(5.0e-8)


def test_third_oracle_reports_scalar_mismatch_and_bounds_samples():
    channel = SimpleNamespace(plug="|model|bone.rotateZ", unit="angle")
    native = tuple((float(index),) for index in range(25))
    normal = tuple((float(index) + 1.0e-3,) for index in range(25))

    result = _compare_third_oracle_values(
        [channel],
        tuple(float(index) for index in range(25)),
        native,
        normal,
    )

    assert result["status"] == "fail"
    assert result["mismatch_count"] == 25
    assert len(result["mismatches"]) == 20
    assert result["max_abs_error"] == pytest.approx(1.0e-3)


def test_third_oracle_frames_are_prefix_bounded():
    assert _third_oracle_frames(120) == (0.0, 100.0, 110.0, 119.0)
    assert _third_oracle_frames(110) == (0.0, 100.0)


def test_ccdik_world_witness_may_pass_without_skin_but_append_requires_skin():
    assert _witness_category_status("mmdCcdIk", 4, 4, False) == "pass"
    assert _witness_category_status("mmdCcdIk", 4, 4, True) == "pass"
    assert _witness_category_status("mmdAppend", 4, 4, False) == "fail"
    assert _witness_category_status("finger", 4, 4, False) == "fail"
    assert _witness_category_status("mmdCcdIk", 3, 4, False) == "fail"
