"""Balanced Maya-side A/B measurements for VMD import batching."""

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def measure_tt(key_count):
    """Compare the same authoring path with command tangents enabled/disabled."""
    from maya import cmds
    from mmd_tools.converters.vmd_rotation_time_curve import _author_vmd_rotation_time_curve

    frames = [{"frame_number": i, "interpolation": {"rotation":
               (.15, .8, .7, .9) if i % 2 else (.3, .1, .85, .6)}}
              for i in range(key_count)]
    native = cmds.mmdVmdTimeCurveKeys
    result = {"reference": [], "native": [], "max_error": 0., "keys": key_count}
    reference_values = None
    for variant in ("reference", "native", "native", "reference", "reference", "native"):
        cmds.file(new=True, force=True)
        control = cmds.createNode("transform")
        curves = [cmds.createNode("animCurveTA") for _ in range(3)]
        curve = cmds.createNode("animCurveTT")

        def command(**kwargs):
            if variant == "reference" and kwargs.get("version"):
                return 1
            return native(**kwargs)

        with patch.object(cmds, "mmdVmdTimeCurveKeys", side_effect=command):
            start = time.perf_counter()
            _author_vmd_rotation_time_curve(frames, curves, control, curve, "bone", float)
            result[variant].append(time.perf_counter() - start)
        values = [cmds.keyframe(curve, query=True, eval=True, time=(i+.375, i+.375))[0]
                  for i in range(0, key_count-1, max(1, key_count//100))]
        if reference_values is None:
            reference_values = values
        result["max_error"] = max(result["max_error"], max(abs(a-b) for a,b in zip(reference_values, values)))
    assert result["max_error"] < 1e-6, result
    result["median_seconds"] = {name: statistics.median(result[name]) for name in ("reference", "native")}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="Debug")
    parser.add_argument("--keys", type=int, default=20000)
    parser.add_argument("--out", default="build/reports/vmd-import-batch.json")
    args = parser.parse_args()
    if args.keys < 2:
        parser.error("--keys must be at least 2")
    import maya.standalone
    maya.standalone.initialize()
    from maya import cmds
    version = str(cmds.about(version=True)).split()[0]
    directory = ROOT / "plug-ins" / version / args.config
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
    dll_handle = os.add_dll_directory(str(directory)) if hasattr(os, "add_dll_directory") else None
    try:
        cmds.loadPlugin(str(directory / ("mmd_tools_cpp.mll" if os.name == "nt" else "mmd_tools_cpp.bundle")))
        report = {"maya": version, "config": args.config, "tt": measure_tt(args.keys)}
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        cmds.file(new=True, force=True)
        maya.standalone.uninitialize()
        if dll_handle is not None:
            dll_handle.close()


if __name__ == "__main__":
    main()
