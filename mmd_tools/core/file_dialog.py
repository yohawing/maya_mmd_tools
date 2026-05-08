"""ファイルダイアログユーティリティ"""

from maya import cmds


def get_save_file_path(title, filter_str, default_name=""):
    """保存ファイルパスを取得

    Args:
        title: ダイアログのタイトル
        filter_str: ファイルフィルタ (例: "JSON Files (*.json)")
        default_name: デフォルトファイル名

    Returns:
        選択されたファイルパス、キャンセルされた場合はNone
    """
    # ファイルダイアログを表示
    result = cmds.fileDialog2(
        fileMode=0,  # Save
        caption=title,
        fileFilter=filter_str,
        startingDirectory=cmds.workspace(q=True, rootDirectory=True),
        dialogStyle=2,
    )

    if result:
        return result[0]
    return None


def get_open_file_path(title, filter_str):
    """開くファイルパスを取得

    Args:
        title: ダイアログのタイトル
        filter_str: ファイルフィルタ (例: "JSON Files (*.json)")

    Returns:
        選択されたファイルパス、キャンセルされた場合はNone
    """
    # ファイルダイアログを表示
    result = cmds.fileDialog2(
        fileMode=1,  # Open
        caption=title,
        fileFilter=filter_str,
        startingDirectory=cmds.workspace(q=True, rootDirectory=True),
        dialogStyle=2,
    )

    if result:
        return result[0]
    return None


def get_directory_path(title):
    """ディレクトリパスを取得

    Args:
        title: ダイアログのタイトル

    Returns:
        選択されたディレクトリパス、キャンセルされた場合はNone
    """
    # ディレクトリダイアログを表示
    result = cmds.fileDialog2(
        fileMode=3,  # Directory
        caption=title,
        startingDirectory=cmds.workspace(q=True, rootDirectory=True),
        dialogStyle=2,
    )

    if result:
        return result[0]
    return None
