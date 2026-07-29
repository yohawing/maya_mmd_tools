"""Handle-wrapper classes for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
import math
import threading
from ctypes import CDLL, c_float, c_size_t, c_uint8, c_uint32, c_void_p
from typing import Callable, List, Optional, Sequence, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_MODE_LIVE,
    MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED,
    MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP,
    MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR,
    MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION,
    MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES,
    MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL,
    MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT,
    MMD_RUNTIME_GENERIC_ROTATION_BASIS_NONE,
    MMD_RUNTIME_GENERIC_ROTATION_BASIS_RUNTIME_QUATERNION,
    MMD_RUNTIME_GENERIC_VALUE_QUATERNION,
    MMD_RUNTIME_GENERIC_VALUE_SCALAR,
    MMD_RUNTIME_GENERIC_VALUE_TRANSLATION,
    MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1,
    MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC,
    MMD_RUNTIME_MODEL_DESCRIPTOR_FLAGS_NONE,
    MMD_RUNTIME_MODEL_DESCRIPTOR_VERSION_V1,
    MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS,
    MMD_RUNTIME_STATUS_OK,
    MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL,
    MMD_RUNTIME_STATUS_UNSUPPORTED,
    MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER,
    MMD_RUNTIME_BONE_TRACK_CURVE_NONE,
    MmdRuntimeBatchEvaluation,
    MmdRuntimeBoneTrack,
    MmdRuntimeBoneTrackCurve,
    MmdRuntimeBoneTrackDescriptor,
    MmdRuntimeBoneTrackKey,
    MmdRuntimeFfiBoneTrackDescriptor,
    MmdRuntimeFfiBoneTrackKey,
    MmdRuntimeFfiGenericCurveDescriptor,
    MmdRuntimeFfiGenericCurveInfo,
    MmdRuntimeFfiGenericCurveKey,
    MmdRuntimeFfiPoseReductionReport,
    MmdRuntimeFfiReductionTolerances,
    MmdRuntimeFfiPhysicsJointDesc,
    MmdRuntimeFfiHostPoseView,
    MmdRuntimeFfiPhysicsRigidbodyBinding,
    MmdRuntimeFfiPhysicsRigidbodyDesc,
    MmdRuntimeFfiPhysicsTickConfig,
    MmdRuntimeFfiPhysicsWorldStepReport,
    MmdRuntimeModelAppendDescriptor,
    MmdRuntimeModelBoneMorphOffsetDescriptor,
    MmdRuntimeModelBoneDescriptor,
    MmdRuntimeModelDescriptor,
    MmdRuntimeModelGroupMorphOffsetDescriptor,
    MmdRuntimeModelIkLinkDescriptor,
    MmdRuntimeModelIkSolverDescriptor,
    MmdRuntimeGenericCurve,
    MmdRuntimeGenericCurveDescriptor,
    MmdRuntimeGenericCurveInfo,
    MmdRuntimeGenericCurveKey,
    MmdRuntimePoseReductionReport,
    MmdRuntimeReducedPoseResult,
    MmdRuntimeReductionTolerances,
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


def _as_finite_f32(value) -> Optional[float]:
    """Return a finite value after the ABI's ``c_float`` quantization."""
    number = _as_finite_float(value)
    if number is None:
        return None
    try:
        quantized = c_float(number).value
    except (TypeError, ValueError, OverflowError):
        return None
    return quantized if math.isfinite(quantized) else None


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


def _finite_floats(values: Sequence[float], expected_len: int) -> Optional[List[float]]:
    """Copy an exact-length finite float buffer, or reject it without native mutation."""
    try:
        copied = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if len(copied) != expected_len or not all(math.isfinite(value) for value in copied):
        return None
    return copied


def _normalized_quaternions(values: Sequence[float], bone_count: int) -> Optional[List[float]]:
    copied = _finite_floats(values, bone_count * 4)
    if copied is None:
        return None
    for offset in range(0, len(copied), 4):
        norm_sq = sum(value * value for value in copied[offset : offset + 4])
        if abs(norm_sq - 1.0) > 2.0e-3:
            return None
    return copied


