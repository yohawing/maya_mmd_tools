"""Production-widget fixtures for the lightweight authoring UI surface gate."""

from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Tuple

from mmd_tools.ui.qt_compat import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QColor,
    QColorDialog,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QWidget,
)
from mmd_tools.ui.components.category_stack import CategoryStack
from tests.common.ui_action_coverage import (
    PreconstructionMethodSpy,
    build_surface_witness,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tools" / "ui_coverage_manifest.json"
HEADLESS_CASE_ID = "headless.authoring_ui_surface_matrix"


def load_headless_surfaces() -> Tuple[Mapping[str, Any], ...]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return tuple(
        surface
        for surface in manifest["surfaces"]
        if surface.get("disposition") == "qt_case"
    )


def install_noninteractive_dialog_responses(monkeypatch: Any) -> None:
    """Keep a production presenter dispatch bounded in offscreen Qt."""
    for name in ("getOpenFileName", "getSaveFileName"):
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *_a, **_k: ("", "")))
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *_a, **_k: "")
    )
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *_a, **_k: ("", False)))
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *_a, **_k: QMessageBox.Ok))
    monkeypatch.setattr(
        QColorDialog, "getColor", staticmethod(lambda *_a, **_k: QColor())
    )
    exec_name = "exec" if hasattr(QDialog, "exec") else "exec_"
    monkeypatch.setattr(QDialog, exec_name, lambda *_a, **_k: QDialog.Rejected)


def _resolve_expected_handler(path: str) -> Tuple[Any, str]:
    module_name, owner_name, method_name = path.rsplit(".", 2)
    owner = getattr(importlib.import_module(module_name), owner_name)
    if method_name not in owner.__dict__:
        raise AssertionError("expected handler is not class-owned: {}".format(path))
    return owner, method_name


def create_production_main_window(
    monkeypatch: Any, surface: Mapping[str, Any]
) -> Tuple[Any, PreconstructionMethodSpy]:
    """Construct the actual MainWindow, tabs, and presenters with bounded Maya calls."""
    from maya import cmds
    from mmd_tools.ui.main_window import MainWindow

    owner, method_name = _resolve_expected_handler(surface["expected_handler"])
    handler_spy = PreconstructionMethodSpy(
        surface["expected_handler"], owner, method_name
    ).install(monkeypatch)
    cmds.about.return_value = "2024"
    cmds.optionVar.return_value = 0
    monkeypatch.setattr(MainWindow, "get_maya_main_window", staticmethod(lambda: None))
    monkeypatch.setattr(MainWindow, "_create_authoring_composition", lambda _self: None)
    install_noninteractive_dialog_responses(monkeypatch)
    window = MainWindow(parent=None)
    window.show()
    return window, handler_spy


def locate_surface(window: Any, surface: Mapping[str, Any]) -> QWidget:
    tab = window.tab_widget.widget(
        {
            "import_export": 0,
            "export": 1,
            "info": 2,
            "material": 3,
            "bone": 4,
            "morph": 5,
            "display_pane": 6,
            "physics": 7,
            "settings": 8,
        }[surface["tab"]]
    )
    if "attribute" in surface:
        owner = window if surface["attribute"] in {"mainTabWidget", "export_tab"} else tab
        attribute = "tab_widget" if surface["attribute"] == "mainTabWidget" else surface["attribute"]
        widget = getattr(owner, attribute, None)
        if widget is None:
            widget = tab.findChild(QWidget, surface["attribute"])
    else:
        selector = surface["selector"]
        object_name = selector[len("objectName=") :] if selector.startswith("objectName=") else selector
        widget = getattr(window, "tab_widget", None) if selector == "mainTabWidget" else None
        if widget is None:
            widget = getattr(tab, selector, None)
        if widget is None:
            widget = tab if tab.objectName() == object_name else tab.findChild(QWidget, object_name)
    if widget is None:
        raise AssertionError("production widget not found: {}".format(surface["id"]))
    return widget


