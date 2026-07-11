"""Compare Maya viewport captures with GoldenOracle images and enforce gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


DEFAULT_THRESHOLDS = {
    "diffuse": 0.10,
    "toon": 0.12,
    "uv": 0.10,
    "sphere": 0.14,
    "alpha": 0.10,
    "outline": 0.12,
}


def _threshold(name: str, overrides: dict[str, float], fallback: float) -> tuple[float, str]:
    for pattern, value in overrides.items():
        if pattern in name:
            return value, pattern
    for pattern, value in DEFAULT_THRESHOLDS.items():
        if pattern in name:
            return value, pattern
    return fallback, "default"


def _image_metrics(reference: Path, actual: Path) -> dict[str, object]:
    ref = Image.open(reference).convert("RGB")
    got = Image.open(actual).convert("RGB")
    if ref.size != got.size:
        return {"size_mismatch": [list(ref.size), list(got.size)]}
    diff = ImageChops.difference(ref, got)
    mean_channels = ImageStat.Stat(diff).mean
    def image_chroma(image: Image.Image) -> float:
        pixels = image.resize((min(image.width, 128), min(image.height, 128))).getdata()
        values = [abs(r - g) + abs(g - b) + abs(b - r) for r, g, b in pixels]
        return sum(values) / (2.0 * max(len(values), 1))

    ref_chroma = image_chroma(ref)
    got_chroma = image_chroma(got)
    return {
        "normalized_mean_absolute_error": sum(mean_channels) / (3.0 * 255.0),
        "changed_bbox": list(diff.getbbox()) if diff.getbbox() else None,
        "reference_chroma": ref_chroma,
        "actual_chroma": got_chroma,
        "chroma_ratio": got_chroma / max(ref_chroma, 1.0),
    }


def compare_report(
    capture_report: Path,
    output: Path,
    overrides: dict[str, float] | None = None,
    fallback_threshold: float = 0.12,
    flat_gray_min_chroma_ratio: float = 0.25,
) -> dict[str, object]:
    report = json.loads(capture_report.read_text(encoding="utf-8"))
    results = []
    overrides = overrides or {}
    for capture in report.get("results", []):
        name = str(capture.get("name"))
        reference = Path(str(capture.get("oracle_png") or ""))
        actual = Path(str(capture.get("actual_png") or ""))
        threshold, threshold_source = _threshold(name, overrides, fallback_threshold)
        failures = []
        metrics: dict[str, object] = {}
        if not reference.is_file():
            failures.append(f"missing GoldenOracle PNG: {reference}")
        if not actual.is_file():
            failures.append(f"missing Maya capture PNG: {actual}")
        if not failures:
            metrics = _image_metrics(reference, actual)
            if "size_mismatch" in metrics:
                failures.append(f"image size mismatch: {metrics['size_mismatch']}")
            else:
                error = float(metrics["normalized_mean_absolute_error"])
                if error > threshold:
                    failures.append(f"pixel error {error:.6f} exceeds {threshold:.6f}")
                ratio = float(metrics["chroma_ratio"])
                if float(metrics["reference_chroma"]) >= 2.0 and ratio < flat_gray_min_chroma_ratio:
                    failures.append(
                        f"flat-gray suspected: chroma ratio {ratio:.6f} below {flat_gray_min_chroma_ratio:.6f}"
                    )
        results.append(
            {
                "name": name,
                "reference": str(reference),
                "actual": str(actual),
                "threshold": threshold,
                "threshold_source": threshold_source,
                "metrics": metrics,
                "failures": failures,
                "status": "pass" if not failures else "fail",
            }
        )
    capture_errors = list(report.get("errors", []))
    comparison = {
        "schemaVersion": 1,
        "kind": "maya-visual-regression-comparison",
        "capture_report": str(capture_report),
        "status": "pass" if results and all(item["status"] == "pass" for item in results) else "fail",
        "capture_errors": capture_errors,
        "results": results,
    }
    if capture_errors:
        comparison["status"] = "fail"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-report", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--threshold", action="append", default=[], metavar="PATTERN=VALUE")
    parser.add_argument("--default-threshold", type=float, default=0.12)
    parser.add_argument("--flat-gray-min-chroma-ratio", type=float, default=0.25)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    overrides = {}
    for item in args.threshold:
        pattern, separator, raw_value = item.partition("=")
        if not separator or not pattern:
            raise ValueError(f"Invalid --threshold, expected PATTERN=VALUE: {item}")
        overrides[pattern] = float(raw_value)
    comparison = compare_report(
        Path(args.capture_report),
        Path(args.out),
        overrides,
        args.default_threshold,
        args.flat_gray_min_chroma_ratio,
    )
    failed = [item["name"] for item in comparison["results"] if item["status"] != "pass"]
    if comparison["status"] != "pass":
        raise RuntimeError(
            f"Visual comparison failed: cases={failed}, capture_errors={len(comparison['capture_errors'])}. See {args.out}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
