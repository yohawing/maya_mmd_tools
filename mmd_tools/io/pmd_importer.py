"""
PMDファイルをMayaシーンにインポートするためのモジュール。
"""

import time
from typing import Any, Callable, Dict, Optional

from mmd_tools.core import maya_utils

from ..converters import BoneConverter, MeshConverter, MorphConverter
from ..core.logger import get_logger
from .model_import_pipeline import ModelImportPipeline
from ..core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)
from ..core.namespace_utils import NamespaceUtils

# ロガーを取得
logger = get_logger("mmd_tools.io.pmd_importer")


def import_pmd_file(
    parser: Any,
    filepath: str,
    scale: float = 1.0,
    options: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Optional[str]:
    """
    PMDファイルをMayaシーンにインポートします。

    Args:
        parser (PmdParser): PMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMDファイルのパス
        scale (float): スケール値（互換性のため）
        options (dict): インポートオプション
        progress_callback (Callable[[int], None]): フェーズ進捗通知コールバック。

    Returns:
        bool: インポートが成功したかどうか
    """
    if options is None:
        options = {}
    pipeline = ModelImportPipeline(
        logger=logger,
        filepath=filepath,
        scale=scale,
        options=options,
        progress_callback=progress_callback,
    )

    logger.info("Starting PMD file import: %s", filepath)

    logger.debug("Scale factor: %f", scale)

    model_name = maya_utils.sanitize_text(parser.header.get_name())
    namespace = pipeline.resolve_namespace(model_name)

    try:
        # namespace context内でモデルを構築
        with NamespaceUtils.namespace_context(namespace):
            pipeline.emit_progress(15)
            # ルートグループを作成
            root_group = pipeline.create_root_group(
                model_name,
                {
                    ATTR_MMD_MODEL_NAME: parser.header.get_name(),
                    ATTR_MMD_MODEL_NAME_EN: "",
                    ATTR_MMD_COMMENT: parser.header.get_comment(),
                    ATTR_MMD_COMMENT_EN: "",
                    # Phase 1: store source for later VMD runtime bake
                    "mmd_source_file": filepath,
                },
            )

            # メッシュを変換
            logger.info("Converting mesh...")
            mesh_converter = MeshConverter(filepath)
            phase_start = time.perf_counter()
            mesh_group, mesh_name = mesh_converter.convert_pmd_mesh(parser, root_group)
            pipeline.record_phase("mesh_conversion_sec", phase_start)
            pipeline.emit_progress(35)

            mesh_names = mesh_name if isinstance(mesh_name, list) else [mesh_name]
            logger.debug("Mesh conversion complete: group=%s, name=%s", mesh_group, mesh_name)

            # モーフを変換
            logger.info("Converting morphs...")
            morph_converter = MorphConverter()
            phase_start = time.perf_counter()
            morph_result = morph_converter.convert_pmd_morphs(parser, mesh_name)
            pipeline.record_phase("morph_conversion_sec", phase_start)
            pipeline.emit_progress(50)
            logger.debug("Morph conversion complete: %s", mesh_name)

            # ボーンを変換
            logger.info("Converting bones...")
            bone_converter = BoneConverter()
            phase_start = time.perf_counter()
            maya_joints, skin_cluster = bone_converter.convert_pmd_bones(parser, mesh_name, root_group)
            pipeline.record_phase("bone_and_skin_conversion_sec", phase_start)
            pipeline.emit_progress(70)
            logger.debug(
                "Bone conversion complete: %d joints, %d meshes",
                len(maya_joints) if maya_joints else 0,
                len(mesh_names),
            )

            pipeline.convert_physics(
                file_kind="pmd",
                parser=parser,
                maya_joints=maya_joints,
                root_group=root_group,
            )

            # MMD ライトコントローラ（操作可能なヌル）を作成（get-or-create）。
            # 結線は dx11 uniform 生成（refresh）後に行うため名前だけ控える。
            light_ctrl = pipeline.create_light_controller()

            # スケールを適用
            pipeline.apply_scale_and_select(root_group)

            # dx11 uniform を生成・同期してから MMD ライトを各シェーダーへ結線。
            if light_ctrl:
                try:
                    pipeline.sync_dx11_uniforms(mesh_converter, refresh_if_dx11=True)
                    pipeline.wire_light_controller(mesh_converter, light_ctrl)
                except Exception:
                    logger.debug("Failed to wire MMD light", exc_info=True)

            # Color Management を MMD 向けに整える（CM の enable は触らない）。
            pipeline.setup_view()
            if pipeline.profile is not None:
                pipeline.profile["phase_timings"] = pipeline.phase_timings
                pipeline.profile["mesh_converter"] = dict(mesh_converter.profile)
                pipeline.profile["texture_issues"] = list(mesh_converter.unresolved_textures)
                pipeline.profile["morph_result"] = {
                    "morphs_converted": morph_result.get("morphs_converted"),
                    "total_morphs": morph_result.get("total_morphs"),
                    "blend_shape_nodes": len(morph_result.get("blend_shape_nodes", []) or []),
                }
            if mesh_converter.unresolved_texture_count:
                logger.warning(
                    "%d texture(s) could not be loaded. Use Resolve textures to repair them.",
                    mesh_converter.unresolved_texture_count,
                )
        logger.info("PMD file import succeeded: %s", filepath)
        return root_group  # ルートノードの名前を返す

    except Exception:
        logger.error("Failed to import PMD file: %s", filepath)
        import traceback

        logger.error("Error details: %s", traceback.format_exc())

        # エラー時のnamespaceクリーンアップ
        pipeline.cleanup_namespace(namespace)

        return None
