"""Capture a PMX+VMD scene with an explicit Bake/Rig import mode."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

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
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--mode", choices=["bake", "rig", "default"], default="bake")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=640)
    return parser.parse_args()


def _initialize(repo_root: Path) -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    current_root = str(DEFAULT_ROOT.resolve())
    sys.path[:] = [
        entry
        for entry in sys.path
        if str(Path(entry).resolve()) != current_root
        and str(Path(entry).resolve()) != str(DEFAULT_ROOT.resolve() / "tests" / "viewport")
    ]
    sys.path.insert(0, str(repo_root))


def _resolve(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _import_options(mode: str) -> dict:
    if mode == "bake":
        return {
            "setup_rig": False,
            "setup_bone_orientation": False,
            "import_physics": False,
            "create_mmd_shaders": False,
        }
    if mode == "rig":
        return {
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
            "create_mmd_shaders": False,
        }
    return {"import_physics": False, "create_mmd_shaders": False}


def _mesh_points(root: str) -> list[list[float]]:
    points: list[list[float]] = []
    shapes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        try:
            if cmds.getAttr(f"{shape}.intermediateObject"):
                continue
        except Exception:
            pass
        sel = om.MSelectionList()
        sel.add(shape)
        fn = om.MFnMesh(sel.getDagPath(0))
        points.extend([[p.x, p.y, p.z] for p in fn.getPoints(om.MSpace.kWorld)])
    return points


def _bbox(points: list[list[float]]) -> dict[str, list[float] | float]:
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


def _assign_debug_material(root: str) -> None:
    shader = cmds.shadingNode("lambert", asShader=True, name="captureDebug_lambert")
    cmds.setAttr(f"{shader}.color", 0.62, 0.72, 0.9, type="double3")
    cmds.setAttr(f"{shader}.ambientColor", 0.15, 0.15, 0.15, type="double3")
    shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{shader}SG")
    cmds.connectAttr(f"{shader}.outColor", f"{shading_group}.surfaceShader", force=True)
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    transforms = sorted({cmds.listRelatives(mesh, parent=True, fullPath=True)[0] for mesh in meshes})
    if transforms:
        cmds.sets(transforms, edit=True, forceElement=shading_group)


def _look_at(camera: str, eye: list[float], target: list[float]) -> None:
    direction = [target[i] - eye[i] for i in range(3)]
    horizontal = math.sqrt(direction[0] ** 2 + direction[2] ** 2)
    yaw = math.degrees(math.atan2(direction[0], direction[2]))
    pitch = -math.degrees(math.atan2(direction[1], horizontal))
    cmds.xform(camera, worldSpace=True, translation=eye, rotation=[pitch, yaw, 0.0])


def _capture(root: str, out: Path, frame: int, width: int, height: int, bounds: dict) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    _assign_debug_material(root)
    cmds.directionalLight(name="captureLight", intensity=1.8)
    center = bounds["center"]
    diag = float(bounds["diag"])
    distance = max(diag * 3.0, 30.0)
    eye = [
        float(center[0]) + distance * 0.38,
        float(center[1]) + distance * 0.22,
        float(center[2]) + distance * 0.84,
    ]
    camera = "persp"
    _look_at(camera, eye, [float(value) for value in center])
    shapes = cmds.listRelatives(camera, shapes=True) or []
    if shapes:
        cmds.setAttr(f"{shapes[0]}.focalLength", 35.0)
        cmds.setAttr(f"{shapes[0]}.farClipPlane", max(distance * 5.0, 100.0))
    cmds.currentTime(frame, edit=True)
    cmds.refresh(force=True)
    result = cmds.playblast(
        frame=frame,
        format="image",
        filename=str(out.with_suffix("")),
        compression="png",
        width=width,
        height=height,
        percent=100,
        quality=90,
        viewer=False,
        showOrnaments=False,
        forceOverwrite=True,
        offScreen=True,
        offScreenViewportUpdate=True,
    )
    candidates = [
        out,
        out.with_suffix(".png"),
        out.parent / f"{out.stem}.{frame:04d}.png",
        out.parent / f"{out.stem}.{frame}.png",
        Path(result) if result else out,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    pngs = sorted(out.parent.glob(f"{out.stem}*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
    if pngs:
        return pngs[0].resolve()
    raise RuntimeError(f"playblast did not create PNG: {out}")


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    _initialize(repo_root)
    import mmd_tools
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    pmx_path = _resolve(repo_root, args.pmx)
    vmd_path = _resolve(repo_root, args.vmd)
    out = _resolve(repo_root, args.out)

    cmds.file(new=True, force=True)
    root = import_mmd_file(str(pmx_path), options=_import_options(args.mode))
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    ok = import_mmd_file(str(vmd_path), options={"target_model": root, "pmx_path": str(pmx_path), "vmd_fps": 30})
    if not ok:
        raise RuntimeError(f"VMD import failed: {vmd_path}")
    cmds.currentTime(args.frame, edit=True)
    cmds.refresh(force=True)
    points = _mesh_points(root)
    bounds = _bbox(points)
    png = _capture(root, out, args.frame, args.width, args.height, bounds)
    report = {
        "status": "passed",
        "repo_root": str(repo_root),
        "mmd_tools": str(Path(mmd_tools.__file__).resolve()),
        "mode": args.mode,
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "frame": args.frame,
        "vertices": len(points),
        "bbox": bounds,
        "png": str(png),
    }
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
