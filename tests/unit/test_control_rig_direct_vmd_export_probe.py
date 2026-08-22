"""Host-side contracts for the Control Rig direct-export GUI probe."""

import json
from pathlib import Path

import pytest

from tools.control_rig_direct_vmd_export_probe import (
    DEFAULT_POSE_TOLERANCE,
    _command,
    _filter_scene_pose,
    _require_selected_control_tracks,
    load_config,
)


def _write_assets_and_config(tmp_path: Path, *, ranges=None):
    pmx = tmp_path / "重音テト.pmx"
    vmd = tmp_path / "愛言葉IV.vmd"
    pmx.write_bytes(b"pmx")
    vmd.write_bytes(b"vmd")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "pmx": str(pmx),
                "vmd": str(vmd),
                "ranges": ranges
                if ranges is not None
                else [
                    {"name": "short", "start": 0, "end": 2},
                    {
                        "name": "specified",
                        "start": 10,
                        "end": 20,
                        "oracle_frames": [10, 15, 20],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config, pmx, vmd


def test_load_config_keeps_non_ascii_assets_outside_ascii_argv(tmp_path):
    config, pmx, vmd = _write_assets_and_config(tmp_path)

    loaded = load_config(config)
    command = _command(
        config,
        tmp_path / "report.json",
        tmp_path / "probe.log",
    )

    assert loaded["pmx"] == str(pmx.resolve())
    assert loaded["vmd"] == str(vmd.resolve())
    assert loaded["pose_tolerance"] == DEFAULT_POSE_TOLERANCE == 1.0e-4
    assert loaded["ranges"] == [
        {"name": "short", "start": 0, "end": 2, "oracle_frames": [0, 1, 2]},
        {
            "name": "specified",
            "start": 10,
            "end": 20,
            "oracle_frames": [10, 15, 20],
        },
    ]
    assert str(pmx) not in command
    assert str(vmd) not in command
    assert repr(str(config.resolve())) in command
    assert command.isascii()


def test_filter_scene_pose_keeps_only_control_bound_bones():
    scene = {
        "pose": {
            "joint_count": 2,
            "joints": [{"name": "センター"}, {"name": "スカート"}],
            "frames": {
                "0": [
                    {"name": "センター", "translation": [1.0, 2.0, 3.0]},
                    {"name": "スカート", "translation": [4.0, 5.0, 6.0]},
                ]
            },
        },
        "metadata": {"mmd_model_name": "fixture"},
    }

    filtered = _filter_scene_pose(scene, {"センター"})

    assert filtered["pose"] == {
        "joint_count": 1,
        "joints": [{"name": "センター"}],
        "frames": {
            "0": [{"name": "センター", "translation": [1.0, 2.0, 3.0]}]
        },
    }
    assert filtered["metadata"] == scene["metadata"]
    assert scene["pose"]["joint_count"] == 2


def test_probe_rejects_empty_selected_control_track_oracle():
    with pytest.raises(RuntimeError, match="no keyed Control tracks"):
        _require_selected_control_tracks(set())


@pytest.mark.parametrize(
    ("ranges", "message"),
    [
        ([], "non-empty array"),
        ([{"name": "bad", "start": 3, "end": 2}], "ordered non-negative"),
        (
            [{"name": "bad", "start": 0, "end": 2, "oracle_frames": [3]}],
            "must stay in range",
        ),
        ([{"name": "日本語", "start": 0, "end": 2}], "unique ASCII"),
    ],
)
def test_load_config_rejects_invalid_range_contract(tmp_path, ranges, message):
    config, _pmx, _vmd = _write_assets_and_config(tmp_path, ranges=ranges)

    with pytest.raises(ValueError, match=message):
        load_config(config)
