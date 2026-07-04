"""Compare current Maya import against a mesh oracle exported by a public build."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import maya.cmds as cmds
import maya.standalone

from mesh_oracle_utils import bbox, distance, mesh_points_under_root

ROOT = Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--oracle", required=True)
    parser.add_argument("--out", default="build/reports/public_mesh_oracle_compare.json")
    parser.add_argument("--mode", choices=["bake", "rig"], default="bake")
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument(
        "--disable-live-ik",
        action="store_true",
        help="Disconnect and disable mmdCcdIk.enabled after import for diagnosis.",
    )
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _import_current(pmx_path: Path, vmd_path: Path, mode: str) -> str:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

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
    if not import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path)}):
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    return root


def _disable_live_ik_nodes() -> int:
    disabled = 0
    for node in cmds.ls(type="mmdCcdIk") or []:
        try:
            for plug in cmds.listConnections(f"{node}.enabled", s=True, d=False, p=True) or []:
                cmds.disconnectAttr(plug, f"{node}.enabled")
            cmds.setAttr(f"{node}.enabled", False)
            disabled += 1
        except Exception:
            pass
    return disabled


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = (ROOT / args.pmx).resolve() if not Path(args.pmx).is_absolute() else Path(args.pmx)
    vmd_path = (ROOT / args.vmd).resolve() if not Path(args.vmd).is_absolute() else Path(args.vmd)
    oracle_path = (ROOT / args.oracle).resolve() if not Path(args.oracle).is_absolute() else Path(args.oracle)
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))

    root = _import_current(pmx_path, vmd_path, args.mode)
    disabled_live_ik = _disable_live_ik_nodes() if args.disable_live_ik else 0
    frames = []
    all_distances: list[float] = []
    for frame_data in oracle.get("frames", []):
        frame = int(frame_data["frame"])
        expected = frame_data["vertices"]
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        actual = mesh_points_under_root(root)
        if len(actual) != len(expected):
            raise RuntimeError(f"frame {frame}: vertex count mismatch actual={len(actual)} oracle={len(expected)}")
        distances = [distance(a, b) for a, b in zip(actual, expected)]
        worst_index, worst_distance = max(enumerate(distances), key=lambda item: item[1])
        all_distances.extend(distances)
        frames.append({
            "frame": frame,
            "vertices": len(distances),
            "max": round(max(distances), 6),
            "mean": round(statistics.fmean(distances), 6),
            "p95": round(sorted(distances)[int(len(distances) * 0.95)], 6),
            "failed": max(distances) > args.threshold,
            "actual_bbox": bbox(actual),
            "oracle_bbox": bbox(expected),
            "worst_vertex": {
                "index": worst_index,
                "distance": round(worst_distance, 6),
                "actual": [round(value, 6) for value in actual[worst_index]],
                "oracle": [round(value, 6) for value in expected[worst_index]],
            },
        })

    overall_max = max(all_distances) if all_distances else float("inf")
    report = {
        "status": "passed" if all_distances and overall_max <= args.threshold else "failed",
        "mode": args.mode,
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "oracle": str(oracle_path),
        "threshold": args.threshold,
        "disable_live_ik": bool(args.disable_live_ik),
        "disabled_live_ik": disabled_live_ik,
        "overall_max": round(overall_max, 6),
        "overall_mean": round(statistics.fmean(all_distances), 6) if all_distances else None,
        "frames": frames,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(
        "\n".join([
            "# Public Mesh Oracle Compare",
            "",
            f"- status: `{report['status']}`",
            f"- mode: `{args.mode}`",
            f"- oracle: `{oracle_path}`",
            f"- disable live IK: `{args.disable_live_ik}` ({disabled_live_ik} nodes)",
            f"- overall max: `{report['overall_max']}`",
            f"- overall mean: `{report['overall_mean']}`",
            "",
            *[
                (
                    f"- frame {frame['frame']}: max=`{frame['max']}`, mean=`{frame['mean']}`, "
                    f"p95=`{frame['p95']}`, failed=`{frame['failed']}`, "
                    f"actual_bbox=`{frame['actual_bbox']}`, oracle_bbox=`{frame['oracle_bbox']}`, "
                    f"worst_vertex=`{frame['worst_vertex']}`"
                )
                for frame in frames
            ],
        ]),
        encoding="utf-8",
    )
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {out.with_suffix('.md')}")
    print(f"Status: {report['status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
