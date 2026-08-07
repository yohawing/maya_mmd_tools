"""Compare the native UI VP2 material witness with GoldenOracle and Python.

This is a report-only diagnostic harness for the first C++ native MMD material
slice.  It uses one GoldenOracle manifest case, captures the opt-in native
shape with the same manifest camera and image size as the Python visual
capture, then runs NVIDIA FLIP against both the Oracle image and the current
Python baseline.  It does not claim full texture/toon/outline parity or change
the normal Python importer path.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE = "fixture-render-generated-visual-mmd-alpha-blend-overlap"
DEFAULT_OUTPUT = ROOT / "build" / "render-override" / "native-material-parity"
MAYA_LAUNCH_GRACE_SECONDS, MAYA_REPORT_GRACE_SECONDS, MAYA_CLEANUP_GRACE_SECONDS = 120, 30, 120
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_location import mayapy as _mayapy_for_version  # noqa: E402
from tools.render_override_visual_gate import (  # noqa: E402
    FLIP_THRESHOLDS,
    _default_flip_runner,
    _safe_case_dir_name,
    _threshold_evaluation,
    _write_html,
    load_manifest_cases,
)

def _load_case(
    manifest_path: Path, case_name: str
) -> Tuple[Path, Dict[str, Any], Path, Path, int]:
    """Resolve one manifest case, its model, camera, and GoldenOracle PNG."""
    manifest_path = manifest_path.resolve()
    _, cases = load_manifest_cases(manifest_path)
    selected = next((case for case in cases if case["name"] == case_name), None)
    if selected is None:
        raise ValueError(f"manifest case not found: {case_name}")
    raw_assets = selected.get("raw", {}).get("assets", {})
    if not isinstance(raw_assets, dict) or not isinstance(raw_assets.get("model"), str):
        raise ValueError(f"manifest model asset missing for {case_name}")
    model = (manifest_path.parent / raw_assets["model"]).resolve()
    oracle = Path(selected["oracle_png"]).resolve()
    camera = selected.get("camera")
    if not isinstance(camera, dict):
        raise ValueError(f"manifest camera missing for {case_name}")
    if not model.is_file():
        raise FileNotFoundError(model)
    if not oracle.is_file():
        raise FileNotFoundError(oracle)
    return manifest_path, {"name": case_name, "camera": camera}, model, oracle, int(selected["frame"])

def _png_size(path: Path) -> Tuple[int, int]:
    """Read PNG dimensions without requiring Pillow."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"not a PNG: {path}")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid PNG dimensions: {path}")
    return width, height

def _run_flip(
    reference: Path,
    actual: Path,
    output_dir: Path,
    basename: str,
    flip_executable: str,
) -> Dict[str, Any]:
    """Run FLIP once and retain its text/error map as durable local evidence."""
    retained_error_map = output_dir / f"flip-error-{basename}.png"
    try:
        retained_error_map.unlink()
    except FileNotFoundError:
        pass
    with tempfile.TemporaryDirectory(prefix=f"flip-{basename}-", dir=output_dir) as work_dir_name:
        comparison = _default_flip_runner(
            reference=reference,
            actual=actual,
            work_dir=Path(work_dir_name),
            basename=basename,
            flip_executable=flip_executable,
        )
        generated_text = Path(str(comparison["text_path"])) if comparison.get("text_path") else None
        text = (
            generated_text.read_text(encoding="utf-8", errors="replace")
            if generated_text and generated_text.is_file()
            else str(comparison.get("stdout", ""))
            + str(comparison.get("stderr", ""))
        )
        metrics = comparison.get("metrics") or {}
        error_map = Path(str(comparison["error_map_path"])) if comparison.get("error_map_path") else None
        if error_map and error_map.is_file():
            shutil.copy2(error_map, retained_error_map)
    threshold = FLIP_THRESHOLDS["transparency"]["full"]
    return {
        **comparison,
        "text_path": None,
        "error_map_path": None,
        "status": comparison.get("status", "fail"),
        "metrics": metrics,
        "thresholdEvaluation": _threshold_evaluation(metrics, threshold),
        "text": text,
        "textPath": str(output_dir / f"flip-{basename}.txt"),
        "errorMap": str(retained_error_map) if retained_error_map.is_file() else None,
    }


def _publish_native_gallery(
    gallery_output: Optional[Path],
    case_name: str,
    oracle: Path,
    native_capture: Path,
    oracle_comparison: Dict[str, Any],
) -> None:
    """Publish only native artifacts to the image-first HTML gallery."""

    if gallery_output is None:
        return
    case_dir = Path(gallery_output).resolve() / "cases" / _safe_case_dir_name(case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(oracle, case_dir / "reference.png")
    shutil.copy2(native_capture, case_dir / "native.png")
    error_map = Path(str(oracle_comparison.get("errorMap", "")))
    if error_map.is_file():
        shutil.copy2(error_map, case_dir / "flip-error-native.png")
    summary_path = Path(gallery_output).resolve() / "summary.json"
    if summary_path.is_file():
        _write_html(json.loads(summary_path.read_text(encoding="utf-8")), Path(gallery_output))


def _resolve_mayapy(maya: str) -> Path:
    """Resolve the mayapy executable through the shared version-aware helper."""
    candidate = _mayapy_for_version(str(maya))
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"could not resolve mayapy for Maya {maya}: {candidate}")


