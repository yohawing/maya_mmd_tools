"""Shared icon-only tool button with localized fallback and accessibility."""

from pathlib import Path

from ..qt_compat import QIcon, QPushButton, QSize, Signal

_SYMBOL_DIR = Path(__file__).resolve().parents[1] / "assets" / "symbols"


class MaterialSymbolToolButton(QPushButton):
    """Consistent square icon button used by refresh and Animator controls."""

    stateChanged = Signal(int)

    def __init__(self, symbol: str, text: str = "", parent=None, checkable=False):
        super().__init__(parent)
        self._fallback_text = text
        path = _SYMBOL_DIR / f"{symbol}.svg"
        self._has_icon = path.is_file()
        if self._has_icon:
            self.setIcon(QIcon(str(path)))
            self.setIconSize(QSize(18, 18))
        super().setText("" if self._has_icon else text)
        self.setFixedSize(28, 28)
        self.setCheckable(checkable)
        self.setToolTip(text)
        self.setAccessibleName(text)
        if checkable:
            self.toggled.connect(lambda checked: self.stateChanged.emit(2 if checked else 0))

    def setText(self, text: str) -> None:
        """Keep localization in tooltip/accessibility while retaining icon-only UI."""
        self._fallback_text = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        super().setText("" if self._has_icon else text)