def _copy_native_last_error(lib: CDLL) -> Optional[str]:
    """Copy the thread-local native error before any subsequent FFI call."""
    getter = getattr(lib, "mmd_runtime_last_error_message", None)
    if getter is None:
        return None
    try:
        raw = getter()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, ctypes.c_char_p):
            raw = raw.value
        if raw:
            value = ctypes.string_at(raw)
            return value.decode("utf-8", errors="replace") if value else None
    except Exception as exc:
        logger.debug("mmd_runtime_last_error_message failed: %s", exc)
    return None


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
        self._call_lock = threading.RLock()

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

    @classmethod
    def from_descriptors(cls, descriptors) -> Optional["MmdRuntimeModel"]:
        """Create a runtime model from a validated scene descriptor snapshot."""
        lib = cls._get_library()
        flags_func = getattr(lib, "mmd_runtime_feature_flags", None) if lib else None
        func = getattr(lib, "mmd_runtime_model_create_from_descriptor", None) if lib else None
        if flags_func is None or func is None or not descriptors.bones:
            return None

        try:
            feature_flags = int(flags_func())
        except Exception as exc:
            logger.debug("mmd_runtime_feature_flags failed for model descriptor: %s", exc)
            return None
        if not (feature_flags & MMD_RUNTIME_FEATURE_MODEL_DESCRIPTOR):
            return None

        def _array(ctype, values):
            return (ctype * len(values))(*values) if values else None

        try:
            bones = _array(MmdRuntimeModelBoneDescriptor, descriptors.bones)
            ik_solvers = _array(MmdRuntimeModelIkSolverDescriptor, descriptors.ik_solvers)
            ik_links = _array(MmdRuntimeModelIkLinkDescriptor, descriptors.ik_links)
            append = _array(MmdRuntimeModelAppendDescriptor, descriptors.append_transforms)
            bone_morphs = _array(MmdRuntimeModelBoneMorphOffsetDescriptor, descriptors.bone_morph_offsets)
            group_morphs = _array(MmdRuntimeModelGroupMorphOffsetDescriptor, descriptors.group_morph_offsets)
            descriptor = MmdRuntimeModelDescriptor(
                struct_size=ctypes.sizeof(MmdRuntimeModelDescriptor),
                descriptor_version=MMD_RUNTIME_MODEL_DESCRIPTOR_VERSION_V1,
                flags=MMD_RUNTIME_MODEL_DESCRIPTOR_FLAGS_NONE,
                reserved=0,
                bones=bones,
                bone_count=len(descriptors.bones),
                ik_solvers=ik_solvers,
                ik_solver_count=len(descriptors.ik_solvers),
                ik_links=ik_links,
                ik_link_count=len(descriptors.ik_links),
                append_transforms=append,
                append_transform_count=len(descriptors.append_transforms),
                morph_count=descriptors.morph_count,
                bone_morph_offsets=bone_morphs,
                bone_morph_offset_count=len(descriptors.bone_morph_offsets),
                group_morph_offsets=group_morphs,
                group_morph_offset_count=len(descriptors.group_morph_offsets),
            )
            handle = func(ctypes.pointer(descriptor))
            if not handle:
                error = _copy_native_last_error(lib)
                if error:
                    logger.error("mmd_runtime_model_create_from_descriptor returned NULL: %s", error)
                else:
                    logger.error("mmd_runtime_model_create_from_descriptor returned NULL")
                return None
            return cls(lib, handle)
        except Exception as exc:
            logger.error("MmdRuntimeModel.from_descriptors failed: %s", exc, exc_info=True)
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

    def reduce_dense_pose(self, dense_batch: MmdRuntimeBatchEvaluation, **kwargs):
        """Return detached generic reduced curves for this model.

        This convenience method keeps native handle ownership inside the
        reduction wrapper and returns ``None`` for unsupported runtimes.
        """
        return reduce_dense_pose(self, dense_batch, **kwargs)


