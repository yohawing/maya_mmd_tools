"""Compare Maya viewport captures with GoldenOracle images and enforce gates."""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "diffuse": 0.10,
    "toon": 0.14,
    "uv": 0.10,
    "sphere": 0.21,
    "alpha": 0.15,
    "outline": 0.12,
}
BACKEND_THRESHOLDS = {"diffuse": 0.08, "toon": 0.12, "uv": 0.14, "sphere": 0.08, "alpha": 0.22, "outline": 0.21}


def _threshold(name: str, overrides: dict[str, float], fallback: float) -> tuple[float, str]:
    for pattern, value in overrides.items():
        if pattern in name:
            return value, pattern
    for pattern, value in DEFAULT_THRESHOLDS.items():
        if pattern in name:
            return value, pattern
    return fallback, "default"


def _decode_png(path: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    pos = 8
    compressed = b""
    width = height = channels = 0
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type, compression, png_filter, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or color_type not in (2, 6) or compression or png_filter or interlace:
                raise ValueError(
                    "visual gate requires non-interlaced 8-bit RGB/RGBA PNG: "
                    f"depth={depth} color_type={color_type} compression={compression} "
                    f"filter={png_filter} interlace={interlace} path={path}"
                )
            channels = 3 if color_type == 2 else 4
        elif kind == b"IDAT":
            compressed += chunk
        elif kind == b"IEND":
            break
    raw = zlib.decompress(compressed)
    stride = width * channels
    previous = [0] * stride
    offset = 0
    pixels = []
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        encoded = raw[offset : offset + stride]
        offset += stride
        row = []
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            up = previous[index]
            up_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = (value + left) & 255
            elif filter_type == 2:
                decoded = (value + up) & 255
            elif filter_type == 3:
                decoded = (value + ((left + up) // 2)) & 255
            elif filter_type == 4:
                estimate = left + up - up_left
                distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
                predictor = (left, up, up_left)[distances.index(min(distances))]
                decoded = (value + predictor) & 255
            else:
                raise ValueError(f"unsupported PNG filter: {filter_type}")
            row.append(decoded)
        pixels.extend(tuple(row[index : index + 3]) for index in range(0, stride, channels))
        previous = row
    return width, height, pixels


def _image_metrics(reference: Path, actual: Path) -> dict[str, object]:
    ref_width, ref_height, ref = _decode_png(reference)
    got_width, got_height, got = _decode_png(actual)
    if (ref_width, ref_height) != (got_width, got_height):
        return {"size_mismatch": [[ref_width, ref_height], [got_width, got_height]]}
    absolute_error = sum(abs(a - b) for left, right in zip(ref, got) for a, b in zip(left, right))
    ref_chroma = sum(abs(r - g) + abs(g - b) + abs(b - r) for r, g, b in ref) / (2.0 * max(len(ref), 1))
    got_chroma = sum(abs(r - g) + abs(g - b) + abs(b - r) for r, g, b in got) / (2.0 * max(len(got), 1))
    return {
        "normalized_mean_absolute_error": absolute_error / (max(len(ref), 1) * 3.0 * 255.0),
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


def backend_capture_report(
    reference_report: Path,
    actual_report: Path,
    output: Path,
    threshold: float,
    overrides: dict[str, float] | None = None,
    ignore_cases: set[str] | None = None,
) -> dict[str, object]:
    reference = json.loads(reference_report.read_text(encoding="utf-8"))
    actual = json.loads(actual_report.read_text(encoding="utf-8"))
    ignored = set(ignore_cases or ())
    reference_by_name = {
        item["name"]: item for item in reference.get("results", []) if item["name"] not in ignored
    }
    actual_results = [item for item in actual.get("results", []) if item["name"] not in ignored]
    capture_errors = list(reference.get("errors", [])) + list(actual.get("errors", []))
    synthetic = {
        "errors": [error for error in capture_errors if error.get("name") not in ignored],
        "results": [
            {
                "name": item["name"],
                "oracle_png": reference_by_name.get(item["name"], {}).get("actual_png"),
                "actual_png": item.get("actual_png"),
            }
            for item in actual_results
        ],
    }
    missing_cases = sorted(set(reference_by_name) - {item["name"] for item in actual_results})
    synthetic["errors"].extend({"name": name, "error": "missing backend capture"} for name in missing_cases)
    synthetic_path = output.with_suffix(".capture-input.json")
    synthetic_path.parent.mkdir(parents=True, exist_ok=True)
    synthetic_path.write_text(json.dumps(synthetic, ensure_ascii=False, indent=2), encoding="utf-8")
    thresholds = dict(BACKEND_THRESHOLDS)
    thresholds.update(overrides or {})
    comparison = compare_report(synthetic_path, output, thresholds, threshold, 0.0)
    comparison["kind"] = "maya-visual-regression-backend-comparison"
    comparison["reference_capture_report"] = str(reference_report)
    comparison["actual_capture_report"] = str(actual_report)
    comparison["ignored_cases"] = sorted(ignored)
    output.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-report", required=True)
    parser.add_argument("--reference-capture-report", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--threshold", action="append", default=[], metavar="PATTERN=VALUE")
    parser.add_argument("--default-threshold", type=float, default=0.12)
    parser.add_argument("--flat-gray-min-chroma-ratio", type=float, default=0.25)
    parser.add_argument(
        "--ignore-case",
        action="append",
        default=[],
        help="Backend-only comparison: explicitly exclude one non-common case. Repeatable.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.ignore_case and not args.reference_capture_report:
        raise ValueError("--ignore-case is only valid with --reference-capture-report")
    overrides = {}
    for item in args.threshold:
        pattern, separator, raw_value = item.partition("=")
        if not separator or not pattern:
            raise ValueError(f"Invalid --threshold, expected PATTERN=VALUE: {item}")
        overrides[pattern] = float(raw_value)
    if args.reference_capture_report:
        comparison = backend_capture_report(
            Path(args.reference_capture_report),
            Path(args.capture_report),
            Path(args.out),
            args.default_threshold,
            overrides or None,
            set(args.ignore_case),
        )
    else:
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
