"""End-to-end gate for the opt-in reduced runtime-bake key path.

The harness intentionally runs the same PMX/VMD import twice in fresh Maya
standalone scenes.  The first scene is the dense runtime bake and becomes the
oracle; the second uses ``reduce_bake_keys=True`` and is compared at every
Maya sample frame.  World matrices are mandatory.  When a skinned mesh or
PMX morph controller is present, JO-aware skin matrices and morph weights are
compared as additional gates.  The reduced scene is saved and reopened before
the final comparison so serialization is part of the acceptance evidence.

Typical invocation (the Maya version is selected by the caller's mayapy):

    mayapy tests/viewport/reduced_pose_runtime_e2e.py --maya 2024 \
        --pmx tests/data/mmt_test_model.pmx \
        --vmd tests/data/mmt_test_model_test_motion.vmd \
        --fps 60 --start-frame 120

The updated mmd-anim FFI must be discoverable through the absolute
``MMD_ANIM_FFI_PATH`` environment variable.  ``--physics`` opts into the
native physics import/bake route when the supplied DLL advertises it.
For long motions, ``--profile-only`` keeps both imports and skips per-frame
Maya oracle sampling plus save/reopen checks, reporting only reducer/profile
and curve/key-count gates.
"""

from __future__ import annotations

import argparse
import builtins
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.standalone


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD_TOLERANCE = 1.0e-3
DEFAULT_SKIN_TOLERANCE = 1.0e-3
DEFAULT_MORPH_TOLERANCE = 1.0e-3
DEFAULT_REDUCE_TRANSLATE_TOLERANCE = 5.0e-4
DEFAULT_REDUCE_ROTATE_TOLERANCE = 1.0e-4
DEFAULT_REDUCE_MORPH_TOLERANCE = 1.0e-3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024", help="Maya version label for the report (mayapy is selected externally).")
    parser.add_argument("--pmx", default="tests/data/mmt_test_model.pmx")
    parser.add_argument("--vmd", default="tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--fps", type=int, choices=(30, 60), default=30)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--physics", action="store_true", help="Use import_physics and native physics bake when available.")
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Run both imports but skip frame oracle sampling and scene save/reopen checks.",
    )
    parser.add_argument("--out", default="build/reports/reduced_pose_runtime_e2e.json")
    parser.add_argument("--world-tolerance", type=float, default=DEFAULT_WORLD_TOLERANCE)
    parser.add_argument("--skin-tolerance", type=float, default=DEFAULT_SKIN_TOLERANCE)
    parser.add_argument("--morph-tolerance", type=float, default=DEFAULT_MORPH_TOLERANCE)
    parser.add_argument(
        "--reduce-translate-tolerance",
        type=float,
        default=DEFAULT_REDUCE_TRANSLATE_TOLERANCE,
        help="Reducer local/world translation tolerance (Maya units).",
    )
    parser.add_argument(
        "--reduce-rotate-tolerance",
        type=float,
        default=DEFAULT_REDUCE_ROTATE_TOLERANCE,
        help="Reducer local/world rotation tolerance (radians).",
    )
    parser.add_argument(
        "--reduce-morph-tolerance",
        type=float,
        default=DEFAULT_REDUCE_MORPH_TOLERANCE,
        help="Reducer morph-weight tolerance.",
    )
    return parser.parse_args()


def _initialize() -> None:
    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        # mayapy can already be initialized by a surrounding runner.
        pass
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    # Maya's Python plug-in loader executes ``plugin_main.py`` without a
    # module-level ``__file__``.  The production entrypoint uses that value
    # only to resolve its menu icon, so provide a temporary builtins fallback
    # while the canonical loader performs registration.  This keeps the E2E
    # runner self-contained without modifying plugin code or userSetup.py.
    previous_file = getattr(builtins, "__file__", None)
    builtins.__file__ = str((ROOT / "mmd_tools" / "plugin_main.py").resolve())
    try:
        load_mmd_tools_plugin(ROOT)
    finally:
        if previous_file is None:
            try:
                del builtins.__file__
            except AttributeError:
                pass
        else:
            builtins.__file__ = previous_file


