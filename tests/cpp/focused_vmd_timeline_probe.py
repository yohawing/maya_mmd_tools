"""Bounded Maya 2024 A/B probe for context versus normal-timeline sampling.

Run one strategy and one prefix per fresh mayapy process.  The output JSON can
then be paired by ``prefix_frames``; keeping the processes separate avoids DG
and stateful-node warmup from contaminating the other strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PREFIX_CHOICES = (120, 300, 600)


def _plugin_path() -> Path:
    explicit = os.environ.get("MMD_TOOLS_CPP_PLUGIN")
    if explicit:
        path = Path(explicit)
    else:
        version = os.environ.get("MAYA_VERSION", "2024")
        config = os.environ.get("MMD_TOOLS_CPP_CONFIG", "Release")
        path = ROOT / "plug-ins" / version / config / "mmd_tools_cpp.mll"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evaluation-mode",
        required=True,
        choices=("context", "timeline_probe"),
    )
    parser.add_argument(
        "--prefix-frames",
        required=True,
        type=int,
        choices=PREFIX_CHOICES,
    )
    parser.add_argument("--out", type=Path)
    return parser.parse_args()


def _payload(mode: str, frame_count: int, channels: list[dict[str, str]]) -> str:
    request: dict[str, Any] = {
        "version": 1,
        "frames": [float(frame) for frame in range(frame_count)],
        "channels": channels,
    }
    if mode == "timeline_probe":
        request["evaluation_mode"] = mode
    return json.dumps(request, separators=(",", ":"), ensure_ascii=False)


def _packed_hash(values: list[float]) -> str:
    canonical = json.dumps(values, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def main() -> int:
    args = _arguments()
    import maya.cmds as cmds
    import maya.standalone

    plugin_path = _plugin_path()
    os.environ["PATH"] = str(plugin_path.parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("MMD_TOOLS_CPP_SKIP_NATIVE_CASTER", "1")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(plugin_path.parent))

    maya.standalone.initialize(name="python")
    plugin_name = plugin_path.stem
    try:
        cmds.loadPlugin(str(plugin_path), quiet=True)
        source = cmds.createNode("transform", name="timeline_probe_source")
        target = cmds.createNode("transform", name="timeline_probe_target")
        cmds.addAttr(source, longName="sourceValue", attributeType="double", keyable=True)
        cmds.addAttr(target, longName="timedValue", attributeType="double", keyable=True)
        cmds.setKeyframe(source, attribute="sourceValue", time=0.0, value=-2.0)
        cmds.setKeyframe(
            source,
            attribute="sourceValue",
            time=float(args.prefix_frames - 1),
            value=8.0,
        )
        conversion = cmds.createNode("unitConversion", name="timeline_probe_conversion")
        cmds.setAttr(f"{conversion}.conversionFactor", 1.75)
        cmds.connectAttr(f"{source}.sourceValue", f"{conversion}.input", force=True)
        cmds.connectAttr(f"{conversion}.output", f"{target}.timedValue", force=True)
        channels = [
            {
                "plug": f"{target}.timedValue",
                "unit": "scalar",
                "hint": "timed_mplug",
            },
            {
                "plug": f"{source}.sourceValue",
                "unit": "scalar",
                "hint": "direct_curve",
            },
        ]
        cmds.currentTime(17.25, edit=True)
        entry_time = float(cmds.currentTime(query=True))
        payload = _payload(args.evaluation_mode, args.prefix_frames, channels)
        started = time.perf_counter()
        packed = [float(value) for value in cmds.mmdVmdBatchSample(payload=payload)]
        wall_sec = time.perf_counter() - started
        restored_time = float(cmds.currentTime(query=True))
        if not math.isclose(restored_time, entry_time, rel_tol=0.0, abs_tol=1.0e-9):
            raise RuntimeError(
                f"current time was not restored: entry={entry_time}, actual={restored_time}"
            )
        if packed[:6] != [1.0, float(args.prefix_frames), 2.0, 1.0, 0.0, 1.0]:
            raise RuntimeError(f"unexpected packed header: {packed[:6]!r}")
        expected_length = 6 + args.prefix_frames * len(channels)
        if len(packed) != expected_length or any(not math.isfinite(value) for value in packed):
            raise RuntimeError("invalid packed result")
        result = {
            "schema_version": 1,
            "evaluation_mode": args.evaluation_mode,
            "prefix_frames": args.prefix_frames,
            "wall_sec": round(wall_sec, 6),
            "mean_wall_sec_per_frame": round(wall_sec / args.prefix_frames, 9),
            "packed_sha256": _packed_hash(packed),
            "packed_header": packed[:6],
            "entry_time": entry_time,
            "restored_time": restored_time,
        }
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0
    finally:
        try:
            if cmds.pluginInfo(plugin_name, query=True, loaded=True):
                cmds.unloadPlugin(plugin_name, force=True)
        finally:
            maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
