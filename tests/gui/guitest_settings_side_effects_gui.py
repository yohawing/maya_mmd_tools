"""Production Settings-tab side-effect smoke tests.

These checks deliberately drive the real Qt controls on ``MainWindow``.  They
are intended to run inside the isolated Maya GUI process launched by
``tests/run_gui_tests.py``; no presenter method is used as a substitute for a
widget signal.
"""

import copy
import json
import logging
import socket
import time
import unittest

import maya.cmds as cmds

from mmd_tools.core import settings_keys
from mmd_tools.core import logger as logger_module
from mmd_tools.core.logger import get_logger, set_all_logger_levels
from mmd_tools.core.settings import get_settings
from mmd_tools.services.settings_service import SettingsService
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QSettings, QT_BINDING, Qt
from mmd_tools.ui.translations import UITranslator
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import QtSignalInvocationSpy, build_surface_witness

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


_SETTING_KEYS = (
    settings_keys.UI_GENERAL_DEVELOPMENT_MODE,
    settings_keys.UI_GENERAL_LANGUAGE,
    settings_keys.UI_DEV_COMMAND_PORT,
    settings_keys.LOGGING_ENABLED,
    settings_keys.LOGGING_LEVEL,
    settings_keys.LOGGING_LOG_FILE_PATH,
)


class _RecordingHandler(logging.Handler):
    """Small in-memory handler used to prove cached logger filtering."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _option_var_name(key_path):
    """Return the flattened Maya optionVar name used by ``Settings``."""

    return f"mmd_tools_{key_path.replace('.', '::')}"


def _snapshot_option_vars():
    snapshot = {}
    for key_path in _SETTING_KEYS:
        name = _option_var_name(key_path)
        existed = bool(cmds.optionVar(exists=name))
        snapshot[name] = {
            "existed": existed,
            "value": cmds.optionVar(query=name) if existed else None,
        }
    return snapshot


def _restore_option_vars(snapshot):
    for name, entry in snapshot.items():
        if cmds.optionVar(exists=name):
            cmds.optionVar(remove=name)
        if not entry["existed"]:
            continue
        value = entry["value"]
        if isinstance(value, bool):
            cmds.optionVar(intValue=(name, int(value)))
        elif isinstance(value, int):
            cmds.optionVar(intValue=(name, value))
        elif isinstance(value, float):
            cmds.optionVar(floatValue=(name, value))
        else:
            cmds.optionVar(stringValue=(name, str(value)))


def _free_local_port():
    """Reserve an ephemeral local TCP port long enough to select it in Maya."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _port_is_open(port):
    """Query a Maya commandPort without turning a closed port into an error."""

    try:
        return bool(cmds.commandPort(f":{port}", query=True))
    except Exception:
        return False


