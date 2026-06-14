"""
PMXファイルをMayaシーンにインポートするためのモジュール。
"""

import os
import time

from maya import cmds
from mmd_tools.core import maya_utils

from .. import settings
from ..converters import BoneConverter, MeshConverter, MorphConverter, PhysicsConverter
from ..converters.mesh_converter import sync_dx11_generated_uniforms
from ..core.logger import get_logger
from ..core.utils import create_bone_joint_mapping
from ..core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    SCENE_ROOT_SUFFIX,
)
from ..core.namespace_utils import NamespaceUtils

# ロガーを取得
logger = get_logger("mmd_tools.io.pmx_importer")


def import_pmx_file(parser, filepath, scale=1.0, options=None):
    """
    PMXファイルをMayaシーンにインポートします。

    Args:
        parser (PmxParser): PMXファイルを解析したパーサーオブジェクト
        filepath (str): インポートするPMXファイルのパス
        scale (float): スケール値（互換性のため）
        options (dict): インポートオプション

    Returns:
        bool: インポートが成功したかどうか
    """
    if options is None:
        options = {}
    profile = options.get("profile") if isinstance(options.get("profile"), dict) else None
    phase_timings = {}

    def _record_phase(name: str, start: float) -> None:
        if profile is not None:
            phase_timings[name] = round(time.perf_counter() - start, 6)

    logger.info("PMXファイルのインポートを開始: %s", filepath)

    logger.debug("スケールファクター: %f", scale)

    # Namespace処理
    use_namespace = options.get("use_namespace", False)
    custom_namespace = options.get("custom_namespace")
    namespace = None

    if use_namespace:
        if custom_namespace:
            # カスタムnamespaceを使用
            namespace = NamespaceUtils.ensure_unique_namespace(custom_namespace)
            logger.info(f"Using custom namespace: {namespace}")
        else:
            # モデル名からnamespace生成
            model_name = maya_utils.sanitize_text(parser.header.get_name())
            base_ns = NamespaceUtils.generate_namespace(model_name)
            namespace = NamespaceUtils.ensure_unique_namespace(base_ns)
            logger.info(f"Using auto-generated namespace: {namespace}")
        model_name = maya_utils.sanitize_text(parser.header.get_name())
    else:
        model_name = maya_utils.sanitize_text(parser.header.get_name())

    try:
        # namespace context内でモデルを構築
        with NamespaceUtils.namespace_context(namespace):
            # ルートグループを作成
            root_group = cmds.group(empty=True, name=f"{model_name}{SCENE_ROOT_SUFFIX}")
            logger.debug("ルートグループ作成: %s", root_group)

            # Add attributes to root node
            maya_utils.set_custom_attributes(
                root_group,
                {
                    ATTR_MMD_MODEL_NAME: parser.header.model_name,
                    ATTR_MMD_MODEL_NAME_EN: parser.header.model_name_english,
                    ATTR_MMD_COMMENT: parser.header.comment,
                    ATTR_MMD_COMMENT_EN: parser.header.comment_english,
                    # Phase 1: runtime bake で VMD インポート時に PMX ソースを容易に見つけるため
                    "mmd_source_file": filepath,
                },
            )

            # メッシュを変換
            logger.info("メッシュを変換中...")
            mesh_converter = MeshConverter(filepath)
            phase_start = time.perf_counter()
            mesh_group, mesh_name = mesh_converter.convert_pmx_mesh(parser, root_group)
            _record_phase("mesh_conversion_sec", phase_start)

            # mesh_name が list かどうかで分岐
            mesh_names = mesh_name if isinstance(mesh_name, list) else [mesh_name]
            logger.debug("メッシュ変換完了: グループ=%s, 名前=%s", mesh_group, mesh_name)

            logger.info("モーフを変換中...")
            morph_converter = MorphConverter()
            phase_start = time.perf_counter()
            morph_result = morph_converter.convert_pmx_morphs(parser, mesh_name)
            _record_phase("morph_conversion_sec", phase_start)
            logger.debug("モーフ変換完了")

            # ボーンを変換
            logger.info("ボーンを変換中...")
            bone_converter = BoneConverter()
            phase_start = time.perf_counter()
            maya_joints, skin_cluster = bone_converter.convert_pmx_bones(
                parser,
                mesh_name,
                root_group,
                setup_rig=options.get("setup_rig", True),
                setup_bone_orientation=options.get("setup_bone_orientation", True),
            )
            _record_phase("bone_and_skin_conversion_sec", phase_start)
            logger.debug(
                "ボーン変換完了: %d個のジョイント, %d個のメッシュ",
                len(maya_joints) if maya_joints else 0,
                len(mesh_names),
            )

            # 呼び出しオプションを優先し、未指定時はグローバル設定に従う。
            import_physics = options.get(
                "import_physics",
                settings.get("import.physics.import_physics", True),
            )
            if import_physics:
                logger.info("物理を変換中...")
                physics_converter = PhysicsConverter()

                # ボーン名とMayaジョイント名のマッピングを作成
                bone_joint_mapping = create_bone_joint_mapping(parser.bones, maya_joints, "pmx")

                # 物理データが存在する場合のみ変換
                if hasattr(parser, "rigid_bodies") and parser.rigid_bodies:
                    phase_start = time.perf_counter()
                    ncloth_nodes, constraint_nodes = physics_converter.convert_pmx_physics(
                        parser, bone_joint_mapping, root_group
                    )
                    _record_phase("physics_conversion_sec", phase_start)
                    logger.debug(
                        "物理変換完了: nCloth=%d, Constraints=%d",
                        len(ncloth_nodes),
                        len(constraint_nodes),
                    )
                else:
                    logger.debug("物理データが存在しません")

            # スケールを適用
            if root_group and scale != 1.0:
                logger.info("スケールを適用中: %f", scale)
                cmds.setAttr(root_group + ".scaleX", scale)
                cmds.setAttr(root_group + ".scaleY", scale)
                cmds.setAttr(root_group + ".scaleZ", scale)
                cmds.makeIdentity(root_group, apply=True, scale=True)

            cmds.select(root_group)
            try:
                try:
                    # dx11Shader generates effect attrs such as DiffuseColorRGB
                    # only after VP2 evaluates the .fx file.  Force that once
                    # before copying MMD custom attrs into generated uniforms.
                    phase_start = time.perf_counter()
                    cmds.refresh(force=True)
                    _record_phase("refresh_sec", phase_start)
                except Exception:
                    pass
                phase_start = time.perf_counter()
                synced_dx11 = sync_dx11_generated_uniforms(mesh_converter.created_shaders)
                _record_phase("dx11_uniform_sync_sec", phase_start)
                if synced_dx11:
                    logger.debug("dx11Shader generated uniforms synchronized: %d", synced_dx11)
            except Exception:
                logger.debug("Failed to synchronize dx11 generated uniforms", exc_info=True)
            if profile is not None:
                profile["phase_timings"] = phase_timings
                profile["mesh_converter"] = dict(mesh_converter.profile)
                profile["morph_result"] = {
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
        logger.info("PMXファイルのインポートが完了しました: %s", os.path.basename(filepath))
        return root_group  # ルートノードの名前を返す

    except Exception as e:
        logger.error("PMXファイルのインポートに失敗しました: %s - %s", filepath, str(e))
        import traceback

        logger.debug("エラーの詳細:\n%s", traceback.format_exc())

        # エラー時のnamespaceクリーンアップ
        if namespace:
            logger.info(f"Cleaning up namespace: {namespace}")
            NamespaceUtils.cleanup_namespace(namespace, force=True)

        return None
