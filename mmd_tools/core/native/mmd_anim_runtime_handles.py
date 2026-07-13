"""Handle-wrapper classes for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
import math
from ctypes import CDLL, c_float, c_size_t, c_uint8, c_uint32, c_void_p
from typing import Callable, List, Optional, Sequence, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
    MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS,
    MMD_RUNTIME_STATUS_OK,
    MMD_RUNTIME_STATUS_UNSUPPORTED,
    MmdRuntimeBatchEvaluation,
    MmdRuntimeFfiPhysicsJointDesc,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
    MmdRuntimeFfiPhysicsTickConfig,
    MmdRuntimeFfiPhysicsWorldStepReport,
)

logger = get_logger(__name__)


def get_mmd_runtime_library() -> Optional[CDLL]:
    """Compatibility indirection for tests that patch this module-level getter."""
    return _runtime_loader.get_mmd_runtime_library()


def _as_finite_float(value) -> Optional[float]:
    """Return float(value) when finite; otherwise None (rejects NaN/±inf/non-numeric)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_positive_integral_count(value) -> Optional[int]:
    """Accept only non-bool values whose numeric value is exactly integral and > 0.

    Rejects lossy coercion such as ``1.5 -> 1`` and bools (``True``/``False``),
    while allowing exact integer-like values (``3``, ``3.0``).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0:
        return None
    count = int(number)
    if float(count) != number:
        return None
    return count if count > 0 else None


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


class MmdRuntimePhysicsWorld:
    """mmd-anim native physics world handle."""

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle

    @classmethod
    def from_descriptors(
        cls,
        rigid_bodies: Sequence[MmdRuntimeFfiPhysicsRigidbodyDesc],
        joints: Sequence[MmdRuntimeFfiPhysicsJointDesc],
    ) -> Optional["MmdRuntimePhysicsWorld"]:
        """Create a physics world from typed descriptors."""
        lib = cls._get_library()
        if lib is None:
            return None
        flags_func = getattr(lib, "mmd_runtime_feature_flags", None)
        create_func = getattr(lib, "mmd_runtime_physics_world_create", None)
        if flags_func is None or create_func is None:
            return None
        try:
            flags = int(flags_func())
            if (flags & MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS) != MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS:
                return None
            rb_count = len(rigid_bodies)
            jt_count = len(joints)
            rb_array = (MmdRuntimeFfiPhysicsRigidbodyDesc * rb_count)(*rigid_bodies) if rb_count else None
            jt_array = (MmdRuntimeFfiPhysicsJointDesc * jt_count)(*joints) if jt_count else None
            out_world = c_void_p()
            status = int(create_func(
                rb_array,
                c_size_t(rb_count),
                jt_array,
                c_size_t(jt_count),
                ctypes.byref(out_world),
            ))
            if status != MMD_RUNTIME_STATUS_OK or not out_world.value:
                logger.error("mmd_runtime_physics_world_create failed: status=%s", status)
                return None
            return cls(lib, out_world)
        except Exception as exc:
            logger.error("MmdRuntimePhysicsWorld.from_descriptors failed: %s", exc, exc_info=True)
            return None

    @classmethod
    def from_pmx_bytes(cls, pmx_bytes: bytes) -> Optional["MmdRuntimePhysicsWorld"]:
        """Create a native physics world from PMX bytes when physics features are enabled."""
        lib = cls._get_library()
        if lib is None or not pmx_bytes:
            return None
        flags_func = getattr(lib, "mmd_runtime_feature_flags", None)
        create_func = getattr(lib, "mmd_runtime_physics_world_create_from_pmx_bytes", None)
        if flags_func is None or create_func is None:
            return None
        try:
            flags = int(flags_func())
            if (flags & MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS) != MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS:
                return None
            buf = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
            out_world = c_void_p()
            status = int(create_func(buf, len(pmx_bytes), ctypes.byref(out_world)))
            if status != MMD_RUNTIME_STATUS_OK or not out_world.value:
                logger.error("mmd_runtime_physics_world_create_from_pmx_bytes failed: status=%s", status)
                return None
            return cls(lib, out_world)
        except Exception as exc:
            logger.error("MmdRuntimePhysicsWorld.from_pmx_bytes failed: %s", exc, exc_info=True)
            return None

    @property
    def handle(self) -> c_void_p:
        return self._handle

    def _physics_features_enabled(self) -> bool:
        """Return True when this library advertises the required physics feature flags."""
        if self._lib is None:
            return False
        flags_func = getattr(self._lib, "mmd_runtime_feature_flags", None)
        if flags_func is None:
            return False
        try:
            flags = int(flags_func())
        except Exception as exc:
            logger.error("mmd_runtime_feature_flags failed: %s", exc, exc_info=True)
            return False
        return (flags & MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS) == MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS

    def reset(self, instance: "MmdRuntimeInstance") -> Optional[int]:
        """Reset the physics world from the current runtime instance pose."""
        if not self._handle or not instance or not instance.handle or self._lib is None:
            return None
        reset_func = getattr(self._lib, "mmd_runtime_physics_world_reset", None)
        if reset_func is None:
            return None
        try:
            seeded = c_size_t(0)
            status = int(reset_func(self._handle, instance.handle, ctypes.byref(seeded)))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("mmd_runtime_physics_world_reset failed: status=%s", status)
                return None
            return int(seeded.value)
        except Exception as exc:
            logger.error("MmdRuntimePhysicsWorld.reset failed: %s", exc, exc_info=True)
            return None

    def prepare_for_sequential_bake(self, instance: "MmdRuntimeInstance") -> bool:
        """Initialize LIVE mode, rest pose, and world reset before sequential physics bake.

        Order is fixed and fail-closed:
        1. set instance physics mode to LIVE
        2. evaluate rest pose
        3. reset this physics world from the instance pose
        """
        if not self._handle or not instance or not instance.handle or self._lib is None:
            return False
        if not self._physics_features_enabled():
            return False
        if not instance.set_physics_mode(MMD_RUNTIME_PHYSICS_MODE_LIVE):
            return False
        if not instance.evaluate_rest_pose():
            return False
        return self.reset(instance) is not None

    def step_runtime(
        self,
        instance: "MmdRuntimeInstance",
        dt_seconds: float,
    ) -> Optional[MmdRuntimeFfiPhysicsWorldStepReport]:
        """Advance the physics world one runtime step against the instance pose."""
        if not self._handle or not instance or not instance.handle or self._lib is None:
            return None
        if not self._physics_features_enabled():
            return None
        step_func = getattr(self._lib, "mmd_runtime_physics_world_step_runtime", None)
        if step_func is None:
            return None
        dt = _as_finite_float(dt_seconds)
        if dt is None or dt < 0.0:
            logger.error("step_runtime rejected non-finite or negative dt_seconds=%s", dt_seconds)
            return None
        try:
            report = MmdRuntimeFfiPhysicsWorldStepReport()
            status = int(step_func(self._handle, instance.handle, c_float(dt), ctypes.byref(report)))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("mmd_runtime_physics_world_step_runtime failed: status=%s", status)
                return None
            return report
        except Exception as exc:
            logger.error("MmdRuntimePhysicsWorld.step_runtime failed: %s", exc, exc_info=True)
            return None

    def bake_clip_frames_with_physics(
        self,
        instance: "MmdRuntimeInstance",
        clip: "MmdRuntimeClip",
        start_frame: float,
        frame_step: float,
        frame_count: int,
        dt_seconds: float,
        *,
        prepare: bool = True,
    ) -> Optional[MmdRuntimeBatchEvaluation]:
        """Sequentially bake clip frames through the native physics world.

        Output layout matches non-physics batch evaluation:
        - ``world_matrices``: flat ``[frame][bone][16]`` column-major f32
        - ``morph_weights``: flat ``[frame][morph]``

        ``frame_step`` is the clip sample advance in VMD frame units (fixed 30fps
        timeline). ``dt_seconds`` is the actual elapsed wall/simulation time in
        seconds between consecutive sequential samples and must be supplied
        explicitly by the caller — it is never derived from ``frame_step`` or
        scene FPS. Callers that sample Maya output at N fps should pass
        ``dt_seconds`` from adjacent Maya times divided by scene FPS (e.g. at
        60fps output with VMD ``frame_step=0.5``, pass ``dt_seconds=1/60``).

        Invalid or non-positive ``dt_seconds`` / ``frame_step``, non-finite
        ``start_frame``, or non-integral / non-positive ``frame_count`` are
        rejected before any native bake/step call. When ``prepare`` is True
        (default), runs :meth:`prepare_for_sequential_bake` first
        (LIVE → rest pose → reset).
        """
        if not self._handle or not instance or not instance.handle:
            return None
        if not clip or not clip.handle or self._lib is None:
            return None
        if not self._physics_features_enabled():
            return None

        dt = _as_finite_float(dt_seconds)
        step = _as_finite_float(frame_step)
        start = _as_finite_float(start_frame)
        count = _as_positive_integral_count(frame_count)
        if dt is None or dt <= 0.0:
            logger.error(
                "bake_clip_frames_with_physics rejected non-positive/invalid dt_seconds=%s",
                dt_seconds,
            )
            return None
        if step is None or step <= 0.0:
            logger.error("bake_clip_frames_with_physics rejected non-positive/invalid frame_step=%s", frame_step)
            return None
        if start is None:
            logger.error("bake_clip_frames_with_physics rejected non-finite start_frame=%s", start_frame)
            return None
        if count is None:
            logger.error(
                "bake_clip_frames_with_physics rejected non-integral/non-positive frame_count=%s",
                frame_count,
            )
            return None

        bake_func = getattr(self._lib, "mmd_runtime_physics_world_bake_clip_frames", None)
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
        if bake_func is None or world_len_func is None or morph_len_func is None:
            logger.debug("mmd-anim runtime does not provide physics bake ABI")
            return None

        if prepare and not self.prepare_for_sequential_bake(instance):
            return None

        try:
            frame_count_size = c_size_t(count)
            world_len = int(world_len_func(instance.handle, frame_count_size))
            morph_len = int(morph_len_func(instance.handle, frame_count_size))
            if world_len == 0:
                logger.error("physics bake world matrix output length is zero for non-empty frame range")
                return None
            world_buf = (c_float * world_len)()
            morph_buf = (c_float * morph_len)()
            last_report = MmdRuntimeFfiPhysicsWorldStepReport()
            status = int(
                bake_func(
                    self._handle,
                    instance.handle,
                    clip.handle,
                    c_float(start),
                    c_float(step),
                    c_float(dt),
                    frame_count_size,
                    world_buf,
                    c_size_t(world_len),
                    morph_buf,
                    c_size_t(morph_len),
                    ctypes.byref(last_report),
                )
            )
            if status != MMD_RUNTIME_STATUS_OK:
                if status == MMD_RUNTIME_STATUS_UNSUPPORTED:
                    logger.error("mmd_runtime_physics_world_bake_clip_frames unsupported")
                else:
                    logger.error(
                        "mmd_runtime_physics_world_bake_clip_frames failed: status=%s",
                        status,
                    )
                return None
            bone_count = world_len // (count * 16)
            morph_count = morph_len // count if morph_len else 0
            logger.debug(
                "physics bake complete frames=%s dt=%s substeps=%s bones_written_back=%s",
                count,
                dt,
                int(last_report.tick.substeps),
                int(last_report.bones_written_back),
            )
            return MmdRuntimeBatchEvaluation(
                count,
                bone_count,
                morph_count,
                world_buf,
                morph_buf,
            )
        except Exception as exc:
            logger.error(
                "bake_clip_frames_with_physics failed "
                "(start=%s, step=%s, count=%s, dt=%s): %s",
                start_frame,
                frame_step,
                frame_count,
                dt_seconds,
                exc,
                exc_info=True,
            )
            return None

    def copy_rigidbody_states(self) -> Optional[List[Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]]]:
        """Copy all rigid-body states as (position_xyz, rotation_xyzw) per body."""
        if not self._handle or self._lib is None:
            return None
        count = self.rigidbody_count()
        if count is None:
            return None
        if count == 0:
            return []
        copy_func = getattr(self._lib, "mmd_runtime_physics_world_copy_rigidbody_states", None)
        if copy_func is None:
            return None
        try:
            # 7 floats per body: position_xyz[3] + rotation_xyzw[4]
            buf_len = count * 7
            buf = (c_float * buf_len)()
            status = int(copy_func(self._handle, buf, c_size_t(buf_len)))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("mmd_runtime_physics_world_copy_rigidbody_states failed: status=%s", status)
                return None
            states = []
            for i in range(count):
                off = i * 7
                pos = (float(buf[off]), float(buf[off + 1]), float(buf[off + 2]))
                rot = (float(buf[off + 3]), float(buf[off + 4]), float(buf[off + 5]), float(buf[off + 6]))
                states.append((pos, rot))
            return states
        except Exception as exc:
            logger.error("copy_rigidbody_states failed: %s", exc, exc_info=True)
            return None

    def rigidbody_count(self) -> Optional[int]:
        """Return the rigid body count for diagnostics when the ABI is available."""
        if not self._handle or self._lib is None:
            return None
        count_func = getattr(self._lib, "mmd_runtime_physics_world_rigidbody_count", None)
        if count_func is None:
            return None
        try:
            count = c_size_t(0)
            status = int(count_func(self._handle, ctypes.byref(count)))
            if status != MMD_RUNTIME_STATUS_OK:
                return None
            return int(count.value)
        except Exception as exc:
            logger.error("MmdRuntimePhysicsWorld.rigidbody_count failed: %s", exc, exc_info=True)
            return None

    def free(self) -> None:
        """Free the native physics world handle."""
        if self._handle and self._lib:
            try:
                free_func = getattr(self._lib, "mmd_runtime_physics_world_free", None)
                if free_func is not None:
                    free_func(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_physics_world_free failed: %s", exc)
            self._handle = None

    def __del__(self):
        self.free()

    def __repr__(self):
        return f"<MmdRuntimePhysicsWorld handle={self._handle}>"


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

    def set_physics_mode(self, mode: int) -> bool:
        """Set the instance physics mode (OFF/TRACE/LIVE). Fail-closed when ABI missing."""
        if not self._handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_set_physics_mode", None)
        if func is None:
            return False
        try:
            status = int(func(self._handle, c_uint32(int(mode))))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("mmd_runtime_instance_set_physics_mode failed: status=%s mode=%s", status, mode)
                return False
            return True
        except Exception as exc:
            logger.error("set_physics_mode failed: %s", exc, exc_info=True)
            return False

    def get_physics_mode(self) -> Optional[int]:
        """Return the current instance physics mode, or None when unavailable."""
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_instance_get_physics_mode", None)
        if func is None:
            return None
        try:
            out_mode = c_uint32(0)
            status = int(func(self._handle, ctypes.byref(out_mode)))
            if status != MMD_RUNTIME_STATUS_OK:
                return None
            return int(out_mode.value)
        except Exception as exc:
            logger.error("get_physics_mode failed: %s", exc, exc_info=True)
            return None

    def get_physics_tick_config(self) -> Optional[Tuple[float, int]]:
        """Return (fixed_substep_seconds, max_substeps_per_tick), or None."""
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_instance_get_physics_tick_config", None)
        if func is None:
            return None
        try:
            config = MmdRuntimeFfiPhysicsTickConfig()
            status = int(func(self._handle, ctypes.byref(config)))
            if status != MMD_RUNTIME_STATUS_OK:
                return None
            return (float(config.fixed_substep_seconds), int(config.max_substeps_per_tick))
        except Exception as exc:
            logger.error("get_physics_tick_config failed: %s", exc, exc_info=True)
            return None

    def set_physics_tick_config(self, fixed_substep_seconds: float, max_substeps_per_tick: int) -> bool:
        """Set physics tick configuration. Fail-closed when ABI missing."""
        if not self._handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_set_physics_tick_config", None)
        if func is None:
            return False
        dt = _as_finite_float(fixed_substep_seconds)
        count = _as_positive_integral_count(max_substeps_per_tick)
        if dt is None or dt <= 0.0 or count is None:
            return False
        try:
            config = MmdRuntimeFfiPhysicsTickConfig()
            config.fixed_substep_seconds = dt
            config.max_substeps_per_tick = count
            status = int(func(self._handle, ctypes.byref(config)))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("set_physics_tick_config failed: status=%s", status)
                return False
            return True
        except Exception as exc:
            logger.error("set_physics_tick_config failed: %s", exc, exc_info=True)
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
