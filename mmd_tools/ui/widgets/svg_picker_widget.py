"""SVG-backed, path-hit-tested canvases used by the Animator Toolset pickers.

The SVG remains the visual authority while the same closed vector shapes drive
hover and click testing.  This keeps Illustrator-authored regions accurate
without reintroducing rectangular ``QPushButton`` hit areas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..qt_compat import (
    QByteArray,
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPointF,
    QPolygonF,
    QRectF,
    QSvgRenderer,
    QSizePolicy,
    QToolTip,
    QTransform,
    Qt,
    Signal,
    QWidget,
)

_SVG_NS = "http://www.w3.org/2000/svg"
_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_PATH_TOKEN_RE = re.compile(rf"[A-Za-z]|{_NUMBER}")
_TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
_CANVAS_WIDTH = 268.0
_CANVAS_HEIGHT = 378.0


@dataclass(frozen=True)
class SvgRegionSource:
    """Map one Illustrator element to the semantic picker region it drives."""

    element_id: str
    region_id: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _float_values(value: str) -> list[float]:
    return [float(item) for item in re.findall(_NUMBER, value)]


def _svg_path(path_data: str) -> QPainterPath:
    """Convert the path commands emitted by Illustrator into ``QPainterPath``."""

    tokens = _PATH_TOKEN_RE.findall(path_data.replace(",", " "))
    path = QPainterPath()
    index = 0
    command = ""
    current = QPointF(0.0, 0.0)
    subpath_start = QPointF(0.0, 0.0)
    last_control = None
    previous_command = ""

    argument_counts = {
        "M": 2,
        "L": 2,
        "H": 1,
        "V": 1,
        "C": 6,
        "S": 4,
        "Q": 4,
        "T": 2,
    }

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command.upper() == "Z":
                path.closeSubpath()
                current = QPointF(subpath_start)
                last_control = None
                previous_command = command
                continue

        upper = command.upper()
        if upper not in argument_counts:
            raise ValueError(f"Unsupported SVG path command: {command!r}")

        count = argument_counts[upper]
        if index + count > len(tokens) or tokens[index].isalpha():
            raise ValueError(f"Incomplete SVG path command: {command!r}")
        values = [float(value) for value in tokens[index : index + count]]
        index += count
        relative = command.islower()

        def point(x: float, y: float) -> QPointF:
            if relative:
                return QPointF(current.x() + x, current.y() + y)
            return QPointF(x, y)

        if upper == "M":
            destination = point(values[0], values[1])
            path.moveTo(destination)
            current = destination
            subpath_start = QPointF(destination)
            command = "l" if relative else "L"
            last_control = None
        elif upper == "L":
            destination = point(values[0], values[1])
            path.lineTo(destination)
            current = destination
            last_control = None
        elif upper == "H":
            x = current.x() + values[0] if relative else values[0]
            current = QPointF(x, current.y())
            path.lineTo(current)
            last_control = None
        elif upper == "V":
            y = current.y() + values[0] if relative else values[0]
            current = QPointF(current.x(), y)
            path.lineTo(current)
            last_control = None
        elif upper == "C":
            control_1 = point(values[0], values[1])
            control_2 = point(values[2], values[3])
            destination = point(values[4], values[5])
            path.cubicTo(control_1, control_2, destination)
            current = destination
            last_control = control_2
        elif upper == "S":
            if previous_command.upper() in {"C", "S"} and last_control is not None:
                control_1 = QPointF(
                    2.0 * current.x() - last_control.x(),
                    2.0 * current.y() - last_control.y(),
                )
            else:
                control_1 = QPointF(current)
            control_2 = point(values[0], values[1])
            destination = point(values[2], values[3])
            path.cubicTo(control_1, control_2, destination)
            current = destination
            last_control = control_2
        elif upper == "Q":
            control = point(values[0], values[1])
            destination = point(values[2], values[3])
            path.quadTo(control, destination)
            current = destination
            last_control = control
        elif upper == "T":
            if previous_command.upper() in {"Q", "T"} and last_control is not None:
                control = QPointF(
                    2.0 * current.x() - last_control.x(),
                    2.0 * current.y() - last_control.y(),
                )
            else:
                control = QPointF(current)
            destination = point(values[0], values[1])
            path.quadTo(control, destination)
            current = destination
            last_control = control

        previous_command = command

    return path


def _element_transform(value: str) -> QTransform:
    transform = QTransform()
    for match in _TRANSFORM_RE.finditer(value or ""):
        operation = match.group(1).lower()
        values = _float_values(match.group(2))
        if operation == "translate":
            transform.translate(values[0], values[1] if len(values) > 1 else 0.0)
        elif operation == "rotate":
            if len(values) == 3:
                transform.translate(values[1], values[2])
                transform.rotate(values[0])
                transform.translate(-values[1], -values[2])
            else:
                transform.rotate(values[0])
        elif operation == "scale":
            transform.scale(values[0], values[1] if len(values) > 1 else values[0])
        elif operation == "matrix" and len(values) == 6:
            transform *= QTransform(*values)
        else:
            raise ValueError(f"Unsupported SVG transform: {match.group(0)!r}")
    return transform


def _shape_path(element: ET.Element) -> QPainterPath:
    tag = _local_name(element.tag)
    path = QPainterPath()
    if tag == "path":
        path = _svg_path(element.get("d", ""))
    elif tag == "rect":
        path.addRect(
            float(element.get("x", 0.0)),
            float(element.get("y", 0.0)),
            float(element.get("width", 0.0)),
            float(element.get("height", 0.0)),
        )
    elif tag == "polygon":
        values = _float_values(element.get("points", ""))
        points = [QPointF(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        if points:
            path.addPolygon(QPolygonF(points))
            path.closeSubpath()
    else:
        raise ValueError(f"Unsupported SVG picker shape: {tag}")

    transform_value = element.get("transform")
    if transform_value:
        path = _element_transform(transform_value).map(path)
    return path


def _remove_elements(root: ET.Element, element_ids: set[str]) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") in element_ids:
                parent.remove(child)


def _renderer_bytes(svg_text: str, removed_element_ids: set[str]) -> bytes:
    root = ET.fromstring(svg_text)
    _remove_elements(root, removed_element_ids)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _paths_by_element_id(svg_text: str, sources: tuple[SvgRegionSource, ...]) -> dict[str, QPainterPath]:
    root = ET.fromstring(svg_text)
    elements = {element.get("id"): element for element in root.iter() if element.get("id")}
    paths: dict[str, QPainterPath] = {}
    for source in sources:
        element = elements.get(source.element_id)
        if element is None:
            raise ValueError(f"SVG picker element is missing: {source.element_id}")
        paths[source.region_id] = _shape_path(element)
    return paths


def _paths_from_shape_order(svg_text: str, region_ids: tuple[str, ...]) -> dict[str, QPainterPath]:
    root = ET.fromstring(svg_text)
    shapes = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"path", "rect", "polygon"}
        and element.get("id") != "canvas-background"
    ]
    if len(shapes) != len(region_ids):
        raise ValueError(
            f"SVG picker shape count changed: expected {len(region_ids)}, got {len(shapes)}"
        )
    return {region_id: _shape_path(element) for region_id, element in zip(region_ids, shapes)}


class SvgPickerWidget(QWidget):
    """Render an SVG overlay and emit semantic IDs from exact vector hit paths."""

    shape_clicked = Signal(str)
    shapes_selected = Signal(object)

    def __init__(
        self,
        svg_path: Path,
        *,
        background_path: Path | None = None,
        region_sources: tuple[SvgRegionSource, ...] = (),
        ordered_region_ids: tuple[str, ...] = (),
        region_labels: dict[str, str] | None = None,
        tooltip_labels: dict[str, str] | None = None,
        removed_element_ids: set[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(134, 189)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        svg_text = svg_path.read_text(encoding="utf-8")
        self._renderer = QSvgRenderer(
            QByteArray(_renderer_bytes(svg_text, removed_element_ids or set()))
        )
        if region_sources:
            self._region_paths = _paths_by_element_id(svg_text, region_sources)
        else:
            self._region_paths = _paths_from_shape_order(svg_text, ordered_region_ids)

        self._background = QPixmap(str(background_path)) if background_path else QPixmap()
        self._region_labels = region_labels or {}
        self._tooltip_labels = tooltip_labels or {}
        self._hovered_region: str | None = None
        self._pressed_region: str | None = None
        self._drag_origin: QPointF | None = None
        self._selection_rect: QRectF | None = None
        self._selected_regions: set[str] = set()
        self._enabled_regions = set(self._region_paths)
        self._additive_selection = False

    @property
    def region_ids(self) -> tuple[str, ...]:
        """Return semantic region IDs in front-to-back hit-test order."""

        return tuple(self._region_paths)

    @property
    def additive_selection(self) -> bool:
        """Whether the currently emitted picker action has Shift held."""

        return self._additive_selection

    def update_region_texts(
        self,
        *,
        labels: dict[str, str] | None = None,
        tooltips: dict[str, str] | None = None,
    ) -> None:
        """Update localized overlay text without rebuilding picker geometry."""

        if labels:
            self._region_labels.update(labels)
        if tooltips:
            self._tooltip_labels.update(tooltips)
        self.update()

    def set_enabled_regions(self, region_ids) -> None:
        """Disable unavailable model regions while keeping the artwork visible."""

        self._enabled_regions = set(region_ids) & set(self._region_paths)
        self._selected_regions &= self._enabled_regions
        self.update()

    def set_selected_regions(self, region_ids) -> None:
        """Show the current Maya selection immediately on the picker."""

        self._selected_regions = set(region_ids) & self._enabled_regions
        self.repaint()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(189, round(width * _CANVAS_HEIGHT / _CANVAS_WIDTH))

    def _canvas_rect(self) -> QRectF:
        """Fit the authored canvas into all available space without distortion."""

        scale = min(self.width() / _CANVAS_WIDTH, self.height() / _CANVAS_HEIGHT)
        width = _CANVAS_WIDTH * scale
        height = _CANVAS_HEIGHT * scale
        return QRectF(
            (self.width() - width) * 0.5,
            (self.height() - height) * 0.5,
            width,
            height,
        )

    def _canvas_point(self, point: QPointF) -> QPointF | None:
        canvas_rect = self._canvas_rect()
        if not canvas_rect.contains(point):
            return None
        return QPointF(
            (point.x() - canvas_rect.x()) * _CANVAS_WIDTH / canvas_rect.width(),
            (point.y() - canvas_rect.y()) * _CANVAS_HEIGHT / canvas_rect.height(),
        )

    def _region_at(self, point: QPointF) -> str | None:
        point = self._canvas_point(point)
        if point is None:
            return None
        for region_id, path in reversed(tuple(self._region_paths.items())):
            if region_id in self._enabled_regions and path.contains(point):
                return region_id
        return None

    def _regions_in_rect(self, widget_rect: QRectF) -> list[str]:
        canvas_rect = widget_rect.intersected(self._canvas_rect())
        if canvas_rect.isEmpty():
            return []
        top_left = self._canvas_point(canvas_rect.topLeft())
        bottom_right = self._canvas_point(canvas_rect.bottomRight())
        if top_left is None or bottom_right is None:
            return []
        selection = QRectF(top_left, bottom_right).normalized()
        return [
            region_id
            for region_id, path in self._region_paths.items()
            if region_id in self._enabled_regions and path.intersects(selection)
        ]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        canvas_rect = self._canvas_rect()
        if not self._background.isNull():
            painter.drawPixmap(
                canvas_rect,
                self._background,
                QRectF(self._background.rect()),
            )
        self._renderer.render(painter, canvas_rect)

        painter.save()
        painter.translate(canvas_rect.x(), canvas_rect.y())
        painter.scale(
            canvas_rect.width() / _CANVAS_WIDTH,
            canvas_rect.height() / _CANVAS_HEIGHT,
        )

        for region_id in self._selected_regions:
            path = self._region_paths[region_id]
            color = QColor(77, 196, 255, 150)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 255), 2.2))
            painter.drawPath(path)

        if self._region_labels:
            font = QFont()
            font.setPixelSize(7)
            painter.setFont(font)
            painter.setPen(QPen(QColor(225, 235, 244), 1.0))
            for region_id, label in self._region_labels.items():
                painter.drawText(
                    self._region_paths[region_id].boundingRect(),
                    Qt.AlignCenter,
                    label,
                )

        if self._hovered_region:
            path = self._region_paths[self._hovered_region]
            color = QColor(110, 194, 255, 80)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 210), 1.2))
            painter.drawPath(path)

        painter.restore()
        if self._selection_rect is not None:
            painter.setBrush(QBrush(QColor(75, 165, 230, 45)))
            painter.setPen(QPen(QColor(105, 205, 255, 230), 1.0))
            painter.drawRect(self._selection_rect)
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        position = event.position() if hasattr(event, "position") else event.pos()
        if self._drag_origin is not None:
            distance = abs(position.x() - self._drag_origin.x()) + abs(
                position.y() - self._drag_origin.y()
            )
            if distance >= 4.0:
                self._selection_rect = QRectF(self._drag_origin, position).normalized()
                self._hovered_region = None
                QToolTip.hideText()
                self.update()
                super().mouseMoveEvent(event)
                return

        hovered = self._region_at(position)
        if hovered != self._hovered_region:
            self._hovered_region = hovered
            label = self._tooltip_labels.get(hovered or "", "")
            if label:
                global_position = (
                    event.globalPosition().toPoint()
                    if hasattr(event, "globalPosition")
                    else event.globalPos()
                )
                QToolTip.showText(global_position, label, self)
            else:
                QToolTip.hideText()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered_region = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            position = event.position() if hasattr(event, "position") else event.pos()
            self._pressed_region = self._region_at(position)
            if self._pressed_region:
                # A picker click is latency-sensitive: commit on press rather
                # than waiting for release and Maya's next UI cycle.
                self._additive_selection = bool(event.modifiers() & Qt.ShiftModifier)
                self.shape_clicked.emit(self._pressed_region)
                self._additive_selection = False
                self._drag_origin = None
            else:
                self._drag_origin = QPointF(position)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._selection_rect is not None:
                self._additive_selection = bool(event.modifiers() & Qt.ShiftModifier)
                self.shapes_selected.emit(self._regions_in_rect(self._selection_rect))
                self._additive_selection = False
        self._pressed_region = None
        self._drag_origin = None
        self._selection_rect = None
        self.update()
        super().mouseReleaseEvent(event)
