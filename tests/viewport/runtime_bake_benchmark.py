"""Benchmark the Maya runtime-bake import path.

This script imports a PMX model, applies a VMD in bake mode, and writes a JSON
report with coarse timing.  Detailed runtime evaluation/apply timings are
emitted by ``VmdConverter`` logs during the VMD import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import maya.cmds as cmds
import maya.standalone

ROOT = Path(__file__).resolve().parents[2]

BENCH_CASES = {
    "mmt_short": {
        "pmx": "tests/data/mmt_test_model.pmx",
        "vmd": "tests/data/mmt_test_model_test_motion.vmd",
    },
    "alicia_weekender": {
        "pmx": r"F:\MMD\pmx\Alicia\Alicia\MMD\Alicia_solid.pmx",
        "vmd": r"F:\MMD\vmd\110_weekender_girl\weekender_girl\wg_motion.vmd",
    },
    "aria_weekender": {
        "pmx": r"F:\MMD\pmx\aria\aria.pmx",
        "vmd": r"F:\MMD\vmd\110_weekender_girl\weekender_girl\wg_motion.vmd",
    },
    "lumine_weekender": {
        "pmx": r"F:\MMD\pmx\【女主角_荧】_by_原神\Lumine.pmx",
        "vmd": r"F:\MMD\vmd\110_weekender_girl\weekender_girl\wg_motion.vmd",
    },
    "alicia_rabbithole": {
        "pmx": r"F:\MMD\pmx\Alicia\Alicia\MMD\Alicia_solid.pmx",
        "vmd": r"F:\MMD\vmd\ラビットホール\ラビットホール.vmd",
    },
    "aria_rabbithole": {
        "pmx": r"F:\MMD\pmx\aria\aria.pmx",
        "vmd": r"F:\MMD\vmd\ラビットホール\ラビットホール.vmd",
    },
    "lumine_rabbithole": {
        "pmx": r"F:\MMD\pmx\【女主角_荧】_by_原神\Lumine.pmx",
        "vmd": r"F:\MMD\vmd\ラビットホール\ラビットホール.vmd",
    },
    "eunice_rabbithole": {
        "pmx": r"F:\MMD\pmx\Eunice231103WB\Eunice15WB.pmx",
        "vmd": r"F:\MMD\vmd\ラビットホール\ラビットホール.vmd",
    },
    "alicia_addiction": {
        "pmx": r"F:\MMD\pmx\Alicia\Alicia\MMD\Alicia_solid.pmx",
        "vmd": r"F:\MMD\vmd\124_[A]ddiction_モーション\[A]ddiction_モーション\[A]ddiction_Tda式.vmd",
    },
    "aria_addiction": {
        "pmx": r"F:\MMD\pmx\aria\aria.pmx",
        "vmd": r"F:\MMD\vmd\124_[A]ddiction_モーション\[A]ddiction_モーション\[A]ddiction_Tda式.vmd",
    },
}


def _is_ascii_safe(path: str) -> bool:
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


class _PathStaging:
    """Provide ASCII-safe aliases for non-ASCII PMX/VMD paths."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._junctions: list[Path] = []
        self._copies: list[Path] = []
        self._dir_map: dict[str, Path] = {}

    def setup(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._cleanup_orphans()

    def _cleanup_orphans(self) -> None:
        if not self._root.exists():
            return
        for child in self._root.iterdir():
            try:
                if child.is_dir():
                    subprocess.run(["cmd", "/c", "rmdir", str(child)], check=False, capture_output=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass

    def resolve(self, path: Path) -> Path:
        resolved = path.resolve()
        if _is_ascii_safe(str(resolved)):
            return resolved
        if resolved.is_dir():
            return self._junction_for(resolved)
        if _is_ascii_safe(resolved.name):
            return self._junction_for(resolved.parent) / resolved.name

        digest = hashlib.sha256(str(resolved).encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
        safe_file = self._root / f"{digest}{resolved.suffix}"
        if not safe_file.exists():
            shutil.copy2(str(resolved), str(safe_file))
            self._copies.append(safe_file)
        return safe_file

    def _junction_for(self, directory: Path) -> Path:
        key = str(directory.resolve())
        if key in self._dir_map:
            return self._dir_map[key]
        if _is_ascii_safe(key):
            self._dir_map[key] = directory
            return directory
        digest = hashlib.sha256(key.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
        junction = self._root / f"d_{digest}"
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
        for copied in self._copies:
            try:
                copied.unlink(missing_ok=True)
            except OSError:
                pass
        for junction in reversed(self._junctions):
            try:
                if junction.exists():
                    subprocess.run(["cmd", "/c", "rmdir", str(junction)], check=False, capture_output=True)
            except OSError:
                pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(BENCH_CASES), default=None)
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--out", default="build/reports/runtime_bake_benchmark.json")
    parser.add_argument("--log", default="build/reports/runtime_bake_benchmark.log")
    parser.add_argument("--repeat", type=int, default=1)
    return parser.parse_args()


def _initialize(log_path: Path) -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def _motion_summary(vmd_path: Path) -> dict[str, Any]:
    from mmd_tools.core.mmd_parser import parse_mmd_file

    vmd_data = parse_mmd_file(str(vmd_path))
    frame_numbers = []
    for attr in ("bone_frames", "morph_frames", "camera_frames", "light_frames", "shadow_frames"):
        for frame in getattr(vmd_data, attr, []) or []:
            frame_numbers.append(int(getattr(frame, "frame_number", 0)))
    return {
        "bone_frames": len(getattr(vmd_data, "bone_frames", []) or []),
        "morph_frames": len(getattr(vmd_data, "morph_frames", []) or []),
        "camera_frames": len(getattr(vmd_data, "camera_frames", []) or []),
        "light_frames": len(getattr(vmd_data, "light_frames", []) or []),
        "shadow_frames": len(getattr(vmd_data, "shadow_frames", []) or []),
        "max_frame": max(frame_numbers) if frame_numbers else 0,
    }


def _run_once(pmx_path: Path, vmd_path: Path) -> dict[str, Any]:
    from mmd_tools.core import settings
    from mmd_tools.core.native.mmd_anim_runtime import get_runtime_library_path, is_mmd_runtime_available
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    settings.set("logging.level", "INFO")

    start = time.perf_counter()
    model_start = time.perf_counter()
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": False,
            "setup_bone_orientation": False,
        },
    )
    model_elapsed = time.perf_counter() - model_start
    if root is None:
        raise RuntimeError(f"PMX import failed: {pmx_path}")

    cmds.select(root, replace=True)
    motion_start = time.perf_counter()
    motion_ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": True,
        },
    )
    motion_elapsed = time.perf_counter() - motion_start
    if not motion_ok:
        raise RuntimeError(f"VMD runtime bake import failed: {vmd_path}")

    total_elapsed = time.perf_counter() - start
    return {
        "model_import_seconds": model_elapsed,
        "motion_import_seconds": motion_elapsed,
        "total_seconds": total_elapsed,
        "anim_curve_count": len(cmds.ls(type="animCurve") or []),
        "joint_count": len(cmds.ls(type="joint") or []),
        "runtime_available": bool(is_mmd_runtime_available()),
        "runtime_library_path": str(get_runtime_library_path() or ""),
    }


