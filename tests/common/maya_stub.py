"""``maya`` をスタブ化して Maya 非依存ユニットテストを可能にするヘルパー。

一部の Maya 非依存ロジック (例: UI presenter の純粋な分岐処理) は、それ自体は
``maya.cmds`` を実行時に呼ばないにもかかわらず、import 連鎖の途中で
``from maya import cmds`` を含むモジュールを経由するため、``maya`` パッケージが
``sys.modules`` に無いと **import 時点で** ``ModuleNotFoundError`` になる。

``install_maya_stub()`` は ``maya`` / ``maya.cmds`` / ``maya.mel`` /
``maya.api.OpenMaya`` / ``maya.api.OpenMayaAnim`` / ``maya.api.OpenMayaRender`` を
属性アクセス可能なダミーモジュール (``MagicMock`` ベース) として ``sys.modules`` に
登録する。これにより「import は通るが、実際に Maya API を呼ぶと意味のない Mock が
返る」状態になる。

重要な前提:
- このスタブは *import を通すため* のものであり、Maya API の挙動は再現しない。
  実際に ``cmds.xxx()`` を呼ぶロジックを検証したい場合は mayapy が必要。
- 既に本物の ``maya`` が ``sys.modules`` にある (mayapy 実行時) 場合は何もしない。
  したがって mayapy 経由のテストを汚染しない。

使い方 (テストモジュールの先頭、対象 import より前に呼ぶ)::

    from tests.common.maya_stub import install_maya_stub
    install_maya_stub(profile="minimal")

    from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter
"""

import sys
from types import ModuleType
from typing import Optional
from unittest.mock import MagicMock

# install_maya_stub() が登録したモジュール名 (テスト側の後始末用)
_STUBBED_MODULE_NAMES = (
    "maya",
    "maya.cmds",
    "maya.mel",
    "maya.OpenMaya",
    "maya.OpenMayaMPx",
    "maya.api",
    "maya.api.OpenMaya",
    "maya.api.OpenMayaAnim",
    "maya.api.OpenMayaRender",
    "maya.api.OpenMayaUI",
)

_CMDS_PROFILE_METHODS = (
    "namespace",
    "namespaceInfo",
    "ls",
    "listRelatives",
    "listConnections",
    "objExists",
    "attributeQuery",
)


def _reset_cmds_profile_methods(cmds: MagicMock) -> None:
    """Reset methods managed by named profiles to plain MagicMock children."""
    for name in _CMDS_PROFILE_METHODS:
        setattr(cmds, name, MagicMock(name=f"maya.cmds.{name}"))


def _configure_cmds_headless_profile(cmds: MagicMock) -> None:
    """Apply headless-safe defaults for common Maya query commands.

    Bare ``MagicMock`` results are truthy and record every chained call.  Code
    that probes Maya state in a loop, such as namespace collision checks, can
    otherwise grow memory abruptly in pure Python tests.
    """
    _reset_cmds_profile_methods(cmds)

    def _namespace(*_args, **kwargs):
        if "exists" in kwargs:
            return False
        if "set" in kwargs or "add" in kwargs or "removeNamespace" in kwargs:
            return None
        return None

    def _namespace_info(*_args, **kwargs):
        if kwargs.get("currentNamespace"):
            return ":"
        if kwargs.get("listOnlyNamespaces"):
            return []
        return None

    cmds.namespace.side_effect = _namespace
    cmds.namespaceInfo.side_effect = _namespace_info
    cmds.ls.return_value = []
    cmds.listRelatives.return_value = []
    cmds.listConnections.return_value = []
    cmds.objExists.return_value = False
    cmds.attributeQuery.return_value = False


def _configure_cmds_minimal_profile(_cmds: MagicMock) -> None:
    """Leave ``maya.cmds`` as a plain MagicMock for import-only tests."""
    _reset_cmds_profile_methods(_cmds)


_CMDS_PROFILE_CONFIGURERS = {
    "headless": _configure_cmds_headless_profile,
    "minimal": _configure_cmds_minimal_profile,
}


