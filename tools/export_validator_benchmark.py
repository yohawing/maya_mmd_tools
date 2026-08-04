"""Measure the Maya-independent export validator on a deterministic payload.

This focused benchmark reports current scanner and full-validator timings for a
fixed synthetic vertex payload.  It intentionally does not claim a before /
after speedup; compare separately captured runs when evaluating a change.
"""

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mmd_tools.validation.export_validator import _scan_non_finite_numbers  # noqa: E402
from mmd_tools.validation.export_validator import validate_model_data  # noqa: E402


def _build_payload(vertex_count: int) -> Dict[str, Any]:
    """Build a deterministic collector-shaped payload without non-finite values."""
    vertices = [
        {
            "position": [float(index), 0.0, 0.0],
            "normal": [0.0, 1.0, 0.0],
            "uv": [0.0, 0.0],
            "bone_indices": [0],
        }
        for index in range(vertex_count)
    ]
    return {
        "vertices": vertices,
        "faces": [[0, 1, 2]],
        "bones": None,
    }


def _measure_scan(payload: Dict[str, Any], repeat: int) -> List[float]:
    """Measure only the recursive non-finite scan after payload construction."""
    samples = []
    for _ in range(repeat):
        issues = []
        started = time.perf_counter()
        _scan_non_finite_numbers(payload, "", issues, set())
        elapsed = time.perf_counter() - started
        if issues:
            raise RuntimeError(f"deterministic benchmark payload produced {len(issues)} issues")
        samples.append(elapsed)
    return samples


def _measure_validation(payload: Dict[str, Any], repeat: int) -> List[float]:
    """Measure the complete PMX model validation after payload construction."""
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        report = validate_model_data(payload, "pmx")
        elapsed = time.perf_counter() - started
        if report.issues:
            raise RuntimeError(f"deterministic benchmark payload produced {len(report.issues)} issues")
        samples.append(elapsed)
    return samples


def main() -> None:
    """Run the focused benchmark and emit stable JSON metadata plus timings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vertices", type=int, default=100_000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.vertices < 3:
        parser.error("--vertices must be at least 3")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    payload = _build_payload(args.vertices)
    scan_samples = _measure_scan(payload, args.repeat)
    validation_samples = _measure_validation(payload, args.repeat)
    print(
        json.dumps(
            {
                "benchmark": "export_validator_non_finite_scan",
                "vertices": args.vertices,
                "repeat": args.repeat,
                "scan_seconds": scan_samples,
                "validation_seconds": validation_samples,
                "issue_count": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
