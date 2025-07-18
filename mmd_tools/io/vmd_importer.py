"""
VMDファイル（モーションデータ）をMayaシーンにインポートするためのモジュール。
"""

from maya import cmds
from ..converters.vmd_converter import VmdConverter
from ..core.logger import get_logger


def import_vmd_file(parser, filepath):
    """
    VMDファイルをMayaシーンにインポートします。

    Args:
        parser (VmdParser): VMDファイルを解析したパーサーオブジェクト
        filepath (str): インポートするVMDファイルのパス

    Returns:
        bool: インポートが成功したかどうか
    """
    logger = get_logger("vmd_importer")
    logger.info(f"VMDファイルのインポートを開始: {filepath}")

    try:
        # 選択されているオブジェクトからターゲットネームスペースを取得
        selected = cmds.ls(selection=True)
        target_namespace = None

        if selected:
            # 選択されたオブジェクトからネームスペースを取得
            node_namespace = selected[0].split(":")[0] if ":" in selected[0] else None
            if node_namespace:
                target_namespace = node_namespace
                logger.info(f"ターゲットネームスペース: {target_namespace}")

        # VMDコンバーターを使用してアニメーションを変換
        converter = VmdConverter()
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
