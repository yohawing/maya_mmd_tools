"""Handle-wrapper classes for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
from ctypes import CDLL, c_float, c_size_t, c_uint8, c_uint32, c_void_p
from typing import Callable, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeBatchEvaluation

logger = get_logger(__name__)


def get_mmd_runtime_library() -> Optional[CDLL]:
    """Compatibility indirection for tests that patch this module-level getter."""
    return _runtime_loader.get_mmd_runtime_library()


class MmdRuntimeModel:
    """
    mmd-anim のランタイムモデル (PMX 由来) を表すクラス。

    主に mmd_runtime_model_create_from_pmx_bytes のラッパー。
    リソースはデストラクタまたは明示的な free() で解放されます。
    """

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

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
        lib = cls._get_library()
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

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

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
        lib = cls._get_library()
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

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def for_model(cls, model: MmdRuntimeModel) -> Optional["MmdRuntimeInstance"]:
        """モデルからインスタンスを作成します (最もシンプルな生成方法)。"""
        lib = cls._get_library()
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