def _select_widget_path(window: Any, widget: QWidget) -> None:
    """Reach a control through production tab selectors without forcing visibility."""
    for stack in window.findChildren(CategoryStack):
        for index in range(stack.count()):
            page = stack.stacked_widget.widget(index)
            if page is widget or page.isAncestorOf(widget):
                stack.setCurrentIndex(index)
                break
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, CategoryStack):
            candidate = widget
            while candidate is not None and candidate.parentWidget() is not parent.stacked_widget:
                candidate = candidate.parentWidget()
            if candidate is not None:
                index = parent.stacked_widget.indexOf(candidate)
                if index >= 0:
                    parent.setCurrentIndex(index)
        if isinstance(parent, QTabWidget):
            page = next(
                (
                    parent.widget(index)
                    for index in range(parent.count())
                    if parent.widget(index) is widget or parent.widget(index).isAncestorOf(widget)
                ),
                None,
            )
            if page is not None:
                parent.setCurrentWidget(page)
        parent = parent.parentWidget()
    window.activateWindow()


def _prepare_surface(window: Any, surface: Mapping[str, Any], widget: QWidget) -> None:
    """Create the smallest ordinary view state in which the surface is usable."""
    tab = surface["tab"]
    view = window.tab_widget.widget(
        {
            "import_export": 0, "export": 1, "info": 2, "material": 3,
            "bone": 4, "morph": 5, "display_pane": 6, "physics": 7,
            "settings": 8,
        }[tab]
    )
    _select_widget_path(window, widget)
    if tab == "import_export":
        view.settings_service.set("ui.general.development_mode", True)
        view._apply_dev_mode_visibility()
        if surface["id"] in {
            "import_export.native_physics_bake",
            "import_export.reduce_bake_keys",
            "import_export.reduce_quality",
        }:
            view.bake_mode_check.setChecked(True)
        if surface["id"] == "import_export.reduce_quality":
            view.reduce_bake_keys_check.setChecked(True)
        if surface["id"] in {
            "import_export.custom_namespace",
            "import_export.namespace",
        }:
            view.use_namespace_check.setChecked(True)
        if surface["id"] == "import_export.namespace":
            view.custom_namespace_check.setChecked(True)
        if surface["id"] == "import_export.cpp_vp2_ownership":
            view.use_cpp_fast_load_check.setChecked(True)
        if surface["id"] == "import_export.vmd_rotation_time_curve":
            view.create_mmd_control_rig_check.setChecked(True)
            view.bake_mode_check.setChecked(False)
        if surface["id"] == "import_export.new_model":
            presenter = window.import_export_presenter
            presenter.create_model_action = SimpleNamespace(execute=lambda _request: None)
            presenter.model_template_loader = lambda: (
                SimpleNamespace(template_id="matrix", label="Matrix"),
            )
            presenter._populate_create_model_templates()
    elif tab == "export" and surface["id"].startswith("export.validation_"):
        from mmd_tools.validation.export_validator import (
            ExportValidationIssue,
            ExportValidationReport,
        )

        view.validation_console.set_report(
            ExportValidationReport(
                "pmx",
                (
                    ExportValidationIssue(
                        "BONES_EMPTY",
                        "warning",
                        False,
                        "materials[0]",
                        "matrix warning",
                    ),
                ),
            )
        )
    elif tab == "info":
        view.set_fields_enabled(True)
    elif tab == "material":
        view.material_list.addItem(QListWidgetItem("matrix-material"))
        view.material_list.setCurrentRow(0)
        view._set_details_enabled(True)
        for action in ("create", "duplicate", "delete", "move_up", "move_down"):
            view.authoring_toolbar.set_action_enabled(action, True, "", "")
    elif tab == "bone":
        view.bone_list.addItem(QListWidgetItem("matrix-bone"))
        view.bone_list.setCurrentRow(0)
        view.set_bone_details_enabled(True)
        for action in ("move_up", "move_down", "reset"):
            view.bone_authoring_toolbar.set_action_enabled(action, True, "", "")
        key = surface["id"].split(".", 1)[1]
        if key == "external_parent_key":
            view.external_parent_check.setChecked(True)
        if key.startswith("ik_") or key in {
            "select_ik_target", "add_ik_link", "remove_ik_link",
            "move_ik_link_up", "move_ik_link_down",
        }:
            view.ik_enabled_check.setChecked(True)
        if key in {"grant_parent", "select_grant_parent", "grant_rate", "local_grant"}:
            view.rotation_grant_check.setChecked(True)
        if key == "fixed_axis":
            view.fixed_axis_check.setChecked(True)
        if key in {"local_x_axis", "local_z_axis"}:
            view.local_axis_check.setChecked(True)
    elif tab == "morph":
        view.morph_list.addItem(QListWidgetItem("matrix-morph"))
        view.morph_list.setCurrentRow(0)
        view.set_morph_details_enabled(True)
        view.set_morph_controls_enabled(True)
        view.set_authoring_controls_enabled(True)
        view.set_work_material_controls(True, ((0, "matrix-offset"),))
    elif tab == "display_pane":
        view.frame_list.addItem(QListWidgetItem("matrix-frame"))
        view.frame_list.setCurrentRow(0)
        view.item_table.setRowCount(1)
        view.item_table.setItem(0, 0, QTableWidgetItem("matrix-item"))
        view.set_editor_enabled(True)
    elif tab == "physics":
        view.rigid_body_list.addItem(QListWidgetItem("matrix-rigid"))
        view.joint_list.addItem(QListWidgetItem("matrix-joint"))
        kind = "joint" if surface["id"].startswith("physics.joint_") else "rigid"
        view.list_tabs.setCurrentIndex(1 if kind == "joint" else 0)
        view.set_physics_form(kind, {})
        view.set_physics_details_enabled(True)
        window.physics_presenter._set_apply_reset_enabled(True)
        if surface["id"] == "physics.enable_physics":
            presenter = window.physics_presenter
            presenter._find_physics_world_shape = lambda: "|matrixWorld|matrixWorldShape"
            presenter._world_solvers = lambda _world: ("matrixSolver",)
            presenter._sync_physics_enable_checkbox()
    elif tab == "settings":
        window.settings_presenter._refresh_dev_tools_visibility(True)