def _emit_witness(
    surface_id,
    locator_key,
    locator,
    interaction,
    oracle,
    action_spy,
    control,
    interaction_control=None,
):
    """Emit one deterministic runtime witness for the coverage gate."""

    evidence = build_surface_witness(
        surface_id=surface_id,
        case_id="gui.settings_side_effects",
        interaction=interaction,
        oracle=oracle,
        action_spy=action_spy,
        control=control,
        interaction_control=interaction_control,
        **{locator_key: locator},
    )
    print(
        "[UI COVERAGE WITNESS] "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


def _drain_events_until(predicate, timeout=2.0):
    """Process Qt events until *predicate* is true or a bounded timeout elapses."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
    QApplication.processEvents()
    return bool(predicate())


@requires_gui
class TestSettingsSideEffects(GuiTestBase):
    """Exercise Settings controls through their production signal wiring."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self._settings_store = get_settings()
        self._settings_data = copy.deepcopy(self._settings_store.data)
        self._option_vars = _snapshot_option_vars()
        self._translator = UITranslator.instance()
        self._language = self._translator.get_language()
        self._cached_logger_levels = {
            name: logger._logger.level
            for name, logger in logger_module._loggers.items()
        }
        self._qt_settings = QSettings("yohawing", "maya_mmd_tools")
        self._qt_geometry = self._qt_settings.value("geometry")
        self._qt_window_state = self._qt_settings.value("windowState")
        self._owned_ports = set()
        self.window = None

        # Start in a deterministic, non-dev state.  The original optionVars
        # and in-memory store are restored in tearDown.
        self._settings_store.data = copy.deepcopy(self._settings_data)
        self._settings_store.set(settings_keys.UI_GENERAL_DEVELOPMENT_MODE, False)
        self.window = MainWindow()
        self.window.show()
        QApplication.processEvents()

    def tearDown(self):
        try:
            self._close_owned_ports()
            if self.window is not None:
                self.window.close()
                self.window.deleteLater()
            QApplication.processEvents()
        finally:
            _restore_option_vars(self._option_vars)
            self._settings_store.data = copy.deepcopy(self._settings_data)
            self._translator.set_language(self._language)
            original_level_name = self._settings_store.get(settings_keys.LOGGING_LEVEL, "WARNING")
            original_level = getattr(logging, str(original_level_name).upper(), logging.WARNING)
            set_all_logger_levels(original_level)
            for name, level in self._cached_logger_levels.items():
                cached = logger_module._loggers.get(name)
                if cached is not None:
                    cached.set_level(level)
            if self._qt_geometry is None:
                self._qt_settings.remove("geometry")
            else:
                self._qt_settings.setValue("geometry", self._qt_geometry)
            if self._qt_window_state is None:
                self._qt_settings.remove("windowState")
            else:
                self._qt_settings.setValue("windowState", self._qt_window_state)
            super().tearDown()

    def _close_owned_ports(self):
        for port in tuple(self._owned_ports):
            port_name = f":{port}"
            try:
                if cmds.commandPort(port_name, query=True):
                    cmds.commandPort(name=port_name, close=True)
            except Exception:
                # Cleanup must not mask the assertion that discovered a
                # commandPort failure; the owning Maya process is isolated by
                # the GUI runner and will be closed after this test command.
                pass
            finally:
                self._owned_ports.discard(port)

    @staticmethod
    def _select_language(combo, language):
        index = combo.findData(language)
        if index < 0:
            raise AssertionError(f"language {language!r} is not available in Settings combo")
        combo.setCurrentIndex(index)
        QApplication.processEvents()
        return index

    def test_settings_controls_apply_real_side_effects(self):
        settings_tab = self.window.settings_presenter.view
        try:
            self.window.tab_widget.setCurrentWidget(settings_tab)
            QApplication.processEvents()
            # Language combo signal: all nine tabs are checked by widget
            # identity, and Display Pane's child label must be retranslated.
            current_language = settings_tab.language_combo.currentData()
            target_language = "en" if current_language != "en" else "ja"
            language_spy = QtSignalInvocationSpy(
                "SettingsPresenter.set_language",
                settings_tab.language_combo.currentIndexChanged,
                source_control=settings_tab.language_combo,
            )
            self._select_language(settings_tab.language_combo, target_language)
            translator = UITranslator.instance()
            expected_tabs = (
                ("file_io", self.window.import_export_tab),
                ("export_workflow", self.window.export_tab),
                ("info", self.window.info_presenter.view),
                ("material", self.window.material_presenter.view),
                ("bone", self.window.bone_presenter.view),
                ("morph", self.window.morph_tab),
                ("display_pane", self.window.display_pane_tab),
                ("physics", self.window.physics_tab),
                ("settings", settings_tab),
            )
            self.assertEqual(self.window.tab_widget.count(), len(expected_tabs))
            for key, widget in expected_tabs:
                index = self.window.tab_widget.indexOf(widget)
                self.assertGreaterEqual(index, 0, key)
                self.assertIs(self.window.tab_widget.widget(index), widget)
                self.assertEqual(self.window.tab_widget.tabText(index), translator.translate(key, "tabs"))
            self.assertEqual(
                self.window.display_pane_tab.name_jp_label.text(),
                translator.translate("display_frame_name_jp", "fields"),
            )
            self.assertEqual(SettingsService().get(settings_keys.UI_GENERAL_LANGUAGE), target_language)
            self.assertEqual(
                cmds.optionVar(query=_option_var_name(settings_keys.UI_GENERAL_LANGUAGE)),
                target_language,
            )
            _emit_witness(
                "settings.language",
                "selector",
                "objectName=settingsLanguageCombo",
                "QTest.setCurrentIndex(objectName=settingsLanguageCombo)",
                "language persisted and translated all tabs",
                language_spy,
                settings_tab.language_combo,
            )

            # Fresh service/window read the language persisted by the combo
            # signal, without invoking the presenter directly.
            self.window.close()
            self.window.deleteLater()
            QApplication.processEvents()
            self.window = MainWindow()
            self.window.show()
            QApplication.processEvents()
            self.assertEqual(
                self.window.settings_presenter.view.language_combo.currentData(),
                target_language,
            )
            self.assertEqual(
                self.window.tab_widget.tabText(self.window.tab_widget.indexOf(self.window.display_pane_tab)),
                translator.translate("display_pane", "tabs"),
            )
            settings_tab = self.window.settings_presenter.view
            self.window.tab_widget.setCurrentWidget(settings_tab)
            QApplication.processEvents()

            tab_surface_spies = {}
            tab_bar = self.window.tab_widget.tabBar()
            for surface_id, _key, widget in (
                ("import_export.tab_selector", "file_io", self.window.import_export_tab),
                ("export.tab_selector", "export_workflow", self.window.export_tab),
                ("info.tab_selector", "info", self.window.info_presenter.view),
                ("material.tab_selector", "material", self.window.material_presenter.view),
                ("bone.tab_selector", "bone", self.window.bone_presenter.view),
                ("morph.tab_selector", "morph", self.window.morph_tab),
                ("display_pane.tab_selector", "display_pane", self.window.display_pane_tab),
                ("physics.tab_selector", "physics", self.window.physics_tab),
                ("settings.tab_selector", "settings", settings_tab),
            ):
                target_index = self.window.tab_widget.indexOf(widget)
                prep_index = (target_index + 1) % self.window.tab_widget.count()
                self.window.tab_widget.setCurrentIndex(prep_index)
                QApplication.processEvents()
                tab_spy = QtSignalInvocationSpy(
                    "MainWindow._on_main_tab_changed",
                    self.window.tab_widget.currentChanged,
                    source_control=self.window.tab_widget,
                )
                QTest.mouseClick(
                    tab_bar,
                    Qt.LeftButton,
                    pos=tab_bar.tabRect(target_index).center(),
                )
                QApplication.processEvents()
                tab_spy.stop()
                self.assertIs(self.window.tab_widget.currentWidget(), widget)
                self.assertEqual(tab_spy.action_count, 1)
                tab_surface_spies[surface_id] = (
                    tab_spy,
                    widget,
                    self.window.tab_widget,
                )

            # Development Mode is driven by its checkbox signal.  Selecting a
            # free port first makes the auto-open deterministic and owned by
            # this test.
            port = _free_local_port()
            self._owned_ports.add(port)
            port_spy = QtSignalInvocationSpy(
                "SettingsPresenter.set_command_port",
                settings_tab.command_port_spin.valueChanged,
                settings_tab.command_port_spin,
            )
            settings_tab.command_port_spin.setValue(port)
            settings_tab.development_mode_check.click()
            self.assertTrue(_drain_events_until(settings_tab.development_mode_check.isChecked))
            self.assertTrue(settings_tab.dev_tools_group.isVisible())
            self.assertTrue(_port_is_open(port))
            self.assertEqual(SettingsService().get(settings_keys.UI_DEV_COMMAND_PORT), port)
            self.assertEqual(settings_tab.open_command_port_btn.text(), translator.translate("close_command_port", "buttons"))

            # The same production button must close and reopen the port.
            close_port_spy = QtSignalInvocationSpy(
                "SettingsPresenter.toggle_command_port",
                settings_tab.open_command_port_btn.clicked,
                settings_tab.open_command_port_btn,
            )
            settings_tab.open_command_port_btn.click()
            close_port_spy.stop()
            self.assertTrue(_drain_events_until(lambda: not _port_is_open(port)))
            self.assertEqual(settings_tab.open_command_port_btn.text(), translator.translate("open_command_port", "buttons"))
            open_port_spy = QtSignalInvocationSpy(
                "SettingsPresenter.toggle_command_port",
                settings_tab.open_command_port_btn.clicked,
                settings_tab.open_command_port_btn,
            )
            settings_tab.open_command_port_btn.click()
            open_port_spy.stop()
            self.assertTrue(_drain_events_until(lambda: _port_is_open(port)))
            self.assertEqual(settings_tab.open_command_port_btn.text(), translator.translate("close_command_port", "buttons"))
            development_spy = QtSignalInvocationSpy(
                "SettingsPresenter.set_development_mode",
                settings_tab.development_mode_check.clicked,
                settings_tab.development_mode_check,
            )
            settings_tab.development_mode_check.click()
            development_spy.stop()
            self.assertFalse(settings_tab.development_mode_check.isChecked())
            self.assertFalse(settings_tab.dev_tools_group.isVisible())
            self.assertTrue(_drain_events_until(lambda: not _port_is_open(port)))

            # Logging checkbox/level controls are persisted through the real
            # Save button.  A temporary handler proves cached logger behavior;
            # this intentionally does not claim file-handler/path emission.
            test_logger = get_logger("tests.gui.settings_side_effects")
            handler = _RecordingHandler()
            test_logger.add_handler(handler)
            try:
                if settings_tab.logging_enabled_check.isChecked():
                    settings_tab.logging_enabled_check.click()
                settings_tab.save_settings_btn.click()
                self.assertFalse(SettingsService().get(settings_keys.LOGGING_ENABLED))
                self.assertEqual(
                    cmds.optionVar(query=_option_var_name(settings_keys.LOGGING_ENABLED)),
                    0,
                )
                handler.records[:] = []
                test_logger.warning("disabled logging probe")
                self.assertEqual(handler.records, [])

                logging_spy = QtSignalInvocationSpy(
                    "SettingsPresenter.set_logging_enabled",
                    settings_tab.logging_enabled_check.clicked,
                    settings_tab.logging_enabled_check,
                )
                settings_tab.logging_enabled_check.click()
                error_index = settings_tab.log_level_combo.findText("ERROR")
                self.assertGreaterEqual(error_index, 0)
                level_spy = QtSignalInvocationSpy(
                    "SettingsPresenter.set_log_level",
                    settings_tab.log_level_combo.currentIndexChanged,
                    settings_tab.log_level_combo,
                )
                settings_tab.log_level_combo.setCurrentIndex(error_index)
                level_spy.stop()
                save_spy = QtSignalInvocationSpy(
                    "SettingsPresenter.save_all_settings",
                    settings_tab.save_settings_btn.clicked,
                    settings_tab.save_settings_btn,
                )
                settings_tab.save_settings_btn.click()
                self.assertTrue(SettingsService().get(settings_keys.LOGGING_ENABLED))
                self.assertEqual(SettingsService().get(settings_keys.LOGGING_LEVEL), "ERROR")
                self.assertEqual(
                    cmds.optionVar(query=_option_var_name(settings_keys.LOGGING_LEVEL)),
                    "ERROR",
                )
                handler.records[:] = []
                test_logger.info("filtered info probe")
                test_logger.error("accepted error probe")
                self.assertEqual([record.levelno for record in handler.records], [logging.ERROR])
            finally:
                test_logger.remove_handler(handler)
                handler.close()

            # Restore the normal enabled Development Mode surface while the
            # already-frozen witnesses for its child controls are emitted.
            settings_tab.development_mode_check.click()
            self.assertTrue(_drain_events_until(settings_tab.dev_tools_group.isVisible))

            surface_spies = {
                "settings.save": (save_spy, settings_tab.save_settings_btn),
                "settings.development_mode": (
                    development_spy,
                    settings_tab.development_mode_check,
                ),
                "settings.command_port": (port_spy, settings_tab.command_port_spin),
                "settings.open_command_port": (
                    open_port_spy,
                    settings_tab.open_command_port_btn,
                ),
                "settings.logging_enabled": (
                    logging_spy,
                    settings_tab.logging_enabled_check,
                ),
                "settings.log_level": (level_spy, settings_tab.log_level_combo),
            }
            surface_spies.update(
                (surface_id, (spy, widget))
                for surface_id, (spy, widget, _interaction_control) in tab_surface_spies.items()
            )

            for surface_id, locator_key, locator, interaction, fired_action, oracle in (
                (
                    "import_export.tab_selector",
                    "selector",
                    "objectName=ImportExportTab",
                    "QTest.mouseClick(mainTabBar, ImportExportTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match import_export",
                ),
                (
                    "export.tab_selector",
                    "attribute",
                    "export_tab",
                    "QTest.mouseClick(mainTabBar, export_tab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match export",
                ),
                (
                    "info.tab_selector",
                    "selector",
                    "objectName=InfoTab",
                    "QTest.mouseClick(mainTabBar, InfoTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match info",
                ),
                (
                    "material.tab_selector",
                    "selector",
                    "objectName=MaterialTab",
                    "QTest.mouseClick(mainTabBar, MaterialTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match material",
                ),
                (
                    "bone.tab_selector",
                    "selector",
                    "objectName=BoneTab",
                    "QTest.mouseClick(mainTabBar, BoneTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match bone",
                ),
                (
                    "morph.tab_selector",
                    "selector",
                    "objectName=MorphTab",
                    "QTest.mouseClick(mainTabBar, MorphTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match morph",
                ),
                (
                    "display_pane.tab_selector",
                    "selector",
                    "objectName=DisplayPaneTab",
                    "QTest.mouseClick(mainTabBar, DisplayPaneTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and child label match display_pane",
                ),
                (
                    "physics.tab_selector",
                    "selector",
                    "objectName=PhysicsTab",
                    "QTest.mouseClick(mainTabBar, PhysicsTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match physics",
                ),
                (
                    "settings.tab_selector",
                    "selector",
                    "objectName=SettingsTab",
                    "QTest.mouseClick(mainTabBar, SettingsTab)",
                    "MainWindow._on_main_tab_changed",
                    "tab identity and translated label match settings",
                ),
                (
                    "settings.save",
                    "selector",
                    "objectName=settingsSaveButton",
                    "QTest.click(objectName=settingsSaveButton)",
                    "SettingsPresenter.save_all_settings",
                    "logging enabled and ERROR level persisted with filtered logger output",
                ),
                (
                    "settings.development_mode",
                    "selector",
                    "objectName=settingsDevelopmentModeCheck",
                    "QTest.click(objectName=settingsDevelopmentModeCheck)",
                    "SettingsPresenter.set_development_mode",
                    "development mode visibility, command port, and close state verified",
                ),
                (
                    "settings.command_port",
                    "selector",
                    "objectName=settingsCommandPortSpin",
                    "QTest.setValue(objectName=settingsCommandPortSpin)",
                    "SettingsPresenter.set_command_port",
                    "selected port persisted and opened by development mode",
                ),
                (
                    "settings.open_command_port",
                    "selector",
                    "objectName=settingsOpenCommandPortButton",
                    "QTest.click(objectName=settingsOpenCommandPortButton)",
                    "SettingsPresenter.toggle_command_port",
                    "command port close and reopen states verified",
                ),
                (
                    "settings.logging_enabled",
                    "selector",
                    "objectName=settingsLoggingEnabledCheck",
                    "QTest.click(objectName=settingsLoggingEnabledCheck)",
                    "SettingsPresenter.set_logging_enabled",
                    "disabled and re-enabled logging side effects verified",
                ),
                (
                    "settings.log_level",
                    "selector",
                    "objectName=settingsLogLevelCombo",
                    "QTest.setCurrentIndex(objectName=settingsLogLevelCombo, ERROR)",
                    "SettingsPresenter.set_log_level",
                    "ERROR persisted and only error record accepted by handler",
                ),
            ):
                action_spy, control = surface_spies[surface_id]
                if surface_id.endswith(".tab_selector"):
                    self.window.tab_widget.setCurrentWidget(control)
                    QApplication.processEvents()
                else:
                    self.window.tab_widget.setCurrentWidget(settings_tab)
                    QApplication.processEvents()
                _emit_witness(
                    surface_id,
                    locator_key,
                    locator,
                    interaction,
                    oracle,
                    action_spy,
                    control,
                    self.window.tab_widget
                    if surface_id.endswith(".tab_selector")
                    else None,
                )
        finally:
            self._close_owned_ports()


if __name__ == "__main__":
    unittest.main()
