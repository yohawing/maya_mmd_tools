"""Runtime GUI coverage for the remaining Import, Export, Info, and Settings surfaces.

The test cases use a production :class:`MainWindow` in a real Maya GUI.  File
dialogs are replaced only at the Qt dialog boundary so the production button
slots, presenters, and settings/metadata writers still execute.
"""

import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.adapters.maya_cmds_adapter import MayaCmdsAdapter
from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer
from mmd_tools.core import settings_keys
from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME_EN,
)
from mmd_tools.core.settings import get_settings
from mmd_tools.services.settings_service import SettingsService
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QDialog, QSettings, QMessageBox, Qt
from mmd_tools.validation.export_validator import (
    ExportValidationIssue,
    ExportValidationReport,
)
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import (
    ActionInvocationSpy,
    QtSignalInvocationSpy,
    build_surface_witness,
)

try:
    from PySide6.QtTest import QTest
except ImportError:  # pragma: no cover - Maya 2024 may use PySide2
    from PySide2.QtTest import QTest


_CASE_ID = "gui.ui_coverage_import_settings_remaining"
_VIEW_STATE_KEYS = (
    "file_history",
    "import_path",
    "vmd_path",
    "new_file_check",
    "custom_namespace_check",
    "custom_namespace_name",
)

_IMPORT_SELECTORS = {
    "import_export.scale": "scale_spin",
    "import_export.use_namespace": "use_namespace_check",
    "import_export.custom_namespace": "custom_namespace_check",
    "import_export.namespace": "namespace_edit",
    "import_export.create_mmd_shaders": "create_mmd_shaders_check",
    "import_export.create_mmd_control_rig": "create_mmd_control_rig_check",
    "import_export.separate_meshes": "separate_meshes_check",
    "import_export.auto_resolve_textures": "auto_resolve_textures_check",
    "import_export.disable_backface_culling": "disable_backface_culling_check",
    "import_export.texture_search_path": "texture_search_path_edit",
    "import_export.uv_set_name": "uv_set_name_edit",
    "import_export.import_morphs": "import_morphs_check",
    "import_export.import_physics": "import_physics_check",
    "import_export.cpp_fast_load": "use_cpp_fast_load_check",
    "import_export.cpp_vp2_ownership": "use_cpp_vp2_ownership_check",
    "import_export.cpp_rig_nodes": "use_cpp_rig_nodes_check",
    "import_export.vmd_fps": "vmd_fps_combo",
    "import_export.motion_scale": "motion_scale_spin",
    "import_export.bake_mode": "bake_mode_check",
    "import_export.vmd_rotation_time_curve": "vmd_rotation_time_curve_check",
    "import_export.native_physics_bake": "native_physics_bake_check",
    "import_export.reduce_bake_keys": "reduce_bake_keys_check",
    "import_export.reduce_quality": "reduce_quality_slider",
    "import_export.import_browse": "import_path_button",
    "import_export.new_file": "new_file_check",
    "import_export.vmd_browse": "vmd_path_button",
    "import_export.clear_existing_motion": "clear_existing_motion_check",
    "import_export.clear_history": "clear_history_button",
}

_EXPORT_SELECTORS = {
    "export.output_browse": "output_browse_button",
    "export.validation_revalidate": "objectName=validationRevalidateButton",
    "export.validation_copy": "objectName=validationCopyButton",
    "export.validation_save": "objectName=validationSaveButton",
}

_INFO_SELECTORS = {
    "info.model_name_en": "model_name_en_edit",
    "info.comment_en": "comment_en_edit",
}

_SETTINGS_SELECTORS = {
    "settings.reset": "objectName=settingsResetButton",
    "settings.export": "objectName=settingsExportButton",
    "settings.import": "objectName=settingsImportButton",
    "settings.file_history_limit": "objectName=settingsFileHistoryLimitSpin",
    "settings.log_file_path": "objectName=settingsLogFilePathEdit",
    "settings.log_file_browse": "objectName=settingsLogFileBrowseButton",
}

# The manifest distinguishes a Python attribute locator from a Qt selector.
# Keep that distinction in the runtime evidence object: the gate compares the
# key as well as the locator value, so emitting every locator under ``selector``
# is not contract-compatible for attribute-inventoried controls.
_ATTRIBUTE_SURFACE_IDS = frozenset(
    set(_IMPORT_SELECTORS)
    | set(_INFO_SELECTORS)
    | {"export.output_browse"}
)