def _interaction(widget: QWidget, kind: str) -> Tuple[str, Any]:
    """Return one real Qt interaction for the selected production control."""
    if isinstance(widget, CategoryStack):
        target = 1 if widget.currentIndex() == 0 else 0
        return "setCurrentIndex", lambda: widget.setCurrentIndex(target)
    if isinstance(widget, QPushButton):
        return "mouseClick", widget.click
    if isinstance(widget, QCheckBox):
        return "mouseClick", widget.click
    if isinstance(widget, QComboBox):
        if widget.count() < 2:
            widget.addItems(("matrix-a", "matrix-b"))
        target = 0 if widget.currentIndex() else 1
        return "setCurrentIndex", lambda: widget.setCurrentIndex(target)
    if isinstance(widget, (QLineEdit, QTextEdit)):
        if isinstance(widget, QTextEdit):
            value = "matrix-value" if widget.toPlainText() != "matrix-value" else "matrix-other"
            return "setPlainText", lambda: widget.setPlainText(value)
        value = "matrix-value" if widget.text() != "matrix-value" else "matrix-other"
        return "setText", lambda: widget.setText(value)
    if isinstance(widget, QAbstractSpinBox):
        current = widget.value()
        step = widget.singleStep()
        target = current + step if current + step <= widget.maximum() else current - step
        return "setValue", lambda: widget.setValue(target)
    if isinstance(widget, QSlider):
        target = widget.value() + 1 if widget.value() < widget.maximum() else widget.value() - 1
        return "setValue", lambda: widget.setValue(target)
    if isinstance(widget, QListWidget):
        if widget.count() == 0:
            widget.addItem(QListWidgetItem("matrix-item"))
        target = 0 if widget.currentRow() != 0 else (-1 if widget.count() else 0)
        return "setCurrentRow", lambda: widget.setCurrentRow(target)
    if isinstance(widget, QTableWidget):
        if widget.rowCount() == 0:
            widget.setRowCount(1)
        if widget.columnCount() == 0:
            widget.setColumnCount(1)
        if widget.item(0, 0) is None:
            widget.setItem(0, 0, QTableWidgetItem("matrix-item"))
        target = (0, 0) if widget.currentRow() != 0 else (-1, -1)
        return "setCurrentCell", lambda: widget.setCurrentCell(*target)
    if isinstance(widget, QTabWidget):
        if widget.count() < 2:
            return "setCurrentIndex", lambda: (
                widget.setCurrentIndex(-1), widget.setCurrentIndex(0)
            )
        target = 0 if widget.currentIndex() else 1
        return "setCurrentIndex", lambda: widget.setCurrentIndex(target)
    if hasattr(widget, "valueChanged") and hasattr(widget, "spins"):
        spin = widget.spins[0]
        target = spin.value() + spin.singleStep()
        return "setVectorComponent", lambda: spin.setValue(target)
    if hasattr(widget, "valueChanged") and hasattr(widget, "buttons"):
        return "clickGroup", widget.buttons[0].click
    if hasattr(widget, "valueChanged") and hasattr(widget, "value") and hasattr(widget, "setValue"):
        target = float(widget.value()) + 0.1
        return "setValue", lambda: widget.setValue(target)
    if kind == "color":
        return "mousePressEvent", lambda: widget.mousePressEvent(None)
    raise AssertionError("no bounded production interaction for {} ({})".format(widget, kind))


