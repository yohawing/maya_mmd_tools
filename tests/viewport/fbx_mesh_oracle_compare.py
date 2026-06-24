"""Compare current PMX/VMD playback against a mesh FBX oracle.

The FBX is treated as an external visual oracle exported from a known-good Maya
scene.  The comparison samples visible world-space mesh vertices over multiple
frames; it intentionally does not compare rotations alone because jointOrient
bugs can leave bones plausible while the skinned mesh collapses.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel
import maya.standalone

ROOT = Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default=r"build/local_assets/addiction_tda.vmd")
    parser.add_argument("--fbx", default=r"build/mmt_test_model_motion.fbx")
    parser.add_argument("--out", default="build/reports/fbx_mesh_oracle_compare.json")
    parser.add_argument("--mode", choices=["bake", "rig", "both"], default="both")
    parser.add_argument("--frame", action="append", type=int, default=[])
    parser.add_argument(
        "--current-frame",
        action="append",
        type=int,
        default=[],
        help="Current-scene frame list paired with --frame. Defaults to the oracle frames.",
    )
    parser.add_argument("--threshold", type=float, default=1.0)
    parser.add_argument(
        "--disable-runtime-bake",
        action="store_true",
        help="Force VMD import through the legacy converter path for diagnosis.",
    )
    parser.add_argument("--search-current-start", type=int)
    parser.add_argument("--search-current-end", type=int)
    parser.add_argument("--search-current-step", type=int, default=1)
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def _load_fbx_plugin() -> None:
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)


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


def _mesh_points(mesh_transform: str) -> list[tuple[float, float, float]]:
    shapes = cmds.listRelatives(mesh_transform, shapes=True, noIntermediate=True, fullPath=True) or []
    points: list[tuple[float, float, float]] = []
    for shape in shapes:
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn = om.MFnMesh(dag)
        points.extend((p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld))
    return points


def _capture_vertices(root: str | None, frames: list[int]) -> dict[int, list[tuple[float, float, float]]]:
    meshes = _mesh_transforms(root)
    if not meshes:
        raise RuntimeError("No visible mesh transforms found")
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


def _bbox(points: list[tuple[float, float, float]]) -> dict[str, Any]:
    if not points:
        return {"min": [], "max": [], "center": [], "diag": 0.0}
    mins = [min(point[i] for point in points) for i in range(3)]
    maxs = [max(point[i] for point in points) for i in range(3)]
    center = [(mins[i] + maxs[i]) * 0.5 for i in range(3)]
    diag = _distance(tuple(mins), tuple(maxs))
    return {
        "min": [round(value, 6) for value in mins],
        "max": [round(value, 6) for value in maxs],
        "center": [round(value, 6) for value in center],
        "diag": round(diag, 6),
    }


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    return sorted(values)[min(len(values) - 1, int(len(values) * 0.95))]


def _nearest_distances(
    points: list[tuple[float, float, float]],
    targets: list[tuple[float, float, float]],
) -> list[float]:
    if not points or not targets:
        return []
    distances: list[float] = []
    for point in points:
        best_sq = min(
            (point[0] - target[0]) ** 2 + (point[1] - target[1]) ** 2 + (point[2] - target[2]) ** 2
            for target in targets
        )
        distances.append(math.sqrt(best_sq))
    return distances


def _point_cloud_metrics(
    actual: list[tuple[float, float, float]],
    oracle: list[tuple[float, float, float]],
) -> dict[str, Any]:
    actual_to_oracle = _nearest_distances(actual, oracle)
    oracle_to_actual = _nearest_distances(oracle, actual)
    all_distances = actual_to_oracle + oracle_to_actual
    return {
        "actual_to_oracle_mean": round(statistics.fmean(actual_to_oracle), 6) if actual_to_oracle else None,
        "actual_to_oracle_p95": round(_p95(actual_to_oracle), 6) if actual_to_oracle else None,
        "oracle_to_actual_mean": round(statistics.fmean(oracle_to_actual), 6) if oracle_to_actual else None,
        "oracle_to_actual_p95": round(_p95(oracle_to_actual), 6) if oracle_to_actual else None,
        "symmetric_mean": round(statistics.fmean(all_distances), 6) if all_distances else None,
        "symmetric_max": round(max(all_distances), 6) if all_distances else None,
    }


def _bbox_delta(
    actual: list[tuple[float, float, float]],
    oracle: list[tuple[float, float, float]],
) -> dict[str, Any]:
    actual_bbox = _bbox(actual)
    oracle_bbox = _bbox(oracle)
    if not actual or not oracle:
        return {"actual": actual_bbox, "oracle": oracle_bbox}
    return {
        "actual": actual_bbox,
        "oracle": oracle_bbox,
        "center_distance": round(
            _distance(tuple(actual_bbox["center"]), tuple(oracle_bbox["center"])),
            6,
        ),
        "diag_delta": round(float(actual_bbox["diag"]) - float(oracle_bbox["diag"]), 6),
    }


def _import_fbx_oracle(fbx_path: Path, frames: list[int]) -> dict[str, Any]:
    cmds.file(new=True, force=True)
    _load_fbx_plugin()
    mel.eval("FBXResetImport;")
    mel.eval(f'FBXImport -f "{fbx_path.as_posix()}";')
    meshes = _mesh_transforms(None)
    vertices = _capture_vertices(None, frames)
    return {
        "source": "fbx",
        "path": str(fbx_path),
        "meshes": meshes,
        "vertices": vertices,
        "frame_bboxes": {str(frame): _bbox(vertices[frame]) for frame in frames},
    }


def _import_current(
    pmx_path: Path,
    vmd_path: Path,
    mode: str,
    frames: list[int],
    disable_runtime_bake: bool,
) -> dict[str, Any]:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": mode == "rig",
            "setup_bone_orientation": mode == "rig",
            "import_physics": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    cmds.select(root, replace=True)
    if disable_runtime_bake:
        import mmd_tools.converters.vmd_converter as vmd_converter

        vmd_converter.HAS_MMD_RUNTIME = False
        vmd_converter.is_mmd_runtime_available = lambda: False
    ok = import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path)})
    if not ok:
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    vertices = _capture_vertices(root, frames)
    return {
        "source": mode,
        "root": root,
        "meshes": _mesh_transforms(root),
        "vertices": vertices,
        "frame_bboxes": {str(frame): _bbox(vertices[frame]) for frame in frames},
    }


def _compare(
    actual: dict[int, list[tuple[float, float, float]]],
    expected: dict[int, list[tuple[float, float, float]]],
    frames: list[int],
    current_frames: list[int],
    threshold: float,
) -> dict[str, Any]:
    all_distances: list[float] = []
    frame_reports: dict[str, Any] = {}
    failed = False
    for oracle_frame, current_frame in zip(frames, current_frames):
        lhs = actual.get(current_frame, [])
        rhs = expected.get(oracle_frame, [])
        if len(lhs) != len(rhs):
            frame_reports[str(oracle_frame)] = {
                "failed": True,
                "reason": "vertex_count_mismatch",
                "current_frame": current_frame,
                "actual_count": len(lhs),
                "oracle_count": len(rhs),
                "bbox_delta": _bbox_delta(lhs, rhs),
                "point_cloud": _point_cloud_metrics(lhs, rhs),
            }
            failed = True
            continue
        distances = [_distance(a, b) for a, b in zip(lhs, rhs)]
        all_distances.extend(distances)
        worst_index, worst_distance = max(enumerate(distances), key=lambda item: item[1])
        max_dist = max(distances)
        mean = statistics.fmean(distances)
        p95 = _p95(distances)
        frame_failed = max_dist > threshold
        failed = failed or frame_failed
        frame_reports[str(oracle_frame)] = {
            "failed": frame_failed,
            "current_frame": current_frame,
            "vertex_count": len(lhs),
            "max": round(max_dist, 6),
            "mean": round(mean, 6),
            "p95": round(p95, 6),
            "bbox_delta": _bbox_delta(lhs, rhs),
            "point_cloud": _point_cloud_metrics(lhs, rhs),
            "worst_vertex": {
                "index": worst_index,
                "distance": round(worst_distance, 6),
                "actual": [round(value, 6) for value in lhs[worst_index]],
                "oracle": [round(value, 6) for value in rhs[worst_index]],
            },
        }
    return {
        "passed": bool(all_distances) and not failed,
        "threshold": threshold,
        "overall_max": round(max(all_distances), 6) if all_distances else None,
        "overall_mean": round(statistics.fmean(all_distances), 6) if all_distances else None,
        "frames": frame_reports,
    }


def _search_frame_map(
    actual: dict[int, list[tuple[float, float, float]]],
    expected: dict[int, list[tuple[float, float, float]]],
    oracle_frames: list[int],
) -> dict[str, Any]:
    search_reports: dict[str, Any] = {}
    best_means: list[float] = []
    for oracle_frame in oracle_frames:
        rhs = expected.get(oracle_frame, [])
        candidates: list[dict[str, Any]] = []
        for current_frame, lhs in actual.items():
            if len(lhs) != len(rhs):
                continue
            distances = [_distance(a, b) for a, b in zip(lhs, rhs)]
            if not distances:
                continue
            worst_index, worst_distance = max(enumerate(distances), key=lambda item: item[1])
            candidates.append({
                "current_frame": current_frame,
                "max": max(distances),
                "mean": statistics.fmean(distances),
                "p95": sorted(distances)[int(len(distances) * 0.95)],
                "worst_vertex": {
                    "index": worst_index,
                    "distance": worst_distance,
                    "actual": lhs[worst_index],
                    "oracle": rhs[worst_index],
                },
                "actual_bbox": _bbox(lhs),
                "oracle_bbox": _bbox(rhs),
            })
        candidates.sort(key=lambda item: (item["mean"], item["max"]))
        best = candidates[0] if candidates else None
        if best:
            best_means.append(float(best["mean"]))
            rounded = {
                "current_frame": best["current_frame"],
                "max": round(float(best["max"]), 6),
                "mean": round(float(best["mean"]), 6),
                "p95": round(float(best["p95"]), 6),
                "actual_bbox": best["actual_bbox"],
                "oracle_bbox": best["oracle_bbox"],
                "worst_vertex": {
                    "index": best["worst_vertex"]["index"],
                    "distance": round(float(best["worst_vertex"]["distance"]), 6),
                    "actual": [round(value, 6) for value in best["worst_vertex"]["actual"]],
                    "oracle": [round(value, 6) for value in best["worst_vertex"]["oracle"]],
                },
            }
        else:
            rounded = {"reason": "no comparable candidate"}
        search_reports[str(oracle_frame)] = {
            "best": rounded,
            "top_candidates": [
                {
                    "current_frame": item["current_frame"],
                    "max": round(float(item["max"]), 6),
                    "mean": round(float(item["mean"]), 6),
                    "p95": round(float(item["p95"]), 6),
                }
                for item in candidates[:5]
            ],
        }
    return {
        "overall_best_mean": round(statistics.fmean(best_means), 6) if best_means else None,
        "frames": search_reports,
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# FBX Mesh Oracle Compare",
        "",
        f"- status: `{report['status']}`",
        f"- fbx: `{report['fbx']}`",
        f"- pmx: `{report['pmx']}`",
        f"- vmd: `{report['vmd']}`",
        f"- frames: `{report['frames']}`",
        f"- current frames: `{report['current_frames']}`",
        "",
        "## Oracle",
        "",
        f"- meshes: `{report['oracle']['meshes']}`",
        f"- frame bboxes: `{report['oracle']['frame_bboxes']}`",
        "",
    ]
    for mode, comparison in report["comparisons"].items():
        lines.extend([
            f"## {mode}",
            "",
            f"- passed: `{comparison.get('passed')}`",
            f"- max: `{comparison.get('overall_max')}`",
            f"- mean: `{comparison.get('overall_mean')}`",
        ])
        if "search" in comparison:
            lines.append(f"- search overall best mean: `{comparison['search'].get('overall_best_mean')}`")
            for frame, frame_report in comparison["search"]["frames"].items():
                lines.append(
                    f"- oracle frame {frame}: best=`{frame_report.get('best')}`, "
                    f"top=`{frame_report.get('top_candidates')}`"
                )
            lines.append("")
            continue
        for frame, frame_report in comparison["frames"].items():
            lines.append(
                f"- frame {frame}: failed=`{frame_report.get('failed')}`, "
                f"current_frame=`{frame_report.get('current_frame')}`, "
                f"max=`{frame_report.get('max')}`, mean=`{frame_report.get('mean')}`, "
                f"p95=`{frame_report.get('p95')}`, bbox_delta=`{frame_report.get('bbox_delta')}`, "
                f"point_cloud=`{frame_report.get('point_cloud')}`, "
                f"worst_vertex=`{frame_report.get('worst_vertex')}`"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = _resolve(args.pmx)
    vmd_path = _resolve(args.vmd)
    fbx_path = _resolve(args.fbx)
    frames = args.frame or [0, 30, 60, 120, 300, 600]
    search_frames = None
    if args.search_current_start is not None or args.search_current_end is not None:
        if args.search_current_start is None or args.search_current_end is None:
            raise ValueError("--search-current-start and --search-current-end must be specified together")
        if args.search_current_step <= 0:
            raise ValueError("--search-current-step must be positive")
        search_frames = list(range(args.search_current_start, args.search_current_end + 1, args.search_current_step))
    current_frames = search_frames or args.current_frame or frames
    if search_frames is None and len(current_frames) != len(frames):
        raise ValueError("--current-frame count must match --frame count")
    for path in (pmx_path, vmd_path, fbx_path):
        if not path.exists():
            raise FileNotFoundError(path)

    oracle = _import_fbx_oracle(fbx_path, frames)
    modes = ["bake", "rig"] if args.mode == "both" else [args.mode]
    comparisons: dict[str, Any] = {}
    current_summaries: dict[str, Any] = {}
    for mode in modes:
        current = _import_current(pmx_path, vmd_path, mode, current_frames, args.disable_runtime_bake)
        current_summaries[mode] = {
            "meshes": current["meshes"],
            "frame_bboxes": current["frame_bboxes"],
        }
        if search_frames is not None:
            direct = _compare(current["vertices"], oracle["vertices"], frames, frames, args.threshold) if all(
                frame in current["vertices"] for frame in frames
            ) else {"passed": False, "reason": "direct frames not captured", "frames": {}}
            direct["search"] = _search_frame_map(current["vertices"], oracle["vertices"], frames)
            comparisons[mode] = direct
        else:
            comparisons[mode] = _compare(current["vertices"], oracle["vertices"], frames, current_frames, args.threshold)

    passed = all(value["passed"] for value in comparisons.values())
    report = {
        "status": "passed" if passed else "failed",
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "fbx": str(fbx_path),
        "frames": frames,
        "current_frames": current_frames,
        "threshold": args.threshold,
        "disable_runtime_bake": args.disable_runtime_bake,
        "oracle": {
            "meshes": oracle["meshes"],
            "frame_bboxes": oracle["frame_bboxes"],
        },
        "current": current_summaries,
        "comparisons": comparisons,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, out.with_suffix(".md"))
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {out.with_suffix('.md')}")
    print(f"Status: {report['status']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
