"""Append-node decomposition helpers for VMD runtime bake conversion."""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from .vmd_runtime_rig_helper import _ls_mmd_append_nodes


def stable_long_dag_path(node: Optional[str]) -> Optional[str]:
    """Return a long DAG path when node resolves unambiguously; otherwise preserve it."""
    if not node:
        return node
    matches = cmds.ls(node, long=True) or []
    if len(matches) == 1 and matches[0].startswith("|"):
        return matches[0]
    return node


class DagPathKeyDict(dict):
    """Canonical long-path mapping with unambiguous short-name lookup compatibility."""

    @staticmethod
    def _canonical_key(key):
        return stable_long_dag_path(key) if isinstance(key, str) else key

    def __contains__(self, key):
        return super().__contains__(self._canonical_key(key))

    def __setitem__(self, key, value):
        return super().__setitem__(self._canonical_key(key), value)

    def __getitem__(self, key):
        return super().__getitem__(self._canonical_key(key))

    def get(self, key, default=None):
        return super().get(self._canonical_key(key), default)


def canonicalize_dag_mapping(mapping: dict, path_cache: dict) -> Tuple[dict, dict]:
    """Canonicalize a DAG-keyed mapping once and retain its original-key aliases."""
    canonical = {}
    aliases = {}
    for key, value in mapping.items():
        if isinstance(key, str):
            canonical_key = cached_long_dag_path(key, path_cache)
        else:
            canonical_key = key
        canonical[canonical_key] = value
        aliases.setdefault(canonical_key, []).append(key)
    return canonical, aliases


def cached_long_dag_path(node: str, path_cache: dict) -> str:
    """Resolve one DAG identifier at most once for a decomposition call."""
    if node not in path_cache:
        canonical = stable_long_dag_path(node)
        path_cache[node] = canonical
        path_cache.setdefault(canonical, canonical)
    return path_cache[node]


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


