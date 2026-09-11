"""Profile Maya VP2-managed PMX geometry with the existing DX11 MMD effect.

This is a deliberately limited A/B candidate for ``profile_e2e.py``.  It uses
ordinary Maya meshes split by material and ``dx11Shader``/``MMDShader.fx``;
it never uses ``mmdRenderShape`` nodes for drawing or selects the ``mmdOrdered``
render override. Saved scenes may retain dormant proxy nodes. Native playback
post-render intervals are primary for saved scenes; optional controlled
forced-refresh timings remain separate.

This runner is intended only for the isolated Maya process launched by
``run_maya_e2e``. Calling ``run_probe`` inside an interactive Maya session
discards the unsaved scene and leaves material, visibility, and DG mutations
in place; it is not an interactive editing API.
The separate Profiler pass is diagnostic and must not be compared numerically
with the primary samples.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.viewport.maya_e2e_harness import run_maya_e2e  # noqa: E402
from tools.render_override.common import (  # noqa: E402
    capture_view,
    require_requested_plugin,
    write_report,
)
from tools.render_override.render_override_visual_gate import read_png_rgb  # noqa: E402

MARKER = "MMD VP2 NATIVE GEOMETRY PROFILE FINISHED"
FX_PATH = ROOT / "mmd_tools" / "shaders" / "MMDShader.fx"
STANDARD_TOON_TEXTURE_DIR = ROOT / "mmd_tools" / "shaders" / "toon_textures"
FORBIDDEN_CPU_GEOMETRY_EVENTS = (
    "MMD.GetInputMeshObject",
    "MMD.GetVertexNormals",
    "MMD.PackVertices",
    "MMD.PackIndices",
    "MMD.UploadFrame",
)
SCENARIOS = ("static", "camera", "deform")


def sha256_file(path: Path) -> str:
    """Return a complete SHA-256 digest for one required artifact."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def secondary_texture_plan(
    slot: str,
    resolved_path: str,
    texture_index: int,
    *,
    shared_toon: bool = False,
    sphere_mode: int = 0,
) -> dict[str, Any]:
    """Resolve one canonical sphere/toon state without guessing from shader history."""

    if slot not in {"sphere", "toon"}:
        raise ValueError(f"unsupported secondary texture slot: {slot}")
    if isinstance(texture_index, bool) or not isinstance(texture_index, int):
        raise ValueError(f"{slot} texture index must be an integer")
    resolved_path = str(resolved_path or "")
    provenance = {
        "canonicalAttributeValue": resolved_path,
        "textureIndex": texture_index,
        "sharedToon": bool(shared_toon),
        "sphereMode": int(sphere_mode),
    }
    if slot == "sphere":
        if isinstance(sphere_mode, bool) or sphere_mode not in (0, 1, 2, 3):
            raise ValueError("sphere mode must be an integer between 0 and 3")
        if sphere_mode == 3:
            raise ValueError("sphere subtexture mode is not supported by MMDShader.fx")
        if sphere_mode == 0:
            if texture_index < 0:
                if resolved_path:
                    raise ValueError("unused sphere texture has a stale resolved path")
                return {
                    **provenance,
                    "status": "unused",
                    "reason": "sphere mode disabled and texture index absent",
                }
            if not resolved_path:
                raise ValueError(
                    "disabled sphere texture index has no canonical resolved path"
                )
            path = Path(resolved_path)
            if not path.is_file():
                raise ValueError(f"canonical sphere texture does not exist: {path}")
            return {
                **provenance,
                "status": "unused",
                "reason": "sphere mode disabled; canonical texture validated but not bound",
                "path": str(path.resolve()),
                "sha256": sha256_file(path),
                "sourceKind": "pmx_texture",
                "canonicalTextureValidated": True,
            }
        if texture_index < 0:
            if resolved_path:
                raise ValueError("active sphere mode has an invalid texture index")
            raise ValueError("active sphere mode has no texture index")
        if not resolved_path:
            raise ValueError("used sphere texture has no canonical resolved path")
        path = Path(resolved_path)
        source_kind = "pmx_texture"
    elif shared_toon:
        if resolved_path:
            raise ValueError("shared toon must not carry a custom resolved path")
        if not 0 <= texture_index <= 9:
            raise ValueError("shared toon texture index must be between 0 and 9")
        path = Path(STANDARD_TOON_TEXTURE_DIR) / f"toon{texture_index + 1:02d}.bmp"
        source_kind = "shared_toon"
    else:
        if texture_index < 0:
            if resolved_path:
                raise ValueError("unused custom toon has a stale resolved path")
            return {
                **provenance,
                "status": "unused",
                "reason": "custom toon texture index absent",
            }
        if not resolved_path:
            raise ValueError("used custom toon has no canonical resolved path")
        path = Path(resolved_path)
        source_kind = "pmx_texture"
    if not path.is_file():
        raise ValueError(f"canonical {slot} texture does not exist: {path}")
    return {
        **provenance,
        "status": "resolved",
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "sourceKind": source_kind,
    }


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the portable runner contract before starting Maya."""

    if not isinstance(config, dict):
        raise ValueError("configuration must be a JSON object")
    validated = dict(config)
    for key in ("output",):
        value = validated.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty path string")
    model_value = validated.get("model")
    scene_value = validated.get("scene")
    if bool(model_value) == bool(scene_value):
        raise ValueError("exactly one of model or scene must be provided")
    source = Path(model_value or scene_value)
    expected_suffix = ".pmx" if model_value else ".ma"
    if source.suffix.lower() != expected_suffix:
        raise ValueError(f"{'model' if model_value else 'scene'} must be a {expected_suffix} file")
    if not source.is_file():
        raise ValueError(f"input does not exist: {source}")
    if scene_value:
        plugin_value = validated.get("plugin")
        if not isinstance(plugin_value, str) or not Path(plugin_value).is_file():
            raise ValueError("scene profiling requires an existing C++ plugin path")
    if validated.get("separateMeshesByMaterial") is not True:
        raise ValueError("separateMeshesByMaterial must be true for this limited probe")
    for key in ("frames", "warmup", "repeats"):
        value = validated.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("playbackFrames", "playbackWarmup"):
        value = validated.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    animation_probe_offset = validated.get("animationProbeOffset", 1)
    if (
        isinstance(animation_probe_offset, bool)
        or not isinstance(animation_probe_offset, int)
        or animation_probe_offset < 1
    ):
        raise ValueError("animationProbeOffset must be a positive integer")
    if validated.get("playbackFrames", 0) and not scene_value:
        raise ValueError("native playback measurement requires a saved scene")
    if validated.get("evaluation") not in {"off", "serial", "parallel"}:
        raise ValueError("evaluation must be off, serial, or parallel")
    if validated.get("geometryRoute", "source-direct") not in {"source-direct", "display-copy"}:
        raise ValueError("geometryRoute must be source-direct or display-copy")
    if not isinstance(validated.get("disableCache", False), bool):
        raise ValueError("disableCache must be a boolean")
    expected_mesh_count = validated.get("expectedMeshCount")
    if expected_mesh_count is not None and (
        isinstance(expected_mesh_count, bool)
        or not isinstance(expected_mesh_count, int)
        or expected_mesh_count < 1
    ):
        raise ValueError("expectedMeshCount must be a positive integer or null")
    return validated


def _percentile95(values: list[float]) -> float:
    """Return the observed nearest-rank p95 without inventing samples."""

    if not values:
        raise ValueError("p95 requires at least one sample")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def sixteen_ms_acceptance(
    cases: list[dict[str, Any]], summary: dict[str, Any], *, eligible: bool
) -> dict[str, bool]:
    """Evaluate the fixed 16 ms playback acceptance and expose each sub-gate."""

    repeat_means_pass = bool(cases) and all(case["meanMs"] <= 16.0 for case in cases)
    pooled_p95_pass = summary["p95Ms"] <= 16.67
    notifications_pass = summary["missingNotificationCount"] == 0
    return {
        "repeatMeansAtMost16Ms": repeat_means_pass,
        "pooledP95AtMost16_67Ms": pooled_p95_pass,
        "notificationCompleteness": notifications_pass,
        "sixteenMsPassed": bool(
            eligible
            and repeat_means_pass
            and pooled_p95_pass
            and notifications_pass
        ),
    }


def normalize_playback_callbacks(
    callbacks: list[list[float]], first: int, warmup: int, interval_count: int
) -> dict[str, Any]:
    """Normalize callback evidence and require N+1 ordered frame notifications for N intervals."""

    unique_frames: list[list[float]] = []
    previous_stamp = None
    for stamp, frame in callbacks:
        stamp = float(stamp)
        frame = float(frame)
        if previous_stamp is not None and stamp <= previous_stamp:
            raise ValueError("playback callback timestamps are not strictly ordered")
        if not frame.is_integer():
            raise ValueError(f"playback callback frame is not integral: {frame}")
        if unique_frames and frame < unique_frames[-1][1]:
            raise ValueError("playback callback frames moved backwards")
        previous_stamp = stamp
        if not unique_frames or frame != unique_frames[-1][1]:
            unique_frames.append([stamp, frame])
    last = first + warmup + interval_count
    expected_frames = list(range(first + warmup, last + 1))
    measured = [item for item in unique_frames if first + warmup <= item[1] <= last]
    observed_frames = [int(item[1]) for item in measured]
    missing_frames = [frame for frame in expected_frames if frame not in observed_frames]
    intervals = [(b[0] - a[0]) * 1000.0 for a, b in zip(measured, measured[1:])]
    evidence = {
        "rawCallbackCount": len(callbacks),
        "uniqueFrameCallbackCount": len(unique_frames),
        "measuredFrameCallbackCount": len(measured),
        "intervalSampleCount": len(intervals),
        "expectedIntervalSampleCount": interval_count,
        "missingNotificationFrames": missing_frames,
        "intervalMs": intervals,
    }
    if observed_frames != expected_frames or len(intervals) != interval_count:
        raise ValueError(
            "native playback post-render notifications were incomplete: "
            f"expected={expected_frames} observed={observed_frames}"
        )
    return evidence


def native_playback_steps(cmds: Any, panel: str, config: dict[str, Any], report: dict[str, Any]):
    """Measure real Maya playback from successive VP2 post-render callbacks."""

    from maya import OpenMaya as om
    from maya import OpenMayaUI as omui

    playback_frames = config.get("playbackFrames", 0)
    if not playback_frames:
        return
    time_unit = cmds.currentUnit(query=True, time=True)
    report["nativePlaybackTimeUnit"] = time_unit
    if time_unit in ("sec", "min", "hour", "millisec"):
        raise ValueError("native playback requires a frame-based scene time unit")
    cmds.profiler(sampling=False)
    cmds.modelEditor(panel, edit=True, rendererOverrideName="")
    cmds.setFocus(panel)
    first = config["frame"]
    warmup = config.get("playbackWarmup", 10)
    last = first + warmup + playback_frames
    cmds.playbackOptions(
        minTime=first, maxTime=last, loop="once", by=1,
        playbackSpeed=0, maxPlaybackSpeed=0, view="active",
    )
    report["nativePlaybackSettings"] = {
        key: cmds.playbackOptions(query=True, **{key: True})
        for key in ("minTime", "maxTime", "loop", "by", "playbackSpeed", "maxPlaybackSpeed", "view")
    }
    cases = []
    report["measurements"]["nativePlayback"] = cases
    for repeat in range(config["repeats"]):
        cmds.currentTime(first)
        yield
        callbacks: list[list[float]] = []
        case: dict[str, Any] = {"repeat": repeat, "status": "collecting"}
        cases.append(case)

        def rendered(*unused: Any) -> None:
            callbacks.append([time.perf_counter(), cmds.currentTime(query=True)])

        callback = omui.MUiMessage.add3dViewPostRenderMsgCallback(panel, rendered)
        started = time.perf_counter()
        try:
            cmds.play(forward=True)
            while cmds.play(query=True, state=True):
                if time.perf_counter() - started > 60:
                    raise RuntimeError("native playback did not finish within 60 seconds")
                yield
        finally:
            try:
                cmds.play(state=False)
            finally:
                om.MMessage.removeCallback(callback)

        try:
            evidence = normalize_playback_callbacks(callbacks, first, warmup, playback_frames)
        except ValueError as error:
            case["normalizationError"] = str(error)
            raise RuntimeError(f"native playback repeat {repeat} failed: {error}") from error
        intervals = evidence["intervalMs"]
        case.update(evidence)
        case["profilerSampling"] = False
        case.update({
            "status": "pass",
            "meanMs": statistics.mean(intervals),
            "medianMs": statistics.median(intervals),
            "p95Ms": _percentile95(intervals),
            "maxMs": max(intervals),
        })
    all_intervals = [value for case in cases for value in case["intervalMs"]]
    summary = {
        "repeatCount": len(cases),
        "intervalSampleCount": len(all_intervals),
        "expectedIntervalSampleCount": playback_frames * config["repeats"],
        "missingNotificationCount": sum(
            len(case["missingNotificationFrames"]) for case in cases
        ),
        "meanMs": statistics.mean(all_intervals),
        "medianMs": statistics.median(all_intervals),
        "p95Ms": _percentile95(all_intervals),
        "maxMs": max(all_intervals),
    }
    report["measurements"]["nativePlaybackSummary"] = summary
    report["acceptance"].update(
        sixteen_ms_acceptance(
            cases,
            summary,
            eligible=report["acceptance"]["sixteenMsEligible"],
        )
    )


def profiler_events(cmds: Any) -> dict[str, dict[str, float | int]]:
    """Collect inclusive top-level VP2/MMD event durations for diagnosis."""

    events: defaultdict[str, list[float]] = defaultdict(list)
    count = cmds.profiler(query=True, eventCount=True)
    for index in range(count):
        name = cmds.profiler(query=True, eventIndex=index, eventName=True)
        if name.startswith("MMD.") or name.startswith("Vp2"):
            duration = cmds.profiler(query=True, eventIndex=index, eventDuration=True)
            events[name].append(duration / 1000.0)
    return {
        name: {"count": len(values), "inclusiveTotalMs": sum(values)}
        for name, values in events.items()
    }


def _assigned_shaders(cmds: Any, meshes: list[str]) -> list[str]:
    """Return unique surface shaders assigned to the selected ordinary meshes."""

    shaders: set[str] = set()
    for mesh in meshes:
        if cmds.getAttr(mesh + ".intermediateObject"):
            continue
        for shading_group in cmds.listConnections(mesh, type="shadingEngine") or []:
            shaders.update(
                cmds.listConnections(
                    shading_group + ".surfaceShader",
                    source=True,
                    destination=False,
                )
                or []
            )
    return sorted(shaders)


def _convert_imported_materials_to_dx11(
    cmds: Any, meshes: list[str], expected_model_path: str
) -> tuple[list[str], list[dict], list[dict]]:
    """Convert only indexed MMD standardSurface nodes and verify value parity."""

    from mmd_tools.converters import mesh_converter
    from mmd_tools.converters.material_shader_parameters import (
        ATTR_MMD_DIFFUSE_ALPHA,
        ATTR_MMD_EDGE_ALPHA,
    )
    from mmd_tools.core.constants import (
        ATTR_MMD_DIFFUSE_COLOR,
        ATTR_MMD_EDGE_COLOR,
        ATTR_MMD_MATERIAL,
        ATTR_MMD_MATERIAL_INDEX,
        ATTR_MMD_ORIGINAL_TEXTURE_PATH,
        ATTR_MMD_SHARED_TOON_FLAG,
        ATTR_MMD_SOURCE_MODEL_PATH,
        ATTR_MMD_SPHERE_MODE,
        ATTR_MMD_SPHERE_TEXTURE_INDEX,
        ATTR_MMD_TOON_TEXTURE_INDEX,
    )
    from mmd_tools.core.maya_material_utils import (
        ATTR_MMD_SHARED_TOON_ID,
        ATTR_MMD_TEXTURE_SOURCE_KIND,
        mark_mmd_texture_file_node,
    )

    if mesh_converter.effective_mmd_shader_backend() != "dx11":
        raise RuntimeError("configured MMD shader backend did not resolve to dx11")
    texture_bindings = []
    material_contracts = []
    seen_indices = set()
    secondary_file_cache: dict[tuple[str, str, str, str, str], str] = {}
    sources = _assigned_shaders(cmds, meshes)
    model_paths = set()
    for source in sources:
        if cmds.nodeType(source) != "standardSurface":
            continue
        for file_node in cmds.listConnections(
            source + ".baseColor", source=True, destination=False, type="file"
        ) or []:
            if cmds.attributeQuery(ATTR_MMD_SOURCE_MODEL_PATH, node=file_node, exists=True):
                value = cmds.getAttr(f"{file_node}.{ATTR_MMD_SOURCE_MODEL_PATH}") or ""
                if value:
                    model_paths.add(str(Path(value).resolve()))
    canonical_model_path = str(Path(expected_model_path).resolve())
    if model_paths and model_paths != {canonical_model_path}:
        raise RuntimeError(
            "MMD texture provenance does not match the authoritative model path: "
            f"expected={canonical_model_path!r} discovered={sorted(model_paths)!r}"
        )

    for source in sources:
        if cmds.nodeType(source) == "dx11Shader":
            continue
        if cmds.nodeType(source) != "standardSurface":
            raise RuntimeError(f"unexpected imported shader type: {source} ({cmds.nodeType(source)})")
        for attribute in (
            ATTR_MMD_MATERIAL, ATTR_MMD_MATERIAL_INDEX, ATTR_MMD_DIFFUSE_COLOR,
            ATTR_MMD_DIFFUSE_ALPHA, ATTR_MMD_EDGE_COLOR, ATTR_MMD_EDGE_ALPHA,
            ATTR_MMD_SPHERE_MODE, ATTR_MMD_SPHERE_TEXTURE_INDEX,
            ATTR_MMD_SHARED_TOON_FLAG, ATTR_MMD_TOON_TEXTURE_INDEX,
            "mmd_resolved_sphere_texture_path", "mmd_resolved_toon_texture_path",
        ):
            if not cmds.attributeQuery(attribute, node=source, exists=True):
                raise RuntimeError(f"MMD material contract is missing {source}.{attribute}")
        if cmds.getAttr(f"{source}.{ATTR_MMD_MATERIAL}") != 1:
            raise RuntimeError(f"shader is not marked as an MMD material: {source}")
        material_index = cmds.getAttr(f"{source}.{ATTR_MMD_MATERIAL_INDEX}")
        if (
            isinstance(material_index, bool)
            or not isinstance(material_index, int)
            or material_index < 0
        ):
            raise RuntimeError(f"invalid MMD material index: {source}={material_index!r}")
        if material_index in seen_indices:
            raise RuntimeError(f"duplicate MMD material index: {material_index}")
        seen_indices.add(material_index)
        expected_diffuse = list(cmds.getAttr(f"{source}.{ATTR_MMD_DIFFUSE_COLOR}")[0])
        expected_diffuse_alpha = float(cmds.getAttr(f"{source}.{ATTR_MMD_DIFFUSE_ALPHA}"))
        expected_edge = list(cmds.getAttr(f"{source}.{ATTR_MMD_EDGE_COLOR}")[0])
        expected_edge_alpha = float(cmds.getAttr(f"{source}.{ATTR_MMD_EDGE_ALPHA}"))
        destinations = cmds.listConnections(
            source + ".outColor", source=False, destination=True, plugs=True
        ) or []
        replacement = mesh_converter._create_backend_replacement(source, "dx11")
        try:
            mesh_converter._copy_shader_backend_state(source, replacement)
            required_uniforms = (
                "DiffuseColorRGB", "DiffuseColorA", "EdgeColorRGB", "EdgeColorA",
                "EdgeSize", "Opacity", "MainTexture", "HasMainTexture",
                "SphereTexture", "HasSphereTexture", "ToonTexture", "HasToonTexture",
            )
            missing_uniforms = [
                attr for attr in required_uniforms
                if not cmds.attributeQuery(attr, node=replacement, exists=True)
            ]
            if missing_uniforms:
                raise RuntimeError(
                    f"DX11 required uniforms missing on {replacement}: {missing_uniforms}"
                )
            main_files = cmds.listConnections(
                source + ".baseColor", source=True, destination=False, type="file"
            ) or []
            main_files = sorted(set(main_files))
            if len(main_files) > 1:
                raise RuntimeError(f"ambiguous direct standardSurface texture for {source}: {main_files}")
            authored_texture = (
                cmds.getAttr(source + ".mmd_texture_path") or ""
                if cmds.attributeQuery("mmd_texture_path", node=source, exists=True)
                else ""
            )
            resolved_main_texture = (
                cmds.getAttr(source + ".mmd_resolved_texture_path") or ""
                if cmds.attributeQuery(
                    "mmd_resolved_texture_path", node=source, exists=True
                )
                else ""
            )
            main_provenance = None
            if main_files:
                main_file = main_files[0]
                required_file_attrs = (
                    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
                    ATTR_MMD_SOURCE_MODEL_PATH,
                    ATTR_MMD_TEXTURE_SOURCE_KIND,
                    "mmd_texture_unresolved",
                )
                missing_file_attrs = [
                    attr
                    for attr in required_file_attrs
                    if not cmds.attributeQuery(attr, node=main_file, exists=True)
                ]
                if missing_file_attrs:
                    raise RuntimeError(
                        f"main texture provenance missing for {source}: {missing_file_attrs}"
                    )
                actual_main_path = cmds.getAttr(main_file + ".fileTextureName") or ""
                original_main_path = cmds.getAttr(
                    f"{main_file}.{ATTR_MMD_ORIGINAL_TEXTURE_PATH}"
                ) or ""
                main_model_path = cmds.getAttr(
                    f"{main_file}.{ATTR_MMD_SOURCE_MODEL_PATH}"
                ) or ""
                main_source_kind = cmds.getAttr(
                    f"{main_file}.{ATTR_MMD_TEXTURE_SOURCE_KIND}"
                ) or ""
                main_unresolved = bool(cmds.getAttr(main_file + ".mmd_texture_unresolved"))
                main_path_matches = bool(resolved_main_texture) and (
                    os.path.normcase(os.path.normpath(actual_main_path))
                    == os.path.normcase(os.path.normpath(resolved_main_texture))
                )
                if not (
                    authored_texture
                    and actual_main_path
                    and Path(actual_main_path).is_file()
                    and main_path_matches
                    and original_main_path == authored_texture
                    and main_model_path
                    and str(Path(main_model_path).resolve()) == canonical_model_path
                    and main_source_kind == "pmx_texture"
                    and not main_unresolved
                ):
                    raise RuntimeError(f"main texture provenance validation failed for {source}")
                if not mesh_converter.bind_dx11_texture_file_node(
                    replacement, main_file, "MainTexture", "HasMainTexture"
                ):
                    raise RuntimeError(f"failed to bind main texture for {source}")
                main_provenance = {
                    "resolvedPath": actual_main_path,
                    "originalPath": original_main_path,
                    "modelPath": main_model_path,
                    "sourceKind": main_source_kind,
                    "validated": True,
                }
            elif authored_texture or resolved_main_texture:
                raise RuntimeError(
                    f"authored main texture was not rebound for {source}: "
                    f"authored={authored_texture!r} resolved={resolved_main_texture!r}"
                )
            sphere_texture_index = cmds.getAttr(
                f"{source}.{ATTR_MMD_SPHERE_TEXTURE_INDEX}"
            )
            sphere_mode = cmds.getAttr(f"{source}.{ATTR_MMD_SPHERE_MODE}")
            toon_texture_index = cmds.getAttr(f"{source}.{ATTR_MMD_TOON_TEXTURE_INDEX}")
            shared_toon_flag = cmds.getAttr(f"{source}.{ATTR_MMD_SHARED_TOON_FLAG}")
            for label, value, minimum, maximum in (
                ("sphere texture index", sphere_texture_index, -1, None),
                ("sphere mode", sphere_mode, 0, 3),
                ("toon texture index", toon_texture_index, -1, None),
                ("shared toon flag", shared_toon_flag, 0, 1),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < minimum
                    or (maximum is not None and value > maximum)
                ):
                    raise RuntimeError(f"invalid {label} for {source}: {value!r}")
            shared_toon = shared_toon_flag == 1
            toon_source_path = (
                cmds.getAttr(source + ".mmd_toon_path") or ""
                if cmds.attributeQuery("mmd_toon_path", node=source, exists=True)
                else ""
            )
            if shared_toon and toon_source_path:
                raise RuntimeError(
                    f"shared toon carries stale custom source path for {source}: "
                    f"{toon_source_path}"
                )
            sphere_plan = secondary_texture_plan(
                "sphere",
                cmds.getAttr(source + ".mmd_resolved_sphere_texture_path") or "",
                sphere_texture_index,
                sphere_mode=sphere_mode,
            )
            toon_plan = secondary_texture_plan(
                "toon",
                cmds.getAttr(source + ".mmd_resolved_toon_texture_path") or "",
                toon_texture_index,
                shared_toon=shared_toon,
            )
            model_path = canonical_model_path
            for slot, plan, texture_attr, has_attr, original_attr in (
                ("sphere", sphere_plan, "SphereTexture", "HasSphereTexture", "mmd_sphere_path"),
                ("toon", toon_plan, "ToonTexture", "HasToonTexture", "mmd_toon_path"),
            ):
                if plan["status"] == "unused":
                    incoming = cmds.connectionInfo(
                        f"{replacement}.{texture_attr}", sourceFromDestination=True
                    )
                    if incoming or cmds.getAttr(f"{replacement}.{has_attr}"):
                        raise RuntimeError(
                            f"unused {slot} texture unexpectedly bound for {source}: {incoming}"
                        )
                    plan["bindingValidated"] = True
                    continue
                if plan["sourceKind"] == "pmx_texture" and not model_path:
                    raise RuntimeError(
                        f"custom {slot} texture has no model provenance for {source}"
                    )
                original_path = ""
                if cmds.attributeQuery(original_attr, node=source, exists=True):
                    original_path = cmds.getAttr(f"{source}.{original_attr}") or ""
                expected_shared_toon_id = (
                    f"toon{toon_texture_index + 1:02d}"
                    if plan["sourceKind"] == "shared_toon"
                    else ""
                )
                cache_key = (
                    os.path.normcase(os.path.normpath(plan["path"])),
                    original_path,
                    model_path,
                    plan["sourceKind"],
                    expected_shared_toon_id,
                )
                file_node = secondary_file_cache.get(cache_key)
                reused_file_node = file_node is not None
                if file_node is None:
                    file_node = cmds.shadingNode(
                        "file", asTexture=True, isColorManaged=True,
                        name=f"{source}_{slot}_texture",
                    )
                    cmds.setAttr(
                        file_node + ".fileTextureName", plan["path"], type="string"
                    )
                    mark_mmd_texture_file_node(
                        file_node,
                        original_path,
                        model_path,
                        source_kind=plan["sourceKind"],
                        shared_toon_id=expected_shared_toon_id,
                    )
                    secondary_file_cache[cache_key] = file_node
                actual_path = cmds.getAttr(file_node + ".fileTextureName") or ""
                actual_source_kind = cmds.getAttr(
                    f"{file_node}.{ATTR_MMD_TEXTURE_SOURCE_KIND}"
                ) or ""
                actual_original_path = cmds.getAttr(
                    f"{file_node}.{ATTR_MMD_ORIGINAL_TEXTURE_PATH}"
                ) or ""
                actual_model_path = cmds.getAttr(
                    f"{file_node}.{ATTR_MMD_SOURCE_MODEL_PATH}"
                ) or ""
                actual_shared_toon_id = ""
                if cmds.attributeQuery(
                    ATTR_MMD_SHARED_TOON_ID, node=file_node, exists=True
                ):
                    actual_shared_toon_id = cmds.getAttr(
                        f"{file_node}.{ATTR_MMD_SHARED_TOON_ID}"
                    ) or ""
                path_matches = os.path.normcase(os.path.normpath(actual_path)) == os.path.normcase(
                    os.path.normpath(plan["path"])
                )
                if not (
                    path_matches
                    and actual_source_kind == plan["sourceKind"]
                    and actual_original_path == original_path
                    and actual_model_path == model_path
                    and actual_shared_toon_id == expected_shared_toon_id
                ):
                    raise RuntimeError(
                        f"{slot} texture provenance validation failed for {source}"
                    )
                if not mesh_converter.bind_dx11_texture_file_node(
                    replacement, file_node, texture_attr, has_attr
                ):
                    raise RuntimeError(f"failed to bind {slot} texture for {source}")
                incoming = cmds.connectionInfo(
                    f"{replacement}.{texture_attr}", sourceFromDestination=True
                )
                if incoming != file_node + ".outColor" or not cmds.getAttr(
                    f"{replacement}.{has_attr}"
                ):
                    raise RuntimeError(
                        f"{slot} texture binding validation failed for {source}: {incoming}"
                    )
                plan["fileNode"] = file_node
                plan["textureAttribute"] = texture_attr
                plan["presenceAttribute"] = has_attr
                plan["reusedFileNode"] = reused_file_node
                plan["bindingValidated"] = True
                plan["provenance"] = {
                    "originalPath": actual_original_path,
                    "modelPath": actual_model_path,
                    "sourceKind": actual_source_kind,
                    "sharedToonId": actual_shared_toon_id,
                    "validated": True,
                }
            texture_bindings.append(
                {
                    "shader": source,
                    "mainTextureFile": main_files[0] if main_files else None,
                    "authoredMainTexture": authored_texture,
                    "mainTextureComplete": not authored_texture or bool(main_files),
                    "mainTextureProvenance": main_provenance,
                    "sphereTexture": sphere_plan,
                    "toonTexture": toon_plan,
                }
            )
            legacy = cmds.rename(source, source + "__vp2_native_standard")
            replacement = cmds.rename(replacement, source)
            for plan in (sphere_plan, toon_plan):
                if plan["status"] == "resolved":
                    plan["binding"] = f"{replacement}.{plan['textureAttribute']}"
            for destination in destinations:
                if destination.endswith(".surfaceShader"):
                    cmds.connectAttr(replacement + ".outColor", destination, force=True)
            cmds.delete(legacy)
            if mesh_converter.sync_dx11_generated_uniforms([replacement]) < 1:
                raise RuntimeError(f"DX11 generated uniforms did not sync for {replacement}")
            actual_diffuse = list(cmds.getAttr(replacement + ".DiffuseColorRGB")[0])
            actual_diffuse_alpha = float(cmds.getAttr(replacement + ".DiffuseColorA"))
            actual_edge = list(cmds.getAttr(replacement + ".EdgeColorRGB")[0])
            actual_edge_alpha = float(cmds.getAttr(replacement + ".EdgeColorA"))
            comparisons = (
                ("diffuseRGB", actual_diffuse, expected_diffuse),
                ("diffuseAlpha", [actual_diffuse_alpha], [expected_diffuse_alpha]),
                ("edgeRGB", actual_edge, expected_edge),
                ("edgeAlpha", [actual_edge_alpha], [expected_edge_alpha]),
            )
            for label, actual, expected in comparisons:
                if len(actual) != len(expected) or any(
                    abs(float(a) - float(b)) > 1e-6 for a, b in zip(actual, expected)
                ):
                    raise RuntimeError(
                        f"DX11 {label} parity failed for material {material_index}: "
                        f"expected={expected} actual={actual}"
                    )
            material_contracts.append({
                "shader": replacement,
                "materialIndex": material_index,
                "mmdMarker": True,
                "requiredUniforms": list(required_uniforms),
                "diffuseRGBA": expected_diffuse + [expected_diffuse_alpha],
                "edgeRGBA": expected_edge + [expected_edge_alpha],
                "directMainTextureFile": main_files[0] if main_files else None,
                "secondaryTextures": {"sphere": sphere_plan, "toon": toon_plan},
                "valueParity": "pass",
            })
        except Exception:
            if replacement and cmds.objExists(replacement):
                cmds.delete(replacement)
            raise
    shaders = _assigned_shaders(cmds, meshes)
    if not shaders or any(cmds.nodeType(shader) != "dx11Shader" for shader in shaders):
        raise RuntimeError("ordinary PMX meshes did not resolve exclusively to dx11Shader")
    if len(material_contracts) != len(shaders):
        raise RuntimeError(
            f"not every DX11 shader passed MMD material parity: "
            f"verified={len(material_contracts)} shaders={len(shaders)}"
        )
    return shaders, texture_bindings, sorted(
        material_contracts, key=lambda item: item["materialIndex"]
    )


def _scene_source_meshes(
    cmds: Any, geometry_route: str
) -> tuple[list[str], list[str], list[dict]]:
    """Expose direct sources or create an explicitly additional display consumer."""

    proxies = cmds.ls(type="mmdRenderShape", long=True) or []
    displays = []
    detached = []
    for proxy in proxies:
        input_source = cmds.connectionInfo(proxy + ".inputMesh", sourceFromDestination=True)
        source = input_source.rsplit(".", 1)[0] if input_source else ""
        if not source or cmds.nodeType(source) != "mesh":
            raise RuntimeError(f"render proxy has no ordinary source mesh: {proxy}")
        matches = cmds.ls(source, long=True) or []
        source = matches[0] if len(matches) == 1 else source
        visibility = source + ".visibility"
        link = proxy + ".sourceVisibility"
        was_connected = cmds.isConnected(link, visibility)
        if was_connected:
            cmds.disconnectAttr(link, visibility)
        input_disconnected = False
        if geometry_route == "source-direct":
            cmds.setAttr(visibility, True)
            cmds.disconnectAttr(input_source, proxy + ".inputMesh")
            input_disconnected = True
            render_consumers = cmds.listConnections(
                source + ".outMesh", source=False, destination=True, type="mmdRenderShape"
            ) or []
            if render_consumers:
                raise RuntimeError(
                    f"source-direct retained mmdRenderShape consumers: {source} {render_consumers}"
                )
            display_shape = source
        else:
            cmds.setAttr(visibility, False)
            display_transform = cmds.createNode("transform", name="mmdVp2NativeDisplay#")
            display_shape = cmds.createNode(
                "mesh", name="mmdVp2NativeDisplayShape#", parent=display_transform
            )
            source_transform = cmds.listRelatives(source, parent=True, fullPath=True)[0]
            cmds.connectAttr(source + ".outMesh", display_shape + ".inMesh", force=True)
            cmds.connectAttr(
                source_transform + ".worldMatrix[0]",
                display_transform + ".offsetParentMatrix",
                force=True,
            )
            shading_groups = sorted(set(cmds.listConnections(source, type="shadingEngine") or []))
            if len(shading_groups) != 1:
                raise RuntimeError(
                    f"split source mesh must have exactly one shading group: {source} {shading_groups}"
                )
            cmds.sets(display_shape, edit=True, forceElement=shading_groups[0])
        displays.append(cmds.ls(display_shape, long=True)[0])
        detached.append({
            "proxy": proxy,
            "source": source,
            "mayaManagedDisplayMesh": displays[-1],
            "visibilityLinkDetached": was_connected,
            "proxyInputMeshDisconnected": input_disconnected,
            "sourceShapeHidden": geometry_route == "display-copy",
        })
    if not displays:
        raise RuntimeError("scene contains no source-mesh-connected mmdRenderShape nodes")
    return sorted(displays), list(proxies), detached


def _rebind_material_morphs(cmds: Any, root: str, shaders: list[str]) -> dict[str, Any]:
    """Rebuild hardware morph routes and report driven/non-driven status per material."""

    from mmd_tools.converters.material_morph_runtime import (
        EVAL_NODE_TYPE,
        build_material_morph_graph,
    )

    result = build_material_morph_graph(root)
    if not result.get("success"):
        raise RuntimeError(f"material morph hardware rebind failed: {result}")
    materials = []
    driven_count = 0
    for shader in shaders:
        index = int(cmds.getAttr(shader + ".mmd_material_index"))
        evaluators = sorted(set(cmds.listConnections(shader, source=True, type=EVAL_NODE_TYPE) or []))
        driven = bool(evaluators)
        driven_count += int(driven)
        materials.append({
            "shader": shader,
            "materialIndex": index,
            "driven": driven,
            "evaluators": evaluators,
            "reason": None if driven else "not_driven; no material morph contribution targets this material",
        })
    contributions = int(result.get("contributions", 0))
    if contributions and not driven_count:
        raise RuntimeError(
            "material morph contributions exist but no DX11 shader has an evaluator connection"
        )
    return {
        "graphSuccess": True,
        "contributionCount": contributions,
        "drivenMaterialCount": driven_count,
        "animatedMaterialParity": (
            "pass" if contributions else "not_run; scene has no material morph contributions"
        ),
        "skipped": result.get("skipped", []),
        "materials": sorted(materials, key=lambda item: item["materialIndex"]),
    }


def _image_difference(left: tuple, right: tuple) -> int:
    """Count materially changed pixels between equal-size RGB captures."""

    if left[:2] != right[:2]:
        raise ValueError("captured image sizes differ")
    return sum(
        max(abs(a - b) for a, b in zip(first, second)) > 8
        for first, second in zip(left[2], right[2])
    )


def _animation_state(cmds: Any, meshes: list[str]) -> dict[str, list[float]]:
    """Sample evaluated source vertices and joint matrices at the current time."""

    vertices = []
    for mesh in meshes:
        if cmds.polyEvaluate(mesh, vertex=True):
            vertices.extend(cmds.pointPosition(mesh + ".vtx[0]", world=True))
    joints = []
    for joint in cmds.ls(type="joint", long=True) or []:
        joints.extend(cmds.xform(joint, query=True, matrix=True, worldSpace=True))
    return {"sourceVertexWorld": vertices, "jointWorldMatrices": joints}


def _max_state_delta(first: list[float], second: list[float]) -> float:
    """Return a fail-closed scalar delta for two equally shaped samples."""

    if len(first) != len(second):
        raise RuntimeError("animation evidence sample shape changed between frames")
    return max((abs(a - b) for a, b in zip(first, second)), default=0.0)


def _configure_cache_evaluator(cmds: Any, config: dict[str, Any], report: dict[str, Any]) -> None:
    """Apply and verify the cache-evaluator measurement condition."""

    if config.get("disableCache", False):
        cmds.evaluator(name="cache", enable=False)
    queried = cmds.evaluator(name="cache", query=True, enable=True)
    enabled = any(queried) if isinstance(queried, (list, tuple)) else bool(queried)
    report["cacheEvaluatorEnabled"] = enabled
    report["acceptance"] = {
        "sixteenMsEligible": bool(config.get("disableCache", False) and not enabled),
        "requirement": "16ms acceptance requires --disable-cache and cache evaluator disabled",
    }
    if config.get("disableCache", False) and enabled:
        raise RuntimeError("cache evaluator remained enabled after --disable-cache")


def run_probe(config_path: str) -> None:
    """Schedule the Maya-side generator without blocking the GUI event loop."""

    try:
        from PySide2.QtCore import QTimer
    except ImportError:
        from PySide6.QtCore import QTimer
    config = validate_config(json.loads(Path(config_path).read_text(encoding="utf-8")))
    steps = probe_steps(config)

    def advance() -> None:
        try:
            next(steps)
        except StopIteration:
            return
        QTimer.singleShot(100, advance)

    advance()


def probe_steps(config: dict[str, Any]):
    """Import, validate, capture, and measure the VP2-native candidate."""

    from maya import cmds
    from maya.api import OpenMayaUI as omui
    from mmd_tools.core.settings import settings
    from mmd_tools.io.mmd_importer import import_mmd_file

    out = Path(config["output"])
    report: dict[str, Any] = {
        "status": "fail",
        "runnerStatus": "fail",
        "performanceAcceptanceStatus": "not_run",
        "conditions": config,
        "scope": (
            "ordinary split Maya meshes with dx11Shader/MMDShader.fx; native playback "
            "uses post-render intervals; optional controlled cluster timing is separate"
        ),
        "route": {
            "geometryOwner": "Maya Viewport 2.0",
            "rendererOverrideName": "",
            "sceneGeometryRoute": config.get("geometryRoute", "source-direct"),
            "mmdRenderShapeNodesPresent": None,
            "mmdRenderShapeUsedForDrawing": False,
            "orderedRawDx11Used": False,
            "additionalEvaluatedMeshConsumer": config.get("geometryRoute") == "display-copy",
        },
        "notRun": {
            "selfShadow": "not_run",
            "customOrderedParity": "not_run",
            "gpuTimestamp": "not_run",
            "nativePlaybackFps": "not_run unless --playback-frames is greater than zero",
            "unifiedMesh": "not_run; split-by-material is mandatory",
        },
        "measurements": {
            "primary": "profilerOffNativePlaybackPostRenderIntervalMs for saved scenes",
            "profilerWarning": "inclusive diagnostic durations are nested and must not be summed",
            "timingCases": [],
            "profilerDiagnostics": [],
        },
    }
    try:
        cmds.file(new=True, force=True)
        cmds.evaluationManager(mode=config["evaluation"])
        cmds.loadPlugin("dx11Shader", quiet=True)
        cmds.loadPlugin(str(ROOT / "plug-ins" / "mmd_tools_plugin.py"), quiet=True)
        settings.set("import.model.create_mmd_shaders", True)
        settings.set("import.model.mmd_shader_backend", "dx11")
        settings.set("import.model.separate_meshes_by_material", True)
        if config.get("scene"):
            plugin = require_requested_plugin(cmds, config["plugin"], print)
            cmds.file(config["scene"], open=True, force=True, prompt=False)
            require_requested_plugin(cmds, config["plugin"], print)
            cmds.evaluationManager(mode=config["evaluation"])
            _configure_cache_evaluator(cmds, config, report)
            cmds.currentTime(config["frame"])
            meshes, proxies, visibility_changes = _scene_source_meshes(
                cmds, config.get("geometryRoute", "source-direct")
            )
            source_paths = [item["source"] for item in visibility_changes]
            roots = set()
            for source_path in source_paths:
                node = source_path
                while node:
                    if cmds.attributeQuery("mmd_model_name", node=node, exists=True):
                        roots.add(node)
                        break
                    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
                    node = parents[0] if len(parents) == 1 else ""
            roots = sorted(roots)
            root = roots[0] if len(roots) == 1 else None
            report["modelRootCandidates"] = roots
            report["hashes"] = {
                "sceneSha256": sha256_file(Path(config["scene"])),
                "pluginSha256": sha256_file(plugin),
                "probeSha256": sha256_file(Path(__file__)),
                "fxSha256": sha256_file(FX_PATH),
            }
            _configure_cache_evaluator(cmds, config, report)
            report["sourceVisibilityChanges"] = visibility_changes
            report["route"]["mmdRenderShapeInputConsumersDetached"] = all(
                item["proxyInputMeshDisconnected"] for item in visibility_changes
            )
            report["sceneSaved"] = False
        else:
            root = import_mmd_file(
                config["model"],
                options={
                    "use_cpp_fast_load": False,
                    "use_cpp_vp2_ownership": False,
                    "import_morphs": False,
                    "import_physics": False,
                    "separate_meshes_by_material": True,
                },
            )
            if not root:
                raise RuntimeError("import_mmd_file returned no model root")
            meshes = [
                mesh
                for mesh in (cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or [])
                if not cmds.getAttr(mesh + ".intermediateObject")
            ]
            proxies = cmds.listRelatives(
                root, allDescendents=True, type="mmdRenderShape", fullPath=True
            ) or []
            if proxies:
                raise RuntimeError(f"PMX import unexpectedly created {len(proxies)} render proxies")
            report["hashes"] = {
                "inputModelSha256": sha256_file(Path(config["model"])),
                "probeSha256": sha256_file(Path(__file__)),
                "fxSha256": sha256_file(FX_PATH),
            }
        if not meshes:
            raise RuntimeError("probe found no ordinary source meshes")
        if not root:
            raise RuntimeError("MMD model root is ambiguous; source provenance is unavailable")
        if config.get("model"):
            expected_model_path = str(Path(config["model"]).resolve())
        else:
            if not cmds.attributeQuery("mmd_source_file", node=root, exists=True):
                raise RuntimeError(f"scene model root has no mmd_source_file: {root}")
            source_file = cmds.getAttr(root + ".mmd_source_file") or ""
            if not source_file or not Path(source_file).is_file():
                raise RuntimeError(
                    f"scene model root has no readable authoritative PMX: {source_file!r}"
                )
            expected_model_path = str(Path(source_file).resolve())
        expected_mesh_count = config.get("expectedMeshCount")
        if expected_mesh_count is not None and len(meshes) != expected_mesh_count:
            raise RuntimeError(
                f"source mesh count differs from required split fixture: "
                f"expected={expected_mesh_count} actual={len(meshes)}"
            )
        shaders, texture_bindings, material_contracts = _convert_imported_materials_to_dx11(
            cmds, meshes, expected_model_path
        )
        shader_files = sorted({str(Path(cmds.getAttr(shader + ".shader")).resolve()) for shader in shaders})
        if shader_files != [str(FX_PATH.resolve())]:
            raise RuntimeError(f"dx11Shader nodes do not use the repository MMDShader.fx: {shader_files}")

        report["counts"] = {
            "mesh": len(meshes),
            "dx11Shader": len(shaders),
            "mmdRenderShape": len(proxies),
            "geometryFilterBeforeProbe": len(cmds.ls(type="geometryFilter") or []),
        }
        report["route"]["mmdRenderShapeNodesPresent"] = len(proxies)
        report["shaderFiles"] = shader_files
        report["textureBindings"] = texture_bindings
        secondary_nodes = [
            plan["fileNode"]
            for binding in texture_bindings
            for plan in (binding["sphereTexture"], binding["toonTexture"])
            if plan.get("fileNode")
        ]
        report["secondaryTextureGraph"] = {
            "bindingCount": len(secondary_nodes),
            "uniqueFileNodeCount": len(set(secondary_nodes)),
            "reusedBindingCount": sum(
                bool(plan.get("reusedFileNode"))
                for binding in texture_bindings
                for plan in (binding["sphereTexture"], binding["toonTexture"])
            ),
            "canonicalProvenanceKeyUsed": True,
        }
        report["materialContracts"] = material_contracts
        report["materialUniformSync"] = {
            "verifiedShaderCount": len(material_contracts),
            "expectedShaderCount": len(shaders),
            "complete": len(material_contracts) == len(shaders),
        }
        if not root:
            raise RuntimeError("MMD model root is ambiguous; material morph parity cannot be verified")
        report["materialMorphParity"] = _rebind_material_morphs(cmds, root, shaders)
        if not report["materialMorphParity"]["contributionCount"]:
            report["notRun"]["materialMorphAnimation"] = (
                "not_run; scene has no material morph contributions"
            )
        model_sources = {expected_model_path}
        report["modelSourceArtifacts"] = [
            {
                "path": path,
                "sha256": sha256_file(Path(path)) if Path(path).is_file() else None,
                "status": "available" if Path(path).is_file() else "not_available",
            }
            for path in sorted(model_sources)
        ]
        discovered_model_hashes = [
            artifact["sha256"]
            for artifact in report["modelSourceArtifacts"]
            if artifact["sha256"]
        ]
        if len(discovered_model_hashes) == 1:
            report["hashes"]["discoveredModelArtifactSha256"] = (
                discovered_model_hashes[0]
            )
        report["mayaVersion"] = cmds.about(version=True)
        report["device"] = cmds.ogs(deviceInformation=True)
        report["evaluationMode"] = cmds.evaluationManager(query=True, mode=True)
        if "directx" not in str(report["device"]).lower() and "dx11" not in str(report["device"]).lower():
            raise RuntimeError(f"active VP2 device is not DirectX 11: {report['device']}")

        bounds = cmds.exactWorldBoundingBox(root or meshes)
        amplitude = max(bounds[3] - bounds[0], bounds[4] - bounds[1], 0.01) * 0.05
        handle = None

        for old_panel in cmds.getPanel(type="modelPanel"):
            cmds.deleteUI(old_panel, panel=True)
        window = cmds.window(widthHeight=(660, 540))
        pane = cmds.paneLayout()
        panel = cmds.modelPanel(parent=pane)
        cmds.showWindow(window)
        camera, _ = cmds.camera()
        cmds.setAttr(camera + ".translateZ", 10)
        cmds.modelEditor(
            panel,
            edit=True,
            rendererName="vp2Renderer",
            rendererOverrideName="",
            camera=camera,
            displayAppearance="smoothShaded",
            displayTextures=True,
            grid=False,
        )
        cmds.select(meshes)
        cmds.isolateSelect(panel, state=True)
        cmds.isolateSelect(panel, addSelected=True)
        cmds.viewFit(camera, fitFactor=0.9, animate=False)
        cmds.select(clear=True)
        cmds.setAttr("hardwareRenderingGlobals.multiSampleEnable", False)
        yield
        cmds.refresh(force=True)
        view = omui.M3dView.getM3dViewFromModelPanel(panel)
        report["viewportSize"] = [view.portWidth(), view.portHeight()]
        report["cameraMatrix"] = cmds.xform(camera, query=True, matrix=True, worldSpace=True)
        if len(cmds.getPanel(type="modelPanel")) != 1:
            raise RuntimeError("probe requires exactly one model panel")
        if cmds.modelEditor(panel, query=True, rendererOverrideName=True):
            raise RuntimeError("mmdOrdered or another renderer override is active")

        # DX11 effect and secondary-texture resources are prepared lazily per
        # material.  Finish that one-time work before capturing parity images
        # or starting the profiler-off playback interval measurement.
        shader_warmup_draws = max(4, len(shaders) * 2)
        for _ in range(shader_warmup_draws):
            cmds.refresh(force=True)
        report["shaderWarmup"] = {
            "forcedRefreshCount": shader_warmup_draws,
            "includedInPlaybackTiming": False,
        }

        if config.get("scene"):
            cmds.currentTime(config["frame"])
            yield
            first_state = _animation_state(cmds, meshes)
            baseline_path = capture_view(
                cmds, out / "baseline.png", panel, 640, 480, frame=config["frame"]
            )
            baseline = read_png_rgb(baseline_path)
            animation_frame = config["frame"] + config.get("animationProbeOffset", 1)
            cmds.currentTime(animation_frame)
            yield
            second_state = _animation_state(cmds, meshes)
            deformed_path = capture_view(
                cmds, out / "deformed.png", panel, 640, 480, frame=animation_frame
            )
            deformed = read_png_rgb(deformed_path)
            vertex_delta = _max_state_delta(
                first_state["sourceVertexWorld"], second_state["sourceVertexWorld"]
            )
            joint_delta = _max_state_delta(
                first_state["jointWorldMatrices"], second_state["jointWorldMatrices"]
            )
            if max(vertex_delta, joint_delta) <= 1e-7:
                raise RuntimeError(
                    "saved scene animation did not change source vertices or joint matrices "
                    f"between frames {config['frame']} and {animation_frame}"
                )
            report["savedSceneAnimationEvidence"] = {
                "startFrame": config["frame"],
                "comparisonFrame": animation_frame,
                "sourceVertexScalarCount": len(first_state["sourceVertexWorld"]),
                "jointMatrixScalarCount": len(first_state["jointWorldMatrices"]),
                "maxSourceVertexWorldDelta": vertex_delta,
                "maxJointWorldMatrixDelta": joint_delta,
                "status": "pass",
            }
            cmds.currentTime(config["frame"])
            yield
        else:
            components = [mesh + ".vtx[*]" for mesh in meshes]
            _, handle = cmds.cluster(components, relative=True)
            cmds.setAttr(handle + ".translateX", 0.0)
            report["controlledDeformation"] = {
                "type": "cluster", "meshCount": len(meshes), "amplitude": amplitude,
            }
            baseline_path = capture_view(cmds, out / "baseline.png", panel, 640, 480)
            baseline = read_png_rgb(baseline_path)
            cmds.setAttr(handle + ".translateX", amplitude)
            yield
            deformed_path = capture_view(cmds, out / "deformed.png", panel, 640, 480)
            deformed = read_png_rgb(deformed_path)
            cmds.setAttr(handle + ".translateX", 0.0)
            yield
        changed_pixels = _image_difference(baseline, deformed)
        if changed_pixels <= 100:
            raise RuntimeError(f"deformation changed too few pixels: {changed_pixels}")
        restored_frame = config["frame"] if config.get("scene") else 1
        restored_path = capture_view(
            cmds, out / "restored.png", panel, 640, 480, frame=restored_frame
        )
        restored = read_png_rgb(restored_path)
        if restored != baseline:
            raise RuntimeError("restored viewport pixels differ from baseline")
        report["captures"] = {
            "baseline": str(baseline_path),
            "deformed": str(deformed_path),
            "restored": str(restored_path),
            "baselineFrame": config["frame"] if config.get("scene") else 1,
            "deformedFrame": animation_frame if config.get("scene") else 1,
            "restoredFrame": restored_frame,
            "deformedChangedPixels": changed_pixels,
            "restoredPixelsEqual": True,
        }

        camera_x = cmds.getAttr(camera + ".translateX")

        def neutral() -> None:
            cmds.setAttr(camera + ".translateX", camera_x)
            if handle:
                cmds.setAttr(handle + ".translateX", 0.0)

        def step(scenario: str, frame: int) -> None:
            offset = math.sin((frame + 1) * 0.37) * amplitude
            if scenario == "camera":
                cmds.setAttr(camera + ".translateX", camera_x + offset)
            elif scenario == "deform" and handle:
                cmds.setAttr(handle + ".translateX", offset)
            cmds.refresh(force=True)

        # Artificial forced-refresh timings are optional and separate from playback.
        cmds.profiler(sampling=False)
        if config.get("controlledDeformation"):
            if not handle:
                components = [mesh + ".vtx[*]" for mesh in meshes]
                _, handle = cmds.cluster(components, relative=True)
                report["controlledDeformation"] = {
                    "type": "cluster", "meshCount": len(meshes), "amplitude": amplitude,
                }
            for repeat in range(config["repeats"]):
                for scenario in SCENARIOS:
                    neutral()
                    for frame in range(config["warmup"]):
                        step(scenario, frame)
                    durations = []
                    for frame in range(config["frames"]):
                        started = time.perf_counter()
                        step(scenario, frame + config["warmup"])
                        durations.append((time.perf_counter() - started) * 1000.0)
                    report["measurements"]["timingCases"].append({
                        "repeat": repeat, "scenario": scenario, "profilerSampling": False,
                        "frameMs": durations, "medianMs": statistics.median(durations),
                        "meanMs": statistics.mean(durations), "maxMs": max(durations),
                    })

        # Diagnostics are intentionally separate because Profiler changes timing.
        cmds.profiler(bufferSize=64)
        category_status = "not_available"
        try:
            cmds.profiler(categoryName="MMD Render", categoryRecording=True)
            category_status = "enabled"
        except RuntimeError as error:
            category_status = str(error)
        report["measurements"]["mmdProfilerCategory"] = category_status
        aggregate_forbidden = {name: 0 for name in FORBIDDEN_CPU_GEOMETRY_EVENTS}
        diagnostic_scenarios = SCENARIOS if handle else ("static", "camera")
        for scenario in diagnostic_scenarios:
            neutral()
            for frame in range(config["warmup"]):
                step(scenario, frame)
            cmds.profiler(reset=True)
            cmds.profiler(sampling=True)
            try:
                for frame in range(config["frames"]):
                    step(scenario, frame + config["warmup"])
            finally:
                cmds.profiler(sampling=False)
            events = profiler_events(cmds)
            forbidden = {
                name: int(events.get(name, {}).get("count", 0))
                for name in FORBIDDEN_CPU_GEOMETRY_EVENTS
            }
            for name, count in forbidden.items():
                aggregate_forbidden[name] += count
            if any(forbidden.values()):
                raise RuntimeError(f"custom CPU geometry event observed in {scenario}: {forbidden}")
            top_level = {
                name: value for name, value in events.items() if name.startswith("Vp2")
            }
            if not top_level:
                raise RuntimeError(f"no VP2 Profiler event observed for {scenario}")
            profile_path = out / f"profiler-{scenario}.txt"
            cmds.profiler(output=str(profile_path))
            report["measurements"]["profilerDiagnostics"].append(
                {
                    "scenario": scenario,
                    "profilerSampling": True,
                    "vp2TopLevelEvents": top_level,
                    "forbiddenCpuGeometryEventCounts": forbidden,
                    "rawProfile": str(profile_path),
                }
            )
        report["cpuGeometryEventCounts"] = aggregate_forbidden
        report["cpuGeometryEventsAbsent"] = not any(aggregate_forbidden.values())
        neutral()
        if config.get("playbackFrames", 0):
            report["notRun"].pop("nativePlaybackFps", None)
            yield from native_playback_steps(cmds, panel, config, report)
            report["performanceAcceptanceStatus"] = (
                "pass" if report["acceptance"]["sixteenMsPassed"] else "fail"
            )
        report["runnerStatus"] = "pass"
        report["status"] = "pass"
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        try:
            cmds.profiler(sampling=False)
        except Exception:
            pass
        if config.get("scene"):
            end_hash = sha256_file(Path(config["scene"]))
            start_hash = report.get("hashes", {}).get("sceneSha256")
            report["sceneArtifactIntegrity"] = {
                "startSha256": start_hash,
                "endSha256": end_hash,
                "unchanged": bool(start_hash) and start_hash == end_hash,
            }
            if start_hash and start_hash != end_hash:
                report["status"] = "fail"
                report["runnerStatus"] = "fail"
                report["error"] = "input scene artifact changed during isolated profiling"
        write_report(out / "report.json", report)
        (out / "probe.log").write_text(MARKER + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", choices=("2024", "2026"), default="2026")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", type=Path)
    source.add_argument("--scene", type=Path)
    parser.add_argument("--plugin", type=Path, help="Required with --scene")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=7761)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--frame", type=int, default=650, help="Saved-scene frame to profile")
    parser.add_argument(
        "--playback-frames",
        type=int,
        help="Native-playback intervals (default: 100 for --scene, disabled for --model)",
    )
    parser.add_argument("--playback-warmup", type=int, default=10)
    parser.add_argument("--animation-probe-offset", type=int, default=10)
    parser.add_argument(
        "--geometry-route",
        choices=("source-direct", "display-copy"),
        default="source-direct",
        help="Direct source floor or an extra Maya mesh consumer for product experiments",
    )
    parser.add_argument(
        "--controlled-deformation",
        action="store_true",
        help="Also run the artificial cluster forced-refresh probe",
    )
    parser.add_argument(
        "--disable-cache",
        action="store_true",
        help="Disable Maya's cache evaluator; required for 16ms acceptance measurements",
    )
    parser.add_argument(
        "--expected-mesh-count",
        type=int,
        help="Fail closed unless the source has this many ordinary meshes (use 48 for live-miku)",
    )
    parser.add_argument(
        "--evaluation", choices=("off", "serial", "parallel"), default="parallel"
    )
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    if not out.is_relative_to((ROOT / "build").resolve()):
        parser.error("--out-dir must be inside the repository build directory")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.scene and not args.plugin:
        parser.error("--scene requires --plugin")
    playback_frames = args.playback_frames
    if playback_frames is None:
        playback_frames = 100 if args.scene else 0
    expected_mesh_count = args.expected_mesh_count
    if args.scene and expected_mesh_count is None:
        expected_mesh_count = 48
    config = validate_config(
        {
            "model": str(args.model.resolve()) if args.model else None,
            "scene": str(args.scene.resolve()) if args.scene else None,
            "plugin": str(args.plugin.resolve()) if args.plugin else None,
            "output": str(out),
            "frames": args.frames,
            "warmup": args.warmup,
            "repeats": args.repeats,
            "evaluation": args.evaluation,
            "frame": args.frame,
            "playbackFrames": playback_frames,
            "playbackWarmup": args.playback_warmup,
            "animationProbeOffset": args.animation_probe_offset,
            "controlledDeformation": args.controlled_deformation or not bool(args.scene),
            "geometryRoute": args.geometry_route,
            "disableCache": args.disable_cache,
            "expectedMeshCount": expected_mesh_count,
            "separateMeshesByMaterial": True,
            "shaderBackend": "dx11",
            "createMmdShaders": True,
        }
    )
    out.mkdir(parents=True, exist_ok=True)
    config_path = out / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT,
        version=args.maya,
        out_dir=out,
        port=args.port,
        timeout=600,
        log_path=out / "probe.log",
        report_path=out / "report.json",
        command=(
            "from tools.render_override.vp2_native_geometry_profile import run_probe\n"
            f"run_probe({str(config_path)!r})"
        ),
        marker=MARKER,
        send_label="mmd-vp2-native-geometry-profile",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={
            "MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11",
            "MAYA_SKIP_USERSETUP_PY": "1",
            "MMD_TOOLS_SKIP_SHADER_OVERRIDE": "1",
            "MMD_TOOLS_CPP_ENABLE_ORDERED_RENDER": "0",
            **(
                {
                    "MMD_TOOLS_CPP_PLUGIN": str(args.plugin.resolve()),
                    "PATH": str(args.plugin.resolve().parent)
                    + os.pathsep
                    + os.environ.get("PATH", ""),
                }
                if args.plugin
                else {"PATH": os.environ.get("PATH", "")}
            ),
        },
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
