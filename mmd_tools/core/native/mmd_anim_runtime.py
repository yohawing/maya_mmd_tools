"""
mmd-anim (Rust) の C ABI を ctypes でラップするモジュール。

このファイルは Maya 環境で mmd-anim-ffi の共有ライブラリをロードし、
PMX モデルと VMD モーションの忠実なランタイム評価を提供します。

対応する主な機能 (mmd-anim-ffi ABI 3 / 互換 ABI 2):
- PMX バイト列からのモデル構築
- VMD バイト列 + モデルからのクリップ構築
- 任意フレーム (float) での評価
- 連続フレーム範囲の batch 評価 (対応 DLL のみ)
- 実験的な native physics world feature 判定と world handle 作成
- ワールド行列、スキニング行列、モーフウェイト、IK 状態の取得

注意:
- 物理演算 ABI は実験的で、feature flags が揃う DLL でのみ使用します。
- 事前ビルドされた mmd_runtime_ffi.dll (Windows) / libmmd_runtime_ffi.dylib (macOS) が必要です。
- ライブラリが見つからない場合、すべての公開 API は安全に失敗 (None / False) します。

ファイルヘッダ / コーディング規約:
- Google スタイル docstring
- snake_case / PascalCase 遵守
- プロジェクト logger 使用
"""

from __future__ import annotations

from ctypes import CDLL
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION as _MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION,
    MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE as _MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE,
    MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES as _MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES,
    MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION as _MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION,
    MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1 as _MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1,
    MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC as _MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC,
    MMD_RUNTIME_PHYSICS_MODE_LIVE as _MMD_RUNTIME_PHYSICS_MODE_LIVE,
    MMD_RUNTIME_RIG_BONE_FIXED_AXIS as _MMD_RUNTIME_RIG_BONE_FIXED_AXIS,
    MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS as _MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS,
    MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL as _MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL,
    MMD_RUNTIME_STATUS_ERROR as _MMD_RUNTIME_STATUS_ERROR,
    MMD_RUNTIME_STATUS_INVALID_INPUT as _MMD_RUNTIME_STATUS_INVALID_INPUT,
    MMD_RUNTIME_STATUS_OK as _MMD_RUNTIME_STATUS_OK,
    MMD_RUNTIME_STATUS_UNSUPPORTED as _MMD_RUNTIME_STATUS_UNSUPPORTED,
    MmdRuntimeBatchEvaluation as MmdRuntimeBatchEvaluation,
    MmdRuntimeFfiAppendConfig as MmdRuntimeFfiAppendConfig,
    MmdRuntimeFfiByteBuffer as MmdRuntimeFfiByteBuffer,
    MmdRuntimeFfiIkSolveStats as MmdRuntimeFfiIkSolveStats,
    MmdRuntimeFfiPhysicsStepStats as MmdRuntimeFfiPhysicsStepStats,
    MmdRuntimeFfiPhysicsWorldStepReport as MmdRuntimeFfiPhysicsWorldStepReport,
    MmdRuntimeFfiRigBone as MmdRuntimeFfiRigBone,
    MmdRuntimeFfiRigIkLink as MmdRuntimeFfiRigIkLink,
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
    MmdRuntimePhysicsWorld,
    MmdRuntimeReducedPose as _MmdRuntimeReducedPose,
    reduce_dense_pose as _reduce_dense_pose,
)
from mmd_tools.core.native import mmd_anim_runtime_local_channels as _runtime_local_channels
from mmd_tools.core.native.mmd_anim_runtime_parsed_model import MmdParsedModel
from mmd_tools.core.native import mmd_anim_runtime_parsed_model as _runtime_parsed_model
from mmd_tools.core.native import mmd_anim_runtime_rig_primitives as _runtime_rig_primitives
from mmd_tools.core.native.mmd_anim_runtime_rig_primitives import (
    MmdAppendSolver,
    MmdIkChain,
    MmdRigSpec,
)
from mmd_tools.core.native import mmd_anim_runtime_sampling as _runtime_sampling

logger = get_logger(__name__)
MMD_RUNTIME_RIG_BONE_FIXED_AXIS = _MMD_RUNTIME_RIG_BONE_FIXED_AXIS

