"""Physics main-tab activation refresh wiring tests."""

import sys
from types import ModuleType
from unittest.mock import Mock, patch

from tests.common.maya_stub import install_headless_ui_stubs, install_maya_stub

install_maya_stub()
install_headless_ui_stubs()
open_maya_ui = ModuleType("maya.OpenMayaUI")
open_maya_ui.MQtUtil = Mock()
sys.modules["maya.OpenMayaUI"] = open_maya_ui

from mmd_tools.ui.main_window import MainWindow  # noqa: E402


class _Tabs:
    def __init__(self, widgets):
        self.widgets = widgets

    def widget(self, index):
        return self.widgets[index]

    def indexOf(self, widget):
        try:
            return self.widgets.index(widget)
        except ValueError:
            return -1

    def count(self):
        return len(self.widgets)

    def insertTab(self, index, widget, _label):
        self.widgets.insert(index, widget)

    def removeTab(self, index):
        self.widgets.pop(index)


def test_physics_refreshes_only_when_its_main_tab_activates():
    physics_tab = object()
    other_tab = object()
    presenter = Mock()
    window = type(
        "Window",
        (),
        {
            "physics_tab": physics_tab,
            "physics_presenter": presenter,
            "tab_widget": _Tabs([other_tab, physics_tab]),
        },
    )()

    MainWindow._on_main_tab_changed(window, 0)
    presenter.refresh_physics.assert_not_called()

    MainWindow._on_main_tab_changed(window, 1)
    presenter.refresh_physics.assert_called_once_with()


def test_morphs_load_when_their_main_tab_activates():
    morph_tab = object()
    other_tab = object()
    presenter = Mock()
    window = type(
        "Window",
        (),
        {
            "morph_tab": morph_tab,
            "morph_presenter": presenter,
            "tab_widget": _Tabs([other_tab, morph_tab]),
        },
    )()

    MainWindow._on_main_tab_changed(window, 0)
    presenter.ensure_morphs_loaded.assert_not_called()

    MainWindow._on_main_tab_changed(window, 1)
    presenter.ensure_morphs_loaded.assert_called_once_with()


def test_development_visibility_refresh_hides_existing_physics_tab_in_normal_mode():
    physics_tab = object()
    physics_presenter = object()
    import_export_tab = Mock()
    window = type(
        "Window",
        (),
        {
            "import_export_tab": import_export_tab,
            "physics_tab": physics_tab,
            "physics_presenter": physics_presenter,
            "tab_widget": _Tabs([physics_tab]),
            "tabs": [physics_tab],
            "settings_service": Mock(is_development_mode=Mock(return_value=False)),
        },
    )()

    with patch("mmd_tools.plugin_main.install_mmd_menu") as install_menu:
        MainWindow.refresh_development_mode_visibility(window)

    import_export_tab._apply_dev_mode_visibility.assert_called_once_with()
    install_menu.assert_called_once_with()
    assert window.physics_tab is physics_tab
    assert window.physics_presenter is physics_presenter
    assert window.tab_widget.indexOf(physics_tab) == -1
    assert physics_tab not in window.tabs


def test_development_visibility_refresh_shows_existing_physics_tab_in_dev_mode():
    physics_tab = object()
    import_export_tab = Mock()
    window = type(
        "Window",
        (),
        {
            "import_export_tab": import_export_tab,
            "physics_tab": physics_tab,
            "physics_presenter": object(),
            "tab_widget": _Tabs([]),
            "tabs": [],
            "settings_service": Mock(is_development_mode=Mock(return_value=True)),
        },
    )()

    with patch("mmd_tools.plugin_main.install_mmd_menu"):
        MainWindow.refresh_development_mode_visibility(window)

    assert window.tab_widget.indexOf(physics_tab) == 0
    assert physics_tab in window.tabs
