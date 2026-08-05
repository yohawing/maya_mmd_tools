"""Run the RO-0 GoldenOracle, FLIP, and static HTML render report.

The runner deliberately stays outside the Maya implementation.  It resolves
cases from a GoldenOracle-compatible manifest, delegates the actual Maya image
capture to ``tests/viewport/visual_regression_capture.py``, compares the
images with NVIDIA FLIP, and writes one replaceable report at
``build/render-override/latest``.  RO-0 is report-only: the dedicated FLIP
threshold contract is recorded and evaluated, but a numeric result still
requires human inspection of the HTML images before a later feature gate can
claim parity.

The pure helpers are intentionally usable without Maya, GoldenOracle assets,
or an installed FLIP executable.  Tests can inject capture and FLIP runners
through :func:`run_gate`.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_RELATIVE = Path("build/render-override/latest")
MANIFEST_ENV = "GOLDEN_ORACLE_RENDER_MANIFEST"
FEATURES = ("transparency", "outline", "self-shadow", "all")
BACKENDS = ("dx11", "glsl")
DEFAULT_MAYA_COMMAND_PORT = 7721
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# This is deliberately separate from the GoldenOracle manifest's compare.epsilon.
# RO-0 is report-only, so these values are evidence-collection thresholds rather
# than a release claim.  A later gate may replace them only with documented
# known-good/negative-control evidence.
FLIP_THRESHOLD_CONTRACT: Dict[str, Any] = {
    "version": 1,
    "mode": "report-only",
    "source": "Plan 010 RO-0 dedicated FLIP contract; not GoldenOracle compare.epsilon",
    "features": {
        "transparency": {
            "full": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
            "roi": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
        },
        "outline": {
            "full": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
            "roi": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
        },
        "self-shadow": {
            "full": {},
            "roi": {},
        },
        "unclassified": {
            "full": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
            "roi": {"mean": 0.05, "weighted_median": 0.05, "q3": 0.10, "max": 0.50},
        },
    },
}

# Public alias useful to callers that only need the per-feature contract.
FLIP_THRESHOLDS = FLIP_THRESHOLD_CONTRACT["features"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_FLOAT_PATTERN = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
_METRIC_PATTERNS = {
    "mean": re.compile(r"\bMean:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
    "weighted_median": re.compile(r"\bWeighted\s+median:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
    "q1": re.compile(r"\b(?:1st|First)\s+weighted\s+quartile:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
    "q3": re.compile(r"\b(?:3rd|Third)\s+weighted\s+quartile:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
    "min": re.compile(r"\bMin:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
    "max": re.compile(r"\bMax:\s*" + _FLOAT_PATTERN, re.IGNORECASE),
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a recursive manifest-default merge without mutating either input."""

    result: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_path(path_value: Any, base: Path) -> Optional[Path]:
    """Resolve a manifest path while preserving ``None`` and empty values."""

    if not path_value:
        return None
    path = Path(str(path_value))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _normal_feature(value: Any) -> Optional[str]:
    """Map common manifest feature aliases to the three RO-0 feature names."""

    if value is None:
        return None
    token = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "alpha": "transparency",
        "alpha-depth": "transparency",
        "transparent": "transparency",
        "transparency": "transparency",
        "edge": "outline",
        "edge-order": "outline",
        "outline": "outline",
        "selfshadow": "self-shadow",
        "self-shadow": "self-shadow",
        "shadow": "self-shadow",
    }
    return aliases.get(token)


def classify_case(case: Dict[str, Any]) -> Optional[str]:
    """Classify one manifest case without guessing from unrelated asset data.

    Explicit ``feature`` metadata wins.  The fallback names are limited to the
    stable fixture vocabulary already used by Plan 010 and the local manifest.
    """

    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    for value in (case.get("feature"), metadata.get("feature")):
        feature = _normal_feature(value)
        if feature:
            return feature

    tags = {str(tag).lower().replace("_", "-") for tag in metadata.get("tags", [])}
    if case.get("selfShadow") or "self-shadow" in tags or "selfshadow" in tags:
        return "self-shadow"
    if "transparency" in tags or "transparent" in tags or "alpha" in tags:
        return "transparency"
    if "outline" in tags or "edge" in tags:
        return "outline"

    name = str(case.get("name", "")).lower()
    if "self-shadow" in name or "selfshadow" in name:
        return "self-shadow"
    if "alpha" in name or "transparent" in name:
        return "transparency"
    if "outline" in name or "edge-order" in name:
        return "outline"
    return None


