"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os
import time
from typing import Any, Callable, Dict, Optional

from mmd_tools.core import maya_name_utils
from mmd_tools.core.exceptions import MMDImportException

from ..converters import BoneConverter, MeshConverter, MorphConverter
from ..converters.bone_morph_runtime import build_bone_morph_graph
from ..converters.material_morph_runtime import build_material_morph_graph
from ..core.logger import get_logger
from .model_import_pipeline import ModelImportPipeline
from ..core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
)
from ..core.display_frame_metadata import display_frames_to_json
from ..core.namespace_utils import NamespaceUtils

# ロガーを取得
logger = get_logger("mmd_tools.io.pmx_importer")


def import_pmx_file(
    parser: Any,
    filepath: str,
    scale: float = 1.0,
    options: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Optional[str]:
    """
    PMXファイルをMayaシーンにインポートします。

    Args:
        parser (PmxParser): PMXファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMXファイルのパス
        scale (float): スケール値（互換性のため）
        options (dict): インポートオプション
        progress_callback (Callable[[int], None]): フェーズ進捗通知コールバック。

    Returns:
        str: 作成したモデルルートノード名。

    Raises:
        MMDImportException: PMX/PMD 変換済みデータのインポートに失敗した場合。
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

    logger.info("Starting PMX file import: %s", filepath)

    logger.debug("Scale factor: %f", scale)

    model_name = maya_name_utils.sanitize_text(parser.header.get_name())
    namespace = pipeline.resolve_namespace(model_name, custom_namespace=options.get("custom_namespace"))

    try:
        # namespace context内でモデルを構築
        with NamespaceUtils.namespace_context(namespace):
            pipeline.emit_progress(15)
            # ルートグループを作成
            root_group = pipeline.create_root_group(
                model_name,
                {
                    ATTR_MMD_MODEL_NAME: parser.header.model_name,
                    ATTR_MMD_MODEL_NAME_EN: parser.header.model_name_english,
                    ATTR_MMD_COMMENT: parser.header.comment,
                    ATTR_MMD_COMMENT_EN: parser.header.comment_english,
                    ATTR_MMD_DISPLAY_FRAMES_JSON: display_frames_to_json(
                        getattr(parser, "display_frames", [])
                    ),
                    # Phase 1: runtime bake で VMD インポート時に PMX ソースを容易に見つけるため
                    "mmd_source_file": filepath,
                },
            )

            # メッシュを変換
            logger.info("Converting mesh...")
            mesh_converter = MeshConverter(filepath, scale=scale)
            phase_start = time.perf_counter()
            mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser, root_group)
            pipeline.record_phase("mesh_conversion_sec", phase_start)
            pipeline.emit_progress(35)

            # mesh_name が list かどうかで分岐
            mesh_names = mesh_name if isinstance(mesh_name, list) else [mesh_name]
            logger.debug("Mesh conversion complete: group=%s, name=%s", mesh_group, mesh_name)

            logger.info("Converting morphs...")
            morph_converter = MorphConverter(scale=scale)
            phase_start = time.perf_counter()
            morph_result = morph_converter.convert_pmx_morphs(parser, mesh_name)
            pipeline.record_phase("morph_conversion_sec", phase_start)
            pipeline.emit_progress(50)
            logger.debug("Morph conversion complete")

            # network morph ノードをモデルルートに message 接続で紐付ける
            pipeline.connect_morph_nodes_to_root(root_group, morph_result)

            # ボーンを変換
            logger.info("Converting bones...")
            bone_converter = BoneConverter()
            phase_start = time.perf_counter()
            maya_joints, skin_cluster = bone_converter.convert_pmx_bones(
                parser,
                mesh_name,
                root_group,
                setup_rig=options.get("setup_rig", True),
                setup_bone_orientation=options.get("setup_bone_orientation", True),
                pmx_filepath=filepath,
                scale=scale,
            )
            pipeline.record_phase("bone_and_skin_conversion_sec", phase_start)
            pipeline.emit_progress(70)
            logger.debug(
                "Bone conversion complete: %d joints, %d meshes",
                len(maya_joints) if maya_joints else 0,
                len(mesh_names),
            )

            logger.info("Building bone morph runtime graph...")
            phase_start = time.perf_counter()
            bone_morph_runtime_result = build_bone_morph_graph(root_group)
            pipeline.record_phase("bone_morph_runtime_sec", phase_start)
            logger.debug("Bone morph runtime graph result: %s", bone_morph_runtime_result)

            logger.info("Building material morph runtime graph...")
            phase_start = time.perf_counter()
            material_morph_runtime_result = build_material_morph_graph(root_group)
            pipeline.record_phase("material_morph_runtime_sec", phase_start)
            pipeline.emit_progress(78)
            logger.debug("Material morph runtime graph result: %s", material_morph_runtime_result)

            pipeline.convert_physics(
                file_kind="pmx",
                parser=parser,
                maya_joints=maya_joints,
                root_group=root_group,
            )

            # MMD ライトコントローラ（操作可能なヌル）を作成（get-or-create）。
            # シェーダーへの結線は dx11 uniform 生成（refresh）後に行うため、
            # ここでは transform 名だけ控えておく。
            light_ctrl = pipeline.create_light_controller()

            # PMX座標は mesh / bone / morph 生成時点で scale 済み。
            # bind 後の root scale freeze は skinCluster.bindPreMatrix を stale にするため避ける。
            pipeline.apply_scale_and_select(root_group, apply_scale=False)
            try:
                pipeline.sync_dx11_uniforms(mesh_converter, refresh_if_dx11=True)
            except Exception:
                logger.debug("Failed to synchronize dx11 generated uniforms", exc_info=True)

            # MMD ライトコントローラを各 dx11Shader に結線（uniform 生成後）。
            pipeline.wire_light_controller(mesh_converter, light_ctrl)

            # Color Management を MMD 向けに整える（CM の enable は触らない）。
            pipeline.setup_view()
            if pipeline.profile is not None:
                pipeline.profile["phase_timings"] = pipeline.phase_timings
                pipeline.profile["mesh_converter"] = dict(mesh_converter.profile)
                pipeline.profile["morph_converter"] = dict(morph_converter.profile)
                pipeline.profile["bone_converter"] = dict(bone_converter.profile)
                pipeline.profile["texture_issues"] = list(mesh_converter.unresolved_textures)
                pipeline.profile["morph_result"] = {
                    "morphs_converted": morph_result.get("morphs_converted"),
                    "total_morphs": morph_result.get("total_morphs"),
                    "blend_shape_nodes": len(morph_result.get("blend_shape_nodes", []) or []),
                    "bone_morph_nodes": len(morph_result.get("bone_morph_nodes", []) or []),
                    "material_morph_nodes": len(morph_result.get("material_morph_nodes", []) or []),
                    "vertex_morphs_skipped_by_material": morph_result.get(
                        "vertex_morphs_skipped_by_material",
                        0,
                    ),
                    "vertex_morphs_skipped_by_group": morph_result.get(
                        "vertex_morphs_skipped_by_group",
                        0,
                    ),
                }
                pipeline.profile["bone_morph_runtime"] = bone_morph_runtime_result
                logger.info("PMX import phase timings: %s", pipeline.profile["phase_timings"])
                logger.info("Mesh converter profile: %s", pipeline.profile["mesh_converter"])
                logger.info("Morph converter profile: %s", pipeline.profile["morph_converter"])
                logger.info("Bone converter profile: %s", pipeline.profile["bone_converter"])
            if mesh_converter.unresolved_texture_count:
                logger.warning(
                    "%d texture(s) could not be loaded. Use Resolve textures to repair them.",
                    mesh_converter.unresolved_texture_count,
                )
        logger.info("PMX file import completed: %s", os.path.basename(filepath))
        return root_group  # ルートノードの名前を返す

    except Exception as e:
        logger.error("Failed to import PMX file: %s - %s", filepath, str(e), exc_info=True)
        # エラー時のnamespaceクリーンアップ
        pipeline.cleanup_namespace(namespace)
        raise MMDImportException(f"Failed to import PMX file {filepath}: {e}") from e
