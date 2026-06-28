"""Append-node decomposition helpers for VMD runtime bake conversion."""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


def get_or_expand_runtime_channel(
    ch_dict: Dict[str, Optional[om.MDoubleArray]],
    st_dict: Dict[str, dict],
    attr: str,
    n_frames: int,
) -> Optional[om.MDoubleArray]:
    """Return a dynamic channel array or expand a static channel to all frames."""
    arr = ch_dict.get(attr)
    if arr is not None:
        return arr
    state = st_dict.get(attr, {})
    if state.get("is_static") and state.get("first") is not None:
        return om.MDoubleArray(n_frames, float(state["first"]))
    return None


def joint_orient_quat_from_joint(joint: str) -> om.MQuaternion:
    """Return jointOrient as a quaternion, or identity when unavailable."""
    try:
        jo = cmds.getAttr(f"{joint}.jointOrient")[0]
    except Exception:
        return om.MQuaternion()
    if not any(abs(v) > 1e-8 for v in jo):
        return om.MQuaternion()
    return om.MEulerRotation(
        math.radians(float(jo[0])),
        math.radians(float(jo[1])),
        math.radians(float(jo[2])),
    ).asQuaternion()


def decompose_append_own_rotation(
    target_rx: om.MDoubleArray,
    target_ry: om.MDoubleArray,
    target_rz: om.MDoubleArray,
    source_rx: om.MDoubleArray,
    source_ry: om.MDoubleArray,
    source_rz: om.MDoubleArray,
    ratio: float,
    target_joint_orient: om.MQuaternion | None = None,
    source_joint_orient: om.MQuaternion | None = None,
    source_rotation_is_mmd: bool = False,
):
    """Remove grant contribution from final rotation and return own/grant rotations."""
    n = len(target_rx)
    own_rx = om.MDoubleArray(n, 0.0)
    own_ry = om.MDoubleArray(n, 0.0)
    own_rz = om.MDoubleArray(n, 0.0)
    grant_rx = om.MDoubleArray(n, 0.0)
    grant_ry = om.MDoubleArray(n, 0.0)
    grant_rz = om.MDoubleArray(n, 0.0)
    identity = om.MQuaternion()

    for i in range(n):
        src_euler = om.MEulerRotation(source_rx[i], source_ry[i], source_rz[i])
        src_q = src_euler.asQuaternion()
        grant_q = om.MQuaternion.slerp(identity, src_q, ratio)
        grant_inv = grant_q.conjugate()
        grant_euler = grant_q.asEulerRotation()
        grant_rx[i] = grant_euler.x
        grant_ry[i] = grant_euler.y
        grant_rz[i] = grant_euler.z

        final_euler = om.MEulerRotation(target_rx[i], target_ry[i], target_rz[i])
        final_q = final_euler.asQuaternion()

        own_q = final_q * grant_inv
        own_euler = own_q.asEulerRotation()
        own_rx[i] = own_euler.x
        own_ry[i] = own_euler.y
        own_rz[i] = own_euler.z

    return (own_rx, own_ry, own_rz), (grant_rx, grant_ry, grant_rz)


def decompose_append_rotations_for_scene(
    converter,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    append_info: Dict[str, dict],
    n_frames: int,
) -> Dict[str, Dict[str, om.MDoubleArray]]:
    """Decompose final rotations into own rotations following append dependencies."""
    resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
    resolving = set()

    def _final_rotation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
        channels = joint_channel_values.get(joint, {})
        static = joint_channel_static.get(joint, {})
        rx = get_or_expand_runtime_channel(channels, static, "rotateX", n_frames)
        ry = get_or_expand_runtime_channel(channels, static, "rotateY", n_frames)
        rz = get_or_expand_runtime_channel(channels, static, "rotateZ", n_frames)
        if rx is None or ry is None or rz is None:
            return None
        return rx, ry, rz

    def _resolve(joint: str) -> Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]:
        if joint in resolved:
            return resolved[joint]
        if joint in resolving:
            converter.logger.warning(f"append rotation cycle detected at {joint}; using baked rotation fallback")
            resolved[joint] = None
            return None

        info = append_info.get(joint)
        if not info or not info.get("affect_rotation") or not info.get("source_joint"):
            resolved[joint] = None
            return None

        final_rotation = _final_rotation(joint)
        if final_rotation is None:
            resolved[joint] = None
            return None

        resolving.add(joint)
        source_joint = info["source_joint"]
        source_info = append_info.get(source_joint)
        source_rotation = _final_rotation(source_joint)
        source_resolved = _resolve(source_joint) if source_info else None
        source_rotation_is_mmd = bool(info.get("source_rotation_is_mmd", False))
        if source_resolved:
            source_rotation = (
                source_resolved["own"]
                if info.get("local_append")
                else source_resolved["grant"]
            )
            source_rotation_is_mmd = not info.get("local_append")

        resolving.remove(joint)
        if source_rotation is None:
            resolved[joint] = None
            return None

        own_rotation, grant_rotation = decompose_append_own_rotation(
            final_rotation[0], final_rotation[1], final_rotation[2],
            source_rotation[0], source_rotation[1], source_rotation[2],
            info["ratio"],
            target_joint_orient=info.get("target_joint_orient"),
            source_joint_orient=info.get("source_joint_orient"),
            source_rotation_is_mmd=source_rotation_is_mmd,
        )
        resolved[joint] = {"own": own_rotation, "grant": grant_rotation}
        return resolved[joint]

    decomposed = {}
    for joint in append_info:
        state = _resolve(joint)
        if state:
            own_rx, own_ry, own_rz = state["own"]
            decomposed[joint] = {
                "rotateX": own_rx,
                "rotateY": own_ry,
                "rotateZ": own_rz,
            }
    return decomposed


