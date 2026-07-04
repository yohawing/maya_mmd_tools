"""Export world-space mesh vertices for a PMX/VMD import as a JSON oracle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import maya.cmds as cmds
import maya.standalone

from mesh_oracle_utils import bbox, mesh_points, visible_mesh_transforms

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


def _mesh_transforms(root: str) -> list[str]:
    return visible_mesh_transforms(root, prefer_skin_cluster=True)


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
            vertices.extend([list(point) for point in mesh_points(mesh)])
        result.append({
            "frame": frame,
            "vertices": vertices,
            "bbox": bbox(vertices),
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
