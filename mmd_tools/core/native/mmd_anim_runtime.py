"""
mmd-anim (Rust) の C ABI を ctypes でラップするモジュール。

このファイルは Maya 環境で mmd-anim-ffi の共有ライブラリをロードし、
PMX モデルと VMD モーションの忠実なランタイム評価を提供します。

対応する主な機能 (mmd-anim-ffi v1 ABI 基準):
- PMX バイト列からのモデル構築
- VMD バイト列 + モデルからのクリップ構築
- 任意フレーム (float) での評価
- ワールド行列、スキニング行列、モーフウェイト、IK 状態の取得

注意:
- 物理演算は mmd-anim 側で提供されません (ホスト側で別途対応)。
- 事前ビルドされた mmd_anim_ffi.dll (Windows) / .dylib (macOS) が必要です。
- ライブラリが見つからない場合、すべての公開 API は安全に失敗 (None / False) します。

ファイルヘッダ / コーディング規約:
- Google スタイル docstring
- snake_case / PascalCase 遵守
- プロジェクト logger 使用
"""

from __future__ import annotations

import ctypes
import os
import platform
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    byref,
    c_bool,
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

logger = get_logger(__name__)

# ------------------------------------------------------------------
# ABI 定数 (mmd_runtime.h より)
# ------------------------------------------------------------------
MMD_RUNTIME_ABI_VERSION = 1

# ライブラリ名候補
if platform.system() == "Windows":
    _LIB_NAMES = ["mmd_anim_ffi.dll"]
elif platform.system() == "Darwin":
    _LIB_NAMES = ["libmmd_anim_ffi.dylib", "mmd_anim_ffi.dylib"]
else:
    _LIB_NAMES = ["libmmd_anim_ffi.so", "mmd_anim_ffi.so"]

# 検索パス候補 (相対はパッケージ位置基準)
_THIS_FILE = Path(__file__).resolve()
_PACKAGE_ROOT = _THIS_FILE.parents[2]  # mmd_tools/

_CANDIDATE_PATHS: List[Path] = [
    # 1. 環境変数で明示指定
    Path(os.environ.get("MMD_ANIM_FFI_PATH", "")) if os.environ.get("MMD_ANIM_FFI_PATH") else None,
    # 2. 推奨配置: mmd_tools/native/<platform>/
    _PACKAGE_ROOT / "native" / ("win64" if platform.system() == "Windows" else "macos" if platform.system() == "Darwin" else "linux"),
    # 3. 開発用: external/mmd-anim ビルド成果物 (参考)
    _PACKAGE_ROOT.parent / "external" / "mmd-anim" / "target" / "release",
    # 4. カレントディレクトリ / Maya プラグイン隣接
    Path.cwd(),
    Path("plug-ins"),
]

# グローバルキャッシュ
_runtime_lib: Optional[CDLL] = None
_runtime_lib_path: Optional[Path] = None


# ------------------------------------------------------------------
# 構造体定義
# ------------------------------------------------------------------

class MmdRuntimeFfiByteBuffer(Structure):
    """
    Rust 側の MmdRuntimeFfiByteBuffer (repr(C)) に対応する ctypes Structure。

    Fields:
        data: バイト列のポインタ (uint8_t*)
        len:  バイト列の長さ (size_t)

    mmd_runtime_byte_buffer_free にそのまま渡す値型の構造体。
    """
    _fields_ = [
        ("data", POINTER(c_uint8)),
        ("len", c_size_t),
    ]


def _find_library() -> Optional[Path]:
    """mmd-anim-ffi 共有ライブラリを複数の候補パスから探す。"""
    for raw_base in _CANDIDATE_PATHS:
        if raw_base is None:
            continue
        base = Path(raw_base)
        if not base.exists():
            continue

        # 環境変数などで「ファイル本体」を直接指定されたケースを最初に処理
        if base.is_file():
            if base.name in _LIB_NAMES:
                return base.resolve()
            # ファイル名が一致しない場合はこの候補をスキップ
            continue

        # ディレクトリとして扱う通常ケース
        for name in _LIB_NAMES:
            candidate = base / name
            if candidate.exists():
                return candidate.resolve()
            # サブディレクトリも少し見る (debug/release など)
            for sub in ("", "debug", "release"):
                c2 = (base / sub / name) if sub else candidate
                if c2.exists():
                    return c2.resolve()

    # 最後の手段: システムサーチ (PATH や カレントに置いてある場合)
    for name in _LIB_NAMES:
        try:
            # CDLL は名前だけで探せる場合がある
            # ここでは実体パスを返したいので、find_library 的なことはせず None のまま
            pass
        except Exception:
            pass
    return None