def _resolve(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _json_safe(value: Any) -> Any:
    """Convert profile values to deterministic JSON primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _matrix(value: Any) -> List[float]:
    """Return a Maya matrix as a flat row-major list."""
    try:
        matrix = om.MMatrix(value)
        return [float(matrix[index]) for index in range(16)]
    except Exception:
        flat = list(value or [])
        return [float(flat[index]) for index in range(min(16, len(flat)))] + [0.0] * max(0, 16 - len(flat))


def _matrix_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))


def _long_name(node: str) -> str:
    names = cmds.ls(node, long=True) or []
    return names[0] if names else str(node)


def _joint_map(root: str) -> Dict[int, str]:
    result: Dict[int, str] = {}
    root_long = _long_name(root)
    for joint in cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []:
        if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
            continue
        if root_long and not (joint == root_long or joint.startswith(root_long + "|")):
            continue
        try:
            result[int(cmds.getAttr(f"{joint}.mmd_bone_index"))] = joint
        except (TypeError, ValueError, RuntimeError):
            continue
    return result


def _skin_influence_pairs(skin_cluster: str) -> Iterable[Tuple[str, int]]:
    """Yield ``(joint, logicalIndex)`` from actual matrix connections."""
    for plug in cmds.listConnections(f"{skin_cluster}.matrix", s=True, d=False, p=True) or []:
        joint = str(plug).split(".", 1)[0]
        for destination in cmds.listConnections(plug, s=False, d=True, p=True) or []:
            prefix = f"{skin_cluster}.matrix["
            if not str(destination).startswith(prefix):
                continue
            try:
                yield joint, int(str(destination).split("[", 1)[1].split("]", 1)[0])
            except (IndexError, ValueError):
                continue


def _skin_clusters(root: str) -> List[str]:
    clusters: List[str] = []
    for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []:
        if cmds.getAttr(f"{shape}.intermediateObject"):
            continue
        for node in cmds.listHistory(shape, pruneDagObjects=True) or []:
            if cmds.nodeType(node) == "skinCluster" and node not in clusters:
                clusters.append(node)
    return clusters


def _capture_skin_matrices(root: str, joints: Mapping[int, str]) -> Dict[int, List[Dict[str, Any]]]:
    result: Dict[int, List[Dict[str, Any]]] = {}
    for skin_cluster in _skin_clusters(root):
        for joint, logical_index in _skin_influence_pairs(skin_cluster):
            if not cmds.objExists(joint) or not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            try:
                bone_index = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
                bind_pre = om.MMatrix(cmds.getAttr(f"{skin_cluster}.bindPreMatrix[{logical_index}]"))
                world = om.MMatrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
            except (TypeError, ValueError, RuntimeError):
                continue
            if bone_index not in joints:
                continue
            result.setdefault(bone_index, []).append(
                {
                    "skin_cluster": _long_name(skin_cluster),
                    "logical_index": int(logical_index),
                    "matrix": _matrix(bind_pre * world),
                }
            )
    return result


def _morph_controller(root: str) -> Optional[str]:
    if not cmds.attributeQuery("mmd_morph_controller", node=root, exists=True):
        return None
    controllers = cmds.listConnections(
        f"{root}.mmd_morph_controller", source=True, destination=False
    ) or []
    return controllers[0] if len(controllers) == 1 else None


def _capture_morph_values(root: str, morph_count: int) -> Optional[Dict[str, float]]:
    controller = _morph_controller(root)
    if not controller:
        return None
    indices = cmds.getAttr(f"{controller}.inputWeight", multiIndices=True) or []
    if not indices:
        indices = list(range(max(0, int(morph_count))))
    values: Dict[str, float] = {}
    for index in indices:
        try:
            values[str(int(index))] = float(cmds.getAttr(f"{controller}.inputWeight[{int(index)}]"))
        except (TypeError, ValueError, RuntimeError):
            continue
    return values


def _capture_frame(root: str, morph_count: int) -> Dict[str, Any]:
    joints = _joint_map(root)
    worlds: Dict[str, List[float]] = {}
    for bone_index, joint in sorted(joints.items()):
        try:
            worlds[str(bone_index)] = _matrix(cmds.getAttr(f"{joint}.worldMatrix[0]"))
        except (TypeError, ValueError, RuntimeError):
            continue
    skin = _capture_skin_matrices(root, joints)
    morphs = _capture_morph_values(root, morph_count)
    return {
        "world": worlds,
        "skin": {str(index): rows for index, rows in sorted(skin.items())},
        "morph": morphs,
    }


def _sample_scene(root: str, frames: Sequence[int], morph_count: int) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for frame in frames:
        cmds.currentTime(int(frame), edit=True)
        try:
            cmds.refresh(force=True)
        except RuntimeError:
            pass
        result[str(int(frame))] = _capture_frame(root, morph_count)
    return result


def _count_animation_keys() -> Dict[str, int]:
    curves = cmds.ls(type="animCurve") or []
    per_curve: Dict[str, int] = {}
    for curve in curves:
        try:
            per_curve[_long_name(curve)] = int(cmds.keyframe(curve, query=True, keyframeCount=True) or 0)
        except (TypeError, ValueError, RuntimeError):
            per_curve[_long_name(curve)] = 0
    return per_curve


def _shift_animation_keys(start_frame: int) -> None:
    """Apply the harness-only non-zero timeline offset to authored curves."""
    if not start_frame:
        return
    for curve in cmds.ls(type="animCurve") or []:
        try:
            cmds.keyframe(curve, edit=True, relative=True, timeChange=float(start_frame))
        except (TypeError, ValueError, RuntimeError):
            # A non-animation helper curve should not make the gate unusable.
            continue
    end_time = float(cmds.playbackOptions(query=True, max=True)) + float(start_frame)
    cmds.playbackOptions(
        min=float(start_frame),
        animationStartTime=float(start_frame),
        max=end_time,
        animationEndTime=end_time,
    )


def _import_scene(
    pmx_path: Path,
    vmd_path: Path,
    *,
    fps: int,
    start_frame: int,
    physics: bool,
    reduce: bool,
    reduce_translate_tolerance: float,
    reduce_rotate_tolerance: float,
    reduce_morph_tolerance: float,
) -> Tuple[str, Dict[str, Any], Dict[str, int]]:
    from mmd_tools.core import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    settings.set("import.model.create_mmd_shaders", False)
    settings.set("import.rig.add_semi_standard_bones", False)
    settings.set("import.native.require_native_pmx_parse", False)
    settings.set("import.native.use_cpp_fast_load", False)
    settings.set("logging.level", "INFO")
    root = import_mmd_file(
        str(pmx_path),
        options={
            "setup_rig": False,
            "setup_bone_orientation": False,
            "import_physics": bool(physics),
            "create_physics_joints": bool(physics),
            "create_mmd_shaders": False,
            "use_namespace": False,
            "cpp_fast_load": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {pmx_path}")
    profile: Dict[str, Any] = {}
    ok = import_mmd_file(
        str(vmd_path),
        options={
            "target_model": root,
            "pmx_path": str(pmx_path),
            "bake_mode": True,
            "use_native_physics_bake": bool(physics),
            "reduce_bake_keys": bool(reduce),
            "reduce_translate_tolerance": float(reduce_translate_tolerance),
            "reduce_rotate_tolerance": float(reduce_rotate_tolerance),
            "reduce_morph_tolerance": float(reduce_morph_tolerance),
            "vmd_fps": int(fps),
            "profile": profile,
        },
    )
    if not ok:
        raise RuntimeError(f"VMD bake import failed (reduce={reduce}, physics={physics}): {vmd_path}")
    _shift_animation_keys(int(start_frame))
    return _long_name(root), profile, _count_animation_keys()


def _compare_frames(
    dense: Mapping[str, Mapping[str, Any]],
    reduced: Mapping[str, Mapping[str, Any]],
    frames: Sequence[int],
    *,
    world_tolerance: float,
    skin_tolerance: float,
    morph_tolerance: float,
) -> Dict[str, Any]:
    world_max = 0.0
    skin_max = 0.0
    morph_max = 0.0
    diagnostics: List[Dict[str, Any]] = []
    world_missing = skin_missing = morph_missing = 0
    morph_present = any((dense.get(str(frame), {}).get("morph") is not None) for frame in frames)
    skin_present = any(bool(dense.get(str(frame), {}).get("skin")) for frame in frames)
    for frame in frames:
        key = str(int(frame))
        left = dense.get(key, {})
        right = reduced.get(key, {})
        left_world = left.get("world") or {}
        right_world = right.get("world") or {}
        for bone_index, matrix in left_world.items():
            if bone_index not in right_world:
                world_missing += 1
                continue
            error = _matrix_distance(matrix, right_world[bone_index])
            world_max = max(world_max, error)
            if error > world_tolerance:
                diagnostics.append({"frame": int(frame), "oracle": "world", "bone_index": bone_index, "error": error})

        left_skin = left.get("skin") or {}
        right_skin = right.get("skin") or {}
        for bone_index, rows in left_skin.items():
            candidates = right_skin.get(bone_index, [])
            by_cluster = {str(row.get("skin_cluster")): row for row in candidates}
            for row in rows:
                candidate = by_cluster.get(str(row.get("skin_cluster")))
                if candidate is None:
                    skin_missing += 1
                    continue
                error = _matrix_distance(row.get("matrix", []), candidate.get("matrix", []))
                skin_max = max(skin_max, error)
                if error > skin_tolerance:
                    diagnostics.append({"frame": int(frame), "oracle": "skin", "bone_index": bone_index, "error": error})

        left_morph = left.get("morph") or {}
        right_morph = right.get("morph") or {}
        if left.get("morph") is not None:
            for morph_index, value in left_morph.items():
                if morph_index not in right_morph:
                    morph_missing += 1
                    continue
                error = abs(float(value) - float(right_morph[morph_index]))
                morph_max = max(morph_max, error)
                if error > morph_tolerance:
                    diagnostics.append({"frame": int(frame), "oracle": "morph", "morph_index": morph_index, "error": error})

    world_pass = not world_missing and world_max <= world_tolerance
    skin_pass = (not skin_present) or (not skin_missing and skin_max <= skin_tolerance)
    morph_pass = (not morph_present) or (not morph_missing and morph_max <= morph_tolerance)
    return {
        "passed": bool(world_pass and skin_pass and morph_pass),
        "world": {"passed": world_pass, "max_error": world_max, "missing": world_missing, "tolerance": world_tolerance},
        "skin": {"passed": skin_pass, "max_error": skin_max, "missing": skin_missing, "tolerance": skin_tolerance, "skipped": not skin_present},
        "morph": {"passed": morph_pass, "max_error": morph_max, "missing": morph_missing, "tolerance": morph_tolerance, "skipped": not morph_present},
        "diagnostics": sorted(diagnostics, key=lambda item: float(item.get("error", 0.0)), reverse=True)[:50],
    }


def _profile_reduction(profile: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(((profile.get("vmd_converter") or {}).get("reduced_bake_keys") or {}))


def _find_reopened_root(previous_root: str, pmx_path: Path) -> str:
    if cmds.objExists(previous_root):
        return previous_root
    for node in cmds.ls(type="transform", long=True) or []:
        if cmds.attributeQuery("mmd_source_file", node=node, exists=True):
            try:
                source = str(cmds.getAttr(f"{node}.mmd_source_file") or "")
            except RuntimeError:
                source = ""
            if source and Path(source).resolve() == pmx_path.resolve():
                return node
    raise RuntimeError("reduced scene reopened but model root could not be resolved")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    report_path = _resolve(args.out)
    pmx_path = _resolve(args.pmx)
    vmd_path = _resolve(args.vmd)
    ffi_raw = os.environ.get("MMD_ANIM_FFI_PATH", "").strip()
    report: Dict[str, Any] = {
        "status": "failed",
        "maya": str(args.maya),
        "pmx": str(pmx_path),
        "vmd": str(vmd_path),
        "fps": int(args.fps),
        "start_frame": int(args.start_frame),
        "physics": bool(args.physics),
        "profile_only": bool(args.profile_only),
        "mmd_anim_ffi_path": ffi_raw,
        "report": str(report_path),
        "reduce_tolerances": {
            "translate": float(args.reduce_translate_tolerance),
            "rotate": float(args.reduce_rotate_tolerance),
            "morph": float(args.reduce_morph_tolerance),
        },
    }
    if not ffi_raw:
        report.update({"status": "skipped", "gate": "MMD_ANIM_FFI_PATH_missing", "reason": "updated local mmd-anim DLL path is required"})
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3
    ffi_path = Path(ffi_raw).expanduser()
    if not ffi_path.exists():
        report.update({"status": "failed", "gate": "MMD_ANIM_FFI_PATH_missing", "reason": f"path does not exist: {ffi_raw}"})
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3
    if not pmx_path.is_file() or not vmd_path.is_file():
        report.update({"gate": "fixture_missing", "reason": f"PMX/VMD fixture missing: {pmx_path} / {vmd_path}"})
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if min(
        float(args.world_tolerance),
        float(args.skin_tolerance),
        float(args.morph_tolerance),
        float(args.reduce_translate_tolerance),
        float(args.reduce_rotate_tolerance),
        float(args.reduce_morph_tolerance),
    ) < 0.0:
        report.update({"gate": "invalid_tolerance", "reason": "tolerances must be non-negative"})
        _write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        _initialize()
        from mmd_tools.converters.vmd_timeline import get_animation_frame_range
        from mmd_tools.core.mmd_parser import parse_mmd_file
        from mmd_tools.core.native.mmd_anim_runtime import get_runtime_feature_flags, get_runtime_library_path, is_mmd_runtime_available

        parsed_vmd = parse_mmd_file(str(vmd_path))
        _min_vmd, max_vmd = get_animation_frame_range(parsed_vmd)
        max_maya = int(math.floor(float(max_vmd) * float(args.fps) / 30.0 + 1.0e-9))
        frames = [int(args.start_frame) + offset for offset in range(max_maya + 1)]
        morph_count = len(
            getattr(
                parse_mmd_file(str(pmx_path), require_native_pmx_parse=False),
                "morphs",
                [],
            )
            or []
        )
        report.update(
            {
                "runtime_available": bool(is_mmd_runtime_available()),
                "runtime_library_path": str(get_runtime_library_path() or ""),
                "feature_flags": int(get_runtime_feature_flags()),
                "max_vmd_frame": int(max_vmd),
                "sample_frames": frames,
                "morph_count": int(morph_count),
            }
        )
        if not report["runtime_available"]:
            report.update({"gate": "runtime_unavailable", "reason": "MMD_ANIM_FFI_PATH did not load as a supported runtime"})
            _write_report(report_path, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 3

        dense_root, dense_profile, dense_curve_keys = _import_scene(
            pmx_path,
            vmd_path,
            fps=int(args.fps),
            start_frame=int(args.start_frame),
            physics=bool(args.physics),
            reduce=False,
            reduce_translate_tolerance=float(args.reduce_translate_tolerance),
            reduce_rotate_tolerance=float(args.reduce_rotate_tolerance),
            reduce_morph_tolerance=float(args.reduce_morph_tolerance),
        )
        dense_samples = {} if args.profile_only else _sample_scene(dense_root, frames, morph_count)
        report["dense"] = {
            "root": dense_root,
            "profile": _json_safe(dense_profile),
            "curve_count": len(dense_curve_keys),
            "key_count": sum(dense_curve_keys.values()),
        }

        reduced_root, reduced_profile_raw, reduced_curve_keys = _import_scene(
            pmx_path,
            vmd_path,
            fps=int(args.fps),
            start_frame=int(args.start_frame),
            physics=bool(args.physics),
            reduce=True,
            reduce_translate_tolerance=float(args.reduce_translate_tolerance),
            reduce_rotate_tolerance=float(args.reduce_rotate_tolerance),
            reduce_morph_tolerance=float(args.reduce_morph_tolerance),
        )
        reduced_samples = {} if args.profile_only else _sample_scene(reduced_root, frames, morph_count)
        reduction_profile = _profile_reduction(reduced_profile_raw)
        report["reduced"] = {
            "root": reduced_root,
            "profile": _json_safe(reduced_profile_raw),
            "reduction": _json_safe(reduction_profile),
            "curve_count": len(reduced_curve_keys),
            "key_count": sum(reduced_curve_keys.values()),
        }
        if args.profile_only:
            comparison = {"passed": True, "skipped": True, "reason": "profile-only mode"}
            reopen_comparison = {"passed": True, "skipped": True, "reason": "profile-only mode"}
            report["skipped_reasons"] = [
                "per-frame world/skin/morph oracle sampling",
                "dense-vs-reduced oracle comparison",
                "reduced scene save/reopen oracle",
            ]
        else:
            comparison = _compare_frames(
                dense_samples,
                reduced_samples,
                frames,
                world_tolerance=float(args.world_tolerance),
                skin_tolerance=float(args.skin_tolerance),
                morph_tolerance=float(args.morph_tolerance),
            )
            reduced_scene = report_path.with_name(report_path.stem + "_reduced.ma")
            cmds.file(rename=str(reduced_scene))
            cmds.file(save=True, type="mayaAscii", force=True)
            report["reduced_scene"] = str(reduced_scene)

            cmds.file(new=True, force=True)
            cmds.file(str(reduced_scene), open=True, force=True)
            reopened_root = _find_reopened_root(reduced_root, pmx_path)
            reopened_samples = _sample_scene(reopened_root, frames, morph_count)
            reopen_comparison = _compare_frames(
                dense_samples,
                reopened_samples,
                frames,
                world_tolerance=float(args.world_tolerance),
                skin_tolerance=float(args.skin_tolerance),
                morph_tolerance=float(args.morph_tolerance),
            )
        report["comparison"] = comparison
        report["reopen_comparison"] = reopen_comparison

        reduction_used = bool(reduction_profile.get("used"))
        reduced_key_count = int(report["reduced"]["key_count"])
        dense_key_count = int(report["dense"]["key_count"])
        assertions = {
            "reducer_used": reduction_used,
            "key_count_reduced": reduced_key_count < dense_key_count,
        }
        if not args.profile_only:
            assertions.update(
                {
                    "runtime_world_oracle": bool(comparison["world"]["passed"]),
                    "runtime_skin_oracle": bool(comparison["skin"]["passed"]),
                    "runtime_morph_oracle": bool(comparison["morph"]["passed"]),
                    "scene_reopen_oracle": bool(reopen_comparison["passed"]),
                }
            )
        report["assertions"] = assertions
        report["status"] = "passed" if all(assertions.values()) else "failed"
        report["gate"] = "ok" if report["status"] == "passed" else "reduced_pose_runtime_e2e"
        if args.profile_only:
            report["skin_skip"] = "profile-only mode skipped skin oracle"
            report["morph_skip"] = "profile-only mode skipped morph oracle"
        else:
            if not comparison["skin"].get("skipped"):
                report["skin_skip"] = None
            else:
                report["skin_skip"] = "no skinned mesh / skinCluster under model root"
            if not comparison["morph"].get("skipped"):
                report["morph_skip"] = None
            else:
                report["morph_skip"] = "no mmdMorphController under model root"
    except Exception as exc:
        report.update({"status": "failed", "gate": "exception", "error": f"{type(exc).__name__}: {exc}"})

    _write_report(report_path, report)
    print(json.dumps(_json_safe(report), ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
