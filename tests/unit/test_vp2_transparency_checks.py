"""Pure contracts for the R3 VP2 transparency capability probe."""

from __future__ import annotations

import pytest

from tools.render_override.vp2_transparency_checks import (
    capability_decision,
    changed_pixels,
    timing_summary,
)


def test_changed_pixels_is_fail_closed_for_capture_shape() -> None:
    assert changed_pixels([(0, 0, 0), (1, 1, 1)], [(0, 0, 0), (2, 2, 2)]) == 1
    with pytest.raises(ValueError, match="sizes differ"):
        changed_pixels([(0, 0, 0)], [])


def test_timing_summary_uses_observed_nearest_rank_p95() -> None:
    summary = timing_summary([4.0, 1.0, 3.0, 2.0, 100.0])
    assert summary["medianMs"] == 3.0
    assert summary["p95Ms"] == 100.0
    with pytest.raises(ValueError, match="must not be empty"):
        timing_summary([])


def test_capability_decision_does_not_promote_split_success_to_unified() -> None:
    report = {
        "split": {"orderChangedPixels": 500, "positiveControl": {"valid": True}},
        "unified": {
            "faceIsolationChangedPixels": 0,
            "frontOrderChangedPixels": 0,
            "reverseOrderChangedPixels": 0,
        },
        "materialMorph": {"imageChangedPixels": 200},
        "productContract": {
            "sourceVisibilityUnchanged": True,
            "syntheticCanonicalStateUnchanged": True,
        },
        "comparison": {"eligible": False},
    }
    decision = capability_decision(report)
    assert decision["splitObjectOrdering"] is True
    assert decision["unifiedFaceIsolation"] is False
    assert decision["unifiedFaceOrdering"] is False
    assert decision["rawDx11ComparisonEligible"] is False
    assert decision["syntheticCanonicalStatePreserved"] is True


def test_capability_decision_requires_split_positive_control() -> None:
    report = {
        "split": {"orderChangedPixels": 500, "positiveControl": {"valid": False}},
        "unified": {
            "faceIsolationChangedPixels": 0,
            "frontOrderChangedPixels": 0,
            "reverseOrderChangedPixels": 0,
        },
        "materialMorph": {"imageChangedPixels": 200},
        "productContract": {
            "sourceVisibilityUnchanged": True,
            "syntheticCanonicalStateUnchanged": True,
        },
        "comparison": {"eligible": False},
    }
    assert capability_decision(report)["splitObjectOrdering"] is False
