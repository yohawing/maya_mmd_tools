"""Fail-closed parity harness for the two VMD model-import routes.

The same PMX/VMD fixture is imported into two fresh Maya standalone scenes:
the legacy bone-owned route and ``create_mmd_control_rig=True``.  The report
keeps route parity independent from the external ``mmd-anim`` oracle; this
script does not change fixtures or numeric thresholds.

Usage::

    mayapy -m tests.viewport.mmd_control_rig_vmd_import_parity --maya 2026
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping

import maya.standalone


_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_DEFAULT_MODEL = _ROOT / "tests" / "data" / "mmt_test_model.pmx"
_DEFAULT_MOTION = _ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd"
_MATRIX_EPSILON = 5.0e-3
_EVALUATION_MODES = ("dg", "serial", "parallel")
_MAYA_EVALUATION_MODES = {"dg": "off", "serial": "serial", "parallel": "parallel"}
cmds = None


def _floats(value: Any) -> list[float]:
    if value is None or isinstance(value, (str, bytes)):
        return []
    if isinstance(value, Iterable):
        result: list[float] = []
        for item in value:
            result.extend(_floats(item))
        return result
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _matrix_error(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if len(left_values) != len(right_values):
        return float("inf")
    values = [abs(float(a) - float(b)) for a, b in zip(left_values, right_values)]
    return max(values, default=0.0)


def _compact_error(value: Any) -> str:
    """Keep the first actionable failure while avoiding rollback-noise floods."""

    text = str(value)
    return text.split("; restore scene channel", 1)[0]


def _load_plugins() -> dict[str, str]:
    """Load the Python plugin and the native solver needed by Control Rig."""

    maya_major = str(cmds.about(version=True)).split(".", 1)[0]
    cpp = _ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    if not cpp.is_file():
        raise RuntimeError(f"required native plugin is missing: {cpp}")
    plugin_dir = str(cpp.parent)
    if plugin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
    if not cmds.pluginInfo(str(cpp), query=True, loaded=True):
        cmds.loadPlugin(str(cpp), quiet=True)
    py_plugin = _ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(py_plugin.stem, query=True, loaded=True):
        cmds.loadPlugin(str(py_plugin), quiet=True)
    return {"python": str(py_plugin), "native": str(cpp)}


def _apply_evaluation_mode(requested: str) -> dict[str, Any]:
    """Set and read back one exact Maya evaluation mode before either route."""

    requested = str(requested).lower()
    if requested not in _EVALUATION_MODES:
        raise ValueError(f"unsupported evaluation mode: {requested}")
    maya_requested = _MAYA_EVALUATION_MODES[requested]
    cmds.evaluationManager(mode=maya_requested)
    raw = cmds.evaluationManager(query=True, mode=True) or []
    maya_mode = str(raw[0]) if raw else "unknown"
    active = "dg" if maya_mode == "off" else maya_mode
    result = {
        "requested": requested,
        "mayaRequested": maya_requested,
        "mayaMode": maya_mode,
        "active": active,
        "pass": active == requested,
    }
    if not result["pass"]:
        raise RuntimeError(
            f"evaluation mode readback mismatch: requested={requested} maya={maya_mode}"
        )
    return result


def _import_model(model: Path) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(model),
        options={
            "use_namespace": True,
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
            "create_mmd_shaders": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
        },
    )
    if not root:
        raise RuntimeError(f"PMX import failed: {model}")
    return str(root)


def _joint_skin_records(root: str) -> dict[str, dict[str, Any]]:
    """Index joints and their JO-aware bind-pre * world observables."""

    records: dict[str, dict[str, Any]] = {}
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    for joint in joints:
        try:
            if not cmds.attributeQuery("mmd_bone_index", node=joint, exists=True):
                continue
            index = str(int(cmds.getAttr(f"{joint}.mmd_bone_index")))
        except (TypeError, ValueError, RuntimeError):
            continue
        records[index] = {"joint": str(joint), "skin": []}

    for skin in cmds.ls(type="skinCluster", long=True) or []:
        for logical in cmds.getAttr(f"{skin}.matrix", multiIndices=True) or []:
            sources = cmds.listConnections(
                f"{skin}.matrix[{logical}]", source=True, destination=False, plugs=True
            ) or []
            if not sources:
                continue
            source = str(sources[0]).split(".", 1)[0]
            source_paths = cmds.ls(source, long=True) or []
            if not source_paths:
                continue
            source_path = str(source_paths[0])
            match = next((key for key, row in records.items() if row["joint"] == source_path), None)
            if match is None:
                continue
            records[match]["skin"].append(
                {"skinCluster": str(skin), "logicalIndex": int(logical)}
            )
    for row in records.values():
        row["skin"] = sorted(row["skin"], key=lambda item: (item["skinCluster"], item["logicalIndex"]))
    return dict(sorted(records.items(), key=lambda item: int(item[0])))


def _skin_matrix(joint: str, skin: str, logical: int) -> list[float]:
    import maya.api.OpenMaya as om

    bind = _floats(cmds.getAttr(f"{skin}.bindPreMatrix[{logical}]"))
    world = _floats(cmds.getAttr(f"{joint}.worldMatrix[0]"))
    product = om.MMatrix(bind) * om.MMatrix(world)
    return [float(product[index]) for index in range(16)]


def _capture_observables(records: Mapping[str, Mapping[str, Any]], frames: list[int]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        joints: dict[str, Any] = {}
        for index, row in records.items():
            joint = str(row["joint"])
            if not cmds.objExists(joint):
                raise RuntimeError(f"indexed joint disappeared at frame {frame}: {joint}")
            joints[index] = {
                "worldMatrix": _floats(cmds.getAttr(f"{joint}.worldMatrix[0]")),
                "skinMatrices": [
                    _skin_matrix(joint, item["skinCluster"], int(item["logicalIndex"]))
                    for item in row["skin"]
                ],
            }
        captured[str(frame)] = joints
    return captured


def _keyframe_inventory(root: str) -> list[dict[str, Any]]:
    rows = []
    for curve in cmds.ls(type="animCurve", long=True) or []:
        destinations = sorted(
            str(value)
            for value in (cmds.listConnections(curve, source=False, destination=True, plugs=True) or [])
        )
        if not destinations:
            continue
        rows.append(
            {
                "curve": str(curve),
                "destinations": destinations,
                "times": _floats(cmds.keyframe(curve, query=True, timeChange=True)),
                "values": _floats(cmds.keyframe(curve, query=True, valueChange=True)),
            }
        )
    return sorted(rows, key=lambda item: item["curve"])


def _evaluation_evidence(frames: list[int], records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Exercise sequential, repeated, and non-sequential frame seeks."""

    sequential = list(frames)
    repeated_frame = frames[min(2, len(frames) - 1)]
    first = _capture_observables(records, [repeated_frame])[str(repeated_frame)]
    second = _capture_observables(records, [repeated_frame])[str(repeated_frame)]
    repeated_error = 0.0
    for index in first:
        repeated_error = max(
            repeated_error,
            _matrix_error(first[index]["worldMatrix"], second[index]["worldMatrix"]),
        )
    random_order = random.Random(0).sample(frames, len(frames))
    _capture_observables(records, random_order)
    return {
        "sequentialFrames": sequential,
        "repeatedFrame": repeated_frame,
        "repeatedWorldMatrixMaxError": repeated_error,
        "randomSeekOrder": random_order,
        "pass": repeated_error <= _MATRIX_EPSILON,
    }


