"""
Qt互換性モジュール。
PySide6とPySide2の差異を吸収し、透過的に扱えるようにします。
"""

QT_BINDING = ""

try:
    from PySide6.QtCore import QObject, Signal, Qt, QSettings
    from PySide6.QtGui import QDoubleValidator, QColor
    from PySide6.QtWidgets import (
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
        QFileDialog,
        QGroupBox,
        QFormLayout,
        QCheckBox,
        QComboBox,
        QListWidget,
        QSlider,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QColorDialog,
        QDoubleSpinBox,
        QSpinBox,
        QGridLayout,
        QScrollArea,
        QListWidgetItem,
        QStatusBar,
        QProgressBar,
    )
    from shiboken6 import wrapInstance

    QT_BINDING = "PySide6"

except ImportError:
    from PySide2.QtCore import QObject, Signal, Qt, QSettings
    from PySide2.QtGui import QDoubleValidator, QColor
    from PySide2.QtWidgets import (
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
        QFileDialog,
        QGroupBox,
        QFormLayout,
        QCheckBox,
        QComboBox,
        QListWidget,
        QSlider,
        QTreeView,
        QTreeWidget,
        QTreeWidgetItem,
        QColorDialog,
        QDoubleSpinBox,
        QSpinBox,
        QGridLayout,
        QScrollArea,
        QListWidgetItem,
        QStatusBar,
        QProgressBar,
    )
    from shiboken2 import wrapInstance

    QT_BINDING = "PySide2"
