"""
Maya対応ログハンドラー

Maya環境に特化したログハンドラーを提供します。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def is_maya_environment() -> bool:
    """Maya環境かどうかを判定"""
    try:
        import maya.cmds

        return True
    except ImportError:
        return False


class MayaScriptEditorHandler(logging.Handler):
    """Maya Script Editorへの出力ハンドラー"""

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self._maya_available = is_maya_environment()

    def emit(self, record):
        """ログレコードをMaya Script Editorに出力"""
        if not self._maya_available:
            return

        try:
            import maya.cmds as cmds

            message = self.format(record)

            # ログレベルに応じて色を変更
            if record.levelno >= logging.ERROR:
                # エラーは赤色で表示
                cmds.cmdScrollFieldReporter(
                    "commandReporter1",
                    edit=True,
                    text=message + "\n",
                    textColor=(1.0, 0.0, 0.0),
                )
            elif record.levelno >= logging.WARNING:
                # 警告は黄色で表示
                cmds.cmdScrollFieldReporter(
                    "commandReporter1",
                    edit=True,
                    text=message + "\n",
                    textColor=(1.0, 1.0, 0.0),
                )
            else:
                # 通常のメッセージは白色で表示
                cmds.cmdScrollFieldReporter(
                    "commandReporter1",
                    edit=True,
                    text=message + "\n",
                    textColor=(1.0, 1.0, 1.0),
                )
        except Exception:
            # Maya Script Editorへの出力に失敗した場合は標準出力に出力
            print(self.format(record))


class MayaOutputWindowHandler(logging.Handler):
    """Maya Output Windowへの出力ハンドラー"""

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self._maya_available = is_maya_environment()

    def emit(self, record):
        """ログレコードをMaya Output Windowに出力"""
        try:
            message = self.format(record)

            if record.levelno >= logging.ERROR:
                # エラーはstderrに出力
                sys.stderr.write(message + "\n")
                sys.stderr.flush()
            else:
                # 通常のメッセージはstdoutに出力
                sys.stdout.write(message + "\n")
                sys.stdout.flush()
        except Exception:
            # 出力に失敗した場合はprintを使用
            print(self.format(record))


class UTF8FileHandler(RotatingFileHandler):
    """UTF-8対応ファイルハンドラー"""

    def __init__(self, filename, max_bytes=10 * 1024 * 1024, backup_count=5):
        """
        UTF-8対応のローテーションファイルハンドラーを初期化

        Args:
            filename: ログファイル名
            max_bytes: ファイルの最大サイズ
            backup_count: 保持するバックアップファイル数
        """
        super().__init__(
            filename,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )

    def emit(self, record):
        """ログレコードをファイルに出力"""
        try:
            super().emit(record)
        except Exception:
            # ファイル出力に失敗した場合はコンソールに出力
            print(f"ファイル出力エラー: {self.format(record)}")


class MayaDialogHandler(logging.Handler):
    """Maya UIダイアログ表示ハンドラー（ERROR/CRITICALレベル用）"""

    def __init__(self, level=logging.ERROR):
        super().__init__(level)
        self._maya_available = is_maya_environment()

    def emit(self, record):
        """ログレコードをMayaダイアログとして表示"""
        if not self._maya_available:
            return

        if record.levelno >= logging.ERROR:
            try:
                import maya.cmds as cmds

                title = "エラー" if record.levelno == logging.ERROR else "重大なエラー"
                message = self.format(record)

                # ダイアログを表示
                cmds.confirmDialog(
                    title=title,
                    message=message,
                    button=["OK"],
                    defaultButton="OK",
                    dismissString="OK",
                )
            except Exception:
                # Maya UIが使用できない場合はコンソールに出力
                print(f"Maya Dialog Error: {self.format(record)}")
