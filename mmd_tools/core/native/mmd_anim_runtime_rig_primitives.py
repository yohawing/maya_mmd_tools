"""Rig primitive wrappers for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
import json
import math
from ctypes import CDLL, c_float, c_uint8, c_void_p
from typing import Any, Callable, Dict, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MmdRuntimeFfiAppendConfig,
    MmdRuntimeFfiByteBuffer,
    MmdRuntimeFfiIkSolveStats,
    MmdRuntimeFfiRigBone,
    MmdRuntimeFfiRigBoneLocalAxisV2,
    MmdRuntimeFfiRigIkLink,
)

logger = get_logger(__name__)


def get_mmd_runtime_library() -> Optional[CDLL]:
    """Compatibility indirection for tests that patch this module-level getter."""
    return _runtime_loader.get_mmd_runtime_library()


def _resolve_library(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> Optional[CDLL]:
    return (get_library or get_mmd_runtime_library)()


def is_rig_primitive_available(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> bool:
    """Return whether rig primitive ABI symbols are available in the runtime library."""
    lib = _resolve_library(get_library)
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_ik_chain_create")


class MmdRigSpec:
    """PMX バイト列から rig spec を取得し、manifest JSON を返す。"""

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["MmdRigSpec"]:
        lib = cls._get_library()
        if lib is None or not pmx_bytes:
            return None
        if not hasattr(lib, "mmd_runtime_pmx_rig_spec_create"):
            return None
        try:
            buf = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
            handle = lib.mmd_runtime_pmx_rig_spec_create(buf, len(pmx_bytes))
            if not handle:
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRigSpec.from_pmx_bytes failed: {e}", exc_info=True)
            return None

    def manifest_json(self) -> Optional[Dict[str, Any]]:
        if not self._handle:
            return None
        try:
            buf: MmdRuntimeFfiByteBuffer = self._lib.mmd_runtime_pmx_rig_spec_manifest_json(
                self._handle
            )
            if not buf.data or buf.len == 0:
                return None
            raw = ctypes.string_at(buf.data, buf.len)
            self._lib.mmd_runtime_byte_buffer_free(buf)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.error(f"MmdRigSpec.manifest_json failed: {e}", exc_info=True)
            return None

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_pmx_rig_spec_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_pmx_rig_spec_free failed: %s", exc)
            self._handle = None

    def __del__(self) -> None:
        self.free()


class MmdIkChain:
    """mmd-anim IK chain primitive のラッパー。"""

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p, bone_count: int, link_count: int):
        self._lib = lib
        self._handle = handle
        self.bone_count = bone_count
        self.link_count = link_count

    @classmethod
    def create(
        cls,
        bones: List[Dict[str, Any]],
        target_bone_slot: int,
        links: List[Dict[str, Any]],
        iteration_count: int,
        limit_angle: float,
    ) -> Optional["MmdIkChain"]:
        """
        IK チェーンプリミティブを作成する。

        Args:
            bones: [{"parent_slot": int, "rest_position": [x,y,z], "flags": int,
                     "fixed_axis": [x,y,z], "local_axis": {"x": [x,y,z], "z": [x,y,z]} | None}]
            target_bone_slot: effector のミニチェーン内スロット
            links: [{"bone_slot": int, "has_angle_limit": bool, "angle_limit_min": [x,y,z], "angle_limit_max": [x,y,z]}]
            iteration_count: IK 反復回数
            limit_angle: 1 反復あたりの角度制限 (rad)
        """
        lib = cls._get_library()
        if lib is None or not hasattr(lib, "mmd_runtime_ik_chain_create"):
            return None

        bone_count = len(bones)
        link_count = len(links)

        c_bones = (MmdRuntimeFfiRigBone * bone_count)()
        c_local_axes = (MmdRuntimeFfiRigBoneLocalAxisV2 * bone_count)()
        has_any_local_axis = False
        for i, b in enumerate(bones):
            c_bones[i].parent_slot = b.get("parent_slot", -1)
            pos = b.get("rest_position", [0, 0, 0])
            for j in range(3):
                c_bones[i].rest_position_xyz[j] = pos[j]
            c_bones[i].flags = b.get("flags", 0)
            axis = b.get("fixed_axis", [0, 0, 0])
            for j in range(3):
                c_bones[i].fixed_axis_xyz[j] = axis[j]

            local_axis = b.get("local_axis")
            if local_axis is None:
                continue
            if not isinstance(local_axis, dict) or set(local_axis) != {"x", "z"}:
                logger.error("Invalid local_axis descriptor at bone slot %d", i)
                return None
            axis_x = local_axis["x"]
            axis_z = local_axis["z"]
            if any(
                not isinstance(values, (list, tuple))
                or len(values) != 3
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    for value in values
                )
                for values in (axis_x, axis_z)
            ):
                logger.error("Invalid local_axis vectors at bone slot %d", i)
                return None
            c_local_axes[i].has_local_axis = True
            for j in range(3):
                c_local_axes[i].local_axis_x_xyz[j] = axis_x[j]
                c_local_axes[i].local_axis_z_xyz[j] = axis_z[j]
            has_any_local_axis = True

        c_links = (MmdRuntimeFfiRigIkLink * link_count)()
        for i, lk in enumerate(links):
            c_links[i].bone_slot = lk["bone_slot"]
            c_links[i].has_angle_limit = lk.get("has_angle_limit", False)
            lmin = lk.get("angle_limit_min", [0, 0, 0])
            lmax = lk.get("angle_limit_max", [0, 0, 0])
            for j in range(3):
                c_links[i].angle_limit_min_xyz[j] = lmin[j]
                c_links[i].angle_limit_max_xyz[j] = lmax[j]

        try:
            if has_any_local_axis and hasattr(lib, "mmd_runtime_ik_chain_create_v2"):
                handle = lib.mmd_runtime_ik_chain_create_v2(
                    c_bones,
                    bone_count,
                    c_local_axes,
                    target_bone_slot,
                    c_links,
                    link_count,
                    iteration_count,
                    limit_angle,
                )
            else:
                handle = lib.mmd_runtime_ik_chain_create(
                    c_bones,
                    bone_count,
                    target_bone_slot,
                    c_links,
                    link_count,
                    iteration_count,
                    limit_angle,
                )
            if not handle:
                return None
            return cls(lib, handle, bone_count, link_count)
        except Exception as e:
            logger.error(f"MmdIkChain.create failed: {e}", exc_info=True)
            return None

    def solve(
        self,
        positions: List[float],
        rotations: List[float],
        goal: List[float],
        tolerance: float = 1e-5,
        max_iterations_cap: int = 0,
        parent_world_matrix: Optional[List[float]] = None,
    ) -> Optional[Tuple[List[float], MmdRuntimeFfiIkSolveStats]]:
        """
        IK を解く。

        Args:
            positions: bone_count * 3 の位置オフセット (xyz)
            rotations: bone_count * 4 のローカル回転 (xyzw)
            goal: IK ゴール位置 [x, y, z]
            tolerance: 収束閾値
            max_iterations_cap: 0 = 無制限
            parent_world_matrix: 16 floats (column-major) or None

        Returns:
            (link_count * 4 の solved rotations xyzw, stats) or None
        """
        if not self._handle:
            return None

        c_pos = (c_float * len(positions))(*positions)
        c_rot = (c_float * len(rotations))(*rotations)
        c_goal = (c_float * 3)(*goal)

        out_len = self.link_count * 4
        c_out = (c_float * out_len)()
        stats = MmdRuntimeFfiIkSolveStats()

        c_parent = None
        if parent_world_matrix is not None:
            c_parent = (c_float * 16)(*parent_world_matrix)

        try:
            ok = self._lib.mmd_runtime_ik_chain_solve(
                self._handle,
                c_parent,
                c_pos,
                c_rot,
                c_goal,
                tolerance,
                max_iterations_cap,
                c_out,
                out_len,
                ctypes.byref(stats),
            )
            if not ok:
                return None
            return list(c_out), stats
        except Exception as e:
            logger.error(f"MmdIkChain.solve failed: {e}", exc_info=True)
            return None

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_ik_chain_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_ik_chain_free failed: %s", exc)
            self._handle = None

    def __del__(self) -> None:
        self.free()


class MmdAppendSolver:
    """mmd-anim append (付与変形) primitive のラッパー。"""

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def create(
        cls,
        ratio: float,
        affect_rotation: bool = True,
        affect_translation: bool = False,
    ) -> Optional["MmdAppendSolver"]:
        lib = cls._get_library()
        if lib is None or not hasattr(lib, "mmd_runtime_append_solver_create"):
            return None

        config = MmdRuntimeFfiAppendConfig()
        config.ratio = ratio
        config.affect_rotation = affect_rotation
        config.affect_translation = affect_translation

        try:
            handle = lib.mmd_runtime_append_solver_create(ctypes.byref(config))
            if not handle:
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdAppendSolver.create failed: {e}", exc_info=True)
            return None

    def solve(
        self,
        source_position: List[float],
        source_rotation: List[float],
    ) -> Optional[Tuple[List[float], List[float]]]:
        """
        付与変形を解く。

        Args:
            source_position: source bone の位置オフセット [x, y, z]
            source_rotation: source bone の回転 [x, y, z, w]

        Returns:
            (out_position [x,y,z], out_rotation [x,y,z,w]) or None
        """
        if not self._handle:
            return None

        c_src_pos = (c_float * 3)(*source_position)
        c_src_rot = (c_float * 4)(*source_rotation)
        c_out_pos = (c_float * 3)()
        c_out_rot = (c_float * 4)()

        try:
            ok = self._lib.mmd_runtime_append_solver_solve(
                self._handle,
                c_src_pos,
                c_src_rot,
                c_out_pos,
                c_out_rot,
            )
            if not ok:
                return None
            return list(c_out_pos), list(c_out_rot)
        except Exception as e:
            logger.error(f"MmdAppendSolver.solve failed: {e}", exc_info=True)
            return None

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_append_solver_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_append_solver_free failed: %s", exc)
            self._handle = None

    def __del__(self) -> None:
        self.free()
