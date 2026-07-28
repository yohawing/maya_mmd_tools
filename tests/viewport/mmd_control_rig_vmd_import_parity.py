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


def _remove_stale_artifacts(paths: list[Path]) -> None:
    """Remove exact generated artifacts or fail before collecting evidence."""

    for artifact in paths:
        try:
            artifact.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"stale VMD cleanup failed: {artifact}: {exc}") from exc


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
        bone_name = ""
        try:
            if cmds.attributeQuery("mmd_bone_name", node=joint, exists=True):
                bone_name = str(cmds.getAttr(f"{joint}.mmd_bone_name") or "")
        except (RuntimeError, TypeError):
            bone_name = ""
        records[index] = {"joint": str(joint), "boneName": bone_name, "skin": []}

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


def _capture_observables(
    records: Mapping[str, Mapping[str, Any]], frames: list[int | float]
) -> dict[str, Any]:
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
                "boneName": str(row.get("boneName", "")),
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


def _fresh_bone_key_times(root: str) -> list[dict[str, Any]]:
    """Resolve fresh upstream key times to PMX bone names.

    Query each target joint independently so an intermediate blend node cannot
    leak one curve's times into another bone or channel.
    """

    records = _joint_skin_records(root)
    rows = []
    for record in records.values():
        joint = str(record["joint"])
        if not cmds.objExists(joint):
            continue
        name = str(cmds.getAttr(f"{joint}.mmd_bone_name") or "")
        if not name:
            continue
        times = {
            _frame_value(value)
            for value in _floats(cmds.keyframe(joint, query=True, timeChange=True))
        }
        if times:
            rows.append({"boneName": name, "times": sorted(times, key=_frame_sort_key)})
    return sorted(rows, key=lambda row: row["boneName"])


