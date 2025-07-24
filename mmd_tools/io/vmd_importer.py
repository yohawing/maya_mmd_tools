"""
VMDファイル（モーションデータ）をMayaシーンにインポートするためのモジュール。
"""

from maya import cmds
from ..converters.vmd_converter import VmdConverter
from ..core.logger import get_logger


def import_vmd_file(parser, filepath, options=None):
    """
    VMDファイルをMayaシーンにインポートします。

    Args:
        parser (VmdParser): VMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするVMDファイルのパス
        options (dict): インポートオプション

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
        target_model = options.get('target_model')
        
        if target_model:
            # ターゲットモデルからネームスペースを取得
            if ":" in target_model:
                target_namespace = target_model.split(":")[0]
                logger.info(f"ターゲットネームスペース: {target_namespace}")
        else:
            # 選択されているオブジェクトからターゲットネームスペースを取得
            selected = cmds.ls(selection=True)
            if selected:
                # 選択されたオブジェクトからネームスペースを取得
                node_namespace = selected[0].split(":")[0] if ":" in selected[0] else None
                if node_namespace:
                    target_namespace = node_namespace
                    logger.info(f"ターゲットネームスペース: {target_namespace}")

        # VMDコンバーターを使用してアニメーションを変換
        converter = VmdConverter()
        # TODO: VmdConverterにoptionsを渡すように拡張
        success = converter.convert(parser, target_namespace)

        if success:
            logger.info("VMDファイルのインポートが完了しました")
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