def _setup_function_signatures(lib: CDLL) -> None:
    """ctypes の argtypes / restype を設定して安全に呼び出せるようにする。"""
    # ABI バージョン
    lib.mmd_runtime_abi_version.restype = c_uint32
    lib.mmd_runtime_abi_version.argtypes = []

    # 解放
    lib.mmd_runtime_model_free.restype = None
    lib.mmd_runtime_model_free.argtypes = [c_void_p]

    lib.mmd_runtime_clip_free.restype = None
    lib.mmd_runtime_clip_free.argtypes = [c_void_p]

    lib.mmd_runtime_instance_free.restype = None
    lib.mmd_runtime_instance_free.argtypes = [c_void_p]

    # byte buffer: 正しい構造体で定義する
    lib.mmd_runtime_byte_buffer_free.restype = None
    lib.mmd_runtime_byte_buffer_free.argtypes = [MmdRuntimeFfiByteBuffer]

    # モデル作成 (最も実用的)
    lib.mmd_runtime_model_create_from_pmx_bytes.restype = c_void_p
    lib.mmd_runtime_model_create_from_pmx_bytes.argtypes = [POINTER(c_uint8), c_size_t]

    # クリップ作成
    lib.mmd_runtime_clip_create_from_vmd_bytes_for_model.restype = c_void_p
    lib.mmd_runtime_clip_create_from_vmd_bytes_for_model.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]

    # インスタンス
    lib.mmd_runtime_instance_create_for_model.restype = c_void_p
    lib.mmd_runtime_instance_create_for_model.argtypes = [c_void_p]

    # 評価
    lib.mmd_runtime_instance_evaluate_clip_frame.restype = c_bool
    lib.mmd_runtime_instance_evaluate_clip_frame.argtypes = [c_void_p, c_void_p, c_float]
    try:
        lib.mmd_runtime_instance_evaluate_clip_frame_with_ik_options.restype = c_bool
        lib.mmd_runtime_instance_evaluate_clip_frame_with_ik_options.argtypes = [
            c_void_p,
            c_void_p,
            c_float,
            c_float,
            c_uint32,
        ]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose evaluate_clip_frame_with_ik_options")

    # 出力取得 (コピー版を優先)
    lib.mmd_runtime_instance_world_matrix_f32_len.restype = c_size_t
    lib.mmd_runtime_instance_world_matrix_f32_len.argtypes = [c_void_p]

    lib.mmd_runtime_instance_copy_world_matrices.restype = c_bool
    lib.mmd_runtime_instance_copy_world_matrices.argtypes = [c_void_p, POINTER(c_float), c_size_t]

    lib.mmd_runtime_instance_morph_weight_len.restype = c_size_t
    lib.mmd_runtime_instance_morph_weight_len.argtypes = [c_void_p]

    lib.mmd_runtime_instance_copy_morph_weights.restype = c_bool
    lib.mmd_runtime_instance_copy_morph_weights.argtypes = [c_void_p, POINTER(c_float), c_size_t]

    lib.mmd_runtime_instance_ik_enabled_len.restype = c_size_t
    lib.mmd_runtime_instance_ik_enabled_len.argtypes = [c_void_p]

    lib.mmd_runtime_instance_copy_ik_enabled.restype = c_bool
    lib.mmd_runtime_instance_copy_ik_enabled.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]

    # --- parsed-model ABI (optional, guarded) ---
    _setup_parsed_model_signatures(lib)


