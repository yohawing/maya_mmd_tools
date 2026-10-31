"""Non-interactive search icon with a translated accessible label."""

from pathlib import Path

from ..qt_compat import QIcon, QLabel, Qt


class SearchLabel(QLabel):
    """Keep the Search label accessible without taking space from its input."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        icon = QIcon(str(Path(__file__).resolve().parents[1] / "assets/symbols/search.svg"))
        self._search_pixmap = icon.pixmap(20, 20)
        self.setFixedWidth(24)
        self.setAlignment(Qt.AlignCenter)
        self.setText(text)

    def setText(self, text):
        self.setToolTip(text)
        self.setAccessibleName(text)
        if self._search_pixmap.isNull():
            super().setText(text)
        else:
            self.setPixmap(self._search_pixmap)
