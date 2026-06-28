"""Runtime world-matrix to Maya local-channel decomposition helpers."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds


ORDER_MAP = {
    0: om.MEulerRotation.kXYZ,
    1: om.MEulerRotation.kYZX,
    2: om.MEulerRotation.kZXY,
    3: om.MEulerRotation.kXZY,
    4: om.MEulerRotation.kYXZ,
    5: om.MEulerRotation.kZYX,
}


def build_bone_hierarchy_and_order_maps(converter) -> None:
    """Build bone parent and rotateOrder maps for runtime bake caches."""
    converter._bone_parent_map: Dict[int, Optional[int]] = {}
    converter._bone_rotate_orders: Dict[int, int] = {}
    for bidx, joint in list(converter.bone_index_to_joint.items()):
        converter._bone_rotate_orders[bidx] = 0
        try:
            if cmds.attributeQuery("rotateOrder", node=joint, exists=True):
                ro = cmds.getAttr(f"{joint}.rotateOrder")
                if ro is not None:
                    converter._bone_rotate_orders[bidx] = int(ro)
        except Exception:
            pass
        converter._bone_parent_map[bidx] = None
        try:
            parents = cmds.listRelatives(joint, parent=True, type="joint", fullPath=False) or []
            if parents:
                pjoint = parents[0]
                for pidx, pj in converter.bone_index_to_joint.items():
                    if pj == pjoint:
                        converter._bone_parent_map[bidx] = pidx
                        break
        except Exception:
            pass
    converter.logger.debug(f"Built hierarchy map for {len(converter._bone_parent_map)} bones for runtime cache")


def build_runtime_bind_world_maps(converter) -> None:
    """Build bind-space maps used to convert runtime matrices for JO skinning."""
    converter._runtime_bind_world_matrices: Dict[int, om.MMatrix] = {}
    converter._runtime_no_orient_bind_world_matrices: Dict[int, om.MMatrix] = {}
    converter._native_local_decompose_inputs = None
    if not hasattr(converter, "_bone_parent_map") or len(getattr(converter, "_bone_parent_map", {})) == 0:
        converter._build_bone_hierarchy_and_order_maps()

    index_to_bone_name = {idx: name for name, idx in converter.bone_name_to_index.items()}
    resolved_bind_worlds: Dict[int, om.MMatrix] = {}

    def _bind_translate(bidx: int, joint: str) -> Tuple[float, float, float]:
        bone_name = index_to_bone_name.get(bidx)
        value = converter._bone_bind_poses.get(bone_name) if bone_name else None
        if value is None:
            try:
                value = cmds.getAttr(f"{joint}.translate")[0]
            except Exception:
                value = (0.0, 0.0, 0.0)
        return float(value[0]), float(value[1]), float(value[2])

    def _resolve_bind_world(bidx: int) -> Optional[om.MMatrix]:
        if bidx in resolved_bind_worlds:
            return resolved_bind_worlds[bidx]
        joint = converter.bone_index_to_joint.get(bidx)
        if not joint or not cmds.objExists(joint):
            return None

        tx, ty, tz = _bind_translate(bidx, joint)
        tm = om.MTransformationMatrix()
        tm.setTranslation(om.MVector(tx, ty, tz), om.MSpace.kTransform)
        q_jo, _ro = converter._get_joint_orient_cache(joint)
        if q_jo is not None:
            tm.setRotation(q_jo)
        local_bind = tm.asMatrix()

        parent_idx = getattr(converter, "_bone_parent_map", {}).get(bidx)
        parent_world = _resolve_bind_world(parent_idx) if parent_idx is not None else None
        bind_world = local_bind * parent_world if parent_world is not None else local_bind
        resolved_bind_worlds[bidx] = bind_world
        return bind_world

    for bidx, joint in converter.bone_index_to_joint.items():
        bind_world = _resolve_bind_world(bidx)
        if bind_world is None:
            continue
        converter._runtime_bind_world_matrices[bidx] = bind_world
        bind_no_orient = om.MMatrix()
        bind_no_orient[12] = bind_world[12]
        bind_no_orient[13] = bind_world[13]
        bind_no_orient[14] = bind_world[14]
        converter._runtime_no_orient_bind_world_matrices[bidx] = bind_no_orient


def compute_all_bone_locals(
    converter,
    world_matrices: List[List[float]],
) -> Dict[int, Tuple[float, float, float, float, float, float]]:
    """Compute Maya local translate/rotate channels from runtime world matrices."""
    if not world_matrices or not converter.bone_index_to_joint:
        return {}
    if not hasattr(converter, "_runtime_bind_world_matrices"):
        converter._build_runtime_bind_world_maps()
    native_locals = converter._compute_all_bone_locals_native(world_matrices)
    if native_locals is not None:
        return native_locals
    locals_map: Dict[int, Tuple[float, float, float, float, float, float]] = {}
    maya_worlds: Dict[int, om.MMatrix] = {}
    for bidx in converter.bone_index_to_joint.keys():
        if bidx < len(world_matrices):
            mmd_m = world_matrices[bidx]
            if isinstance(mmd_m, (list, tuple)) and len(mmd_m) == 16:
                try:
                    maya_flat = converter._convert_mmd_world_matrix_to_maya(list(mmd_m))
                    runtime_world = om.MMatrix(maya_flat)
                    bind_world = getattr(converter, "_runtime_bind_world_matrices", {}).get(bidx)
                    bind_no_orient = getattr(converter, "_runtime_no_orient_bind_world_matrices", {}).get(bidx)
                    if bind_world is not None and bind_no_orient is not None:
                        maya_worlds[bidx] = bind_world * bind_no_orient.inverse() * runtime_world
                    else:
                        maya_worlds[bidx] = runtime_world
                except Exception:
                    pass
    for bidx, joint in converter.bone_index_to_joint.items():
        if bidx not in maya_worlds:
            continue
        mw = maya_worlds[bidx]
        pidx = getattr(converter, "_bone_parent_map", {}).get(bidx)
        pw = maya_worlds.get(pidx) if pidx is not None else None
        try:
            local_m = (mw * pw.inverse()) if pw is not None else mw
            tm = om.MTransformationMatrix(local_m)
            t = tm.translation(om.MSpace.kTransform)
            tx, ty, tz = float(t.x), float(t.y), float(t.z)
            q_total = tm.rotation(asQuaternion=True)
            q_jo, ro = converter._get_joint_orient_cache(joint)
            if q_jo is not None:
                q_rotate = q_total * q_jo.inverse()
            else:
                q_rotate = q_total
            e = q_rotate.asEulerRotation()
            order = ORDER_MAP.get(ro, om.MEulerRotation.kXYZ)
            if e.order != order:
                e.reorderIt(order)
            rx = math.degrees(e.x)
            ry = math.degrees(e.y)
            rz = math.degrees(e.z)
            locals_map[bidx] = (tx, ty, tz, rx, ry, rz)
        except Exception as e:
            converter.logger.debug(f"local compute fail for bone_idx={bidx}: {e}")
    return locals_map


def compute_all_bone_locals_native(
    converter,
    world_matrices: List[List[float]],
    compute_maya_local_channels,
) -> Optional[Dict[int, Tuple[float, float, float, float, float, float]]]:
    """Use mmd-anim FFI to decompose runtime world matrices when available."""
    if compute_maya_local_channels is None:
        return None

    ordered_bone_indices = [
        bidx
        for bidx in converter.bone_index_to_joint.keys()
        if bidx < len(world_matrices)
        and isinstance(world_matrices[bidx], (list, tuple))
        and len(world_matrices[bidx]) == 16
    ]
    if not ordered_bone_indices:
        return None

    static_inputs = converter._get_native_local_decompose_static_inputs(ordered_bone_indices)
    if static_inputs is None:
        return None

    world_flat = []
    for bidx in ordered_bone_indices:
        world_flat.extend(float(value) for value in world_matrices[bidx])

    native_values = compute_maya_local_channels(
        world_flat,
        static_inputs["parent_indices"],
        static_inputs["bind_flat"],
        static_inputs["no_orient_flat"],
        static_inputs["joint_orient_flat"],
        static_inputs["rotate_orders"],
    )
    if native_values is None or len(native_values) != len(ordered_bone_indices):
        return None
    return {bidx: tuple(native_values[slot]) for slot, bidx in enumerate(ordered_bone_indices)}


def compute_native_local_channel_batch(converter, batch_result, compute_maya_local_channels_batch):
    """Compute native local channels for an entire runtime batch when possible."""
    if compute_maya_local_channels_batch is None:
        return None
    bone_count = int(getattr(batch_result, "bone_count", 0))
    ordered_bone_indices = list(range(bone_count))
    if not ordered_bone_indices or any(bidx not in converter.bone_index_to_joint for bidx in ordered_bone_indices):
        return None
    static_inputs = converter._get_native_local_decompose_static_inputs(ordered_bone_indices)
    if static_inputs is None:
        return None
    native_batch = compute_maya_local_channels_batch(
        batch_result.world_matrices,
        int(batch_result.frame_count),
        int(batch_result.bone_count),
        static_inputs["parent_indices"],
        static_inputs["bind_flat"],
        static_inputs["no_orient_flat"],
        static_inputs["joint_orient_flat"],
        static_inputs["rotate_orders"],
    )
    if native_batch is None:
        return None
    return {
        "ordered_bone_indices": tuple(ordered_bone_indices),
        "frame_count": int(native_batch.frame_count),
        "bone_count": int(native_batch.bone_count),
        "local_channels": native_batch.local_channels,
    }


def get_native_local_decompose_static_inputs(converter, ordered_bone_indices: List[int]) -> Optional[Dict[str, list]]:
    """Return cached static inputs for native runtime local decomposition."""
    cached = getattr(converter, "_native_local_decompose_inputs", None)
    if cached and cached.get("ordered_bone_indices") == tuple(ordered_bone_indices):
        return cached

    parent_lookup = {bidx: slot for slot, bidx in enumerate(ordered_bone_indices)}
    parent_indices = []
    bind_flat = []
    no_orient_flat = []
    joint_orient_flat = []
    rotate_orders = []
    for bidx in ordered_bone_indices:
        joint = converter.bone_index_to_joint.get(bidx)
        bind_world = getattr(converter, "_runtime_bind_world_matrices", {}).get(bidx)
        bind_no_orient = getattr(converter, "_runtime_no_orient_bind_world_matrices", {}).get(bidx)
        if not joint or bind_world is None or bind_no_orient is None:
            return None

        parent_bidx = getattr(converter, "_bone_parent_map", {}).get(bidx)
        parent_indices.append(parent_lookup.get(parent_bidx, -1))
        bind_flat.extend(float(bind_world[index]) for index in range(16))
        no_orient_flat.extend(float(bind_no_orient[index]) for index in range(16))

        q_jo, ro = converter._get_joint_orient_cache(joint)
        if q_jo is None:
            joint_orient_flat.extend((0.0, 0.0, 0.0, 1.0))
        else:
            joint_orient_flat.extend((float(q_jo.x), float(q_jo.y), float(q_jo.z), float(q_jo.w)))
        rotate_orders.append(int(ro))

    if any(order != 0 for order in rotate_orders):
        return None

    cached = {
        "ordered_bone_indices": tuple(ordered_bone_indices),
        "parent_indices": parent_indices,
        "bind_flat": bind_flat,
        "no_orient_flat": no_orient_flat,
        "joint_orient_flat": joint_orient_flat,
        "rotate_orders": rotate_orders,
    }
    converter._native_local_decompose_inputs = cached
    return cached
