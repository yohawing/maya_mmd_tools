"""Focused tests for the standalone name-translation dialog adapter."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mmd_tools.tools.translate_names import NameEntry, NameTranslationError
from mmd_tools.ui import name_translation_dialog


def _entry(kind, node, source, english="", index=None, rename_allowed=True):
    return NameEntry(
        kind=kind,
        node=node,
        source_name=source,
        english_name=english,
        english_attr=f"mmd_{kind}_name_en",
        index=index,
        rename_allowed=rename_allowed,
    )


def test_format_dialog_preview_includes_kind_source_and_destinations():
    plan = [
        SimpleNamespace(
            entry=_entry("bone", "|root|joint", "左腕", index=2),
            english_name="left arm",
            maya_name="left_arm",
        )
    ]

    assert name_translation_dialog.format_dialog_preview(plan) == (
        "bone[2]: source='左腕'; node=|root|joint; EnglishName='left arm'; rename='left_arm'",
    )


def test_build_translation_preview_is_read_only_and_uses_core_policy(monkeypatch, tmp_path):
    dictionary = tmp_path / "names.csv"
    dictionary.write_text("左腕,left arm\n", encoding="utf-8")
    entries = (_entry("bone", "|root|joint", "左腕"),)
    cmds = SimpleNamespace(ls=lambda **_kwargs: ["|root", "|root|joint"])

    monkeypatch.setattr(name_translation_dialog, "resolve_model_root", lambda *_a, **_k: "|root")
    monkeypatch.setattr(name_translation_dialog, "collect_name_entries", lambda *_a, **_k: entries)

    root, plan = name_translation_dialog.build_translation_preview(str(dictionary), cmds_module=cmds)

    assert root == "|root"
    assert len(plan) == 1
    assert plan[0].english_name == "left arm"
    assert not hasattr(cmds, "setAttr")
    assert not hasattr(cmds, "rename")


def test_validate_preview_targets_rejects_stale_nodes():
    cmds = SimpleNamespace(
        objExists=lambda _node: False,
        ls=lambda *_args, **_kwargs: [],
        attributeQuery=lambda *_args, **_kwargs: False,
    )
    plan = [
        SimpleNamespace(
            entry=_entry("bone", "|root|deleted", "左腕"),
            english_name="left arm",
            maya_name=None,
        )
    ]

    with pytest.raises(NameTranslationError, match="stale translation target"):
        name_translation_dialog._validate_preview_targets(plan, cmds_module=cmds)


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in self._callbacks:
            callback(*args)


class _Widget:
    def __init__(self, *_args, **_kwargs):
        self.clicked = _Signal()
        self.textChanged = _Signal()
        self.stateChanged = _Signal()
        self._text = ""
        self._enabled = True
        self._checked = False
        self._plain_text = ""

    def setObjectName(self, value):
        self.object_name = value

    def setWindowTitle(self, value):
        self.window_title = value

    def setModal(self, value):
        self.modal = value

    def setEnabled(self, value):
        self._enabled = bool(value)

    def isEnabled(self):
        return self._enabled

    def setDefault(self, _value):
        pass

    def setText(self, value):
        self._text = str(value)
        self.textChanged.emit(self._text)

    def text(self):
        return self._text

    def setReadOnly(self, _value):
        pass

    def setMinimumHeight(self, _value):
        pass

    def setPlainText(self, value):
        self._plain_text = str(value)

    def toPlainText(self):
        return self._plain_text

    def clear(self):
        self._plain_text = ""

    def setChecked(self, value):
        self._checked = bool(value)
        self.stateChanged.emit(int(self._checked))

    def isChecked(self):
        return self._checked

    def addWidget(self, _widget):
        pass

    def addLayout(self, _layout):
        pass

    def addStretch(self):
        pass

    def addRow(self, *_args):
        pass


class _FakeDialog(_Widget):
    def accept(self):
        self.accepted = True

    def reject(self):
        self.rejected = True

    def exec(self):
        return 1 if getattr(self, "accepted", False) else 0


def _install_dialog_qt_stub(monkeypatch):
    qt = SimpleNamespace(
        QCheckBox=_Widget,
        QDialog=_FakeDialog,
        QFileDialog=SimpleNamespace(getOpenFileName=staticmethod(lambda *_args: ("", ""))),
        QFormLayout=_Widget,
        QHBoxLayout=_Widget,
        QLabel=_Widget,
        QLineEdit=_Widget,
        QMessageBox=SimpleNamespace(warning=staticmethod(lambda *_args: None)),
        QPushButton=_Widget,
        QTextEdit=_Widget,
        QVBoxLayout=_Widget,
    )
    monkeypatch.setitem(sys.modules, "mmd_tools.ui.qt_compat", qt)


def test_dialog_requires_preview_before_apply_and_cancel_is_read_only(monkeypatch):
    _install_dialog_qt_stub(monkeypatch)
    entry = _entry("bone", "|root|joint", "左腕")
    change = name_translation_dialog.NameChange(entry, "left arm", "left arm", None)
    state = {"uuid": "uuid-1", "source": "左腕", "english": ""}

    def ls(node, **kwargs):
        return [state["uuid"]] if kwargs.get("uuid") else [node]

    def get_attr(path):
        return state["source"] if path.endswith("mmd_bone_name") else state["english"]

    cmds = SimpleNamespace(
        objExists=lambda _node: True,
        ls=ls,
        attributeQuery=lambda *_args, **_kwargs: True,
        getAttr=get_attr,
    )
    monkeypatch.setattr(name_translation_dialog, "resolve_model_root", lambda *_a, **_k: "|root")
    monkeypatch.setattr(
        name_translation_dialog,
        "build_translation_preview",
        lambda *_a, **_k: ("|root", (change,)),
    )
    apply_plan = MagicMock(return_value=(change,))
    monkeypatch.setattr(name_translation_dialog, "apply_translation_plan", apply_plan)

    dialog = name_translation_dialog.NameTranslationDialog(cmds_module=cmds)
    assert not dialog.apply_button.isEnabled()
    assert dialog.apply() is None
    assert not apply_plan.called

    dialog.dictionary_edit.setText("names.csv")
    assert dialog.preview() == (change,)
    assert dialog.apply_button.isEnabled()
    dialog.cancel_button.clicked.emit()
    assert not apply_plan.called

    dialog = name_translation_dialog.NameTranslationDialog(cmds_module=cmds)
    dialog.dictionary_edit.setText("names.csv")
    dialog.preview()
    assert dialog.apply() == (change,)
    apply_plan.assert_called_once_with((change,), cmds_module=cmds)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("english", "changed by another operation", "stale EnglishName"),
        ("source", "別の原名", "stale original MMD name"),
        ("uuid", "uuid-replaced", "stale translation target identity"),
    ),
)
def test_dialog_rejects_stale_preview_before_apply(monkeypatch, field, value, message):
    _install_dialog_qt_stub(monkeypatch)
    entry = _entry("bone", "|root|joint", "左腕")
    change = name_translation_dialog.NameChange(entry, "left arm", "left arm", None)
    state = {"uuid": "uuid-1", "source": "左腕", "english": ""}

    def ls(node, **kwargs):
        return [state["uuid"]] if kwargs.get("uuid") else [node]

    def get_attr(path):
        return state["source"] if path.endswith("mmd_bone_name") else state["english"]

    cmds = SimpleNamespace(
        objExists=lambda _node: True,
        ls=ls,
        attributeQuery=lambda *_args, **_kwargs: True,
        getAttr=get_attr,
    )
    monkeypatch.setattr(name_translation_dialog, "resolve_model_root", lambda *_a, **_k: "|root")
    monkeypatch.setattr(
        name_translation_dialog,
        "build_translation_preview",
        lambda *_a, **_k: ("|root", (change,)),
    )
    apply_plan = MagicMock(return_value=(change,))
    monkeypatch.setattr(name_translation_dialog, "apply_translation_plan", apply_plan)

    dialog = name_translation_dialog.NameTranslationDialog(cmds_module=cmds)
    dialog.dictionary_edit.setText("names.csv")
    assert dialog.preview() == (change,)
    state[field] = value

    assert dialog.apply() is None
    apply_plan.assert_not_called()
    assert message in dialog.status_label.text()
