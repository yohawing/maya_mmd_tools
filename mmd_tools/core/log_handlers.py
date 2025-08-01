import logging
from mmd_tools.ui.qt_compat import QObject, Signal

class QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._qt_bridge = QtLogBridge()
        # ログレベル情報を含むフォーマッターを設定
        formatter = logging.Formatter('[MMD] %(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.setFormatter(formatter)
    
    @property
    def message_written(self):
        return self._qt_bridge.message_written

    def emit(self, record):
        message = self.format(record)
        self._qt_bridge.send_message(message)


class QtLogBridge(QObject):
    message_written = Signal(str)
    
    def send_message(self, message):
        self.message_written.emit(message)