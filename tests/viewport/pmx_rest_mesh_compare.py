"""Compare imported Maya REST mesh against raw PMX vertex positions."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone

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
    from mmd_tools.core.pmx_data import PmxData

    pmx = PmxData().parse_file(str(pmx_path))
    return [(v.position[0], v.position[1], -v.position[2]) for v in pmx.vertices]


def _visible_meshes(root: str) -> list[str]:
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    meshes: list[str] = []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        if not _node_is_visible(shape):
            continue
        parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if parent and _node_is_visible(parent[0]) and parent[0] not in meshes:
            meshes.append(parent[0])
    return sorted(meshes)


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


def _source_indices(mesh: str) -> list[int]:
    from mmd_tools.core import maya_utils
    from mmd_tools.core.constants import ATTR_MMD_SOURCE_VERTEX_INDICES

    if cmds.attributeQuery(ATTR_MMD_SOURCE_VERTEX_INDICES, node=mesh, exists=True):
        return list(maya_utils.get_int_array_attribute(mesh, ATTR_MMD_SOURCE_VERTEX_INDICES))
    return list(range(int(cmds.polyEvaluate(mesh, vertex=True))))


def _mesh_points(mesh: str) -> list[tuple[float, float, float]]:
    shapes = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True) or []
    points: list[tuple[float, float, float]] = []
    for shape in shapes:
        sel = om.MSelectionList()
        sel.add(shape)
        dag = sel.getDagPath(0)
        fn = om.MFnMesh(dag)
        points.extend((p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kWorld))
    return points


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = (ROOT / args.pmx).resolve() if not Path(args.pmx).is_absolute() else Path(args.pmx)
    expected = _pmx_positions(pmx_path)
    root = _import_pmx(pmx_path, args.setup_rig, args.setup_bone_orientation)
    distances = []
    missing = 0
    for mesh in _visible_meshes(root):
        points = _mesh_points(mesh)
        indices = _source_indices(mesh)
        if len(points) != len(indices):
            raise RuntimeError(f"{mesh}: point/source-index count mismatch")
        for source_index, point in zip(indices, points):
            if source_index >= len(expected):
                missing += 1
                continue
            distances.append(_distance(point, expected[source_index]))

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
