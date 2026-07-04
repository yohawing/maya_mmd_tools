"""Compare local PMX/VMD motion playback across Maya import modes and FBX.

This is a report-oriented verifier for local assets that are too large or
license-sensitive to commit as fixtures. It writes JSON + Markdown reports with:

- Bake vs Rig mesh world-vertex comparison.
- Rig scene exported to FBX, re-imported, then mesh world-vertex comparison.

The comparison intentionally uses mesh world positions, not only joint rotations,
because incorrect jointOrient handling can leave bones plausible while the skinned
mesh collapses.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import traceback
from pathlib import Path
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel
import maya.standalone


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Path staging — alias non-ASCII paths via Windows junctions / hard-links
# so that Maya string attributes (mmd_source_file etc.) stay within codepage.
# ---------------------------------------------------------------------------

def _is_ascii_safe(p: str) -> bool:
    try:
        p.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


class _PathStaging:
    """Create ASCII-safe junctions (dirs) and hard-links (files) for non-ASCII
    asset paths.  PMX directories get a junction so relative texture paths still
    resolve.  Individual VMD files get a hard-link with an ASCII name."""

    def __init__(self, staging_root: Path) -> None:
        self._root = staging_root.resolve()
        self._junctions: list[Path] = []
        self._hardlinks: list[Path] = []
        self._dir_map: dict[str, Path] = {}

    def setup(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphans()

    def _cleanup_orphans(self) -> None:
        """Remove junctions/links left behind by a previous crashed run."""
        import subprocess
        if not self._root.exists():
            return
        for child in self._root.iterdir():
            try:
                if child.is_dir():
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(child)],
                        check=False, capture_output=True,
                    )
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass

    def resolve(self, path: Path) -> Path:
        p = path.resolve()
        if _is_ascii_safe(str(p)):
            return p

        if p.is_dir():
            return self._junction_for(p)

        if _is_ascii_safe(p.name):
            safe_dir = self._junction_for(p.parent)
            return safe_dir / p.name

        import hashlib
        import shutil
        h = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:16]
        safe_file = self._root / (h + p.suffix)
        if not safe_file.exists():
            shutil.copy2(str(p), str(safe_file))
            self._hardlinks.append(safe_file)
        return safe_file

    def _junction_for(self, directory: Path) -> Path:
        key = str(directory)
        if key in self._dir_map:
            return self._dir_map[key]

        if _is_ascii_safe(key):
            self._dir_map[key] = directory
            return directory

        import hashlib
        import subprocess
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        junction = self._root / f"d_{h}"
        if not junction.exists():
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(directory)],
                check=True,
                capture_output=True,
            )
            self._junctions.append(junction)
        self._dir_map[key] = junction
        return junction

    def cleanup(self) -> None:
        import subprocess
        for hl in self._hardlinks:
            try:
                hl.unlink(missing_ok=True)
            except OSError:
                pass
        self._hardlinks.clear()

        for jn in reversed(self._junctions):
            try:
                if jn.exists():
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(jn)],
                        check=False,
                        capture_output=True,
                    )
            except OSError:
                pass
        self._junctions.clear()
        self._dir_map.clear()


DEFAULT_CASES = [
    {
        "name": "mmt_short",
        "pmx": "tests/data/mmt_test_model.pmx",
        "vmd": "tests/data/mmt_test_model_test_motion.vmd",
        "frames": [0, 30, 60],
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/reports/local_asset_motion_compare.json")
    parser.add_argument("--manifest", default="", help="Optional JSON manifest with cases/assets.")
    parser.add_argument("--case", action="append", default=[], help="Run only the named default case. Repeatable.")
    parser.add_argument("--frame", action="append", type=int, default=[], help="Override frames. Repeatable.")
    parser.add_argument("--vertex-threshold", type=float, default=1.0)
    parser.add_argument("--fbx-threshold", type=float, default=1.0)
    parser.add_argument("--skip-fbx", action="store_true")
    parser.add_argument("--strict-local", action="store_true")
    return parser.parse_args()


def _load_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    if not args.manifest:
        return list(DEFAULT_CASES)

    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_cases = manifest.get("cases") or manifest.get("assets") or []
    cases: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_cases):
        pmx = raw.get("pmx") or raw.get("model")
        vmd = raw.get("vmd") or raw.get("motion")
        name = str(raw.get("name") or pmx or f"case_{index}")
        if not pmx or not vmd:
            cases.append(
                {
                    "name": name,
                    "pmx": str(pmx or ""),
                    "vmd": str(vmd or ""),
                    "frames": list(raw.get("frames") or [0]),
                    "missing_manifest_fields": True,
                }
            )
            continue
        pmx_path = Path(str(pmx))
        vmd_path = Path(str(vmd))
        if not pmx_path.is_absolute():
            pmx_path = manifest_path.parent / pmx_path
        if not vmd_path.is_absolute():
            vmd_path = manifest_path.parent / vmd_path
        cases.append(
            {
                "name": name,
                "pmx": str(pmx_path.resolve()),
                "vmd": str(vmd_path.resolve()),
                "frames": list(raw.get("frames") or [0, 30, 60]),
            }
        )
    return cases


def _repo_imports() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _initialize_maya() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass


def _mesh_transforms(root: str | None = None) -> list[str]:
    if root and cmds.objExists(root):
        shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    else:
        shapes = cmds.ls(type="mesh", long=True) or []
    transforms: list[str] = []
    skinned_transforms: list[str] = []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        if not _node_is_visible(shape):
            continue
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parent and _node_is_visible(parent[0]) and parent[0] not in transforms:
            transforms.append(parent[0])
            if _has_skin_cluster(parent[0]):
                skinned_transforms.append(parent[0])
    return sorted(skinned_transforms or transforms)


def _node_is_visible(node: str) -> bool:
    current = node
    while current:
        try:
            if cmds.attributeQuery("visibility", node=current, exists=True) and not cmds.getAttr(f"{current}.visibility"):
                return False
        except Exception:
            pass
        parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parent[0] if parent else ""
    return True


def _has_skin_cluster(mesh_transform: str) -> bool:
    history = cmds.listHistory(mesh_transform, pruneDagObjects=True) or []
    return any(cmds.nodeType(node) == "skinCluster" for node in history)


def _mesh_points(mesh_transform: str) -> list[tuple[float, float, float]]:
    shapes = cmds.listRelatives(mesh_transform, shapes=True, noIntermediate=True, fullPath=True) or []
    points: list[tuple[float, float, float]] = []
    for shape in shapes:
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn_mesh = om.MFnMesh(dag)
        points.extend((p.x, p.y, p.z) for p in fn_mesh.getPoints(om.MSpace.kWorld))
    return points


def _capture_vertices(root: str | None, frames: list[int]) -> dict[int, list[tuple[float, float, float]]]:
    meshes = _mesh_transforms(root)
    if not meshes:
        raise RuntimeError("No mesh transforms found")
    result: dict[int, list[tuple[float, float, float]]] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        frame_points: list[tuple[float, float, float]] = []
        for mesh in meshes:
            frame_points.extend(_mesh_points(mesh))
        result[frame] = frame_points
    return result


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _compare_frames(
    lhs: dict[int, list[tuple[float, float, float]]],
    rhs: dict[int, list[tuple[float, float, float]]],
    frames: list[int],
    threshold: float,
) -> dict[str, Any]:
    per_frame: dict[str, Any] = {}
    all_distances: list[float] = []
    failed = False
    for frame in frames:
        left = lhs.get(frame, [])
        right = rhs.get(frame, [])
        if len(left) != len(right):
            per_frame[str(frame)] = {
                "vertex_count_lhs": len(left),
                "vertex_count_rhs": len(right),
                "max": None,
                "mean": None,
                "p95": None,
                "failed": True,
                "reason": "vertex_count_mismatch",
            }
            failed = True
            continue

        distances = [_dist(a, b) for a, b in zip(left, right)]
        all_distances.extend(distances)
        max_dist = max(distances) if distances else 0.0
        mean_dist = statistics.fmean(distances) if distances else 0.0
        p95 = sorted(distances)[int(len(distances) * 0.95)] if distances else 0.0
        frame_failed = max_dist > threshold
        failed = failed or frame_failed
        per_frame[str(frame)] = {
            "vertex_count": len(left),
            "max": round(max_dist, 6),
            "mean": round(mean_dist, 6),
            "p95": round(p95, 6),
            "failed": frame_failed,
        }

    overall_max = max(all_distances) if all_distances else None
    overall_mean = statistics.fmean(all_distances) if all_distances else None
    return {
        "passed": not failed,
        "threshold": threshold,
        "overall_max": round(overall_max, 6) if overall_max is not None else None,
        "overall_mean": round(overall_mean, 6) if overall_mean is not None else None,
        "frames": per_frame,
    }


def _import_pmx_vmd(pmx_path: Path, vmd_path: Path, setup_rig: bool, setup_bone_orientation: bool) -> str:
    _repo_imports()
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": setup_rig,
            "setup_bone_orientation": setup_bone_orientation,
            "import_physics": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    cmds.select(root, replace=True)
    ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
        },
    )
    if not ok:
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    return root


def _export_selected_fbx(root: str, out_fbx: Path, frames: list[int]) -> None:
    out_fbx.parent.mkdir(parents=True, exist_ok=True)
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.select(root, replace=True)
    start = min(frames)
    end = max(frames)
    mel.eval("FBXResetExport;")
    mel.eval("FBXExportSkins -v true;")
    mel.eval("FBXExportShapes -v true;")
    mel.eval("FBXExportBakeComplexAnimation -v true;")
    mel.eval(f"FBXExportBakeComplexStart -v {start};")
    mel.eval(f"FBXExportBakeComplexEnd -v {end};")
    mel.eval("FBXExportBakeComplexStep -v 1;")
    mel.eval("FBXExportInputConnections -v true;")
    mel.eval(f'FBXExport -f "{out_fbx.as_posix()}" -s;')
    if not out_fbx.exists() or out_fbx.stat().st_size <= 0:
        raise RuntimeError(f"FBX export did not produce a file: {out_fbx}")


def _import_fbx(fbx_path: Path) -> None:
    cmds.file(new=True, force=True)
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval(f'FBXImport -f "{fbx_path.as_posix()}";')


def _run_case(
    case: dict[str, Any],
    args: argparse.Namespace,
    report_dir: Path,
    stage: "_PathStaging | None" = None,
) -> dict[str, Any]:
    name = case["name"]
    pmx_path = Path(case["pmx"])
    vmd_path = Path(case["vmd"])
    frames = args.frame or list(case["frames"])
    result: dict[str, Any] = {
        "name": name,
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "frames": frames,
    }
    if case.get("missing_manifest_fields"):
        result["status"] = "failed" if args.strict_local else "skipped"
        result["reason"] = "manifest_case_requires_pmx_and_vmd"
        return result
    if not pmx_path.exists() or not vmd_path.exists():
        result["status"] = "failed" if args.strict_local else "skipped"
        result["reason"] = "asset_not_found"
        result["pmx_exists"] = pmx_path.exists()
        result["vmd_exists"] = vmd_path.exists()
        return result

    if stage:
        pmx_path = stage.resolve(pmx_path)
        vmd_path = stage.resolve(vmd_path)
        result["staged_pmx"] = str(pmx_path)
        result["staged_vmd"] = str(vmd_path)

    root = _import_pmx_vmd(pmx_path, vmd_path, setup_rig=False, setup_bone_orientation=False)
    bake_vertices = _capture_vertices(root, frames)

    root = _import_pmx_vmd(pmx_path, vmd_path, setup_rig=True, setup_bone_orientation=True)
    rig_vertices = _capture_vertices(root, frames)
    result["bake_vs_rig_mesh"] = _compare_frames(
        bake_vertices,
        rig_vertices,
        frames,
        args.vertex_threshold,
    )

    if not args.skip_fbx:
        fbx_path = report_dir / f"{name}_rig_baked.fbx"
        _export_selected_fbx(root, fbx_path, frames)
        result["fbx_path"] = str(fbx_path)
        _import_fbx(fbx_path)
        fbx_vertices = _capture_vertices(root=None, frames=frames)
        result["rig_vs_fbx_mesh"] = _compare_frames(
            rig_vertices,
            fbx_vertices,
            frames,
            args.fbx_threshold,
        )

    result["status"] = "passed" if _case_passed(result) else "failed"
    return result


def _case_passed(result: dict[str, Any]) -> bool:
    for key in ("bake_vs_rig_mesh", "rig_vs_fbx_mesh"):
        value = result.get(key)
        if value and not value.get("passed"):
            return False
    return result.get("status") != "skipped"


def _write_markdown(report: dict[str, Any], md_path: Path) -> None:
    lines = [
        "# Local Asset Motion Compare",
        "",
        f"- status: `{report['status']}`",
        f"- cases: `{len(report['cases'])}`",
        "",
    ]
    for case in report["cases"]:
        lines.append(f"## {case['name']}")
        lines.append("")
        lines.append(f"- status: `{case.get('status')}`")
        lines.append(f"- pmx: `{case.get('pmx')}`")
        lines.append(f"- vmd: `{case.get('vmd')}`")
        lines.append(f"- frames: `{case.get('frames')}`")
        for key, label in (
            ("bake_vs_rig_mesh", "Bake vs Rig mesh"),
            ("rig_vs_fbx_mesh", "Rig vs FBX mesh"),
        ):
            value = case.get(key)
            if not value:
                continue
            lines.append(
                f"- {label}: passed=`{value['passed']}`, max=`{value['overall_max']}`, "
                f"mean=`{value['overall_mean']}`, threshold=`{value['threshold']}`"
            )
            for frame, frame_result in value["frames"].items():
                lines.append(
                    f"  - frame {frame}: max=`{frame_result.get('max')}`, "
                    f"mean=`{frame_result.get('mean')}`, p95=`{frame_result.get('p95')}`, "
                    f"failed=`{frame_result.get('failed')}`"
                )
        if case.get("reason"):
            lines.append(f"- reason: `{case['reason']}`")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    _initialize_maya()
    selected_names = set(args.case)
    cases = [case for case in _load_cases(args) if not selected_names or case["name"] in selected_names]
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report_dir = out_path.parent
    report: dict[str, Any] = {
        "status": "passed",
        "vertex_threshold": args.vertex_threshold,
        "fbx_threshold": args.fbx_threshold,
        "cases": [],
    }

    staging_root = ROOT / "build" / "staging"
    stage: _PathStaging | None = None
    needs_staging = any(
        not _is_ascii_safe(case["pmx"]) or not _is_ascii_safe(case["vmd"])
        for case in cases
    )
    if needs_staging:
        stage = _PathStaging(staging_root)
        stage.setup()
        print(f"Path staging active: {staging_root}")

    try:
        if not cases:
            report["status"] = "failed" if args.strict_local else "skipped"
            report["cases"].append(
                {
                    "name": str(Path(args.manifest).resolve()) if args.manifest else "default",
                    "status": report["status"],
                    "reason": "manifest_contains_no_cases" if args.manifest else "no_cases_selected",
                }
            )
        for case in cases:
            try:
                result = _run_case(case, args, report_dir, stage=stage)
            except Exception as exc:
                result = {
                    "name": case["name"],
                    "pmx": case["pmx"],
                    "vmd": case["vmd"],
                    "status": "failed",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            report["cases"].append(result)
    finally:
        if stage:
            stage.cleanup()

    if any(case.get("status") == "failed" for case in report["cases"]):
        report["status"] = "failed"
    if args.strict_local and any(case.get("status") == "skipped" for case in report["cases"]):
        report["status"] = "failed"
    if report["status"] != "failed" and (
        not report["cases"] or all(case.get("status") == "skipped" for case in report["cases"])
    ):
        report["status"] = "skipped"

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, out_path.with_suffix(".md"))
    print(f"Report JSON: {out_path}")
    print(f"Report Markdown: {out_path.with_suffix('.md')}")
    print(f"Status: {report['status']}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