def _route_snapshot(root: str, requested_control_rig: bool, profile: Mapping[str, Any]) -> dict[str, Any]:
    from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata

    metadata = read_mmd_control_rig_metadata(root)
    return {
        "requestedCreateMmdControlRig": bool(requested_control_rig),
        "route": "vmd_import_control_rig" if requested_control_rig else "legacy_bone_vmd_import",
        "owner": metadata.get("owner") if metadata else "MMD_BONE",
        "state": metadata.get("state") if metadata else "legacy",
        "controlRigMetadataPresent": metadata is not None,
        "profile": dict(profile),
    }


def _run_route(model: Path, motion: Path, create_control_rig: bool, frames: list[int]) -> dict[str, Any]:
    from mmd_tools.io.mmd_importer import import_mmd_file

    cmds.file(new=True, force=True)
    root = _import_model(model)
    profile: dict[str, Any] = {}
    options = {
        "target_model": root,
        "pmx_path": str(model),
        "bake_mode": False,
        "clear_existing_motion": True,
        "profile": profile,
        "create_mmd_control_rig": bool(create_control_rig),
    }
    try:
        imported = import_mmd_file(str(motion), options=options)
    except Exception as exc:  # noqa: BLE001 - route red evidence is part of the report
        return {
            "root": root,
            "route": _route_snapshot(root, create_control_rig, profile),
            "importStatus": "fail",
            "error": _compact_error(exc),
            "keyframes": _keyframe_inventory(root),
            "observables": {},
            "evaluation": {"pass": False},
            "records": {},
        }
    if not imported:
        return {
            "root": root,
            "route": _route_snapshot(root, create_control_rig, profile),
            "importStatus": "fail",
            "error": f"VMD import returned false: control_rig={create_control_rig}",
            "keyframes": _keyframe_inventory(root),
            "observables": {},
            "evaluation": {"pass": False},
            "records": {},
        }
    records = _joint_skin_records(root)
    if not records:
        raise RuntimeError(f"no indexed joints below imported root: {root}")
    return {
        "root": root,
        "importStatus": "pass",
        "route": _route_snapshot(root, create_control_rig, profile),
        "keyframes": _keyframe_inventory(root),
        "observables": _capture_observables(records, frames),
        "evaluation": _evaluation_evidence(frames, records),
        "records": records,
    }