def _configure_cmds_profile(cmds: MagicMock, profile: str) -> None:
    """Apply a named ``maya.cmds`` stub profile."""
    try:
        configure = _CMDS_PROFILE_CONFIGURERS[profile]
    except KeyError as exc:
        valid = ", ".join(sorted(_CMDS_PROFILE_CONFIGURERS))
        raise ValueError(f"Unknown Maya cmds stub profile '{profile}'. Expected one of: {valid}") from exc
    configure(cmds)


def _is_real_maya_present() -> bool:
    """本物の Maya 環境 (mayapy) で動いているかを判定する。

    本物の ``maya`` は通常 ``ModuleType`` で、かつ ``MagicMock`` ではない。
    既にスタブを入れている場合は ``maya`` が ``ModuleType`` だが ``cmds`` 属性が
    ``MagicMock`` になっているのでスタブとして扱う (再登録は冪等)。
    """
    maya_mod = sys.modules.get("maya")
    if maya_mod is None:
        return False
    # 既存が我々のスタブ (MagicMock 属性) なら「本物ではない」
    cmds = getattr(maya_mod, "cmds", None)
    # cmds が None → 他テストの _seed_maya_modules() 等が maya モジュールに
    # cmds 属性をセットしないまま sys.modules だけ登録した状態。本物の Maya では
    # maya.cmds は必ず実モジュールなので None は「本物ではない」と判定する。
    if cmds is None or isinstance(cmds, MagicMock):
        return False
    # それ以外で maya が存在するなら本物 (mayapy) とみなす
    return True


def install_maya_stub(profile: Optional[str] = None) -> bool:
    """``maya`` 系モジュールをスタブとして ``sys.modules`` に登録する。

    Args:
        profile: ``maya.cmds`` の既定挙動。未指定で新規登録する場合は
            ``"minimal"`` として import を通すだけの素の ``MagicMock`` に留める。
            ``"headless"`` は既存互換の query-safe default を設定する。
            既存スタブに対する未指定呼び出しは冪等 no-op とし、明示指定時だけ
            profile を切り替える。

    Returns:
        スタブを新規登録した場合 True、本物の Maya が既にあり何もしなかった場合 False。
    """
    if _is_real_maya_present():
        return False

    # 既にスタブ済みなら冪等に True を返す
    if isinstance(sys.modules.get("maya"), ModuleType) and isinstance(
        getattr(sys.modules.get("maya"), "cmds", None), MagicMock
    ):
        if profile is not None:
            _configure_cmds_profile(sys.modules["maya"].cmds, profile)
        return True

    maya = ModuleType("maya")
    maya.cmds = MagicMock(name="maya.cmds")
    _configure_cmds_profile(maya.cmds, profile or "minimal")
    maya.mel = MagicMock(name="maya.mel")
    maya.OpenMaya = MagicMock(name="maya.OpenMaya")

    class _StubMPxFileTranslator:
        kImportAccessMode = 0
        kOpenAccessMode = 1
        kReferenceAccessMode = 2
        kIsMyFileType = 0
        kCouldBeMyFileType = 1
        kNotMyFileType = 2

        def __init__(self, *args, **kwargs):
            pass

    open_maya_mpx = ModuleType("maya.OpenMayaMPx")
    open_maya_mpx.MPxFileTranslator = _StubMPxFileTranslator
    open_maya_mpx.MFnPlugin = MagicMock(name="maya.OpenMayaMPx.MFnPlugin")
    open_maya_mpx.asMPxPtr = MagicMock(name="maya.OpenMayaMPx.asMPxPtr", side_effect=lambda value: value)
    maya.OpenMayaMPx = open_maya_mpx

    api = ModuleType("maya.api")
    api.OpenMaya = MagicMock(name="maya.api.OpenMaya")
    api.OpenMayaAnim = MagicMock(name="maya.api.OpenMayaAnim")
    api.OpenMayaRender = MagicMock(name="maya.api.OpenMayaRender")
    api.OpenMayaUI = MagicMock(name="maya.api.OpenMayaUI")
    maya.api = api

    sys.modules["maya"] = maya
    sys.modules["maya.cmds"] = maya.cmds
    sys.modules["maya.mel"] = maya.mel
    sys.modules["maya.OpenMaya"] = maya.OpenMaya
    sys.modules["maya.OpenMayaMPx"] = maya.OpenMayaMPx
    sys.modules["maya.api"] = api
    sys.modules["maya.api.OpenMaya"] = api.OpenMaya
    sys.modules["maya.api.OpenMayaAnim"] = api.OpenMayaAnim
    sys.modules["maya.api.OpenMayaRender"] = api.OpenMayaRender
    sys.modules["maya.api.OpenMayaUI"] = api.OpenMayaUI
    return True


