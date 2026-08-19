"""Focused non-Maya tests for the real-asset timeline sampler probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.vmd_timeline_real_asset_probe import (
    PREFIX_FRAMES,
    ProbeConfigurationError,
    _console_summary,
    compare_pair,
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


def _result(tmp_path: Path, strategy: str, wall: float, values: bytes) -> dict:
    artifact = tmp_path / f"{strategy}.bin"
    packed = bytes(48) + values
    artifact.write_bytes(packed)
    digest = hashlib.sha256(packed).hexdigest()
    return {
        "strategy": strategy,
        "prefix_frames": 120,
        "wall_sec": wall,
        "packed": {
            "artifact": str(artifact),
            "sha256": digest,
            "values_sha256": digest,
            "header": [1.0, 120.0, 1.0, 1.0, 0.0, 0.0],
        },
        "route_inventory_sha256": "same-route",
    }


def test_pair_requires_exact_binary_value_and_header_parity(tmp_path):
    context = _result(tmp_path, "context", 1.0, b"same")
    timeline = _result(tmp_path, "timeline", 1.5, b"same")

    same = compare_pair(context, timeline)
    timeline["packed"]["artifact"] = str(tmp_path / "different.bin")
    Path(timeline["packed"]["artifact"]).write_bytes(bytes(48) + b"different")
    different = compare_pair(context, timeline)

    assert same["packed_values_exactly_equal"] is True
    assert same["timeline_over_context_ratio"] == 1.5
    assert different["packed_values_exactly_equal"] is False


def test_full_wall_estimate_uses_all_prefixes_through_origin():
    results = [
        {"prefix_frames": 120, "wall_sec": 1.2},
        {"prefix_frames": 300, "wall_sec": 3.0},
        {"prefix_frames": 600, "wall_sec": 6.0},
    ]

    assert estimate_full_wall(results, 6786) == 67.86


def test_console_summary_omits_large_route_inventory():
    summary = _console_summary(
        {
            "schema_version": 1,
            "status": "pass",
            "strategy": "context",
            "prefix_frames": 120,
            "route_inventory": {"channels": [object()] * 100},
            "packed": {"header": [1.0], "sha256": "abc", "artifact": "large.bin"},
        }
    )

    assert "route_inventory" not in summary
    assert "artifact" not in summary["packed"]