def _compare(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    divergences = []
    ref_frames = reference["observables"]
    candidate_frames = candidate["observables"]
    for frame in sorted(set(ref_frames) | set(candidate_frames), key=int):
        if frame not in ref_frames or frame not in candidate_frames:
            divergences.append({"category": "frame_set", "frame": int(frame)})
            continue
        ref_joints = ref_frames[frame]
        candidate_joints = candidate_frames[frame]
        for index in sorted(set(ref_joints) | set(candidate_joints), key=int):
            if index not in ref_joints or index not in candidate_joints:
                divergences.append({"category": "joint_set", "frame": int(frame), "jointIndex": index})
                continue
            world_error = _matrix_error(ref_joints[index]["worldMatrix"], candidate_joints[index]["worldMatrix"])
            ref_skin = ref_joints[index]["skinMatrices"]
            candidate_skin = candidate_joints[index]["skinMatrices"]
            if len(ref_skin) != len(candidate_skin):
                divergences.append(
                    {
                        "category": "skin_matrix_count",
                        "frame": int(frame),
                        "jointIndex": index,
                        "legacyCount": len(ref_skin),
                        "directCount": len(candidate_skin),
                    }
                )
            skin_error = max(
                (_matrix_error(left, right) for left, right in zip(ref_skin, candidate_skin)),
                default=0.0,
            )
            row = {"frame": int(frame), "jointIndex": index, "worldMatrixMax": world_error, "skinMatrixMax": skin_error}
            rows.append(row)
            if max(world_error, skin_error) > _MATRIX_EPSILON:
                divergences.append({"category": "jo_aware_matrix", **row})
    worst_world = max((row["worldMatrixMax"] for row in rows), default=0.0)
    worst_skin = max((row["skinMatrixMax"] for row in rows), default=0.0)
    return {
        "threshold": _MATRIX_EPSILON,
        "maxWorldMatrixError": worst_world,
        "maxSkinMatrixError": worst_skin,
        "firstDivergence": divergences[0] if divergences else None,
        "divergenceCount": len(divergences),
        "pass": not divergences and worst_world <= _MATRIX_EPSILON and worst_skin <= _MATRIX_EPSILON,
    }


def _coverage(vmd: Any) -> dict[str, Any]:
    """Report fixture coverage explicitly; missing categories stay red."""

    bone_names = {str(frame.bone_name) for frame in getattr(vmd, "bone_frames", []) or []}
    morph_present = bool(getattr(vmd, "morph_frames", None))
    ik_present = bool(getattr(vmd, "ik_show_hide_frames", None))
    append_names = sorted(
        name for name in bone_names if "付与" in name or "append" in name.lower()
    )
    foot_names = sorted(name for name in bone_names if "足IK" in name or "足ＩＫ" in name)
    toe_names = sorted(name for name in bone_names if "つま先IK" in name or "つま先ＩＫ" in name)
    rows = {
        "boneMorph": {"fixturePresent": morph_present, "status": "covered" if morph_present else "missing"},
        "append": {"fixturePresent": bool(append_names), "roles": append_names, "status": "covered" if append_names else "missing"},
        "footIk": {"fixturePresent": bool(foot_names), "roles": foot_names, "status": "covered" if foot_names else "missing"},
        "toeIk": {"fixturePresent": bool(toe_names), "roles": toe_names, "status": "covered" if toe_names else "missing"},
        "ikEnable": {"fixturePresent": ik_present, "status": "covered" if ik_present else "missing"},
    }
    return {"items": rows, "coverageMissing": sorted(name for name, row in rows.items() if row["status"] == "missing")}


def run(model: Path, motion: Path, output: Path, evaluation_mode: str = "dg") -> int:
    global cmds
    if cmds is None:
        import maya.cmds as maya_cmds

        cmds = maya_cmds
    payload: dict[str, Any] = {
        "kind": "mmd-control-rig-vmd-import-parity",
        "status": "error",
        "model": str(model),
        "motion": str(motion),
        "requiredRunMatrix": {
            "requestedModes": list(_EVALUATION_MODES),
            "currentMode": str(evaluation_mode),
            "singleModeReport": True,
            "complete": False,
        },
        "externalOracle": {
            "identity": "mmd-anim-mesh-oracle",
            "status": "not_run",
            "reason": "This harness reports route parity only; existing external oracle is separate.",
        },
    }
    try:
        plugins = _load_plugins()
        evaluation = _apply_evaluation_mode(evaluation_mode)
        from mmd_tools.core.vmd_data import VmdData

        if not model.is_file() or not motion.is_file():
            raise FileNotFoundError(f"fixture missing: model={model} motion={motion}")
        vmd = VmdData().parse_file(str(motion))
        if not vmd.bone_frames:
            raise RuntimeError("fixture VMD contains no bone frames")
        authored_frames = sorted({int(frame.frame_number) for frame in vmd.bone_frames})
        end = authored_frames[-1]
        frames = sorted({0, 1, max(1, end // 2), end, *authored_frames})
        coverage = _coverage(vmd)
        for name in ("externalOracle", "exportFreshImport", "evaluationModes"):
            coverage["items"][name] = {"status": "missing"}
        coverage_missing = sorted(
            set(coverage["coverageMissing"])
            | {"externalOracle", "exportFreshImport", "evaluationModes"}
        )
        coverage["coverageMissing"] = coverage_missing
        legacy = _run_route(model, motion, False, frames)
        direct = _run_route(model, motion, True, frames)
        baked = None
        bake_error = None
        try:
            if direct.get("importStatus") != "pass":
                raise RuntimeError("Control Rig direct VMD import failed; bake was not attempted")
            from mmd_tools.core.mmd_control_rig_motion import bake_mmd_control_rig

            baked_metadata = bake_mmd_control_rig(direct["root"])
            records = _joint_skin_records(direct["root"])
            baked = {
                "state": baked_metadata.get("state"),
                "owner": baked_metadata.get("owner"),
                "observables": _capture_observables(records, frames),
            }
        except (ImportError, AttributeError) as exc:
            bake_error = f"public bake API unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001 - preserve exact gate evidence
            bake_error = str(exc)
        if legacy.get("importStatus") == "pass" and direct.get("importStatus") == "pass":
            direct_vs_legacy = _compare(legacy, direct)
        else:
            failed_route = "legacy" if legacy.get("importStatus") != "pass" else "controlRigDirect"
            failed_detail = legacy if failed_route == "legacy" else direct
            direct_vs_legacy = {
                "threshold": _MATRIX_EPSILON,
                "pass": False,
                "firstDivergence": {
                    "category": "route_import",
                    "route": failed_route,
                    "error": failed_detail.get("error", "route import failed"),
                },
            }
        baked_vs_legacy = (
            _compare(legacy, {"observables": baked["observables"]})
            if baked and legacy.get("importStatus") == "pass"
            else {
                "pass": False,
                "firstDivergence": {
                    "category": "bake",
                    "error": bake_error or "legacy route unavailable",
                },
            }
        )
        payload.update(
            {
                "mayaVersion": str(cmds.about(version=True)),
                "plugins": plugins,
                "evaluationMode": evaluation,
                "frames": frames,
                "authoredBoneKeyFrames": authored_frames,
                "coverage": coverage,
                "coverageMissing": coverage_missing,
                "routes": {
                    "legacy": {key: value for key, value in legacy.items() if key != "records"},
                    "controlRigDirect": {key: value for key, value in direct.items() if key != "records"},
                },
                "routeParity": {
                    "directVsLegacy": direct_vs_legacy,
                    "bakedVsLegacy": baked_vs_legacy,
                    "pass": bool(direct_vs_legacy.get("pass")) and bool(baked_vs_legacy.get("pass")),
                },
                "bakeToMmdRig": {
                    "attempted": direct.get("importStatus") == "pass",
                    "status": (
                        "pass"
                        if baked
                        else (
                            "not_attempted"
                            if direct.get("importStatus") != "pass"
                            else (
                                "not_available"
                                if bake_error and bake_error.startswith("public bake API unavailable")
                                else "fail"
                            )
                        )
                    ),
                    "error": bake_error,
                    "result": baked,
                },
            }
        )
        payload["status"] = (
            "pass"
            if payload["routeParity"]["pass"] and not coverage_missing
            else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - fail closed in report
        payload["error"] = str(exc)
        payload["status"] = "blocked" if isinstance(exc, (FileNotFoundError, RuntimeError)) else "error"
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": payload["status"], "report": str(output), "firstDivergence": payload.get("routeParity", {}).get("directVsLegacy", {}).get("firstDivergence")}, ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2026", help="report label; mayapy supplies the actual version")
    parser.add_argument("--evaluation-mode", choices=_EVALUATION_MODES, default="dg")
    parser.add_argument("--model", default=str(_DEFAULT_MODEL))
    parser.add_argument("--motion", default=str(_DEFAULT_MOTION))
    parser.add_argument("--out", default=str(_ROOT / "build" / "reports" / "mmd_control_rig_vmd_import_parity.json"))
    args = parser.parse_args()
    return run(
        Path(args.model).resolve(),
        Path(args.motion).resolve(),
        Path(args.out).resolve(),
        args.evaluation_mode,
    )


if __name__ == "__main__":
    try:
        maya.standalone.initialize(name="python")
    except Exception:
        traceback.print_exc()
        raise
    exit_code = 0 if main() == 0 else 1
    sys.stdout.flush()
    sys.stderr.flush()
    maya.standalone.uninitialize()
    raise SystemExit(exit_code)
