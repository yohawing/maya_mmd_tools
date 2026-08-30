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
from mmd_tools.ui.presenters.bone_presenter import BonePresenter  # noqa: E402
from mmd_tools.ui.presenters.display_pane_presenter import DisplayPanePresenter  # noqa: E402
from mmd_tools.ui.presenters.info_presenter import InfoPresenter  # noqa: E402
from mmd_tools.ui.presenters.material_presenter import MaterialPresenter  # noqa: E402
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter  # noqa: E402
from mmd_tools.ui.presenters.physics_presenter import PhysicsPresenter  # noqa: E402


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


def test_display_frames_refresh_when_their_main_tab_activates():
    display_pane_tab = object()
    other_tab = object()
    presenter = Mock()
    window = type(
        "Window",
        (),
        {
            "display_pane_tab": display_pane_tab,
            "display_pane_presenter": presenter,
            "tab_widget": _Tabs([other_tab, display_pane_tab]),
        },
    )()

    MainWindow._on_main_tab_changed(window, 0)
    presenter.refresh.assert_not_called()

    MainWindow._on_main_tab_changed(window, 1)
    presenter.refresh.assert_called_once_with()


def test_real_presenters_consume_initial_generation_once_across_tab_reactivation():
    """A real presenter owns its first generation load and then stays idle."""
    cases = (
        (InfoPresenter, "info_presenter", "load_model_info", {"_undo_chunk_open": False, "_edit_session_root": None}),
        (MaterialPresenter, "material_presenter", "load_materials", {"has_unsaved_changes": False}),
        (
            BonePresenter,
            "bone_presenter",
            "load_bones",
            {"_reindex_dirty": False, "_reset_plan": None, "current_bone": None, "bone_data": {}},
        ),
        (
            MorphPresenter,
            "morph_presenter",
            "load_morphs",
            {
                "_authoring_spec": None,
                "_authoring_spec_baseline": None,
                "current_morph": None,
                "_morph_edit_baseline": None,
                "material_morph_work": None,
            },
        ),
        (DisplayPanePresenter, "display_pane_presenter", "refresh", {"frames": [], "_original_frames": []}),
        (PhysicsPresenter, "physics_presenter", "refresh_physics", {"_form_dirty": False}),
    )
    for presenter_cls, presenter_attr, load_name, state in cases:
        tab = object()
        presenter = presenter_cls.__new__(presenter_cls)
        presenter.view = tab
        presenter.app_state = type("AppState", (), {"refresh_generation": 3})()
        presenter._pending_refresh_generation = None
        presenter._last_refresh_generation = None
        for name, value in state.items():
            setattr(presenter, name, value)
        loader = Mock()
        setattr(presenter, load_name, loader)
        window = type(
            "Window",
            (),
            {
                "app_state": presenter.app_state,
                presenter_attr: presenter,
                "tab_widget": _Tabs([object(), tab]),
                "physics_tab": tab if presenter_attr == "physics_presenter" else None,
                "morph_tab": tab if presenter_attr == "morph_presenter" else None,
                "display_pane_tab": tab if presenter_attr == "display_pane_presenter" else None,
            },
        )()

        MainWindow._on_main_tab_changed(window, 0)
        MainWindow._on_main_tab_changed(window, 1)
        MainWindow._on_main_tab_changed(window, 0)
        MainWindow._on_main_tab_changed(window, 1)

        if load_name == "refresh_physics":
            loader.assert_called_once_with(force=True)
        else:
            loader.assert_called_once_with()


def test_development_visibility_refresh_keeps_always_present_physics_tab():
    physics_tab = object()
    physics_presenter = object()
    import_export_tab = Mock()
    tabs = [physics_tab]
    window = type(
        "Window",
        (),
        {
            "import_export_tab": import_export_tab,
            "physics_tab": physics_tab,
            "physics_presenter": physics_presenter,
            "tab_widget": _Tabs([physics_tab]),
            "tabs": tabs,
        },
    )()

    with patch("mmd_tools.plugin_main.install_mmd_menu") as install_menu:
        MainWindow.refresh_development_mode_visibility(window)

    import_export_tab._apply_dev_mode_visibility.assert_called_once_with()
    install_menu.assert_called_once_with()
    assert window.physics_tab is physics_tab
    assert window.physics_presenter is physics_presenter
    assert window.tab_widget.widgets == [physics_tab]
    assert window.tabs is tabs
    assert window.tabs == [physics_tab]


def test_retranslate_rebuilds_locale_dependent_model_labels():
    presenters = [Mock() for _ in range(5)]
    window = type(
        "Window",
        (),
        {
            "import_export_tab": object(),
            "export_tab": object(),
            "info_presenter": presenters[0],
            "material_presenter": presenters[1],
            "bone_presenter": presenters[2],
            "morph_presenter": presenters[3],
            "morph_tab": object(),
            "display_pane_presenter": presenters[4],
            "display_pane_tab": object(),
            "physics_tab": object(),
            "settings_presenter": Mock(),
            "tabs": [],
            "tab_widget": _Tabs([]),
            "header_widget": Mock(),
            "retranslateUi": Mock(),
            "app_state": Mock(),
        },
    )()

    MainWindow.retranslate_all_tabs(window)

    window.header_widget.retranslateUi.assert_called_once_with()
    window.app_state.refresh_model_list.assert_called_once_with(explicit=True)