# ----------------------------------------------------------------------
# Qt (PySide6) スタブ
# ----------------------------------------------------------------------
#
# ``mmd_tools.ui.qt_compat`` は PySide6 → PySide2 の順に import を試みる。
# CI の純Python 環境にはどちらも入っていない場合があるため、``qt_compat`` を
# import するだけで ``ModuleNotFoundError`` になる。
#
# presenter の純粋ロジックを検証するには Qt の実体は不要なので、最小限の
# PySide6 スタブを ``sys.modules`` に登録して ``qt_compat`` の import を通す。
# ``QObject`` だけはサブクラス化 + ``super().__init__()`` 呼び出しに耐える
# 実クラスにする (presenter が ``class X(QObject)`` で継承するため)。


class _StubQObject:
    """サブクラス化できる最小限の QObject 代替。"""

    def __init__(self, *args, **kwargs):
        # 親 (object) には引数を渡さない
        super().__init__()


class _StubSignal:
    """Signal() 呼び出しに耐えるダミー。connect/emit は no-op。"""

    def __init__(self, *args, **kwargs):
        pass

    def connect(self, *_args, **_kwargs):
        pass

    def emit(self, *_args, **_kwargs):
        pass


# qt_compat が QtCore/QtGui/QtWidgets から名前付きで import する識別子。
# QObject/Signal 以外は「呼べる/継承できるダミークラス」で十分。
_QTCORE_NAMES = ["Qt", "QSettings", "QTimer"]
_QTGUI_NAMES = [
    "QAction", "QDoubleValidator", "QColor", "QTextCursor", "QTextCharFormat",
]
_QTWIDGETS_NAMES = [
    "QApplication", "QMainWindow", "QTabWidget", "QDockWidget", "QPushButton",
    "QLineEdit", "QWidget", "QVBoxLayout", "QHBoxLayout", "QLabel", "QTextEdit", "QDialog",
    "QFileDialog", "QGroupBox", "QFormLayout", "QCheckBox", "QComboBox",
    "QListWidget", "QSlider", "QTreeView", "QTreeWidget", "QTreeWidgetItem",
    "QColorDialog", "QDoubleSpinBox", "QSpinBox", "QGridLayout", "QScrollArea",
    "QListWidgetItem", "QStatusBar", "QProgressBar", "QSplitter", "QTableWidget",
    "QTableWidgetItem", "QHeaderView", "QMessageBox", "QInputDialog", "QToolBar",
    "QMenuBar", "QMenu",
]


def _make_stub_qclass(name):
    """継承・インスタンス化可能なダミー Qt クラスを生成する。"""
    return type(name, (object,), {"__init__": lambda self, *a, **k: None})


class _StubQt:
    """Qt namespace stub with minimal constants for headless tests."""
    UserRole = 256
    ItemDataRole = type("ItemDataRole", (), {"UserRole": 256})()
    AlignLeft = 1
    AlignRight = 2
    AlignCenter = 4
    Horizontal = 1
    Vertical = 2


class _StubQListWidgetItem:
    """QListWidgetItem stub with setData/data support."""

    def __init__(self, *args, **kwargs):
        self._role_data: dict = {}
        self._text = args[0] if args else ""

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setData(self, role, value):
        self._role_data[role] = value

    def data(self, role):
        return self._role_data.get(role)

    def setHidden(self, hidden):
        pass

    def isHidden(self):
        return False


