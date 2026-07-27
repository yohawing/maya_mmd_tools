"""
VMDファイル（モーションデータ）をMayaシーンにインポートするためのモジュール。
"""

import os
from typing import Any, Callable, Dict, Optional

from maya import cmds
from ..converters import vmd_profile
from ..converters.vmd_converter import VmdConverter
from ..core.exceptions import MMDImportException
from ..core.logger import get_logger
from ..core.namespace_utils import NamespaceUtils
from ..core.native.mmd_anim_runtime import is_mmd_runtime_available


def _try_recover_physics_drivers(target_model, logger, profile):
    """Attempt to reconnect orphaned physics drivers after VMD import.

    Only runs in development mode since the live physics graph is not
    public-ready.  Fail-soft: never raises.
    """
    if not target_model or not cmds.objExists(target_model):
        return
    try:
        from ..services.settings_service import SettingsService

        if not SettingsService().is_development_mode():
            return
    except Exception:
        return
    try:
        from ..converters.physics_scene_builder import recover_physics_driver_connections

        result = recover_physics_driver_connections(target_model, logger=logger)
        if result.get("recovered", 0) > 0:
            logger.info(
                "event=physics_driver_recovery recovered=%d skipped=%d",
                result["recovered"],
                result.get("skipped", 0),
            )
            profile["physics_driver_recovery"] = result
    except Exception as exc:
        logger.debug("Physics driver recovery failed: %s", exc, exc_info=True)


