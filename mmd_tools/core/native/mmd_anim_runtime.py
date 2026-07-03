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
    c_int32,
    c_size_t,
    c_uint8,
    c_uint32,
    c_void_p,
)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_RIG_BONE_FIXED_AXIS as _MMD_RUNTIME_RIG_BONE_FIXED_AXIS,
    MmdRuntimeBatchEvaluation,
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
    """
    parsed-model の DLL シンボル群が利用可能かどうかを返す。

    Returns:
        少なくとも create/free のパース系シンボルが DLL にあれば True。
    """
    lib = get_mmd_runtime_library()
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_parsed_model_create_from_pmx_bytes")


def sample_vmd_camera_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD camera state through mmd-anim's camera interpolation logic."""
    lib = get_mmd_runtime_library()
    if lib is None or not vmd_bytes or frame_count <= 0:
        return None
    create_track = getattr(lib, "mmd_runtime_vmd_camera_track_create_from_vmd_bytes", None)
    sample_track = getattr(lib, "mmd_runtime_vmd_camera_track_sample", None)
    free_track = getattr(lib, "mmd_runtime_vmd_camera_track_free", None)
    if create_track is None or sample_track is None or free_track is None:
        return None

    track = None
    try:
        buf = (c_uint8 * len(vmd_bytes)).from_buffer_copy(vmd_bytes)
        track = create_track(buf, len(vmd_bytes))
        if not track:
            return None
        out = (c_float * 9)()
        samples: List[Dict[str, Any]] = []
        for index in range(int(frame_count)):
            frame = float(start_frame) + float(frame_step) * index
            if not sample_track(track, c_float(frame), out, c_size_t(9)):
                continue
            samples.append(
                {
                    "frame": frame,
                    "distance": float(out[0]),
                    "position": (float(out[1]), float(out[2]), float(out[3])),
                    "rotation": (float(out[4]), float(out[5]), float(out[6])),
                    "fov": float(out[7]),
                    "perspective": bool(out[8] != 0.0),
                }
            )
        return samples or None
    except Exception as e:
        logger.error(f"sample_vmd_camera_frames failed: {e}", exc_info=True)
        return None
    finally:
        if track:
            free_track(track)


def sample_vmd_light_frames(
    vmd_bytes: bytes,
    start_frame: float,
    frame_step: float,
    frame_count: int,
) -> Optional[List[Dict[str, Any]]]:
    """Sample VMD light state through mmd-anim's light interpolation logic."""
    lib = get_mmd_runtime_library()
    if lib is None or not vmd_bytes or frame_count <= 0:
        return None
    create_track = getattr(lib, "mmd_runtime_vmd_light_track_create_from_vmd_bytes", None)
    sample_track = getattr(lib, "mmd_runtime_vmd_light_track_sample", None)
    free_track = getattr(lib, "mmd_runtime_vmd_light_track_free", None)
    if create_track is None or sample_track is None or free_track is None:
        return None

    track = None
    try:
        buf = (c_uint8 * len(vmd_bytes)).from_buffer_copy(vmd_bytes)
        track = create_track(buf, len(vmd_bytes))
        if not track:
            return None
        out = (c_float * 6)()
        samples: List[Dict[str, Any]] = []
        for index in range(int(frame_count)):
            frame = float(start_frame) + float(frame_step) * index
            if not sample_track(track, c_float(frame), out, c_size_t(6)):
                continue
            samples.append(
                {
                    "frame": frame,
                    "color": (float(out[0]), float(out[1]), float(out[2])),
                    "position": (float(out[3]), float(out[4]), float(out[5])),
                }
            )
        return samples or None
    except Exception as e:
        logger.error(f"sample_vmd_light_frames failed: {e}", exc_info=True)
        return None
    finally:
        if track:
            free_track(track)


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

    lib = get_mmd_runtime_library()
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

    lib = get_mmd_runtime_library()
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


# ------------------------------------------------------------------
# Python ラッパークラス
# ------------------------------------------------------------------

