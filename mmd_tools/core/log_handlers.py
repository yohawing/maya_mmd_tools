import logging
from mmd_tools.ui.qt_compat import QObject, Signal


class QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._qt_bridge = QtLogBridge()
        self._emitting = False  # 再帰防止フラグ
        # ログレベル情報を含むフォーマッターを設定
        formatter = logging.Formatter("[MMD] %(asctime)s - %(name)s - %(levelname)s - %(message)s")
        self.setFormatter(formatter)

    @property
    def message_written(self):
        return self._qt_bridge.message_written

    def emit(self, record):
        # 再帰を防ぐ
        if self._emitting:
            return

        # 翻訳関連のログを除外（無限ループを防ぐ）
        if record.name == "mmd_tools.ui.translations.translator":
            return

        try:
            self._emitting = True
            message = self.format(record)
            self._qt_bridge.send_message(message)
        finally:
            self._emitting = False


class QtLogBridge(QObject):
    message_written = Signal(str)

    def send_message(self, message):
        self.message_written.emit(message)