def decompose_append_own_translation(
    target_tx: om.MDoubleArray,
    target_ty: om.MDoubleArray,
    target_tz: om.MDoubleArray,
    source_tx: om.MDoubleArray,
    source_ty: om.MDoubleArray,
    source_tz: om.MDoubleArray,
    ratio: float,
):
    """Remove grant contribution from final translation and return own/grant translations."""
    n = len(target_tx)
    own_tx = om.MDoubleArray(n, 0.0)
    own_ty = om.MDoubleArray(n, 0.0)
    own_tz = om.MDoubleArray(n, 0.0)
    grant_tx = om.MDoubleArray(n, 0.0)
    grant_ty = om.MDoubleArray(n, 0.0)
    grant_tz = om.MDoubleArray(n, 0.0)

    for i in range(n):
        gx = source_tx[i] * ratio
        gy = source_ty[i] * ratio
        gz = source_tz[i] * ratio
        grant_tx[i] = gx
        grant_ty[i] = gy
        grant_tz[i] = gz
        own_tx[i] = target_tx[i] - gx
        own_ty[i] = target_ty[i] - gy
        own_tz[i] = target_tz[i] - gz

    return (own_tx, own_ty, own_tz), (grant_tx, grant_ty, grant_tz)


def decompose_append_translations_for_scene(
    converter,
    joint_channel_values: Dict[str, Dict[str, Optional[om.MDoubleArray]]],
    joint_channel_static: Dict[str, Dict[str, dict]],
    append_info: Dict[str, dict],
    n_frames: int,
) -> Dict[str, Dict[str, om.MDoubleArray]]:
    """Decompose final translations into own translations following append dependencies."""
    resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
    resolving = set()

    def _final_translation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
        channels = joint_channel_values.get(joint, {})
        static = joint_channel_static.get(joint, {})
        tx = get_or_expand_runtime_channel(channels, static, "translateX", n_frames)
        ty = get_or_expand_runtime_channel(channels, static, "translateY", n_frames)
        tz = get_or_expand_runtime_channel(channels, static, "translateZ", n_frames)
        if tx is None or ty is None or tz is None:
            return None
        return tx, ty, tz

    def _rest_translation(joint: str) -> Tuple[float, float, float]:
        info = append_info.get(joint)
        if info:
            try:
                return tuple(float(v) for v in cmds.getAttr(f"{info['node']}.baseTranslate")[0])
            except Exception:
                pass
        try:
            return tuple(float(v) for v in cmds.getAttr(f"{joint}.translate")[0])
        except Exception:
            return (0.0, 0.0, 0.0)

    def _subtract_rest(
        values: Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray],
        rest: Tuple[float, float, float],
    ) -> Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]:
        tx, ty, tz = values
        out_x = om.MDoubleArray(n_frames, 0.0)
        out_y = om.MDoubleArray(n_frames, 0.0)
        out_z = om.MDoubleArray(n_frames, 0.0)
        for i in range(n_frames):
            out_x[i] = tx[i] - rest[0]
            out_y[i] = ty[i] - rest[1]
            out_z[i] = tz[i] - rest[2]
        return out_x, out_y, out_z

    def _resolve(joint: str) -> Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]:
        if joint in resolved:
            return resolved[joint]
        if joint in resolving:
            converter.logger.warning(f"append translation cycle detected at {joint}; using baked translation fallback")
            resolved[joint] = None
            return None

        info = append_info.get(joint)
        if not info or not info.get("affect_translation") or not info.get("source_joint"):
            resolved[joint] = None
            return None

        final_translation = _final_translation(joint)
        if final_translation is None:
            resolved[joint] = None
            return None

        resolving.add(joint)
        source_joint = info["source_joint"]
        source_info = append_info.get(source_joint)
        source_translation = _final_translation(source_joint)
        source_resolved = _resolve(source_joint) if source_info else None
        if source_resolved:
            if info.get("local_append"):
                if source_translation is not None:
                    source_translation = _subtract_rest(source_translation, _rest_translation(source_joint))
            else:
                source_translation = source_resolved["grant"]
        elif source_translation is not None:
            source_translation = _subtract_rest(source_translation, _rest_translation(source_joint))

        resolving.remove(joint)
        if source_translation is None:
            resolved[joint] = None
            return None

        own_translation, grant_translation = decompose_append_own_translation(
            final_translation[0], final_translation[1], final_translation[2],
            source_translation[0], source_translation[1], source_translation[2],
            info["ratio"],
        )
        resolved[joint] = {"own": own_translation, "grant": grant_translation}
        return resolved[joint]

    decomposed = {}
    for joint in append_info:
        state = _resolve(joint)
        if state:
            own_tx, own_ty, own_tz = state["own"]
            decomposed[joint] = {
                "translateX": own_tx,
                "translateY": own_ty,
                "translateZ": own_tz,
            }
    return decomposed