def _setup_parsed_model_signatures(lib: CDLL) -> None:
    """
    parsed-model シンボル (mmd_runtime_parsed_model_*) の argtypes/restype を
    getattr / try で安全に設定する。

    古い DLL はこれらのシンボルをエクスポートしていない可能性があるため、
    各シンボルの有無を個別にチェックする。
    設定に失敗しても全体としてはフォールバックする。
    """
    try:
        # 作成 / 解放
        _set_sig(
            lib,
            "mmd_runtime_parsed_model_create_from_pmx_bytes",
            c_void_p,
            [POINTER(c_uint8), c_size_t],
        )
        _set_sig(lib, "mmd_runtime_parsed_model_free", None, [c_void_p])

        # カウント (const model* → size_t)
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_count", c_size_t, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_index_count", c_size_t, [c_void_p])
        _set_sig(
            lib,
            "mmd_runtime_parsed_model_material_group_count",
            c_size_t,
            [c_void_p],
        )
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_count", c_size_t, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_offset_count", c_size_t, [c_void_p])

        # ポインターアクセサ (const model* → const float* / const uint32_t*)
        _set_sig(lib, "mmd_runtime_parsed_model_positions", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_normals", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_uvs", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_edge_scale", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_indices", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_skin_indices", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_skin_weights", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_material_groups", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_spans", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_vertex_indices", c_void_p, [c_void_p])
        _set_sig(lib, "mmd_runtime_parsed_model_vertex_morph_position_offsets", c_void_p, [c_void_p])

        # byte buffers (const model* → MmdRuntimeFfiByteBuffer by value)
        _set_sig(
            lib,
            "mmd_runtime_parsed_model_vertex_morph_name",
            MmdRuntimeFfiByteBuffer,
            [c_void_p, c_size_t],
        )
        _set_sig(lib, "mmd_runtime_parsed_model_metadata_json", MmdRuntimeFfiByteBuffer, [c_void_p])
    except Exception as exc:
        logger.debug(f"parsed-model ABI のシグネチャ設定中にエラー: {exc}")


def _set_sig(
    lib: CDLL, name: str, restype: Any, argtypes: List[Any]
) -> None:
    """
    シンボル name が lib に存在すれば argtypes/restype を設定する。
    存在しなければ何もしない。
    """
    func = getattr(lib, name, None)
    if func is None:
        logger.debug(f"parsed-model ABI シンボル '{name}' は DLL に存在しません")
        return
    func.restype = restype
    func.argtypes = argtypes


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


def get_mmd_runtime_library() -> Optional[CDLL]:
    """
    mmd-anim-ffi 共有ライブラリを取得する (キャッシュ付き)。

    Returns:
        ロード済み CDLL インスタンス。失敗時は None。
    """
    global _runtime_lib, _runtime_lib_path

    if _runtime_lib is not None:
        return _runtime_lib if _runtime_lib is not False else None

    path = _find_library()
    if path is None:
        logger.info("mmd-anim runtime library が見つかりませんでした (事前ビルドの配置を確認してください)")
        _runtime_lib = False
        return None

    try:
        lib = ctypes.CDLL(str(path))
        _setup_function_signatures(lib)

        abi = lib.mmd_runtime_abi_version()
        if abi != MMD_RUNTIME_ABI_VERSION:
            logger.warning(
                f"mmd-anim runtime ABI バージョンが一致しません: got={abi}, expected={MMD_RUNTIME_ABI_VERSION}"
            )
            # 互換性の範囲で続行するか、厳格に拒否するかは将来調整
        else:
            logger.info(f"mmd-anim runtime library をロードしました: {path} (ABI {abi})")

        _runtime_lib = lib
        _runtime_lib_path = path
        return lib

    except Exception as e:
        logger.error(f"mmd-anim runtime library のロードに失敗しました: {path} - {e}", exc_info=True)
        _runtime_lib = False
        return None


def is_mmd_runtime_available() -> bool:
    """mmd-anim ランタイムが利用可能かどうかを返す。"""
    return get_mmd_runtime_library() is not None


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
                logger.error("mmd_runtime_model_create_from_pmx_bytes が NULL を返しました")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeModel.from_pmx_bytes に失敗: {e}", exc_info=True)
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
            except Exception:
                pass
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
                logger.error("mmd_runtime_clip_create_from_vmd_bytes_for_model が NULL を返しました")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeClip.from_vmd_bytes_for_model に失敗: {e}", exc_info=True)
            return None

    @property
    def handle(self) -> c_void_p:
        return self._handle

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_clip_free(self._handle)
            except Exception:
                pass
            self._handle = None

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
                logger.error("mmd_runtime_instance_create_for_model が NULL を返しました")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdRuntimeInstance.for_model に失敗: {e}", exc_info=True)
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
            logger.error(f"evaluate_clip_frame 失敗 (frame={frame}): {e}", exc_info=True)
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
            logger.warning("mmd-anim runtime が IK option 評価 ABI を提供していません")
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
                f"evaluate_clip_frame_with_ik_options 失敗 (frame={frame}): {e}",
                exc_info=True,
            )
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
            logger.error(f"get_world_matrices 失敗: {e}", exc_info=True)
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
            logger.error(f"get_morph_weights 失敗: {e}", exc_info=True)
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
            logger.error(f"get_ik_enabled 失敗: {e}", exc_info=True)
            return None

    def free(self) -> None:
        if self._handle and self._lib:
            try:
                self._lib.mmd_runtime_instance_free(self._handle)
            except Exception:
                pass
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
    get_mmd_runtime_library()  # ロードをトリガー
    return _runtime_lib_path


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
            logger.debug("parsed-model create シンボルがありません")
            return None
        try:
            buf = (c_uint8 * len(pmx_bytes)).from_buffer_copy(pmx_bytes)
            handle = func(buf, len(pmx_bytes))
            if not handle:
                logger.error("mmd_runtime_parsed_model_create_from_pmx_bytes が NULL を返しました")
                return None
            return cls(lib, handle)
        except Exception as e:
            logger.error(f"MmdParsedModel.from_pmx_bytes に失敗: {e}", exc_info=True)
            return None

    # ---- 解放 ----

    def free(self) -> None:
        """明示的にリソースを解放する。"""
        if self._handle and self._lib:
            func = getattr(self._lib, "mmd_runtime_parsed_model_free", None)
            if func:
                try:
                    func(self._handle)
                except Exception:
                    pass
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
            logger.error(f"positions 読み取り失敗: {e}")
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
            logger.error(f"normals 読み取り失敗: {e}")
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
            logger.error(f"uvs 読み取り失敗: {e}")
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
            logger.error(f"edge_scale 読み取り失敗: {e}")
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
            logger.error(f"indices 読み取り失敗: {e}")
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
            logger.error(f"skin_indices 読み取り失敗: {e}")
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
            logger.error(f"skin_weights 読み取り失敗: {e}")
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
            logger.error(f"material_groups 読み取り失敗: {e}")
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
            logger.error(f"vertex_morph_spans 読み取り失敗: {e}")
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
            logger.error(f"vertex_morph_vertex_indices 読み取り失敗: {e}")
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
            logger.error(f"vertex_morph_position_offsets 読み取り失敗: {e}")
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
            logger.error(f"vertex_morph_names 読み取り失敗: {e}")
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
            logger.error(f"metadata_json 読み取り失敗: {e}")
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
            except Exception:
                pass

    def __repr__(self):
        return f"<MmdParsedModel handle={self._handle}>"


