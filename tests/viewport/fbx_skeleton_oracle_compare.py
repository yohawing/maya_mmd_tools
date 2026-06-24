"""Compare current PMX/VMD skeleton motion against an FBX skeleton oracle."""

from __future__ import annotations

import argparse
import json
import math
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
    parser.add_argument("--out", default="build/reports/fbx_skeleton_oracle_compare.json")
    parser.add_argument("--mode", choices=["bake", "rig", "both"], default="both")
    parser.add_argument("--frame", action="append", type=int, default=[])
    parser.add_argument("--current-frame", action="append", type=int, default=[])
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--disable-runtime-bake",
        action="store_true",
        help="Force VMD import through the legacy converter path for diagnosis.",
    )
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


def _leaf_name(node: str) -> str:
    return node.rsplit("|", 1)[-1].rsplit(":", 1)[-1]


def _world_matrix(node: str) -> om.MMatrix:
    values = cmds.xform(node, query=True, matrix=True, worldSpace=True)
    return om.MMatrix(values)


def _translation(matrix: om.MMatrix) -> tuple[float, float, float]:
    return (float(matrix[12]), float(matrix[13]), float(matrix[14]))


def _rotation_quat(matrix: om.MMatrix) -> om.MQuaternion:
    return om.MTransformationMatrix(matrix).rotation(asQuaternion=True)


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _quat_angle_deg(a: om.MQuaternion, b: om.MQuaternion) -> float:
    dot = abs(a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w)
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def _joint_map() -> dict[str, str]:
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    for joint in cmds.ls(type="joint", long=True) or []:
        leaf = _leaf_name(joint)
        if leaf in result:
            duplicates.add(leaf)
        result[leaf] = joint
    for duplicate in duplicates:
        result.pop(duplicate, None)
    return result


def _capture_joints(frames: list[int]) -> dict[str, Any]:
    joints = _joint_map()
    result: dict[str, Any] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        try:
            cmds.refresh(force=True)
        except Exception:
            pass
        frame_data: dict[str, Any] = {}
        for name, joint in joints.items():
            matrix = _world_matrix(joint)
            quat = _rotation_quat(matrix)
            frame_data[name] = {
                "path": joint,
                "translate": _translation(matrix),
                "quat": (float(quat.x), float(quat.y), float(quat.z), float(quat.w)),
            }
        result[str(frame)] = frame_data
    return {"joint_count": len(joints), "frames": result}


def _import_fbx_oracle(fbx_path: Path, frames: list[int]) -> dict[str, Any]:
    cmds.file(new=True, force=True)
    _load_fbx_plugin()
    mel.eval("FBXResetImport;")
    mel.eval(f'FBXImport -f "{fbx_path.as_posix()}";')
    return _capture_joints(frames)


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
    return _capture_joints(frames)