def exercise_surface(
    window: Any,
    surface: Mapping[str, Any],
    qapp: Any,
    handler_spy: PreconstructionMethodSpy,
) -> Dict[str, Any]:
    """Exercise exactly one production control and return its measured witness."""
    widget = locate_surface(window, surface)
    qapp.processEvents()
    _prepare_surface(window, surface, widget)
    interaction_control = widget
    control_ready = bool(widget.isVisible() and widget.isEnabled())
    interaction_ready = control_ready
    if surface["kind"] == "tab_selector" and not isinstance(widget, QTabWidget):
        interaction_control = window.tab_widget
        interaction_ready = bool(
            interaction_control.isVisible() and interaction_control.isEnabled()
        )
        target = window.tab_widget.indexOf(widget)
        other = 0 if target != 0 else 1
        window.tab_widget.setCurrentIndex(other)
        interaction = "setCurrentIndex"
        def invoke():
            window.tab_widget.setCurrentIndex(target)
    else:
        interaction, invoke = _interaction(widget, surface["kind"])
    handler_spy.source_control = interaction_control
    before = handler_spy.action_count
    invoke()
    qapp.processEvents()
    if handler_spy.action_count == before and surface["expected_handler"].endswith(
        (
            ".import_file", ".import_vmd_file", ".apply_changes", ".apply",
            ".save_all_settings",
        )
    ):
        if surface["tab"] == "import_export":
            window.import_export_presenter.view.import_path_edit.clear()
            window.import_export_presenter.view.vmd_path_edit.clear()
        commit_control = {
            "import_export": window.import_export_presenter.view.import_vmd_button
            if surface["expected_handler"].endswith(".import_vmd_file")
            else window.import_export_presenter.view.import_button,
            "bone": window.bone_presenter.view.apply_btn,
            "morph": window.morph_presenter.view.apply_btn,
            "display_pane": window.display_pane_presenter.view.apply_btn,
            "settings": window.settings_presenter.view.save_settings_btn,
        }[surface["tab"]]
        interaction_control = commit_control
        interaction_ready = bool(
            commit_control.isVisible() and commit_control.isEnabled()
        )
        handler_spy.source_control = commit_control
        commit_control.click()
        qapp.processEvents()
        interaction += "; {}".format(commit_control.objectName() or "commit")
    if before:
        del handler_spy.calls[:before]
    qapp.processEvents()
    locator_key = "selector" if "selector" in surface else "attribute"
    return build_surface_witness(
        surface_id=surface["id"],
        case_id=HEADLESS_CASE_ID,
        interaction="{}({})".format(interaction, surface[locator_key]),
        oracle="production_handler_dispatched_once",
        action_spy=handler_spy,
        control=widget,
        interaction_control=interaction_control,
        control_ready=control_ready,
        interaction_ready=interaction_ready,
        **{locator_key: surface[locator_key]},
    )
