"""Compact, column-stable controls used by the Animator morph editor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..qt_compat import (
    QLabel,
    QDoubleSpinBox,
    QPainter,
    QPixmap,
    QSlider,
    QSvgRenderer,
    Signal,
    Qt,
)

_ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "morph_types"
_ICON_ALIASES = {
    "additionaluv1": "additional_uv1",
    "additionaluv2": "additional_uv2",
    "additionaluv3": "additional_uv3",
    "additionaluv4": "additional_uv4",
}


def normalized_morph_type(morph_type: str) -> str:
    """Normalize scene metadata to one bundled icon identifier."""
    value = str(morph_type or "generic").lower().replace("-", "_").replace(" ", "_")
    return _ICON_ALIASES.get(value, value)


class ElidedMorphLabel(QLabel):
    """Fixed-width label that keeps the complete name in accessibility text."""

    def __init__(self, text: str, tooltip: str, parent=None):
        super().__init__(parent)
        self._full_text = str(text)
        self.setFixedWidth(116)
        self.setToolTip(tooltip)
        self.setAccessibleName(self._full_text)
        self.setAccessibleDescription(tooltip)
        self._update_elision()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        width = max(0, self.contentsRect().width() - 2)
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width))


class MorphWeightSpinBox(QDoubleSpinBox):
    """Wheel-safe 0..1 editor with explicit edit transaction signals."""

    edit_started = Signal()
    edit_finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing = False
        self.setRange(0.0, 1.0)
        self.setDecimals(3)
        self.setSingleStep(0.01)
        self.setKeyboardTracking(False)
        self.setFixedWidth(72)
        self.valueChanged.connect(self._ensure_edit_started)
        self.editingFinished.connect(self._finish_edit)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._ensure_edit_started()

    def focusOutEvent(self, event):
        self._finish_edit()
        super().focusOutEvent(event)

    def _ensure_edit_started(self, *_args) -> None:
        if not self._editing:
            self._editing = True
            self.edit_started.emit()

    def _finish_edit(self) -> None:
        if self._editing:
            self._editing = False
            self.edit_finished.emit()

    @property
    def is_editing(self) -> bool:
        """Whether keyboard or spin-button input is still uncommitted."""
        return self._editing


def create_morph_type_icon(morph_type: str) -> QLabel:
    """Create an accessible SVG icon label with a generic fallback."""
    normalized = normalized_morph_type(morph_type)
    path = _ICON_DIR / f"{normalized}.svg"
    if not path.is_file():
        normalized = "generic"
        path = _ICON_DIR / "generic.svg"

    label = QLabel()
    label.setFixedSize(22, 22)
    label.setAlignment(Qt.AlignCenter)
    renderer = QSvgRenderer(str(path))
    if renderer.isValid():
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        label.setPixmap(pixmap)
    label.setToolTip(f"Morph type: {normalized}")
    label.setAccessibleName(f"{normalized} morph")
    return label


@dataclass
class MorphRowWidgets:
    """Widgets and state belonging to one logical morph row."""

    icon: QLabel
    label: ElidedMorphLabel
    slider: QSlider
    editor: MorphWeightSpinBox
    plugs: tuple[str, ...]

    def set_value(self, value: float) -> None:
        """Update evaluated value without feeding signals back into Maya."""
        clamped = max(0.0, min(1.0, float(value)))
        for widget, display_value in (
            (self.slider, round(clamped * 100.0)),
            (self.editor, clamped),
        ):
            blocked = widget.blockSignals(True)
            widget.setValue(display_value)
            widget.blockSignals(blocked)

    def set_animation_state(self, state: str) -> None:
        """Render unanimated, keyed-here, or interpolated animation state."""
        styles = {
            "key": "QDoubleSpinBox { background: #71343b; border: 1px solid #ef6572; }",
            "animated": "QDoubleSpinBox { background: #5a4144; border: 1px solid #a96970; }",
            "static": "",
        }
        descriptions = {
            "key": "Animated morph; a key exists at the current frame.",
            "animated": "Animated morph; current value is between keys.",
            "static": "Morph is not animated.",
        }
        state = state if state in styles else "static"
        self.editor.setStyleSheet(styles[state])
        self.editor.setToolTip(descriptions[state])
        self.editor.setAccessibleDescription(descriptions[state])
