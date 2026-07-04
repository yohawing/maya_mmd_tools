"""Compare imported Maya REST mesh against raw PMX vertex positions."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import maya.standalone

from mesh_oracle_utils import distance, mesh_points, source_indices, visible_mesh_transforms

ROOT = Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--out", default="build/reports/pmx_rest_mesh_compare.json")
    parser.add_argument("--threshold", type=float, default=0.01)
    parser.add_argument("--setup-rig", action="store_true", help="Import PMX with rig setup enabled.")
    parser.add_argument(
        "--setup-bone-orientation",
        action="store_true",
        help="Pass setup_bone_orientation=True to PMX import.",
    )
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _import_pmx(pmx_path: Path, setup_rig: bool, setup_bone_orientation: bool) -> str:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

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
    return root


def _pmx_positions(pmx_path: Path) -> list[tuple[float, float, float]]:
    from mmd_tools.core.mmd_parser import parse_pmx_file

    pmx = parse_pmx_file(str(pmx_path))
    return [(v.position[0], v.position[1], -v.position[2]) for v in pmx.vertices]


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = (ROOT / args.pmx).resolve() if not Path(args.pmx).is_absolute() else Path(args.pmx)
    expected = _pmx_positions(pmx_path)
    root = _import_pmx(pmx_path, args.setup_rig, args.setup_bone_orientation)
    distances = []
    missing = 0
    for mesh in visible_mesh_transforms(root):
        points = mesh_points(mesh)
        indices = source_indices(mesh)
        if len(points) != len(indices):
            raise RuntimeError(f"{mesh}: point/source-index count mismatch")
        for source_index, point in zip(indices, points):
            if source_index >= len(expected):
                missing += 1
                continue
            distances.append(distance(point, expected[source_index]))

    max_dist = max(distances) if distances else None
    mean = statistics.fmean(distances) if distances else None
    p95 = sorted(distances)[int(len(distances) * 0.95)] if distances else None
    passed = bool(distances) and missing == 0 and max_dist <= args.threshold
    report = {
        "status": "passed" if passed else "failed",
        "pmx": str(pmx_path),
        "threshold": args.threshold,
        "setup_rig": args.setup_rig,
        "setup_bone_orientation": args.setup_bone_orientation,
        "compared_vertices": len(distances),
        "missing": missing,
        "max": round(max_dist, 6) if max_dist is not None else None,
        "mean": round(mean, 6) if mean is not None else None,
        "p95": round(p95, 6) if p95 is not None else None,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(
        "\n".join([
            "# PMX REST Mesh Compare",
            "",
            f"- status: `{report['status']}`",
            f"- pmx: `{pmx_path}`",
            f"- setup rig: `{args.setup_rig}`",
            f"- setup bone orientation: `{args.setup_bone_orientation}`",
            f"- compared vertices: `{report['compared_vertices']}`",
            f"- max: `{report['max']}`",
            f"- mean: `{report['mean']}`",
            f"- p95: `{report['p95']}`",
        ]),
        encoding="utf-8",
    )
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {md}")
    print(f"Status: {report['status']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
