"""Validate two native physics bake route reports for deterministic output."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

REQUIRED_FEATURE_FLAGS = 0x3
REQUIRED_ASSERTIONS = {
    "native_physics_bake_used",
    "physics_bone_local_transform_delta",
    "preview_constraints_blocked",
}


def _canonical(report: dict[str, Any]) -> dict[str, Any]:
    """Select stable, meaningful native physics outputs for comparison."""
    baseline = report.get("baseline") or {}
    native = report.get("native") or {}
    return {
        "feature_flags": report.get("feature_flags"),
        "native_physics_available": report.get("native_physics_available"),
        "eval_frames": report.get("eval_frames"),
        "delta_epsilon": report.get("delta_epsilon"),
        "baseline": {
            "physics_routing": baseline.get("physics_routing"),
            "physics_bones": baseline.get("physics_bones"),
            "samples": baseline.get("samples"),
        },
        "native": {
            "physics_routing": native.get("physics_routing"),
            "physics_bones": native.get("physics_bones"),
            "samples": native.get("samples"),
            "preview_constraints": native.get("preview_constraints"),
        },
        "delta": report.get("delta"),
        "assertions": report.get("assertions"),
    }


def validate_report(report: dict[str, Any], expected_runtime: Path) -> list[str]:
    """Return fail-closed contract errors for one E2E report."""
    errors: list[str] = []
    if report.get("status") != "passed":
        errors.append(f"status is not passed: {report.get('status')!r}")
    runtime = str(report.get("runtime_library_path") or "")
    if not runtime or os.path.normcase(str(Path(runtime).resolve())) != os.path.normcase(str(expected_runtime.resolve())):
        errors.append(f"runtime path mismatch: {runtime!r}")
    flags = report.get("feature_flags")
    if not isinstance(flags, int) or (flags & REQUIRED_FEATURE_FLAGS) != REQUIRED_FEATURE_FLAGS:
        errors.append(f"required feature flags missing: {flags!r}")
    routing = ((report.get("native") or {}).get("physics_routing") or {})
    if routing.get("used") is not True:
        errors.append("native physics routing was not used")
    assertions = report.get("assertions")
    if not isinstance(assertions, list):
        errors.append("assertions list missing")
    else:
        names = {item.get("name") for item in assertions if isinstance(item, dict)}
        if not REQUIRED_ASSERTIONS.issubset(names):
            errors.append("required assertions missing")
        if any(item.get("pass") is not True for item in assertions if isinstance(item, dict)):
            errors.append("one or more assertions failed")
    delta = report.get("delta") or {}
    if delta.get("passed") is not True or not delta.get("comparedChannels"):
        errors.append("meaningful native local-channel delta missing")
    return errors


def compare_reports(first: dict[str, Any], second: dict[str, Any], expected_runtime: Path) -> dict[str, Any]:
    """Return a deterministic comparison summary for two reports."""
    first_errors = validate_report(first, expected_runtime)
    second_errors = validate_report(second, expected_runtime)
    deterministic = _canonical(first) == _canonical(second)
    errors = [*(f"run1: {error}" for error in first_errors), *(f"run2: {error}" for error in second_errors)]
    if not deterministic:
        errors.append("meaningful native physics outputs differ")
    return {
        "status": "pass" if not errors else "fail",
        "deterministic": deterministic,
        "expectedRuntimePath": str(expected_runtime.resolve()),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run1", required=True, type=Path)
    parser.add_argument("--run2", required=True, type=Path)
    parser.add_argument("--ffi", required=True, type=Path)
    parser.add_argument("--out-json", required=True, type=Path)
    parser.add_argument("--out-md", required=True, type=Path)
    args = parser.parse_args()
    missing = [path for path in (args.run1, args.run2, args.ffi) if not path.is_file()]
    if missing:
        result = {"status": "fail", "deterministic": False, "errors": [f"missing file: {path}" for path in missing]}
    else:
        try:
            first = json.loads(args.run1.read_text(encoding="utf-8"))
            second = json.loads(args.run2.read_text(encoding="utf-8"))
            result = compare_reports(first, second, args.ffi)
        except (OSError, ValueError, TypeError) as exc:
            result = {"status": "fail", "deterministic": False, "errors": [f"invalid report: {exc}"]}
    result.update({"run1": str(args.run1.resolve()), "run2": str(args.run2.resolve())})
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Native Physics Determinism",
        "",
        f"- Status: {result['status']}",
        f"- Deterministic: {result['deterministic']}",
        f"- Run 1: `{result['run1']}`",
        f"- Run 2: `{result['run2']}`",
    ]
    lines.extend(f"- Error: {error}" for error in result.get("errors", []))
    args.out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