def collect_append_info() -> Dict[str, dict]:
    """Collect mmdAppend dependency metadata keyed by target joint."""
    result = DagPathKeyDict()
    append_nodes = _ls_mmd_append_nodes()

    def _compound_destinations(src_attr, dst_attr):
        plugs = cmds.listConnections(src_attr, s=False, d=True, p=True) or []
        suffix = f".{dst_attr}"
        return [
            stable_long_dag_path(plug.rsplit(".", 1)[0])
            for plug in plugs
            if plug.endswith(suffix)
        ]

    node_targets = {}
    for node in append_nodes:
        rotate_dsts = _compound_destinations(f"{node}.outputRotate", "rotate")
        translate_dsts = _compound_destinations(f"{node}.outputTranslate", "translate")
        if not rotate_dsts and not translate_dsts:
            continue
        target_joint = rotate_dsts[0] if rotate_dsts else translate_dsts[0]
        node_targets[node] = target_joint

    for node in append_nodes:
        target_joint = node_targets.get(node)
        if not target_joint:
            continue
        rotate_dsts = _compound_destinations(f"{node}.outputRotate", "rotate")
        translate_dsts = _compound_destinations(f"{node}.outputTranslate", "translate")

        def _source_from_plug(plug: str, append_prefix: str, joint_attr: str):
            src_node, src_attr = plug.rsplit(".", 1)
            if src_attr.startswith(append_prefix):
                return node_targets.get(src_node), src_node
            if src_attr.startswith(joint_attr):
                return stable_long_dag_path(src_node), None
            if src_attr.startswith("output3D"):
                upstream = cmds.listConnections(f"{src_node}.input3D[0]", s=True, d=False, p=True) or []
                if upstream:
                    return _source_from_plug(upstream[0], append_prefix, joint_attr)
            return None, None

        source_joint = None
        source_append_node = None
        rotate_src_plugs = cmds.listConnections(f"{node}.sourceRotate", s=True, d=False, p=True) or []
        if rotate_src_plugs:
            source_joint, source_append_node = _source_from_plug(rotate_src_plugs[0], "appendRotate", "rotate")
        translate_src_plugs = cmds.listConnections(f"{node}.sourceTranslate", s=True, d=False, p=True) or []
        if not source_joint and translate_src_plugs:
            source_joint, source_append_node = _source_from_plug(
                translate_src_plugs[0],
                "appendTranslate",
                "translate",
            )
        ratio = cmds.getAttr(f"{node}.ratio")
        affect_rot = cmds.getAttr(f"{node}.affectRotation")
        local_append = False
        if cmds.attributeQuery("localAppend", node=node, exists=True):
            local_append = bool(cmds.getAttr(f"{node}.localAppend"))
        attr_map = {}
        if affect_rot and target_joint in rotate_dsts:
            attr_map.update({
                "rotateX": "baseRotateX",
                "rotateY": "baseRotateY",
                "rotateZ": "baseRotateZ",
            })
        affect_translate = False
        if cmds.attributeQuery("affectTranslation", node=node, exists=True):
            affect_translate = bool(cmds.getAttr(f"{node}.affectTranslation"))
        if target_joint in translate_dsts:
            attr_map.update({
                "translateX": "baseTranslateX",
                "translateY": "baseTranslateY",
                "translateZ": "baseTranslateZ",
            })
        result[target_joint] = {
            "node": node,
            "target_joint": target_joint,
            "source_joint": source_joint,
            "source_append_node": source_append_node,
            "ratio": ratio,
            "affect_rotation": affect_rot,
            "affect_translation": affect_translate,
            "local_append": local_append,
            "source_rotation_is_mmd": bool(source_append_node and not local_append),
            "source_joint_orient": (
                om.MQuaternion()
                if source_append_node and not local_append
                else joint_orient_quat_from_joint(source_joint)
            ),
            "target_joint_orient": joint_orient_quat_from_joint(target_joint),
            "attr_map": attr_map,
        }
    return result


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
    path_cache = {}
    canonical_values, value_aliases = canonicalize_dag_mapping(joint_channel_values, path_cache)
    canonical_static, _ = canonicalize_dag_mapping(joint_channel_static, path_cache)
    canonical_append_info, _ = canonicalize_dag_mapping(append_info, path_cache)
    resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
    resolving = set()

    def _final_rotation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
        canonical_joint = cached_long_dag_path(joint, path_cache)
        channels = canonical_values.get(canonical_joint, {})
        static = canonical_static.get(canonical_joint, {})
        rx = get_or_expand_runtime_channel(channels, static, "rotateX", n_frames)
        ry = get_or_expand_runtime_channel(channels, static, "rotateY", n_frames)
        rz = get_or_expand_runtime_channel(channels, static, "rotateZ", n_frames)
        if rx is None or ry is None or rz is None:
            return None
        return rx, ry, rz

    def _resolve(joint: str) -> Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]:
        joint = cached_long_dag_path(joint, path_cache)
        if joint in resolved:
            return resolved[joint]
        if joint in resolving:
            converter.logger.warning(f"append rotation cycle detected at {joint}; using baked rotation fallback")
            resolved[joint] = None
            return None

        info = canonical_append_info.get(joint)
        if not info or not info.get("affect_rotation") or not info.get("source_joint"):
            resolved[joint] = None
            return None

        final_rotation = _final_rotation(joint)
        if final_rotation is None:
            resolved[joint] = None
            return None

        resolving.add(joint)
        source_joint = cached_long_dag_path(info["source_joint"], path_cache)
        source_info = canonical_append_info.get(source_joint)
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
    for joint in canonical_append_info:
        state = _resolve(joint)
        if state:
            own_rx, own_ry, own_rz = state["own"]
            channels = {
                "rotateX": own_rx,
                "rotateY": own_ry,
                "rotateZ": own_rz,
            }
            for alias in value_aliases.get(joint, [joint]):
                decomposed[alias] = channels
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
    path_cache = {}
    canonical_values, value_aliases = canonicalize_dag_mapping(joint_channel_values, path_cache)
    canonical_static, _ = canonicalize_dag_mapping(joint_channel_static, path_cache)
    canonical_append_info, _ = canonicalize_dag_mapping(append_info, path_cache)
    resolved: Dict[str, Optional[Dict[str, Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]]] = {}
    resolving = set()

    def _final_translation(joint: str) -> Optional[Tuple[om.MDoubleArray, om.MDoubleArray, om.MDoubleArray]]:
        canonical_joint = cached_long_dag_path(joint, path_cache)
        channels = canonical_values.get(canonical_joint, {})
        static = canonical_static.get(canonical_joint, {})
        tx = get_or_expand_runtime_channel(channels, static, "translateX", n_frames)
        ty = get_or_expand_runtime_channel(channels, static, "translateY", n_frames)
        tz = get_or_expand_runtime_channel(channels, static, "translateZ", n_frames)
        if tx is None or ty is None or tz is None:
            return None
        return tx, ty, tz

    def _rest_translation(joint: str) -> Tuple[float, float, float]:
        info = canonical_append_info.get(cached_long_dag_path(joint, path_cache))
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
        joint = cached_long_dag_path(joint, path_cache)
        if joint in resolved:
            return resolved[joint]
        if joint in resolving:
            converter.logger.warning(f"append translation cycle detected at {joint}; using baked translation fallback")
            resolved[joint] = None
            return None

        info = canonical_append_info.get(joint)
        if not info or not info.get("affect_translation") or not info.get("source_joint"):
            resolved[joint] = None
            return None

        final_translation = _final_translation(joint)
        if final_translation is None:
            resolved[joint] = None
            return None

        resolving.add(joint)
        source_joint = cached_long_dag_path(info["source_joint"], path_cache)
        source_info = canonical_append_info.get(source_joint)
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
    for joint in canonical_append_info:
        state = _resolve(joint)
        if state:
            own_tx, own_ty, own_tz = state["own"]
            channels = {
                "translateX": own_tx,
                "translateY": own_ty,
                "translateZ": own_tz,
            }
            for alias in value_aliases.get(joint, [joint]):
                decomposed[alias] = channels
    return decomposed
