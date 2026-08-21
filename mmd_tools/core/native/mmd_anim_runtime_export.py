"""Native JSON and PMX parts export helpers for the mmd-anim runtime FFI."""

from __future__ import annotations

from array import array
import ctypes
import json
import math
from ctypes import CDLL, c_float, c_uint8, c_uint32, c_void_p
from typing import Any, Callable, Optional

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer

logger = get_logger(__name__)


class MmdAnimRuntimeExportError(RuntimeError):
    """Raised when a native format export cannot be completed safely."""

_JSON_EXPORT_SYMBOLS = {
    "pmx": "mmd_runtime_export_pmx_model_json",
}


def _resolve_library(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> Optional[CDLL]:
    return (get_library or _runtime_loader.get_mmd_runtime_library)()


def is_native_pmx_parts_export_available(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> bool:
    """
    PMX parts export の DLL シンボル群が利用可能かどうかを返す。

    Returns:
        PMX metadata/geometry から PMX バイト列を書き出す ABI があれば True。
    """
    lib = _resolve_library(get_library)
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_export_pmx_from_parts") and hasattr(lib, "mmd_runtime_byte_buffer_free")


def is_native_vmd_parts_export_available(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> bool:
    """Return whether the typed VMD parts ABI and its release function exist."""

    lib = _resolve_library(get_library)
    return bool(
        lib is not None
        and hasattr(lib, "mmd_runtime_export_vmd_from_parts")
        and hasattr(lib, "mmd_runtime_byte_buffer_free")
    )


def _encode_export_metadata(metadata: Any) -> Optional[bytes]:
    """PMX export metadata を UTF-8 JSON bytes に変換する。"""
    if isinstance(metadata, bytes):
        return metadata
    if isinstance(metadata, bytearray):
        return bytes(metadata)
    if isinstance(metadata, str):
        return metadata.encode("utf-8")
    try:
        return json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception as exc:
        logger.error("Failed to encode PMX export metadata: %s", exc)
        return None


def _float_buffer(values: Any, expected_len: int, name: str):
    """float 配列を ctypes buffer に変換する。長さ不一致時は None。"""
    try:
        flat = [float(value) for value in values]
    except Exception as exc:
        logger.error("PMX export %s buffer is not float-compatible: %s", name, exc)
        return None
    if len(flat) != expected_len:
        logger.error("PMX export %s buffer length mismatch: expected %s, got %s", name, expected_len, len(flat))
        return None
    return (c_float * len(flat))(*flat)


def _uint32_buffer(values: Any, expected_len: int, name: str):
    """uint32 配列を ctypes buffer に変換する。長さ不一致時は None。"""
    try:
        flat = [int(value) for value in values]
    except Exception as exc:
        logger.error("PMX export %s buffer is not int-compatible: %s", name, exc)
        return None
    if len(flat) != expected_len:
        logger.error("PMX export %s buffer length mismatch: expected %s, got %s", name, expected_len, len(flat))
        return None
    return (c_uint32 * len(flat))(*flat)


def _byte_buffer_to_bytes(lib: CDLL, buffer: MmdRuntimeFfiByteBuffer) -> Optional[bytes]:
    """FFI byte buffer を Python bytes へコピーし、必ず native buffer を解放する。"""
    free_func = getattr(lib, "mmd_runtime_byte_buffer_free", None)
    if free_func is None:
        return None
    try:
        if not buffer.data or buffer.len == 0:
            return None
        addr = ctypes.cast(buffer.data, c_void_p).value
        if addr is None or addr == 0:
            return None
        raw_bytes = (c_uint8 * buffer.len).from_address(addr)
        return bytes(raw_bytes)
    finally:
        free_func(buffer)


def _last_error_message(lib: CDLL) -> str:
    """Copy the thread-local native error before another FFI call can replace it."""

    getter = getattr(lib, "mmd_runtime_last_error_message", None)
    if getter is None:
        return "native runtime did not provide an error message"
    try:
        value = getter()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace") or "native export failed"
        return str(value or "native export failed")
    except Exception as exc:
        return "native runtime error message unavailable: {}".format(exc)


def _strict_native_buffer_to_bytes(
    lib: CDLL,
    buffer: MmdRuntimeFfiByteBuffer,
    *,
    symbol: str,
) -> bytes:
    """Copy and release one returned buffer exactly once, rejecting malformed output."""

    free_func = getattr(lib, "mmd_runtime_byte_buffer_free", None)
    if free_func is None:
        raise MmdAnimRuntimeExportError("{} requires mmd_runtime_byte_buffer_free".format(symbol))
    try:
        data_address = ctypes.cast(buffer.data, c_void_p).value if buffer.data else None
        if data_address is None or int(buffer.len) <= 0:
            detail = _last_error_message(lib)
            raise MmdAnimRuntimeExportError("{} failed: {}".format(symbol, detail))
        try:
            return bytes((c_uint8 * int(buffer.len)).from_address(data_address))
        except (TypeError, ValueError, OverflowError, OSError) as exc:
            raise MmdAnimRuntimeExportError(
                "{} returned an invalid byte buffer: {}".format(symbol, exc)
            ) from exc
    finally:
        free_func(buffer)


def _strict_u32_values(values: Any, name: str) -> array:
    if isinstance(values, array) and values.typecode == "I" and values.itemsize == 4:
        return values
    try:
        raw_values = list(values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain uint32 values".format(name)) from exc
    if any(isinstance(value, bool) for value in raw_values):
        raise MmdAnimRuntimeExportError("{} must contain uint32 values".format(name))
    try:
        result = [int(value) for value in raw_values]
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain uint32 values".format(name)) from exc
    if any(value != raw or value < 0 or value > 0xFFFFFFFF for value, raw in zip(result, raw_values)):
        raise MmdAnimRuntimeExportError("{} contains a value outside the uint32 range".format(name))
    return array("I", result)


def _strict_f32_values(values: Any, name: str) -> array:
    if isinstance(values, array) and values.typecode == "f" and values.itemsize == 4:
        if any(not math.isfinite(value) for value in values):
            raise MmdAnimRuntimeExportError("{} contains a non-finite value".format(name))
        return values
    try:
        raw_values = list(values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain float values".format(name)) from exc
    if any(isinstance(value, bool) for value in raw_values):
        raise MmdAnimRuntimeExportError("{} must contain float values".format(name))
    try:
        result = [float(value) for value in raw_values]
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain float values".format(name)) from exc
    if any(not math.isfinite(value) for value in result):
        raise MmdAnimRuntimeExportError("{} contains a non-finite value".format(name))
    try:
        return array("f", result)
    except (OverflowError, ValueError) as exc:
        raise MmdAnimRuntimeExportError("{} contains a value outside the float32 range".format(name)) from exc


def _strict_u8_values(values: Any, name: str) -> array:
    if isinstance(values, array) and values.typecode == "B":
        return values
    try:
        raw_values = list(values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain byte values".format(name)) from exc
    if any(isinstance(value, bool) for value in raw_values):
        raise MmdAnimRuntimeExportError("{} must contain byte values".format(name))
    try:
        result = [int(value) for value in raw_values]
    except (TypeError, ValueError, OverflowError) as exc:
        raise MmdAnimRuntimeExportError("{} must contain byte values".format(name)) from exc
    if any(value != raw or value < 0 or value > 0xFF for value, raw in zip(result, raw_values)):
        raise MmdAnimRuntimeExportError("{} contains a value outside the byte range".format(name))
    return array("B", result)


def is_native_json_export_available(
    format_kind: str,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> bool:
    """
    指定 MMD format の JSON writer FFI が利用可能かどうかを返す。

    Args:
        format_kind: ``"pmx"``。

    Returns:
        対応する native JSON writer と byte buffer free ABI があれば True。
    """
    symbol = _JSON_EXPORT_SYMBOLS.get(str(format_kind).lower())
    if symbol is None:
        return False
    lib = _resolve_library(get_library)
    if lib is None:
        return False
    return hasattr(lib, symbol) and hasattr(lib, "mmd_runtime_byte_buffer_free")


def _export_json_with_symbol(
    symbol: str,
    payload: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """JSON payload を指定 native writer に渡して MMD bytes を返す。"""
    lib = _resolve_library(get_library)
    if lib is None:
        return None
    export_func = getattr(lib, symbol, None)
    if export_func is None or getattr(lib, "mmd_runtime_byte_buffer_free", None) is None:
        return None
    payload_bytes = _encode_export_metadata(payload)
    if not payload_bytes:
        return None
    payload_buf = (c_uint8 * len(payload_bytes)).from_buffer_copy(payload_bytes)
    try:
        native_buffer: MmdRuntimeFfiByteBuffer = export_func(payload_buf, len(payload_bytes))
        return _byte_buffer_to_bytes(lib, native_buffer)
    except Exception as exc:
        logger.error("%s failed: %s", symbol, exc, exc_info=True)
        return None


def export_pmx_model_json(
    payload: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """PmxParsedModel JSON から PMX バイト列を native writer で生成する。"""
    return _export_json_with_symbol(_JSON_EXPORT_SYMBOLS["pmx"], payload, get_library)


def export_pmx_from_parts(
    metadata: Any,
    positions_xyz: Any,
    normals_xyz: Any,
    uvs_xy: Any,
    indices: Any = None,
    skin_indices: Any = None,
    skin_weights: Any = None,
    edge_scale: Any = None,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """PMX metadata と flat geometry buffers から PMX バイト列を native exporter で生成する。

    Args:
        metadata: mmd-anim exporter metadata JSON。dict/list/str/bytes を受け付ける。
        positions_xyz: 頂点数 * 3 の flat float 配列。
        normals_xyz: 頂点数 * 3 の flat float 配列。
        uvs_xy: 頂点数 * 2 の flat float 配列。
        indices: 省略可能な uint32 index 配列。
        skin_indices: 省略可能な 頂点数 * 4 の uint32 bone index 配列。
        skin_weights: 省略可能な 頂点数 * 4 の float weight 配列。
        edge_scale: 省略可能な 頂点数分の float 配列。

    Returns:
        PMX bytes。DLL/シンボルが無い、または native export に失敗した場合は None。
    """
    lib = _resolve_library(get_library)
    if lib is None:
        return None
    export_func = getattr(lib, "mmd_runtime_export_pmx_from_parts", None)
    if export_func is None or getattr(lib, "mmd_runtime_byte_buffer_free", None) is None:
        return None

    metadata_bytes = _encode_export_metadata(metadata)
    if not metadata_bytes:
        return None
    try:
        positions_flat = [float(value) for value in positions_xyz]
    except Exception as exc:
        logger.error("PMX export positions buffer is not float-compatible: %s", exc)
        return None
    if not positions_flat or len(positions_flat) % 3 != 0:
        logger.error("PMX export positions buffer length must be a non-empty multiple of 3")
        return None

    vertex_count = len(positions_flat) // 3
    metadata_buf = (c_uint8 * len(metadata_bytes)).from_buffer_copy(metadata_bytes)
    positions_buf = (c_float * len(positions_flat))(*positions_flat)
    normals_buf = _float_buffer(normals_xyz, vertex_count * 3, "normals")
    uvs_buf = _float_buffer(uvs_xy, vertex_count * 2, "uvs")
    if normals_buf is None or uvs_buf is None:
        return None

    indices_buf = None
    index_count = 0
    if indices is not None:
        try:
            index_values = [int(value) for value in indices]
        except Exception as exc:
            logger.error("PMX export indices buffer is not int-compatible: %s", exc)
            return None
        index_count = len(index_values)
        indices_buf = (c_uint32 * index_count)(*index_values) if index_count else None

    if (skin_indices is None) != (skin_weights is None):
        logger.error("PMX export skin_indices and skin_weights must be supplied together")
        return None
    skin_indices_buf = None
    skin_weights_buf = None
    if skin_indices is not None and skin_weights is not None:
        skin_indices_buf = _uint32_buffer(skin_indices, vertex_count * 4, "skin_indices")
        skin_weights_buf = _float_buffer(skin_weights, vertex_count * 4, "skin_weights")
        if skin_indices_buf is None or skin_weights_buf is None:
            return None

    edge_scale_buf = None
    if edge_scale is not None:
        edge_scale_buf = _float_buffer(edge_scale, vertex_count, "edge_scale")
        if edge_scale_buf is None:
            return None

    try:
        native_buffer: MmdRuntimeFfiByteBuffer = export_func(
            metadata_buf,
            len(metadata_bytes),
            positions_buf,
            vertex_count,
            normals_buf,
            uvs_buf,
            indices_buf,
            index_count,
            skin_indices_buf,
            skin_weights_buf,
            edge_scale_buf,
        )
        return _byte_buffer_to_bytes(lib, native_buffer)
    except Exception as exc:
        logger.error("mmd_runtime_export_pmx_from_parts failed: %s", exc, exc_info=True)
        return None


def export_vmd_from_parts(
    metadata: Any,
    bone_name_indices: Any,
    bone_frames: Any,
    bone_translations_xyz: Any,
    bone_rotations_xyzw: Any,
    bone_interpolations: Any,
    morph_name_indices: Any,
    morph_frames: Any,
    morph_weights: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> bytes:
    """Export VMD bytes through the typed Bone/Morph SoA ABI.

    The metadata JSON contains only model/name tables and low-density VMD
    sections.  Every native result is copied before its byte buffer is freed;
    missing symbols, native errors, malformed inputs, and malformed output are
    surfaced as :class:`MmdAnimRuntimeExportError` so production callers can
    fail closed without selecting the legacy writer.
    """

    symbol = "mmd_runtime_export_vmd_from_parts"
    lib = _resolve_library(get_library)
    if lib is None:
        raise MmdAnimRuntimeExportError("{} is unavailable: runtime DLL could not be loaded".format(symbol))
    export_func = getattr(lib, symbol, None)
    free_func = getattr(lib, "mmd_runtime_byte_buffer_free", None)
    if export_func is None or free_func is None:
        raise MmdAnimRuntimeExportError("{} is unavailable: required ABI symbol is missing".format(symbol))

    metadata_bytes = _encode_export_metadata(metadata)
    if not metadata_bytes:
        raise MmdAnimRuntimeExportError("VMD parts metadata must be non-empty UTF-8 JSON")

    bone_names = _strict_u32_values(bone_name_indices, "bone_name_indices")
    bone_frame_values = _strict_u32_values(bone_frames, "bone_frames")
    bone_translation_values = _strict_f32_values(bone_translations_xyz, "bone_translations_xyz")
    bone_rotation_values = _strict_f32_values(bone_rotations_xyzw, "bone_rotations_xyzw")
    bone_interpolation_values = _strict_u8_values(bone_interpolations, "bone_interpolations")
    morph_names = _strict_u32_values(morph_name_indices, "morph_name_indices")
    morph_frame_values = _strict_u32_values(morph_frames, "morph_frames")
    morph_weight_values = _strict_f32_values(morph_weights, "morph_weights")
    bone_count = len(bone_names)
    morph_count = len(morph_names)
    if len(bone_frame_values) != bone_count:
        raise MmdAnimRuntimeExportError("bone_frames length does not match bone_name_indices")
    if len(bone_translation_values) != bone_count * 3:
        raise MmdAnimRuntimeExportError("bone_translations_xyz length does not match the bone SoA stride")
    if len(bone_rotation_values) != bone_count * 4:
        raise MmdAnimRuntimeExportError("bone_rotations_xyzw length does not match the bone SoA stride")
    if len(bone_interpolation_values) != bone_count * 64:
        raise MmdAnimRuntimeExportError("bone_interpolations length does not match the bone SoA stride")
    if len(morph_frame_values) != morph_count or len(morph_weight_values) != morph_count:
        raise MmdAnimRuntimeExportError("morph SoA lengths do not match morph_name_indices")

    def _u32_buffer(values: array):
        return (c_uint32 * len(values)).from_buffer(values) if values else None

    def _f32_buffer(values: array):
        return (c_float * len(values)).from_buffer(values) if values else None

    def _u8_buffer(values: array):
        return (c_uint8 * len(values)).from_buffer(values) if values else None

    metadata_buf = (c_uint8 * len(metadata_bytes)).from_buffer_copy(metadata_bytes)
    bone_names_buf = _u32_buffer(bone_names)
    bone_frames_buf = _u32_buffer(bone_frame_values)
    bone_translations_buf = _f32_buffer(bone_translation_values)
    bone_rotations_buf = _f32_buffer(bone_rotation_values)
    bone_interpolations_buf = _u8_buffer(bone_interpolation_values)
    morph_names_buf = _u32_buffer(morph_names)
    morph_frames_buf = _u32_buffer(morph_frame_values)
    morph_weights_buf = _f32_buffer(morph_weight_values)
    try:
        native_buffer: MmdRuntimeFfiByteBuffer = export_func(
            metadata_buf,
            len(metadata_bytes),
            bone_names_buf,
            bone_count,
            bone_frames_buf,
            len(bone_frame_values),
            bone_translations_buf,
            len(bone_translation_values),
            bone_rotations_buf,
            len(bone_rotation_values),
            bone_interpolations_buf,
            len(bone_interpolation_values),
            morph_names_buf,
            morph_count,
            morph_frames_buf,
            len(morph_frame_values),
            morph_weights_buf,
            len(morph_weight_values),
        )
    except MmdAnimRuntimeExportError:
        raise
    except Exception as exc:
        raise MmdAnimRuntimeExportError("{} call failed: {}".format(symbol, exc)) from exc
    return _strict_native_buffer_to_bytes(lib, native_buffer, symbol=symbol)


__all__ = [
    "MmdAnimRuntimeExportError",
    "is_native_pmx_parts_export_available",
    "is_native_vmd_parts_export_available",
    "is_native_json_export_available",
    "export_pmx_model_json",
    "export_pmx_from_parts",
    "export_vmd_from_parts",
]
