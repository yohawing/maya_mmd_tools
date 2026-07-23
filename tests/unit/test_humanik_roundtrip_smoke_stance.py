"""Unit tests for S5 stance-coincidence classification helpers.

These cover the pure, cmds-independent helpers added for stance-coincident
frame semantics in ``tests/viewport/humanik_roundtrip_smoke.py`` (see that
module's docstring for the full HumanIK evidence and gate rationale). The
smoke itself is mayapy-only and is not exercised here.
"""

from __future__ import annotations

import tests.viewport.humanik_roundtrip_smoke as smoke
from tests.viewport.humanik_roundtrip_smoke import (
    STANCE_COINCIDENCE_POSE_EPSILON,
    STANCE_COINCIDENT_MATRIX_TOLERANCE,
    _pose_coincidence_metric,
    _split_matrix_fidelity_by_stance,
)


def test_stance_coincidence_epsilon_separates_measured_evidence():
    """0.1 must sit strictly between the measured bind-vs-frame0/frame1 deltas."""
    bind_vs_frame0 = 0.0044075900114741995  # build/reports/hik_frame0_stance_compare.json
    bind_vs_frame1 = 1.015140175819397
    assert bind_vs_frame0 < STANCE_COINCIDENCE_POSE_EPSILON < bind_vs_frame1


def test_pose_coincidence_metric_is_max_delta_across_joints(monkeypatch):
    reference = {
        "Hips": (0.0, 10.0, 0.0),
        "LeftUpLeg": (1.0, 8.0, 0.0),
        "RightUpLeg": (-1.0, 8.0, 0.0),
    }
    current = {
        "Hips": (0.0, 10.0, 0.0),
        "LeftUpLeg": (1.03, 8.0, 0.0),
        "RightUpLeg": (-1.0, 8.0, 0.0),
    }
    monkeypatch.setattr(smoke, "_world_translation", lambda joint: current[joint])

    metric = _pose_coincidence_metric(reference, list(reference))

    assert abs(metric - 0.03) < 1.0e-9


def test_pose_coincidence_metric_below_epsilon_is_stance_coincident(monkeypatch):
    reference = {"Hips": (0.0, 0.0, 0.0)}
    monkeypatch.setattr(smoke, "_world_translation", lambda joint: (0.05, 0.0, 0.0))

    metric = _pose_coincidence_metric(reference, ["Hips"])

    assert metric < STANCE_COINCIDENCE_POSE_EPSILON


def test_split_matrix_fidelity_by_stance_separates_classes():
    matrix_fidelity = {
        "frames": [
            {"frame": 0, "max": 0.0299, "mean": 0.001},
            {"frame": 1, "max": 0.0007, "mean": 0.0002},
            {"frame": 2, "max": 0.0006, "mean": 0.0001},
        ]
    }
    classification = {
        "coincidentFrames": [0],
        "nonCoincidentFrames": [1, 2],
    }

    result = _split_matrix_fidelity_by_stance(matrix_fidelity, classification)

    assert result["stanceCoincident"]["frameCount"] == 1
    assert result["stanceCoincident"]["frames"] == [0]
    assert result["stanceCoincident"]["max"] == 0.0299
    assert result["stanceCoincident"]["max"] <= STANCE_COINCIDENT_MATRIX_TOLERANCE

    assert result["nonCoincident"]["frameCount"] == 2
    assert result["nonCoincident"]["frames"] == [1, 2]
    assert result["nonCoincident"]["max"] == 0.0007
    assert abs(result["nonCoincident"]["mean"] - 0.00015) < 1.0e-12


def test_split_matrix_fidelity_by_stance_handles_no_coincident_frames():
    matrix_fidelity = {"frames": [{"frame": 5, "max": 0.0005, "mean": 0.0001}]}
    classification = {"coincidentFrames": [], "nonCoincidentFrames": [5]}

    result = _split_matrix_fidelity_by_stance(matrix_fidelity, classification)

    assert result["stanceCoincident"]["frameCount"] == 0
    assert result["stanceCoincident"]["max"] == 0.0
    assert result["stanceCoincident"]["mean"] == 0.0
    assert result["nonCoincident"]["frameCount"] == 1
