"""
VMDファイル（モーションデータ）をMayaシーンにインポートするためのモジュール。
"""

import os

from maya import cmds
from ..converters.vmd_converter import VmdConverter
from ..core.logger import get_logger
from ..core.namespace_utils import NamespaceUtils
from ..core.native.mmd_anim_runtime import is_mmd_runtime_available


def import_vmd_file(parser, filepath, options=None):
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
            - vmd_fps: VMDインポート時のMayaシーンFPS (30 or 60, default 30)。VMDフレーム番号はリスケールせず、シーンのタイムユニットのみ変更。

    Returns:
        bool: インポートが成功したかどうか
    """
    if options is None:
        options = {}
    logger = get_logger("vmd_importer")
    logger.info(f"VMDファイルのインポートを開始: {filepath}")

    try:
        # オプションからターゲットモデルを取得
        target_namespace = None
        target_model = options.get("target_model")

        if target_model:
            target_namespace = NamespaceUtils.get_namespace_from_node(target_model)
            if target_namespace:
                logger.info(f"ターゲットネームスペース: {target_namespace}")
        else:
            selected = cmds.ls(selection=True)
            if selected:
                for sel in selected:
                    target_namespace = NamespaceUtils.get_namespace_from_node(sel)
                    if target_namespace:
                        logger.info(f"ターゲットネームスペース: {target_namespace}")
                        target_model = sel
                        break
                if not target_model:
                    target_model = selected[0]
                    logger.info(f"ネームスペースなしのターゲットモデル: {target_model}")
            else:
                logger.warning("ターゲットモデルが指定されていません。")

        # Bake mode needs raw VMD bytes for mmd-anim final-pose evaluation.
        # Rig mode still receives these bytes, but VmdConverter rejects runtime
        # bake when live mmdCcdIk/mmdAppend rig connections are present.
        vmd_bytes = None
        try:
            with open(filepath, "rb") as f:
                vmd_bytes = f.read()
        except Exception as e:
            logger.warning(f"VMD 生バイトの読み込みに失敗: {e}")

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
                        logger.info(f"モデルから PMX ソースを復元: {pmx_path}")
            except Exception:
                pass

        if not pmx_bytes and not pmx_path:
            # 同じディレクトリに .pmx/.pmd があるか簡易推定
            try:
                vmd_dir = os.path.dirname(os.path.abspath(filepath))
                candidates = [f for f in os.listdir(vmd_dir) if f.lower().endswith((".pmx", ".pmd"))] if os.path.isdir(vmd_dir) else []
                if candidates:
                    pmx_path = os.path.join(vmd_dir, candidates[0])
                    logger.info(f"PMX ソースを自動推定: {pmx_path} （明示指定を推奨）")
            except Exception:
                pass

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
        success = converter.convert(
            parser,
            target_namespace,
            vmd_bytes=vmd_bytes,
            pmx_bytes=pmx_bytes,
            pmx_path=pmx_path,
        )

        if success:
            logger.info("VMDファイルのインポートが完了しました")
            if is_mmd_runtime_available():
                # Phase 2: ライブノードの自動作成オプション
                if options.get("use_live_runtime", False) and target_model:
                    if not pmx_path or not os.path.exists(pmx_path):
                        logger.warning(
                            "ライブランタイムノード作成をスキップ: PMX ファイル path が解決できません"
                        )
                    else:
                        try:
                            from mmd_tools.core.native.mmd_anim_runtime import (
                                connect_runtime_node_outputs_to_model,
                                create_runtime_node_for_model,
                            )
                            node = create_runtime_node_for_model(target_model, pmx_path, filepath)
                            logger.info(f"ライブランタイムノードを作成: {node}")
                            dg_result = connect_runtime_node_outputs_to_model(node, target_model, pmx_path=pmx_path)
                            logger.info(
                                "ライブランタイムDG接続: bones=%d morphs=%d skipped=%d warnings=%d",
                                len(dg_result.get("connected_bones", [])),
                                len(dg_result.get("connected_morphs", [])),
                                len(dg_result.get("skipped", [])),
                                len(dg_result.get("warnings", [])),
                            )
                            for warning in dg_result.get("warnings", []):
                                logger.warning(f"ライブランタイムDG接続警告: {warning}")
                        except Exception as e:
                            logger.warning(f"ライブノード作成に失敗: {e}")

            cmds.inViewMessage(
                amg=f"VMD animation imported successfully from: {filepath}",
                pos="midCenter",
                fade=True,
                fadeStayTime=2000,
                fadeOutTime=500,
            )
        else:
            cmds.warning("VMDファイルのインポートに失敗しました")

        return success

    except Exception as e:
        cmds.error(f"Failed to import VMD file {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return False