def main() -> int:
    args = _parse_args()
    if args.case:
        case = BENCH_CASES[args.case]
        args.pmx = case["pmx"]
        args.vmd = case["vmd"]

    original_pmx_path = (ROOT / args.pmx).resolve() if not Path(args.pmx).is_absolute() else Path(args.pmx)
    original_vmd_path = (ROOT / args.vmd).resolve() if not Path(args.vmd).is_absolute() else Path(args.vmd)
    out_path = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    log_path = (ROOT / args.log).resolve() if not Path(args.log).is_absolute() else Path(args.log)

    _initialize(log_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    staging = _PathStaging(ROOT / "build" / "tmp" / "runtime_bake_bench_paths")
    staging.setup()
    try:
        pmx_path = staging.resolve(original_pmx_path)
        vmd_path = staging.resolve(original_vmd_path)

        runs = []
        for _ in range(max(1, int(args.repeat))):
            runs.append(_run_once(pmx_path, vmd_path))

        report = {
            "case": args.case or "custom",
            "pmx": str(original_pmx_path),
            "vmd": str(original_vmd_path),
            "staged_pmx": str(pmx_path),
            "staged_vmd": str(vmd_path),
            "motion": _motion_summary(vmd_path),
            "repeat": len(runs),
            "runs": runs,
            "log": str(log_path),
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"OK: runtime bake benchmark -> {out_path}")
        print(f"LOG: {log_path}")
    finally:
        staging.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
