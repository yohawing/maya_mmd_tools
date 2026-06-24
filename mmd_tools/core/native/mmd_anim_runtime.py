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
import json
import os
import platform
from ctypes import (
    CDLL,
    POINTER,
    Structure,
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


class MmdRuntimeFfiRigBone(Structure):
    _fields_ = [
        ("parent_slot", c_int32),
        ("rest_position_xyz", c_float * 3),
        ("flags", c_uint32),
        ("fixed_axis_xyz", c_float * 3),
    ]


MMD_RUNTIME_RIG_BONE_FIXED_AXIS = 1 << 0


class MmdRuntimeFfiRigIkLink(Structure):
    _fields_ = [
        ("bone_slot", c_uint32),
        ("has_angle_limit", c_bool),
        ("angle_limit_min_xyz", c_float * 3),
        ("angle_limit_max_xyz", c_float * 3),
    ]


class MmdRuntimeFfiIkSolveStats(Structure):
    _fields_ = [
        ("executed_iterations", c_uint32),
        ("link_steps", c_uint32),
        ("final_distance", c_float),
        ("break_reason", c_uint32),
    ]


class MmdRuntimeFfiAppendConfig(Structure):
    _fields_ = [
        ("ratio", c_float),
        ("affect_rotation", c_bool),
        ("affect_translation", c_bool),
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
    try:
        lib.mmd_runtime_instance_evaluate_rest_pose.restype = c_bool
        lib.mmd_runtime_instance_evaluate_rest_pose.argtypes = [c_void_p]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose evaluate_rest_pose")

    # 出力取得 (コピー版を優先)
    lib.mmd_runtime_instance_world_matrix_f32_len.restype = c_size_t
    lib.mmd_runtime_instance_world_matrix_f32_len.argtypes = [c_void_p]

    lib.mmd_runtime_instance_copy_world_matrices.restype = c_bool
    lib.mmd_runtime_instance_copy_world_matrices.argtypes = [c_void_p, POINTER(c_float), c_size_t]
    try:
        lib.mmd_runtime_instance_skinning_matrix_f32_len.restype = c_size_t
        lib.mmd_runtime_instance_skinning_matrix_f32_len.argtypes = [c_void_p]

        lib.mmd_runtime_instance_copy_skinning_matrices.restype = c_bool
        lib.mmd_runtime_instance_copy_skinning_matrices.argtypes = [c_void_p, POINTER(c_float), c_size_t]
    except AttributeError:
        logger.debug("mmd-anim runtime does not expose skinning matrix copy ABI")

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

    # --- rig primitive ABI (optional, guarded) ---
    _setup_rig_primitive_signatures(lib)


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
        logger.debug(f"Error while setting parsed-model ABI signatures: {exc}")


def _set_sig(
    lib: CDLL, name: str, restype: Any, argtypes: List[Any]
) -> None:
    """
    シンボル name が lib に存在すれば argtypes/restype を設定する。
    存在しなければ何もしない。
    """
    func = getattr(lib, name, None)
    if func is None:
        logger.debug(f"parsed-model ABI symbol '{name}' does not exist in the DLL")
        return
    func.restype = restype
    func.argtypes = argtypes


def _setup_rig_primitive_signatures(lib: CDLL) -> None:
    try:
        # --- rig spec ---
        _set_sig(lib, "mmd_runtime_pmx_rig_spec_create", c_void_p, [POINTER(c_uint8), c_size_t])
        _set_sig(lib, "mmd_runtime_pmx_rig_spec_free", None, [c_void_p])
        _set_sig(lib, "mmd_runtime_pmx_rig_spec_manifest_json", MmdRuntimeFfiByteBuffer, [c_void_p])

        # --- IK chain ---
        _set_sig(
            lib,
            "mmd_runtime_ik_chain_create",
            c_void_p,
            [
                POINTER(MmdRuntimeFfiRigBone),  # bones
                c_size_t,                       # bone_count
                c_uint32,                       # target_bone_slot
                POINTER(MmdRuntimeFfiRigIkLink),  # links
                c_size_t,                       # link_count
                c_uint32,                       # iteration_count
                c_float,                        # limit_angle
            ],
        )
        _set_sig(lib, "mmd_runtime_ik_chain_free", None, [c_void_p])
        _set_sig(
            lib,
            "mmd_runtime_ik_chain_solve",
            c_bool,
            [
                c_void_p,                         # chain
                POINTER(c_float),                 # parent_world_matrix (nullable)
                POINTER(c_float),                 # local_position_offsets_xyz
                POINTER(c_float),                 # local_rotations_xyzw
                POINTER(c_float),                 # goal_position_xyz
                c_float,                          # tolerance
                c_uint32,                         # max_iterations_cap
                POINTER(c_float),                 # out_link_rotations_xyzw
                c_size_t,                         # out_link_rotation_f32_len
                POINTER(MmdRuntimeFfiIkSolveStats),  # out_stats (nullable)
            ],
        )

        # --- append solver ---
        _set_sig(
            lib,
            "mmd_runtime_append_solver_create",
            c_void_p,
            [POINTER(MmdRuntimeFfiAppendConfig)],
        )
        _set_sig(lib, "mmd_runtime_append_solver_free", None, [c_void_p])
        _set_sig(
            lib,
            "mmd_runtime_append_solver_solve",
            c_bool,
            [
                c_void_p,          # solver
                POINTER(c_float),  # source_position_offset_xyz
                POINTER(c_float),  # source_rotation_xyzw
                POINTER(c_float),  # out_position_offset_xyz
                POINTER(c_float),  # out_rotation_xyzw
            ],
        )
    except Exception as exc:
        logger.debug(f"Error while setting rig primitive ABI signatures: {exc}")


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
        logger.info("mmd-anim runtime library was not found (check prebuilt binary placement)")
        _runtime_lib = False
        return None

    try:
        lib = ctypes.CDLL(str(path))
        _setup_function_signatures(lib)

        abi = lib.mmd_runtime_abi_version()
        if abi != MMD_RUNTIME_ABI_VERSION:
            logger.warning(
                f"mmd-anim runtime ABI version mismatch: got={abi}, expected={MMD_RUNTIME_ABI_VERSION}"
            )
            # 互換性の範囲で続行するか、厳格に拒否するかは将来調整
        else:
            logger.debug(f"Loaded mmd-anim runtime library: {path} (ABI {abi})")

        _runtime_lib = lib
        _runtime_lib_path = path
        return lib

    except Exception as e:
        logger.error(f"Failed to load mmd-anim runtime library: {path} - {e}", exc_info=True)
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
    except Exception:
        pass

    # モデルルートにメッセージで関連付け (将来のドライバ用)
    if cmds.objExists(model_root):
        try:
            if not cmds.attributeQuery("mmdRuntimeNode", node=model_root, exists=True):
                cmds.addAttr(model_root, ln="mmdRuntimeNode", at="message")
            existing_connections = (
                cmds.listConnections(f"{model_root}.mmdRuntimeNode", source=True, destination=False, plugs=True)
                or []
            )
            for source in existing_connections:
                if source == f"{node}.message":
                    break
                try:
                    cmds.disconnectAttr(source, f"{model_root}.mmdRuntimeNode")
                except Exception:
                    pass
            cmds.connectAttr(f"{node}.message", f"{model_root}.mmdRuntimeNode", force=True)
        except Exception:
            pass

    return node


def get_runtime_matrices_from_node(node: str) -> list:
    """ノードから現在の world matrices を取得 (float flat list)"""
    import maya.cmds as cmds
    try:
        return cmds.getAttr(f"{node}.worldMatrices[*]") or []
    except Exception:
        return []


def connect_runtime_node_outputs_to_model(
    node: str,
    model_root: str,
    pmx_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Connect an mmdRuntimeInstance node\'s worldMatrices/morphWeights outputs to existing
    Maya joints (with mmd_bone_index) and blendShape weights via standard DG nodes.

    For each bone with a matching joint:
      1. Build a fourByFourMatrix from the 16 raw runtime float values.
      2. Apply the MMD-to-Maya Z-flip via S * M * S where S = diag(1,1,-1,1)
         using a shared Z-flip fourByFourMatrix and two multMatrix nodes.
      3. Multiply by the DAG parent\'s worldInverseMatrix[0] to get the local matrix.
      4. DecomposeMatrix with the joint\'s rotateOrder, then connect translate/rotate.

    For morphs, when pmx_path resolves:
      - Parse PMX vertex morph names, find matching blendShape aliases,
        and connect morphWeights[pmx_idx] → blendShape.weight[bs_idx].

    Args:
        node: The mmdRuntimeInstance node name.
        model_root: The root transform of the imported MMD model.
        pmx_path: Path to the .pmx file (optional; needed for morph resolution).

    Returns:
        A dict with keys:
          connected_bones: list of (joint_name, bone_index) connected.
          connected_morphs: list of (morph_name, pmx_index, bs_node, weight_idx) connected.
          skipped: list of strings describing why some bones/morphs were skipped.
          warnings: list of strings describing non-blocking issues (jointOrient, rotateAxis).
          utility_nodes: list of created DG node names (for cleanup).
    """
    import maya.cmds as cmds
    from mmd_tools.core.constants import ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, ATTR_MMD_BONE_INDEX

    result: Dict[str, Any] = {
        "connected_bones": [],
        "connected_morphs": [],
        "skipped": [],
        "warnings": [],
        "utility_nodes": [],
    }

    if not cmds.objExists(node):
        result["skipped"].append(f"Runtime node {node!r} does not exist")
        return result
    if not cmds.objExists(model_root):
        result["skipped"].append(f"Model root {model_root!r} does not exist")
        return result

    _ATTR_MAP = [
        (0, "in00"), (1, "in01"), (2, "in02"), (3, "in03"),
        (4, "in10"), (5, "in11"), (6, "in12"), (7, "in13"),
        (8, "in20"), (9, "in21"), (10, "in22"), (11, "in23"),
        (12, "in30"), (13, "in31"), (14, "in32"), (15, "in33"),
    ]

    def _make_zflip_node() -> str:
        """Create a shared fourByFourMatrix representing S = diag(1,1,-1,1)."""
        flip = cmds.createNode("fourByFourMatrix", name=f"{node}_zflip")
        result["utility_nodes"].append(flip)
        # S = diag(1,1,-1,1) in row-major for fourByFourMatrix
        for row in range(4):
            for col in range(4):
                if row == col == 0:
                    val = 1.0
                elif row == col == 1:
                    val = 1.0
                elif row == col == 2:
                    val = -1.0
                elif row == col == 3:
                    val = 1.0
                else:
                    val = 0.0
                attr_name = f"in{row}{col}"
                cmds.setAttr(f"{flip}.{attr_name}", val)
        return flip

    # Collect joints with mmd_bone_index
    joints_by_index: Dict[int, str] = {}
    for joint in cmds.listRelatives(model_root, allDescendents=True, type="joint") or []:
        if not cmds.attributeQuery(ATTR_MMD_BONE_INDEX, node=joint, exists=True):
            continue
        try:
            bi = int(cmds.getAttr(f"{joint}.{ATTR_MMD_BONE_INDEX}"))
        except Exception:
            continue
        if bi in joints_by_index:
            result["warnings"].append(
                f"Duplicate mmd_bone_index={bi}: {joints_by_index[bi]} and {joint}"
            )
        joints_by_index[bi] = joint

    if not joints_by_index:
        result["skipped"].append("No joints with mmd_bone_index found")
        return result

    unsupported_orientation = []
    for bone_idx in sorted(joints_by_index.keys()):
        joint = joints_by_index[bone_idx]
        try:
            jo = cmds.getAttr(f"{joint}.jointOrient")[0]
            if any(abs(v) > 1e-6 for v in jo):
                unsupported_orientation.append(
                    f"{joint} (bone_idx={bone_idx}) has non-zero jointOrient {jo}"
                )
        except Exception:
            pass
        try:
            ra = cmds.getAttr(f"{joint}.rotateAxis")[0]
            if any(abs(v) > 1e-6 for v in ra):
                unsupported_orientation.append(
                    f"{joint} (bone_idx={bone_idx}) has non-zero rotateAxis {ra}"
                )
        except Exception:
            pass
    if unsupported_orientation:
        result["skipped"].append(
            "Live DG connection skipped because jointOrient/rotateAxis is not yet supported: "
            + "; ".join(unsupported_orientation)
        )
        return result

    zflip = _make_zflip_node()

    for bone_idx in sorted(joints_by_index.keys()):
        joint = joints_by_index[bone_idx]

        # --- Step 1: fourByFourMatrix from raw runtime floats ---
        fbf = cmds.createNode("fourByFourMatrix", name=f"{joint}_fbf")
        result["utility_nodes"].append(fbf)

        base_idx = bone_idx * 16
        for offset, attr_name in _ATTR_MAP:
            src = f"{node}.worldMatrices[{base_idx + offset}]"
            dst = f"{fbf}.{attr_name}"
            try:
                cmds.connectAttr(src, dst, force=True)
            except Exception as e:
                result["warnings"].append(
                    f"Failed to connect {src} → {dst}: {e}"
                )

        # --- Step 2: S * M * S (Z-flip) via multMatrix ---
        # multMatrix output = matrix0 * matrix1 * matrix2 * ...
        # We want S * raw * S, so inputs: [zflip, fbf, zflip]
        mm_world = cmds.createNode("multMatrix", name=f"{joint}_mm_world")
        result["utility_nodes"].append(mm_world)
        cmds.connectAttr(f"{zflip}.output", f"{mm_world}.matrixIn[0]", force=True)
        cmds.connectAttr(f"{fbf}.output", f"{mm_world}.matrixIn[1]", force=True)
        cmds.connectAttr(f"{zflip}.output", f"{mm_world}.matrixIn[2]", force=True)

        # --- Step 3: parent-relative (local matrix) ---
        parents = cmds.listRelatives(joint, parent=True, fullPath=True) or []
        if parents:
            parent_node = parents[0]
            mm_local = cmds.createNode("multMatrix", name=f"{joint}_mm_local")
            result["utility_nodes"].append(mm_local)
            # local = world * parentWorldInverse
            cmds.connectAttr(f"{mm_world}.matrixSum", f"{mm_local}.matrixIn[0]", force=True)
            cmds.connectAttr(
                f"{parent_node}.worldInverseMatrix[0]",
                f"{mm_local}.matrixIn[1]",
                force=True,
            )
            matrix_source = f"{mm_local}.matrixSum"
        else:
            # Root joint: world = local
            matrix_source = f"{mm_world}.matrixSum"

        # --- Step 4: decomposeMatrix with correct rotateOrder ---
        dm = cmds.createNode("decomposeMatrix", name=f"{joint}_dm")
        result["utility_nodes"].append(dm)
        cmds.connectAttr(matrix_source, f"{dm}.inputMatrix", force=True)

        # RotateOrder: Maya uses 0=xyz, 1=yzx, 2=zxy, 3=xzy, 4=yxz, 5=zyx
        # MMD bone rotation order is typically ZXY (Maya index 2) matching
        # VMD channel order. Query the joint\'s actual rotateOrder.
        try:
            ro = int(cmds.getAttr(f"{joint}.rotateOrder"))
            cmds.setAttr(f"{dm}.inputRotateOrder", ro)
        except Exception:
            pass

        # --- Step 5: Connect to joint ---
        try:
            cmds.connectAttr(f"{dm}.outputTranslate", f"{joint}.translate", force=True)
            cmds.connectAttr(f"{dm}.outputRotate", f"{joint}.rotate", force=True)
        except Exception as e:
            result["warnings"].append(
                f"Failed to connect {dm} outputs to {joint}: {e}"
            )
            continue

        result["connected_bones"].append((joint, bone_idx))

    # --- Morph connections (only if pmx_path resolves) ---
    if pmx_path:
        try:
            from mmd_tools.core.maya_utils import sanitize_text

            pmx_bytes = Path(pmx_path).read_bytes()
            parsed = MmdParsedModel.from_pmx_bytes(pmx_bytes)
            if parsed is not None and parsed.vertex_morph_count > 0:
                pmx_morph_names = parsed.vertex_morph_names or []
                # Get vertex_morph_spans to map vertex-morph-index to global PMX morph index
                pmx_morph_spans = parsed.vertex_morph_spans or []
                parsed.free()

                # Build a mapping: vertex_morph_index to global_pmx_morph_index
                # Each span entry is (start, count, pmx_morph_index)
                vtx_idx_to_global = {}
                for vmi, span in enumerate(pmx_morph_spans):
                    if len(span) >= 3:
                        vtx_idx_to_global[vmi] = int(span[2])
                    else:
                        vtx_idx_to_global[vmi] = vmi  # fallback

                # Find blendShape nodes affecting mesh shapes under model_root.  A
                # blendShape is a DG node, not a DAG child, so listRelatives() on
                # the blendShape itself does not identify model ownership.
                mesh_shapes = cmds.listRelatives(
                    model_root,
                    allDescendents=True,
                    type="mesh",
                    fullPath=True,
                ) or []
                model_blend_shapes = []
                for shape in mesh_shapes:
                    for history_node in cmds.listHistory(shape, pruneDagObjects=True) or []:
                        if cmds.nodeType(history_node) != "blendShape":
                            continue
                        if history_node not in model_blend_shapes:
                            model_blend_shapes.append(history_node)

                for bs_node in model_blend_shapes:

                    weight_count = cmds.blendShape(bs_node, query=True, weightCount=True) or 0

                    # Authoritative map stored at import time: raw morph name -> weight index.
                    # Preferred over the lossy sanitized-alias match (aliases can collide and
                    # are uniquified with numeric suffixes, so alias == sanitize(name) may fail).
                    stored_raw_to_index = {}
                    if cmds.attributeQuery(
                        ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON, node=bs_node, exists=True
                    ):
                        try:
                            parsed_names = json.loads(
                                cmds.getAttr(f"{bs_node}.{ATTR_MMD_BLENDSHAPE_MORPH_NAMES_JSON}") or "{}"
                            )
                            if isinstance(parsed_names, dict):
                                for stored_index, stored_name in parsed_names.items():
                                    stored_raw_to_index[str(stored_name)] = int(stored_index)
                        except (TypeError, ValueError):
                            stored_raw_to_index = {}

                    for vmi, pmx_name in enumerate(pmx_morph_names):
                        if not pmx_name:
                            continue
                        # Resolve global PMX morph index for correct morphWeights indexing
                        global_idx = vtx_idx_to_global.get(vmi, vmi)

                        # Compute sanitized alias using the same logic as MorphConverter
                        sanitized_alias = sanitize_text(pmx_name)
                        stored_wi = stored_raw_to_index.get(pmx_name)

                        # Find the weight index that matches this PMX morph name
                        for wi in range(weight_count):
                            if stored_wi is not None:
                                # Authoritative: only connect the exact stored index.
                                if wi != stored_wi:
                                    continue
                            else:
                                alias = cmds.aliasAttr(f"{bs_node}.weight[{wi}]", query=True)
                                if not alias:
                                    continue
                                # Fallback: sanitized alias match first, then exact (raw) match
                                if not (alias == sanitized_alias or alias == pmx_name):
                                    continue
                            # Direct connection from morphWeights[global_idx] to weight[wi]
                            try:
                                src = f"{node}.morphWeights[{global_idx}]"
                                dst = f"{bs_node}.weight[{wi}]"
                                existing_sources = (
                                    cmds.listConnections(
                                        dst,
                                        source=True,
                                        destination=False,
                                        plugs=True,
                                    )
                                    or []
                                )
                                if src not in existing_sources:
                                    cmds.connectAttr(src, dst, force=True)
                                result["connected_morphs"].append(
                                    (pmx_name, global_idx, bs_node, wi)
                                )
                            except Exception as e:
                                result["warnings"].append(
                                    f"Failed to connect morphWeights[{global_idx}] → "
                                    f"{bs_node}.weight[{wi}]: {e}"
                                )
                            break
        except Exception as e:
            result["warnings"].append(
                f"Morph resolution skipped (could not read PMX morph names): {e}"
            )
    else:
        result["warnings"].append(
            "pmx_path not provided; morphWeights → blendShape connection skipped. "
            "Pass pmx_path to enable morph resolution."
        )

    return result


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
            except Exception:
                pass
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
            except Exception:
                pass
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
            except Exception:
                pass
            self._handle = None

    def __del__(self) -> None:
        self.free()
