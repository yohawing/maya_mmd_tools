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


def measure_rotation(key_count):
    """Include JSON packing and Maya command transport in rotation timings."""
    from functools import partial
    import logging
    from types import SimpleNamespace
    from maya import cmds
    import maya.api.OpenMaya as om
    from mmd_tools.converters import vmd_joint_rotation as rotation
    from mmd_tools.converters.vmd_bone_animation import _sparse_rotation_samples

    cmds.file(new=True, force=True)
    joint = cmds.createNode("joint")
    control = cmds.createNode("transform")
    cmds.setAttr(joint + ".jointOrient", 17., -29., 43.)
    converter = SimpleNamespace(logger=logging.getLogger(__name__), bone_index_to_joint={})
    frames = [SimpleNamespace(frame_number=i, rotation=tuple(om.MEulerRotation(i*.01, i*.013, i*.017).asQuaternion())) for i in range(key_count)]
    route = {"authoring_basis": tuple(om.MEulerRotation(.3, .1, -.7).asQuaternion()),
             "attr_targets": {"rotateX": (control, "rotateX")}}
    context = SimpleNamespace(vmd_frame_to_maya_time=float,
        convert_vmd_quat_to_joint_rotate=partial(rotation.convert_vmd_quat_to_joint_rotate, converter, bind_cache={}))
    result = {"reference": [], "native": [], "max_error": 0., "keys": key_count}
    reference = None
    for variant in ("reference", "native", "native", "reference", "reference", "native"):
        context.convert_vmd_quats_to_joint_rotates = partial(rotation.convert_vmd_quats_to_joint_rotates, converter) if variant == "native" else None
        start = time.perf_counter()
        values = _sparse_rotation_samples(context, joint, frames, route)
        result[variant].append(time.perf_counter()-start)
        if reference is None:
            reference = values
        result["max_error"] = max(result["max_error"], max(abs(x-y) for (_,a),(_,b) in zip(reference, values) for x,y in zip(a,b)))
    assert result["max_error"] < 1e-7, result
    result["median_seconds"] = {name: statistics.median(result[name]) for name in ("reference", "native")}
    return result


def measure_sparse(key_count):
    """Compare adaptation/grouping and peak Python allocations on owned tracks."""
    import gc
    import tracemalloc
    from mmd_tools.converters.vmd_registered_sparse import (
        RegisteredSparseBoneFrame, registered_sparse_bone_frames, _semantic_points)
    from mmd_tools.core.native.mmd_anim_runtime_types import (
        MmdRuntimeBoneTrack, MmdRuntimeBoneTrackDescriptor,
        MmdRuntimeBoneTrackKey, MmdRuntimeBoneTrackCurve,
        MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER)
    curve = MmdRuntimeBoneTrackCurve(MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER, .2, .7, .8, .9)
    keys = tuple(MmdRuntimeBoneTrackKey(0, i, (i*.001, 0., 0.), (0., 0., 0., 1.), curve, curve, curve, curve) for i in range(key_count))
    tracks = (MmdRuntimeBoneTrack(MmdRuntimeBoneTrackDescriptor(0, key_count), keys),)

    def reference():
        frames = tuple(RegisteredSparseBoneFrame("bone", 0, key.frame, tuple(key.position_xyz), tuple(key.rotation_xyzw),
            {"translate_x": _semantic_points(key.translation_x), "translate_y": _semantic_points(key.translation_y),
             "translate_z": _semantic_points(key.translation_z), "rotation": _semantic_points(key.rotation)}) for key in keys)
        groups = {}
        for frame in frames:
            groups.setdefault(("index", frame.bone_index), []).append(frame)
        return frames, groups

    def native():
        frames = registered_sparse_bone_frames(tracks, bone_names_by_index={0: "bone"}, imported_bone_indices={0: "joint"})
        return frames, frames.by_bone

    functions = {"reference": reference, "native": native}
    result = {"reference": [], "native": [], "peak_bytes": {}, "keys": key_count}
    for variant in ("reference", "native", "native", "reference", "reference", "native"):
        gc.collect()
        start = time.perf_counter()
        frames, groups = functions[variant]()
        result[variant].append(time.perf_counter()-start)
        assert len(frames) == len(groups[("index", 0)]) == key_count
        assert frames[-1].position == keys[-1].position_xyz
        assert frames[-1].semantic_interpolation["rotation"] == (.2, .7, .8, .9)
        del frames, groups
    for variant in functions:
        gc.collect()
        tracemalloc.start()
        frames, groups = functions[variant]()
        result["peak_bytes"][variant] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        del frames, groups
    result["median_seconds"] = {name: statistics.median(result[name]) for name in functions}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("tt", "rotation", "sparse", "all"), default="all")
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
        report = {"maya": version, "config": args.config}
        for stage, measure in (("tt", measure_tt), ("rotation", measure_rotation), ("sparse", measure_sparse)):
            if args.stage in (stage, "all"):
                report[stage] = measure(args.keys)
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