def _case_roi(case: Dict[str, Any], merged: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an optional case ROI from the supported manifest locations."""

    metadata = case.get("metadata") if isinstance(case.get("metadata"), dict) else {}
    compare = case.get("compare") if isinstance(case.get("compare"), dict) else {}
    for value in (case.get("roi"), metadata.get("roi"), compare.get("roi"), merged.get("roi")):
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, (list, tuple)) and len(value) == 4:
            return {"x": value[0], "y": value[1], "width": value[2], "height": value[3]}
    return None


def _oracle_png_path(case: Dict[str, Any], manifest_dir: Path, frame: int) -> Optional[Path]:
    oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
    path = _resolve_path(oracle.get("path"), manifest_dir)
    if path is None:
        return None
    if path.suffix.lower() in {".png", ".bmp", ".exr"}:
        return path
    return path.parent / ("frame-%d.png" % frame)


def load_manifest_cases(manifest_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Load and normalize manifest cases for the RO-0 runner."""

    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise ValueError("GoldenOracle manifest must contain a cases list")

    defaults = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}
    manifest_dir = manifest_path.parent
    cases: List[Dict[str, Any]] = []
    for raw in manifest["cases"]:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError("Every GoldenOracle case must have a name")
        merged = _deep_merge(defaults, raw)
        metadata = _deep_merge(
            defaults.get("metadata", {}) if isinstance(defaults.get("metadata"), dict) else {},
            raw.get("metadata", {}) if isinstance(raw.get("metadata"), dict) else {},
        )
        frames = raw.get("frames", defaults.get("frames"))
        if not isinstance(frames, list) or not frames:
            frames = [raw.get("frame", defaults.get("frame", 0))]
        frame = int(frames[0])
        camera = _deep_merge(
            defaults.get("camera", {}) if isinstance(defaults.get("camera"), dict) else {},
            metadata.get("camera", {}) if isinstance(metadata.get("camera"), dict) else {},
        )
        image = _deep_merge(
            defaults.get("image", {}) if isinstance(defaults.get("image"), dict) else {},
            raw.get("image", {}) if isinstance(raw.get("image"), dict) else {},
        )
        display = _deep_merge(
            defaults.get("display", {}) if isinstance(defaults.get("display"), dict) else {},
            raw.get("display", {}) if isinstance(raw.get("display"), dict) else {},
        )
        feature = classify_case(raw)
        cases.append(
            {
                "name": str(raw["name"]),
                "feature": feature,
                "frame": frame,
                "camera": camera,
                "image": image,
                "display": display,
                "viewTransform": raw.get("viewTransform", metadata.get("viewTransform")),
                "renderingSpace": raw.get("renderingSpace", metadata.get("renderingSpace")),
                "metadata": metadata,
                "roi": _case_roi(raw, merged),
                "oracle_png": _oracle_png_path(raw, manifest_dir, frame),
                "raw": raw,
            }
        )
    return manifest, cases


def select_cases(
    cases: Sequence[Dict[str, Any]], feature: str, case_names: Optional[Sequence[str]] = None
) -> List[Dict[str, Any]]:
    """Select cases, optionally including every case in the manifest.

    Feature-specific selection remains strict.  ``all`` is the explicit batch
    mode used by the gallery and includes unclassified cases as well.
    """

    if feature not in FEATURES:
        raise ValueError("unsupported feature: %s" % feature)
    by_name = {str(case["name"]): case for case in cases}
    requested = list(case_names or [])
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise ValueError("unknown manifest case(s): %s" % ", ".join(unknown))

    if requested:
        selected_names = set(requested)
        selected = [case for case in cases if case["name"] in selected_names]
    elif feature == "all":
        selected = list(cases)
    else:
        selected = [case for case in cases if case.get("feature") == feature]
    if not selected:
        raise ValueError("no manifest cases match feature %s" % feature)

    if feature == "all":
        return selected

    unclassified = [str(case["name"]) for case in selected if not case.get("feature")]
    if unclassified:
        raise ValueError("cannot classify selected case(s): %s" % ", ".join(unclassified))
    wrong_feature = [str(case["name"]) for case in selected if case.get("feature") != feature]
    if wrong_feature:
        raise ValueError(
            "case selection crosses feature %s: %s" % (feature, ", ".join(wrong_feature))
        )
    return selected


def _safe_case_dir_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return value or "case"