def import_vmd_file(
    parser: Any,
    filepath: str,
    options: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> bool:
    """
    VMDファイルをMayaシーンにインポートします。

    Bake mode は mmd-anim final-pose bake、Rig mode は sparse key + live rig として読み込みます。

    Args:
        parser (VmdParser または VmdData): VMDファイルを解析したオブジェクト
        filepath (str): インポートするVMDファイルのパス
        options (dict): インポートオプション
            - target_model: 対象モデル
            - scene_animation_only: Camera Motion（camera/lightのみ）をモデル処理なしで読み込む
            - pmx_path: 対応する PMX ファイルのパス
            - pmx_bytes: 生 PMX バイト
            - bake_mode: True の場合はリグ経由ではなく runtime bake を優先
            - create_mmd_control_rig: True の場合は MMD Control Rig を作成/再利用し、直接キーを作成
            - use_native_physics_bake: True かつ bake_mode のとき native physics bake を試行する（default False）
            - reduce_bake_keys: True かつ bake_mode のとき runtime pose reduction を試行する（default False）
            - reduce_translate_tolerance / reduce_rotate_tolerance / reduce_morph_tolerance: reduction tolerances
            - vmd_fps: VMDインポート時のMayaシーンFPS (30 or 60, default 30)。VMDフレーム番号はリスケールせず、シーンのタイムユニットのみ変更。
        progress_callback (Callable[[int], None]): フェーズ進捗通知コールバック。

    Returns:
        bool: インポートが成功したかどうか

    Raises:
        MMDImportException: VMD インポート処理に失敗した場合。
    """
    if options is None:
        options = {}

    def _emit_progress(value: int) -> None:
        if progress_callback is not None:
            try:
                progress_callback(value)
            except Exception:
                logger.debug("Progress callback failed", exc_info=True)

    logger = get_logger("vmd_importer")
    logger.info(f"Starting VMD file import: {filepath}")

    try:
        _emit_progress(15)
        # Camera Motion はモデル解決を一切行わない独立経路。
        scene_animation_only = bool(options.get("scene_animation_only", False))
        target_namespace = None
        target_model = None if scene_animation_only else options.get("target_model")

        if scene_animation_only:
            if "target_model" in options:
                raise MMDImportException("Camera Motion must not specify target_model")
        elif target_model:
            target_namespace = NamespaceUtils.get_namespace_from_node(target_model)
            if target_namespace:
                logger.debug(f"Target namespace: {target_namespace}")
        else:
            raise MMDImportException("VMD model motion requires an explicit target model")

        # Bake mode needs raw VMD bytes for mmd-anim final-pose evaluation.
        # Rig mode still receives these bytes, but VmdConverter rejects runtime
        # bake when live mmdCcdIk/mmdAppend rig connections are present.
        vmd_bytes = None
        try:
            with vmd_profile.scope("vmd_raw_bytes_read"):
                with open(filepath, "rb") as f:
                    vmd_bytes = f.read()
            vmd_profile.set_extra("vmd_bytes", len(vmd_bytes))
            _emit_progress(25)
        except Exception as e:
            logger.warning(f"Failed to read raw VMD bytes: {e}")

        # PMX ソースの解決（明示指定 > モデルに保存されたソース > ディレクトリ推定）
        pmx_bytes = options.get("pmx_bytes")
        pmx_path = options.get("pmx_path")

        if not pmx_bytes and not pmx_path and target_model:
            # モデルインポート時に保存した "mmd_source_file" を優先取得
            try:
                if cmds.objExists(f"{target_model}.mmd_source_file"):
                    stored = cmds.getAttr(f"{target_model}.mmd_source_file")
                    if stored and os.path.exists(stored):
                        pmx_path = stored
                        logger.debug(f"Restored PMX source from model: {pmx_path}")
            except Exception:
                logger.debug("Failed to restore PMX source from target model", exc_info=True)

        if not scene_animation_only and not pmx_bytes and not pmx_path:
            # 同じディレクトリに .pmx/.pmd があるか簡易推定
            try:
                vmd_dir = os.path.dirname(os.path.abspath(filepath))
                candidates = [f for f in os.listdir(vmd_dir) if f.lower().endswith((".pmx", ".pmd"))] if os.path.isdir(vmd_dir) else []
                if candidates:
                    pmx_path = os.path.join(vmd_dir, candidates[0])
                    logger.info(f"Auto-detected PMX source: {pmx_path} (explicit path recommended)")
            except Exception:
                logger.debug("Failed to auto-detect PMX source next to VMD", exc_info=True)
        _emit_progress(35)

        # VMDコンバーターを使用してアニメーションを変換
        converter = VmdConverter()
        # Apply VMD import FPS setting (sets Maya scene time unit; VMD frame numbers are not rescaled)
        vmd_fps = options.get("vmd_fps", 30)
        if vmd_fps not in (30, 60):
            try:
                v = int(vmd_fps)
                if v not in (30, 60):
                    raise ValueError(v)
                vmd_fps = v
            except (TypeError, ValueError):
                logger.warning(f"Invalid vmd_fps={vmd_fps} (only 30 or 60 allowed), falling back to 30")
                vmd_fps = 30
        converter.fps = float(vmd_fps)
        converter.motion_scale = float(options.get("motion_scale", 1.0))
        converter.import_camera_animation = bool(options.get("import_camera_animation", True))
        converter.import_light_animation = bool(options.get("import_light_animation", True))
        profile = options.get("profile")
        if not isinstance(profile, dict):
            profile = {}
            options["profile"] = profile
        use_native_physics_bake = bool(options.get("use_native_physics_bake", False))
        reduce_bake_keys = bool(options.get("reduce_bake_keys", False))
        def _reduction_tolerance(name, default):
            try:
                value = float(options.get(name, default))
                return value if value >= 0.0 else default
            except (TypeError, ValueError, OverflowError):
                return default

        reduce_translate_tolerance = _reduction_tolerance("reduce_translate_tolerance", 5.0e-4)
        reduce_rotate_tolerance = _reduction_tolerance("reduce_rotate_tolerance", 1.0e-4)
        reduce_morph_tolerance = _reduction_tolerance("reduce_morph_tolerance", 1.0e-3)
        model_target_kwargs = {} if scene_animation_only else {"target_model": target_model}
        try:
            with vmd_profile.scope("vmd_converter_convert"):
                success = converter.convert(
                    parser,
                    target_namespace,
                    bake_mode=options.get("bake_mode", False),
                    clear_existing_motion=options.get("clear_existing_motion", False),
                    create_mmd_control_rig=options.get("create_mmd_control_rig", False),
                    vmd_bytes=vmd_bytes,
                    pmx_bytes=pmx_bytes,
                    pmx_path=pmx_path,
                    profile=profile,
                    progress_callback=progress_callback,
                    use_native_physics_bake=use_native_physics_bake,
                    reduce_bake_keys=reduce_bake_keys,
                    reduce_translate_tolerance=reduce_translate_tolerance,
                    reduce_rotate_tolerance=reduce_rotate_tolerance,
                    reduce_morph_tolerance=reduce_morph_tolerance,
                    scene_animation_only=scene_animation_only,
                    **model_target_kwargs,
                )
        finally:
            vmd_profile.flush("import_vmd_file")

        if success:
            logger.info("VMD file import completed")
            if not scene_animation_only:
                _try_recover_physics_drivers(target_model, logger, profile)
                from ..core.collider_authoring import refresh_collider_authoring_pose

                try:
                    collider_shapes = (
                        cmds.listRelatives(
                            target_model,
                            allDescendents=True,
                            fullPath=True,
                            type="mmdRigidBodyShape",
                        )
                        if cmds.objExists(target_model)
                        else []
                    ) or []
                except RuntimeError:
                    # mayapy can import/animate a rig without loading the
                    # optional Python collider-shape registration.
                    collider_shapes = []
                for shape in collider_shapes:
                    transforms = cmds.listRelatives(shape, parent=True, fullPath=True) or []
                    if transforms:
                        display_scale = float(cmds.getAttr(f"{transforms[0]}.scaleX"))
                        refresh_collider_authoring_pose(
                            transforms[0], shape, display_scale
                        )
            native_physics_used = bool(
                (profile.get("vmd_converter") or {})
                .get("native_physics_bake", {})
                .get("used")
            )
            if native_physics_used:
                profile["native_physics_bake_applied"] = True
            if not scene_animation_only and is_mmd_runtime_available():
                # Phase 2: ライブノードの自動作成オプション
                if options.get("use_live_runtime", False) and target_model:
                    if not pmx_path or not os.path.exists(pmx_path):
                        logger.warning(
                            "Skipping live runtime node creation: PMX file path could not be resolved"
                        )
                    else:
                        try:
                            from mmd_tools.core.native.mmd_anim_runtime import (
                                connect_runtime_node_outputs_to_model,
                                create_runtime_node_for_model,
                            )
                            node = create_runtime_node_for_model(target_model, pmx_path, filepath)
                            logger.debug(f"Created live runtime node: {node}")
                            dg_result = connect_runtime_node_outputs_to_model(node, target_model, pmx_path=pmx_path)
                            logger.info(
                                "Live runtime DG connection: bones=%d morphs=%d skipped=%d warnings=%d",
                                len(dg_result.get("connected_bones", [])),
                                len(dg_result.get("connected_morphs", [])),
                                len(dg_result.get("skipped", [])),
                                len(dg_result.get("warnings", [])),
                            )
                            for warning in dg_result.get("warnings", []):
                                logger.warning(f"Live runtime DG connection warning: {warning}")
                        except Exception as e:
                            logger.warning(f"Failed to create live node: {e}")

            cmds.inViewMessage(
                amg=f"VMD animation imported successfully from: {filepath}",
                pos="midCenter",
                fade=True,
                fadeStayTime=2000,
                fadeOutTime=500,
            )
        else:
            cmds.warning("Failed to import VMD file")
            raise MMDImportException(f"Failed to import VMD file {filepath}")

        return success

    except MMDImportException:
        raise
    except Exception as e:
        logger.error(f"Failed to import VMD file {filepath}: {e}", exc_info=True)
        raise MMDImportException(f"Failed to import VMD file {filepath}: {e}") from e
