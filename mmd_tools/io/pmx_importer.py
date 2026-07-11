"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import json
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
    ATTR_MMD_MORPH_DATA,
)
from ..core.display_frame_metadata import display_frames_to_json
from ..core.namespace_utils import NamespaceUtils

# ロガーを取得
logger = get_logger("mmd_tools.io.pmx_importer")


def _build_material_morph_graph_with_retry(root_group, pipeline, mesh_converter):
    """Retry once only when hardware uniform plugs have not materialized yet."""
    first = build_material_morph_graph(root_group, connect_shader=True)
    prefixes = ("dx11_material_plugs_incomplete:", "glsl_material_plugs_incomplete:")
    first_skipped = list(first.get("skipped") or [])
    if not any(str(item).startswith(prefixes) for item in first_skipped):
        return first
    retry_errors = []
    try:
        from maya import cmds

        cmds.refresh(force=True)
    except Exception as exc:
        retry_errors.append({
            "phase": "refresh",
            "type": type(exc).__name__,
            "message": str(exc),
        })
        logger.debug("Failed delayed hardware-uniform refresh", exc_info=True)
    try:
        pipeline.sync_dx11_uniforms(mesh_converter, refresh_if_dx11=False)
    except Exception as exc:
        retry_errors.append({
            "phase": "uniform_sync",
            "type": type(exc).__name__,
            "message": str(exc),
        })
        logger.debug("Failed delayed hardware-uniform sync", exc_info=True)
    final = build_material_morph_graph(root_group, connect_shader=True)
    raw_final_counts = {
        key: int(final.get(key) or 0)
        for key in ("created", "reused", "contributions")
    }
    first_nodes = list(first.get("evaluator_nodes") or [])
    final_nodes = list(final.get("evaluator_nodes") or [])
    final["evaluator_nodes"] = list(dict.fromkeys(first_nodes + final_nodes))
    final["created"] = int(first.get("created") or 0) + int(final.get("created") or 0)
    # The final pass observes first-pass creations as reused; keep its count
    # authoritative instead of adding the same evaluators twice.
    final["reused"] = int(final.get("reused") or 0)
    final["retry"] = {
        "attempted": True,
        "first_skipped": first_skipped,
        "final_skipped": list(final.get("skipped") or []),
        "errors": retry_errors,
        "first_counts": {
            key: int(first.get(key) or 0)
            for key in ("created", "reused", "contributions")
        },
        "final_counts": raw_final_counts,
    }
    if any(str(item).startswith(prefixes) for item in final.get("skipped") or []):
        _schedule_deferred_material_morph_retry(
            root_group,
            pipeline,
            mesh_converter,
            final,
        )
    return final


def _schedule_deferred_material_morph_retry(root_group, pipeline, mesh_converter, result):
    """Retry once at Maya idle after hardware uniforms finish materializing."""
    retry = result.setdefault("retry", {})
    retry["deferred_scheduled"] = False
    try:
        from maya import cmds

        root_uuids = cmds.ls(root_group, uuid=True) or []
        root_uuid = str(root_uuids[0]) if len(root_uuids) == 1 else ""
    except Exception:
        root_uuid = ""
    if not root_uuid:
        retry.setdefault("errors", []).append({
            "phase": "deferred_schedule",
            "type": "RootIdentityUnavailable",
            "message": f"Could not capture UUID for {root_group}",
        })
        return

    def rebuild_after_idle():
        deferred_errors = []
        try:
            from maya import cmds

            current_uuids = cmds.ls(root_group, uuid=True) or []
            if len(current_uuids) != 1 or str(current_uuids[0]) != root_uuid:
                retry["deferred"] = {
                    "skipped": ["root_group_identity_changed"],
                    "errors": [],
                }
                return
            try:
                cmds.refresh(force=True)
            except Exception as exc:
                deferred_errors.append({
                    "phase": "refresh",
                    "type": type(exc).__name__,
                    "message": str(exc),
                })
                logger.debug("Failed deferred hardware-uniform refresh", exc_info=True)
            try:
                pipeline.sync_dx11_uniforms(mesh_converter, refresh_if_dx11=False)
            except Exception as exc:
                deferred_errors.append({
                    "phase": "uniform_sync",
                    "type": type(exc).__name__,
                    "message": str(exc),
                })
                logger.debug("Failed deferred hardware-uniform sync", exc_info=True)
            rebuilt = build_material_morph_graph(root_group, connect_shader=True)
            retry["deferred"] = {
                "skipped": list(rebuilt.get("skipped") or []),
                "errors": deferred_errors,
            }
        except Exception as exc:
            retry["deferred"] = {
                "skipped": [],
                "errors": [{
                    "phase": "graph_build",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }],
            }
            logger.debug("Failed deferred material-morph graph rebuild", exc_info=True)

    try:
        from maya import cmds

        cmds.evalDeferred(rebuild_after_idle, lowestPriority=True)
        retry["deferred_scheduled"] = True
    except Exception as exc:
        retry.setdefault("errors", []).append({
            "phase": "deferred_schedule",
            "type": type(exc).__name__,
            "message": str(exc),
        })
        logger.debug("Failed to schedule deferred material-morph graph rebuild", exc_info=True)