def _free_local_port():
    """Return an unused local port for the Development Mode commandPort."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _nested(mapping, key_path, default=None):
    """Read a dotted value from a nested settings dictionary."""

    value = mapping
    for key in key_path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


@requires_gui
class TestUiCoverageImportSettingsRemainingGUI(GuiTestBase):
    """Drive every currently ``not_run`` surface in the four assigned tabs."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.settings_store = get_settings()
        self._settings_before = copy.deepcopy(self.settings_store.data)
        self._view_settings = QSettings("maya_mmd_tools", "ImportExportTab")
        self._view_before = {
            key: self._view_settings.value(key) for key in _VIEW_STATE_KEYS
        }
        self._temp_dir = tempfile.TemporaryDirectory(prefix="mmd_ui_coverage_remaining_")
        self._owned_ports = set()

        # Start every case in a deterministic Development Mode state so the
        # Import tab's development-only controls are real, visible widgets.
        self._port = _free_local_port()
        self._owned_ports.add(self._port)
        self.settings_store.data = copy.deepcopy(self._settings_before)
        service = SettingsService(self.settings_store)
        initial_values = {
            settings_keys.UI_GENERAL_DEVELOPMENT_MODE: True,
            settings_keys.UI_DEV_COMMAND_PORT: self._port,
            settings_keys.IMPORT_GENERAL_SCALE_FACTOR: 1.0,
            settings_keys.IMPORT_GENERAL_USE_NAMESPACE: False,
            settings_keys.IMPORT_MODEL_CREATE_MMD_SHADERS: True,
            settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG: False,
            settings_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL: False,
            settings_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES: True,
            settings_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING: True,
            settings_keys.IMPORT_MODEL_TEXTURE_SEARCH_PATH: "",
            settings_keys.IMPORT_MODEL_UV_SET_NAME: "map#",
            settings_keys.IMPORT_MORPH_IMPORT_MORPHS: True,
            settings_keys.IMPORT_PHYSICS_IMPORT_PHYSICS: True,
            settings_keys.IMPORT_NATIVE_USE_CPP_FAST_LOAD: False,
            settings_keys.IMPORT_NATIVE_USE_CPP_VP2_OWNERSHIP: False,
            settings_keys.IMPORT_NATIVE_USE_CPP_RIG_NODES: False,
            settings_keys.IMPORT_ANIMATION_VMD_FPS: 30,
            settings_keys.IMPORT_ANIMATION_MOTION_SCALE: 1.0,
            settings_keys.IMPORT_RIG_BAKE_MODE: False,
            settings_keys.IMPORT_ANIMATION_VMD_ROTATION_TIME_CURVE: True,
            settings_keys.IMPORT_ANIMATION_USE_NATIVE_PHYSICS_BAKE: False,
            settings_keys.IMPORT_ANIMATION_REDUCE_BAKE_KEYS: False,
            settings_keys.IMPORT_ANIMATION_REDUCE_QUALITY: 1.0,
            settings_keys.IMPORT_ANIMATION_CLEAR_EXISTING_MOTION: False,
            settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT: 20,
            settings_keys.LOGGING_LOG_FILE_PATH: "logs/mmd_tools.log",
        }
        for key, value in initial_values.items():
            service.set(key, value)
        for key, value in {
            "custom_namespace_check": "False",
            "custom_namespace_name": "",
            "new_file_check": "False",
            "import_path": "",
            "vmd_path": "",
        }.items():
            self._view_settings.setValue(key, value)
        self._view_settings.setValue("file_history", "[]")

        # A real model gives Info writes a canonical Maya undo target and lets
        # the Export presenter run its normal workflow path.
        adapter = MayaModelTemplateInitializer(MayaCmdsAdapter(cmds_module=cmds))
        self.template = adapter.create("pmx20-basic-v1", "Coverage JP", "Coverage EN")
        self.window = MainWindow()
        self.window.show()
        self.window.app_state.current_model_root = self.template.root
        QApplication.processEvents()

    def tearDown(self):
        try:
            for port in tuple(self._owned_ports):
                try:
                    if cmds.commandPort(f":{port}", query=True):
                        cmds.commandPort(name=f":{port}", close=True)
                except Exception:
                    pass
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
            cmds.file(new=True, force=True)
        finally:
            self.settings_store.data = copy.deepcopy(self._settings_before)
            self.settings_store.save()
            for key, value in self._view_before.items():
                if value is None:
                    self._view_settings.remove(key)
                else:
                    self._view_settings.setValue(key, value)
            self._temp_dir.cleanup()
            super().tearDown()

    @staticmethod
    def _pump():
        QApplication.processEvents()
        QApplication.processEvents()
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                title = widget.windowTitle() or widget.objectName() or type(widget).__name__
                widget.reject()
                raise AssertionError(f"unexpected modal during UI coverage: {title}")

    def _click_once(self, widget):
        """Click a production widget and assert one Qt click emission."""

        self._ensure_widget_visible(widget)
        self.assertTrue(widget.isVisible(), widget.objectName() or type(widget).__name__)
        self.assertTrue(widget.isEnabled(), widget.objectName() or type(widget).__name__)
        action_spy = QtSignalInvocationSpy("pending", widget.clicked, widget)
        QTest.mouseClick(widget, Qt.LeftButton)
        self._pump()
        if not action_spy.action_count:
            # Maya 2024's PySide2 can reject a synthetic mouse coordinate for
            # a checkbox inside a scrolled splitter even after the page is
            # active.  Keyboard activation remains a real Qt interaction on
            # the same production widget and avoids direct state mutation.
            widget.setFocus()
            QTest.keyClick(widget, Qt.Key_Space)
            self._pump()
        self.assertEqual(action_spy.action_count, 1, widget.objectName() or type(widget).__name__)
        self._last_action_spy = action_spy
        self._last_action_control = widget
        return action_spy

    @staticmethod
    def _ensure_widget_visible(widget):
        """Reveal a nested control before sending it a real QTest mouse event."""

        window = widget.window()
        if window is not None:
            window.raise_()
            window.activateWindow()
        child = widget
        while child is not None:
            parent = child.parentWidget()
            if parent is None:
                break
            ensure_visible = getattr(parent, "ensureWidgetVisible", None)
            if callable(ensure_visible):
                ensure_visible(widget)
            set_current = getattr(parent, "setCurrentWidget", None)
            if callable(set_current):
                try:
                    set_current(child)
                except (TypeError, RuntimeError):
                    pass
            child = parent
        QApplication.processEvents()

    def _set_line_once(self, widget, value):
        action_spy = QtSignalInvocationSpy("pending", widget.textChanged, widget)
        widget.setText(value)
        self._pump()
        self.assertEqual(action_spy.action_count, 1, widget.objectName() or type(widget).__name__)
        self._last_action_spy = action_spy
        self._last_action_control = widget
        return action_spy

    def _set_value_once(self, widget, value):
        action_spy = QtSignalInvocationSpy("pending", widget.valueChanged, widget)
        widget.setValue(value)
        self._pump()
        self.assertEqual(action_spy.action_count, 1, widget.objectName() or type(widget).__name__)
        self._last_action_spy = action_spy
        self._last_action_control = widget
        return action_spy

    def _set_combo_once(self, widget, value):
        action_spy = QtSignalInvocationSpy("pending", widget.currentTextChanged, widget)
        widget.setCurrentText(value)
        self._pump()
        self.assertEqual(action_spy.action_count, 1, widget.objectName() or type(widget).__name__)
        self._last_action_spy = action_spy
        self._last_action_control = widget
        return action_spy

    def _emit(self, surface_id, locator, interaction, fired_action, oracle):
        """Print the gate-compatible witness after the semantic oracle passes."""

        locator_key = "attribute" if surface_id in _ATTRIBUTE_SURFACE_IDS else "selector"
        action_spy = self._last_action_spy
        action_spy.action_name = fired_action
        witness = build_surface_witness(
            surface_id=surface_id,
            case_id=_CASE_ID,
            interaction=interaction,
            oracle=oracle,
            action_spy=action_spy,
            control=self._last_action_control,
            **{locator_key: locator},
        )
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def test_import_remaining_surfaces(self):
        """Cover all 28 Import/Animation controls and their persisted state."""

        view = self.window.import_export_tab
        self.window.tab_widget.setCurrentWidget(view)
        view.show()
        view.raise_()
        self._pump()
        service = SettingsService(self.settings_store)
        temp_root = Path(self._temp_dir.name)
        import_file = temp_root / "chosen_model.pmx"
        vmd_file = temp_root / "chosen_motion.vmd"
        import_file.write_text("fixture", encoding="utf-8")
        vmd_file.write_text("fixture", encoding="utf-8")

        def emit(surface_id, interaction, action, oracle):
            self._emit(surface_id, _IMPORT_SELECTORS[surface_id], interaction, action, oracle)

        # General/model controls.
        self._set_value_once(view.scale_spin, 1.25)
        self.assertAlmostEqual(service.get(settings_keys.IMPORT_GENERAL_SCALE_FACTOR), 1.25)
        emit("import_export.scale", "QTest.edit(scale_spin,1.25)", "SettingsService.set(import.general.scale_factor)", "stored=1.25")

        self._click_once(view.use_namespace_check)
        self.assertTrue(service.get(settings_keys.IMPORT_GENERAL_USE_NAMESPACE))
        emit("import_export.use_namespace", "QTest.mouseClick(use_namespace_check)", "SettingsService.set(import.general.use_namespace)", "checked=true")

        self._click_once(view.custom_namespace_check)
        self.assertTrue(view.custom_namespace_check.isChecked())
        self.assertTrue(view.namespace_edit.isEnabled())
        emit("import_export.custom_namespace", "QTest.mouseClick(custom_namespace_check)", "ImportExportViewState.set(custom_namespace_check)", "enabled=true")

        self._set_line_once(view.namespace_edit, "coverage_ns")
        self.assertEqual(view.get_custom_namespace(), "coverage_ns")
        emit("import_export.namespace", "QTest.edit(namespace_edit,'coverage_ns')", "ImportExportViewState.set(custom_namespace_name)", "custom_namespace=coverage_ns")

        for surface_id, widget, key, expected, action in (
            ("import_export.create_mmd_shaders", view.create_mmd_shaders_check, settings_keys.IMPORT_MODEL_CREATE_MMD_SHADERS, False, "create_mmd_shaders"),
            ("import_export.create_mmd_control_rig", view.create_mmd_control_rig_check, settings_keys.IMPORT_MODEL_CREATE_MMD_CONTROL_RIG, True, "create_mmd_control_rig"),
            ("import_export.separate_meshes", view.separate_meshes_check, settings_keys.IMPORT_MODEL_SEPARATE_MESHES_BY_MATERIAL, True, "separate_meshes_by_material"),
            ("import_export.auto_resolve_textures", view.auto_resolve_textures_check, settings_keys.IMPORT_MODEL_AUTO_RESOLVE_TEXTURES, False, "auto_resolve_textures"),
            ("import_export.disable_backface_culling", view.disable_backface_culling_check, settings_keys.IMPORT_MODEL_DISABLE_BACKFACE_CULLING, False, "disable_backface_culling"),
        ):
            self._click_once(widget)
            self.assertEqual(service.get(key), expected)
            emit(surface_id, f"QTest.mouseClick({_IMPORT_SELECTORS[surface_id]})", f"SettingsService.set({key})", f"stored={str(expected).lower()}")

        self._set_line_once(view.texture_search_path_edit, str(temp_root / "textures"))
        self.assertEqual(service.get(settings_keys.IMPORT_MODEL_TEXTURE_SEARCH_PATH), str(temp_root / "textures"))
        emit("import_export.texture_search_path", "QTest.edit(texture_search_path_edit)", "SettingsService.set(import.model.texture_search_path)", "stored=temporary texture path")

        self._set_line_once(view.uv_set_name_edit, "coverageUV")
        self.assertEqual(service.get(settings_keys.IMPORT_MODEL_UV_SET_NAME), "coverageUV")
        emit("import_export.uv_set_name", "QTest.edit(uv_set_name_edit,'coverageUV')", "SettingsService.set(import.model.uv_set_name)", "stored=coverageUV")

        for surface_id, widget, key, expected in (
            ("import_export.import_morphs", view.import_morphs_check, settings_keys.IMPORT_MORPH_IMPORT_MORPHS, False),
            ("import_export.import_physics", view.import_physics_check, settings_keys.IMPORT_PHYSICS_IMPORT_PHYSICS, False),
        ):
            self._click_once(widget)
            self.assertEqual(service.get(key), expected)
            emit(surface_id, f"QTest.mouseClick({_IMPORT_SELECTORS[surface_id]})", f"SettingsService.set({key})", f"stored={str(expected).lower()}")

        self._click_once(view.use_cpp_fast_load_check)
        self.assertTrue(service.get(settings_keys.IMPORT_NATIVE_USE_CPP_FAST_LOAD))
        emit("import_export.cpp_fast_load", "QTest.mouseClick(use_cpp_fast_load_check)", "SettingsService.set(import.native.use_cpp_fast_load)", "stored=true")
        self.assertTrue(view.use_cpp_vp2_ownership_check.isEnabled())
        self._click_once(view.use_cpp_vp2_ownership_check)
        self.assertTrue(service.get(settings_keys.IMPORT_NATIVE_USE_CPP_VP2_OWNERSHIP))
        emit("import_export.cpp_vp2_ownership", "QTest.mouseClick(use_cpp_vp2_ownership_check)", "SettingsService.set(import.native.use_cpp_vp2_ownership)", "stored=true")
        self._click_once(view.use_cpp_rig_nodes_check)
        self.assertTrue(service.get(settings_keys.IMPORT_NATIVE_USE_CPP_RIG_NODES))
        emit("import_export.cpp_rig_nodes", "QTest.mouseClick(use_cpp_rig_nodes_check)", "SettingsService.set(import.native.use_cpp_rig_nodes)", "stored=true")

        self._set_combo_once(view.vmd_fps_combo, "60")
        self.assertEqual(service.get(settings_keys.IMPORT_ANIMATION_VMD_FPS), 60)
        emit("import_export.vmd_fps", "QTest.edit(vmd_fps_combo,'60')", "SettingsService.set(import.animation.vmd_fps)", "stored=60")
        self._set_value_once(view.motion_scale_spin, 1.5)
        self.assertAlmostEqual(service.get(settings_keys.IMPORT_ANIMATION_MOTION_SCALE), 1.5)
        emit("import_export.motion_scale", "QTest.edit(motion_scale_spin,1.5)", "SettingsService.set(import.animation.motion_scale)", "stored=1.5")

        self._click_once(view.bake_mode_check)
        self.assertTrue(service.get(settings_keys.IMPORT_RIG_BAKE_MODE))
        self.assertTrue(view.native_physics_bake_check.isEnabled())
        self.assertTrue(view.reduce_bake_keys_check.isEnabled())
        emit("import_export.bake_mode", "QTest.mouseClick(bake_mode_check)", "SettingsService.set(import.rig.bake_mode)", "stored=true; dependent bake controls enabled")

        self._click_once(view.native_physics_bake_check)
        self.assertTrue(service.get(settings_keys.IMPORT_ANIMATION_USE_NATIVE_PHYSICS_BAKE))
        emit("import_export.native_physics_bake", "QTest.mouseClick(native_physics_bake_check)", "SettingsService.set(import.animation.use_native_physics_bake)", "stored=true")

        self._click_once(view.reduce_bake_keys_check)
        self.assertTrue(service.get(settings_keys.IMPORT_ANIMATION_REDUCE_BAKE_KEYS))
        self.assertTrue(view.reduce_quality_slider.isVisible())
        emit("import_export.reduce_bake_keys", "QTest.mouseClick(reduce_bake_keys_check)", "SettingsService.set(import.animation.reduce_bake_keys)", "stored=true; quality visible")

        self._set_value_once(view.reduce_quality_slider, 75)
        self.assertAlmostEqual(service.get(settings_keys.IMPORT_ANIMATION_REDUCE_QUALITY), 0.75)
        self.assertEqual(view.reduce_quality_value_label.text(), "0.75")
        emit("import_export.reduce_quality", "QTest.edit(reduce_quality_slider,75)", "ImportExportTab._on_reduce_quality_changed", "stored=0.75; label=0.75")

        # End Bake Mode to expose the sparse rotation-curve control while the
        # Control Rig setting remains enabled.
        self._click_once(view.bake_mode_check)
        self.assertFalse(view.bake_mode_check.isChecked())
        self.assertTrue(view.vmd_rotation_time_curve_check.isEnabled())
        self._click_once(view.vmd_rotation_time_curve_check)
        self.assertFalse(service.get(settings_keys.IMPORT_ANIMATION_VMD_ROTATION_TIME_CURVE))
        emit("import_export.vmd_rotation_time_curve", "QTest.mouseClick(vmd_rotation_time_curve_check)", "SettingsService.set(import.animation.vmd_rotation_time_curve)", "stored=false")

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.QFileDialog.getOpenFileName",
            return_value=(str(import_file), "MMD Files (*.pmx)"),
        ):
            self._click_once(view.import_path_button)
        self.assertEqual(view.import_path_edit.text(), str(import_file))
        emit("import_export.import_browse", "QTest.mouseClick(import_path_button)", "ImportExportPresenter.select_import_file", "path edit equals chosen_model.pmx")

        self._click_once(view.new_file_check)
        self.assertEqual(self._view_settings.value("new_file_check"), "True")
        emit("import_export.new_file", "QTest.mouseClick(new_file_check)", "ImportExportViewState.set(new_file_check)", "view-state=True")

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.QFileDialog.getOpenFileName",
            return_value=(str(vmd_file), "VMD Files (*.vmd)"),
        ):
            self._click_once(view.vmd_path_button)
        self.assertEqual(view.vmd_path_edit.text(), str(vmd_file))
        emit("import_export.vmd_browse", "QTest.mouseClick(vmd_path_button)", "ImportExportPresenter.select_vmd_file", "path edit equals chosen_motion.vmd")

        self._click_once(view.clear_existing_motion_check)
        self.assertTrue(service.get(settings_keys.IMPORT_ANIMATION_CLEAR_EXISTING_MOTION))
        emit("import_export.clear_existing_motion", "QTest.mouseClick(clear_existing_motion_check)", "SettingsService.set(import.animation.clear_existing_motion)", "stored=true")

        history_model = temp_root / "history_model.pmx"
        history_motion = temp_root / "history_motion.vmd"
        history_model.write_text("model", encoding="utf-8")
        history_motion.write_text("motion", encoding="utf-8")
        view.view_state.save_file_history("import", str(history_model))
        view.view_state.save_file_history("vmd", str(history_motion))
        view.refresh_unified_history()
        self.assertEqual(view.unified_history_list.count(), 2)
        self._click_once(view.clear_history_button)
        self.assertEqual(view.unified_history_list.count(), 0)
        emit("import_export.clear_history", "QTest.mouseClick(clear_history_button)", "ImportExportTab._clear_all_history", "unified import/vmd history count=0")

    def _warning_report(self):
        return ExportValidationReport(
            "vmd",
            (
                ExportValidationIssue(
                    "VMD_RAW_PROVENANCE_MISSING",
                    "warning",
                    False,
                    "raw_provenance",
                    "coverage fixture warning",
                ),
            ),
            mode="A",
        )

    def test_export_remaining_surfaces(self):
        """Cover output browse and the three Validation Console actions."""

        view = self.window.export_tab
        self.window.tab_widget.setCurrentWidget(view)
        self._pump()
        temp_root = Path(self._temp_dir.name)
        output_path = temp_root / "coverage_output.any"

        with patch(
            "mmd_tools.ui.tabs.export_tab.QFileDialog.getSaveFileName",
            return_value=(str(output_path), "PMX Files (*.pmx)"),
        ):
            self._click_once(view.output_browse_button)
        self.assertEqual(view.output_path_edit.text(), str(output_path.with_suffix(".pmx")))
        self._emit("export.output_browse", _EXPORT_SELECTORS["export.output_browse"], "QTest.mouseClick(output_browse_button)", "ExportTab._browse_output", "output path coerced to .pmx")

        revalidate_events = []
        view.validate_requested.connect(lambda: revalidate_events.append(True))
        view.output_path_edit.setText(str(temp_root / "revalidate.pmx"))
        view.validation_console.set_report(self._warning_report())
        self._click_once(view.validation_console.revalidate_button)
        self.assertEqual(revalidate_events, [True])
        self.assertIsNotNone(view.validation_console.report)
        self.assertEqual(view.state_label.text(), "Ready")
        self._emit("export.validation_revalidate", _EXPORT_SELECTORS["export.validation_revalidate"], "QTest.mouseClick(objectName=validationRevalidateButton)", "ExportPresenter.validate", f"report-present; state={view.state_label.text()}")

        view.validation_console.set_report(self._warning_report(), {"fixture": "remaining-ui"})
        clipboard = QApplication.clipboard()
        clipboard.clear()
        self._click_once(view.validation_console.copy_button)
        self.assertIn("VMD_RAW_PROVENANCE_MISSING", clipboard.text())
        self._emit("export.validation_copy", _EXPORT_SELECTORS["export.validation_copy"], "QTest.mouseClick(objectName=validationCopyButton)", "ValidationConsole.copy_report", "clipboard contains VMD_RAW_PROVENANCE_MISSING")

        report_dir = temp_root / "saved_validation"
        with patch(
            "mmd_tools.ui.validation_console.QFileDialog.getExistingDirectory",
            return_value=str(report_dir),
        ):
            self._click_once(view.validation_console.save_button)
        self.assertTrue((report_dir / "report.json").exists())
        self.assertTrue((report_dir / "report.md").exists())
        self.assertIn("VMD_RAW_PROVENANCE_MISSING", (report_dir / "report.json").read_text(encoding="utf-8"))
        self._emit("export.validation_save", _EXPORT_SELECTORS["export.validation_save"], "QTest.mouseClick(objectName=validationSaveButton)", "ValidationConsole.save_report", "report.json and report.md written")

    def test_info_remaining_surfaces(self):
        """Cover English model name/comment with real Maya undo and redo."""

        view = self.window.info_presenter.view
        self.window.tab_widget.setCurrentWidget(view)
        self._pump()
        self.assertTrue(view.model_name_en_edit.isEnabled())

        editor = view.model_name_en_edit
        old_value = cmds.getAttr(f"{self.template.root}.{ATTR_MMD_MODEL_NAME_EN}")
        original_update = self.window.info_presenter.update_model_info
        edit_spy = ActionInvocationSpy.wrap("pending", original_update, editor)
        self.window.info_presenter.update_model_info = edit_spy
        editor.setFocus()
        editor.setText("Coverage English Name")
        self._pump()
        self.window.info_presenter.update_model_info = original_update
        editor.clearFocus()
        self._pump()
        self.assertEqual(edit_spy.action_count, 1)
        self._last_action_spy = edit_spy
        self._last_action_control = editor
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_MODEL_NAME_EN}"), "Coverage English Name")
        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_MODEL_NAME_EN}"), old_value)
        cmds.redo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_MODEL_NAME_EN}"), "Coverage English Name")
        self._emit("info.model_name_en", _INFO_SELECTORS["info.model_name_en"], "QTest.edit(model_name_en_edit,'Coverage English Name')", "InfoPresenter.update_model_info", "Maya attr updated and Undo/Redo restored value")

        editor = view.comment_en_edit
        old_value = cmds.getAttr(f"{self.template.root}.{ATTR_MMD_COMMENT_EN}")
        original_update = self.window.info_presenter.update_model_info
        edit_spy = ActionInvocationSpy.wrap("pending", original_update, editor)
        self.window.info_presenter.update_model_info = edit_spy
        editor.setFocus()
        editor.setPlainText("Coverage English Comment")
        self._pump()
        self.window.info_presenter.update_model_info = original_update
        editor.clearFocus()
        self._pump()
        self.assertEqual(edit_spy.action_count, 1)
        self._last_action_spy = edit_spy
        self._last_action_control = editor
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_COMMENT_EN}"), "Coverage English Comment")
        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_COMMENT_EN}"), old_value)
        cmds.redo()
        self.assertEqual(cmds.getAttr(f"{self.template.root}.{ATTR_MMD_COMMENT_EN}"), "Coverage English Comment")
        self._emit("info.comment_en", _INFO_SELECTORS["info.comment_en"], "QTest.edit(comment_en_edit,'Coverage English Comment')", "InfoPresenter.update_model_info", "Maya attr updated and Undo/Redo restored value")

    def test_settings_remaining_surfaces(self):
        """Cover reset/import/export and the remaining Settings controls."""

        view = self.window.settings_presenter.view
        self.window.tab_widget.setCurrentWidget(view)
        self._pump()
        service = SettingsService(self.settings_store)
        temp_root = Path(self._temp_dir.name)

        # Establish a non-default value through the production Save action,
        # then drive the confirmation-backed Reset action with a deterministic
        # QMessageBox response.
        self._set_value_once(view.file_history_limit_spin, 77)
        self._click_once(view.save_settings_btn)
        self._pump()
        self.assertEqual(service.get(settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT), 77)
        reset_events = []
        view.reset_settings_btn.clicked.connect(lambda *_args: reset_events.append(True))
        with patch(
            "mmd_tools.ui.presenters.settings_presenter.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            self._click_once(view.reset_settings_btn)
        self.assertEqual(reset_events, [True])
        default_limit = _nested(self.settings_store._defaults, settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT, 20)
        self.assertEqual(view.file_history_limit_spin.value(), int(default_limit))
        self.assertEqual(service.get(settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT), int(default_limit))
        self._emit("settings.reset", _SETTINGS_SELECTORS["settings.reset"], "QTest.mouseClick(objectName=settingsResetButton)", "SettingsPresenter.reset_to_defaults", f"file_history_limit reset to {default_limit}")

        export_file = temp_root / "settings_export.json"
        with patch(
            "mmd_tools.ui.presenters.settings_presenter.QFileDialog.getSaveFileName",
            return_value=(str(export_file), "JSON Files (*.json)"),
        ):
            self._click_once(view.export_settings_btn)
        self.assertTrue(export_file.exists())
        exported = json.loads(export_file.read_text(encoding="utf-8"))
        self.assertEqual(exported, service.export_settings_data())
        self._emit("settings.export", _SETTINGS_SELECTORS["settings.export"], "QTest.mouseClick(objectName=settingsExportButton)", "SettingsPresenter.export_settings", "settings_export.json equals service export payload")

        imported_payload = {"ui": copy.deepcopy(self.settings_store.data.get("ui", {}))}
        imported_payload.setdefault("ui", {}).setdefault("general", {})["file_history_limit"] = 33
        import_file = temp_root / "settings_import.json"
        import_file.write_text(json.dumps(imported_payload), encoding="utf-8")
        with patch(
            "mmd_tools.ui.presenters.settings_presenter.QFileDialog.getOpenFileName",
            return_value=(str(import_file), "JSON Files (*.json)"),
        ):
            self._click_once(view.import_settings_btn)
        self.assertEqual(view.file_history_limit_spin.value(), 33)
        self.assertEqual(service.get(settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT), 33)
        self._emit("settings.import", _SETTINGS_SELECTORS["settings.import"], "QTest.mouseClick(objectName=settingsImportButton)", "SettingsPresenter.import_settings", "file_history_limit imported=33")

        view.file_history_limit_spin.setValue(41)
        self._click_once(view.save_settings_btn)
        self._pump()
        self.assertEqual(service.get(settings_keys.UI_GENERAL_FILE_HISTORY_LIMIT), 41)
        self._emit("settings.file_history_limit", _SETTINGS_SELECTORS["settings.file_history_limit"], "QTest.edit(objectName=settingsFileHistoryLimitSpin,41); click Save", "SettingsPresenter.save_all_settings", "stored=41")

        log_path = str(temp_root / "coverage.log")
        self._set_line_once(view.log_file_path_edit, log_path)
        self._click_once(view.save_settings_btn)
        self._pump()
        self.assertEqual(service.get(settings_keys.LOGGING_LOG_FILE_PATH), log_path)
        self._emit("settings.log_file_path", _SETTINGS_SELECTORS["settings.log_file_path"], "QTest.edit(objectName=settingsLogFilePathEdit)", "SettingsPresenter.save_all_settings", "stored=coverage.log")

        browse_log_path = str(temp_root / "browsed.log")
        with patch(
            "mmd_tools.ui.presenters.settings_presenter.QFileDialog.getSaveFileName",
            return_value=(browse_log_path, "Log Files (*.log)"),
        ):
            self._click_once(view.log_file_browse_btn)
        self.assertEqual(view.log_file_path_edit.text(), browse_log_path)
        self._emit("settings.log_file_browse", _SETTINGS_SELECTORS["settings.log_file_browse"], "QTest.mouseClick(objectName=settingsLogFileBrowseButton)", "SettingsPresenter.browse_log_file", "log path edit equals browsed.log")


if __name__ == "__main__":
    unittest.main()