def clear_output_dir(output_dir: Path) -> Path:
    """Replace only the contents of a directory named ``latest``.

    The directory itself is retained so a stale file cannot escape the fixed
    artifact boundary.  The name check also prevents accidental use against a
    broad build or repository directory.
    """

    output_dir = Path(output_dir).resolve()
    if output_dir.name != "latest":
        raise ValueError("RO-0 output directory must be named latest: %s" % output_dir)
    if output_dir.exists() and output_dir.is_symlink():
        raise ValueError("RO-0 output directory must not be a symlink: %s" % output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in list(output_dir.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            _remove_tree(child)
    return output_dir


def _remove_tree(path: Path, retries: int = 20) -> None:
    """Remove one generated directory, allowing Maya to release its log handle."""

    last_error: Optional[OSError] = None
    for attempt in range(retries):
        try:
            shutil.rmtree(str(path))
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(0.5)
    if last_error is not None:
        raise last_error


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def read_png_rgb(path: Path) -> Tuple[int, int, List[Tuple[int, int, int]]]:
    """Decode the 8-bit RGB/RGBA PNG subset used by the visual harness."""

    data = Path(path).read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG: %s" % path)
    offset = len(_PNG_SIGNATURE)
    width = height = color_type = bit_depth = interlace = None
    compressed = bytearray()
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated PNG chunk: %s" % path)
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(data):
            raise ValueError("truncated PNG payload: %s" % path)
        payload = data[payload_start:payload_end]
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
        offset = payload_end + 4
    if width is None or height is None or bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ValueError("unsupported PNG format: %s" % path)

    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    previous = [0] * stride
    cursor = 0
    pixels: List[Tuple[int, int, int]] = []
    for _ in range(height):
        if cursor + stride + 1 > len(raw):
            raise ValueError("truncated PNG scanline: %s" % path)
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        row: List[int] = []
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
                decoded = (value + (left, up, up_left)[distances.index(min(distances))]) & 255
            else:
                raise ValueError("unsupported PNG filter %d: %s" % (filter_type, path))
            row.append(decoded)
        pixels.extend(tuple(row[index : index + channels][:3]) for index in range(0, stride, channels))
        previous = row
    return int(width), int(height), pixels


def write_png_rgb(path: Path, width: int, height: int, pixels: Sequence[Tuple[int, int, int]]) -> None:
    """Write a dependency-free 8-bit RGB PNG."""

    if width <= 0 or height <= 0 or len(pixels) != width * height:
        raise ValueError("invalid RGB PNG dimensions")
    rows = bytearray()
    cursor = 0
    for _ in range(height):
        rows.append(0)
        for _ in range(width):
            pixel = pixels[cursor]
            cursor += 1
            rows.extend(max(0, min(255, int(channel))) for channel in pixel[:3])
    payload = bytearray(_PNG_SIGNATURE)
    payload.extend(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    payload.extend(_png_chunk(b"IDAT", zlib.compress(bytes(rows))))
    payload.extend(_png_chunk(b"IEND", b""))
    Path(path).write_bytes(bytes(payload))


def normalize_roi(roi: Optional[Dict[str, Any]], width: int, height: int) -> Optional[Dict[str, int]]:
    """Normalize a pixel or explicitly normalized ROI and validate its bounds."""

    if roi is None:
        return None
    if not isinstance(roi, dict):
        raise ValueError("ROI must be an object")
    if all(key in roi for key in ("x", "y", "width", "height")):
        x_value, y_value = roi["x"], roi["y"]
        width_value, height_value = roi["width"], roi["height"]
        normalized = bool(roi.get("normalized")) or (
            all(isinstance(value, float) for value in (x_value, y_value, width_value, height_value))
            and all(0.0 <= float(value) <= 1.0 for value in (x_value, y_value, width_value, height_value))
        )
        if normalized:
            x = int(math.floor(float(x_value) * width))
            y = int(math.floor(float(y_value) * height))
            right = int(math.ceil((float(x_value) + float(width_value)) * width))
            bottom = int(math.ceil((float(y_value) + float(height_value)) * height))
        else:
            x = int(x_value)
            y = int(y_value)
            right = x + int(width_value)
            bottom = y + int(height_value)
    elif all(key in roi for key in ("left", "top", "right", "bottom")):
        x = int(roi["left"])
        y = int(roi["top"])
        right = int(roi["right"])
        bottom = int(roi["bottom"])
    else:
        raise ValueError("ROI requires x/y/width/height or left/top/right/bottom")
    if x < 0 or y < 0 or right > width or bottom > height or right <= x or bottom <= y:
        raise ValueError("ROI is outside image bounds: %r for %dx%d" % (roi, width, height))
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def crop_png(source: Path, destination: Path, roi: Dict[str, Any]) -> Dict[str, int]:
    """Crop a PNG using the same deterministic coordinates for both images."""

    width, height, pixels = read_png_rgb(source)
    bounds = normalize_roi(roi, width, height)
    if bounds is None:
        raise ValueError("ROI is required for crop_png")
    cropped: List[Tuple[int, int, int]] = []
    for row in range(bounds["y"], bounds["y"] + bounds["height"]):
        start = row * width + bounds["x"]
        cropped.extend(pixels[start : start + bounds["width"]])
    write_png_rgb(destination, bounds["width"], bounds["height"], cropped)
    return bounds


def parse_flip_metrics(text: str) -> Dict[str, Optional[float]]:
    """Parse FLIP v1.7 pooled metrics, retaining missing fields as ``None``."""

    metrics: Dict[str, Optional[float]] = {}
    for name, pattern in _METRIC_PATTERNS.items():
        match = pattern.search(text)
        metrics[name] = float(match.group(1)) if match else None
    return metrics


def _threshold_evaluation(metrics: Dict[str, Optional[float]], threshold: Dict[str, float]) -> Dict[str, Any]:
    violations = []
    unavailable = []
    for name, limit in threshold.items():
        value = metrics.get(name)
        if value is None:
            unavailable.append(name)
        elif float(value) > float(limit):
            violations.append({"metric": name, "value": value, "threshold": limit})
    return {
        "status": "unavailable" if unavailable else ("fail" if violations else "pass"),
        "violations": violations,
        "unavailable": unavailable,
    }


def _default_flip_runner(
    reference: Path,
    actual: Path,
    work_dir: Path,
    basename: str,
    flip_executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Run NVIDIA FLIP and return paths/metrics without hiding failures."""

    executable = flip_executable or shutil.which("flip")
    if not executable:
        return {
            "status": "fail",
            "reason": "NVIDIA FLIP executable not found",
            "command": [],
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "metrics": parse_flip_metrics(""),
        }
    work_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "-r",
        str(reference),
        "-t",
        str(actual),
        "-d",
        str(work_dir),
        "-b",
        basename,
        "-txt",
    ]
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)
    text_path = work_dir / (basename + ".txt")
    error_map_path = work_dir / (basename + ".png")
    text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.is_file() else ""
    metrics = parse_flip_metrics(text)
    status = "pass" if completed.returncode == 0 and text_path.is_file() and metrics.get("mean") is not None else "fail"
    return {
        "status": status,
        "reason": None if status == "pass" else "FLIP returned no complete report",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "text_path": str(text_path) if text_path.is_file() else None,
        "error_map_path": str(error_map_path) if error_map_path.is_file() else None,
        "text": text,
        "metrics": metrics,
    }


def _default_capture_runner(
    manifest_path: Path,
    cases: Sequence[Dict[str, Any]],
    capture_dir: Path,
    maya: str,
    backend: str,
    timeout: int,
    width: Optional[int],
    height: Optional[int],
) -> Dict[str, Any]:
    """Delegate capture to the existing Maya visual regression harness."""

    capture_dir.mkdir(parents=True, exist_ok=True)
    capture_script = PROJECT_ROOT / "tests" / "viewport" / "visual_regression_capture.py"
    device = "dx11" if backend == "dx11" else "glcore"
    command = [
        sys.executable,
        str(capture_script),
        "--manifest",
        str(manifest_path),
        "--out",
        str(capture_dir),
        "--maya",
        str(maya),
        "--shader-backend",
        backend,
        "--vp2-device",
        device,
        "--timeout",
        str(timeout),
    ]
    if width is not None:
        command.extend(["--width", str(width)])
    if height is not None:
        command.extend(["--height", str(height)])
    for case in cases:
        command.extend(["--case", str(case["name"])])
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)
    try:
        from tests.common import maya_commandport

        maya_commandport.wait_for_port_close(DEFAULT_MAYA_COMMAND_PORT, min(float(timeout), 30.0))
    except (OSError, TimeoutError):
        # The capture report and process return code remain authoritative.  The
        # outer cleanup retry handles a short-lived Maya log lock as well.
        pass
    report_path = capture_dir / "visual-regression-report.json"
    log_path = capture_dir / "maya_visual_regression.log"
    return {
        "status": "pass" if completed.returncode == 0 and report_path.is_file() else "fail",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "report_path": str(report_path) if report_path.is_file() else None,
        "log_path": str(log_path) if log_path.is_file() else None,
    }


def _resolve_report_path(value: Any, report_path: Optional[Path]) -> Optional[Path]:
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    if report_path is not None:
        return (report_path.parent / path).resolve()
    return path.resolve()


def _copy_if_file(source: Optional[Path], destination: Path) -> Optional[str]:
    if source is None or not source.is_file():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(source), str(destination))
    return str(destination)


def _relative_artifact(path: Optional[Path], output_dir: Path) -> Optional[str]:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_commit(root: Path) -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(root), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _case_result_template(case: Dict[str, Any], feature: str, backend: str) -> Dict[str, Any]:
    case_feature = case.get("feature") or ("unclassified" if feature == "all" else feature)
    return {
        "name": case["name"],
        "feature": case_feature,
        "backend": backend,
        "status": "fail",
        "oracleStatus": "available",
        "oracle-status": "available",
        "frame": case["frame"],
        "camera": case.get("camera", {}),
        "viewTransform": case.get("viewTransform"),
        "renderingSpace": case.get("renderingSpace"),
        "display": case.get("display", {}),
        "roi": case.get("roi"),
        "full": {"status": "not-run", "metrics": parse_flip_metrics(""), "thresholdEvaluation": None},
        "roiComparison": {"status": "unavailable", "metrics": parse_flip_metrics(""), "thresholdEvaluation": None},
        "artifacts": {},
        "errors": [],
    }


def _write_html(summary: Dict[str, Any], output_dir: Path) -> Path:
    """Write a static, image-first report with deterministic ordering."""

    def rank(item: Dict[str, Any]) -> Tuple[int, float, str]:
        status = str(item.get("status"))
        priority = {"fail": 0, "unavailable": 1, "unreviewed": 1, "pass": 2}.get(status, 0)
        metric = item.get("roiComparison", {}).get("metrics", {}).get("mean")
        return priority, -(float(metric) if metric is not None else -1.0), str(item.get("name"))

    rows = sorted(summary.get("cases", []), key=rank)
    cards = []
    for item in rows:
        artifacts = item.get("artifacts", {})
        image_tags = []
        for label, key in (
            ("GoldenOracle", "reference"),
            ("Maya", "maya"),
            ("FLIP", "flipError"),
            ("FLIP ROI", "flipErrorRoi"),
        ):
            href = artifacts.get(key)
            if href:
                escaped_href = html.escape(str(href), quote=True)
                escaped_label = html.escape(label)
                image_tags.append(
                    '<figure><a href="%s"><img loading="lazy" src="%s" alt="%s"></a>'
                    '<figcaption>%s</figcaption></figure>'
                    % (escaped_href, escaped_href, escaped_label, escaped_label)
                )
        if not image_tags:
            image_tags.append('<p class="no-images">画像なし: oracle-status: unavailable</p>')
        full = item.get("full", {})
        roi = item.get("roiComparison", {})
        threshold = summary.get("thresholdContract", {}).get("features", {}).get(item.get("feature"), {})
        errors = item.get("errors", [])
        error_text = "; ".join(str(error) for error in errors)
        cards.append(
            '<article class="case-card status-%s">'
            '<header class="case-head"><h2>%s</h2><span class="status %s">%s</span></header>'
            '<div class="gallery">%s</div>'
            '<details><summary>%s · full %s · ROI %s</summary>'
            '<div class="details">feature=%s · frame=%s · threshold=%s%s</div></details>'
            '</article>'
            % (
                html.escape(str(item.get("status"))),
                html.escape(str(item.get("name"))),
                html.escape(str(item.get("status"))),
                html.escape(str(item.get("status"))),
                "".join(image_tags),
                html.escape(str(item.get("backend"))),
                html.escape(str(full.get("metrics", {}).get("mean"))),
                html.escape(str(roi.get("metrics", {}).get("mean"))),
                html.escape(str(item.get("feature"))),
                html.escape(str(item.get("frame"))),
                html.escape(json.dumps(threshold, ensure_ascii=False, sort_keys=True)),
                (" · errors=" + html.escape(error_text)) if error_text else "",
            )
        )

    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>RO-0 RenderOverride visual gate</title>
<style>
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;margin:8px;background:#111;color:#ddd}
h1{font-size:1rem;margin:0 0 4px}.meta{font-size:.62rem;opacity:.75;margin:0 0 8px}
.case-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:8px}.case-card{background:#1b1b1b;border:1px solid #3b3b3b;padding:5px;min-width:0}.case-card.status-fail{border-color:#b44}
.case-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.case-head h2{font-size:.82rem;line-height:1.15;margin:0;overflow-wrap:anywhere}.status{font-size:.62rem;font-weight:700;white-space:nowrap}.status.fail{color:#f88}.status.unavailable,.status.unreviewed{color:#e9b85c}.status.pass{color:#7dce9b}
.gallery{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-top:4px}.gallery figure{margin:0;background:#050505}.gallery img{display:block;width:100%%;height:min(58vh,640px);object-fit:contain;border:1px solid #333}.gallery figcaption{text-align:center;font-size:.58rem;color:#aaa;padding:2px}.gallery a{display:block}.no-images{font-size:.62rem;color:#e9b85c;margin:12px 4px}
@media(max-width:560px){.gallery{grid-template-columns:1fr}}
details{font-size:.58rem;color:#999;margin-top:4px}summary{cursor:pointer}.details{overflow-wrap:anywhere}.case-card.status-unavailable .gallery{min-height:24px}
</style></head><body>
<h1>RO-0 RenderOverride visual gate</h1>
<p class="meta">status=%s · feature=%s · backend=%s · Maya=%s · mode=%s · cases=%d</p>
<main class="case-grid">%s</main>
</body></html>
""" % (
        html.escape(str(summary.get("status"))),
        html.escape(str(summary.get("feature"))),
        html.escape(str(summary.get("backend"))),
        html.escape(str(summary.get("maya"))),
        html.escape(str(summary.get("gateMode"))),
        len(rows),
        "\n".join(cards),
    )
    path = output_dir / "index.html"
    path.write_text(document, encoding="utf-8")
    return path


def _capture_report_by_name(report_path: Optional[Path]) -> Tuple[Dict[str, Dict[str, Any]], List[Any]]:
    if report_path is None or not report_path.is_file():
        return {}, ["capture report missing"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result_map = {str(item.get("name")): item for item in report.get("results", []) if item.get("name")}
    return result_map, list(report.get("errors", []))


def run_gate(
    manifest_path: Path,
    feature: str,
    case_names: Optional[Sequence[str]] = None,
    maya: str = "2024",
    backend: str = "dx11",
    output_dir: Optional[Path] = None,
    project_root: Optional[Path] = None,
    capture_runner: Optional[Callable[..., Dict[str, Any]]] = None,
    flip_runner: Optional[Callable[..., Dict[str, Any]]] = None,
    flip_executable: Optional[str] = None,
    timeout: int = 420,
    width: Optional[int] = None,
    height: Optional[int] = None,
    roi_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run RO-0 and return the exact JSON object written to ``summary.json``."""

    root = Path(project_root or PROJECT_ROOT).resolve()
    manifest_path = Path(manifest_path).resolve()
    if backend not in BACKENDS:
        raise ValueError("unsupported backend: %s" % backend)
    manifest, all_cases = load_manifest_cases(manifest_path)
    selected = select_cases(all_cases, feature, case_names)
    if roi_overrides:
        selected = [
            {
                **case,
                "roi": dict(roi_overrides[case["name"]]),
            }
            if case["name"] in roi_overrides
            else case
            for case in selected
        ]
    target_value = Path(output_dir) if output_dir is not None else root / OUTPUT_RELATIVE
    target = (root / target_value if not target_value.is_absolute() else target_value).resolve()
    clear_output_dir(target)
    cases_dir = target / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    summary: Dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "render-override-visual-gate",
        "status": "fail",
        "exitCode": 1,
        "gateMode": "report-only",
        "feature": feature,
        "backend": backend,
        "maya": str(maya),
        "manifest": str(manifest_path),
        "manifestSchemaVersion": manifest.get("schemaVersion"),
        "pluginCommit": _git_commit(root),
        "thresholdContract": FLIP_THRESHOLD_CONTRACT,
        "roiOverrides": roi_overrides or {},
        "cases": [],
        "capture": {"status": "not-run"},
    }

    selected_cases = [case for case in selected]
    capture_cases = [case for case in selected_cases if case.get("feature") != "self-shadow"]
    all_unavailable = not capture_cases
    capture_dir = target / ".capture"
    capture_result: Dict[str, Any] = {
        "status": "not-run",
        "reason": "all selected cases are oracle-status: unavailable",
    }
    capture_report_path: Optional[Path] = None
    report_map: Dict[str, Dict[str, Any]] = {}
    capture_errors: List[Any] = []
    capture_errors_by_name: Dict[str, List[str]] = {}

    if not all_unavailable:
        runner = capture_runner or _default_capture_runner
        capture_result = runner(
            manifest_path=manifest_path,
            cases=capture_cases,
            capture_dir=capture_dir,
            maya=str(maya),
            backend=backend,
            timeout=timeout,
            width=width,
            height=height,
        )
        capture_report_path = _resolve_report_path(capture_result.get("report_path"), None)
        report_map, capture_errors = _capture_report_by_name(capture_report_path)
        for error in capture_errors:
            if isinstance(error, dict) and error.get("name"):
                name = str(error["name"])
                message = str(error.get("error") or error)
                capture_errors_by_name.setdefault(name, []).append(message)
    summary["capture"] = {
        key: value
        for key, value in capture_result.items()
        if key not in {"stdout", "stderr"}
    }
    summary["capture"]["stdout"] = capture_result.get("stdout", "")
    summary["capture"]["stderr"] = capture_result.get("stderr", "")

    log_parts = []
    if capture_result.get("stdout"):
        log_parts.append("[capture stdout]\n" + str(capture_result["stdout"]))
    if capture_result.get("stderr"):
        log_parts.append("[capture stderr]\n" + str(capture_result["stderr"]))
    log_path = _resolve_report_path(capture_result.get("log_path"), None)
    if log_path is not None and log_path.is_file():
        log_parts.append("[maya log]\n" + log_path.read_text(encoding="utf-8", errors="replace"))
    (target / "maya.log").write_text("\n\n".join(log_parts) or "capture not run\n", encoding="utf-8")

    for case in selected_cases:
        result = _case_result_template(case, feature, backend)
        case_dir = cases_dir / _safe_case_dir_name(str(case["name"]))
        case_dir.mkdir(parents=True, exist_ok=True)
        if case.get("feature") == "self-shadow":
            result["status"] = "unavailable"
            result["oracleStatus"] = "unavailable"
            result["oracle-status"] = "unavailable"
            result["errors"] = ["SelfShadow has no formal GoldenOracle image in RO-0"]
            result["capture"] = {
                "status": "not-run",
                "reason": "oracle-status: unavailable",
            }
            _write_json(case_dir / "capture.json", result["capture"])
            result["artifacts"]["capture"] = "cases/%s/capture.json" % _safe_case_dir_name(str(case["name"]))
            _write_json(case_dir / "pass-diagnostics.json", result)
            summary["cases"].append(result)
            continue

        thresholds = FLIP_THRESHOLD_CONTRACT["features"][result["feature"]]
        capture_item = report_map.get(str(case["name"]))
        result["capture"] = capture_item or {"status": "missing", "name": case["name"]}
        _write_json(case_dir / "capture.json", result["capture"])
        result["artifacts"]["capture"] = "cases/%s/capture.json" % _safe_case_dir_name(str(case["name"]))
        if capture_item:
            diagnostics_path = _resolve_report_path(capture_item.get("diagnostics"), capture_report_path)
            if diagnostics_path is not None and diagnostics_path.is_file():
                try:
                    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    diagnostics = {}
                color_management = (
                    diagnostics.get("scene", {}).get("colorManagement", {})
                    if isinstance(diagnostics, dict)
                    else {}
                )
                result["viewTransform"] = color_management.get("viewTransformName")
                result["renderingSpace"] = color_management.get("renderingSpaceName")
                result["display"] = {
                    "displayTextures": capture_item.get("display_textures"),
                    "colorManagement": color_management,
                }
        if not capture_item:
            result["errors"].append("selected case is missing from Maya capture report")
        result["errors"].extend(capture_errors_by_name.get(str(case["name"]), []))
        if capture_item and capture_item.get("ok") is False:
            result["errors"].append("Maya capture reported failure for selected case")
        if not capture_item and capture_result.get("status") != "pass":
            result["errors"].append(str(capture_result.get("reason") or "Maya capture failed"))

        reference = _resolve_report_path(
            capture_item.get("oracle_png") if capture_item else None, capture_report_path
        ) or case.get("oracle_png")
        actual = _resolve_report_path(capture_item.get("actual_png") if capture_item else None, capture_report_path)
        reference_copy = case_dir / "reference.png"
        actual_copy = case_dir / "maya.png"
        if _copy_if_file(reference, reference_copy):
            result["artifacts"]["reference"] = "cases/%s/reference.png" % _safe_case_dir_name(str(case["name"]))
        else:
            result["errors"].append("missing GoldenOracle PNG: %s" % reference)
        if _copy_if_file(actual, actual_copy):
            result["artifacts"]["maya"] = "cases/%s/maya.png" % _safe_case_dir_name(str(case["name"]))
        else:
            result["errors"].append("missing Maya capture PNG: %s" % actual)

        if not result["errors"]:
            try:
                ref_width, ref_height, _ = read_png_rgb(reference_copy)
                actual_width, actual_height, _ = read_png_rgb(actual_copy)
                if (ref_width, ref_height) != (actual_width, actual_height):
                    result["errors"].append(
                        "image size mismatch: %dx%d vs %dx%d"
                        % (ref_width, ref_height, actual_width, actual_height)
                    )
            except (OSError, ValueError, zlib.error) as error:
                result["errors"].append("image decode failed: %s" % error)

        if not result["errors"]:
            flip = flip_runner or _default_flip_runner
            full_work = case_dir / ".flip-full"
            full = flip(
                reference=reference_copy,
                actual=actual_copy,
                work_dir=full_work,
                basename="full",
                flip_executable=flip_executable,
            )
            full_text = str(full.get("text", ""))
            if not full_text and full.get("text_path") and Path(str(full["text_path"])).is_file():
                full_text = Path(str(full["text_path"])).read_text(encoding="utf-8", errors="replace")
            full["metrics"] = full.get("metrics") or parse_flip_metrics(full_text)
            result["full"] = {
                "status": full.get("status", "fail"),
                "metrics": full["metrics"],
                "threshold": thresholds["full"],
                "thresholdEvaluation": _threshold_evaluation(
                    full["metrics"], thresholds["full"]
                ),
                "command": full.get("command", []),
                "returncode": full.get("returncode"),
                "stdout": full.get("stdout", ""),
                "stderr": full.get("stderr", ""),
            }
            (case_dir / "flip-full.txt").write_text(full_text, encoding="utf-8")
            result["artifacts"]["flipFull"] = "cases/%s/flip-full.txt" % _safe_case_dir_name(str(case["name"]))
            full_error_map = _resolve_report_path(full.get("error_map_path"), None)
            if _copy_if_file(full_error_map, case_dir / "flip-error.png"):
                result["artifacts"]["flipError"] = "cases/%s/flip-error.png" % _safe_case_dir_name(str(case["name"]))
            if full.get("status") != "pass":
                result["errors"].append(str(full.get("reason") or "FLIP full-frame report failed"))

            roi = case.get("roi")
            if roi is None:
                result["roiComparison"] = {
                    "status": "unavailable",
                    "reason": "manifest case has no ROI contract",
                    "metrics": parse_flip_metrics(""),
                    "threshold": thresholds["roi"],
                    "thresholdEvaluation": None,
                }
            elif full.get("status") == "pass":
                roi_dir = case_dir / ".roi"
                try:
                    roi_dir.mkdir(parents=True, exist_ok=True)
                    ref_roi = roi_dir / "reference.png"
                    actual_roi = roi_dir / "maya.png"
                    bounds = crop_png(reference_copy, ref_roi, roi)
                    actual_bounds = crop_png(actual_copy, actual_roi, roi)
                    if bounds != actual_bounds:
                        raise ValueError("reference and Maya ROI dimensions differ")
                    roi_flip = flip(
                        reference=ref_roi,
                        actual=actual_roi,
                        work_dir=roi_dir / "flip",
                        basename="roi",
                        flip_executable=flip_executable,
                    )
                    roi_text = str(roi_flip.get("text", ""))
                    if not roi_text and roi_flip.get("text_path") and Path(str(roi_flip["text_path"])).is_file():
                        roi_text = Path(str(roi_flip["text_path"])).read_text(encoding="utf-8", errors="replace")
                    roi_flip["metrics"] = roi_flip.get("metrics") or parse_flip_metrics(roi_text)
                    result["roiComparison"] = {
                        "status": roi_flip.get("status", "fail"),
                        "bounds": bounds,
                        "metrics": roi_flip["metrics"],
                        "threshold": thresholds["roi"],
                        "thresholdEvaluation": _threshold_evaluation(
                            roi_flip["metrics"], thresholds["roi"]
                        ),
                        "command": roi_flip.get("command", []),
                        "returncode": roi_flip.get("returncode"),
                    }
                    (case_dir / "flip-roi.txt").write_text(roi_text, encoding="utf-8")
                    result["artifacts"]["flipRoi"] = "cases/%s/flip-roi.txt" % _safe_case_dir_name(str(case["name"]))
                    roi_error_map = _resolve_report_path(roi_flip.get("error_map_path"), None)
                    if _copy_if_file(roi_error_map, case_dir / "flip-error-roi.png"):
                        result["artifacts"]["flipErrorRoi"] = "cases/%s/flip-error-roi.png" % _safe_case_dir_name(str(case["name"]))
                    if roi_flip.get("status") != "pass":
                        result["errors"].append(str(roi_flip.get("reason") or "FLIP ROI report failed"))
                except (OSError, ValueError, zlib.error) as error:
                    result["roiComparison"] = {
                        "status": "fail",
                        "reason": str(error),
                        "metrics": parse_flip_metrics(""),
                        "threshold": thresholds["roi"],
                        "thresholdEvaluation": None,
                    }
                    result["errors"].append("ROI comparison failed: %s" % error)

        if result["errors"]:
            result["status"] = "fail"
        else:
            result["status"] = "unreviewed"
        if capture_item:
            capture_record = result["capture"]
            for field, local_name in (("actual_png", "maya.png"), ("oracle_png", "reference.png")):
                source = capture_record.get(field)
                if source:
                    capture_record["source_" + field] = source
                    capture_record[field] = local_name
            diagnostics_source = _resolve_report_path(capture_record.get("diagnostics"), capture_report_path)
            if diagnostics_source is not None and diagnostics_source.is_file():
                capture_record["source_diagnostics"] = str(diagnostics_source)
                try:
                    capture_record["diagnosticsData"] = json.loads(
                        diagnostics_source.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError):
                    capture_record["diagnosticsData"] = None
                capture_record.pop("diagnostics", None)
            _write_json(case_dir / "capture.json", capture_record)
        result["passDiagnostics"] = {
            "reportOnly": True,
            "numericFullStatus": result["full"].get("thresholdEvaluation"),
            "numericRoiStatus": result["roiComparison"].get("thresholdEvaluation"),
            "fullMetrics": result["full"].get("metrics"),
            "roiMetrics": result["roiComparison"].get("metrics"),
            "fullStatus": result["full"].get("status"),
            "roiStatus": result["roiComparison"].get("status"),
            "errors": result["errors"],
        }
        _write_json(case_dir / "pass-diagnostics.json", result["passDiagnostics"])
        for temporary_dir in (case_dir / ".flip-full", case_dir / ".roi"):
            if temporary_dir.exists():
                _remove_tree(temporary_dir)
        summary["cases"].append(result)

    if capture_dir.exists():
        _remove_tree(capture_dir)
    if summary["capture"].get("report_path"):
        summary["capture"]["report_path"] = None
        summary["capture"]["reportRetainedInCases"] = True
    if summary["capture"].get("log_path"):
        summary["capture"]["log_path"] = "maya.log"

    statuses = [str(item.get("status")) for item in summary["cases"]]
    if any(status == "fail" for status in statuses):
        summary["status"] = "fail"
        summary["exitCode"] = 1
    elif statuses and all(status == "unavailable" for status in statuses):
        summary["status"] = "not-gated"
        summary["exitCode"] = 1
    elif any(status == "unreviewed" for status in statuses):
        summary["status"] = "unreviewed"
        summary["exitCode"] = 0
    else:
        summary["status"] = "pass"
        summary["exitCode"] = 0
    _write_json(target / "summary.json", summary)
    _write_html(summary, target)
    return summary


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    manifest_default = os.environ.get(MANIFEST_ENV)
    parser.add_argument("--manifest", default=manifest_default, required=not bool(manifest_default))
    parser.add_argument("--feature", choices=FEATURES, required=True)
    parser.add_argument("--case", action="append", default=[], help="Repeatable manifest case name.")
    parser.add_argument(
        "--roi-case",
        action="append",
        default=[],
        metavar="CASE=X,Y,WIDTH,HEIGHT",
        help="Attach a fixed pixel ROI to one case; repeatable and kept out of the source manifest.",
    )
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--backend", choices=BACKENDS, default="dx11")
    parser.add_argument("--flip", default="", help="Optional path to the NVIDIA FLIP executable.")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument(
        "--out",
        default=str(OUTPUT_RELATIVE),
        help="Artifact directory; the default and required production shape is build/render-override/latest.",
    )
    return parser.parse_args(argv)


def _parse_roi_case_specs(specs: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """Parse repeatable ``CASE=x,y,width,height`` CLI ROI overrides."""

    overrides: Dict[str, Dict[str, int]] = {}
    for spec in specs:
        case_name, separator, coordinates = str(spec).partition("=")
        if not separator or not case_name:
            raise ValueError("--roi-case requires CASE=x,y,width,height: %s" % spec)
        values = coordinates.split(",")
        if len(values) != 4:
            raise ValueError("--roi-case requires four integer coordinates: %s" % spec)
        try:
            x, y, width, height = (int(value.strip()) for value in values)
        except ValueError as error:
            raise ValueError("--roi-case coordinates must be integers: %s" % spec) from error
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise ValueError("--roi-case coordinates must describe a positive ROI: %s" % spec)
        if case_name in overrides:
            raise ValueError("duplicate --roi-case for %s" % case_name)
        overrides[case_name] = {"x": x, "y": y, "width": width, "height": height}
    return overrides


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point returning the report's strict exit code."""

    args = _parse_args(argv)
    try:
        roi_overrides = _parse_roi_case_specs(args.roi_case)
        summary = run_gate(
            manifest_path=Path(args.manifest),
            feature=args.feature,
            case_names=args.case,
            roi_overrides=roi_overrides,
            maya=args.maya,
            backend=args.backend,
            output_dir=Path(args.out),
            flip_executable=args.flip or None,
            timeout=args.timeout,
            width=args.width,
            height=args.height,
        )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print("RO-0 render override visual gate failed: %s" % error, file=sys.stderr)
        return 2
    print(
        "RO-0 status=%s feature=%s backend=%s cases=%d output=%s"
        % (summary["status"], summary["feature"], summary["backend"], len(summary["cases"]), args.out)
    )
    return int(summary["exitCode"])


if __name__ == "__main__":
    raise SystemExit(main())