def _serialize_pmx_morph_data(morphs: Any) -> str:
    """Serialize authoritative PMX morph metadata for the model root.

    MorphPresenter uses the raw Japanese morph name as its scene key.  Keep the
    PMX type value intact so later UI features can distinguish UV and PMX 2.1
    morphs without reconstructing it from Maya nodes.
    """
    metadata = []
    for index, morph in enumerate(morphs or []):
        name_jp = str(getattr(morph, "name", "") or "")
        morph_type = getattr(morph, "morph_type", 0)
        metadata.append(
            {
                "name_jp": name_jp,
                "name_en": str(getattr(morph, "name_english", "") or ""),
                "panel": int(getattr(morph, "panel", 0)),
                "type": int(morph_type),
                "index": index,
            }
        )
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))


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
                    ATTR_MMD_MORPH_DATA: _serialize_pmx_morph_data(
                        getattr(parser, "morphs", [])
                    ),
                    # Phase 1: runtime bake で VMD インポート時に PMX ソースを容易に見つけるため
                    "mmd_source_file": filepath,
                },
            )

            # メッシュを変換
            logger.debug("Converting mesh...")
            mesh_converter = MeshConverter(filepath, scale=scale)
            phase_start = time.perf_counter()
            mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser, root_group)
            pipeline.connect_texture_nodes_to_root(
                root_group,
                mesh_converter.created_texture_file_nodes,
            )
            pipeline.record_phase("mesh_conversion_sec", phase_start)
            pipeline.emit_progress(35)

            # mesh_name が list かどうかで分岐
            mesh_names = mesh_name if isinstance(mesh_name, list) else [mesh_name]
            logger.debug("Mesh conversion complete: group=%s, name=%s", mesh_group, mesh_name)

            logger.debug("Converting morphs...")
            morph_converter = MorphConverter(scale=scale)
            phase_start = time.perf_counter()
            morph_result = morph_converter.convert_pmx_morphs(parser, mesh_name)
            pipeline.record_phase("morph_conversion_sec", phase_start)
            pipeline.emit_progress(50)
            logger.debug("Morph conversion complete")

            # network morph ノードをモデルルートに message 接続で紐付ける
            pipeline.connect_morph_nodes_to_root(root_group, morph_result)

            # ボーンを変換
            logger.debug("Converting bones...")
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

            logger.debug("Building bone morph runtime graph...")
            phase_start = time.perf_counter()
            bone_morph_runtime_result = build_bone_morph_graph(root_group)
            pipeline.record_phase("bone_morph_runtime_sec", phase_start)
            logger.debug("Bone morph runtime graph result: %s", bone_morph_runtime_result)
            for warning in bone_morph_runtime_result.get("warnings") or []:
                logger.warning(
                    "Bone morph runtime warning: code=%s detail=%s",
                    warning.get("code") or warning.get("reason"),
                    warning.get("detail"),
                )

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
                # Generated dx11 uniforms (DiffuseColorRGB etc.) only materialize
                # after VP2 refresh; material morph routing must run after this.
                pipeline.sync_dx11_uniforms(mesh_converter, refresh_if_dx11=True)
            except Exception:
                logger.debug("Failed to synchronize dx11 generated uniforms", exc_info=True)

            # Material morph colour route needs post-sync plugs; do not re-sync.
            logger.debug("Building material morph runtime graph...")
            phase_start = time.perf_counter()
            material_morph_runtime_result = _build_material_morph_graph_with_retry(
                root_group, pipeline, mesh_converter
            )
            pipeline.record_phase("material_morph_runtime_sec", phase_start)
            pipeline.emit_progress(90)
            logger.debug("Material morph runtime graph result: %s", material_morph_runtime_result)

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
                }
                pipeline.profile["bone_morph_runtime"] = bone_morph_runtime_result
                pipeline.profile["material_morph_runtime"] = material_morph_runtime_result
                logger.debug("PMX import phase timings: %s", pipeline.profile["phase_timings"])
                logger.debug("Mesh converter profile: %s", pipeline.profile["mesh_converter"])
                logger.debug("Morph converter profile: %s", pipeline.profile["morph_converter"])
                logger.debug("Bone converter profile: %s", pipeline.profile["bone_converter"])
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