# ------------------------------------------------------------------
# ABI 定数 (mmd_runtime.h より)
# ------------------------------------------------------------------
MMD_RUNTIME_ABI_VERSION_CURRENT = _runtime_loader.MMD_RUNTIME_ABI_VERSION_CURRENT
MMD_RUNTIME_ABI_VERSIONS_SUPPORTED = _runtime_loader.MMD_RUNTIME_ABI_VERSIONS_SUPPORTED
# Backward-compatible alias; this denotes the current wrapper ABI, not the
# only ABI accepted by the loader.
MMD_RUNTIME_ABI_VERSION = MMD_RUNTIME_ABI_VERSION_CURRENT
MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION = _MMD_RUNTIME_FEATURE_SPLIT_PHYSICS_EVALUATION
MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE = _MMD_RUNTIME_FEATURE_PHYSICS_BULLET_NATIVE
MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES = _MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES
MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION = _MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION
MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1 = _MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1
MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC = _MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC
MmdRuntimeReducedPose = _MmdRuntimeReducedPose
reduce_dense_pose = _reduce_dense_pose
MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS = _MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS
MMD_RUNTIME_PHYSICS_MODE_LIVE = _MMD_RUNTIME_PHYSICS_MODE_LIVE
MMD_RUNTIME_STATUS_OK = _MMD_RUNTIME_STATUS_OK
MMD_RUNTIME_STATUS_INVALID_INPUT = _MMD_RUNTIME_STATUS_INVALID_INPUT
MMD_RUNTIME_STATUS_UNSUPPORTED = _MMD_RUNTIME_STATUS_UNSUPPORTED
MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL = _MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL
MMD_RUNTIME_STATUS_ERROR = _MMD_RUNTIME_STATUS_ERROR

# The generic DCC curve reduction path is optional in the native ABI.  Keep
# this list at the wrapper boundary so callers can fail closed before they
# disconnect a Maya rig or clear existing keys.
_MMD_RUNTIME_REDUCED_POSE_REQUIRED_SYMBOLS = (
    "mmd_runtime_reduced_pose_create_from_dense",
    "mmd_runtime_reduced_pose_free",
    "mmd_runtime_reduced_pose_report",
    "mmd_runtime_reduced_pose_generic_curve_info",
    "mmd_runtime_reduced_pose_generic_curve_count",
    "mmd_runtime_reduced_pose_generic_curve_descriptor",
    "mmd_runtime_reduced_pose_generic_curve_keys",
)

def _find_library() -> Optional[Path]:
    """Compatibility wrapper for runtime library discovery."""
    return _runtime_loader.find_library()


def _set_sig(lib: CDLL, name: str, restype: Any, argtypes: List[Any]) -> None:
    """Compatibility wrapper for optional FFI signature binding."""
    _signature_set_sig(lib, name, restype, argtypes)


def is_rig_primitive_available() -> bool:
    """Compatibility proxy for rig primitive ABI availability."""
    return _runtime_rig_primitives.is_rig_primitive_available(get_mmd_runtime_library)


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


def get_runtime_feature_flags() -> int:
    """Return mmd-anim runtime feature flags, or 0 when unavailable."""
    lib = get_mmd_runtime_library()
    flags_func = getattr(lib, "mmd_runtime_feature_flags", None) if lib is not None else None
    if flags_func is None:
        return 0
    try:
        return int(flags_func())
    except Exception as exc:
        logger.error("mmd_runtime_feature_flags failed: %s", exc, exc_info=True)
        return 0


def is_native_reduced_pose_available() -> bool:
    """Return whether the generic native pose reducer can be used safely.

    This is deliberately a cheap capability check: it only inspects the
    loaded DLL's feature flag and exported symbols.  It does not create a
    model, evaluate a clip, or mutate a Maya scene, so Bake callers can reject
    an explicit ``Reduce Bake Keys`` request before clearing or disconnecting
    any existing animation.
    """
    lib = get_mmd_runtime_library()
    if lib is None:
        return False
    if any(getattr(lib, name, None) is None for name in _MMD_RUNTIME_REDUCED_POSE_REQUIRED_SYMBOLS):
        return False
    flags_func = getattr(lib, "mmd_runtime_feature_flags", None)
    if flags_func is None:
        return False
    try:
        return bool(int(flags_func()) & MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES)
    except Exception as exc:
        logger.debug("mmd_runtime_feature_flags failed for generic reduction preflight: %s", exc)
        return False


def is_native_physics_available() -> bool:
    """Return True when the loaded runtime advertises all required physics features."""
    flags = get_runtime_feature_flags()
    return (flags & MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS) == MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS


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
MmdRuntimePhysicsWorld._get_library = staticmethod(lambda: get_mmd_runtime_library())


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
# Rig Primitive ラッパークラス
# ------------------------------------------------------------------

MmdRigSpec._get_library = staticmethod(lambda: get_mmd_runtime_library())
MmdIkChain._get_library = staticmethod(lambda: get_mmd_runtime_library())
MmdAppendSolver._get_library = staticmethod(lambda: get_mmd_runtime_library())


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
