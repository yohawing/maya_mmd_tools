"""Maya standalone A-to-B VMD clear-existing-motion reliability probe.

The probe deliberately exercises one route in one fresh scene.  Motion A is
authored on two separately namespaced models, while B is a real binary VMD
copy with one bone track, one morph track, and one IK state removed.  B is
then imported only on the target model with ``clear_existing_motion=True``.
The sibling matrix module launches this probe for every Maya/evaluation/route
combination.

Usage (inside ``mayapy``)::

    mayapy -m tests.viewport.vmd_clear_existing_motion_e2e \
        --maya 2024 --evaluation-mode dg --route legacy --out report.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "tests" / "data" / "yw_test_model_control_rig_bone_morph.pmx"
DEFAULT_MOTION = ROOT / "tests" / "data" / "yw_test_model_control_rig_bone_morph.vmd"
ROUTES = ("legacy", "animation_layer", "control_rig", "bake")
MODES = ("dg", "serial", "parallel")
MAYA_MODES = {"dg": "off", "serial": "serial", "parallel": "parallel"}
_BONE_ATTRIBUTES = (
    "translateX",
    "translateY",
    "translateZ",
    "rotateX",
    "rotateY",
    "rotateZ",
)


class BlockedProbe(RuntimeError):
    """The host cannot provide a required route witness."""


def _frame(value: Any) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _frames(values: Iterable[Any]) -> list[int | float]:
    return sorted({_frame(value) for value in values}, key=float)


def _read_vmd(path: Path):
    from mmd_tools.core.vmd_data import VmdData

    return VmdData().parse_file(str(path))


def _make_motion_b(source_path: Path, output_path: Path) -> tuple[Path, dict[str, Any]]:
    """Write a valid VMD B while retaining all optional binary sections."""

    source = _read_vmd(source_path)
    bone_names = sorted({str(frame.bone_name) for frame in source.bone_frames})
    morph_names = sorted({str(frame.morph_name) for frame in source.morph_frames})
    ik_names = sorted(
        {
            str(name)
            for frame in source.ik_show_hide_frames
            for name, _flag in (frame.ik_states or ())
        }
    )
    if not bone_names or not morph_names or not ik_names:
        raise BlockedProbe(
            "A fixture must contain bone, morph, and IK tracks to build a real B"
        )
    removed = {
        "bone": bone_names[0],
        "morph": morph_names[0],
        "ik": ik_names[0],
    }
    ik_candidates = sorted(
        int(frame.frame_number)
        for frame in source.ik_show_hide_frames
        if any(str(name) == removed["ik"] for name, _flag in (frame.ik_states or ()))
        and int(frame.frame_number) > 0
    )
    if not ik_candidates:
        raise BlockedProbe("A fixture must contain a non-zero IK state frame")
    removed_ik_frame = ik_candidates[0]
    variant = copy.deepcopy(source)
    variant.bone_frames = [
        frame for frame in variant.bone_frames if str(frame.bone_name) != removed["bone"]
    ]
    variant.morph_frames = [
        frame for frame in variant.morph_frames if str(frame.morph_name) != removed["morph"]
    ]
    variant.ik_show_hide_frames = []
    removed_ik = False
    for frame in source.ik_show_hide_frames:
        copied = copy.deepcopy(frame)
        if int(copied.frame_number) == removed_ik_frame and not removed_ik:
            copied.ik_states = [
                (name, flag)
                for name, flag in copied.ik_states
                if str(name) != removed["ik"]
            ]
            removed_ik = True
        copied.ik_count = len(copied.ik_states)
        variant.ik_show_hide_frames.append(copied)
    if not removed_ik:
        raise RuntimeError("VMD B did not remove the selected IK state")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    variant.write_file(str(output_path))
    roundtrip = _read_vmd(output_path)
    if removed["bone"] in {str(frame.bone_name) for frame in roundtrip.bone_frames}:
        raise RuntimeError("VMD B still contains the removed bone track")
    if removed["morph"] in {str(frame.morph_name) for frame in roundtrip.morph_frames}:
        raise RuntimeError("VMD B still contains the removed morph track")
    if any(
        int(frame.frame_number) == removed_ik_frame
        and any(str(name) == removed["ik"] for name, _flag in (frame.ik_states or ()))
        for frame in roundtrip.ik_show_hide_frames
    ):
        raise RuntimeError("VMD B still contains the removed IK state")
    return output_path, {
        "source": str(output_path),
        "removed": removed,
        "removedIkFrame": removed_ik_frame,
        "aCounts": {
            "boneFrames": len(source.bone_frames),
            "morphFrames": len(source.morph_frames),
            "ikFrames": len(source.ik_show_hide_frames),
        },
        "bCounts": {
            "boneFrames": len(roundtrip.bone_frames),
            "morphFrames": len(roundtrip.morph_frames),
            "ikFrames": len(roundtrip.ik_show_hide_frames),
        },
        "aTracks": {
            "bone": bone_names,
            "morph": morph_names,
            "ik": ik_names,
        },
        "bTracks": {
            "bone": sorted({str(frame.bone_name) for frame in roundtrip.bone_frames}),
            "morph": sorted({str(frame.morph_name) for frame in roundtrip.morph_frames}),
            "ik": sorted(
                {
                    str(name)
                    for frame in roundtrip.ik_show_hide_frames
                    for name, _flag in (frame.ik_states or ())
                }
            ),
        },
    }


def _resolve_cpp_plugin_path(maya_major: str) -> Path:
    override = os.environ.get(f"MMD_TOOLS_CPP_PLUGIN_{maya_major}") or os.environ.get(
        "MMD_TOOLS_CPP_PLUGIN"
    )
    if override:
        return Path(override).expanduser().resolve()
    debug = ROOT / "plug-ins" / maya_major / "Debug" / "mmd_tools_cpp.mll"
    if debug.is_file():
        return debug
    # Release binaries are checked in to some worktrees while Debug binaries
    # are produced on the developer machine.  Keep the same version-bound
    # path contract, with Release as a narrow local fallback.
    return ROOT / "plug-ins" / maya_major / "Release" / "mmd_tools_cpp.mll"


def _load_plugins(cmds) -> dict[str, Any]:
    maya_major = str(cmds.about(version=True)).split(".", 1)[0]
    cpp = _resolve_cpp_plugin_path(maya_major)
    if not cpp.is_file():
        raise BlockedProbe(f"required native plugin is missing: {cpp}")
    plugin_dir = str(cpp.parent)
    path_items = os.environ.get("PATH", "").split(os.pathsep)
    if plugin_dir not in path_items:
        os.environ["PATH"] = plugin_dir + os.pathsep + os.environ.get("PATH", "")
    if not cmds.pluginInfo(str(cpp), query=True, loaded=True):
        cmds.loadPlugin(str(cpp), quiet=True)
    python_plugin = ROOT / "mmd_tools" / "plugin_main.py"
    if not cmds.pluginInfo(python_plugin.stem, query=True, loaded=True):
        cmds.loadPlugin(str(python_plugin), quiet=True)
    return {
        "native": str(cpp),
        "python": str(python_plugin),
        "nativeLoaded": bool(cmds.pluginInfo(str(cpp), query=True, loaded=True)),
        "pythonLoaded": bool(cmds.pluginInfo(python_plugin.stem, query=True, loaded=True)),
    }


def _set_evaluation_mode(cmds, requested: str) -> dict[str, Any]:
    if requested not in MODES:
        raise ValueError(f"unsupported evaluation mode: {requested}")
    maya_requested = MAYA_MODES[requested]
    cmds.evaluationManager(mode=maya_requested)
    values = cmds.evaluationManager(query=True, mode=True) or []
    maya_mode = str(values[0]) if values else "unknown"
    active = "dg" if maya_mode == "off" else maya_mode
    result = {
        "requested": requested,
        "mayaRequested": maya_requested,
        "mayaMode": maya_mode,
        "active": active,
        "pass": active == requested,
    }
    if not result["pass"]:
        raise RuntimeError(f"evaluation mode readback mismatch: {result}")
    return result


def _import_model(cmds, model: Path, namespace: str, *, control_rig: bool) -> str:
    from mmd_tools.io.mmd_importer import import_mmd_file

    root = import_mmd_file(
        str(model),
        options={
            "use_namespace": True,
            "custom_namespace": namespace,
            "setup_rig": True,
            "setup_bone_orientation": True,
            "import_physics": False,
            "import_morphs": True,
            "create_mmd_shaders": False,
            "use_cpp_fast_load": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
            "create_mmd_control_rig": bool(control_rig),
        },
    )
    if not root:
        raise RuntimeError(f"PMX import returned no root: {model}")
    return str((cmds.ls(str(root), long=True) or [root])[0])


def _namespace(root: str) -> str:
    from mmd_tools.core.namespace_utils import NamespaceUtils

    return str(NamespaceUtils.get_namespace_from_node(root) or "")


def _import_motion(
    cmds,
    model: Path,
    motion: Path,
    root: str,
    route: str,
    *,
    clear: bool,
) -> dict[str, Any]:
    from mmd_tools.converters.vmd_converter import VmdConverter

    data = _read_vmd(motion)
    profile: dict[str, Any] = {}
    converter = VmdConverter()
    converter.use_animation_layers = route == "animation_layer"
    converter.use_quaternion_interpolation = route == "control_rig"
    layer_name = f"VMD_Clear_{_namespace(root) or 'model'}"
    success = converter.convert(
        data,
        target_namespace=_namespace(root),
        layer_name=layer_name,
        bake_mode=route == "bake",
        clear_existing_motion=bool(clear),
        vmd_bytes=motion.read_bytes(),
        pmx_path=str(model),
        profile=profile,
        target_model=root,
        create_mmd_control_rig=route == "control_rig",
    )
    if not success:
        raise RuntimeError(f"VMD import returned false for route={route}, root={root}")
    return {
        "profile": profile,
        "layer": layer_name,
        "route": route,
        "root": root,
    }


def _curve_record(cmds, curve: str) -> dict[str, Any]:
    destinations = sorted(
        str(value)
        for value in (cmds.listConnections(curve, source=False, destination=True, plugs=True) or [])
    )
    try:
        uuid_values = cmds.ls(curve, uuid=True) or []
        uuid = str(uuid_values[0]) if uuid_values else ""
    except Exception:
        uuid = ""
    return {
        "curve": str(curve),
        "uuid": uuid,
        "destinations": destinations,
        "times": _frames(cmds.keyframe(curve, query=True, timeChange=True) or []),
        "values": [float(value) for value in (cmds.keyframe(curve, query=True, valueChange=True) or [])],
    }


def _all_curves(cmds) -> dict[str, dict[str, Any]]:
    records = {}
    for curve in cmds.ls(type="animCurve", long=True) or []:
        record = _curve_record(cmds, str(curve))
        if record["uuid"]:
            records[record["uuid"]] = record
    return records


def _descendants(cmds, root: str) -> set[str]:
    return set(str(node) for node in (cmds.listRelatives(root, allDescendents=True, fullPath=True) or [])) | {
        str(root)
    }


def _nodes_with_attr(cmds, root: str, attr: str, value: str) -> set[str]:
    result = set()
    for node in _descendants(cmds, root):
        try:
            if cmds.attributeQuery(attr, node=node, exists=True) and str(cmds.getAttr(f"{node}.{attr}") or "") == value:
                result.add(node)
        except (RuntimeError, TypeError):
            continue
    return result


def _joint_by_bone_name(cmds, root: str, bone_name: str) -> set[str]:
    return _nodes_with_attr(cmds, root, "mmd_bone_name", bone_name)


def _route_destinations(cmds, root: str, category: str, name: str) -> set[str]:
    nodes = set()
    plugs = set()
    if category == "bone":
        joints = _joint_by_bone_name(cmds, root, name)
        nodes.update(joints)
        for joint in joints:
            plugs.update(f"{joint}.{attr}" for attr in _BONE_ATTRIBUTES)
        try:
            from mmd_tools.core.mmd_control_rig_motion import control_rig_edit_routes_for_joints

            for route in control_rig_edit_routes_for_joints(joints).values():
                plugs.update(f"{node}.{attr}" for node, attr in route.values())
        except Exception:
            pass
    elif category == "morph":
        nodes.update(_nodes_with_attr(cmds, root, "mmd_morph_name", name))
        try:
            from mmd_tools.converters.vmd_converter import VmdConverter

            mapping_converter = VmdConverter()
            mapping_converter._build_morph_mappings(target_model=root)
            for mapped_name, mapping in mapping_converter.morph_name_mapping.items():
                if str(mapped_name) != name:
                    continue
                for node, attribute, _original_name in mapping_converter._iter_morph_mappings(mapping):
                    plugs.add(f"{node}.{attribute}")
        except Exception:
            pass
        # Morph network nodes are DG-owned rather than DAG descendants.  The
        # persisted model registry and mmd_morph_index identify the exact
        # controller input that owns a named BoneMorph track.
        try:
            from mmd_tools.converters.vmd_morph_mapping import morph_node_is_owned_by_root

            for node in cmds.ls("*.mmd_morph_name", objectsOnly=True, long=True) or []:
                if not cmds.attributeQuery("mmd_morph_name", node=node, exists=True):
                    continue
                if str(cmds.getAttr(f"{node}.mmd_morph_name") or "") != name:
                    continue
                if not morph_node_is_owned_by_root(node, root):
                    continue
                nodes.add(str(node))
                if cmds.attributeQuery("mmd_morph_index", node=node, exists=True):
                    index = int(cmds.getAttr(f"{node}.mmd_morph_index"))
                    controllers = cmds.listConnections(
                        f"{root}.mmd_morph_controller", source=True, destination=False
                    ) or []
                    for controller in controllers:
                        plugs.add(f"{controller}.inputWeight[{index}]")
        except Exception:
            pass
        for node in nodes:
            for attr in ("weight", "input", "value"):
                plugs.add(f"{node}.{attr}")
    elif category == "ik":
        try:
            from mmd_tools.converters.vmd_ik_enabled_animation import collect_ik_nodes_by_bone_name

            solver = collect_ik_nodes_by_bone_name(target_model=root).get(name)
            if solver:
                plugs.add(f"{solver}.enabled")
        except Exception:
            pass
        try:
            from mmd_tools.core.mmd_control_rig_motion import (
                resolve_control_rig_direct_vmd_export_routes,
            )

            route = (
                resolve_control_rig_direct_vmd_export_routes(root)
                .get("ikStateRoutes", {})
                .get(name)
            )
            if route:
                node, attribute = route
                plugs.add(f"{node}.{attribute}")
        except Exception:
            pass
    if nodes:
        plugs.update(
            destination
            for record in _all_curves(cmds).values()
            for destination in record["destinations"]
            if destination.split(".", 1)[0] in nodes
        )
    return plugs


def _scope_curve_ids(root: str, cmds) -> set[str]:
    from mmd_tools.core.constants import ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON

    try:
        raw = cmds.getAttr(f"{root}.{ATTR_MMD_VMD_IMPORT_PROVENANCE_JSON}")
        payload = json.loads(raw) if raw else {}
        scope = payload.get("clear_scope", {}) if isinstance(payload, dict) else {}
        return {str(value) for value in scope.get("curve_uuids", []) if value}
    except (TypeError, ValueError, RuntimeError):
        return set()


def _marker_nodes(cmds, node: str) -> set[str]:
    return _descendants(cmds, node)


def _marker_snapshot(cmds, camera: str, light: str) -> dict[str, Any]:
    nodes = _marker_nodes(cmds, camera) | _marker_nodes(cmds, light)
    records = {}
    for uuid, record in _all_curves(cmds).items():
        if any(destination.split(".", 1)[0] in nodes for destination in record["destinations"]):
            records[uuid] = record
    return {
        "camera": str(camera),
        "light": str(light),
        "curves": records,
    }


def _create_sentinels(cmds) -> tuple[str, str]:
    from mmd_tools.converters.vmd_camera_animation import get_or_create_camera
    from mmd_tools.converters.light_converter import create_mmd_light_controller

    camera = str(get_or_create_camera())
    light = str(create_mmd_light_controller())
    cmds.setKeyframe(camera, attribute="mmd_camera_target_x", time=3, value=1.25)
    cmds.setKeyframe(camera, attribute="mmd_camera_target_x", time=13, value=-2.5)
    cmds.setKeyframe(light, attribute="mmd_light_colorR", time=5, value=0.25)
    cmds.setKeyframe(light, attribute="mmd_light_colorR", time=15, value=0.75)
    return camera, light


def _ik_state_values(vmd_data) -> dict[str, dict[int | float, bool]]:
    values: dict[str, dict[int | float, bool]] = {}
    for frame in vmd_data.ik_show_hide_frames:
        time = _frame(frame.frame_number)
        for name, enabled in frame.ik_states or ():
            values.setdefault(str(name), {})[time] = bool(enabled)
    return values


def _sample_control_rig_ik_values(
    cmds,
    root: str,
    sample_times: Mapping[str, Iterable[int | float]],
) -> dict[str, Any]:
    from mmd_tools.core.mmd_control_rig_motion import (
        resolve_control_rig_direct_vmd_export_routes,
    )

    routes = resolve_control_rig_direct_vmd_export_routes(root).get(
        "ikStateRoutes", {}
    )
    samples = {}
    missing = []
    for name, times in sorted(sample_times.items()):
        route = routes.get(name)
        if not route:
            missing.append(name)
            continue
        node, attribute = route
        plug = f"{node}.{attribute}"
        samples[name] = {
            "plug": plug,
            "values": {
                str(_frame(time)): bool(cmds.getAttr(plug, time=float(time)))
                for time in sorted(set(times), key=float)
            },
        }
    return {"samples": samples, "missing": missing}


def _sample_joint_world_matrices(
    cmds,
    root: str,
    bone_names: Iterable[str],
    sample_times: Iterable[int | float],
) -> dict[str, Any]:
    samples = {}
    missing = []
    times = sorted(set(sample_times), key=float)
    for name in sorted(set(str(value) for value in bone_names)):
        joints = sorted(_joint_by_bone_name(cmds, root, name))
        if not joints:
            missing.append(name)
            continue
        joint = joints[0]
        samples[name] = {
            "joint": joint,
            "matrices": {
                str(_frame(time)): [
                    float(value)
                    for value in cmds.getAttr(
                        f"{joint}.worldMatrix[0]", time=float(time)
                    )
                ]
                for time in times
            },
        }
    return {"samples": samples, "missing": missing}


def _compare_fresh_b_scene(
    *,
    target_joint_samples: Mapping[str, Any],
    fresh_joint_samples: Mapping[str, Any],
    target_ik_samples: Mapping[str, Any] | None = None,
    fresh_ik_samples: Mapping[str, Any] | None = None,
    expected_ik_values: Mapping[str, Mapping[int | float, bool]] | None = None,
) -> dict[str, Any]:
    matrix_rows = []
    joint_names = set(target_joint_samples.get("samples", {})) | set(
        fresh_joint_samples.get("samples", {})
    )
    fresh_variation = 0.0
    for name in sorted(joint_names):
        target = target_joint_samples.get("samples", {}).get(name, {})
        fresh = fresh_joint_samples.get("samples", {}).get(name, {})
        target_matrices = target.get("matrices", {})
        fresh_matrices = fresh.get("matrices", {})
        frames = set(target_matrices) | set(fresh_matrices)
        first_fresh = None
        for frame in sorted(frames, key=float):
            target_matrix = target_matrices.get(frame)
            fresh_matrix = fresh_matrices.get(frame)
            if target_matrix is None or fresh_matrix is None:
                delta = None
            else:
                delta = max(
                    abs(float(left) - float(right))
                    for left, right in zip(target_matrix, fresh_matrix)
                )
                if first_fresh is None:
                    first_fresh = fresh_matrix
                else:
                    fresh_variation = max(
                        fresh_variation,
                        max(
                            abs(float(left) - float(right))
                            for left, right in zip(first_fresh, fresh_matrix)
                        ),
                    )
            matrix_rows.append(
                {
                    "bone": name,
                    "frame": _frame(frame),
                    "maxAbsDelta": delta,
                    "pass": delta is not None and delta <= 1.0e-5,
                }
            )

    ik_rows = []
    if target_ik_samples is not None and fresh_ik_samples is not None:
        expected_ik_values = expected_ik_values or {}
        ik_names = set(target_ik_samples.get("samples", {})) | set(
            fresh_ik_samples.get("samples", {})
        )
        for name in sorted(ik_names):
            target_values = target_ik_samples.get("samples", {}).get(name, {}).get(
                "values", {}
            )
            fresh_values = fresh_ik_samples.get("samples", {}).get(name, {}).get(
                "values", {}
            )
            authored = {
                str(_frame(frame)): bool(value)
                for frame, value in expected_ik_values.get(name, {}).items()
            }
            for frame in sorted(set(target_values) | set(fresh_values), key=float):
                target_value = target_values.get(frame)
                fresh_value = fresh_values.get(frame)
                expected_value = authored.get(frame)
                matches_authored = expected_value is None or (
                    target_value == expected_value and fresh_value == expected_value
                )
                ik_rows.append(
                    {
                        "name": name,
                        "frame": _frame(frame),
                        "target": target_value,
                        "freshB": fresh_value,
                        "expectedAuthored": expected_value,
                        "pass": target_value == fresh_value and matches_authored,
                    }
                )

    missing = {
        "targetJoints": target_joint_samples.get("missing", []),
        "freshBJoints": fresh_joint_samples.get("missing", []),
        "targetIkRoutes": (
            target_ik_samples.get("missing", [])
            if target_ik_samples is not None
            else []
        ),
        "freshBIkRoutes": (
            fresh_ik_samples.get("missing", [])
            if fresh_ik_samples is not None
            else []
        ),
    }
    return {
        "matrixSamples": matrix_rows,
        "ikEnabledSamples": ik_rows,
        "freshBWorldMatrixVariation": fresh_variation,
        "missing": missing,
        "pass": (
            bool(matrix_rows)
            and all(row["pass"] for row in matrix_rows)
            and fresh_variation > 1.0e-6
            and all(not values for values in missing.values())
            and (target_ik_samples is None or bool(ik_rows))
            and all(row["pass"] for row in ik_rows)
        ),
    }


def _create_route_evidence(cmds, root: str, route: str, layer: str, profile: Mapping[str, Any]) -> dict[str, Any]:
    curves = _all_curves(cmds)
    target_scope = _scope_curve_ids(root, cmds)
    descendants = _descendants(cmds, root)
    joint_nodes = {
        node
        for node in descendants
        if cmds.nodeType(node) == "joint"
    }
    joint_curves = [
        record
        for record in curves.values()
        if any(destination.split(".", 1)[0] in joint_nodes for destination in record["destinations"])
    ]
    layer_attributes = []
    if cmds.objExists(layer):
        try:
            layer_attributes = [str(value) for value in (cmds.animLayer(layer, query=True, attribute=True) or [])]
        except RuntimeError:
            layer_attributes = []
    metadata = None
    try:
        from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata

        metadata = read_mmd_control_rig_metadata(root)
    except Exception:
        metadata = None
    runtime = profile.get("vmd_converter", {}).get("runtime_registration", {})
    if not isinstance(runtime, Mapping):
        runtime = {}
    actual = {
        "layerExists": bool(cmds.objExists(layer)),
        "layerAttributeCount": len(layer_attributes),
        "jointCurveCount": len(joint_curves),
        "controlRigOwner": metadata.get("owner") if isinstance(metadata, Mapping) else None,
        "controlRigState": metadata.get("state") if isinstance(metadata, Mapping) else None,
        "runtimeRegistrationStatus": runtime.get("status"),
        "targetCurveCount": len(target_scope),
        "runtimeEvaluationMode": runtime.get("evaluation_mode"),
    }
    if route == "legacy":
        passed = not actual["layerExists"] and actual["targetCurveCount"] > 0 and actual["runtimeEvaluationMode"] not in {"batch", "frame"}
    elif route == "animation_layer":
        passed = actual["layerExists"] and actual["layerAttributeCount"] > 0
    elif route == "control_rig":
        passed = actual["controlRigOwner"] == "CONTROL_OWNED" and actual["targetCurveCount"] > 0
    else:
        passed = actual["runtimeRegistrationStatus"] == "success"
    return {"expected": route, "actual": actual, "pass": bool(passed)}


def _compare_track_times(
    cmds,
    records: Mapping[str, Mapping[str, Any]],
    root: str,
    variant: Mapping[str, Any],
    *,
    layer: str = "",
) -> list[dict[str, Any]]:
    results = []
    source = _read_vmd(Path(str(variant["source"])))
    expected_tracks = {
        "bone": {},
        "morph": {},
        "ik": {},
    }
    for frame in source.bone_frames:
        expected_tracks["bone"].setdefault(str(frame.bone_name), set()).add(_frame(frame.frame_number))
    for frame in source.morph_frames:
        expected_tracks["morph"].setdefault(str(frame.morph_name), set()).add(_frame(frame.frame_number))
    for frame in source.ik_show_hide_frames:
        for name, _flag in frame.ik_states:
            expected_tracks["ik"].setdefault(str(name), set()).add(_frame(frame.frame_number))
    bake_route = variant.get("route") == "bake"
    for category, tracks in expected_tracks.items():
        for name, expected in sorted(tracks.items()):
            plugs = _route_destinations(cmds, root, category, name)
            actual = {
                time
                for record in records.values()
                for destination in record["destinations"]
                if destination in plugs
                for time in record["times"]
            }
            if not actual:
                for plug in sorted(plugs):
                    try:
                        actual.update(_frames(cmds.keyframe(plug, query=True, timeChange=True) or []))
                    except RuntimeError:
                        continue
            # Maya animation-layer curves terminate at an animBlend input,
            # not at the visible target plug.  Read the layered plug as a
            # fallback so this check remains about authored frame times.
            if not actual and layer and cmds.objExists(layer):
                try:
                    attributes = cmds.animLayer(layer, query=True, attribute=True) or []
                except RuntimeError:
                    attributes = []
                for attribute in attributes:
                    node = str(attribute).split(".", 1)[0]
                    if category == "bone" and node in _nodes_with_attr(cmds, root, "mmd_bone_name", name):
                        actual.update(_frames(cmds.keyframe(attribute, query=True, timeChange=True) or []))
                    elif category == "morph" and node in _nodes_with_attr(cmds, root, "mmd_morph_name", name):
                        actual.update(_frames(cmds.keyframe(attribute, query=True, timeChange=True) or []))
            passed = bool(expected) and expected.issubset(actual)
            if not bake_route:
                passed = passed and actual == expected
            results.append(
                {
                    "kind": category,
                    "name": name,
                    "expected": sorted(expected, key=float),
                    "actual": sorted(actual, key=float),
                    "comparison": "contains_expected_authored_times" if bake_route else "exact",
                    "pass": bool(passed),
                }
            )
    return results


def _removed_track_check(
    cmds,
    root: str,
    records_before: Mapping[str, Mapping[str, Any]],
    records_after: Mapping[str, Mapping[str, Any]],
    removed: Mapping[str, str],
    known_curve_uuids: set[str],
    before_track_curve_uuids: Mapping[str, set[str]],
    removed_ik_frame: int,
) -> dict[str, Any]:
    rows = []
    for category in ("bone", "morph", "ik"):
        plugs = _route_destinations(cmds, root, category, str(removed[category]))
        before = sorted(
            set(before_track_curve_uuids.get(category, set()))
            & set(known_curve_uuids)
        )
        after = [
            uuid
            for uuid, record in records_after.items()
            if any(destination in plugs for destination in record["destinations"])
            and record["times"]
        ]
        try:
            after = sorted(
                set(after)
                | _curve_uuids_for_plugs(cmds, plugs, require_keys=True)
            )
        except RuntimeError:
            pass
        after_key_times = set()
        for plug in plugs:
            try:
                after_key_times.update(_frames(cmds.keyframe(plug, query=True, timeChange=True) or []))
            except RuntimeError:
                continue
        if not after:
            for plug in plugs:
                try:
                    if cmds.keyframe(plug, query=True, timeChange=True):
                        after.append(plug)
                except RuntimeError:
                    continue
        rows.append(
            {
                "kind": category,
                "name": str(removed[category]),
                "candidatePlugs": sorted(plugs),
                "beforeCurveUuids": sorted(before),
                "afterCurveUuidsWithKeys": sorted(after),
                "afterKeyTimes": sorted(after_key_times, key=float),
                "pass": (
                    bool(before)
                    and (removed_ik_frame not in after_key_times if category == "ik" else not after)
                ),
            }
        )
    return {"tracks": rows, "pass": all(bool(row["pass"]) for row in rows)}


def _curve_uuids_for_plugs(cmds, plugs: Iterable[str], *, require_keys: bool = False) -> set[str]:
    """Resolve animCurve identities through Maya's evaluated keyframe query."""

    uuids = set()
    for plug in plugs:
        try:
            curves = cmds.keyframe(plug, query=True, name=True) or []
        except RuntimeError:
            curves = []
        for curve in curves:
            if require_keys:
                try:
                    if not cmds.keyframe(curve, query=True, timeChange=True):
                        continue
                except RuntimeError:
                    continue
            try:
                values = cmds.ls(curve, uuid=True) or []
            except RuntimeError:
                values = []
            if values:
                uuids.add(str(values[0]))
    return uuids