def _compare_fresh_bone_key_times(exported: Any, fresh_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare exported VMD bone frame times with fresh Maya curve keys.

    VMD stores one key time for the whole bone, while Maya may omit constant
    Euler or translation components.  Compare the union of authored component
    times per bone and keep the per-channel rows as diagnostics.
    """

    normalized = _normalized_vmd(exported)
    expected_by_bone: dict[str, set[int | float]] = {}
    for bone_name, frame in normalized["boneKeyTimes"]:
        expected_by_bone.setdefault(str(bone_name), set()).add(frame)
    actual_by_bone: dict[str, set[int | float]] = {}
    for row in fresh_rows:
        actual_by_bone.setdefault(str(row["boneName"]), set()).update(row["times"])
    mismatches = []
    for bone_name, expected_times in sorted(expected_by_bone.items()):
        expected = tuple(sorted(expected_times, key=_frame_sort_key))
        fresh = tuple(sorted(actual_by_bone.get(bone_name, ()), key=_frame_sort_key))
        if fresh != expected:
            mismatches.append(
                {
                    "boneName": bone_name,
                    "exported": list(expected),
                    "fresh": list(fresh),
                }
            )
    for bone_name in sorted(set(actual_by_bone) - set(expected_by_bone)):
        mismatches.append(
            {
                "boneName": bone_name,
                "exported": [],
                "fresh": sorted(actual_by_bone[bone_name], key=_frame_sort_key),
            }
        )
    first = mismatches[0] if mismatches else None
    return {
        "exportedBoneCount": len(expected_by_bone),
        "freshBoneCount": len(actual_by_bone),
        "mismatchCount": len(mismatches),
        "firstMismatch": first,
        "pass": bool(expected_by_bone) and not mismatches,
    }


def _ik_state_inventory(root: str, frames: list[int | float]) -> list[dict[str, Any]]:
    """Capture target-owned ``mmdCcdIk.enabled`` by PMX IK bone name."""

    from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name

    nodes_by_name = collect_ik_nodes_by_bone_name(target_model=str(root))
    rows = []
    for frame in frames:
        cmds.currentTime(frame, edit=True)
        cmds.refresh(force=True)
        states = []
        for bone_name, node in sorted(nodes_by_name.items()):
            try:
                enabled = bool(cmds.getAttr(f"{node}.enabled"))
            except (RuntimeError, TypeError):
                enabled = None
            states.append({"boneName": str(bone_name), "enabled": enabled})
        rows.append({"frame": _frame_value(frame), "states": states})
    return rows


def _compare_ik_state_inventory(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare evaluated IK enabled state, preserving the first mismatch."""

    reference_map = {row["frame"]: row["states"] for row in reference}
    candidate_map = {row["frame"]: row["states"] for row in candidate}
    first = None
    for frame in sorted(set(reference_map) | set(candidate_map), key=_frame_sort_key):
        left = reference_map.get(frame)
        right = candidate_map.get(frame)
        if left != right:
            first = {"category": "export_fresh_ik_state", "frame": frame, "baked": left, "fresh": right}
            break
    observed = any(row["states"] for row in reference) and any(row["states"] for row in candidate)
    if not observed and first is None:
        first = {"category": "export_fresh_ik_state_missing", "baked": reference, "fresh": candidate}
    return {
        "baked": reference,
        "fresh": candidate,
        "observed": observed,
        "firstMismatch": first,
        "pass": observed and first is None,
    }


def _frame_value(value: Any) -> int | float:
    """Return an integer frame when exact, otherwise preserve a half-frame."""

    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def _frame_sort_key(value: Any) -> float:
    return float(value)


def _normalized_vmd(vmd: Any) -> dict[str, Any]:
    """Normalize exported VMD payloads for key-time/interpolation/IK gates."""

    bone_rows = []
    for frame in getattr(vmd, "bone_frames", []) or []:
        bone_rows.append(
            {
                "boneName": str(frame.bone_name),
                "frame": _frame_value(frame.frame_number),
                "interpolation": list(bytes(getattr(frame, "interpolation", b""))),
            }
        )
    bone_rows.sort(key=lambda row: (row["boneName"], _frame_sort_key(row["frame"])))
    ik_rows = []
    for frame in getattr(vmd, "ik_show_hide_frames", []) or []:
        states = sorted(
            (str(name), bool(enabled))
            for name, enabled in (getattr(frame, "ik_states", ()) or ())
        )
        ik_rows.append(
            {
                "frame": _frame_value(frame.frame_number),
                "visible": bool(getattr(frame, "visible", True)),
                "states": states,
            }
        )
    ik_rows.sort(key=lambda row: _frame_sort_key(row["frame"]))
    return {
        "boneKeyTimes": [(row["boneName"], row["frame"]) for row in bone_rows],
        "boneInterpolation": bone_rows,
        "ikProperties": ik_rows,
    }


def _compare_vmd_roundtrip(exported: Any, fresh: Any) -> dict[str, Any]:
    """Compare normalized VMD key times, Bezier bytes, and IK properties."""

    left = _normalized_vmd(exported)
    right = _normalized_vmd(fresh)
    key_times_pass = left["boneKeyTimes"] == right["boneKeyTimes"]
    interpolation_mismatches = []
    left_interpolation = {
        (row["boneName"], row["frame"]): row["interpolation"]
        for row in left["boneInterpolation"]
    }
    right_interpolation = {
        (row["boneName"], row["frame"]): row["interpolation"]
        for row in right["boneInterpolation"]
    }
    for key in sorted(
        set(left_interpolation) | set(right_interpolation),
        key=lambda item: (str(item[0]), _frame_sort_key(item[1])),
    ):
        if left_interpolation.get(key) != right_interpolation.get(key):
            interpolation_mismatches.append(
                {
                    "boneName": key[0],
                    "frame": key[1],
                    "exported": left_interpolation.get(key),
                    "fresh": right_interpolation.get(key),
                }
            )
    ik_pass = left["ikProperties"] == right["ikProperties"]
    first_divergence = None
    if not key_times_pass:
        first_divergence = {
            "category": "export_fresh_key_times",
            "exported": left["boneKeyTimes"],
            "fresh": right["boneKeyTimes"],
        }
    elif interpolation_mismatches:
        first_divergence = {
            "category": "export_fresh_bone_interpolation",
            **interpolation_mismatches[0],
        }
    elif not ik_pass:
        first_divergence = {
            "category": "export_fresh_ik_properties",
            "exported": left["ikProperties"],
            "fresh": right["ikProperties"],
        }
    return {
        "keyTimes": {
            "exported": left["boneKeyTimes"],
            "fresh": right["boneKeyTimes"],
            "pass": key_times_pass,
        },
        "boneInterpolation": {
            "mismatchCount": len(interpolation_mismatches),
            "firstMismatch": interpolation_mismatches[0] if interpolation_mismatches else None,
            "pass": not interpolation_mismatches,
        },
        "ikProperties": {
            "exported": left["ikProperties"],
            "fresh": right["ikProperties"],
            "pass": ik_pass,
        },
        "firstDivergence": first_divergence,
        "pass": key_times_pass and not interpolation_mismatches and ik_pass,
    }


def _evaluation_evidence(
    frames: list[int | float], records: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
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


def _run_route(
    model: Path, motion: Path, create_control_rig: bool, frames: list[int | float]
) -> dict[str, Any]:
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
    for frame in sorted(set(ref_frames) | set(candidate_frames), key=_frame_sort_key):
        if frame not in ref_frames or frame not in candidate_frames:
            divergences.append({"category": "frame_set", "frame": _frame_value(frame)})
            continue
        ref_joints = ref_frames[frame]
        candidate_joints = candidate_frames[frame]
        for index in sorted(set(ref_joints) | set(candidate_joints), key=lambda value: int(value)):
            if index not in ref_joints or index not in candidate_joints:
                divergences.append({"category": "joint_set", "frame": _frame_value(frame), "jointIndex": index})
                continue
            world_error = _matrix_error(ref_joints[index]["worldMatrix"], candidate_joints[index]["worldMatrix"])
            ref_skin = ref_joints[index]["skinMatrices"]
            candidate_skin = candidate_joints[index]["skinMatrices"]
            if len(ref_skin) != len(candidate_skin):
                divergences.append(
                    {
                        "category": "skin_matrix_count",
                        "frame": _frame_value(frame),
                        "jointIndex": index,
                        "legacyCount": len(ref_skin),
                        "directCount": len(candidate_skin),
                    }
                )
            skin_error = max(
                (_matrix_error(left, right) for left, right in zip(ref_skin, candidate_skin)),
                default=0.0,
            )
            row = {
                "frame": _frame_value(frame),
                "jointIndex": index,
                "boneName": str(ref_joints[index].get("boneName") or candidate_joints[index].get("boneName") or ""),
                "worldMatrixMax": world_error,
                "skinMatrixMax": skin_error,
            }
            rows.append(row)
            if max(world_error, skin_error) > _MATRIX_EPSILON:
                divergences.append({"category": "jo_aware_matrix", **row})
    worst_world = max((row["worldMatrixMax"] for row in rows), default=0.0)
    worst_skin = max((row["skinMatrixMax"] for row in rows), default=0.0)
    worst_world_row = max(rows, key=lambda row: row["worldMatrixMax"], default=None)
    worst_skin_row = max(rows, key=lambda row: row["skinMatrixMax"], default=None)
    return {
        "threshold": _MATRIX_EPSILON,
        "maxWorldMatrixError": worst_world,
        "maxSkinMatrixError": worst_skin,
        "worstWorld": worst_world_row,
        "worstSkin": worst_skin_row,
        "firstDivergence": divergences[0] if divergences else None,
        "divergenceCount": len(divergences),
        "pass": not divergences and worst_world <= _MATRIX_EPSILON and worst_skin <= _MATRIX_EPSILON,
    }


def _run_export_fresh_import(
    model: Path,
    baked: Mapping[str, Any],
    frames: list[int | float],
    interpolation_frames: list[int | float],
    output: Path,
) -> dict[str, Any]:
    """Export baked Control Rig motion and compare a fresh ordinary import."""

    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
    from mmd_tools.io.mmd_importer import import_mmd_file
    from mmd_tools.io.vmd_exporter import VmdExporter

    export_path = output.with_name(f"{output.stem}_baked_export.vmd")
    fresh_export_path = output.with_name(f"{output.stem}_fresh_reexport.vmd")
    gate: dict[str, Any] = {
        "attempted": True,
        "status": "fail",
        "pass": False,
        "exportPath": str(export_path),
        "freshExportPath": str(fresh_export_path),
        "errors": [],
    }
    try:
        _remove_stale_artifacts([export_path, fresh_export_path])
        baked_ik_states = _ik_state_inventory(str(baked["root"]), frames)
        collected = VmdSceneCollector().collect({"target_model": str(baked["root"])})
        VmdExporter().export_vmd_animation(str(export_path), collected)
        if not export_path.is_file():
            raise RuntimeError(f"baked VMD export did not produce a file: {export_path}")
        exported_vmd = VmdData().parse_file(str(export_path))
        if not exported_vmd.bone_frames:
            raise RuntimeError("baked VMD export contains no bone frames")

        cmds.file(new=True, force=True)
        fresh_root = _import_model(model)
        imported = import_mmd_file(
            str(export_path),
            options={
                "target_model": str(fresh_root),
                "pmx_path": str(model),
                "bake_mode": False,
                "clear_existing_motion": True,
                "create_mmd_control_rig": False,
            },
        )
        if not imported:
            raise RuntimeError("fresh ordinary VMD import returned false")
        fresh_records = _joint_skin_records(str(fresh_root))
        if not fresh_records:
            raise RuntimeError("fresh ordinary import has no indexed joints")
        fresh_observables = _capture_observables(fresh_records, frames)
        mesh_compare = _compare(
            {"observables": baked["observables"]},
            {"observables": fresh_observables},
        )
        interpolation_keys = [str(_frame_value(frame)) for frame in interpolation_frames]
        interpolation_compare = _compare(
            {
                "observables": {
                    key: baked["observables"][key]
                    for key in interpolation_keys
                    if key in baked["observables"]
                }
            },
            {
                "observables": {
                    key: fresh_observables[key]
                    for key in interpolation_keys
                    if key in fresh_observables
                }
            },
        )
        mesh_compare["interpolationProbe"] = {
            "frames": [_frame_value(frame) for frame in interpolation_frames],
            "tested": bool(interpolation_frames)
            and len(interpolation_compare.get("worstWorld") or {}) > 0,
            "metric": "JO-aware world/skin matrix max abs error",
            "threshold": _MATRIX_EPSILON,
            "comparison": interpolation_compare,
        }
        fresh_ik_states = _ik_state_inventory(str(fresh_root), frames)
        ik_state_compare = _compare_ik_state_inventory(baked_ik_states, fresh_ik_states)
        fresh_bone_key_times = _fresh_bone_key_times(str(fresh_root))
        keyframe_compare = _compare_fresh_bone_key_times(exported_vmd, fresh_bone_key_times)

        # Re-export the fresh ordinary scene so interpolation bytes and IK
        # property/state are compared as data, not inferred from Maya curves.
        fresh_collected = VmdSceneCollector().collect({"target_model": str(fresh_root)})
        VmdExporter().export_vmd_animation(str(fresh_export_path), fresh_collected)
        if not fresh_export_path.is_file():
            raise RuntimeError(f"fresh VMD re-export did not produce a file: {fresh_export_path}")
        fresh_vmd = VmdData().parse_file(str(fresh_export_path))
        data_compare = _compare_vmd_roundtrip(exported_vmd, fresh_vmd)
        gate.update(
            {
                "freshRoot": str(fresh_root),
                "exportedBoneFrames": len(exported_vmd.bone_frames),
                "freshExportedBoneFrames": len(fresh_vmd.bone_frames),
                "freshKeyframes": _keyframe_inventory(str(fresh_root)),
                "freshBoneKeyTimes": fresh_bone_key_times,
                "keyframeParity": keyframe_compare,
                "ikStateParity": ik_state_compare,
                "meshParity": mesh_compare,
                "dataParity": data_compare,
                "firstDivergence": (
                    mesh_compare.get("firstDivergence")
                    or keyframe_compare.get("firstMismatch")
                    or ik_state_compare.get("firstMismatch")
                    or data_compare.get("firstDivergence")
                ),
            }
        )
        gate["pass"] = (
            bool(mesh_compare.get("pass"))
            and bool(keyframe_compare.get("pass"))
            and bool(ik_state_compare.get("pass"))
            and bool(data_compare.get("pass"))
        )
        gate["status"] = "pass" if gate["pass"] else "fail"
    except Exception as exc:  # noqa: BLE001 - preserve first round-trip red evidence
        gate["errors"].append(_compact_error(exc))
        gate["firstDivergence"] = {"category": "export_fresh_import", "error": gate["errors"][0]}
    return gate


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
        authored_frame_set = set(authored_frames)
        authored_intervals = [
            (right - left, left, right)
            for left, right in zip(authored_frames, authored_frames[1:])
            if left < right
        ]
        interpolation_frames: list[int | float] = []
        if authored_intervals:
            _, left, right = max(authored_intervals, key=lambda row: (row[0], -row[1]))
            midpoint = (left + right) / 2.0
            if midpoint not in authored_frame_set:
                interpolation_frames.append(_frame_value(midpoint))
        frames = sorted({0, 1, end, *authored_frames, *interpolation_frames})
        coverage = _coverage(vmd)
        for name in ("externalOracle", "exportFreshImport", "evaluationModes"):
            coverage["items"][name] = {"status": "missing"}
        coverage_missing_set = (
            set(coverage["coverageMissing"])
            | {"externalOracle", "exportFreshImport", "evaluationModes"}
        )
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
        export_fresh_import = {"attempted": False, "status": "not_run", "pass": False}
        if baked and legacy.get("importStatus") == "pass":
            export_fresh_import = _run_export_fresh_import(
                model,
                {**baked, "root": direct["root"]},
                frames,
                interpolation_frames,
                output,
            )
            coverage["items"]["exportFreshImport"] = {
                "status": "covered",
                "gatePass": bool(export_fresh_import.get("pass")),
            }
            # Executed red is a gate failure, not missing coverage.  The
            # remaining five categories stay fail-closed below.
            coverage_missing_set.discard("exportFreshImport")
        coverage_missing = sorted(coverage_missing_set)
        coverage["coverageMissing"] = coverage_missing
        payload.update(
            {
                "mayaVersion": str(cmds.about(version=True)),
                "plugins": plugins,
                "evaluationMode": evaluation,
                "frames": frames,
                "authoredBoneKeyFrames": authored_frames,
                "interpolationFrames": interpolation_frames,
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
                "exportFreshImport": export_fresh_import,
            }
        )
        payload["status"] = (
            "pass"
            if payload["routeParity"]["pass"]
            and bool(export_fresh_import.get("pass"))
            and not coverage_missing
            else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - fail closed in report
        payload["error"] = str(exc)
        payload["status"] = "blocked" if isinstance(exc, (FileNotFoundError, RuntimeError)) else "error"
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "report": str(output),
                    "firstDivergence": payload.get("routeParity", {})
                    .get("directVsLegacy", {})
                    .get("firstDivergence"),
                    "exportFreshImport": {
                        "status": payload.get("exportFreshImport", {}).get("status"),
                        "firstDivergence": payload.get("exportFreshImport", {}).get("firstDivergence"),
                    },
                },
                ensure_ascii=False,
            )
        )
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