def _quat_from_tuple(values: tuple[float, float, float, float] | list[float]) -> om.MQuaternion:
    return om.MQuaternion(float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def _compare(
    actual: dict[str, Any],
    oracle: dict[str, Any],
    frames: list[int],
    current_frames: list[int],
    top: int,
) -> dict[str, Any]:
    frame_reports: dict[str, Any] = {}
    all_translate: list[float] = []
    all_rotate: list[float] = []
    for oracle_frame, current_frame in zip(frames, current_frames):
        expected = oracle["frames"][str(oracle_frame)]
        observed = actual["frames"][str(current_frame)]
        shared = sorted(set(expected) & set(observed))
        rows: list[dict[str, Any]] = []
        for name in shared:
            lhs = observed[name]
            rhs = expected[name]
            translate_delta = _distance(tuple(lhs["translate"]), tuple(rhs["translate"]))
            rotate_delta = _quat_angle_deg(_quat_from_tuple(lhs["quat"]), _quat_from_tuple(rhs["quat"]))
            all_translate.append(translate_delta)
            all_rotate.append(rotate_delta)
            rows.append({
                "joint": name,
                "translate_delta": translate_delta,
                "rotate_delta_deg": rotate_delta,
                "actual_translate": lhs["translate"],
                "oracle_translate": rhs["translate"],
                "actual_path": lhs["path"],
                "oracle_path": rhs["path"],
            })
        rows.sort(key=lambda item: (item["translate_delta"], item["rotate_delta_deg"]), reverse=True)
        frame_reports[str(oracle_frame)] = {
            "current_frame": current_frame,
            "shared_joint_count": len(shared),
            "missing_in_current": sorted(set(expected) - set(observed))[:top],
            "missing_in_oracle": sorted(set(observed) - set(expected))[:top],
            "top_translate": [
                {
                    **item,
                    "translate_delta": round(float(item["translate_delta"]), 6),
                    "rotate_delta_deg": round(float(item["rotate_delta_deg"]), 6),
                    "actual_translate": [round(float(value), 6) for value in item["actual_translate"]],
                    "oracle_translate": [round(float(value), 6) for value in item["oracle_translate"]],
                }
                for item in rows[:top]
            ],
        }
    return {
        "overall_translate_max": round(max(all_translate), 6) if all_translate else None,
        "overall_translate_mean": round(sum(all_translate) / len(all_translate), 6) if all_translate else None,
        "overall_rotate_max_deg": round(max(all_rotate), 6) if all_rotate else None,
        "frames": frame_reports,
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# FBX Skeleton Oracle Compare",
        "",
        f"- fbx: `{report['fbx']}`",
        f"- pmx: `{report['pmx']}`",
        f"- vmd: `{report['vmd']}`",
        f"- frames: `{report['frames']}`",
        f"- current frames: `{report['current_frames']}`",
        f"- oracle joints: `{report['oracle_joint_count']}`",
        "",
    ]
    for mode, comparison in report["comparisons"].items():
        lines.extend([
            f"## {mode}",
            "",
            f"- overall translate max: `{comparison['overall_translate_max']}`",
            f"- overall translate mean: `{comparison['overall_translate_mean']}`",
            f"- overall rotate max deg: `{comparison['overall_rotate_max_deg']}`",
        ])
        for frame, frame_report in comparison["frames"].items():
            lines.append(
                f"- frame {frame}: current_frame=`{frame_report['current_frame']}`, "
                f"shared=`{frame_report['shared_joint_count']}`, "
                f"top_translate=`{frame_report['top_translate']}`"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    _initialize()
    pmx_path = _resolve(args.pmx)
    vmd_path = _resolve(args.vmd)
    fbx_path = _resolve(args.fbx)
    frames = args.frame or [0, 1, 2, 3, 4, 5]
    current_frames = args.current_frame or frames
    if len(current_frames) != len(frames):
        raise ValueError("--current-frame count must match --frame count")
    for path in (pmx_path, vmd_path, fbx_path):
        if not path.exists():
            raise FileNotFoundError(path)

    oracle = _import_fbx_oracle(fbx_path, frames)
    modes = ["bake", "rig"] if args.mode == "both" else [args.mode]
    comparisons: dict[str, Any] = {}
    current_joint_counts: dict[str, int] = {}
    for mode in modes:
        current = _import_current(pmx_path, vmd_path, mode, current_frames, args.disable_runtime_bake)
        current_joint_counts[mode] = int(current["joint_count"])
        comparisons[mode] = _compare(current, oracle, frames, current_frames, args.top)

    report = {
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "fbx": str(fbx_path),
        "frames": frames,
        "current_frames": current_frames,
        "disable_runtime_bake": args.disable_runtime_bake,
        "oracle_joint_count": oracle["joint_count"],
        "current_joint_counts": current_joint_counts,
        "comparisons": comparisons,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(report, out.with_suffix(".md"))
    print(f"Report JSON: {out}")
    print(f"Report Markdown: {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
