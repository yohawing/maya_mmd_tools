"""IK pass-through helpers for VMD runtime bake conversion."""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Union

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from .vmd_append_decomposition import get_or_expand_runtime_channel
from .vmd_runtime_rig_helper import _ls_mmd_ccd_ik_nodes


def collect_mmd_ik_passthrough_info() -> Dict[str, Dict[str, Union[str, int]]]:
    """Return joints driven by mmdCcdIk outputRotate and their link indices."""
    result: Dict[str, Dict[str, Union[str, int]]] = {}
    for node in _ls_mmd_ccd_ik_nodes():
        link_slots = []
        try:
            cfg = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
            link_slots = [int(link.get("bone_slot", -1)) for link in cfg.get("links", [])]
        except Exception:
            link_slots = []
        for dest in cmds.listConnections(f"{node}.outputRotate", s=False, d=True, p=True) or []:
            if not dest.endswith(".rotate"):
                continue
            joint = dest.rsplit(".", 1)[0]
            source_plugs = cmds.listConnections(dest, s=True, d=False, p=True) or []
            link_index = None
            prefix = f"{node}.outputRotate["
            for source in source_plugs:
                if source.startswith(prefix):
                    try:
                        link_index = int(source[len(prefix) :].split("]", 1)[0])
                    except (TypeError, ValueError):
                        link_index = None
                    break
            if link_index is None:
                continue
            input_slot = link_slots[link_index] if link_index < len(link_slots) else link_index
            info = {"node": node, "link_index": link_index, "input_slot": input_slot}
            result[joint] = info
            short_name = joint.rsplit("|", 1)[-1]
            result[short_name] = info
            for long_name in cmds.ls(joint, long=True) or []:
                result[long_name] = info
    return result


def key_mmd_ik_passthrough_rotation(
    converter,
    ik_info: Dict[str, Union[str, int]],
    channels: Dict[str, Optional[om.MDoubleArray]],
    static_state: Dict[str, dict],
    bake_times: om.MTimeArray,
    baked_frames: List[float],
    disable_solver: bool = True,
) -> int:
    """Key mmdCcdIk inputRotate/output pass-through for runtime-live apply."""
    node = str(ik_info.get("node", ""))
    input_slot = int(ik_info.get("input_slot", -1))
    if not node or input_slot < 0 or not cmds.objExists(node):
        return 0

    n_frames = len(baked_frames)
    rx = get_or_expand_runtime_channel(channels, static_state, "rotateX", n_frames)
    ry = get_or_expand_runtime_channel(channels, static_state, "rotateY", n_frames)
    rz = get_or_expand_runtime_channel(channels, static_state, "rotateZ", n_frames)
    if rx is None or ry is None or rz is None:
        return 0
    if len(rx) != n_frames or len(ry) != n_frames or len(rz) != n_frames:
        return 0

    axis_attrs = (
        f"inputRotate[{input_slot}].inputRotateElementX",
        f"inputRotate[{input_slot}].inputRotateElementY",
        f"inputRotate[{input_slot}].inputRotateElementZ",
    )
    for axis_attr in axis_attrs:
        plug_path = f"{node}.{axis_attr}"
        for source in cmds.listConnections(plug_path, s=True, d=False, p=True) or []:
            try:
                cmds.disconnectAttr(source, plug_path)
            except Exception:
                pass

    tangent = oma.MFnAnimCurve.kTangentLinear
    keyed = 0
    for axis_attr, values in zip(axis_attrs, (rx, ry, rz)):
        plug_path = f"{node}.{axis_attr}"
        try:
            sel = om.MSelectionList()
            sel.add(plug_path)
            plug = sel.getPlug(0)
            curve = oma.MFnAnimCurve()
            curve.create(plug)
            curve.addKeys(bake_times, values, tangent, tangent, False)
            keyed += 1
        except Exception as exc:
            converter.logger.debug(f"addKeys failed for {plug_path}, fallback: {exc}")
            for index, frame in enumerate(baked_frames):
                try:
                    cmds.setKeyframe(plug_path, time=frame, value=math.degrees(float(values[index])))
                except Exception as exc2:
                    converter.logger.debug(f"failed to key {plug_path} at frame {frame}: {exc2}")
                    break
            else:
                keyed += 1

    if disable_solver:
        try:
            for source in cmds.listConnections(f"{node}.enabled", s=True, d=False, p=True) or []:
                try:
                    cmds.disconnectAttr(source, f"{node}.enabled")
                except Exception:
                    pass
            cmds.setAttr(f"{node}.enabled", False)
            try:
                sel = om.MSelectionList()
                sel.add(f"{node}.enabled")
                plug = sel.getPlug(0)
                curve = oma.MFnAnimCurve()
                curve.create(plug)
                en_values = om.MDoubleArray([0.0] * n_frames)
                curve.addKeys(bake_times, en_values, tangent, tangent, False)
            except Exception:
                for frame in baked_frames:
                    cmds.setKeyframe(node, attribute="enabled", time=frame, value=0.0)
            keyed += 1
        except Exception as exc:
            converter.logger.debug(f"failed to key {node}.enabled off for runtime live apply: {exc}")

    return keyed