class MmdRuntimeReducedPose:
    """Owned native dense-pose reduction handle and generic curve snapshotter.

    Generic curve values stay in mmd-anim's runtime-native basis (model units,
    radians, sample frames, and normalized local quaternions).  The Euler
    segment fields are copied only as diagnostic fit data; they are not Maya
    channel rotations.
    """

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

    def __init__(self, lib: CDLL, handle: c_void_p):
        self._lib = lib
        self._handle = handle
        self._expected_model_identity = None
        self._expected_start_frame = None
        self._expected_frame_step = None
        self._expected_frame_count = None
        self._expected_bone_count = None
        self._expected_morph_count = None
        self._expected_target = None

    @classmethod
    def from_dense(
        cls,
        model: MmdRuntimeModel,
        dense_batch: MmdRuntimeBatchEvaluation,
        *,
        model_identity: int = 0,
        start_frame: float = 0.0,
        frame_step: float = 1.0,
        target: int = MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC,
        tolerances: Optional[object] = None,
    ) -> Optional["MmdRuntimeReducedPose"]:
        """Reduce an owned dense runtime batch when the generic ABI is available.

        ``None`` is returned for unsupported/malformed inputs so bake callers
        can retain their dense-key fallback without catching native exceptions.
        """
        try:
            lib = model._lib
            model_handle = model.handle
        except Exception:
            return None
        flags_func = getattr(lib, "mmd_runtime_feature_flags", None) if lib else None
        create_func = getattr(lib, "mmd_runtime_reduced_pose_create_from_dense", None) if lib else None
        if not model_handle or lib is None or flags_func is None or create_func is None:
            return None
        required_symbols = (
            "mmd_runtime_reduced_pose_free",
            "mmd_runtime_reduced_pose_report",
            "mmd_runtime_reduced_pose_generic_curve_info",
            "mmd_runtime_reduced_pose_generic_curve_count",
            "mmd_runtime_reduced_pose_generic_curve_descriptor",
            "mmd_runtime_reduced_pose_generic_curve_keys",
        )
        if any(getattr(lib, name, None) is None for name in required_symbols):
            return None
        try:
            if not int(flags_func()) & MMD_RUNTIME_FEATURE_REDUCED_POSE_GENERIC_CURVES:
                return None
        except Exception as exc:
            logger.debug("mmd_runtime_feature_flags failed for generic reduction: %s", exc)
            return None

        try:
            frame_count = _as_positive_integral_count(getattr(dense_batch, "frame_count", None))
            bone_count = _as_positive_integral_count(getattr(dense_batch, "bone_count", None))
            if frame_count is None or bone_count is None:
                return None
            morph_count_raw = getattr(dense_batch, "morph_count", None)
            if isinstance(morph_count_raw, bool):
                return None
            morph_count = int(morph_count_raw)
            if morph_count < 0 or float(morph_count) != float(morph_count_raw):
                return None
            world_values = _finite_floats(
                getattr(dense_batch, "world_matrices", ()), frame_count * bone_count * 16
            )
            morph_values = _finite_floats(
                getattr(dense_batch, "morph_weights", ()), frame_count * morph_count
            )
            # The native ABI stores both values as f32.  Keep the quantized
            # values as the validation authority so metadata round-trips do
            # not compare a Python f64 input against a native f32 value.
            start = _as_finite_f32(start_frame)
            step = _as_finite_f32(frame_step)
            if world_values is None or morph_values is None or start is None or step is None or step <= 0.0:
                return None
            identity = int(model_identity)
            if identity < 0 or identity >= 1 << 64 or identity != model_identity:
                return None
            target_value = int(target)
            if target_value < 0 or target_value > 0xFFFFFFFF:
                return None
            tolerance = cls._coerce_tolerances(tolerances)
            if tolerance is None:
                return None
            world_array = (c_float * len(world_values))(*world_values)
            morph_array = (c_float * len(morph_values))(*morph_values) if morph_values else None
            out_pose = c_void_p()
            status = int(
                create_func(
                    model_handle,
                    identity,
                    world_array,
                    len(world_values),
                    morph_array,
                    len(morph_values),
                    frame_count,
                    start,
                    step,
                    target_value,
                    tolerance,
                    ctypes.byref(out_pose),
                )
            )
            if status != MMD_RUNTIME_STATUS_OK or not out_pose.value:
                logger.debug("mmd_runtime_reduced_pose_create_from_dense failed: status=%s", status)
                return None
            result = cls(lib, out_pose)
            result._expected_model_identity = identity
            result._expected_start_frame = start
            result._expected_frame_step = step
            result._expected_frame_count = frame_count
            result._expected_bone_count = bone_count
            result._expected_morph_count = morph_count
            result._expected_target = target_value
            return result
        except (TypeError, ValueError, OverflowError) as exc:
            logger.debug("invalid generic reduction input: %s", exc)
            return None
        except Exception as exc:
            logger.error("MmdRuntimeReducedPose.from_dense failed: %s", exc, exc_info=True)
            return None

    create_from_dense = from_dense

    @staticmethod
    def _coerce_tolerances(value: Optional[object]) -> Optional[MmdRuntimeFfiReductionTolerances]:
        if value is None:
            value = MmdRuntimeReductionTolerances()
        fields = ("local_position", "local_rotation_radians", "world_position", "world_rotation_radians", "morph_weight")
        try:
            values = [_as_finite_float(getattr(value, field)) for field in fields]
        except (AttributeError, TypeError):
            try:
                values = [_as_finite_float(item) for item in value]  # type: ignore[union-attr]
            except (TypeError, ValueError):
                return None
        if len(values) != len(fields) or any(item is None or item < 0.0 for item in values):
            return None
        return MmdRuntimeFfiReductionTolerances(*values)

    @staticmethod
    def _report_dto(report: MmdRuntimeFfiPoseReductionReport) -> MmdRuntimePoseReductionReport:
        return MmdRuntimePoseReductionReport(
            int(report.source_bone_key_count),
            int(report.reduced_bone_key_count),
            int(report.source_morph_key_count),
            int(report.reduced_morph_key_count),
            float(report.max_local_position_error),
            float(report.max_local_rotation_error_radians),
            float(report.max_world_position_error),
            float(report.max_world_rotation_error_radians),
            float(report.max_morph_weight_error),
        )

    @staticmethod
    def _info_dto(info: MmdRuntimeFfiGenericCurveInfo) -> MmdRuntimeGenericCurveInfo:
        return MmdRuntimeGenericCurveInfo(
            int(info.struct_size),
            int(info.abi_version),
            int(info.reduction_target),
            int(info.coordinate_system),
            int(info.length_unit),
            int(info.angle_unit),
            int(info.time_unit),
            int(info.tangent_unit),
            int(info.model_identity),
            float(info.start_frame),
            float(info.frame_step),
            int(info.frame_count),
            int(info.bone_count),
            int(info.morph_count),
        )

    def get_report(self) -> Optional[MmdRuntimePoseReductionReport]:
        """Copy the native reduction report, or return ``None`` on ABI failure."""
        func = getattr(self._lib, "mmd_runtime_reduced_pose_report", None)
        if func is None or not self._handle:
            return None
        report = MmdRuntimeFfiPoseReductionReport()
        try:
            status = int(func(self._handle, ctypes.byref(report)))
            if status != MMD_RUNTIME_STATUS_OK:
                return None
            values = self._report_dto(report)
            if not all(math.isfinite(value) for value in values[4:]):
                return None
            return values
        except Exception as exc:
            logger.debug("generic reduction report failed: %s", exc)
            return None

    report = get_report

    def get_generic_curve_info(self) -> Optional[MmdRuntimeGenericCurveInfo]:
        """Read and validate generic curve metadata before enumeration."""
        func = getattr(self._lib, "mmd_runtime_reduced_pose_generic_curve_info", None)
        if func is None or not self._handle:
            return None
        info = MmdRuntimeFfiGenericCurveInfo()
        info.struct_size = ctypes.sizeof(MmdRuntimeFfiGenericCurveInfo)
        try:
            if int(func(self._handle, ctypes.byref(info))) != MMD_RUNTIME_STATUS_OK:
                return None
            if int(info.struct_size) < ctypes.sizeof(MmdRuntimeFfiGenericCurveInfo):
                return None
            if int(info.abi_version) != MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1:
                return None
            result = self._info_dto(info)
            if not math.isfinite(result.start_frame) or not math.isfinite(result.frame_step) or result.frame_step <= 0.0:
                return None
            if result.frame_count <= 0 or result.bone_count <= 0 or result.morph_count < 0:
                return None
            if any(
                value != 0
                for value in (
                    result.coordinate_system,
                    result.length_unit,
                    result.angle_unit,
                    result.time_unit,
                    result.tangent_unit,
                )
            ):
                return None
            expected_values = (
                (self._expected_model_identity, result.model_identity),
                (self._expected_frame_count, result.frame_count),
                (self._expected_bone_count, result.bone_count),
                (self._expected_morph_count, result.morph_count),
                (self._expected_target, result.reduction_target),
            )
            if any(expected is not None and expected != actual for expected, actual in expected_values):
                return None
            if self._expected_start_frame is not None and abs(result.start_frame - self._expected_start_frame) > 1.0e-3:
                return None
            if self._expected_frame_step is not None and abs(result.frame_step - self._expected_frame_step) > 1.0e-3:
                return None
            return result
        except Exception as exc:
            logger.debug("generic reduction curve info failed: %s", exc)
            return None

    info = get_generic_curve_info

    def _get_descriptor(self, curve_index: int) -> Optional[MmdRuntimeGenericCurveDescriptor]:
        func = getattr(self._lib, "mmd_runtime_reduced_pose_generic_curve_descriptor", None)
        if func is None or not self._handle or curve_index < 0:
            return None
        native = MmdRuntimeFfiGenericCurveDescriptor()
        native.struct_size = ctypes.sizeof(MmdRuntimeFfiGenericCurveDescriptor)
        try:
            if int(func(self._handle, curve_index, ctypes.byref(native))) != MMD_RUNTIME_STATUS_OK:
                return None
            if int(native.struct_size) < ctypes.sizeof(MmdRuntimeFfiGenericCurveDescriptor):
                return None
            if int(native.abi_version) != MMD_RUNTIME_REDUCED_POSE_GENERIC_CURVE_ABI_VERSION_V1:
                return None
            return MmdRuntimeGenericCurveDescriptor(
                int(native.struct_size),
                int(native.abi_version),
                int(native.kind),
                int(native.target_index),
                int(native.parent_index),
                int(native.value_flags),
                int(native.interpolation),
                int(native.rotation_basis),
                int(native.key_count),
            )
        except Exception as exc:
            logger.debug("generic reduction curve descriptor failed: %s", exc)
            return None

    def _get_keys(
        self, curve_index: int, descriptor: MmdRuntimeGenericCurveDescriptor, info: MmdRuntimeGenericCurveInfo
    ) -> Optional[Tuple[MmdRuntimeGenericCurveKey, ...]]:
        func = getattr(self._lib, "mmd_runtime_reduced_pose_generic_curve_keys", None)
        if func is None or not self._handle or descriptor.key_count <= 0:
            return None
        required = c_size_t()
        try:
            first_status = int(func(self._handle, curve_index, None, 0, ctypes.sizeof(MmdRuntimeFfiGenericCurveKey), ctypes.byref(required)))
            if first_status != MMD_RUNTIME_STATUS_BUFFER_TOO_SMALL or int(required.value) != descriptor.key_count:
                return None
            native_keys = (MmdRuntimeFfiGenericCurveKey * descriptor.key_count)()
            second_status = int(
                func(
                    self._handle,
                    curve_index,
                    native_keys,
                    descriptor.key_count,
                    ctypes.sizeof(MmdRuntimeFfiGenericCurveKey),
                    ctypes.byref(required),
                )
            )
            if second_status != MMD_RUNTIME_STATUS_OK or int(required.value) != descriptor.key_count:
                return None
            result = []
            previous_sample = -1
            for native in native_keys:
                sample_index = int(native.sample_index)
                if sample_index < 0 or sample_index >= info.frame_count or sample_index <= previous_sample:
                    return None
                frame = float(native.frame)
                # Match the native f32 arithmetic (multiply and add each
                # quantized operand) before comparing the returned c_float.
                frame_product = _as_finite_f32(info.frame_step * sample_index)
                expected_frame = (
                    _as_finite_f32(info.start_frame + frame_product)
                    if frame_product is not None
                    else None
                )
                if expected_frame is None or not math.isfinite(frame) or abs(frame - expected_frame) > 1.0e-3:
                    return None
                vectors = [
                    tuple(float(item) for item in native.translation_xyz),
                    tuple(float(item) for item in native.rotation_xyzw),
                    tuple(float(item) for item in native.segment_prev_out_translation_xyz),
                    tuple(float(item) for item in native.segment_current_in_translation_xyz),
                    tuple(float(item) for item in native.segment_from_previous_start_euler_xyz),
                    tuple(float(item) for item in native.segment_from_previous_end_euler_xyz),
                    tuple(float(item) for item in native.segment_prev_out_rotation_xyz),
                    tuple(float(item) for item in native.segment_current_in_rotation_xyz),
                ]
                scalar_values = [
                    float(native.scalar),
                    float(native.segment_prev_out_scalar),
                    float(native.segment_current_in_scalar),
                ]
                if any(not math.isfinite(item) for vector in vectors for item in vector) or any(
                    not math.isfinite(item) for item in scalar_values
                ):
                    return None
                if descriptor.kind == MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL:
                    norm_sq = sum(item * item for item in vectors[1])
                    if abs(norm_sq - 1.0) > 2.0e-3:
                        return None
                result.append(
                    MmdRuntimeGenericCurveKey(
                        sample_index,
                        frame,
                        vectors[0],
                        vectors[1],
                        scalar_values[0],
                        vectors[2],
                        vectors[3],
                        vectors[4],
                        vectors[5],
                        vectors[6],
                        vectors[7],
                        scalar_values[1],
                        scalar_values[2],
                    )
                )
                previous_sample = sample_index
            return tuple(result)
        except Exception as exc:
            logger.debug("generic reduction curve keys failed: %s", exc)
            return None

    def snapshot(self) -> Optional["MmdRuntimeReducedPoseResult"]:
        """Copy validated generic curves and report while the handle is live."""
        info = self.get_generic_curve_info()
        if info is None:
            return None
        count_func = getattr(self._lib, "mmd_runtime_reduced_pose_generic_curve_count", None)
        if count_func is None or not self._handle:
            return None
        try:
            count = c_size_t()
            if int(count_func(self._handle, ctypes.byref(count))) != MMD_RUNTIME_STATUS_OK:
                return None
            expected_count = info.bone_count + info.morph_count
            if int(count.value) != expected_count:
                return None
            curves = []
            for index in range(int(count.value)):
                descriptor = self._get_descriptor(index)
                if descriptor is None:
                    return None
                expected_kind = (
                    MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL
                    if index < info.bone_count
                    else MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT
                )
                expected_target_index = index if index < info.bone_count else index - info.bone_count
                if (
                    descriptor.kind != expected_kind
                    or descriptor.target_index != expected_target_index
                    or descriptor.interpolation != info.reduction_target
                ):
                    return None
                if descriptor.kind == MMD_RUNTIME_GENERIC_CURVE_BONE_LOCAL:
                    if descriptor.target_index >= info.bone_count or descriptor.parent_index < -1 or descriptor.parent_index >= info.bone_count:
                        return None
                    if descriptor.value_flags != MMD_RUNTIME_GENERIC_VALUE_TRANSLATION | MMD_RUNTIME_GENERIC_VALUE_QUATERNION:
                        return None
                    if descriptor.rotation_basis != MMD_RUNTIME_GENERIC_ROTATION_BASIS_RUNTIME_QUATERNION:
                        return None
                elif descriptor.kind == MMD_RUNTIME_GENERIC_CURVE_MORPH_WEIGHT:
                    if descriptor.target_index >= info.morph_count or descriptor.parent_index != -1:
                        return None
                    if descriptor.value_flags != MMD_RUNTIME_GENERIC_VALUE_SCALAR or descriptor.rotation_basis != MMD_RUNTIME_GENERIC_ROTATION_BASIS_NONE:
                        return None
                else:
                    return None
                keys = self._get_keys(index, descriptor, info)
                if keys is None:
                    return None
                curves.append(MmdRuntimeGenericCurve(descriptor, keys))
            report = self.get_report()
            if report is None:
                return None
            if (
                report.source_bone_key_count != info.frame_count * info.bone_count
                or report.source_morph_key_count != info.frame_count * info.morph_count
                or report.reduced_bone_key_count <= 0
                or report.reduced_bone_key_count > report.source_bone_key_count
                or report.reduced_morph_key_count < 0
                or report.reduced_morph_key_count > report.source_morph_key_count
            ):
                return None
            return MmdRuntimeReducedPoseResult(info, tuple(curves), report)
        except Exception as exc:
            logger.debug("generic reduction snapshot failed: %s", exc)
            return None

    get_generic_curves = snapshot

    def free(self) -> None:
        """Release the native reduced-pose handle exactly once."""
        if self._handle and self._lib:
            try:
                free_func = getattr(self._lib, "mmd_runtime_reduced_pose_free", None)
                if free_func is not None:
                    free_func(self._handle)
            except Exception as exc:
                logger.debug("mmd_runtime_reduced_pose_free failed: %s", exc)
            self._handle = None

    @property
    def handle(self) -> c_void_p:
        """Raw reduced-pose handle for advanced integrations."""
        return self._handle

    def __del__(self):
        self.free()

    def __repr__(self):
        return f"<MmdRuntimeReducedPose handle={self._handle}>"


