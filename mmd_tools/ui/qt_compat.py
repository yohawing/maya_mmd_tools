"""
Qt互換性モジュール。
PySide6とPySide2の差異を吸収し、透過的に扱えるようにします。
"""

QT_BINDING = ""

try:
    from PySide6.QtCore import QObject, Signal, Qt, QSettings, QTimer  # noqa: F401
    from PySide6.QtGui import QDoubleValidator, QColor, QTextCursor, QTextCharFormat  # noqa: F401
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
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QHeaderView,
        QMessageBox,
        QInputDialog,
        QToolBar,
        QAction,
        QMenuBar,
        QMenu,
    )
    from shiboken6 import wrapInstance  # noqa: F401

    QT_BINDING = "PySide6"

except ImportError:
    QT_BINDING = "PySide2"
