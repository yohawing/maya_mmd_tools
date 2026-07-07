"""Native JSON and PMX parts export helpers for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
import json
from ctypes import CDLL, c_float, c_uint8, c_uint32, c_void_p
from typing import Any, Callable, Optional

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer

logger = get_logger(__name__)

_JSON_EXPORT_SYMBOLS = {
    "vmd": "mmd_runtime_export_vmd_animation_json",
    "pmx": "mmd_runtime_export_pmx_model_json",
    "pmd": "mmd_runtime_export_pmd_model_json",
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


def is_native_json_export_available(
    format_kind: str,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> bool:
    """
    指定 MMD format の JSON writer FFI が利用可能かどうかを返す。

    Args:
        format_kind: ``"vmd"`` / ``"pmx"`` / ``"pmd"``。

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


def export_vmd_animation_json(
    payload: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """VmdParsedAnimation JSON から VMD バイト列を native writer で生成する。"""
    return _export_json_with_symbol(_JSON_EXPORT_SYMBOLS["vmd"], payload, get_library)


def export_pmx_model_json(
    payload: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """PmxParsedModel JSON から PMX バイト列を native writer で生成する。"""
    return _export_json_with_symbol(_JSON_EXPORT_SYMBOLS["pmx"], payload, get_library)


def export_pmd_model_json(
    payload: Any,
    get_library: Optional[Callable[[], Optional[CDLL]]] = None,
) -> Optional[bytes]:
    """PmdParsedModel JSON から PMD バイト列を native writer で生成する。"""
    return _export_json_with_symbol(_JSON_EXPORT_SYMBOLS["pmd"], payload, get_library)


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
