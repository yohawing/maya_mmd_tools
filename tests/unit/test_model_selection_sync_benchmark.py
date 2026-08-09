"""Pure-Python checks for the model-selection benchmark report helpers."""

from __future__ import annotations

from tools.model_selection_sync_benchmark import distribution


def test_distribution_empty_is_explicitly_unobserved():
    assert distribution([]) == {"count": 0, "status": "not_observed"}


def test_distribution_preserves_percentiles_and_units():
    report = distribution([1, 2, 3, 4, 5])

    assert report["count"] == 5
    assert report["median_ns"] == 3
    assert report["p95_ns"] == 5
    assert report["p99_ns"] == 5
    assert report["status"] == "measured"
