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

    mmd-anim runtime が利用可能な場合は、高精度ベイク（Phase 1）を利用できます。
    その場合、生の VMD バイト列を converter に渡します。

    Args:
        parser (VmdParser または VmdData): VMDファイルを解析したオブジェクト
        filepath (str): インポートするVMDファイルのパス
        options (dict): インポートオプション
            - target_model: 対象モデル
            - pmx_path: 対応する PMX ファイルのパス（runtime bake 用）
            - pmx_bytes: 生 PMX バイト（runtime bake 用）

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
                        break
            else:
                logger.warning("ターゲットモデルが指定されていません。")

        # mmd-anim runtime bake のために生バイトを読み込む
        vmd_bytes = None
        try:
            with open(filepath, "rb") as f:
                vmd_bytes = f.read()
        except Exception as e:
            logger.warning(f"VMD 生バイトの読み込みに失敗（runtime bake は使用できません）: {e}")

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
                logger.info("mmd-anim runtime を使用した高精度ベイクが有効でした")

                # Phase 2: ライブノードの自動作成オプション
                if options.get("use_live_runtime", False) and target_model:
                    try:
                        from mmd_tools.core.native import create_runtime_node_for_model
                        node = create_runtime_node_for_model(target_model, pmx_path or "", filepath)
                        logger.info(f"ライブランタイムノードを作成: {node}")
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
