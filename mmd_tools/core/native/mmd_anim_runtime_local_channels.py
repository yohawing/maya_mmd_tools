"""Native Maya local-channel decomposition helpers for the mmd-anim runtime FFI."""

from __future__ import annotations

from ctypes import CDLL, c_float, c_int32, c_uint8
from typing import Any, Callable, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeLocalChannelBatch

logger = get_logger(__name__)


def _resolve_library(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> Optional[CDLL]:
    return (get_library or _runtime_loader.get_mmd_runtime_library)()


def compute_maya_local_channels(
    world_matrices: List[float],
    parent_indices: List[int],
    bind_world_matrices: List[float],
    bind_no_orient_matrices: List[float],
    joint_orient_quats: List[float],
    rotate_orders: List[int],
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[List[Tuple[float, float, float, float, float, float]]]:
    """mmd-anim FFI で world matrix から Maya local channel を計算する。

    Args:
        world_matrices: `[bone][16]` の flat float 配列。
        parent_indices: `[bone]`、root は `-1`。
        bind_world_matrices: `[bone][16]` の Maya bind world matrix。
        bind_no_orient_matrices: `[bone][16]` の no-JO bind matrix。
        joint_orient_quats: `[bone][x,y,z,w]`。
        rotate_orders: `[bone]` の Maya rotateOrder enum。

    Returns:
        `[bone] -> (tx, ty, tz, rx, ry, rz)`。DLL またはシンボル未対応時は None。
    """
    bone_count = len(parent_indices)
    if bone_count == 0:
        return []
    if (
        len(world_matrices) < bone_count * 16
        or len(bind_world_matrices) < bone_count * 16
        or len(bind_no_orient_matrices) < bone_count * 16
        or len(joint_orient_quats) < bone_count * 4
        or len(rotate_orders) < bone_count
    ):
        return None

    lib = _resolve_library(get_library)
    if lib is None:
        return None
    func = getattr(lib, "mmd_runtime_compute_maya_local_channels", None)
    if func is None:
        return None

    try:
        world_buf = (c_float * (bone_count * 16))(*[float(v) for v in world_matrices[: bone_count * 16]])
        parent_buf = (c_int32 * bone_count)(*[int(v) for v in parent_indices[:bone_count]])
        bind_buf = (c_float * (bone_count * 16))(*[float(v) for v in bind_world_matrices[: bone_count * 16]])
        no_orient_buf = (c_float * (bone_count * 16))(
            *[float(v) for v in bind_no_orient_matrices[: bone_count * 16]]
        )
        jo_buf = (c_float * (bone_count * 4))(*[float(v) for v in joint_orient_quats[: bone_count * 4]])
        ro_buf = (c_uint8 * bone_count)(*[int(v) & 0xFF for v in rotate_orders[:bone_count]])
        out_buf = (c_float * (bone_count * 6))()
        ok = func(
            world_buf,
            len(world_buf),
            parent_buf,
            len(parent_buf),
            bind_buf,
            len(bind_buf),
            no_orient_buf,
            len(no_orient_buf),
            jo_buf,
            len(jo_buf),
            ro_buf,
            len(ro_buf),
            bone_count,
            out_buf,
            len(out_buf),
        )
        if not ok:
            return None
        result = []
        for index in range(bone_count):
            start = index * 6
            result.append(tuple(float(out_buf[start + offset]) for offset in range(6)))
        return result
    except Exception as exc:
        logger.debug("compute_maya_local_channels failed: %s", exc, exc_info=True)
        return None


def compute_maya_local_channels_batch(
    world_matrices: Any,
    frame_count: int,
    bone_count: int,
    parent_indices: List[int],
    bind_world_matrices: List[float],
    bind_no_orient_matrices: List[float],
    joint_orient_quats: List[float],
    rotate_orders: List[int],
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[MmdRuntimeLocalChannelBatch]:
    """mmd-anim FFI で `[frame][bone][16]` を Maya local channel batch へ変換する。"""
    frame_count = int(frame_count)
    bone_count = int(bone_count)
    if frame_count <= 0 or bone_count <= 0:
        return MmdRuntimeLocalChannelBatch(0, bone_count, (c_float * 0)())
    if (
        len(parent_indices) < bone_count
        or len(bind_world_matrices) < bone_count * 16
        or len(bind_no_orient_matrices) < bone_count * 16
        or len(joint_orient_quats) < bone_count * 4
        or len(rotate_orders) < bone_count
    ):
        return None

    lib = _resolve_library(get_library)
    if lib is None:
        return None
    func = getattr(lib, "mmd_runtime_compute_maya_local_channels_batch", None)
    if func is None:
        return None

    world_len = frame_count * bone_count * 16
    try:
        if len(world_matrices) < world_len:
            return None
        parent_buf = (c_int32 * bone_count)(*[int(v) for v in parent_indices[:bone_count]])
        bind_buf = (c_float * (bone_count * 16))(*[float(v) for v in bind_world_matrices[: bone_count * 16]])
        no_orient_buf = (c_float * (bone_count * 16))(
            *[float(v) for v in bind_no_orient_matrices[: bone_count * 16]]
        )
        jo_buf = (c_float * (bone_count * 4))(*[float(v) for v in joint_orient_quats[: bone_count * 4]])
        ro_buf = (c_uint8 * bone_count)(*[int(v) & 0xFF for v in rotate_orders[:bone_count]])
        out_buf = (c_float * (frame_count * bone_count * 6))()
        ok = func(
            world_matrices,
            world_len,
            frame_count,
            parent_buf,
            len(parent_buf),
            bind_buf,
            len(bind_buf),
            no_orient_buf,
            len(no_orient_buf),
            jo_buf,
            len(jo_buf),
            ro_buf,
            len(ro_buf),
            bone_count,
            out_buf,
            len(out_buf),
        )
        if not ok:
            return None
        return MmdRuntimeLocalChannelBatch(frame_count, bone_count, out_buf)
    except Exception as exc:
        logger.debug("compute_maya_local_channels_batch failed: %s", exc, exc_info=True)
        return None
