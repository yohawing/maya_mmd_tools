"""Focused Maya 2024 smoke for the native packed VMD scalar sampler."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
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


def _payload(frames: Iterable[float], channels: List[dict[str, str]]) -> str:
    """Serialize with compact separators so Maya receives one argument."""
    return json.dumps(
        {"version": 1, "frames": list(frames), "channels": channels},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _call(cmds: Any, payload: str) -> List[float]:
    """Call the command and normalize Maya's MDoubleArray result."""
    values = cmds.mmdVmdBatchSample(payload=payload)
    result = [float(value) for value in values]
    if any(not math.isfinite(value) for value in result):
        raise RuntimeError("native sampler returned a non-finite packed value")
    return result


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


def _assert_bone_frame_parity(native_frames: List[dict], python_frames: List[dict]) -> None:
    """Compare final collector frames, not just the packed scalar transport."""

    if len(native_frames) != len(python_frames):
        raise RuntimeError(
            f"collector frame count mismatch: native={len(native_frames)}, "
            f"python={len(python_frames)}"
        )
    for index, (native, python) in enumerate(zip(native_frames, python_frames)):
        for key in ("bone_name", "frame_number"):
            if native[key] != python[key]:
                raise RuntimeError(f"collector frame {index} {key} mismatch")
        for key in ("position", "rotation"):
            if len(native[key]) != len(python[key]):
                raise RuntimeError(f"collector frame {index} {key} width mismatch")
            for component, (actual, expected) in enumerate(zip(native[key], python[key])):
                _assert_close(actual, expected, f"collector frame {index} {key}[{component}]")


def main() -> int:
    """Run direct, static, timed, protocol, and registration checks."""
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

        # An unconnected computed output is numeric but not a safe static
        # input.  A hostile/incorrect hint must be downgraded to timed MPlug.
        computed = cmds.createNode("plusMinusAverage", name="focused_vmd_batch_computed")
        cmds.setAttr(f"{computed}.input1D[0]", 6.0)
        computed_result = _call(
            cmds,
            _payload(
                [0.0],
                [{"plug": f"{computed}.output1D", "unit": "scalar", "hint": "static"}],
            ),
        )
        if computed_result[:6] != [1.0, 1.0, 1.0, 0.0, 0.0, 1.0]:
            raise RuntimeError(f"computed output was accepted as static: {computed_result[:6]!r}")
        _assert_close(computed_result[6], 6.0, "computed timed fallback")

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
        packed = _call(cmds, _payload(frames, channels))
        after_time = float(cmds.currentTime(query=True))
        _assert_close(after_time, before_time, "current time preservation")

        if packed[:6] != [1.0, 3.0, 5.0, 3.0, 1.0, 1.0]:
            raise RuntimeError(f"unexpected packed header: {packed[:6]!r}")
        if len(packed) != 6 + len(frames) * len(channels):
            raise RuntimeError(f"unexpected packed length: {len(packed)}")
        for frame_index, frame in enumerate(frames):
            expected = [
                float(cmds.getAttr(f"{node}.rotateX", time=frame)),
                float(cmds.getAttr(f"{node}.translateX", time=frame)),
                float(cmds.getAttr(f"{node}.directValue", time=frame)),
                float(cmds.getAttr(f"{node}.staticValue", time=frame)),
                float(cmds.getAttr(f"{node}.convertedValue", time=frame)),
            ]
            offset = 6 + frame_index * len(channels)
            for channel_index, value in enumerate(expected):
                _assert_close(packed[offset + channel_index], value, f"frame {frame} channel {channel_index}")

        timeline_request = json.loads(_payload(frames, channels))
        timeline_request["evaluation_mode"] = "timeline_probe"
        timeline_packed = _call(
            cmds,
            json.dumps(timeline_request, separators=(",", ":"), ensure_ascii=False),
        )
        if timeline_packed != packed:
            raise RuntimeError("timeline_probe packed values differ from context values")
        _assert_close(
            float(cmds.currentTime(query=True)),
            before_time,
            "timeline_probe current time preservation",
        )
        timeline_request["evaluation_mode"] = "timeline"
        _must_fail(
            cmds,
            json.dumps(timeline_request, separators=(",", ":"), ensure_ascii=False),
            "unsupported evaluation mode",
        )

        # Exercise the production collector seam.  The native path must yield
        # the same final VMD position/quaternion dictionaries as the existing
        # timed Python evaluator, while leaving the current Maya time alone.
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
        python_frames = VmdSceneCollector().collect_bone_frames(**collector_kwargs)
        native_collector = VmdSceneCollector(
            bone_channel_sampler=NativeVmdBatchSampler(cmds)
        )
        native_frames = native_collector.collect_bone_frames(
            **collector_kwargs,
            bone_channel_sampler=native_collector._bone_channel_sampler,
        )
        _assert_bone_frame_parity(native_frames, python_frames)
        native_evidence = native_collector.diagnostics.get("native_sampler", {})
        if not native_evidence.get("used"):
            raise RuntimeError(f"collector did not use native sampler: {native_evidence!r}")
        _assert_close(
            float(cmds.currentTime(query=True)),
            before_time,
            "collector current time preservation",
        )

        _must_fail(
            cmds,
            '{"version":1,"frames":[0],"frames":[1],"channels":[]}',
            "duplicate field",
        )
        _must_fail(
            cmds,
            _payload([0.0], [{"plug": f"{node}.missing", "unit": "scalar", "hint": "timed_mplug"}]),
            "missing plug",
        )
        oversized_frames = (float(index) for index in range(2_097_153))
        _must_fail(cmds, _payload(oversized_frames, channels[:2]), "oversized sample count")

        cmds.unloadPlugin(plugin_name, force=True)
        if cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("mmd_tools_cpp did not unload")
        cmds.loadPlugin(str(plugin_path), quiet=True)
        if not cmds.pluginInfo(plugin_name, query=True, loaded=True):
            raise RuntimeError("mmd_tools_cpp did not reload")
        if _call(cmds, _payload([0.0], [channels[0]]))[:3] != [1.0, 1.0, 1.0]:
            raise RuntimeError("sampler command was not registered after reload")
        print("OK: focused mmdVmdBatchSample direct/static/timed/protocol/reload")
        return 0
    finally:
        try:
            if cmds.pluginInfo(plugin_name, query=True, loaded=True):
                cmds.unloadPlugin(plugin_name, force=True)
        finally:
            maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