def _qt_already_available() -> bool:
    """本物の PySide6/PySide2 が import 可能かを判定する。"""
    for mod in ("PySide6", "PySide2"):
        if mod in sys.modules:
            return True
    import importlib.util

    for mod in ("PySide6", "PySide2"):
        try:
            if importlib.util.find_spec(mod) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def install_qt_stub() -> bool:
    """PySide6 をスタブとして ``sys.modules`` に登録する。

    本物の PySide6/PySide2 が利用可能な場合は何もせず False を返す
    (実 Qt 環境を汚染しない)。

    Returns:
        スタブを新規登録した場合 True、本物の Qt が既にあれば False。
    """
    if _qt_already_available():
        return False

    pyside6 = ModuleType("PySide6")

    qtcore = ModuleType("PySide6.QtCore")
    qtcore.QObject = _StubQObject
    qtcore.Signal = _StubSignal
    for n in _QTCORE_NAMES:
        setattr(qtcore, n, _make_stub_qclass(n))
    qtcore.Qt = _StubQt  # override with constant-bearing version

    qtgui = ModuleType("PySide6.QtGui")
    for n in _QTGUI_NAMES:
        setattr(qtgui, n, _make_stub_qclass(n))

    qtwidgets = ModuleType("PySide6.QtWidgets")
    for n in _QTWIDGETS_NAMES:
        setattr(qtwidgets, n, _make_stub_qclass(n))
    qtwidgets.QListWidgetItem = _StubQListWidgetItem  # override with data-aware version

    shiboken6 = ModuleType("shiboken6")
    shiboken6.wrapInstance = MagicMock(name="shiboken6.wrapInstance")

    pyside6.QtCore = qtcore
    pyside6.QtGui = qtgui
    pyside6.QtWidgets = qtwidgets

    sys.modules["PySide6"] = pyside6
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6.QtGui"] = qtgui
    sys.modules["PySide6.QtWidgets"] = qtwidgets
    sys.modules["shiboken6"] = shiboken6
    return True


def install_headless_ui_stubs() -> None:
    """Maya + Qt をまとめてスタブ化する (presenter 等の純Python テスト用)。"""
    install_maya_stub(profile="headless")
    install_qt_stub()


# ----------------------------------------------------------------------
# om.MDoubleArray スタブ
# ----------------------------------------------------------------------
#
# MagicMock では ``len()`` が Mock を返し、数値比較が成立しない。
# ``_append_bone_locals_to_channel_arrays`` などのように MDoubleArray に
# append / len / iter を使うロジックを純Python で検証するには
# list ベースの実装スタブが必要になる。


class _MDoubleArray:
    """``om.MDoubleArray`` の最小限スタブ。純Python テスト専用。

    - append(value): float に変換して追加
    - __len__ / __iter__ / __getitem__: list 委譲
    """

    def __init__(self):
        self._data: list = []

    def append(self, value: float) -> None:
        self._data.append(float(value))

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __getitem__(self, idx):
        return self._data[idx]

    def __repr__(self) -> str:
        return f"_MDoubleArray({self._data!r})"


def install_om_double_array_stub() -> None:
    """``om.MDoubleArray`` を実用的なスタブクラスに差し替える。

    ``install_maya_stub()`` の呼び出し後に呼ぶこと。
    ``maya.api.OpenMaya`` モジュール (MagicMock) の ``MDoubleArray`` 属性を
    ``_MDoubleArray`` 実クラスで上書きする。これにより、テスト対象コードが
    ``om.MDoubleArray()`` を呼ぶと list ベースのスタブインスタンスが返る。
    本物の maya が存在する場合は何もしない。

    他のテストが先に ``maya.api.OpenMaya`` を ``sys.modules`` に登録していた場合、
    ``vmd_converter`` がそのオブジェクトを ``om`` として既にバインドしている可能性
    がある。その場合は ``sys.modules`` の差し替えだけでは不十分なため、
    ``vmd_converter`` モジュール内の ``om`` 参照に対しても直接パッチする。
    """
    if _is_real_maya_present():
        return
    om = sys.modules.get("maya.api.OpenMaya")
    if om is not None:
        om.MDoubleArray = _MDoubleArray
    # vmd_converter が先行インポート済みで別の om オブジェクトを保持している場合
    vmd_mod = sys.modules.get("mmd_tools.converters.vmd_converter")
    if vmd_mod is not None:
        bound_om = getattr(vmd_mod, "om", None)
        if bound_om is not None and bound_om is not om:
            bound_om.MDoubleArray = _MDoubleArray
