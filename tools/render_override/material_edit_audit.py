"""Interactive Maya GUI audit helpers; load inside an isolated test Maya.

Call install(window, output_directory) after importing a disposable PMX scene.
All edit actions below use Qt input events. Snapshots read scene state only.
"""

import json
from pathlib import Path

from maya import cmds, OpenMayaUI

try:
    from PySide6 import QtCore, QtTest, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtTest, QtWidgets
    from shiboken2 import wrapInstance


class MaterialEditAudit:
    def __init__(self, window, output):
        self.window = window
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.messages = []
        window.app_state.status_message.connect(self.messages.append)

    def widget(self, name):
        result = self.window.findChild(QtWidgets.QWidget, name)
        if result is None:
            raise ValueError(name)
        return result

    def click(self, target):
        if isinstance(target, str):
            target = self.widget(target)
        parent = target.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                parent.ensureWidgetVisible(target)
                break
            parent = parent.parentWidget()
        QtTest.QTest.mouseClick(target, QtCore.Qt.LeftButton,
                               QtCore.Qt.NoModifier,
                               QtCore.QPoint(8, target.height() // 2)
                               if isinstance(target, QtWidgets.QCheckBox)
                               else target.rect().center())

    def text(self, name, value):
        target = self.widget(name)
        self.click(target)
        target.setFocus()
        QtTest.QTest.keyClick(target, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
        QtTest.QTest.keyClicks(target, str(value))
        QtTest.QTest.keyClick(target, QtCore.Qt.Key_Tab)

    def combo(self, name, index):
        target = self.widget(name)
        self.click(target)
        QtTest.QTest.keyClick(target, QtCore.Qt.Key_Home)
        for _ in range(index):
            QtTest.QTest.keyClick(target, QtCore.Qt.Key_Down)
        QtTest.QTest.keyClick(target, QtCore.Qt.Key_Return)

    def select(self, binding):
        self.text("materialSearchEdit", "")
        target = self.widget("materialList")
        for row in range(target.count()):
            item = target.item(row)
            if item.data(QtCore.Qt.UserRole) == binding:
                target.scrollToItem(item)
                QtTest.QTest.mouseClick(target.viewport(), QtCore.Qt.LeftButton,
                                       QtCore.Qt.NoModifier,
                                       target.visualItemRect(item).center())
                return
        raise ValueError(binding)

    def history(self, redo=False):
        window = wrapInstance(int(OpenMayaUI.MQtUtil.mainWindow()), QtWidgets.QWidget)
        QtTest.QTest.keyClick(window, QtCore.Qt.Key_Y if redo else QtCore.Qt.Key_Z,
                             QtCore.Qt.ControlModifier)

    def color(self, swatch, value):
        """Edit the real color dialog, accounting for Qt5's unnamed HTML field."""
        def fill():
            dialog = QtWidgets.QApplication.activeModalWidget()
            if dialog is None:
                raise RuntimeError("Color dialog did not open")
            edits = [w for w in dialog.findChildren(QtWidgets.QLineEdit)
                     if w.text().startswith("#")]
            if len(edits) != 1:
                QtTest.QTest.keyClick(dialog, QtCore.Qt.Key_Escape)
                raise RuntimeError("Could not identify color HTML field")
            edit = edits[0]
            QtTest.QTest.mouseClick(edit, QtCore.Qt.LeftButton)
            QtTest.QTest.keyClick(edit, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier)
            QtTest.QTest.keyClicks(edit, value)
            QtTest.QTest.keyClick(edit, QtCore.Qt.Key_Tab)
            button = dialog.findChild(QtWidgets.QDialogButtonBox).button(
                QtWidgets.QDialogButtonBox.Ok)
            QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        QtCore.QTimer.singleShot(500, fill)
        self.click(swatch)

    def capture(self, label):
        self.window.grab().save(str(self.output / (label + ".png")))
        scene = {}
        for plug in cmds.ls("*.mmd_material_index") or []:
            node = plug.rsplit(".", 1)[0]
            attrs = {}
            for attr in cmds.listAttr(node, userDefined=True) or []:
                try:
                    attrs[attr] = cmds.getAttr(node + "." + attr)
                except RuntimeError:
                    pass
            scene[node] = attrs
        controls = {}
        for widget in self.window.findChildren(QtWidgets.QWidget):
            if widget.objectName().startswith("material"):
                data = {"enabled": widget.isEnabled(), "visible": widget.isVisible()}
                for method in ("value", "text", "currentIndex", "isChecked"):
                    if hasattr(widget, method):
                        data[method] = getattr(widget, method)()
                controls[widget.objectName()] = data
        materials = self.widget("materialList")
        rows = [materials.item(i).text() for i in range(materials.count())]
        presenter = self.window.material_presenter
        data = {"scene": scene, "controls": controls, "rows": rows,
                "current": presenter.current_material,
                "current_index": presenter.current_material_index,
                "messages": self.messages[:], "undo": cmds.undoInfo(q=True, undoName=True),
                "redo": cmds.undoInfo(q=True, redoName=True)}
        data["file_nodes"] = {
            node: cmds.getAttr(node + ".fileTextureName")
            for node in cmds.ls(type="file") or []
        }
        data["shader_connections"] = {
            node: sorted(cmds.listConnections(node, connections=True, plugs=True) or [])
            for node in scene
        }
        (self.output / (label + ".json")).write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def install(window, output):
    return MaterialEditAudit(window, output)
