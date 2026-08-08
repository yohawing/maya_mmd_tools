"""Capture the current C++ VP2 appearance for all available render cases.

This is an image-production harness with report-only parity diagnostics.  By
default it uses the settings-backed UI import route, which resolves to the
native C++ ``mmdRenderShape`` path, with the manifest camera and the same
sRGB/white-background setup as the visual gate.  It publishes successful C++
captures, their GoldenOracle references, and Oracle-to-C++ FLIP error maps to the
image-first gallery.  Cases without a model or Oracle PNG are recorded as
skipped and are not shown in the HTML viewer.  ``--direct-fast-load`` remains
available only for comparing the old direct command route.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "render-override" / "native-material-capture"
DEFAULT_GALLERY = ROOT / "build" / "render-override" / "latest"
MAYA_LAUNCH_GRACE_SECONDS = 120
MAYA_REPORT_GRACE_SECONDS = 30
MAYA_CLEANUP_GRACE_SECONDS = 120
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.render_override.common import (  # noqa: E402
    png_size as _png_size,
    resolve_mayapy as _resolve_mayapy,
)
from tools.render_override_visual_gate import (  # noqa: E402
    FLIP_THRESHOLDS,
    _default_flip_runner,
    _threshold_evaluation,
    _safe_case_dir_name,
    copy_png_as_rgb,
    _write_html,
    load_manifest_cases,
)


def _resolve_model(manifest_path: Path, case: Dict[str, Any]) -> Optional[Path]:
    """Resolve one normalized manifest case's PMX model asset."""
    raw = case.get("raw") if isinstance(case.get("raw"), dict) else {}
    assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    model_value = assets.get("model")
    if not isinstance(model_value, str) or not model_value:
        return None
    return (manifest_path.parent / model_value).resolve()


def _text(value: Any) -> str:
    """Normalize subprocess output for UTF-8 evidence files."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _clear_native_gallery(gallery_output: Path) -> int:
    """Remove only stale native gallery files, preserving Python evidence."""
    cases_dir = gallery_output / "cases"
    if not cases_dir.is_dir() or cases_dir.is_symlink():
        return 0
    removed = 0
    for case_dir in cases_dir.iterdir():
        if case_dir.is_symlink() or not case_dir.is_dir():
            continue
        for filename in ("native.png", "flip-error-native.png"):
            path = case_dir / filename
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed += 1
    return removed


def _publish_flip_error(
    case_dir: Path,
    flip_executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Create the retained Oracle-to-C++ FLIP error map for one gallery case."""
    work_dir = case_dir / ".flip-native"
    work_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("native.png", "native.txt"):
        stale = work_dir / filename
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
    comparison = _default_flip_runner(
        reference=case_dir / "reference.png",
        actual=case_dir / "native.png",
        work_dir=work_dir,
        basename="native",
        flip_executable=flip_executable,
    )
    retained = case_dir / "flip-error-native.png"
    error_map_value = comparison.get("error_map_path")
    error_map = Path(str(error_map_value)) if error_map_value else None
    if error_map is not None and error_map.is_file():
        shutil.copy2(error_map, retained)
    elif retained.is_file() or retained.is_symlink():
        retained.unlink()
    return comparison


