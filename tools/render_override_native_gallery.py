"""Capture the current C++ VP2 appearance for all available render cases.

This is an image-production harness, not a parity gate.  It uses the native
``mmdFastLoad(..., vp2Ownership=True)`` path with the manifest camera and the
same sRGB/white-background setup as the visual gate, then publishes only
successful C++ captures and their GoldenOracle references to the image-first
gallery.  Cases without a model or Oracle PNG are recorded as skipped and are
not shown in the HTML viewer.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
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

from tests.common.maya_location import mayapy as _mayapy_for_version  # noqa: E402
from tools.render_override_visual_gate import (  # noqa: E402
    _safe_case_dir_name,
    copy_png_as_rgb,
    _write_html,
    load_manifest_cases,
)


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


def _resolve_model(manifest_path: Path, case: Dict[str, Any]) -> Optional[Path]:
    """Resolve one normalized manifest case's PMX model asset."""
    raw = case.get("raw") if isinstance(case.get("raw"), dict) else {}
    assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    model_value = assets.get("model")
    if not isinstance(model_value, str) or not model_value:
        return None
    return (manifest_path.parent / model_value).resolve()


def _resolve_mayapy(maya: str) -> Path:
    """Resolve the target Maya version through the shared project helper."""
    candidate = _mayapy_for_version(str(maya))
    if candidate.is_file():
        return candidate.resolve()
    raise FileNotFoundError(f"could not resolve mayapy for Maya {maya}: {candidate}")


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


def _publish_case(
    gallery_output: Path,
    case_name: str,
    oracle: Path,
    native_capture: Path,
) -> Path:
    """Publish one Oracle/native pair to the current image gallery."""
    case_dir = gallery_output / "cases" / _safe_case_dir_name(case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    copy_png_as_rgb(oracle, case_dir / "reference.png")
    copy_png_as_rgb(native_capture, case_dir / "native.png")
    stale_flip = case_dir / "flip-error-native.png"
    if stale_flip.is_file() or stale_flip.is_symlink():
        stale_flip.unlink()
    return case_dir


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
        "status": "fail",
        "model": str(model) if model else None,
        "oracle": str(oracle) if oracle else None,
        "outputDir": str(case_dir),
        "port": port,
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
    gallery_case_dir = _publish_case(gallery_output, case_name, oracle, native_capture)
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
) -> Dict[str, Any]:
    """Capture every selected manifest case through the C++ VP2 route."""
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
        )
        results.append(result)
        print(f"[{len(results)}/{len(selected_cases)}] {name}: {result['status']}")

    passed = sum(result["status"] == "pass" for result in results)
    skipped = sum(result["status"] == "skipped" for result in results)
    failed = sum(result["status"] == "fail" for result in results)
    summary: Dict[str, Any] = {
        "schemaVersion": 1,
        "kind": "render-override-native-gallery",
        "status": "pass" if failed == 0 else "partial",
        "claim": "current-cpp-native-appearance-only",
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
    html_path = _refresh_gallery_html(gallery_output, manifest_cases)
    summary["html"] = str(html_path)
    summary_path.write_text(
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
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--base-port", type=int, default=7800)
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        default=None,
        help="Capture only this case; repeat the option for a focused subset.",
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
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failedCaseCount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
