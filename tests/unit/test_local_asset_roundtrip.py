"""Host-neutral contracts for the local asset roundtrip runner."""

import os
from types import SimpleNamespace

import pytest

from tools.local_asset_roundtrip import (
    _metric_snapshot,
    _repetitions,
    _select_cases,
    _vmd_payload,
    _vmd_payload_diff,
)


def test_metric_snapshot_exposes_rss_on_windows():
    metrics = _metric_snapshot()

    assert set(metrics) == {"rss_bytes", "peak_rss_bytes"}
    if os.name == "nt":
        assert metrics["rss_bytes"] is not None
        assert metrics["peak_rss_bytes"] is not None


def _bone(interpolation=b"\x14" * 64):
    return SimpleNamespace(
        bone_name="センター",
        frame_number=0,
        position=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
        interpolation=interpolation,
    )


def test_vmd_payload_diff_requires_key_times_and_raw_interpolation():
    data = SimpleNamespace(
        header=SimpleNamespace(model_name="model"),
        bone_frames=[_bone()],
        morph_frames=[],
        camera_frames=[],
        light_frames=[],
        shadow_frames=[],
        ik_show_hide_frames=[],
    )

    payload = _vmd_payload(data)
    assert _vmd_payload_diff(payload, payload) == []

    changed = _vmd_payload(SimpleNamespace(**{**data.__dict__, "bone_frames": [_bone(b"\x15" * 64)]}))
    assert "bone[0].interpolation differs" in _vmd_payload_diff(payload, changed)


def test_profile_selects_one_dense_and_one_sparse_case_without_cartesian_pairs():
    cases = [
        {"name": "pmx_only", "classification": "pmx_only", "pmx": "model.pmx"},
        {"name": "dense_motion", "classification": "dense", "pmx": "model.pmx", "vmd": "dense.vmd"},
        {"name": "sparse_motion", "classification": "sparse", "pmx": "model.pmx", "vmd": "sparse.vmd"},
    ]

    selected = _select_cases(cases, profile="dense-hang-and-sparse-interpolation")

    assert [case["name"] for case in selected] == ["dense_motion", "sparse_motion"]


def test_dense_repetitions_are_cold_one_plus_warm_three():
    assert _repetitions({"classification": "dense"}, 1, 3) == 4
    assert _repetitions({"classification": "sparse"}, 1, 3) == 1
    with pytest.raises(ValueError):
        _select_cases([{"name": "dense", "classification": "dense"}], profile="dense-hang-and-sparse-interpolation")