def run_parity(
    manifest_path: Path,
    case_name: str,
    maya: str,
    output_dir: Path,
    python_baseline: Optional[Path] = None,
    plugin_path: Optional[Path] = None,
    flip_executable: Optional[str] = None,
    timeout: int = 180,
    port: int = 7745,
    enforce_thresholds: bool = False,
    gallery_output: Optional[Path] = None,
    ui_import: bool = True,
) -> Dict[str, Any]:
    """Capture one native case and compare it against Oracle and Python."""
    if case_name != DEFAULT_CASE:
        raise ValueError(
            "native material parity currently supports only "
            f"{DEFAULT_CASE}; the VP2 probe is alpha-fixture specific"
        )
    _, case, model, oracle, frame = _load_case(manifest_path, case_name)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if python_baseline is not None:
        python_baseline = Path(python_baseline).resolve()
    width, height = _png_size(oracle)
    if python_baseline is None or not python_baseline.is_file():
        raise FileNotFoundError("Python baseline is missing")
    try:
        baseline_dimensions = _png_size(python_baseline)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Python baseline is not a valid PNG: {exc}") from exc
    if baseline_dimensions != (width, height):
        raise ValueError("Python baseline dimensions differ from GoldenOracle")
    camera_path = output_dir / "camera.json"
    camera_path.write_text(json.dumps(case["camera"], indent=2), encoding="utf-8")
    model_config_path = output_dir / "model.json"
    model_config_path.write_text(
        json.dumps({"model": str(model)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    plugin = (
        plugin_path.resolve()
        if plugin_path is not None
        else ROOT
        / "plug-ins"
        / maya
        / "Debug"
        / ("mmd_tools_cpp.bundle" if platform.system() == "Darwin" else "mmd_tools_cpp.mll")
    )
    if not plugin.is_file():
        raise FileNotFoundError(plugin)
    mayapy = _resolve_mayapy(maya)
    command = [
        str(mayapy),
        str(ROOT / "tools" / "render_override_vp2_ownership_e2e.py"),
        "--maya",
        maya,
        "--model-json",
        str(model_config_path),
        "--plugin",
        str(plugin),
        "--out-dir",
        str(output_dir),
        "--width",
        str(width),
        "--height",
        str(height),
        "--frame",
        str(frame),
        "--camera-json",
        str(camera_path),
        "--parity",
        "--timeout",
        str(timeout),
        "--port",
        str(port),
    ]
    if ui_import:
        command.append("--ui-import")
    env = os.environ.copy()
    env["PATH"] = str(plugin.parent) + os.pathsep + env.get("PATH", "")
    child_timeout = (
        max(1, timeout)
        + MAYA_LAUNCH_GRACE_SECONDS
        + MAYA_REPORT_GRACE_SECONDS
        + MAYA_CLEANUP_GRACE_SECONDS
    )
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=child_timeout,
        )
        capture_returncode = completed.returncode
        capture_stdout = completed.stdout
        capture_stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        completed = None
        capture_returncode = None
        capture_stdout = str(exc.stdout or "")
        capture_stderr = (
            str(exc.stderr or "")
            + f"\nnative parity child timed out after {child_timeout} seconds"
        )
    report_path = output_dir / f"render_override_vp2_maya{maya}.json"
    result: Dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "render-override-native-material-parity",
        "status": "fail",
        "exitCode": 1,
        "case": case_name,
        "maya": maya,
        "model": str(model),
        "oracle": str(oracle),
        "pythonBaseline": str(python_baseline) if python_baseline else None,
        "modelConfig": str(model_config_path),
        "dimensions": {"width": width, "height": height},
        "frame": frame,
        "captureCommand": command,
        "captureReturncode": capture_returncode,
        "captureStdout": capture_stdout,
        "captureStderr": capture_stderr,
        "nativeReport": str(report_path),
        "comparisons": {},
        "claim": "report-only-native-mmd-material-subset",
        "importRoute": "mmd_tools_ui_settings" if ui_import else "mmdFastLoad",
    }
    if completed is None or completed.returncode != 0 or not report_path.is_file():
        (output_dir / "capture.log").write_text(
            (capture_stdout or "") + "\n" + (capture_stderr or ""), encoding="utf-8"
        )
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    native_report = json.loads(report_path.read_text(encoding="utf-8"))
    result["nativeStatus"] = native_report.get("status")
    result["nativeParityMode"] = native_report.get("parityMode")
    result["importRoute"] = native_report.get("importRoute", result["importRoute"])
    result["uiImportOptions"] = native_report.get("uiImportOptions")
    result["uiCheckboxes"] = native_report.get("uiCheckboxes")
    if native_report.get("status") != "pass" or native_report.get("parityMode") is not True:
        result["error"] = "native probe did not pass in parity mode"
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    native_capture = Path(native_report.get("captures", {}).get("ownership", ""))
    if not native_capture.is_file():
        result["error"] = "native ownership capture missing"
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    try:
        native_dimensions = _png_size(native_capture)
    except (OSError, ValueError) as exc:
        result["error"] = f"native ownership capture is not a valid PNG: {exc}"
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    if native_dimensions != (width, height):
        result["error"] = (
            "native ownership capture dimensions differ from GoldenOracle: "
            f"{native_dimensions} != {(width, height)}"
        )
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    native_copy = output_dir / "native.png"
    shutil.copy2(native_capture, native_copy)
    shutil.copy2(oracle, output_dir / "reference.png")
    if python_baseline is None or not python_baseline.is_file():
        result["error"] = "Python baseline is missing"
        result["exitCode"] = 1
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    flip = flip_executable or shutil.which("flip")
    if not flip:
        result["error"] = "FLIP executable not found"
        (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    oracle_comparison = _run_flip(oracle, native_copy, output_dir, "oracle-native", flip)
    (output_dir / "flip-oracle-native.txt").write_text(
        oracle_comparison["text"], encoding="utf-8"
    )
    result["comparisons"]["oracleVsNative"] = oracle_comparison
    _publish_native_gallery(
        gallery_output,
        case_name,
        oracle,
        native_copy,
        oracle_comparison,
    )
    if python_baseline and python_baseline.is_file():
        if _png_size(python_baseline) != (width, height):
            result["error"] = "Python baseline dimensions differ from GoldenOracle"
        else:
            baseline_copy = output_dir / "python.png"
            shutil.copy2(python_baseline, baseline_copy)
            python_comparison = _run_flip(python_baseline, native_copy, output_dir, "python-native", flip)
            (output_dir / "flip-python-native.txt").write_text(
                python_comparison["text"], encoding="utf-8"
            )
            result["comparisons"]["pythonVsNative"] = python_comparison
    if "error" not in result:
        comparisons = list(result["comparisons"].values())
        threshold_statuses = [
            item["thresholdEvaluation"]["status"] for item in comparisons
        ]
        execution_failed = any(item.get("status") != "pass" for item in comparisons)
        threshold_failed = any(status != "pass" for status in threshold_statuses)
        if execution_failed:
            result["status"] = "fail"
        elif enforce_thresholds:
            result["status"] = "fail" if threshold_failed else "pass"
        else:
            result["status"] = "unreviewed"
        result["exitCode"] = (
            1
            if execution_failed or (threshold_failed and enforce_thresholds)
            else 0
        )
    (output_dir / "parity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

def main(argv: Optional[List[str]] = None) -> int:
    """Run the native material parity report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--python-baseline", type=Path, default=None)
    parser.add_argument("--plugin", type=Path, default=None)
    parser.add_argument("--flip", default=None)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--port", type=int, default=7745)
    parser.add_argument("--enforce-flip-threshold", action="store_true")
    parser.add_argument(
        "--direct-fast-load",
        action="store_true",
        help="Use direct mmdFastLoad instead of the settings-backed UI import route.",
    )
    parser.add_argument(
        "--gallery-out",
        type=Path,
        default=ROOT / "build" / "render-override" / "latest",
        help="Publish native/reference/FLIP images for the native-only HTML gallery.",
    )
    args = parser.parse_args(argv)
    manifest = args.manifest
    if manifest is None:
        manifest_value = os.environ.get("GOLDEN_ORACLE_RENDER_MANIFEST")
        if not manifest_value:
            parser.error("--manifest or GOLDEN_ORACLE_RENDER_MANIFEST is required")
        manifest = Path(manifest_value)
    baseline = args.python_baseline
    if baseline is None:
        parser.error("--python-baseline is required; run the Python visual gate first")
    result = run_parity(
        manifest_path=manifest,
        case_name=args.case,
        maya=str(args.maya),
        output_dir=args.out / _safe_case_dir_name(args.case),
        python_baseline=baseline,
        plugin_path=args.plugin,
        flip_executable=args.flip,
        timeout=args.timeout,
        port=args.port,
        enforce_thresholds=args.enforce_flip_threshold,
        gallery_output=args.gallery_out,
        ui_import=not args.direct_fast_load,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result.get("exitCode", 1))


if __name__ == "__main__":
    raise SystemExit(main())