class MmdRuntimeModel:
    """
    mmd-anim のランタイムモデル (PMX 由来) を表すクラス。

    主に mmd_runtime_model_create_from_pmx_bytes のラッパー。
    リソースはデストラクタまたは明示的な free() で解放されます。
    """

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["MmdRuntimeModel"]:
        """
        PMX ファイルのバイト列からランタイムモデルを作成します。

        Args:
            pmx_bytes: .pmx ファイル全体のバイナリデータ。

        Returns:
            成功時 MmdRuntimeModel、失敗またはランタイム未使用時は None。
        """
        lib = get_mmd_runtime_library()
        if lib is None or not pmx_bytes:
            return None

        try:
            buf = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
            handle = lib.mmd_runtime_model_create_from_pmx_bytes(buf, len(pmx_bytes))
            if not handle:
                logger.error("mmd_runtime_model_create_from_pmx_bytes returned NULL")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeModel.from_pmx_bytes failed: {e}", exc_info=True)
            return None

    @property
    def handle(self) -> c_void_p:
        """生の C ハンドル (上級者向け)。"""
        return self._handle

    def free(self) -> None:
        """明示的にリソースを解放します。"""
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_model_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_model_free failed: %s", exc)
            self._handle = None

    def __del__(self):
        self.free()

    def __repr__(self):
        return f"<MmdRuntimeModel handle={self._handle}>"


class MmdRuntimeClip:
    """
    mmd-anim のアニメーションクリップ (VMD 由来)。

    モデルに対して解決された VMD データを保持します。
    """

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def from_vmd_bytes_for_model(
        cls, model: MmdRuntimeModel, vmd_bytes: bytes
    ) -> Optional["MmdRuntimeClip"]:
        """
        VMD バイト列から、指定モデルに対応するクリップを作成します。

        Args:
            model: 対応する MmdRuntimeModel。
            vmd_bytes: .vmd ファイルのバイナリ。

        Returns:
            成功時 MmdRuntimeClip、失敗時は None。
        """
        lib = get_mmd_runtime_library()
        if lib is None or model is None or not model.handle or not vmd_bytes:
            return None

        try:
            buf = (c_uint8 * len(vmd_bytes)).from_buffer_copy(vmd_bytes)
            handle = lib.mmd_runtime_clip_create_from_vmd_bytes_for_model(
                model.handle, buf, len(vmd_bytes)
            )
            if not handle:
                logger.error("mmd_runtime_clip_create_from_vmd_bytes_for_model returned NULL")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeClip.from_vmd_bytes_for_model failed: {e}", exc_info=True)
            return None

    @property
    def handle(self) -> c_void_p:
        return self._handle

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_clip_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_clip_free failed: %s", exc)
            self._handle = None

    def frame_range(self) -> Optional[Tuple[int, int]]:
        """Return the first/last VMD frame numbers stored in this runtime clip."""
        func = getattr(self._lib, "mmd_runtime_clip_frame_range", None)
        if func is None or not self._handle:
            return None
        try:
            first = c_uint32(0)
            last = c_uint32(0)
            if not func(self._handle, ctypes.byref(first), ctypes.byref(last)):
                return None
            return int(first.value), int(last.value)
        except Exception as e:
            logger.error(f"MmdRuntimeClip.frame_range failed: {e}", exc_info=True)
            return None

    def __del__(self):
        self.free()

    def __repr__(self):
        return f"<MmdRuntimeClip handle={self._handle}>"


