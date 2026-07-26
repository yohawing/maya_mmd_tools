"""
Qt互換性モジュール。
PySide6とPySide2の差異を吸収し、透過的に扱えるようにします。
"""

QT_BINDING = ""

try:
    from PySide6 import QtCore  # noqa: F401
    from PySide6.QtCore import QByteArray, QObject, QPointF, QRectF, QSize, Signal, Qt, QSettings, QTimer  # noqa: F401
    from PySide6.QtGui import (  # noqa: F401
        QAction,
        QBrush,
        QColor,
        QDoubleValidator,
        QFont,
        QIcon,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
        QPolygonF,
        QTextCharFormat,
        QTextCursor,
        QTransform,
    )
    from PySide6.QtSvg import QSvgRenderer  # noqa: F401
    from PySide6.QtWidgets import (  # noqa: F401
        QApplication,
        QMainWindow,
        QTabWidget,
        QDockWidget,
        QPushButton,
        QLineEdit,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QTextEdit,
        QDialog,
        QFileDialog,
        QGroupBox,
        QFormLayout,
        QCheckBox,
        QComboBox as _QComboBox,
        QListWidget,
        QSlider as _QSlider,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QColorDialog,
        QDoubleSpinBox as _QDoubleSpinBox,
        QAbstractSpinBox,
        QSpinBox as _QSpinBox,
        QGridLayout,
        QScrollArea,
        QListWidgetItem,
        QStatusBar,
        QProgressBar,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QHeaderView,
        QMessageBox,
        QInputDialog,
        QToolBar,
        QToolTip,
        QMenuBar,
        QMenu,
        QSizePolicy,
    )
    from shiboken6 import wrapInstance  # noqa: F401

    QT_BINDING = "PySide6"

except ImportError:
    from PySide2 import QtCore  # noqa: F401
    from PySide2.QtCore import QByteArray, QObject, QPointF, QRectF, QSize, Signal, Qt, QSettings, QTimer  # noqa: F401
    from PySide2.QtGui import (  # noqa: F401
        QBrush,
        QColor,
        QDoubleValidator,
        QFont,
        QIcon,
        QPainter,
        QPainterPath,
        QPen,
        QPixmap,
        QPolygonF,
        QTextCharFormat,
        QTextCursor,
        QTransform,
    )
    from PySide2.QtSvg import QSvgRenderer  # noqa: F401
    from PySide2.QtWidgets import (  # noqa: F401
        QApplication,
        QMainWindow,
        QTabWidget,
        QDockWidget,
        QPushButton,
        QLineEdit,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QTextEdit,
        QDialog,
        QFileDialog,
        QGroupBox,
        QFormLayout,
        QCheckBox,
        QComboBox as _QComboBox,
        QListWidget,
        QSlider as _QSlider,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QColorDialog,
        QDoubleSpinBox as _QDoubleSpinBox,
        QAbstractSpinBox,
        QSpinBox as _QSpinBox,
        QGridLayout,
        QScrollArea,
        QListWidgetItem,
        QStatusBar,
        QProgressBar,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QHeaderView,
        QMessageBox,
        QInputDialog,
        QToolBar,
        QToolTip,
        QAction,
        QMenuBar,
        QMenu,
        QSizePolicy,
    )
    from shiboken2 import wrapInstance  # noqa: F401

    QT_BINDING = "PySide2"


class _IgnoreWheelMixin:
    """Prevent accidental value edits while scroll areas handle mouse wheels."""

    def wheelEvent(self, event):
        if hasattr(event, "ignore"):
            event.ignore()


class QComboBox(_IgnoreWheelMixin, _QComboBox):
    pass


class QSlider(_IgnoreWheelMixin, _QSlider):
    pass


class QDoubleSpinBox(_IgnoreWheelMixin, _QDoubleSpinBox):
    pass


class QSpinBox(_IgnoreWheelMixin, _QSpinBox):
    pass
