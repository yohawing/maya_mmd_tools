"""Focused Maya 2024 smoke for the native direct-spool VMD scalar sampler."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import struct
import tempfile
from typing import Any, Iterable, List


ROOT = Path(__file__).resolve().parents[2]


def _plugin_path() -> Path:
    """Resolve the built plugin for the selected Maya/config pair."""
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path
        raise FileNotFoundError(path)
    version = os.environ.get("MAYA_VERSION", "2024")
    config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Release")
    path = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _legacy_payload(frames: Iterable[float], channels: List[dict[str, str]]) -> str:
    """Serialize an intentionally unsupported pre-direct-spool request."""
    return json.dumps(
        {
            "version": 2,
            "evaluation_policy": "maya_timeline_bake_v1",
            "timing": "wall_v3",
            "frames": list(frames),
            "channels": channels,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _call(cmds: Any, payload: str) -> List[float]:
    """Call the command and normalize Maya's MDoubleArray result."""
    values = cmds.mmdVmdBatchSample(payload=payload)
    result = [float(value) for value in values]
    if any(not math.isfinite(value) for value in result):
        raise RuntimeError("native sampler returned a non-finite value")
    return result


def _direct_payload(
    frames: Iterable[float],
    channels: List[dict[str, str]],
    spool_path: str,
    spool_bytes: int,
    output_slots: List[int],
    output_defaults: List[float],
) -> str:
    """Serialize one full Prepare-scoped native direct-spool request."""
    return json.dumps(
        {
            "version": 2,
            "evaluation_policy": "maya_timeline_bake_v1",
            "timing": "wall_v3",
            "mode": "direct_spool",
            "frames": list(frames),
            "channels": channels,
            "spool_path": spool_path,
            "spool_bytes": spool_bytes,
            "output_channel_count": len(output_defaults),
            "output_slots": output_slots,
            "output_defaults": output_defaults,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _must_fail(cmds: Any, payload: str, label: str) -> None:
    """Assert malformed requests fail at the native command boundary."""
    try:
        _call(cmds, payload)
    except Exception:
        return
    raise RuntimeError(f"{label} request unexpectedly succeeded")


def _assert_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1.0e-6):
        raise RuntimeError(f"{label} mismatch: actual={actual!r}, expected={expected!r}")


def _collect_timeline_oracle(cmds: Any, joint: str, frames: Iterable[float]) -> List[dict]:
    """Read a simple joint through Maya Timeline for an independent oracle."""
    before_time = float(cmds.currentTime(query=True))
    result = []
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            translation = tuple(
                float(cmds.getAttr(f"{joint}.translate{axis}"))
                for axis in ("X", "Y", "Z")
            )
            rotate_z = float(cmds.getAttr(f"{joint}.rotateZ"))
            half_angle = math.radians(rotate_z) * 0.5
            result.append(
                {
                    "bone_name": joint.rsplit("|", 1)[-1],
                    "frame_number": int(round(float(frame))),
                    "position": (translation[0], translation[1], -translation[2]),
                    "rotation": (0.0, 0.0, math.sin(half_angle), math.cos(half_angle)),
                }
            )
    finally:
        cmds.currentTime(before_time, edit=True)
    return result


def _assert_bone_frame_matches(actual_frames: List[dict], expected_frames: List[dict]) -> None:
    """Compare native collector frames with the independent Timeline oracle."""
    if len(actual_frames) != len(expected_frames):
        raise RuntimeError(
            f"collector frame count mismatch: actual={len(actual_frames)}, "
            f"expected={len(expected_frames)}"
        )
    for index, (actual, expected) in enumerate(zip(actual_frames, expected_frames)):
        for key in ("bone_name", "frame_number"):
            if actual[key] != expected[key]:
                raise RuntimeError(f"collector frame {index} {key} mismatch")
        for key in ("position", "rotation"):
            if len(actual[key]) != len(expected[key]):
                raise RuntimeError(f"collector frame {index} {key} width mismatch")
            for component, (value, source) in enumerate(zip(actual[key], expected[key])):
                _assert_close(value, source, f"collector frame {index} {key}[{component}]")


def _light_direction(rotate_x: float, rotate_y: float) -> tuple[float, float, float]:
    """Convert the Maya light rotation independently of the collector."""
    rx = math.radians(rotate_x)
    ry = math.radians(rotate_y)
    cos_rx = math.cos(rx)
    return (
        -math.sin(ry) * cos_rx,
        math.sin(rx),
        math.cos(ry) * cos_rx,
    )


def _assert_sorted_frames(rows: List[dict], label: str) -> None:
    frame_numbers = [int(row["frame_number"]) for row in rows]
    if frame_numbers != sorted(frame_numbers):
        raise RuntimeError(f"{label} frames are not ascending: {frame_numbers!r}")


def _collect_nonbone_timeline_oracle(
    cmds: Any,
    blend_shape: str,
    camera: str,
    light: str,
    ik_solver: str,
    frames: Iterable[float],
) -> dict[str, dict[int, Any]]:
    """Evaluate the non-bone fixture through normal Maya Timeline reads."""
    entry_time = float(cmds.currentTime(query=True))
    oracle: dict[str, dict[int, Any]] = {
        "morph": {},
        "camera": {},
        "light": {},
        "ik": {},
    }
    try:
        for frame in frames:
            cmds.currentTime(frame, edit=True)
            frame_number = int(frame)
            oracle["morph"][frame_number] = float(
                cmds.getAttr(f"{blend_shape}.weight[0]")
            )
            oracle["camera"][frame_number] = {
                "position": (
                    float(cmds.getAttr(f"{camera}.translateX")),
                    float(cmds.getAttr(f"{camera}.translateY")),
                    -float(cmds.getAttr(f"{camera}.translateZ")),
                ),
                "rotation": (
                    math.radians(float(cmds.getAttr(f"{camera}.rotateX"))),
                    math.radians(float(cmds.getAttr(f"{camera}.rotateY"))),
                    -math.radians(float(cmds.getAttr(f"{camera}.rotateZ"))),
                ),
                "distance": float(
                    cmds.getAttr(f"{camera}.mmd_camera_distance")
                ),
                "viewing_angle": int(
                    round(cmds.getAttr(f"{camera}.mmd_camera_viewing_angle"))
                ),
                "perspective": int(
                    round(cmds.getAttr(f"{camera}.mmd_camera_perspective"))
                ),
            }
            light_rx = float(cmds.getAttr(f"{light}.rotateX"))
            light_ry = float(cmds.getAttr(f"{light}.rotateY"))
            oracle["light"][frame_number] = {
                "color": tuple(
                    float(cmds.getAttr(f"{light}.mmd_light_color{axis}"))
                    for axis in "RGB"
                ),
                "position": _light_direction(light_rx, light_ry),
            }
            oracle["ik"][frame_number] = bool(
                cmds.getAttr(f"{ik_solver}.enabled")
            )
    finally:
        cmds.currentTime(entry_time, edit=True)
    return oracle


def main() -> int:
    """Run direct, static, timed, Timeline-policy, and registration checks."""
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    # The render override owns a live VP2 operation in standalone Maya and
    # intentionally refuses teardown while that operation is active.  This
    # sampler smoke is registration/unload focused, so keep that unrelated
    # optional capability out of the process.
    os.environ.setdefault("MMD_TOOLS_CPP_SKIP_NATIVE_CASTER", "1")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    plugin_name = plugin_path.stem
    try:
        # Production Prepare does not pre-load the C++ plugin.  Verify the
        # gateway resolves the Maya-version-specific binary and registers the
        # command before the command-level checks below.
        from mmd_tools.adapters.native_vmd_batch_sampler import NativeVmdBatchSampler

        auto_sampler = NativeVmdBatchSampler(cmds)
        if not auto_sampler.available:
            raise RuntimeError(
                f"native sampler gateway did not auto-load: {auto_sampler.last_diagnostics!r}"
            )
        if auto_sampler.last_diagnostics.get("plugin_load_status") not in {
            "loaded",
            "already_loaded",
            "already_available",
        }:
            raise RuntimeError(
                f"unexpected auto-load evidence: {auto_sampler.last_diagnostics!r}"
            )
        if cmds.pluginInfo(plugin_name, query=True, loaded=True):
            cmds.unloadPlugin(plugin_name, force=True)

        cmds.loadPlugin(str(plugin_path), quiet=True)
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("mmd_tools_cpp did not report loaded")

        node = cmds.createNode("transform", name="focused_vmd_batch_sampler")
        cmds.addAttr(node, longName="directValue", attributeType="double", keyable=True)
        cmds.addAttr(node, longName="staticValue", attributeType="double", keyable=True)
        cmds.addAttr(node, longName="convertedValue", attributeType="double", keyable=True)
        cmds.setAttr(f"{node}.staticValue", 2.5)
        cmds.setKeyframe(node, attribute="rotateX", time=0.0, value=0.0)
        cmds.setKeyframe(node, attribute="rotateX", time=2.0, value=90.0)
        cmds.setKeyframe(node, attribute="translateX", time=0.0, value=1.0)
        cmds.setKeyframe(node, attribute="translateX", time=2.0, value=5.0)
        cmds.setKeyframe(node, attribute="directValue", time=0.0, value=10.0)
        cmds.setKeyframe(node, attribute="directValue", time=2.0, value=20.0)
        cmds.setKeyframe(node, attribute="convertedValue", time=0.0, value=3.0)
        cmds.setKeyframe(node, attribute="convertedValue", time=2.0, value=7.0)

        # Route a second custom value through unitConversion so the destination
        # is not a direct animCurve output and must use timed_mplug.
        source = cmds.createNode("transform", name="focused_vmd_batch_source")
        cmds.addAttr(source, longName="sourceValue", attributeType="double", keyable=True)
        cmds.setKeyframe(source, attribute="sourceValue", time=0.0, value=4.0)
        cmds.setKeyframe(source, attribute="sourceValue", time=2.0, value=8.0)
        conversion = cmds.createNode("unitConversion", name="focused_vmd_batch_conversion")
        cmds.setAttr(f"{conversion}.conversionFactor", 2.0)
        cmds.connectAttr(f"{source}.sourceValue", f"{conversion}.input", force=True)
        cmds.connectAttr(f"{conversion}.output", f"{node}.convertedValue", force=True)


        frames = [0.5, 1.25, 2.0]
        channels = [
            {"plug": f"{node}.rotateX", "unit": "angle", "hint": "direct_curve"},
            {"plug": f"{node}.translateX", "unit": "distance", "hint": "direct_curve"},
            {"plug": f"{node}.directValue", "unit": "scalar", "hint": "direct_curve"},
            {"plug": f"{node}.staticValue", "unit": "scalar", "hint": "static"},
            {"plug": f"{node}.convertedValue", "unit": "scalar", "hint": "timed_mplug"},
        ]
        cmds.currentTime(7.5, edit=True)
        before_time = float(cmds.currentTime(query=True))



        # The direct-spool protocol keeps the same Timeline semantics and
        # frame-major double layout, but resolves the route plan only once.
        expected_bytes = len(frames) * len(channels) * 8
        spool_fd, spool_path = tempfile.mkstemp(prefix="mmd_focused_direct_", suffix=".bin")
        try:
            os.ftruncate(spool_fd, expected_bytes)
            os.close(spool_fd)
            spool_fd = -1
            direct = _call(
                cmds,
                _direct_payload(
                    frames,
                    channels,
                    spool_path,
                    expected_bytes,
                    list(range(len(channels))),
                    [0.0] * len(channels),
                ),
            )
            if len(direct) != 27 or direct[:3] != [2.0, 3.0, 5.0]:
                raise RuntimeError(f"unexpected direct spool header: {direct!r}")
            direct_bytes = Path(spool_path).read_bytes()
            if len(direct_bytes) != expected_bytes:
                raise RuntimeError("direct spool byte size mismatch")
            spool_values = struct.unpack("=" + "d" * (len(frames) * len(channels)), direct_bytes)
            for frame_index, frame in enumerate(frames):
                expected = [
                    float(cmds.getAttr(f"{node}.rotateX", time=frame)),
                    float(cmds.getAttr(f"{node}.translateX", time=frame)),
                    float(cmds.getAttr(f"{node}.directValue", time=frame)),
                    float(cmds.getAttr(f"{node}.staticValue", time=frame)),
                    float(cmds.getAttr(f"{node}.convertedValue", time=frame)),
                ]
                offset = frame_index * len(channels)
                for channel_index, value in enumerate(expected):
                    _assert_close(
                        spool_values[offset + channel_index],
                        value,
                        f"direct spool frame {frame} channel {channel_index}",
                    )

            # A Morph-like scalar request made only of direct animCurves must
            # evaluate MFnAnimCurve at each requested time without advancing
            # Maya's global Timeline or triggering a DG frame evaluation.
            curve_frames = [0.25, 1.0, 1.75]
            curve_bytes = len(curve_frames) * 8
            curve_fd, curve_path = tempfile.mkstemp(
                prefix="mmd_focused_direct_curve_", suffix=".bin"
            )
            try:
                os.ftruncate(curve_fd, curve_bytes)
                os.close(curve_fd)
                curve_fd = -1
                curve_entry_time = float(cmds.currentTime(query=True))
                curve_ack = _call(
                    cmds,
                    _direct_payload(
                        curve_frames,
                        [
                            {
                                "plug": f"{node}.directValue",
                                "unit": "scalar",
                                "hint": "direct_curve",
                            }
                        ],
                        curve_path,
                        curve_bytes,
                        [0],
                        [0.0],
                    ),
                )
                if curve_ack[:6] != [2.0, 3.0, 1.0, 1.0, 0.0, 0.0]:
                    raise RuntimeError(
                        f"unexpected direct-curve header: {curve_ack[:6]!r}"
                    )
                _assert_close(curve_ack[6], 0.0, "direct-curve Timeline wall")
                _assert_close(
                    float(cmds.currentTime(query=True)),
                    curve_entry_time,
                    "direct-curve current time preservation",
                )
                curve_values = struct.unpack(
                    "=" + "d" * len(curve_frames),
                    Path(curve_path).read_bytes(),
                )
                for frame, value in zip(curve_frames, curve_values):
                    _assert_close(
                        value,
                        float(cmds.getAttr(f"{node}.directValue", time=frame)),
                        f"direct-curve value {frame}",
                    )
            finally:
                if curve_fd >= 0:
                    os.close(curve_fd)
                try:
                    os.unlink(curve_path)
                except FileNotFoundError:
                    pass

            # A fully static physics request has no native channels after the
            # Python-side compatibility split.  Direct mode still owns the
            # complete frame-major output through output_defaults.
            static_frames = [0.0, 1.0]
            static_defaults = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
            static_expected_bytes = len(static_frames) * len(static_defaults) * 8
            static_fd, static_path = tempfile.mkstemp(
                prefix="mmd_focused_direct_static_", suffix=".bin"
            )
            try:
                os.ftruncate(static_fd, static_expected_bytes)
                os.close(static_fd)
                static_fd = -1
                static_ack = _call(
                    cmds,
                    _direct_payload(
                        static_frames,
                        [],
                        static_path,
                        static_expected_bytes,
                        [],
                        static_defaults,
                    ),
                )
                if static_ack[:6] != [2.0, 2.0, 6.0, 0.0, 0.0, 0.0]:
                    raise RuntimeError(
                        f"unexpected all-static direct header: {static_ack[:6]!r}"
                    )
                static_values = struct.unpack(
                    "=" + "d" * (len(static_frames) * len(static_defaults)),
                    Path(static_path).read_bytes(),
                )
                expected_static = tuple(static_defaults) * len(static_frames)
                if static_values != expected_static:
                    raise RuntimeError(
                        f"all-static direct spool mismatch: {static_values!r}"
                    )
            finally:
                if static_fd >= 0:
                    os.close(static_fd)
                try:
                    os.unlink(static_path)
                except FileNotFoundError:
                    pass

            long_frames = list(range(121))
            long_expected_bytes = len(long_frames) * len(channels) * 8
            long_fd, long_path = tempfile.mkstemp(prefix="mmd_focused_direct_long_", suffix=".bin")
            try:
                os.ftruncate(long_fd, long_expected_bytes)
                os.close(long_fd)
                long_fd = -1
                long_ack = _call(
                    cmds,
                    _direct_payload(
                        long_frames,
                        channels,
                        long_path,
                        long_expected_bytes,
                        list(range(len(channels))),
                        [0.0] * len(channels),
                    ),
                )
                if len(long_ack) != 37 or long_ack[15:17] != [1.0, 2.0]:
                    raise RuntimeError("direct spool checkpoint acknowledgement mismatch")
            finally:
                if long_fd >= 0:
                    os.close(long_fd)
                try:
                    os.unlink(long_path)
                except FileNotFoundError:
                    pass
        finally:
            if spool_fd >= 0:
                os.close(spool_fd)
            try:
                os.unlink(spool_path)
            except FileNotFoundError:
                pass

        _assert_close(
            float(cmds.currentTime(query=True)),
            before_time,
            "timeline current time preservation",
        )

        # Complete transform translate/rotate triples are eligible for the
        # native compound path.  Direct-spool output stays frame-major in the
        # requested order while the acknowledgement reports native coverage.
        compound_channels = [
            {"plug": f"{node}.translate{axis}", "unit": "distance", "hint": "timed_mplug"}
            for axis in ("X", "Y", "Z")
        ] + [
            {"plug": f"{node}.rotate{axis}", "unit": "angle", "hint": "timed_mplug"}
            for axis in ("X", "Y", "Z")
        ]



        # Regression: compound values must be refreshed for every frame.  A
        # previous direct-spool implementation accidentally reused the first
        # frame's compound tuple for the remainder of its 120-frame checkpoint.
        compound_spool_bytes = len(frames) * len(compound_channels) * 8
        compound_spool_fd, compound_spool_path = tempfile.mkstemp(
            prefix="mmd_focused_compound_direct_", suffix=".bin"
        )
        try:
            os.ftruncate(compound_spool_fd, compound_spool_bytes)
            os.close(compound_spool_fd)
            compound_spool_fd = -1
            direct_compound = _call(
                cmds,
                _direct_payload(
                    frames,
                    compound_channels,
                    compound_spool_path,
                    compound_spool_bytes,
                    list(range(len(compound_channels))),
                    [0.0] * len(compound_channels),
                ),
            )
            if len(direct_compound) != 27 or direct_compound[15:17] != [1.0, 1.0]:
                raise RuntimeError("compound direct-spool checkpoint acknowledgement mismatch")
            direct_compound_values = struct.unpack(
                "=" + "d" * (len(frames) * len(compound_channels)),
                Path(compound_spool_path).read_bytes(),
            )
            for frame_index, frame in enumerate(frames):
                expected = [
                    float(cmds.getAttr(f"{node}.translate{axis}", time=frame))
                    for axis in ("X", "Y", "Z")
                ] + [
                    float(cmds.getAttr(f"{node}.rotate{axis}", time=frame))
                    for axis in ("X", "Y", "Z")
                ]
                offset = frame_index * len(compound_channels)
                for channel_index, value in enumerate(expected):
                    _assert_close(
                        direct_compound_values[offset + channel_index],
                        value,
                        f"compound direct frame {frame} channel {channel_index}",
                    )

        finally:
            if compound_spool_fd >= 0:
                os.close(compound_spool_fd)
            try:
                os.unlink(compound_spool_path)
            except FileNotFoundError:
                pass

        legacy_request = json.loads(_legacy_payload(frames, channels))
        legacy_request.pop("evaluation_policy")
        _must_fail(
            cmds,
            json.dumps(legacy_request, separators=(",", ":"), ensure_ascii=False),
            "missing evaluation policy",
        )
        legacy_request["evaluation_mode"] = "timeline_probe"
        _must_fail(
            cmds,
            json.dumps(legacy_request, separators=(",", ":"), ensure_ascii=False),
            "legacy evaluation mode",
        )


        # Regression: a dependency node can enter through two different
        # source plugs (compound parent plus child).  One branch is fed by a
        # physics node; traversal must not mark the shared transform node as
        # visited after inspecting only the other branch.
        physics = cmds.createNode(
            "mmdPhysicsBoneDriver",
            name="focused_vmd_physics_branch",
        )
        shared = cmds.createNode("transform", name="focused_vmd_shared_branch")
        safe = cmds.createNode("transform", name="focused_vmd_safe_branch")
        target = cmds.createNode("transform", name="focused_vmd_physics_target")
        cmds.connectAttr(
            f"{physics}.outTranslateX",
            f"{shared}.translateY",
            force=True,
        )
        cmds.connectAttr(
            f"{safe}.translateX",
            f"{shared}.translateX",
            force=True,
        )
        cmds.connectAttr(f"{shared}.translate", f"{target}.translate", force=True)
        cmds.connectAttr(
            f"{shared}.translateX",
            f"{target}.translateX",
            force=True,
        )
        target_child_sources = cmds.listConnections(
            f"{target}.translateX",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        target_parent_sources = cmds.listConnections(
            f"{target}.translate",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        if not target_child_sources or not target_parent_sources:
            raise RuntimeError(
                "physics branch regression graph did not preserve child and parent sources"
            )
        # Exercise the production collector seam.  Build the expected values
        # independently through Maya Timeline/currentTime + getAttr; the old
        # Python timed evaluator is deliberately not used as an oracle.
        from mmd_tools.adapters.native_vmd_batch_sampler import NativeVmdBatchSampler
        from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector

        joint = cmds.createNode("joint", name="focused_vmd_batch_joint")
        cmds.setKeyframe(joint, attribute="translateX", time=0.0, value=0.0)
        cmds.setKeyframe(joint, attribute="translateX", time=2.0, value=4.0)
        cmds.setKeyframe(joint, attribute="rotateZ", time=0.0, value=-10.0)
        cmds.setKeyframe(joint, attribute="rotateZ", time=2.0, value=30.0)
        collector_frames = [0.0, 1.0, 2.0]
        collector_kwargs = {
            "joints": [joint],
            "start_frame": 0.0,
            "end_frame": 2.0,
            "dense_sample": True,
            "force_dense_sample": True,
            "dense_frame_samples": collector_frames,
            "time_converter": lambda value: int(value),
        }
        oracle_frames = _collect_timeline_oracle(cmds, joint, collector_frames)
        native_collector = VmdSceneCollector(
            bone_channel_sampler=NativeVmdBatchSampler(cmds)
        )
        native_frames = native_collector.collect_bone_frames(
            **collector_kwargs,
            bone_channel_sampler=native_collector._bone_channel_sampler,
        )
        _assert_bone_frame_matches(native_frames, oracle_frames)
        native_evidence = native_collector.diagnostics.get("native_sampler", {})
        if not native_evidence.get("used"):
            raise RuntimeError(f"collector did not use native sampler: {native_evidence!r}")
        _assert_close(
            float(cmds.currentTime(query=True)),
            before_time,
            "collector current time preservation",
        )

        # Exercise all non-bone Bake Timeline tracks without Prepare/ExportWorkflow.
        # The independent oracle uses only currentTime + current-frame getAttr.
        cmds.currentUnit(time="ntsc")
        model_root = cmds.group(empty=True, name="focused_vmd_bake_timeline_root")
        cmds.parent(joint, model_root)

        base_mesh, _base_shape = cmds.polyCube(name="focused_vmd_morph_base")
        target_mesh, _target_shape = cmds.polyCube(name="focused_vmd_morph_target")
        cmds.parent(base_mesh, model_root)
        cmds.parent(target_mesh, model_root)
        blend_shape = cmds.blendShape(
            target_mesh,
            base_mesh,
            name="focused_vmd_bake_timeline_blendShape",
        )[0]
        cmds.addAttr(
            blend_shape,
            longName="mmd_blendshape_morph_names_json",
            dataType="string",
        )
        cmds.setAttr(
            f"{blend_shape}.mmd_blendshape_morph_names_json",
            '{"0":"smile"}',
            type="string",
        )
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=0.0, value=0.0)
        cmds.setKeyframe(blend_shape, attribute="weight[0]", time=2.0, value=1.0)

        camera, _camera_shape = cmds.camera(name="focused_vmd_bake_timeline_camera")
        for attr, attr_type in (
            ("mmd_camera_distance", "double"),
            ("mmd_camera_viewing_angle", "double"),
            ("mmd_camera_perspective", "long"),
        ):
            cmds.addAttr(camera, longName=attr, attributeType=attr_type, keyable=True)
        camera_keys = {
            "translateX": (0.0, 2.0),
            "translateY": (1.0, 3.0),
            "translateZ": (-2.0, -4.0),
            "rotateX": (0.0, 10.0),
            "rotateY": (0.0, 20.0),
            "rotateZ": (0.0, -30.0),
            "mmd_camera_distance": (-10.0, -20.0),
            "mmd_camera_viewing_angle": (40.0, 50.0),
            "mmd_camera_perspective": (0.0, 1.0),
        }
        for attr, (start_value, end_value) in camera_keys.items():
            cmds.setKeyframe(camera, attribute=attr, time=0.0, value=start_value)
            cmds.setKeyframe(camera, attribute=attr, time=2.0, value=end_value)

        light = cmds.group(empty=True, name="focused_vmd_bake_timeline_light")
        cmds.addAttr(
            light,
            longName="mmd_light_color",
            usedAsColor=True,
            attributeType="float3",
        )
        for axis, values in zip("RGB", ((0.1, 0.3), (0.2, 0.4), (0.3, 0.5))):
            attr = f"mmd_light_color{axis}"
            cmds.addAttr(
                light,
                longName=attr,
                attributeType="float",
                parent="mmd_light_color",
                keyable=True,
            )
            cmds.setKeyframe(light, attribute=attr, time=0.0, value=values[0])
            cmds.setKeyframe(light, attribute=attr, time=2.0, value=values[1])
        for attr, values in (("rotateX", (0.0, 20.0)), ("rotateY", (0.0, 40.0))):
            cmds.setKeyframe(light, attribute=attr, time=0.0, value=values[0])
            cmds.setKeyframe(light, attribute=attr, time=2.0, value=values[1])

        ik_solver = cmds.createNode("mmdCcdIk", name="focused_vmd_bake_timeline_ik")
        cmds.addAttr(ik_solver, longName="mmd_ik_bone_name", dataType="string")
        cmds.setAttr(
            f"{ik_solver}.mmd_ik_bone_name", "left leg IK", type="string"
        )
        cmds.addAttr(ik_solver, longName="owner_joint", attributeType="message")
        cmds.connectAttr(f"{joint}.message", f"{ik_solver}.owner_joint")
        cmds.setKeyframe(ik_solver, attribute="enabled", time=0.0, value=1.0)
        cmds.setKeyframe(ik_solver, attribute="enabled", time=2.0, value=0.0)

        entry_time = 7.0
        cmds.currentTime(entry_time, edit=True)
        nonbone_oracle = _collect_nonbone_timeline_oracle(
            cmds,
            blend_shape,
            camera,
            light,
            ik_solver,
            collector_frames,
        )
        bake_timeline_collector = VmdSceneCollector(
            bone_channel_sampler=NativeVmdBatchSampler(cmds)
        )
        class _ProbeSink:
            def __init__(self):
                self.frames = []

            def begin_section(self, _section):
                return None

            def write_frame(self, section, frame):
                self.frames.append((section, frame))

        probe_sink = _ProbeSink()
        bake_timeline_collector.collect_to_sink(
            {
                "target_model": model_root,
                "joints": [joint],
                "blend_shapes": [blend_shape],
                "cameras": [camera],
                "lights": [light],
                "export_strategy": "bake_timeline",
                "frame_range": (0.0, 2.0),
            },
            probe_sink,
        )
        collected = {
            "morph_frames": [
                frame for section, frame in probe_sink.frames if section == "morphs"
            ],
            "camera_frames": [
                frame for section, frame in probe_sink.frames if section == "cameras"
            ],
            "light_frames": [
                frame for section, frame in probe_sink.frames if section == "lights"
            ],
            "ik_show_hide_frames": [
                frame for section, frame in probe_sink.frames if section == "ik"
            ],
        }
        _assert_close(
            float(cmds.currentTime(query=True)),
            entry_time,
            "Bake Timeline non-bone current time preservation",
        )
        expected_counts = {
            "morph_frames": 3,
            "camera_frames": 0,
            "light_frames": 0,
            "ik_show_hide_frames": 2,
        }
        for section in (
            "morph_frames",
            "camera_frames",
            "light_frames",
            "ik_show_hide_frames",
        ):
            if len(collected[section]) != expected_counts[section]:
                raise RuntimeError(
                    f"Bake Timeline {section} count mismatch: {len(collected[section])}"
                )
            _assert_sorted_frames(collected[section], section)

        for row in collected["morph_frames"]:
            _assert_close(
                float(row["weight"]),
                nonbone_oracle["morph"][int(row["frame_number"])],
                f"Bake Timeline morph frame {row['frame_number']}",
            )
        for row in collected["camera_frames"]:
            expected = nonbone_oracle["camera"][int(row["frame_number"])]
            for field in ("position", "rotation"):
                for component, (actual, source) in enumerate(
                    zip(row[field], expected[field])
                ):
                    _assert_close(
                        float(actual),
                        float(source),
                        f"Bake Timeline camera {field}[{component}]",
                    )
            _assert_close(
                float(row["distance"]),
                float(expected["distance"]),
                "Bake Timeline camera distance",
            )
            for field in ("viewing_angle", "perspective"):
                if int(row[field]) != int(expected[field]):
                    raise RuntimeError(f"Bake Timeline camera {field} mismatch")
        for row in collected["light_frames"]:
            expected = nonbone_oracle["light"][int(row["frame_number"])]
            for field in ("color", "position"):
                for component, (actual, source) in enumerate(
                    zip(row[field], expected[field])
                ):
                    _assert_close(
                        float(actual),
                        float(source),
                        f"Bake Timeline light {field}[{component}]",
                    )
        unsupported_sections = bake_timeline_collector.diagnostics.get(
            "unsupported_bake_timeline_sections", {}
        )
        if unsupported_sections != {"cameras": 1, "lights": 1}:
            raise RuntimeError(
                f"Bake Timeline unsupported section evidence mismatch: {unsupported_sections!r}"
            )
        native_morph = bake_timeline_collector.diagnostics.get(
            "native_morph_sampler", {}
        )
        if native_morph.get("strategy_counts", {}).get("direct_curve") != 1:
            raise RuntimeError(
                f"Bake Timeline Morph did not use direct curve: {native_morph!r}"
            )
        _assert_close(
            float(native_morph.get("set_current_time_wall_sec", -1.0)),
            0.0,
            "Bake Timeline direct Morph Timeline wall",
        )
        for row in collected["ik_show_hide_frames"]:
            frame_number = int(row["frame_number"])
            expected_state = nonbone_oracle["ik"][frame_number]
            if row["ik_states"] != [("left leg IK", expected_state)]:
                raise RuntimeError(
                    f"Bake Timeline IK frame {frame_number} mismatch: {row!r}"
                )
        print("OK: Bake Timeline native Morph/IK parity; camera/light unsupported")

        _must_fail(
            cmds,
            '{"version":1,"frames":[0],"frames":[1],"channels":[]}',
            "duplicate field",
        )
        cmds.unloadPlugin(plugin_name, force=True)
        if cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("mmd_tools_cpp did not unload")
        cmds.loadPlugin(str(plugin_path), quiet=True)
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("mmd_tools_cpp did not reload")
        from mmd_tools.adapters.native_vmd_batch_sampler import NativeVmdBatchSampler

        reloaded_samples = NativeVmdBatchSampler(cmds).sample_dense_bone_channels(
            [0.0], [node]
        )
        reloaded_samples.close()
        print("OK: focused mmdVmdBatchSample Timeline/direct/static/timed/protocol/reload")
        return 0
    finally:
        try:
            if cmds.pluginInfo(plugin_name, query=True, loaded=True):
                cmds.unloadPlugin(plugin_name, force=True)
        finally:
            maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
