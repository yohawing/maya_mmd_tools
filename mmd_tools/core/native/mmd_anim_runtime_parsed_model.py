"""Parsed PMX model marshal helpers for the mmd-anim runtime FFI."""

from __future__ import annotations

import ctypes
from ctypes import CDLL, c_float, c_uint8, c_uint32, c_void_p
from typing import Callable, List, Optional, Tuple

from mmd_tools.core.logger import get_logger
from mmd_tools.core.native import mmd_anim_runtime_loader as _runtime_loader
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeFfiByteBuffer

logger = get_logger(__name__)


def get_mmd_runtime_library() -> Optional[CDLL]:
    """Compatibility indirection for tests that patch this module-level getter."""
    return _runtime_loader.get_mmd_runtime_library()


def _resolve_library(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> Optional[CDLL]:
    return (get_library or get_mmd_runtime_library)()


def is_native_pmx_parser_available(get_library: Optional[Callable[[], Optional[CDLL]]] = None) -> bool:
    """Return whether parsed-model creation ABI is available in the runtime library."""
    lib = _resolve_library(get_library)
    if lib is None:
        return False
    return hasattr(lib, "mmd_runtime_parsed_model_create_from_pmx_bytes")


class MmdParsedModel:
    """
    mmd_runtime_parsed_model_* ABI のラッパー。

    PMX バイト列からジオメトリ・スキン・マテリアルグループ・
    メタデータ（JSON）を読み出す。

    リソースは明示的な free() またはデストラクタで解放される。
    DLL またはシンボルが利用できない場合、from_pmx_bytes は None を返す。
    """

    _get_library: Callable[[], Optional[CDLL]] = staticmethod(get_mmd_runtime_library)

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
        lib = cls._get_library()
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