def _publish_case(
    gallery_output: Path,
    case_name: str,
    oracle: Path,
    native_capture: Path,
    flip_executable: Optional[str] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """Publish one Oracle/native pair and retain its FLIP comparison."""
    case_dir = gallery_output / "cases" / _safe_case_dir_name(case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    copy_png_as_rgb(oracle, case_dir / "reference.png")
    copy_png_as_rgb(native_capture, case_dir / "native.png")
    comparison = _publish_flip_error(case_dir, flip_executable=flip_executable)
    return case_dir, comparison


def _run_native_case(
    *,
    manifest_path: Path,
    case: Dict[str, Any],
    maya: str,
    plugin: Path,
    output_root: Path,
    gallery_output: Path,
    port: int,
    timeout: float,
    flip_executable: Optional[str] = None,
    ui_import: bool = True,
    enforce_flip_thresholds: bool = False,
) -> Dict[str, Any]:
    """Run one isolated native capture and publish it only when valid."""
    case_name = str(case["name"])
    case_dir = output_root / _safe_case_dir_name(case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    model = _resolve_model(manifest_path, case)
    oracle_value = case.get("oracle_png")
    oracle = Path(oracle_value).resolve() if oracle_value else None
    result: Dict[str, Any] = {
        "name": case_name,
        "feature": str(case.get("feature") or "unclassified"),
        "status": "fail",
        "model": str(model) if model else None,
        "oracle": str(oracle) if oracle else None,
        "outputDir": str(case_dir),
        "port": port,
        "importRoute": "mmd_tools_ui_settings" if ui_import else "mmdFastLoad",
    }
    if model is None:
        result.update(status="skipped", reason="manifest model asset is missing")
        return result
    if not model.is_file():
        result.update(status="skipped", reason=f"model does not exist: {model}")
        return result
    if oracle is None:
        result.update(status="skipped", reason="GoldenOracle PNG is unavailable")
        return result
    if not oracle.is_file():
        result.update(status="skipped", reason=f"Oracle PNG does not exist: {oracle}")
        return result

    try:
        width, height = _png_size(oracle)
    except (OSError, ValueError) as exc:
        result.update(status="skipped", reason=f"Oracle PNG is invalid: {exc}")
        return result

    camera_path = case_dir / "camera.json"
    camera_path.write_text(
        json.dumps(case.get("camera", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    model_config_path = case_dir / "model.json"
    model_config_path.write_text(
        json.dumps({"model": str(model)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
        str(case_dir),
        "--width",
        str(width),
        "--height",
        str(height),
        "--frame",
        str(case["frame"]),
        "--camera-json",
        str(camera_path),
        "--parity",
        "--capture-only",
        "--timeout",
        str(timeout),
        "--port",
        str(port),
    ]
    if ui_import:
        command.insert(command.index("--timeout"), "--ui-import")
    env = os.environ.copy()
    env["PATH"] = str(plugin.parent) + os.pathsep + env.get("PATH", "")
    child_timeout = (
        max(1, int(timeout))
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
        result["returncode"] = completed.returncode
        stdout = _text(completed.stdout)
        stderr = _text(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        result["returncode"] = None
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr) + f"\nnative capture child timed out after {child_timeout} seconds"
    (case_dir / "capture.log").write_text(
        stdout + "\n" + stderr,
        encoding="utf-8",
    )
    report_path = case_dir / f"render_override_vp2_maya{maya}.json"
    result["nativeReport"] = str(report_path)
    if not report_path.is_file():
        result["reason"] = "native VP2 report is missing"
        return result
    try:
        native_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["reason"] = f"native VP2 report is invalid: {exc}"
        return result
    result["nativeStatus"] = native_report.get("status")
    result["nativeParityMode"] = native_report.get("parityMode")
    result["nativeCaptureOnly"] = native_report.get("captureOnly")
    result["importRoute"] = native_report.get("importRoute", result["importRoute"])
    result["uiImportOptions"] = native_report.get("uiImportOptions")
    result["uiCheckboxes"] = native_report.get("uiCheckboxes")
    result["colorManagement"] = (
        (native_report.get("parityView") or {}).get("activeColorManagement")
    )
    if (
        native_report.get("status") != "pass"
        or native_report.get("parityMode") is not True
        or native_report.get("captureOnly") is not True
    ):
        errors = native_report.get("errors") or []
        result["reason"] = "; ".join(str(error) for error in errors) or (
            "native VP2 capture did not pass in parity capture-only mode"
        )
        return result
    capture_value = (native_report.get("captures") or {}).get("ownership")
    native_capture = Path(str(capture_value)).resolve() if capture_value else None
    if native_capture is None or not native_capture.is_file():
        result["reason"] = "native ownership capture is missing"
        return result
    try:
        native_dimensions = _png_size(native_capture)
    except (OSError, ValueError) as exc:
        result["reason"] = f"native ownership capture is invalid: {exc}"
        return result
    if native_dimensions != (width, height):
        result["reason"] = (
            "native ownership capture dimensions differ from Oracle: "
            f"{native_dimensions} != {(width, height)}"
        )
        return result
    gallery_case_dir, comparison = _publish_case(
        gallery_output,
        case_name,
        oracle,
        native_capture,
        flip_executable=flip_executable,
    )
    feature = str(case.get("feature") or "unclassified")
    thresholds = FLIP_THRESHOLDS.get(feature, FLIP_THRESHOLDS["unclassified"])["full"]
    metrics = comparison.get("metrics") or {}
    threshold_evaluation = _threshold_evaluation(metrics, thresholds)
    comparison_status = str(comparison.get("status", "fail"))
    if comparison_status != "pass":
        parity_status = "fail"
    elif enforce_flip_thresholds:
        parity_status = "pass" if threshold_evaluation["status"] == "pass" else "fail"
    else:
        parity_status = "unreviewed"
    result["parity"] = {
        "status": parity_status,
        "comparisonStatus": comparison_status,
        "metrics": metrics,
        "threshold": thresholds,
        "thresholdEvaluation": threshold_evaluation,
        "errorMap": str(gallery_case_dir / "flip-error-native.png")
        if (gallery_case_dir / "flip-error-native.png").is_file()
        else None,
    }
    result.update(
        status="pass",
        dimensions={"width": width, "height": height},
        nativeCapture=str(native_capture),
        galleryCaseDir=str(gallery_case_dir),
    )
    return result


def _refresh_gallery_html(
    gallery_output: Path,
    manifest_cases: List[Dict[str, Any]],
) -> Path:
    """Refresh HTML with real names while retaining the existing gate summary."""
    summary_path = gallery_output / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary = {}
    previous = {
        str(item.get("name")): item
        for item in summary.get("cases", [])
        if isinstance(item, dict) and item.get("name")
    }
    summary["cases"] = []
    for case in manifest_cases:
        name = str(case["name"])
        item = dict(previous.get(name, {}))
        item["name"] = name
        oracle = case.get("oracle_png")
        item["oracleStatus"] = (
            "available"
            if oracle is not None and Path(oracle).is_file()
            else "unavailable"
        )
        item["oracle-status"] = item["oracleStatus"]
        summary["cases"].append(item)
    return _write_html(summary, gallery_output)


def run_gallery(
    *,
    manifest_path: Path,
    maya: str,
    output_root: Path,
    gallery_output: Path,
    plugin_path: Optional[Path] = None,
    timeout: float = 180,
    base_port: int = 7800,
    selected_names: Optional[List[str]] = None,
    flip_executable: Optional[str] = None,
    ui_import: bool = True,
    enforce_flip_thresholds: bool = False,
) -> Dict[str, Any]:
    """Capture every selected case through the C++ route used by the UI."""
    manifest_path = manifest_path.resolve()
    output_root = output_root.resolve()
    gallery_output = gallery_output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    gallery_output.mkdir(parents=True, exist_ok=True)
    plugin = (
        plugin_path.resolve()
        if plugin_path is not None
        else ROOT
        / "plug-ins"
        / maya
        / "Debug"
        / (
            "mmd_tools_cpp.bundle"
            if platform.system() == "Darwin"
            else "mmd_tools_cpp.mll"
        )
    )
    if not plugin.is_file():
        raise FileNotFoundError(plugin)
    _, manifest_cases = load_manifest_cases(manifest_path)
    selected_set = set(selected_names or [])
    selected_cases = [
        case
        for case in manifest_cases
        if not selected_set or str(case["name"]) in selected_set
    ]
    removed_stale = _clear_native_gallery(gallery_output)
    results: List[Dict[str, Any]] = []
    attempt_index = 0
    for case in selected_cases:
        name = str(case["name"])
        model = _resolve_model(manifest_path, case)
        oracle = case.get("oracle_png")
        if model is None or not model.is_file() or oracle is None or not Path(oracle).is_file():
            result = _run_native_case(
                manifest_path=manifest_path,
                case=case,
                maya=maya,
                plugin=plugin,
                output_root=output_root,
                gallery_output=gallery_output,
                port=base_port + attempt_index,
                timeout=timeout,
                flip_executable=flip_executable,
                ui_import=ui_import,
                enforce_flip_thresholds=enforce_flip_thresholds,
            )
            results.append(result)
            print(f"[{len(results)}/{len(selected_cases)}] {name}: {result['status']}")
            continue
        attempt_index += 1
        print(f"[{len(results) + 1}/{len(selected_cases)}] capturing {name}")
        result = _run_native_case(
            manifest_path=manifest_path,
            case=case,
            maya=maya,
            plugin=plugin,
            output_root=output_root,
            gallery_output=gallery_output,
            port=base_port + attempt_index - 1,
            timeout=timeout,
            flip_executable=flip_executable,
            ui_import=ui_import,
            enforce_flip_thresholds=enforce_flip_thresholds,
        )
        results.append(result)
        print(f"[{len(results)}/{len(selected_cases)}] {name}: {result['status']}")

    passed = sum(result["status"] == "pass" for result in results)
    skipped = sum(result["status"] == "skipped" for result in results)
    failed = sum(result["status"] == "fail" for result in results)
    parity_statuses = [
        str((result.get("parity") or {}).get("status"))
        for result in results
        if result.get("parity")
    ]
    if any(status == "fail" for status in parity_statuses):
        parity_status = "fail"
    elif any(status == "unreviewed" for status in parity_statuses):
        parity_status = "unreviewed"
    elif parity_statuses and all(status == "pass" for status in parity_statuses):
        parity_status = "pass"
    else:
        parity_status = "not-gated"
    color_management_states = []
    for result in results:
        state = result.get("colorManagement")
        if state and state not in color_management_states:
            color_management_states.append(state)
    summary: Dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "render-override-native-gallery",
        "status": "pass" if failed == 0 else "partial",
        "claim": "current-cpp-native-ui-appearance-only"
        if ui_import
        else "current-cpp-native-appearance-only",
        "importRoute": "mmd_tools_ui_settings" if ui_import else "mmdFastLoad",
        "parityStatus": parity_status,
        "parityThresholdsEnforced": bool(enforce_flip_thresholds),
        "nativeColorManagement": color_management_states,
        "exitCode": 1
        if failed > 0
        or (enforce_flip_thresholds and parity_status != "pass")
        else 0,
        "manifest": str(manifest_path),
        "maya": str(maya),
        "plugin": str(plugin),
        "outputDir": str(output_root),
        "gallery": str(gallery_output / "index.html"),
        "selectedCaseCount": len(selected_cases),
        "passedCaseCount": passed,
        "skippedCaseCount": skipped,
        "failedCaseCount": failed,
        "removedStaleNativeFiles": removed_stale,
        "cases": results,
    }
    summary_path = output_root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # The image gallery is the user-facing output.  Keep its summary aligned
    # with the images even when durable capture details live in a separate
    # output directory; otherwise latest/index.html would show C++ UI images
    # beside the previous Python capture report.
    gallery_summary_path = gallery_output / "summary.json"
    if gallery_summary_path.resolve() != summary_path.resolve():
        gallery_summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    html_path = _refresh_gallery_html(gallery_output, manifest_cases)
    summary["html"] = str(html_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if gallery_summary_path.resolve() != summary_path.resolve():
        gallery_summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    """Run the all-case C++ native appearance capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gallery-out", type=Path, default=DEFAULT_GALLERY)
    parser.add_argument("--plugin", type=Path, default=None)
    parser.add_argument(
        "--flip",
        default=None,
        help="Optional NVIDIA FLIP executable path; defaults to PATH lookup.",
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--base-port", type=int, default=7800)
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        default=None,
        help="Capture only this case; repeat the option for a focused subset.",
    )
    parser.add_argument(
        "--direct-fast-load",
        action="store_true",
        help="Use direct mmdFastLoad instead of the settings-backed UI import route.",
    )
    parser.add_argument(
        "--enforce-flip-threshold",
        action="store_true",
        help="Fail the parity status when the fixed FLIP contract is exceeded.",
    )
    args = parser.parse_args(argv)
    manifest = args.manifest
    if manifest is None:
        manifest_value = os.environ.get("GOLDEN_ORACLE_RENDER_MANIFEST")
        if not manifest_value:
            parser.error("--manifest or GOLDEN_ORACLE_RENDER_MANIFEST is required")
        manifest = Path(manifest_value)
    try:
        summary = run_gallery(
            manifest_path=manifest,
            maya=str(args.maya),
            output_root=args.out,
            gallery_output=args.gallery_out,
            plugin_path=args.plugin,
            timeout=args.timeout,
            base_port=args.base_port,
            selected_names=args.cases,
            flip_executable=args.flip or None,
            ui_import=not args.direct_fast_load,
            enforce_flip_thresholds=args.enforce_flip_threshold,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["exitCode"])


if __name__ == "__main__":
    raise SystemExit(main())
