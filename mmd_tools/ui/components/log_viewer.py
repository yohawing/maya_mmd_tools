from PySide6.QtWidgets import QTextEdit

class LogViewer(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
