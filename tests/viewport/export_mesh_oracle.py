"""Export world-space mesh vertices for a PMX/VMD import as a JSON oracle."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=["bake", "rig"], default="bake")
    parser.add_argument("--frame", action="append", type=int, default=[])
    parser.add_argument("--disable-runtime-bake", action="store_true")
    return parser.parse_args()


def _initialize(repo_root: Path) -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    current_root = str(DEFAULT_ROOT.resolve())
    current_viewport = str((DEFAULT_ROOT / "tests" / "viewport").resolve())
    sys.path[:] = [
        entry
        for entry in sys.path
        if str(Path(entry or ".").resolve()) not in {current_root, current_viewport}
    ]
    for name in list(sys.modules):
        if name == "mmd_tools" or name.startswith("mmd_tools."):
            del sys.modules[name]
    sys.path.insert(0, str(repo_root))


def _resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _node_is_visible(node: str) -> bool:
    current = node
    while current:
        try:
            if cmds.attributeQuery("visibility", node=current, exists=True) and not cmds.getAttr(f"{current}.visibility"):
                return False
        except Exception:
            pass
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        current = parents[0] if parents else ""
    return True


def _has_skin_cluster(mesh_transform: str) -> bool:
    history = cmds.listHistory(mesh_transform, pruneDagObjects=True) or []
    return any(cmds.nodeType(node) == "skinCluster" for node in history)


def _mesh_transforms(root: str) -> list[str]:
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms: list[str] = []
    skinned: list[str] = []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        if not _node_is_visible(shape):
            continue
        parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
        if not parents or not _node_is_visible(parents[0]) or parents[0] in transforms:
            continue
        transforms.append(parents[0])
        if _has_skin_cluster(parents[0]):
            skinned.append(parents[0])
    return sorted(skinned or transforms)


def _mesh_points(mesh_transform: str) -> list[list[float]]:
    points: list[list[float]] = []
    shapes = cmds.listRelatives(mesh_transform, shapes=True, noIntermediate=True, fullPath=True) or []
    for shape in shapes:
        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        points.extend([[float(p.x), float(p.y), float(p.z)] for p in fn.getPoints(om.MSpace.kWorld)])
    return points


def _bbox(points: list[list[float]]) -> dict[str, Any]:
    if not points:
        return {"min": [], "max": [], "center": [], "diag": 0.0}
    mins = [min(point[i] for point in points) for i in range(3)]
    maxs = [max(point[i] for point in points) for i in range(3)]
    center = [(mins[i] + maxs[i]) * 0.5 for i in range(3)]
    diag = math.sqrt(sum((maxs[i] - mins[i]) ** 2 for i in range(3)))
    return {
        "min": [round(value, 6) for value in mins],
        "max": [round(value, 6) for value in maxs],
        "center": [round(value, 6) for value in center],
        "diag": round(diag, 6),
    }


def _capture(root: str, frames: list[int]) -> list[dict[str, Any]]:
    meshes = _mesh_transforms(root)
    if not meshes:
        raise RuntimeError("No visible mesh transforms found")
    result: list[dict[str, Any]] = []
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        vertices: list[list[float]] = []
        for mesh in meshes:
            vertices.extend(_mesh_points(mesh))
        result.append({
            "frame": frame,
            "vertices": vertices,
            "bbox": _bbox(vertices),
        })
    return result


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    _initialize(repo_root)

    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)

    pmx_path = _resolve(args.pmx)
    vmd_path = _resolve(args.vmd)
    out = _resolve(args.out)
    frames = args.frame or [0, 1, 2, 3, 4, 5]

    cmds.file(new=True, force=True)
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": args.mode == "rig",
            "setup_bone_orientation": args.mode == "rig",
            "import_physics": False,
            "create_mmd_shaders": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    cmds.select(root, replace=True)
    if args.disable_runtime_bake:
        import mmd_tools.converters.vmd_converter as vmd_converter

        vmd_converter.HAS_MMD_RUNTIME = False
        vmd_converter.is_mmd_runtime_available = lambda: False
    if not import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path)}):
        raise RuntimeError(f"VMD import failed: {vmd_path}")

    report = {
        "repo_root": str(repo_root),
        "mmd_tools": str(Path(sys.modules["mmd_tools"].__file__).resolve()),
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "mode": args.mode,
        "disable_runtime_bake": args.disable_runtime_bake,
        "frames": _capture(root, frames),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Oracle JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
