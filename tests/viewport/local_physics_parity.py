"""Run Maya Bullet versus native physics parity against local PMX/VMD assets.

The manifest-oriented runner keeps license-sensitive models outside the repo,
stages non-ASCII paths for Maya, and writes JSON plus Markdown evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import maya.standalone

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRAMES = [0, 1, 5, 10, 20, 30]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", default="build/reports/local_physics_parity.json")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--frame", action="append", type=int, default=[])
    parser.add_argument("--fps", type=int, default=30, choices=(30, 60))
    parser.add_argument("--mesh-threshold", type=float, default=10.0)
    parser.add_argument("--strict-local", action="store_true")
    return parser.parse_args()


def _load_cases(manifest_path: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_cases = manifest.get("cases") or manifest.get("assets") or []
    cases: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_cases):
        pmx = raw.get("pmx") or raw.get("model")
        vmd = raw.get("vmd") or raw.get("motion")
        case = {
            "name": str(raw.get("name") or pmx or f"case_{index}"),
            "frames": list(raw.get("frames") or DEFAULT_FRAMES),
            "mesh_threshold": raw.get("mesh_threshold"),
        }
        if not pmx or not vmd:
            case.update({"pmx": str(pmx or ""), "vmd": str(vmd or ""), "invalid": True})
        else:
            pmx_path, vmd_path = Path(str(pmx)), Path(str(vmd))
            if not pmx_path.is_absolute():
                pmx_path = manifest_path.parent / pmx_path
            if not vmd_path.is_absolute():
                vmd_path = manifest_path.parent / vmd_path
            case.update({"pmx": str(pmx_path.resolve()), "vmd": str(vmd_path.resolve())})
        cases.append(case)
    return cases


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _capture_scene(pmx: Path, vmd: Path, frames: list[int], fps: int, native: bool) -> dict[str, Any]:
    from tests.viewport.local_asset_motion_compare import _capture_vertices
    from tests.viewport.native_physics_bake_capture import _run_bake_scene

    scene = _run_bake_scene(
        pmx_path=pmx,
        vmd_path=vmd,
        fps=fps,
        use_native_physics_bake=native,
        eval_frames=frames,
    )
    scene["mesh_vertices"] = _capture_vertices(scene["root"], frames)
    return scene


def _run_case(case: dict[str, Any], args: argparse.Namespace, stage: Any) -> dict[str, Any]:
    from mmd_tools.services.settings_service import SettingsService
    from tests.viewport.native_physics_parity import (
        apply_import_scale,
        compare_bullet_world_sanity,
        compare_bullet_world_transform_delta,
        compare_mesh_vertex_samples,
        static_pmx_extent,
    )

    result = {key: case[key] for key in ("name", "pmx", "vmd")}
    frames = args.frame or list(case["frames"])
    result["frames"] = frames
    if case.get("invalid"):
        return {**result, "status": "failed" if args.strict_local else "skipped", "reason": "manifest_case_requires_pmx_and_vmd"}
    pmx, vmd = Path(case["pmx"]), Path(case["vmd"])
    if not pmx.is_file() or not vmd.is_file():
        return {
            **result,
            "status": "failed" if args.strict_local else "skipped",
            "reason": "asset_not_found",
            "pmxExists": pmx.is_file(),
            "vmdExists": vmd.is_file(),
        }
    staged_pmx, staged_vmd = stage.resolve(pmx), stage.resolve(vmd)
    result.update({"stagedPmx": str(staged_pmx), "stagedVmd": str(staged_vmd)})
    extent = apply_import_scale(static_pmx_extent(staged_pmx), SettingsService().resolve_import_scale())
    baseline = _capture_scene(staged_pmx, staged_vmd, frames, args.fps, False)
    native = _capture_scene(staged_pmx, staged_vmd, frames, args.fps, True)
    result["physicsBoneCounts"] = {
        "bullet": len(baseline["physics_bones"]),
        "native": len(native["physics_bones"]),
    }
    result["nativePhysicsRouting"] = native["physics_routing"]
    result["boneWorldParity"] = compare_bullet_world_sanity(
        baseline["samples"], native["samples"], frames, extent
    )
    result["boneWorldTransformDelta"] = compare_bullet_world_transform_delta(
        baseline["samples"], native["samples"], frames
    )
    threshold = case.get("mesh_threshold")
    result["meshWorldParity"] = compare_mesh_vertex_samples(
        baseline["mesh_vertices"],
        native["mesh_vertices"],
        frames,
        args.mesh_threshold if threshold is None else float(threshold),
    )
    route_used = bool(native["physics_routing"].get("used"))
    result["status"] = (
        "passed"
        if baseline["physics_bones"]
        and native["physics_bones"]
        and route_used
        and result["boneWorldParity"]["passed"]
        and result["meshWorldParity"]["passed"]
        else "failed"
    )
    return result


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = ["# Local Physics Parity", "", f"- status: `{report['status']}`", f"- cases: `{len(report['cases'])}`", ""]
    for case in report["cases"]:
        lines.extend([f"## {case['name']}", "", f"- status: `{case.get('status')}`", f"- frames: `{case.get('frames')}`"])
        bone = case.get("boneWorldParity")
        transform_delta = case.get("boneWorldTransformDelta")
        mesh = case.get("meshWorldParity")
        if bone:
            lines.append(f"- bone world: passed=`{bone['passed']}`, samples=`{bone['comparedSamples']}`, failures=`{bone['failureCount']}`")
        if transform_delta:
            lines.append(
                "- bone delta (report-only): "
                f"translation max=`{transform_delta['maxTranslation']}`, "
                f"rotation max deg=`{transform_delta['maxRotationDegrees']}`, "
                f"rotation RMS deg=`{transform_delta['rmsRotationDegrees']}`, "
                f"rotation p95 deg=`{transform_delta['p95RotationDegrees']}`"
            )
        if mesh:
            lines.append(f"- mesh world: passed=`{mesh['passed']}`, vertices=`{mesh['comparedVertexSamples']}`, max=`{mesh['max']}`, RMS=`{mesh['rms']}`, threshold=`{mesh['threshold']}`")
        if case.get("reason") or case.get("error"):
            lines.append(f"- reason: `{case.get('reason') or case.get('error')}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    _initialize()
    manifest = Path(args.manifest).resolve()
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"status": "passed", "manifest": str(manifest), "cases": []}
    if not manifest.is_file():
        report.update({"status": "failed", "error": f"manifest not found: {manifest}"})
    else:
        from tests.viewport.local_asset_motion_compare import _PathStaging

        cases = _load_cases(manifest)
        selected = set(args.case)
        cases = [case for case in cases if not selected or case["name"] in selected]
        stage = _PathStaging(ROOT / "build" / "staging" / "local_physics_parity")
        stage.setup()
        try:
            for case in cases:
                try:
                    result = _run_case(case, args, stage)
                except Exception as exc:
                    result = {**case, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
                report["cases"].append(result)
        finally:
            stage.cleanup()
        if not report["cases"]:
            report["status"] = "failed" if args.strict_local else "skipped"
        elif any(case["status"] == "failed" for case in report["cases"]):
            report["status"] = "failed"
        elif all(case["status"] == "skipped" for case in report["cases"]):
            report["status"] = "failed" if args.strict_local else "skipped"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    _write_markdown(report, out.with_suffix(".md"))
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {out.with_suffix('.md')}")
    print(f"Status: {report['status']}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