class MmdRuntimeInstance:
    """
    特定のモデルに対するランタイム評価インスタンス。

    evaluate_clip_frame() を呼び出して任意フレームの姿勢を取得できます。
    """

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def for_model(cls, model: MmdRuntimeModel) -> Optional["MmdRuntimeInstance"]:
        """モデルからインスタンスを作成します (最もシンプルな生成方法)。"""
        lib = get_mmd_runtime_library()
        if lib is None or model is None or not model.handle:
            return None

        try:
            handle = lib.mmd_runtime_instance_create_for_model(model.handle)
            if not handle:
                logger.error("mmd_runtime_instance_create_for_model returned NULL")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeInstance.for_model failed: {e}", exc_info=True)
            return None

    @property
    def handle(self) -> c_void_p:
        return self._handle

    def evaluate_clip_frame(self, clip: MmdRuntimeClip, frame: float) -> bool:
        """
        指定フレームでクリップを評価します。

        Args:
            clip: 評価対象の MmdRuntimeClip。
            frame: フレーム番号 (小数可。MMD 標準に準ずる)。

        Returns:
            成功時 True。
        """
        if not self._handle or not clip or not clip.handle or self._lib is None:
            return False
        try:
            return bool(
                self._lib.mmd_runtime_instance_evaluate_clip_frame(self._handle, clip.handle, c_float(frame))
            )
        except Exception as e:
            logger.error(f"evaluate_clip_frame failed (frame={frame}): {e}", exc_info=True)
            return False

    def evaluate_clip_frame_with_ik_options(
        self,
        clip: MmdRuntimeClip,
        frame: float,
        *,
        ik_tolerance: float = 1.0e-2,
        ik_max_iterations_cap: int = 0,
    ) -> bool:
        """
        IK solver optionを指定してクリップを評価します。

        Args:
            clip: 評価対象の MmdRuntimeClip。
            frame: フレーム番号。
            ik_tolerance: IK収束判定距離。0.0で早期終了を抑制。
            ik_max_iterations_cap: 0ならPMX設定値を上限なしで使用。

        Returns:
            成功時 True。
        """
        if not self._handle or not clip or not clip.handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_evaluate_clip_frame_with_ik_options", None)
        if func is None:
            logger.warning("mmd-anim runtime does not provide IK option evaluation ABI")
            return False
        try:
            return bool(
                func(
                    self._handle,
                    clip.handle,
                    c_float(frame),
                    c_float(ik_tolerance),
                    c_uint32(max(0, int(ik_max_iterations_cap))),
                )
            )
        except Exception as e:
            logger.error(
                f"evaluate_clip_frame_with_ik_options failed (frame={frame}): {e}",
                exc_info=True,
            )
            return False

    def evaluate_clip_frame_batch(
        self,
        clip: MmdRuntimeClip,
        start_frame: float,
        frame_step: float,
        frame_count: int,
        *,
        worker_count: int = 0,
    ) -> Optional[MmdRuntimeBatchEvaluation]:
        """連続フレーム範囲を 1 回の ABI 呼び出しで評価します。

        Args:
            clip: 評価対象の MmdRuntimeClip。
            start_frame: 最初のフレーム番号。
            frame_step: 次フレームまでの増分。
            frame_count: 評価するフレーム数。
            worker_count: Rust 側 worker 数。0 の場合は DLL 側の既定値。

        Returns:
            成功時は flat ctypes buffer を保持した MmdRuntimeBatchEvaluation。
            DLL が batch ABI を持たない場合や評価失敗時は None。
        """
        if not self._handle or not clip or not clip.handle or self._lib is None:
            return None
        if frame_count < 0:
            return None
        world_len_func = getattr(
            self._lib,
            "mmd_runtime_instance_clip_frame_batch_world_matrix_f32_len",
            None,
        )
        morph_len_func = getattr(
            self._lib,
            "mmd_runtime_instance_clip_frame_batch_morph_weight_f32_len",
            None,
        )
        eval_func = getattr(self._lib, "mmd_runtime_instance_evaluate_clip_frame_batch", None)
        if world_len_func is None or morph_len_func is None or eval_func is None:
            logger.debug("mmd-anim runtime does not provide batch clip evaluation ABI")
            return None
        try:
            frame_count_size = c_size_t(int(frame_count))
            world_len = int(world_len_func(self._handle, frame_count_size))
            morph_len = int(morph_len_func(self._handle, frame_count_size))
            if frame_count == 0:
                return MmdRuntimeBatchEvaluation(0, 0, 0, (c_float * 0)(), (c_float * 0)())
            if world_len == 0:
                logger.error("batch world matrix output length is zero for non-empty frame range")
                return None
            world_buf = (c_float * world_len)()
            morph_buf = (c_float * morph_len)()
            ok = eval_func(
                self._handle,
                clip.handle,
                c_float(start_frame),
                c_float(frame_step),
                frame_count_size,
                c_uint32(max(0, int(worker_count))),
                world_buf,
                c_size_t(world_len),
                morph_buf,
                c_size_t(morph_len),
            )
            if not ok:
                return None
            bone_count = world_len // (int(frame_count) * 16)
            morph_count = morph_len // int(frame_count) if morph_len else 0
            return MmdRuntimeBatchEvaluation(
                int(frame_count),
                bone_count,
                morph_count,
                world_buf,
                morph_buf,
            )
        except Exception as e:
            logger.error(
                "evaluate_clip_frame_batch failed "
                f"(start={start_frame}, step={frame_step}, count={frame_count}): {e}",
                exc_info=True,
            )
            return None

    def evaluate_rest_pose(self) -> bool:
        """モデルの REST pose を評価します。"""
        if not self._handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_evaluate_rest_pose", None)
        if func is None:
            logger.warning("mmd-anim runtime does not provide REST pose evaluation ABI")
            return False
        try:
            return bool(func(self._handle))
        except Exception as e:
            logger.error("evaluate_rest_pose failed: %s", e, exc_info=True)
            return False

    def get_world_matrices(self) -> Optional[List[List[float]]]:
        """
        現在の評価結果のワールド行列 (ボーン数 × 16) を取得します。

        Returns:
            各ボーン 16 要素 (column-major) のリスト。失敗時は None。
        """
        if not self._handle or self._lib is None:
            return None
        try:
            n = self._lib.mmd_runtime_instance_world_matrix_f32_len(self._handle)
            if n == 0:
                return []
            buf = (c_float * n)()
            ok = self._lib.mmd_runtime_instance_copy_world_matrices(self._handle, buf, n)
            if not ok:
                return None
            matrices: List[List[float]] = []
            for i in range(0, n, 16):
                matrices.append(list(buf[i : i + 16]))
            return matrices
        except Exception as e:
            logger.error(f"get_world_matrices failed: {e}", exc_info=True)
            return None

    def get_skinning_matrices(self) -> Optional[List[List[float]]]:
        """
        現在の評価結果のスキニング行列 (ボーン数 × 16) を取得します。

        mmd-anim 側で current world matrix と inverse bind matrix を合成済みの
        行列です。Maya skinCluster との比較では Maya 側の bindPreMatrix と
        world matrix から oracle を作るため、これは診断用 ABI として扱います。
        """
        if not self._handle or self._lib is None:
            return None
        len_func = getattr(self._lib, "mmd_runtime_instance_skinning_matrix_f32_len", None)
        copy_func = getattr(self._lib, "mmd_runtime_instance_copy_skinning_matrices", None)
        if len_func is None or copy_func is None:
            return None
        try:
            n = len_func(self._handle)
            if n == 0:
                return []
            buf = (c_float * n)()
            ok = copy_func(self._handle, buf, n)
            if not ok:
                return None
            matrices: List[List[float]] = []
            for i in range(0, n, 16):
                matrices.append(list(buf[i : i + 16]))
            return matrices
        except Exception as e:
            logger.error("get_skinning_matrices failed: %s", e, exc_info=True)
            return None

    def get_morph_weights(self) -> Optional[List[float]]:
        """現在のモーフウェイト配列を取得します。"""
        if not self._handle or self._lib is None:
            return None
        try:
            n = self._lib.mmd_runtime_instance_morph_weight_len(self._handle)
            if n == 0:
                return []
            buf = (c_float * n)()
            ok = self._lib.mmd_runtime_instance_copy_morph_weights(self._handle, buf, n)
            if not ok:
                return None
            return list(buf)
        except Exception as e:
            logger.error(f"get_morph_weights failed: {e}", exc_info=True)
            return None

    def get_ik_enabled(self) -> Optional[List[int]]:
        """現在の IK 有効状態 (0/1) 配列を取得します。"""
        if not self._handle or self._lib is None:
            return None
        try:
            n = self._lib.mmd_runtime_instance_ik_enabled_len(self._handle)
            if n == 0:
                return []
            buf = (c_uint8 * n)()
            ok = self._lib.mmd_runtime_instance_copy_ik_enabled(self._handle, buf, n)
            if not ok:
                return None
            return [int(x) for x in buf]
        except Exception as e:
            logger.error(f"get_ik_enabled failed: {e}", exc_info=True)
            return None

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_instance_free(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_instance_free failed: %s", exc)
            self._handle = None

    def __del__(self):
        self.free()

    def __repr__(self):
        return f"<MmdRuntimeInstance handle={self._handle}>"


# ------------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------------

def get_runtime_library_path() -> Optional[Path]:
    """現在ロードされているライブラリの実体パスを返します (デバッグ用)。"""
    return _runtime_loader.get_runtime_library_path()


# ------------------------------------------------------------------
# ParsedModel (PMX パース結果) ラッパー
# ------------------------------------------------------------------

class MmdParsedModel:
    """
    mmd_runtime_parsed_model_* ABI のラッパー。

    PMX バイト列からジオメトリ・スキン・マテリアルグループ・
    メタデータ（JSON）を読み出す。

    リソースは明示的な free() またはデストラクタで解放される。
    DLL またはシンボルが利用できない場合、from_pmx_bytes は None を返す。
    """

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    # ---- ファクトリ ----

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["MmdParsedModel"]:
        """
        PMX バイト列からパース済みモデルを作成する。

        Args:
            pmx_bytes: .pmx ファイル全体のバイナリ。

        Returns:
            成功時 MmdParsedModel、失敗またはシンボル不在時は None。
        """
        lib = get_mmd_runtime_library()
        if lib is None or not pmx_bytes:
            return None
        func = getattr(lib, "mmd_runtime_parsed_model_create_from_pmx_bytes", None)
        if func is None:
            logger.debug("parsed-model create symbol is unavailable")
            return None
        try:
            buf = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
            handle = func(buf, len(pmx_bytes))
            if not handle:
                logger.error("mmd_runtime_parsed_model_create_from_pmx_bytes returned NULL")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdParsedModel.from_pmx_bytes failed: {e}", exc_info=True)
            return None

    # ---- 解放 ----

    def free(self) -> None:
        """明示的にリソースを解放する。"""
        if self._handle and self._lib:
            func = getattr(self._lib, "mmd_runtime_parsed_model_free", None)
            if func:
                try:
                    func(self._handle)
                except Exception as exc:
                    logger.debug("mmd_runtime_parsed_model_free failed: %s", exc)
            self._handle = None

    def __del__(self):
        self.free()

    @property
    def handle(self) -> Optional[c_void_p]:
        """生の C ハンドル（上級者向け）。"""
        return self._handle

    # ---- カウントプロパティ ----

    @property
    def vertex_count(self) -> int:
        """頂点数を返す。失敗時は 0。"""
        if not self._handle or self._lib is None:
            return 0
        func = getattr(self._lib, "mmd_runtime_parsed_model_vertex_count", None)
        if func is None:
            return 0
        try:
            return func(self._handle)
        except Exception:
            return 0

    @property
    def index_count(self) -> int:
        """インデックス数（三角形 * 3）を返す。失敗時は 0。"""
        if not self._handle or self._lib is None:
            return 0
        func = getattr(self._lib, "mmd_runtime_parsed_model_index_count", None)
        if func is None:
            return 0
        try:
            return func(self._handle)
        except Exception:
            return 0

    @property
    def material_group_count(self) -> int:
        """マテリアルグループ数を返す。失敗時は 0。"""
        if not self._handle or self._lib is None:
            return 0
        func = getattr(self._lib, "mmd_runtime_parsed_model_material_group_count", None)
        if func is None:
            return 0
        try:
            return func(self._handle)
        except Exception:
            return 0

    @property
    def vertex_morph_count(self) -> int:
        """頂点モーフ数を返す。失敗時は 0。"""
        if not self._handle or self._lib is None:
            return 0
        func = getattr(self._lib, "mmd_runtime_parsed_model_vertex_morph_count", None)
        if func is None:
            return 0
        try:
            return func(self._handle)
        except Exception:
            return 0

    @property
    def vertex_morph_offset_count(self) -> int:
        """全頂点モーフ offset 数を返す。失敗時は 0。"""
        if not self._handle or self._lib is None:
            return 0
        func = getattr(self._lib, "mmd_runtime_parsed_model_vertex_morph_offset_count", None)
        if func is None:
            return 0
        try:
            return func(self._handle)
        except Exception:
            return 0

    # ---- ポインター配列 → Python list 変換 ----

    @property
    def positions(self) -> Optional[List[Tuple[float, float, float]]]:
        """
        頂点位置リスト [(x, y, z), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_positions")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_float * (n * 3)).from_address(ptr)
            return [(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read positions: {e}")
            return None

    @property
    def normals(self) -> Optional[List[Tuple[float, float, float]]]:
        """
        頂点法線リスト [(x, y, z), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_normals")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_float * (n * 3)).from_address(ptr)
            return [(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read normals: {e}")
            return None

    @property
    def uvs(self) -> Optional[List[Tuple[float, float]]]:
        """
        UV リスト [(u, v), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_uvs")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_float * (n * 2)).from_address(ptr)
            return [(arr[i * 2], arr[i * 2 + 1]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read uvs: {e}")
            return None

    @property
    def edge_scale(self) -> Optional[List[float]]:
        """
        エッジスケールリスト [s, ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_edge_scale")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_float * n).from_address(ptr)
            return list(arr)
        except Exception as e:
            logger.error(f"Failed to read edge_scale: {e}")
            return None

    @property
    def indices(self) -> Optional[List[int]]:
        """
        インデックスリスト [idx, ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_indices")
        if ptr is None:
            return None
        try:
            n = self.index_count
            arr = (c_uint32 * n).from_address(ptr)
            return list(arr)
        except Exception as e:
            logger.error(f"Failed to read indices: {e}")
            return None

    @property
    def skin_indices(self) -> Optional[List[Tuple[int, int, int, int]]]:
        """
        スキンインデックスリスト [(b0, b1, b2, b3), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_skin_indices")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_uint32 * (n * 4)).from_address(ptr)
            return [(arr[i * 4], arr[i * 4 + 1], arr[i * 4 + 2], arr[i * 4 + 3]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read skin_indices: {e}")
            return None

    @property
    def skin_weights(self) -> Optional[List[Tuple[float, float, float, float]]]:
        """
        スキンウェイトリスト [(w0, w1, w2, w3), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_skin_weights")
        if ptr is None:
            return None
        try:
            n = self.vertex_count
            arr = (c_float * (n * 4)).from_address(ptr)
            return [(arr[i * 4], arr[i * 4 + 1], arr[i * 4 + 2], arr[i * 4 + 3]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read skin_weights: {e}")
            return None

    @property
    def material_groups(self) -> Optional[List[Tuple[int, int, int]]]:
        """
        マテリアルグループリスト [(start, count, material_index), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_material_groups")
        if ptr is None:
            return None
        try:
            n = self.material_group_count
            arr = (c_uint32 * (n * 3)).from_address(ptr)
            return [(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read material_groups: {e}")
            return None

    @property
    def vertex_morph_spans(self) -> Optional[List[Tuple[int, int, int]]]:
        """
        頂点モーフ span [(start, count, pmx_morph_index), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_vertex_morph_spans")
        if ptr is None:
            return None
        try:
            n = self.vertex_morph_count
            arr = (c_uint32 * (n * 3)).from_address(ptr)
            return [(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read vertex_morph_spans: {e}")
            return None

    @property
    def vertex_morph_vertex_indices(self) -> Optional[List[int]]:
        """
        全頂点モーフ offset の vertex index 配列を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_vertex_morph_vertex_indices")
        if ptr is None:
            return None
        try:
            n = self.vertex_morph_offset_count
            arr = (c_uint32 * n).from_address(ptr)
            return list(arr)
        except Exception as e:
            logger.error(f"Failed to read vertex_morph_vertex_indices: {e}")
            return None

    @property
    def vertex_morph_position_offsets(self) -> Optional[List[Tuple[float, float, float]]]:
        """
        全頂点モーフ offset の移動量 [(dx, dy, dz), ...] を返す。
        利用不可または空の場合は None。
        """
        ptr = self._get_ptr("mmd_runtime_parsed_model_vertex_morph_position_offsets")
        if ptr is None:
            return None
        try:
            n = self.vertex_morph_offset_count
            arr = (c_float * (n * 3)).from_address(ptr)
            return [(arr[i * 3], arr[i * 3 + 1], arr[i * 3 + 2]) for i in range(n)]
        except Exception as e:
            logger.error(f"Failed to read vertex_morph_position_offsets: {e}")
            return None

    @property
    def vertex_morph_names(self) -> Optional[List[str]]:
        """頂点モーフ名を vertex morph accessor 順に返す。"""
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_parsed_model_vertex_morph_name", None)
        free_func = getattr(self._lib, "mmd_runtime_byte_buffer_free", None)
        if func is None or free_func is None:
            return None
        names = []
        try:
            for i in range(self.vertex_morph_count):
                buf: MmdRuntimeFfiByteBuffer = func(self._handle, i)
                if not buf.data or buf.len == 0:
                    free_func(buf)
                    names.append("")
                    continue
                addr = ctypes.cast(buf.data, c_void_p).value
                if addr is None or addr == 0:
                    free_func(buf)
                    names.append("")
                    continue
                raw_bytes = (c_uint8 * buf.len).from_address(addr)
                names.append(bytes(raw_bytes).decode("utf-8", errors="replace"))
                free_func(buf)
            return names
        except Exception as e:
            logger.error(f"Failed to read vertex_morph_names: {e}")
            return None

    @property
    def metadata_json(self) -> Optional[str]:
        """
        非ホットメタデータの JSON 文字列を返す。
        呼び出し毎に mmd_runtime_byte_buffer_free で解放する。
        失敗時は None。
        """
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_parsed_model_metadata_json", None)
        if func is None:
            return None
        free_func = getattr(self._lib, "mmd_runtime_byte_buffer_free", None)
        if free_func is None:
            return None
        try:
            buf: MmdRuntimeFfiByteBuffer = func(self._handle)
            if not buf.data or buf.len == 0:
                # 空バッファでも free を呼んで安全に処理
                if free_func:
                    free_func(buf)
                return None
            # ポインタアドレスを整数として取り出し、バッファをコピーする
            addr = ctypes.cast(buf.data, c_void_p).value
            if addr is None or addr == 0:
                free_func(buf)
                return None
            raw_bytes = (c_uint8 * buf.len).from_address(addr)
            text = bytes(raw_bytes).decode("utf-8", errors="replace")
            # 必ず解放
            free_func(buf)
            return text
        except Exception as e:
            logger.error(f"Failed to read metadata_json: {e}")
            # エラーでも可能なら解放を試みる
            self._safe_free_buffer()
            return None

    # ---- 内部ヘルパー ----

    def _get_ptr(self, func_name: str) -> Optional[int]:
        """
        mmd_runtime_parsed_model_* ポインターアクセサを呼び出し、
        アドレス (int) を返す。NULL または失敗時は None。
        """
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, func_name, None)
        if func is None:
            return None
        try:
            ptr = func(self._handle)
            if not ptr:
                return None
            return ptr if isinstance(ptr, int) else ctypes.addressof(ptr.contents)
        except Exception:
            return None

    def _safe_free_buffer(self) -> None:
        """エラー後などに残っている可能性のあるバッファを安全に解放試行する。"""
        if self._lib is None:
            return
        free_func = getattr(self._lib, "mmd_runtime_byte_buffer_free", None)
        if free_func:
            try:
                free_func(MmdRuntimeFfiByteBuffer(data=None, len=0))
            except Exception as exc:
                logger.debug("mmd_runtime_byte_buffer_free cleanup failed: %s", exc)

    def __repr__(self):
        return f"<MmdParsedModel handle={self._handle}>"


# ---- エイリアス（後方互換） ----
# MmdParsedModel は新しいクラス名。古いコードで使われている
# 可能性は低いが、混乱を避けるためエイリアスは用意しない。


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
