"""Shared icon-only tool button with localized fallback and accessibility."""

from pathlib import Path

from ..qt_compat import QColor, QIcon, QPainter, QPixmap, QPushButton, QSize, Signal

_SYMBOL_DIR = Path(__file__).resolve().parents[1] / "assets" / "symbols"

_VISIBILITY_STATES = ("visible", "reference", "hidden")
_VISIBILITY_LABELS = {
    "visible": "Visible",
    "reference": "Reference",
    "hidden": "Hidden",
}
_VISIBILITY_STYLES = {
    "visible": "QPushButton { border: 1px solid #5f9ea0; }",
    "reference": "QPushButton { border: 1px solid #d6a84f; }",
    "hidden": "QPushButton { border: 1px solid #777777; }",
}


class MaterialSymbolToolButton(QPushButton):
    """Consistent square icon button used by refresh and Animator controls."""

    stateChanged = Signal(int)
    visibilityStateChanged = Signal(str)

    def __init__(
        self,
        symbol: str,
        text: str = "",
        parent=None,
        checkable=False,
        tri_state=False,
    ):
        super().__init__(parent)
        self._fallback_text = text
        self._tri_state = bool(tri_state)
        self._visibility_state = "visible"
        self._visibility_available = True
        self._visibility_state_labels = dict(_VISIBILITY_LABELS)
        self._visibility_unavailable_label = "Unavailable"
        path = _SYMBOL_DIR / f"{symbol}.svg"
        self._has_icon = path.is_file()
        self._base_icon = QIcon()
        if self._has_icon:
            self._base_icon = QIcon(str(path))
            self.setIcon(self._base_icon)
            self.setIconSize(QSize(28, 28))
        super().setText("" if self._has_icon else text)
        self.setFixedSize(32, 32)
        self.setCheckable(bool(checkable) and not self._tri_state)
        self.setToolTip(text)
        self.setAccessibleName(text)
        if self._tri_state:
            # Keep bool toggling out of this path: a state transition emits
            # exactly one visibilityStateChanged signal below.
            self.clicked.connect(self.cycle_visibility_state)
            self._refresh_visibility_presentation()
        elif checkable:
            self.toggled.connect(lambda checked: self.stateChanged.emit(2 if checked else 0))

    def setText(self, text: str) -> None:
        """Keep localization in tooltip/accessibility while retaining icon-only UI."""
        self._fallback_text = text
        self.setAccessibleName(text)
        super().setText("" if self._has_icon else text)
        if self._tri_state:
            self._refresh_visibility_presentation()
        else:
            self.setToolTip(text)

    @property
    def visibility_state(self) -> str:
        """Return the state displayed by a tri-state visibility button."""

        return self._visibility_state

    @property
    def visibilityState(self) -> str:  # noqa: N802 - Qt-facing compatibility API.
        """Qt-style alias for extensions that use camelCase properties."""

        return self._visibility_state

    @property
    def is_tri_state(self) -> bool:
        """Whether this button owns the Animator tri-state cycle."""

        return self._tri_state

    @property
    def isTriState(self) -> bool:  # noqa: N802 - Qt-facing compatibility API.
        """Qt-style alias for :attr:`is_tri_state`."""

        return self._tri_state

    def set_visibility_state(self, state: str) -> None:
        """Update visual state from scene readback without emitting a signal."""

        normalized = str(state).strip().lower()
        if normalized not in _VISIBILITY_STATES:
            normalized = "visible"
        self._visibility_state = normalized
        if self._tri_state:
            self._refresh_visibility_presentation()

    def set_visibility_available(self, available: bool, unavailable_label: str | None = None) -> None:
        """Enable or disable scene-backed visibility interaction."""

        self._visibility_available = bool(available)
        if unavailable_label is not None:
            self._visibility_unavailable_label = str(unavailable_label)
        self.setEnabled(self._visibility_available)
        if self._tri_state:
            self._refresh_visibility_presentation()

    def setVisibilityAvailable(  # noqa: N802
        self, available: bool, unavailable_label: str | None = None
    ) -> None:
        """Qt-style alias for :meth:`set_visibility_available`."""

        self.set_visibility_available(available, unavailable_label)

    def set_visibility_labels(
        self, labels: dict[str, str], unavailable_label: str | None = None
    ) -> None:
        """Set localized state and unavailable labels used by tooltips."""

        self._visibility_state_labels.update(
            {key: str(value) for key, value in labels.items() if key in _VISIBILITY_STATES}
        )
        if unavailable_label is not None:
            self._visibility_unavailable_label = str(unavailable_label)
        if self._tri_state:
            self._refresh_visibility_presentation()

    def setVisibilityLabels(  # noqa: N802
        self, labels: dict[str, str], unavailable_label: str | None = None
    ) -> None:
        """Qt-style alias for :meth:`set_visibility_labels`."""

        self.set_visibility_labels(labels, unavailable_label)

    def setVisibilityState(self, state: str) -> None:  # noqa: N802
        """Qt-style alias for :meth:`set_visibility_state`."""

        self.set_visibility_state(state)

    def cycle_visibility_state(self, *_args) -> str:
        """Advance visible -> reference -> hidden -> visible and notify once."""

        if not self._tri_state:
            return self._visibility_state
        index = _VISIBILITY_STATES.index(self._visibility_state)
        next_state = _VISIBILITY_STATES[(index + 1) % len(_VISIBILITY_STATES)]
        self.set_visibility_state(next_state)
        self.visibilityStateChanged.emit(next_state)
        return next_state

    def cycleVisibilityState(self) -> str:  # noqa: N802
        """Qt-style alias for :meth:`cycle_visibility_state`."""

        return self.cycle_visibility_state()

    def _refresh_visibility_presentation(self) -> None:
        if self._visibility_available:
            label = self._visibility_state_labels[self._visibility_state]
        else:
            label = self._visibility_unavailable_label
        self.setToolTip(f"{self._fallback_text} ({label})")
        self.setAccessibleName(f"{self._fallback_text} ({label})")
        self.setStyleSheet(_VISIBILITY_STYLES[self._visibility_state])
        if not self._tri_state or not self._has_icon:
            return
        try:
            base = self._base_icon.pixmap(QSize(28, 28))
            badge = QPixmap(base)
            painter = QPainter(badge)
            colors = {
                "visible": QColor(84, 190, 126),
                "reference": QColor(232, 176, 65),
                "hidden": QColor(210, 88, 88),
            }
            color = colors[self._visibility_state]
            painter.setBrush(color)
            painter.setPen(color)
            if self._visibility_state == "visible":
                painter.drawEllipse(20, 20, 8, 8)
            elif self._visibility_state == "reference":
                painter.drawRect(20, 20, 8, 8)
            else:
                painter.drawLine(20, 20, 28, 28)
                painter.drawLine(28, 20, 20, 28)
            painter.end()
            self.setIcon(QIcon(badge))
        except Exception:
            # Headless Qt doubles and old Maya Qt builds may not expose the
            # full QPixmap/QPainter surface; tooltip/style remain authoritative.
            return
