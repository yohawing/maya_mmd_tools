"""Runtime bake joint-channel collection helpers."""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


def runtime_joint_attrs() -> Tuple[str, str, str, str, str, str]:
    """Return joint channels keyed by runtime bake."""
    return ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")


def create_runtime_joint_channel_arrays(
    bone_index_to_joint: Dict[int, str],
) -> Dict[str, Dict[str, Optional[om.MDoubleArray]]]:
    """Create per-joint channel arrays for runtime bake."""
    values: Dict[str, Dict[str, Optional[om.MDoubleArray]]] = {}
    for joint in bone_index_to_joint.values():
        if not cmds.objExists(joint):
            continue
        values[joint] = {attr: None for attr in runtime_joint_attrs()}
    return values


def create_runtime_joint_channel_static_state(
    bone_index_to_joint: Dict[int, str],
) -> Dict[str, Dict[str, dict]]:
    """Create per-channel static pruning state for runtime bake."""
    states: Dict[str, Dict[str, dict]] = {}
    for joint in bone_index_to_joint.values():
        if not cmds.objExists(joint):
            continue
        states[joint] = {
            attr: {"first": None, "is_static": True, "count": 0}
            for attr in runtime_joint_attrs()
        }
    return states


def append_bone_locals_to_channel_arrays(
    converter,
    bone_locals: Dict[int, Tuple[float, float, float, float, float, float]],
    channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    static_state: Dict[str, Dict[str, dict]],
) -> None:
    """Append one frame of local bone channels into runtime bake arrays."""
    for bidx, (tx, ty, tz, rx, ry, rz) in bone_locals.items():
        joint = converter.bone_index_to_joint.get(bidx)
        chans = channel_values.get(joint)
        states = static_state.get(joint)
        if not chans or not states:
            continue

        tx, ty, tz = converter._scale_motion_translate_from_bind(joint, tx, ty, tz)
        values = {
            "translateX": float(tx),
            "translateY": float(ty),
            "translateZ": float(tz),
            "rotateX": math.radians(float(rx)),
            "rotateY": math.radians(float(ry)),
            "rotateZ": math.radians(float(rz)),
        }
        for attr, value in values.items():
            state = states[attr]
            first = state["first"]
            if first is None:
                state["first"] = value
                state["count"] = 1
                continue

            eps = (
                converter._static_eps_rotate
                if attr.startswith("rotate")
                else converter._static_eps_translate
            )
            if state["is_static"]:
                if abs(float(value) - float(first)) <= eps:
                    state["count"] += 1
                    continue

                array = om.MDoubleArray()
                for _ in range(int(state["count"])):
                    array.append(float(first))
                array.append(float(value))
                chans[attr] = array
                state["is_static"] = False
                state["count"] += 1
                continue

            array = chans[attr]
            if array is not None:
                array.append(float(value))
            state["count"] += 1
