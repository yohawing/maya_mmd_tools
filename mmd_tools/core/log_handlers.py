import logging
from PySide6.QtCore import QObject, Signal

class QtLogHandler(logging.Handler, QObject):
    message_written = Signal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        message = self.format(record)
        self.message_written.emit(message)