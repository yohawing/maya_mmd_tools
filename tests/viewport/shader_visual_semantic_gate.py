"""Validate DX11 shader regressions with semantic Maya viewport image checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from tests.viewport.visual_regression_compare import _decode_png
except ModuleNotFoundError:  # Direct script execution adds tests/viewport to sys.path.
    from visual_regression_compare import _decode_png


OUTLINE_CASE = "fixture-render-generated-visual-mmd-outline-normal-silhouette"
HAIR_CASE = "fixture-render-generated-visual-mmd-tga-regular-hair-alpha-opaque"
CASE_MIN_FOREGROUND = {
    OUTLINE_CASE: 0.35,
    HAIR_CASE: 0.20,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def _pixel_metrics(path: Path) -> dict[str, object]:
    """Return foreground and sentinel-color coverage for a viewport capture."""
    width, height, pixels = _decode_png(path)

    foreground = 0
    sentinel_magenta = 0
    for red, green, blue in pixels:
        if min(red, green, blue) < 245:
            foreground += 1
        if red >= 180 and blue >= 180 and green <= 100 and red - green >= 80 and blue - green >= 80:
            sentinel_magenta += 1

    pixel_count = len(pixels)
    center = pixels[(height // 2) * width + width // 2]
    return {
        "width": width,
        "height": height,
        "pixelCount": pixel_count,
        "foregroundPixels": foreground,
        "foregroundFraction": foreground / pixel_count,
        "sentinelMagentaPixels": sentinel_magenta,
        "centerRgb": list(center),
    }


def _validate_case(result: dict[str, object]) -> dict[str, object]:
    """Validate one capture and return its structured semantic evidence."""
    name = str(result.get("name", ""))
    failures: list[str] = []
    if not result.get("ok"):
        failures.append("Maya capture reported failure")

    actual_path = Path(str(result.get("actual_png", "")))
    diagnostics_path = Path(str(result.get("diagnostics", "")))
    if not actual_path.is_file():
        failures.append(f"missing actual image: {actual_path}")
        metrics: dict[str, object] = {}
    else:
        metrics = _pixel_metrics(actual_path)
        minimum = CASE_MIN_FOREGROUND[name]
        if float(metrics["foregroundFraction"]) < minimum:
            failures.append(
                f"foreground coverage {float(metrics['foregroundFraction']):.6f} is below {minimum:.6f}"
            )
        if max(metrics["centerRgb"]) >= 245 and min(metrics["centerRgb"]) >= 245:
            failures.append(f"center pixel is background-like: {metrics['centerRgb']}")
        if int(metrics["sentinelMagentaPixels"]) > 8:
            failures.append(
                f"outline sentinel leaked into body pixels: {metrics['sentinelMagentaPixels']} magenta pixels"
            )

    diagnostics: dict[str, object] = {}
    shader_evidence: list[dict[str, object]] = []
    if not diagnostics_path.is_file():
        failures.append(f"missing diagnostics: {diagnostics_path}")
    else:
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        sentinel = diagnostics.get("debug_actions", {}).get("outlineSentinel", [])
        if not sentinel or any("error" in item for item in sentinel):
            failures.append(f"outline sentinel was not applied cleanly: {sentinel!r}")

        for shader in diagnostics.get("shaders", []):
            attrs = shader.get("attrs", {})
            technique = str(attrs.get("technique", ""))
            edge_size = float(attrs.get("EdgeSize", 0.0))
            evidence = {
                "shader": shader.get("name"),
                "technique": technique,
                "edgeSize": edge_size,
            }
            shader_evidence.append(evidence)
            if technique not in {"MMDTechnique", "MMDTechniqueDoubleSided"}:
                failures.append(f"{shader.get('name')} selected an unknown technique: {technique}")
            if abs(edge_size) > 1.0e-6:
                failures.append(f"{shader.get('name')} imported with non-zero EdgeSize: {edge_size}")
        if not shader_evidence:
            failures.append("no DX11 shader diagnostics were captured")

    return {
        "name": name,
        "status": "pass" if not failures else "fail",
        "actual": str(actual_path),
        "diagnostics": str(diagnostics_path),
        "metrics": metrics,
        "shaders": shader_evidence,
        "failures": failures,
    }


def main() -> int:
    """Validate the required semantic cases from a Maya capture report."""
    args = _parse_args()
    capture = json.loads(args.capture_report.read_text(encoding="utf-8"))
    by_name = {str(item.get("name", "")): item for item in capture.get("results", [])}

    missing = [name for name in CASE_MIN_FOREGROUND if name not in by_name]
    results = [_validate_case(by_name[name]) for name in CASE_MIN_FOREGROUND if name in by_name]
    failures = [failure for result in results for failure in result["failures"]]
    failures.extend(f"missing required case: {name}" for name in missing)
    report = {
        "schemaVersion": 1,
        "kind": "maya-dx11-shader-semantic-gate",
        "status": "pass" if not failures else "fail",
        "captureReport": str(args.capture_report),
        "results": results,
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DX11 shader semantic gate: {report['status']}")
    for result in results:
        print(
            f"{result['name']}: {result['status']} "
            f"foreground={result['metrics'].get('foregroundFraction', 0.0):.6f} "
            f"sentinel={result['metrics'].get('sentinelMagentaPixels', 0)}"
        )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
