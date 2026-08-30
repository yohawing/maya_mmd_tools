"""Legacy runtime cache scene-application helpers for VMD conversion."""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import maya.cmds as cmds


def apply_runtime_cache_to_scene(converter, runtime_cache: List[dict], pmx_morph_names: List[str]) -> None:
    """Apply cached runtime frame dictionaries to Maya scene animation curves."""
    if not runtime_cache:
        return

    if converter.bone_index_to_joint:
        per_joint_channels: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}

        for fd in runtime_cache:
            f = fd["frame"]
            for bidx, (tx, ty, tz, rx, ry, rz) in fd.get("bone_locals", {}).items():
                jname = converter.bone_index_to_joint.get(bidx)
                if not jname:
                    continue
                tx, ty, tz = converter._scale_motion_translate_from_bind(jname, tx, ty, tz)
                if jname not in per_joint_channels:
                    per_joint_channels[jname] = {
                        "translateX": [],
                        "translateY": [],
                        "translateZ": [],
                        "rotateX": [],
                        "rotateY": [],
                        "rotateZ": [],
                    }
                chans = per_joint_channels[jname]
                chans["translateX"].append((f, tx))
                chans["translateY"].append((f, ty))
                chans["translateZ"].append((f, tz))
                chans["rotateX"].append((f, math.radians(rx)))
                chans["rotateY"].append((f, math.radians(ry)))
                chans["rotateZ"].append((f, math.radians(rz)))

        total_channels = 0
        keyed_channels = 0
        skipped_static_channels = 0
        for jname, chans in per_joint_channels.items():
            try:
                dynamic_chans = {}
                for attr, samples in chans.items():
                    total_channels += 1
                    if converter._is_static_channel(samples):
                        skipped_static_channels += 1
                        if samples:
                            try:
                                value = float(samples[0][1])
                                if "rotate" in attr:
                                    value = math.degrees(value)
                                cmds.setAttr(f"{jname}.{attr}", value)
                            except Exception:
                                pass
                        continue
                    dynamic_chans[attr] = samples

                if dynamic_chans:
                    keyed_channels += len(dynamic_chans)
                    converter._batch_create_and_key_curves(jname, dynamic_chans)
            except Exception as e:
                converter.logger.debug(f"batch keying error for {jname} (will have used fallbacks): {e}")
        converter.logger.debug(
            "runtime joint channel pruning: "
            f"keyed={keyed_channels}, skipped_static={skipped_static_channels}, "
            f"total={total_channels}"
        )

    morph_cache = [(int(fd["frame"]), list(fd.get("morph_weights", []))) for fd in runtime_cache]
    converter._bake_morph_weight_cache_from_runtime(morph_cache, pmx_morph_names)

    converter.logger.info(f"Applied runtime cache: keyed {len(runtime_cache)} frames")


def scale_motion_translate_from_bind(converter, joint: str, tx: float, ty: float, tz: float) -> Tuple[float, float, float]:
    """Return runtime local translation; VMD track scaling happens pre-evaluation."""
    return float(tx), float(ty), float(tz)


def is_static_channel(samples: List[Tuple[float, float]], tolerance: float = 1e-10) -> bool:
    """Return True when all sampled values are equivalent within tolerance."""
    if len(samples) <= 1:
        return True
    first = float(samples[0][1])
    return all(abs(float(value) - first) <= tolerance for _, value in samples[1:])