def reduce_dense_pose(
    model: MmdRuntimeModel,
    dense_batch: MmdRuntimeBatchEvaluation,
    *,
    model_identity: int = 0,
    start_frame: float = 0.0,
    frame_step: float = 1.0,
    target: int = MMD_RUNTIME_REDUCTION_TARGET_DCC_CUBIC,
    tolerances: Optional[object] = None,
) -> Optional["MmdRuntimeReducedPoseResult"]:
    """Return detached generic reduced curves, freeing the native handle.

    A ``None`` result is an explicit unsupported/failure outcome for callers
    that must retain their dense bake path.
    """
    reduced = MmdRuntimeReducedPose.from_dense(
        model,
        dense_batch,
        model_identity=model_identity,
        start_frame=start_frame,
        frame_step=frame_step,
        target=target,
        tolerances=tolerances,
    )
    if reduced is None:
        return None
    try:
        return reduced.snapshot()
    finally:
        reduced.free()


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

    @staticmethod
    def _bone_track_curve_dto(
        native,
        *,
        first_key: bool,
    ) -> Optional[MmdRuntimeBoneTrackCurve]:
        """Validate and detach one semantic incoming-segment curve."""
        kind = int(native.kind)
        controls = tuple(float(value) for value in (native.x1, native.y1, native.x2, native.y2))
        if not all(math.isfinite(value) for value in controls):
            return None
        if first_key:
            if kind != MMD_RUNTIME_BONE_TRACK_CURVE_NONE or any(value != 0.0 for value in controls):
                return None
        elif kind != MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER:
            return None
        if kind == MMD_RUNTIME_BONE_TRACK_CURVE_CUBIC_BEZIER and not all(
            0.0 <= value <= 1.0 for value in controls
        ):
            return None
        return MmdRuntimeBoneTrackCurve(kind, *controls)

    @classmethod
    def _bone_track_key_dto(
        cls,
        native: MmdRuntimeFfiBoneTrackKey,
        *,
        descriptor: MmdRuntimeBoneTrackDescriptor,
        key_index: int,
    ) -> Optional[MmdRuntimeBoneTrackKey]:
        """Validate one copied key and detach it from caller-owned storage."""
        if int(native.bone_index) != descriptor.bone_index:
            return None
        position = tuple(float(value) for value in native.position_xyz)
        rotation = tuple(float(value) for value in native.rotation_xyzw)
        if not all(math.isfinite(value) for value in position + rotation):
            return None
        norm_squared = sum(value * value for value in rotation)
        if not 0.9999 <= norm_squared <= 1.0001:
            return None
        curves = tuple(
            cls._bone_track_curve_dto(curve, first_key=key_index == 0)
            for curve in (
                native.translation_x,
                native.translation_y,
                native.translation_z,
                native.rotation,
            )
        )
        if any(curve is None for curve in curves):
            return None
        return MmdRuntimeBoneTrackKey(
            int(native.bone_index),
            int(native.frame),
            position,
            rotation,
            curves[0],
            curves[1],
            curves[2],
            curves[3],
        )

    def bone_tracks(self) -> Optional[Tuple[MmdRuntimeBoneTrack, ...]]:
        """Return validated compiled authored keys, or ``None`` on any ABI mismatch.

        The returned values own no native pointers and remain valid after
        :meth:`free`. Missing capability or symbols fail closed; raw VMD keys
        are never substituted here.
        """
        if not self._handle or self._lib is None:
            return None
        required_symbols = (
            "mmd_runtime_clip_bone_track_count",
            "mmd_runtime_clip_bone_track_descriptor",
            "mmd_runtime_clip_bone_track_key_count",
            "mmd_runtime_clip_copy_bone_track_keys",
        )
        flags_func = getattr(self._lib, "mmd_runtime_feature_flags", None)
        if flags_func is None or any(getattr(self._lib, name, None) is None for name in required_symbols):
            return None
        try:
            if not int(flags_func()) & MMD_RUNTIME_FEATURE_CLIP_BONE_TRACK_INTROSPECTION:
                return None
            track_count = int(self._lib.mmd_runtime_clip_bone_track_count(self._handle))
            tracks = []
            for track_index in range(track_count):
                native_descriptor = MmdRuntimeFfiBoneTrackDescriptor()
                status = int(
                    self._lib.mmd_runtime_clip_bone_track_descriptor(
                        self._handle,
                        track_index,
                        ctypes.byref(native_descriptor),
                    )
                )
                if status != MMD_RUNTIME_STATUS_OK:
                    return None
                descriptor = MmdRuntimeBoneTrackDescriptor(
                    int(native_descriptor.bone_index),
                    int(native_descriptor.key_count),
                )
                key_count = int(
                    self._lib.mmd_runtime_clip_bone_track_key_count(self._handle, track_index)
                )
                if key_count != descriptor.key_count:
                    return None
                native_keys = (MmdRuntimeFfiBoneTrackKey * key_count)()
                written = c_size_t(0)
                status = int(
                    self._lib.mmd_runtime_clip_copy_bone_track_keys(
                        self._handle,
                        track_index,
                        native_keys if key_count else None,
                        key_count,
                        ctypes.byref(written),
                    )
                )
                if status != MMD_RUNTIME_STATUS_OK or int(written.value) != key_count:
                    return None
                keys = tuple(
                    self._bone_track_key_dto(
                        native_key,
                        descriptor=descriptor,
                        key_index=key_index,
                    )
                    for key_index, native_key in enumerate(native_keys)
                )
                if any(key is None for key in keys):
                    return None
                tracks.append(MmdRuntimeBoneTrack(descriptor, keys))
            return tuple(tracks)
        except Exception as exc:
            logger.error("MmdRuntimeClip.bone_tracks failed: %s", exc, exc_info=True)
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
        self._call_lock = threading.RLock()

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

    def get_gravity(self) -> Optional[Tuple[float, float, float]]:
        """Return finite world gravity, failing closed for missing/malformed runtimes."""
        if not self._handle or self._lib is None or not self._physics_features_enabled():
            return None
        func = getattr(self._lib, "mmd_runtime_physics_world_get_gravity", None)
        if func is None:
            return None
        gravity = (c_float * 3)()
        with self._call_lock:
            try:
                if int(func(self._handle, gravity)) != MMD_RUNTIME_STATUS_OK:
                    return None
            except Exception as exc:
                logger.error("get_gravity failed: %s", exc, exc_info=True)
                return None
        result = tuple(float(value) for value in gravity)
        return result if all(math.isfinite(value) for value in result) else None

    def set_gravity(self, gravity_xyz: Sequence[float]) -> bool:
        """Set finite XYZ gravity; invalid input never reaches the native ABI."""
        if not self._handle or self._lib is None or not self._physics_features_enabled():
            return False
        values = _finite_floats(gravity_xyz, 3)
        func = getattr(self._lib, "mmd_runtime_physics_world_set_gravity", None)
        if values is None or func is None:
            return False
        buffer = (c_float * 3)(*values)
        with self._call_lock:
            try:
                return int(func(self._handle, buffer)) == MMD_RUNTIME_STATUS_OK
            except Exception as exc:
                logger.error("set_gravity failed: %s", exc, exc_info=True)
                return False

    def copy_rigidbody_bindings(self) -> Optional[List[Tuple[int, int]]]:
        """Copy all bindings and verify that the native count is self-consistent."""
        count = self.rigidbody_count()
        if count is None or not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_physics_world_copy_rigidbody_bindings", None)
        if func is None:
            return None
        buffer = (MmdRuntimeFfiPhysicsRigidbodyBinding * count)()
        out_count = c_size_t(0)
        with self._call_lock:
            try:
                status = int(func(self._handle, buffer, c_size_t(count), ctypes.byref(out_count)))
            except Exception as exc:
                logger.error("copy_rigidbody_bindings failed: %s", exc, exc_info=True)
                return None
        if status != MMD_RUNTIME_STATUS_OK or int(out_count.value) != count:
            return None
        result = [(int(binding.bone_index), int(binding.mode)) for binding in buffer]
        if any(bone_index < -1 for bone_index, _mode in result):
            return None
        return result

    def physics_driven_bone_mask(self, bone_count: int) -> Optional[List[int]]:
        """Return an exact-size driven-bone mask; short buffers fail closed natively."""
        count = _as_positive_integral_count(bone_count)
        if count is None or not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_physics_world_physics_driven_bone_mask", None)
        if func is None or not self._physics_features_enabled():
            return None
        buffer = (c_uint8 * count)()
        with self._call_lock:
            try:
                status = int(func(self._handle, buffer, c_size_t(count)))
            except Exception as exc:
                logger.error("physics_driven_bone_mask failed: %s", exc, exc_info=True)
                return None
        if status != MMD_RUNTIME_STATUS_OK or any(value not in (0, 1) for value in buffer):
            return None
        return [int(value) for value in buffer]

    def evaluate_host_frame(
        self,
        instance: "MmdRuntimeInstance",
        *,
        local_position_offsets_xyz: Sequence[float],
        local_rotation_xyzw: Sequence[float],
        local_scales_xyz: Sequence[float],
        morph_weights: Sequence[float],
        ik_enabled: Sequence[int],
        action: int,
        dt_seconds: float = 0.0,
        ik_tolerance: float = 1.0e-2,
        ik_max_iterations_cap: int = 0,
    ) -> Optional[MmdRuntimeFfiPhysicsWorldStepReport]:
        """Atomically apply a validated host pose and SEED or STEP one frame.

        The caller-owned buffers remain alive for the whole call. Exact counts,
        finite values, normalized quaternions, advertised features, handle
        ownership, and action/dt are checked before crossing the ABI boundary.
        Per-handle locks reflect the upstream non-concurrent handle contract.
        """
        if not instance or instance._lib is not self._lib:
            return None
        func = getattr(self._lib, "mmd_runtime_evaluate_host_frame", None)
        if func is None or action not in (MMD_RUNTIME_PHYSICS_FRAME_ACTION_SEED, MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP):
            return None
        dt = _as_finite_float(dt_seconds)
        tolerance = _as_finite_float(ik_tolerance)
        if dt is None or tolerance is None or tolerance < 0.0 or (action == MMD_RUNTIME_PHYSICS_FRAME_ACTION_STEP and dt < 0.0):
            return None
        with instance._call_lock, self._call_lock:
            if (
                not self._handle
                or self._lib is None
                or not instance.handle
                or not self._physics_features_enabled()
            ):
                return None
            counts = instance._host_pose_counts()
            if counts is None:
                return None
            bone_count, morph_count, ik_count = counts
            positions = _finite_floats(local_position_offsets_xyz, bone_count * 3)
            rotations = _normalized_quaternions(local_rotation_xyzw, bone_count)
            scales = _finite_floats(local_scales_xyz, bone_count * 3)
            morphs = _finite_floats(morph_weights, morph_count)
            try:
                raw_ik_values = list(ik_enabled)
                ik_values = [int(value) for value in raw_ik_values]
            except (TypeError, ValueError, OverflowError):
                return None
            if (
                positions is None
                or rotations is None
                or scales is None
                or morphs is None
                or len(ik_values) != ik_count
                or any(value not in (0, 1) for value in ik_values)
                or any(raw != converted for raw, converted in zip(raw_ik_values, ik_values))
            ):
                return None
            pos_buf = (c_float * len(positions))(*positions)
            rot_buf = (c_float * len(rotations))(*rotations)
            scale_buf = (c_float * len(scales))(*scales)
            morph_buf = (c_float * len(morphs))(*morphs)
            ik_buf = (c_uint8 * len(ik_values))(*ik_values)
            view = MmdRuntimeFfiHostPoseView(
                pos_buf, rot_buf, scale_buf, c_size_t(bone_count),
                morph_buf, c_size_t(morph_count), ik_buf, c_size_t(ik_count),
            )
            report = MmdRuntimeFfiPhysicsWorldStepReport()
            try:
                status = int(func(
                    instance.handle,
                    self._handle,
                    ctypes.byref(view),
                    c_uint32(action),
                    c_float(dt),
                    c_float(tolerance),
                    c_uint32(max(0, int(ik_max_iterations_cap))),
                    ctypes.byref(report),
                ))
            except (TypeError, ValueError, OverflowError) as exc:
                logger.error("evaluate_host_frame failed: %s", exc, exc_info=True)
                return None
        return report if status == MMD_RUNTIME_STATUS_OK else None

    def free(self) -> None:
        """Free the native physics world handle."""
        lock = getattr(self, "_call_lock", None)
        if lock is None:
            lock = self._call_lock = threading.RLock()
        with lock:
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
        self._call_lock = threading.RLock()

    def _host_pose_counts(self) -> Optional[Tuple[int, int, int]]:
        """Return exact host-pose buffer counts from the live instance."""
        if not self._handle or self._lib is None:
            return None
        try:
            world_len = int(self._lib.mmd_runtime_instance_world_matrix_f32_len(self._handle))
            morph_count = int(self._lib.mmd_runtime_instance_morph_weight_len(self._handle))
            ik_count = int(self._lib.mmd_runtime_instance_ik_enabled_len(self._handle))
        except Exception as exc:
            logger.error("host pose count query failed: %s", exc, exc_info=True)
            return None
        if world_len < 0 or world_len % 16 != 0 or morph_count < 0 or ik_count < 0:
            return None
        return world_len // 16, morph_count, ik_count

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

    def evaluate_clip_frame_before_physics(self, clip: MmdRuntimeClip, frame: float) -> bool:
        """Evaluate the clip up to the physics boundary (pre-physics pose)."""
        if not self._handle or not clip or not clip.handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_evaluate_clip_frame_before_physics", None)
        if func is None:
            return False
        try:
            status = int(func(self._handle, clip.handle, c_float(frame)))
            return status == MMD_RUNTIME_STATUS_OK
        except Exception as exc:
            logger.error("evaluate_clip_frame_before_physics failed: %s", exc, exc_info=True)
            return False

    def evaluate_current_pose_after_physics(self) -> bool:
        """Finalize the pose after physics has been stepped (post-physics IK, append, etc.)."""
        if not self._handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_evaluate_current_pose_after_physics", None)
        if func is None:
            return False
        try:
            status = int(func(self._handle))
            return status == MMD_RUNTIME_STATUS_OK
        except Exception as exc:
            logger.error("evaluate_current_pose_after_physics failed: %s", exc, exc_info=True)
            return False

    def evaluate_current_pose_before_physics(self) -> bool:
        """Evaluate pre-physics pose chain (IK, append) using current bone state."""
        if not self._handle or self._lib is None:
            return False
        func = getattr(self._lib, "mmd_runtime_instance_evaluate_current_pose_before_physics", None)
        if func is None:
            return False
        try:
            status = int(func(self._handle))
            return status == MMD_RUNTIME_STATUS_OK
        except Exception as exc:
            logger.error("evaluate_current_pose_before_physics failed: %s", exc, exc_info=True)
            return False

    def apply_physics_world_matrices(
        self,
        matrices_flat: Sequence[float],
        mask: Optional[Sequence[int]] = None,
    ) -> Optional[int]:
        """Inject external world matrices (mmd-anim space) into the instance.

        Args:
            matrices_flat: bone_count * 16 floats in mmd-anim column-major layout.
            mask: Optional per-bone uint8 mask (1=apply, 0=skip). Length = bone_count.
        Returns:
            Number of updated bones, or None on failure.
        """
        if not self._handle or self._lib is None:
            return None
        func = getattr(self._lib, "mmd_runtime_instance_apply_physics_world_matrices", None)
        if func is None:
            return None
        try:
            mat_len = len(matrices_flat)
            mat_buf = (c_float * mat_len)(*matrices_flat)
            if mask is not None:
                mask_len = len(mask)
                mask_buf = (c_uint8 * mask_len)(*mask)
            else:
                mask_buf = None
                mask_len = 0
            out_count = c_size_t(0)
            status = int(func(
                self._handle,
                mat_buf, c_size_t(mat_len),
                mask_buf, c_size_t(mask_len),
                ctypes.byref(out_count),
            ))
            if status != MMD_RUNTIME_STATUS_OK:
                logger.error("apply_physics_world_matrices failed: status=%s", status)
                return None
            return int(out_count.value)
        except Exception as exc:
            logger.error("apply_physics_world_matrices failed: %s", exc, exc_info=True)
            return None

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
        lock = getattr(self, "_call_lock", None)
        if lock is None:
            lock = self._call_lock = threading.RLock()
        with lock:
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
