"""
mmd-anim runtime ネイティブ統合のためのパッケージ。

このパッケージは mmd-anim (https://github.com/yohawing/mmd-anim) の
C ABI (mmd-anim-ffi) を Maya Python から安全に呼び出すための
オプショナルなラッパーを提供します。

主な用途:
- VMD インポート時の高精度ベイク (付与変形 + IK + MMD ベジェ補間の忠実再現)
- 将来的なライブ評価ランタイムノード (C++ Maya プラグイン) のバックエンド

利用方法:
    from mmd_tools.core.native.mmd_anim_runtime import (
        is_mmd_runtime_available,
        MmdRuntimeModel,
        MmdRuntimeClip,
        MmdRuntimeInstance,
    )

    if is_mmd_runtime_available():
        model = MmdRuntimeModel.from_pmx_bytes(pmx_bytes)
        clip = MmdRuntimeClip.from_vmd_bytes_for_model(model, vmd_bytes)
        instance = MmdRuntimeInstance.for_model(model)
        instance.evaluate_clip_frame(clip, 42.0)
        matrices = instance.get_world_matrices()
        # ... Maya ジョイントや skinning へ適用

native バイナリ (mmd_runtime_ffi.dll / libmmd_runtime_ffi.dylib) が存在しない場合は
すべての関数が None / False を返し、既存の Python 実装に自動フォールバックします。
"""

from .mmd_anim_runtime import (
    MmdAppendSolver,
    MmdIkChain,
    MmdParsedModel,
    MmdRigSpec,
    MmdRuntimeClip,
    MmdRuntimeInstance,
    MmdRuntimeModel,
    create_runtime_node_for_model,
    get_mmd_runtime_library,
    is_mmd_runtime_available,
    is_native_pmx_parser_available,
    is_native_pmx_parts_export_available,
    is_rig_primitive_available,
    export_pmx_from_parts,
)
from .native_pmx_parser import is_native_parser_available, parse_pmx_native

__all__ = [
    "is_mmd_runtime_available",
    "is_native_pmx_parser_available",
    "is_native_pmx_parts_export_available",
    "is_native_parser_available",
    "is_rig_primitive_available",
    "get_mmd_runtime_library",
    "export_pmx_from_parts",
    "parse_pmx_native",
    "MmdRuntimeModel",
    "MmdRuntimeClip",
    "MmdRuntimeInstance",
    "MmdParsedModel",
    "MmdRigSpec",
    "MmdIkChain",
    "MmdAppendSolver",
    "create_runtime_node_for_model",
]