def _same_marker_snapshot(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_curves = left.get("curves", {})
    right_curves = right.get("curves", {})
    same_ids = set(left_curves) == set(right_curves)
    mismatches = []
    for uuid in sorted(set(left_curves) & set(right_curves)):
        for key in ("destinations", "times", "values"):
            if left_curves[uuid].get(key) != right_curves[uuid].get(key):
                mismatches.append({"uuid": uuid, "field": key})
    return {
        "sameCurveUuids": same_ids,
        "mismatches": mismatches,
        "pass": same_ids and not mismatches,
    }


def _runtime_provenance() -> dict[str, Any]:
    result = {"status": "not_run", "runtimePath": None, "runtimeSha256": None, "runtimeAbi": None}
    try:
        from mmd_tools.core.native import mmd_anim_runtime

        library = mmd_anim_runtime.get_mmd_runtime_library()
        if library is None:
            return result
        path = mmd_anim_runtime.get_runtime_library_path()
        if path is None:
            raw = getattr(library, "_name", None)
            path = Path(str(raw)) if raw else None
        if path is None or not Path(path).is_file():
            return result
        path = Path(path).resolve()
        result["runtimePath"] = str(path)
        result["runtimeSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        result["runtimeAbi"] = int(library.mmd_runtime_abi_version())
        result["status"] = "ready"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def run(
    *,
    model: Path,
    motion: Path,
    output: Path,
    maya_version: str,
    evaluation_mode: str,
    route: str,
) -> int:
    payload: dict[str, Any] = {
        "kind": "vmd-clear-existing-motion-e2e",
        "schema": 1,
        "status": "error",
        "requested": {
            "maya": str(maya_version),
            "evaluationMode": str(evaluation_mode),
            "route": str(route),
        },
        "model": str(model),
        "motion": str(motion),
    }
    try:
        import maya.cmds as cmds

        payload["mayaVersion"] = str(cmds.about(version=True))
        if payload["mayaVersion"].split(".", 1)[0] != str(maya_version):
            raise BlockedProbe(f"Maya version mismatch: requested={maya_version} actual={payload['mayaVersion']}")
        payload["plugins"] = _load_plugins(cmds)
        payload["evaluationMode"] = _set_evaluation_mode(cmds, evaluation_mode)
        if route not in ROUTES:
            raise ValueError(f"unsupported route: {route}")
        if not model.is_file() or not motion.is_file():
            raise BlockedProbe(f"fixture missing: model={model} motion={motion}")
        if route == "bake":
            provenance = _runtime_provenance()
            payload["runtimeProvenance"] = provenance
            if provenance.get("status") != "ready":
                raise BlockedProbe("Bake route requires an available mmd-anim runtime")
        b_path = output.with_name(f"{output.stem}_{route}_B.vmd")
        _b_path, variant = _make_motion_b(motion, b_path)
        variant["route"] = route
        variant["source"] = str(b_path)
        payload["variant"] = variant

        cmds.file(new=True, force=True)
        camera, light = _create_sentinels(cmds)
        control_rig = route == "control_rig"
        foreign_root = _import_model(cmds, model, "clear_foreign", control_rig=control_rig)
        target_root = _import_model(cmds, model, "clear_target", control_rig=control_rig)
        foreign_namespace = _namespace(foreign_root)
        target_namespace = _namespace(target_root)
        if not foreign_namespace or not target_namespace or foreign_namespace == target_namespace:
            raise RuntimeError(
                f"models did not receive separate namespaces: foreign={foreign_namespace!r} target={target_namespace!r}"
            )

        # Import A into both models.  This gives the target a real previous
        # motion graph and makes the foreign model an isolation witness.
        foreign_a = _import_motion(cmds, model, motion, foreign_root, route, clear=True)
        target_a = _import_motion(cmds, model, motion, target_root, route, clear=True)
        target_scope_before = _scope_curve_ids(target_root, cmds)
        foreign_scope_before = _scope_curve_ids(foreign_root, cmds)
        curves_before = _all_curves(cmds)
        foreign_curves_before = {
            uuid: record for uuid, record in curves_before.items() if uuid in foreign_scope_before
        }
        removed_track_curve_uuids = {
            category: (
                _curve_uuids_for_plugs(
                    cmds,
                    _route_destinations(cmds, target_root, category, str(variant["removed"][category])),
                )
                & target_scope_before
            )
            for category in ("bone", "morph", "ik")
        }
        current_time_before = 37.0
        cmds.currentTime(current_time_before, edit=True)
        marker_before = _marker_snapshot(cmds, camera, light)

        target_b = _import_motion(cmds, model, b_path, target_root, route, clear=True)
        curves_after = _all_curves(cmds)
        marker_after = _marker_snapshot(cmds, camera, light)
        foreign_curves_after = {
            uuid: record for uuid, record in curves_after.items() if uuid in foreign_scope_before
        }

        removed_tracks = _removed_track_check(
            cmds,
            target_root,
            curves_before,
            curves_after,
            variant["removed"],
            target_scope_before,
            removed_track_curve_uuids,
            int(variant["removedIkFrame"]),
        )
        retained_track_times = _compare_track_times(
            cmds,
            curves_after,
            target_root,
            variant,
            layer=target_b["layer"],
        )
        if route == "bake":
            runtime_registration = (
                target_b["profile"].get("vmd_converter", {}).get(
                    "runtime_registration", {}
                )
            )
            raw_ik_frames = runtime_registration.get("raw_ik_frames", []) or []
            runtime_ik_times = {}
            for frame in raw_ik_frames:
                for name, _flag in frame.get("ik_states", []) or []:
                    runtime_ik_times.setdefault(str(name), set()).add(
                        _frame(frame.get("frame_number", 0))
                    )
            for row in retained_track_times:
                if row["kind"] != "ik":
                    continue
                actual = runtime_ik_times.get(str(row["name"]), set())
                expected = set(row["expected"])
                row.update(
                    {
                        "actual": sorted(actual, key=float),
                        "comparison": "runtime_registered_raw_ik_frames",
                        "pass": bool(expected) and actual == expected,
                    }
                )
            for row in removed_tracks["tracks"]:
                if row["kind"] == "ik":
                    still_present = any(
                        _frame(frame.get("frame_number", 0))
                        == _frame(variant["removedIkFrame"])
                        and any(
                            str(name) == str(row["name"])
                            for name, _flag in frame.get("ik_states", []) or []
                        )
                        for frame in raw_ik_frames
                    )
                    row.update(
                        {
                            "comparison": "runtime_registered_raw_ik_frame_removed",
                            "pass": not still_present,
                        }
                    )
                elif row["kind"] == "morph":
                    plugs = set(row["candidatePlugs"])
                    values = [
                        float(value)
                        for record in curves_after.values()
                        if any(destination in plugs for destination in record["destinations"])
                        for value in record["values"]
                    ]
                    if not values:
                        for plug in sorted(plugs):
                            try:
                                values.extend(
                                    float(value)
                                    for value in (
                                        cmds.keyframe(
                                            plug,
                                            query=True,
                                            valueChange=True,
                                        )
                                        or []
                                    )
                                )
                            except RuntimeError:
                                continue
                    row.update(
                        {
                            "afterValues": values,
                            "comparison": "runtime_dense_rest_pose",
                            "pass": bool(row["beforeCurveUuids"])
                            and bool(values)
                            and all(abs(value) <= 1.0e-8 for value in values),
                        }
                    )
            removed_tracks["pass"] = all(
                bool(row["pass"]) for row in removed_tracks["tracks"]
            )
        motion_clear = target_b["profile"].get("motion_clear")
        profile_fields = {
            "present": isinstance(motion_clear, Mapping),
            "requested": isinstance(motion_clear, Mapping) and isinstance(motion_clear.get("requested"), Mapping),
            "effective": isinstance(motion_clear, Mapping) and isinstance(motion_clear.get("effective"), Mapping),
            "before": isinstance(motion_clear, Mapping) and isinstance(motion_clear.get("before"), Mapping),
            "after": isinstance(motion_clear, Mapping) and isinstance(motion_clear.get("after"), Mapping),
            "status": motion_clear.get("status") if isinstance(motion_clear, Mapping) else None,
        }
        current_time_after = float(cmds.currentTime(query=True))
        current_time_check = {
            "before": current_time_before,
            "after": current_time_after,
            "pass": abs(current_time_after - current_time_before) <= 1.0e-6,
        }
        foreign_check = _same_curve_records(foreign_curves_before, foreign_curves_after)
        route_evidence = _create_route_evidence(cmds, target_root, route, target_b["layer"], target_b["profile"])
        target_scope_after = _scope_curve_ids(target_root, cmds)
        fresh_b_scene = {"applicable": False, "pass": True}
        fresh_root = None
        fresh_b = None
        if route in {"control_rig", "bake"}:
            source_b = _read_vmd(b_path)
            scene_sample_times = {
                _frame(frame.frame_number) for frame in source_b.bone_frames
            } | {
                _frame(frame.frame_number)
                for frame in source_b.ik_show_hide_frames
            } | {_frame(variant["removedIkFrame"])}
            target_joint_samples = _sample_joint_world_matrices(
                cmds,
                target_root,
                variant["aTracks"]["bone"],
                scene_sample_times,
            )
            expected_ik_values = _ik_state_values(source_b)
            ik_sample_times = {
                name: set(values) for name, values in expected_ik_values.items()
            }
            ik_sample_times.setdefault(str(variant["removed"]["ik"]), set()).add(
                _frame(variant["removedIkFrame"])
            )
            target_ik_samples = (
                _sample_control_rig_ik_values(cmds, target_root, ik_sample_times)
                if route == "control_rig"
                else None
            )

            # Rebuild B in a genuinely fresh scene without the clear path.
            # This is an independent scene-result oracle for A -> clear -> B.
            cmds.file(new=True, force=True)
            fresh_root = _import_model(
                cmds, model, "clear_fresh_b", control_rig=control_rig
            )
            fresh_b = _import_motion(
                cmds, model, b_path, fresh_root, route, clear=False
            )
            fresh_joint_samples = _sample_joint_world_matrices(
                cmds,
                fresh_root,
                variant["aTracks"]["bone"],
                scene_sample_times,
            )
            fresh_ik_samples = (
                _sample_control_rig_ik_values(cmds, fresh_root, ik_sample_times)
                if route == "control_rig"
                else None
            )
            fresh_b_scene = {
                "applicable": True,
                **_compare_fresh_b_scene(
                    target_joint_samples=target_joint_samples,
                    fresh_joint_samples=fresh_joint_samples,
                    target_ik_samples=target_ik_samples,
                    fresh_ik_samples=fresh_ik_samples,
                    expected_ik_values=expected_ik_values,
                ),
            }
        checks = {
            "route": route_evidence,
            "motionClearProfile": {
                **profile_fields,
                "pass": all(
                    [
                        profile_fields["present"],
                        profile_fields["requested"],
                        profile_fields["effective"],
                        profile_fields["before"],
                        profile_fields["after"],
                        profile_fields["status"] == "success",
                    ]
                ),
            },
            "removedTracks": removed_tracks,
            "retainedTrackTimes": {
                "checks": retained_track_times,
                "pass": bool(retained_track_times) and all(row["pass"] for row in retained_track_times),
            },
            "foreignModelCurves": foreign_check,
            "cameraLightCurves": _same_marker_snapshot(marker_before, marker_after),
            "currentTime": current_time_check,
            "freshBSceneOracle": fresh_b_scene,
            "targetScope": {
                "beforeCurveCount": len(target_scope_before),
                "afterCurveCount": len(target_scope_after),
                "knownAOnlyCurveCount": sum(
                    len(row["beforeCurveUuids"]) for row in removed_tracks["tracks"]
                ),
                "pass": bool(target_scope_before)
                and bool(target_scope_after)
                and removed_tracks["pass"],
            },
        }
        payload.update(
            {
                "models": {
                    "foreign": foreign_root,
                    "target": target_root,
                    "freshB": fresh_root,
                },
                "sentinels": {"camera": camera, "light": light},
                "imports": {
                    "foreignA": {"route": foreign_a["route"], "layer": foreign_a["layer"]},
                    "targetA": {"route": target_a["route"], "layer": target_a["layer"]},
                    "targetB": {"route": target_b["route"], "layer": target_b["layer"]},
                    "freshB": (
                        {"route": fresh_b["route"], "layer": fresh_b["layer"]}
                        if fresh_b is not None
                        else None
                    ),
                },
                "profile": target_b["profile"],
                "checks": checks,
            }
        )
        payload["status"] = "pass" if all(bool(check.get("pass")) for check in checks.values()) else "fail"
    except BlockedProbe as exc:
        payload["status"] = "blocked"
        payload["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - report exact standalone failure
        payload["status"] = "error"
        payload["error"] = str(exc)
        payload["traceback"] = traceback.format_exc()
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": payload["status"], "report": str(output)}, ensure_ascii=False))
    return 0 if payload["status"] == "pass" else 1


def _same_curve_records(left: Mapping[str, Mapping[str, Any]], right: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Compare stable identity and payload fields for an isolation witness."""

    mismatches = []
    if set(left) != set(right):
        mismatches.append({"field": "curveUuids", "before": sorted(left), "after": sorted(right)})
    for uuid in sorted(set(left) & set(right)):
        for field in ("destinations", "times", "values"):
            if left[uuid].get(field) != right[uuid].get(field):
                mismatches.append({"uuid": uuid, "field": field})
    return {"beforeCount": len(left), "afterCount": len(right), "mismatches": mismatches, "pass": not mismatches}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2026")
    parser.add_argument("--evaluation-mode", choices=MODES, default="dg")
    parser.add_argument("--route", choices=ROUTES, default="legacy")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--motion", default=str(DEFAULT_MOTION))
    parser.add_argument("--out", default=str(ROOT / "build" / "reports" / "vmd_clear_existing_motion_e2e.json"))
    args = parser.parse_args(argv)
    return run(
        model=Path(args.model).resolve(),
        motion=Path(args.motion).resolve(),
        output=Path(args.out).resolve(),
        maya_version=str(args.maya),
        evaluation_mode=str(args.evaluation_mode),
        route=str(args.route),
    )


if __name__ == "__main__":
    import maya.standalone

    try:
        maya.standalone.initialize(name="python")
    except Exception:
        traceback.print_exc()
        raise
    try:
        raise SystemExit(main())
    finally:
        maya.standalone.uninitialize()
