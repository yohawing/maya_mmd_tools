"""
mmd-anim (Rust) の C ABI を ctypes でラップするモジュール。

このファイルは Maya 環境で mmd-anim-ffi の共有ライブラリをロードし、
PMX モデルと VMD モーションの忠実なランタイム評価を提供します。

対応する主な機能 (mmd-anim-ffi ABI 2 基準):
- PMX バイト列からのモデル構築
- VMD バイト列 + モデルからのクリップ構築
- 任意フレーム (float) での評価
- 連続フレーム範囲の batch 評価 (対応 DLL のみ)
- ワールド行列、スキニング行列、モーフウェイト、IK 状態の取得

注意:
- 物理演算は mmd-anim 側で提供されません (ホスト側で別途対応)。
- 事前ビルドされた mmd_runtime_ffi.dll (Windows) / libmmd_runtime_ffi.dylib (macOS) が必要です。
- ライブラリが見つからない場合、すべての公開 API は安全に失敗 (None / False) します。

ファイルヘッダ / コーディング規約:
- Google スタイル docstring
- snake_case / PascalCase 遵守
- プロジェクト logger 使用
"""

from __future__ import annotations

import ctypes
import json
from ctypes import (
    CDLL,
    c_float,
    c_uint8,
    c_void_p,
)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_RIG_BONE_FIXED_AXIS as _MMD_RUNTIME_RIG_BONE_FIXED_AXIS,
    MmdRuntimeBatchEvaluation as MmdRuntimeBatchEvaluation,
    MmdRuntimeFfiAppendConfig,
    MmdRuntimeFfiByteBuffer,
    MmdRuntimeFfiIkSolveStats,
    MmdRuntimeFfiRigBone,
    MmdRuntimeFfiRigIkLink,
    MmdRuntimeLocalChannelBatch,
)
from mmd_tools.core.native.mmd_anim_runtime_signatures import (
    set_sig as _signature_set_sig,
)
from mmd_tools.core.native import mmd_anim_runtime_export as _runtime_export
from mmd_tools.core.native.mmd_anim_runtime_handles import (
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
)
from mmd_tools.core.native import mmd_anim_runtime_local_channels as _runtime_local_channels
from mmd_tools.core.native.mmd_anim_runtime_parsed_model import MmdParsedModel
from mmd_tools.core.native import mmd_anim_runtime_parsed_model as _runtime_parsed_model
from mmd_tools.core.native import mmd_anim_runtime_sampling as _runtime_sampling

logger = get_logger(__name__)
MMD_RUNTIME_RIG_BONE_FIXED_AXIS = _MMD_RUNTIME_RIG_BONE_FIXED_AXIS

# ------------------------------------------------------------------
# ABI 定数 (mmd_runtime.h より)
# ------------------------------------------------------------------
MMD_RUNTIME_ABI_VERSION = 2

def _find_library() -> Optional[Path]:
    """Compatibility wrapper for runtime library discovery."""
    return _runtime_loader.find_library()


def _set_sig(lib: CDLL, name: str, restype: Any, argtypes: List[Any]) -> None:
    """Compatibility wrapper for optional FFI signature binding."""
    _signature_set_sig(lib, name, restype, argtypes)


def is_rig_primitive_available() -> bool:
    lib = get_mmd_runtime_library()
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_ik_chain_create")


def is_native_pmx_parser_available() -> bool:
    """Compatibility proxy for parsed-model ABI availability."""
    return _runtime_parsed_model.is_native_pmx_parser_available(get_mmd_runtime_library)


def is_native_pmx_parts_export_available() -> bool:
    """PMX parts export の DLL シンボルが利用可能かどうかを返す。"""
    return _runtime_export.is_native_pmx_parts_export_available(get_mmd_runtime_library)


def is_native_json_export_available(format_kind: str) -> bool:
    """指定 MMD format の JSON writer FFI が利用可能かどうかを返す。"""
    return _runtime_export.is_native_json_export_available(format_kind, get_mmd_runtime_library)


def export_vmd_animation_json(payload: Any) -> Optional[bytes]:
    """VmdParsedAnimation JSON から VMD バイト列を native writer で生成する。"""
    return _runtime_export.export_vmd_animation_json(payload, get_mmd_runtime_library)


def export_pmx_model_json(payload: Any) -> Optional[bytes]:
    """PmxParsedModel JSON から PMX バイト列を native writer で生成する。"""
    return _runtime_export.export_pmx_model_json(payload, get_mmd_runtime_library)


def export_pmd_model_json(payload: Any) -> Optional[bytes]:
    """PmdParsedModel JSON から PMD バイト列を native writer で生成する。"""
    return _runtime_export.export_pmd_model_json(payload, get_mmd_runtime_library)


def export_pmx_from_parts(
    metadata: Any,
    positions_xyz: Any,
    normals_xyz: Any,
    uvs_xy: Any,
    indices: Any = None,
    skin_indices: Any = None,
    skin_weights: Any = None,
    edge_scale: Any = None,
) -> Optional[bytes]:
    """PMX metadata と flat geometry buffers から PMX バイト列を native exporter で生成する。"""
    return _runtime_export.export_pmx_from_parts(
        metadata,
        positions_xyz,
        normals_xyz,
        uvs_xy,
        indices,
        skin_indices,
        skin_weights,
        edge_scale,
        get_mmd_runtime_library,
    )


