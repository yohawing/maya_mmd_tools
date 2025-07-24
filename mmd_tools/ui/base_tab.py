from .qt_compat import QWidget


class BaseTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
