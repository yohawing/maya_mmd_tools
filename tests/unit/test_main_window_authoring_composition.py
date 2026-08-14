"""Focused tests for optional authoring composition at Maya UI startup."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from mmd_tools.adapters import maya_authoring_factory
from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub

install_maya_stub()
install_headless_ui_stubs()
open_maya_ui = ModuleType("maya.OpenMayaUI")
open_maya_ui.MQtUtil = Mock()
sys.modules["maya.OpenMayaUI"] = open_maya_ui

from mmd_tools.ui import main_window as main_window_module  # noqa: E402
from mmd_tools.ui import translations  # noqa: E402
from mmd_tools.ui.main_window import MainWindow  # noqa: E402


def test_main_window_factory_failure_keeps_startup_path_available(monkeypatch, caplog) -> None:
    def fail(_cmds):
        raise RuntimeError("composition failed")

    monkeypatch.setattr(maya_authoring_factory, "build_maya_authoring_composition", fail)
    assert MainWindow._create_authoring_composition() is None
    assert "authoring controls are disabled" in caplog.text


def test_main_window_presenters_receive_same_composition_dependencies() -> None:
    adapter = object()
    coordinator = object()
    scale_resolver = object()
    create_model_action = object()
    window = SimpleNamespace(
        authoring_composition=SimpleNamespace(
            cmds_adapter=adapter,
            coordinator=coordinator,
            model_scale_resolver=scale_resolver,
            create_model_action=create_model_action,
        )
    )

    assert MainWindow._authoring_presenter_kwargs(window) == {
        "maya_adapter": adapter,
        "authoring_coordinator": coordinator,
    }
    assert MainWindow._create_model_action(window) is create_model_action


def test_main_window_disables_authoring_when_composition_is_absent() -> None:
    window = SimpleNamespace(authoring_composition=None)
    assert MainWindow._authoring_presenter_kwargs(window) == {"authoring_coordinator": None}
    assert MainWindow._create_model_action(window) is None


def test_setup_tabs_injects_only_create_model_action_into_file_presenter(monkeypatch) -> None:
    create_action = object()
    calls = []

    class Tab:
        def __init__(self, *_args, **_kwargs):
            pass

    class Presenter:
        def __init__(self, *args, **kwargs):
            calls.append((type(self).__name__, args, kwargs))

    class FilePresenter(Presenter):
        pass

    class DisplayPresenter(Presenter):
        pass

    class Translator:
        def set_language(self, _language):
            pass

        def translate(self, key, _section):
            return key

    translator = Translator()
    monkeypatch.setattr(translations.UITranslator, "instance", lambda: translator)
    for name in (
        "ImportExportTab",
        "ExportTab",
        "InfoTab",
        "MaterialTab",
        "BoneTab",
        "MorphTab",
        "DisplayPaneTab",
        "SettingsTab",
    ):
        monkeypatch.setattr(main_window_module, name, Tab)
    monkeypatch.setattr(main_window_module, "ImportExportPresenter", FilePresenter)
    for name in (
        "ExportPresenter",
        "InfoPresenter",
        "MaterialPresenter",
        "BonePresenter",
        "MorphPresenter",
        "SettingsPresenter",
    ):
        monkeypatch.setattr(main_window_module, name, Presenter)
    monkeypatch.setattr(main_window_module, "DisplayPanePresenter", DisplayPresenter)

    class Window:
        app_state = object()
        settings_service = SimpleNamespace(get=lambda *_args: "ja")
        tab_widget = SimpleNamespace(addTab=lambda *_args: None)

        def _create_model_action(self):
            return create_action

        def _authoring_presenter_kwargs(self):
            return {"authoring_coordinator": None}

        def _add_physics_tab(self):
            self.physics_tab = Tab()

    MainWindow.setup_tabs(Window())

    file_call = next(call for call in calls if call[0] == "FilePresenter")
    assert file_call[2] == {"create_model_action": create_action}
    display_call = next(call for call in calls if call[0] == "DisplayPresenter")
    assert display_call[2] == {"authoring_coordinator": None}