# ---- エイリアス（後方互換） ----
# MmdParsedModel は新しいクラス名。古いコードで使われている
# 可能性は低いが、混乱を避けるためエイリアスは用意しない。


# ------------------------------------------------------------------
# Phase 2 統合用ユーティリティ (C++ ノード連携のプレースホルダ)
# ------------------------------------------------------------------

def create_runtime_node_for_model(model_root: str, pmx_path: str, vmd_path: str = None) -> str:
    """
    Maya シーンに mmdRuntimeInstance ノードを作成し、モデルと関連付けるヘルパー。

    C++ ノード (mmdRuntimeInstance) がロードされている前提。
    ノードを作成し、pmx/vmd パスを設定、time を接続する。

    戻り値: 作成したノード名
    """
    import maya.cmds as cmds

    node = cmds.createNode("mmdRuntimeInstance", name="mmdRuntimeInstance#")

    # パス設定 (ノードの aPmxData / aVmdData に string として)
    cmds.setAttr(f"{node}.pmxData", pmx_path, type="string")
    if vmd_path:
        cmds.setAttr(f"{node}.vmdData", vmd_path, type="string")

    # time 接続 (現在の時間に連動)
    # 簡易: expression や scriptJob で駆動。フルは time1.outTime を接続
    try:
        cmds.connectAttr("time1.outTime", f"{node}.time", force=True)
    except:
        pass

    # モデルルートにメッセージで関連付け (将来のドライバ用)
    if cmds.objExists(model_root):
        try:
            cmds.addAttr(model_root, ln="mmdRuntimeNode", at="message")
            cmds.connectAttr(f"{node}.message", f"{model_root}.mmdRuntimeNode", force=True)
        except:
            pass

    return node


def get_runtime_matrices_from_node(node: str) -> list:
    """ノードから現在の world matrices を取得 (float flat list)"""
    import maya.cmds as cmds
    try:
        return cmds.getAttr(f"{node}.worldMatrices[*]") or []
    except:
        return []