def sample_vmd_camera_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD camera state through mmd-anim's camera interpolation logic."""
    return _runtime_sampling.sample_vmd_camera_frames(vmd_bytes, start_frame, frame_step, frame_count, get_mmd_runtime_library)


def sample_vmd_light_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD light state through mmd-anim's light interpolation logic."""
    return _runtime_sampling.sample_vmd_light_frames(vmd_bytes, start_frame, frame_step, frame_count, get_mmd_runtime_library)


def get_mmd_runtime_library() -> Optional[CDLL]:
    """
    mmd-anim-ffi 共有ライブラリを取得する (キャッシュ付き)。

    Returns:
        ロード済み CDLL インスタンス。失敗時は None。
    """
    return _runtime_loader.get_mmd_runtime_library()


def is_mmd_runtime_available() -> bool:
    """mmd-anim ランタイムが利用可能かどうかを返す。"""
    return _runtime_loader.is_mmd_runtime_available()


def compute_maya_local_channels(
    world_matrices: List[float],
    parent_indices: List[int],
    bind_world_matrices: List[float],
    bind_no_orient_matrices: List[float],
    joint_orient_quats: List[float],
    rotate_orders: List[int],
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
    return _runtime_local_channels.compute_maya_local_channels(
        world_matrices,
        parent_indices,
        bind_world_matrices,
        bind_no_orient_matrices,
        joint_orient_quats,
        rotate_orders,
        get_mmd_runtime_library,
    )


def compute_maya_local_channels_batch(
    world_matrices: Any,
    frame_count: int,
    bone_count: int,
    parent_indices: List[int],
    bind_world_matrices: List[float],
    bind_no_orient_matrices: List[float],
    joint_orient_quats: List[float],
    rotate_orders: List[int],
) -> Optional[MmdRuntimeLocalChannelBatch]:
    """mmd-anim FFI で `[frame][bone][16]` を Maya local channel batch へ変換する。"""
    return _runtime_local_channels.compute_maya_local_channels_batch(
        world_matrices,
        frame_count,
        bone_count,
        parent_indices,
        bind_world_matrices,
        bind_no_orient_matrices,
        joint_orient_quats,
        rotate_orders,
        get_mmd_runtime_library,
    )


# ------------------------------------------------------------------
# Python ラッパークラス
# ------------------------------------------------------------------

MmdRuntimeModel._get_library = staticmethod(lambda: get_mmd_runtime_library())
MmdRuntimeClip._get_library = staticmethod(lambda: get_mmd_runtime_library())
MmdRuntimeInstance._get_library = staticmethod(lambda: get_mmd_runtime_library())


# ------------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------------

def get_runtime_library_path() -> Optional[Path]:
    """現在ロードされているライブラリの実体パスを返します (デバッグ用)。"""
    return _runtime_loader.get_runtime_library_path()


# ------------------------------------------------------------------
# ParsedModel (PMX パース結果) ラッパー
# ------------------------------------------------------------------

MmdParsedModel._get_library = staticmethod(lambda: get_mmd_runtime_library())


# ------------------------------------------------------------------
# Phase 2 統合用ユーティリティ (C++ ノード連携のプレースホルダ)
# ------------------------------------------------------------------

def create_runtime_node_for_model(model_root: str, pmx_path: str, vmd_path: str = None) -> str:
    """Compatibility wrapper for the Maya DG runtime node connector."""
    from mmd_tools.core.native.runtime_node_connector import create_runtime_node_for_model as _create

    return _create(model_root, pmx_path, vmd_path)


def connect_runtime_node_outputs_to_model(
    node: str,
    model_root: str,
    pmx_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for the Maya DG runtime node connector."""
    from mmd_tools.core.native.runtime_node_connector import connect_runtime_node_outputs_to_model as _connect

    return _connect(node, model_root, pmx_path=pmx_path)


# ------------------------------------------------------------------
# Rig Primitive ラッパークラス
# ------------------------------------------------------------------


class MmdRigSpec:
    """PMX バイト列から rig spec を取得し、manifest JSON を返す。"""

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["MmdRigSpec"]:
        lib = get_mmd_runtime_library()
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
            bones: [{"parent_slot": int, "rest_position": [x,y,z], "flags": int, "fixed_axis": [x,y,z]}]
            target_bone_slot: effector のミニチェーン内スロット
            links: [{"bone_slot": int, "has_angle_limit": bool, "angle_limit_min": [x,y,z], "angle_limit_max": [x,y,z]}]
            iteration_count: IK 反復回数
            limit_angle: 1 反復あたりの角度制限 (rad)
        """
        lib = get_mmd_runtime_library()
        if lib is None or not hasattr(lib, "mmd_runtime_ik_chain_create"):
            return None

        bone_count = len(bones)
        link_count = len(links)

        c_bones = (MmdRuntimeFfiRigBone * bone_count)()
        for i, b in enumerate(bones):
            c_bones[i].parent_slot = b.get("parent_slot", -1)
            pos = b.get("rest_position", [0, 0, 0])
            for j in range(3):
                c_bones[i].rest_position_xyz[j] = pos[j]
            c_bones[i].flags = b.get("flags", 0)
            axis = b.get("fixed_axis", [0, 0, 0])
            for j in range(3):
                c_bones[i].fixed_axis_xyz[j] = axis[j]

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
            handle = lib.mmd_runtime_ik_chain_create(
                c_bones, bone_count,
                target_bone_slot,
                c_links, link_count,
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
        lib = get_mmd_runtime_library()
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
