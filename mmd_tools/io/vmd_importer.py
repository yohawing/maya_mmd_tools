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


def _repair_physics_preview_feedback(target_model: Optional[str], logger) -> int:
    """Reconnect Bullet preview feedback that VMD keying may have replaced."""
    if not target_model or not cmds.objExists(target_model):
        return 0
    try:
        from ..converters import PhysicsConverter

        repaired = PhysicsConverter().connect_existing_bullet_preview_to_bones(target_model)
        if repaired:
            logger.info("Repaired %d Bullet physics preview feedback connection(s) after VMD import", repaired)
        return repaired
    except Exception as exc:
        logger.warning(f"Failed to repair Bullet physics preview feedback after VMD import: {exc}")
        return 0


def _set_physics_preview_feedback_enabled(target_model: Optional[str], enabled: bool, logger) -> int:
    """Toggle only generated Bullet preview constraints for one model."""
    if not target_model or not cmds.objExists(target_model):
        return 0
    try:
        from ..converters import PhysicsConverter

        changed = PhysicsConverter().set_existing_bullet_preview_feedback_enabled(target_model, enabled)
        if changed:
            action = "Restored" if enabled else "Disabled"
            logger.info("%s %d Bullet physics preview feedback connection(s) after VMD import", action, changed)
        return changed
    except Exception as exc:
        logger.warning(f"Failed to toggle Bullet physics preview feedback after VMD import: {exc}")
        return 0


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
            - pmx_path: 対応する PMX ファイルのパス
            - pmx_bytes: 生 PMX バイト
            - bake_mode: True の場合はリグ経由ではなく runtime bake を優先
            - use_native_physics_bake: True かつ bake_mode のとき native physics bake を試行する（default False）
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
        # オプションからターゲットモデルを取得
        target_namespace = None
        target_model = options.get("target_model")

        if target_model:
            target_namespace = NamespaceUtils.get_namespace_from_node(target_model)
            if target_namespace:
                logger.info(f"Target namespace: {target_namespace}")
        else:
            selected = cmds.ls(selection=True)
            if selected:
                for sel in selected:
                    target_namespace = NamespaceUtils.get_namespace_from_node(sel)
                    if target_namespace:
                        logger.info(f"Target namespace: {target_namespace}")
                        target_model = sel
                        break
                if not target_model:
                    target_model = selected[0]
                    logger.info(f"Target model without namespace: {target_model}")
            else:
                logger.warning("Target model is not specified.")

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
                        logger.info(f"Restored PMX source from model: {pmx_path}")
            except Exception:
                logger.debug("Failed to restore PMX source from target model", exc_info=True)

        if not pmx_bytes and not pmx_path:
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
        try:
            with vmd_profile.scope("vmd_converter_convert"):
                success = converter.convert(
                    parser,
                    target_namespace,
                    bake_mode=options.get("bake_mode", False),
                    clear_existing_motion=options.get("clear_existing_motion", False),
                    vmd_bytes=vmd_bytes,
                    pmx_bytes=pmx_bytes,
                    pmx_path=pmx_path,
                    profile=profile,
                    progress_callback=progress_callback,
                    use_native_physics_bake=use_native_physics_bake,
                )
        finally:
            vmd_profile.flush("import_vmd_file")

        if success:
            logger.info("VMD file import completed")
            # Ensure Root preference attr + live AE watch for older models that
            # enter the VMD path without a prior physics-setup rewrite. Never
            # overwrites an existing user value. Native bake exclusion is still
            # temporary and must not write this preference.
            from ..converters import PhysicsConverter

            if target_model:
                try:
                    PhysicsConverter.ensure_legacy_bullet_enabled_control(target_model)
                except Exception as exc:
                    logger.warning(
                        f"Failed to ensure Root mmd_legacy_bullet_enabled control: {exc}"
                    )

            # Native physics bake writes final joint animation curves. Do not reconnect
            # Bullet preview/constraint feedback that would overwrite the baked result.
            # Skip only when the converter actually used native physics bake — not merely
            # when the caller requested it (fallback still needs preview repair).
            # Native bake exclusion is temporary and must not overwrite the user's
            # Root.mmd_legacy_bullet_enabled preference.
            native_physics_used = bool(
                (profile.get("vmd_converter") or {})
                .get("native_physics_bake", {})
                .get("used")
            )
            if native_physics_used:
                disabled_physics = _set_physics_preview_feedback_enabled(target_model, False, logger)
                logger.info(
                    "Native physics bake owns final joint animation; Bullet preview feedback is disabled for this model"
                )
                profile["physics_preview_repair_skipped"] = "native_physics_bake_used"
                profile["legacy_physics_preview_disabled"] = disabled_physics
            else:
                legacy_bullet_enabled = PhysicsConverter.get_legacy_bullet_enabled(target_model)
                profile["legacy_bullet_enabled"] = bool(legacy_bullet_enabled)
                if not legacy_bullet_enabled:
                    # User turned off the complete legacy Maya Bullet setup for
                    # this model. Re-apply the persistent Root state here as
                    # scriptJobs do not survive reopening a saved scene.
                    disabled_physics = PhysicsConverter.apply_legacy_bullet_enabled_from_attr(
                        target_model
                    )
                    logger.info(
                        "Root mmd_legacy_bullet_enabled is False; leaving legacy Bullet setup suspended"
                    )
                    profile["physics_preview_repair_skipped"] = "legacy_bullet_disabled"
                    profile["legacy_physics_preview_disabled"] = disabled_physics
                else:
                    restored_physics = _set_physics_preview_feedback_enabled(target_model, True, logger)
                    if restored_physics:
                        profile["legacy_physics_preview_restored"] = restored_physics
                    repaired_physics = _repair_physics_preview_feedback(target_model, logger)
                    if repaired_physics:
                        profile["physics_preview_repaired"] = repaired_physics
            if is_mmd_runtime_available():
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
                            logger.info(f"Created live runtime node: {node}")
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
