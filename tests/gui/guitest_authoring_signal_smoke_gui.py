"""Structured real-Qt smoke for model-authoring signals and Maya persistence."""

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.adapters.maya_authoring_e2e import normalize_spec_payload
from mmd_tools.converters.export_scene_collector import ExportSceneCollector
from mmd_tools.core import model_registry
from mmd_tools.core.constants import (
    ATTR_MMD_AXIS_DIRECTION,
    ATTR_MMD_BONE_FLAGS,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_DEFORM_LAYER,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_EXTERNAL_PARENT_KEY,
    ATTR_MMD_FIXED_AXIS,
    ATTR_MMD_GRANT_PARENT,
    ATTR_MMD_GRANT_PARENT_INDEX,
    ATTR_MMD_GRANT_RATE,
    ATTR_MMD_IK_LINKS,
    ATTR_MMD_IK_LOOP,
    ATTR_MMD_IK_LIMIT_ANGLE,
    ATTR_MMD_IK_TARGET,
    ATTR_MMD_IK_TARGET_INDEX,
    ATTR_MMD_LOCAL_X_AXIS,
    ATTR_MMD_LOCAL_Z_AXIS,
    ATTR_MMD_MATERIAL_NAME_EN,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_X_AXIS_DIRECTION,
    ATTR_MMD_Z_AXIS_DIRECTION,
)
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.pmx_data.bone import PmxBoneFlag
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.io.pmx_exporter import PmxExporter
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QColor, QMessageBox, QT_BINDING, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin
from tests.common.ui_action_coverage import ActionInvocationSpy, QtSignalInvocationSpy

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
else:
    from PySide2.QtTest import QTest
    from PySide2.QtWidgets import QStyle, QStyleOptionSlider


def _canonical_payload(window, root):
    spec = normalize_spec_payload(window.authoring_composition.coordinator.read_spec(root))
    display = display_frames_from_json(cmds.getAttr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))
    return {"spec": spec, "display_frames": display}


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_value(value):
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _node_footprint(node):
    """Capture serialisable user attrs and DG edges for one live node."""
    attributes = {}
    for attribute in cmds.listAttr(node, userDefined=True) or []:
        try:
            attributes[attribute] = _json_value(cmds.getAttr(f"{node}.{attribute}"))
        except Exception:
            continue
    raw_connections = cmds.listConnections(
        node,
        source=True,
        destination=True,
        connections=True,
        plugs=True,
    ) or []
    connections = sorted(
        [str(raw_connections[index]), str(raw_connections[index + 1])]
        for index in range(0, len(raw_connections) - 1, 2)
    )
    return {"node": node, "attributes": attributes, "connections": connections}


def _footprint_delta(before, after):
    before_attrs = before["attributes"]
    after_attrs = after["attributes"]
    changed_attrs = sorted(
        key
        for key in set(before_attrs) | set(after_attrs)
        if before_attrs.get(key) != after_attrs.get(key)
    )
    before_edges = {tuple(edge) for edge in before["connections"]}
    after_edges = {tuple(edge) for edge in after["connections"]}
    return {
        "node": after["node"],
        "attributes_changed": changed_attrs,
        "connections_added": [list(edge) for edge in sorted(after_edges - before_edges)],
        "connections_removed": [list(edge) for edge in sorted(before_edges - after_edges)],
    }


def _changed_spec_sections(before, after):
    return sorted(
        key
        for key in set(before["spec"]) | set(after["spec"])
        if key != "fingerprint"
        if before["spec"].get(key) != after["spec"].get(key)
    )


def _semantic_topology(window, root):
    """Capture ownership topology by semantic role, never by Maya node name."""
    spec = window.authoring_composition.coordinator.read_spec(root)
    registry = model_registry.get_model_registry(root)
    categories = {}
    for category in (model_registry.REGISTRY_CATEGORY_MATERIAL, model_registry.REGISTRY_CATEGORY_MORPH):
        members = model_registry.list_model_registry_members(root, category) or []
        categories[category] = sorted(cmds.nodeType(node) for node in members)
    return {
        "root_type": cmds.nodeType(root),
        "registry_type": cmds.nodeType(registry),
        "bone_types": [cmds.nodeType(item.binding_identity) for item in spec.bones],
        "material_types": [cmds.nodeType(item.binding_identity) for item in spec.materials],
        "morph_types": [cmds.nodeType(item.binding_identity) for item in spec.morphs],
        "registry_members": categories,
        "mesh_shapes": len(cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []),
    }


@requires_gui
class TestAuthoringSignalSmokeGUI(GuiTestBase):
    """Exercise actual widget signals against the generated basic template."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(Path(__file__).resolve().parents[2], cmds_module=cmds)
        self.window = MainWindow()
        composition = self.window.authoring_composition
        self.assertIsNotNone(composition, "production authoring composition unavailable")
        self.template = composition.model_initializer.create(
            "pmx20-basic-v1", "UI Smoke JP", "UI Smoke EN"
        )
        self.root = self.template.root
        self.window.show()
        self.status_messages = []
        self.window.app_state.status_message.connect(self.status_messages.append)
        self.window.app_state.current_model_root = self.root
        QApplication.processEvents()
        self.report = {
            "schema_version": 1,
            "gate_id": "V070-UI-AUTHORING-SMOKE-1",
            "maya_version": str(cmds.about(version=True)),
            "fixture": "pmx20-basic-v1",
            "cases": [],
            "surfaces": [],
            "status": "running",
        }
        self._surface_action_spies = {}

    def tearDown(self):
        try:
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
        finally:
            super().tearDown()

    def _record(self, case_id, callback):
        evidence = {"id": case_id, "status": "running"}
        self.report["cases"].append(evidence)
        try:
            callback(evidence)
        except Exception as exc:
            evidence["status"] = "fail"
            evidence["error"] = str(exc)
            self.report["status"] = "fail"
            self._write_report()
            raise
        evidence["status"] = "pass"

    def _write_report(self):
        output = (
            Path(__file__).resolve().parents[2]
            / "build"
            / "reports"
            / f"ui_authoring_signal_smoke_maya{cmds.about(version=True)}.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    def _register_second_bone_fixture(self):
        """Register one deterministic descendant joint through production APIs."""
        coordinator = self.window.authoring_composition.coordinator
        presenter = self.window.bone_presenter
        joint = cmds.createNode("joint", name="uiCoverageSecondBone", parent=self.root)
        cmds.xform(joint, translation=(0.0, 2.0, 0.0), worldSpace=True)
        cmds.select(joint, replace=True)
        registered = coordinator.register_selected_joint(self.root, joint)
        self.assertEqual(registered.binding_identity, cmds.ls(joint, long=True)[0])
        presenter.load_bones()
        QApplication.processEvents()
        self.assertEqual(len(presenter.all_bones), 2)
        self.assertEqual(len(presenter._registered_indices), 2)
        return tuple(presenter.all_bones)

    def _register_third_bone_fixture(self):
        """Register two deterministic descendants for the IK structural case."""
        bindings = list(self._register_second_bone_fixture())
        coordinator = self.window.authoring_composition.coordinator
        presenter = self.window.bone_presenter
        joint = cmds.createNode("joint", name="uiCoverageThirdBone", parent=self.root)
        cmds.xform(joint, translation=(0.0, -2.0, 0.0), worldSpace=True)
        cmds.select(joint, replace=True)
        registered = coordinator.register_selected_joint(self.root, joint)
        self.assertEqual(registered.binding_identity, cmds.ls(joint, long=True)[0])
        presenter.load_bones()
        QApplication.processEvents()
        self.assertEqual(len(presenter.all_bones), 3)
        self.assertEqual(len(presenter._registered_indices), 3)
        bindings.append(registered.binding_identity)
        return tuple(bindings)

    def _bone_maya_fingerprint(self):
        spec = self.window.authoring_composition.coordinator.read_spec(self.root)
        return {
            bone.binding_identity: _node_footprint(bone.binding_identity)
            for bone in spec.bones
            if bone.binding_identity
        }

    def _emit_surface_witness(
        self,
        surface_id,
        case_id,
        *,
        interaction,
        fired_action,
        oracle,
        action_count=None,
        selector=None,
        attribute=None,
    ):
        """Emit one gate-compatible witness after the semantic oracle passes."""
        if action_count is None:
            action_spy, _control = self._surface_action_spies[surface_id]
            action_count = action_spy.action_count
        self.assertEqual(action_count, 1, f"{surface_id} action must fire exactly once")
        locators = [value for value in (selector, attribute) if value is not None]
        self.assertEqual(len(locators), 1, "runtime witness requires exactly one locator")
        surface_witness = {
            "surface_id": surface_id,
            "case_id": case_id,
            "status": "pass",
            "runtime_witness": {
                "interaction": interaction,
                "fired_action": fired_action,
                "oracle": oracle,
                "action_count": int(action_count),
            },
        }
        if selector is not None:
            surface_witness["selector"] = selector
        else:
            surface_witness["attribute"] = attribute
        self.report["surfaces"].append(surface_witness)
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(surface_witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def _observe_surface_signal(self, surface_id, action_name, control, signal=None):
        action_spy = QtSignalInvocationSpy(
            action_name, signal or control.clicked, control
        )
        self._surface_action_spies[surface_id] = (action_spy, control)
        return action_spy

    def _emit_bone_action_witness(
        self, surface_id, selector, fired_action, oracle, action_count
    ):
        runtime_witness = {
            "interaction": f"QTest.mouseClick({selector})",
            "fired_action": fired_action,
            "oracle": oracle,
            "action_count": action_count,
        }
        surface_witness = {
            "surface_id": surface_id,
            "case_id": "gui.bone_actions",
            "selector": selector,
            "status": "pass",
            "runtime_witness": runtime_witness,
        }
        self.report["surfaces"].append(surface_witness)
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(surface_witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def _emit_bone_value_surface_witnesses(self, surface_ids, oracle, action_count):
        """Emit formal per-control witnesses after a complete value apply oracle."""
        selectors = {
            "bone.name_jp": "objectName=boneNameJpEdit",
            "bone.deform_layer": "objectName=boneDeformLayerSpin",
            "bone.rotatable": "objectName=boneRotatableCheck",
            "bone.movable": "objectName=boneMovableCheck",
            "bone.visible": "objectName=boneVisibleCheck",
            "bone.enabled": "objectName=boneEnabledCheck",
            "bone.after_physics": "objectName=boneAfterPhysicsCheck",
            "bone.fixed_axis_enabled": "objectName=boneFixedAxisCheck",
            "bone.fixed_axis": "objectName=boneFixedAxisXSpin",
            "bone.local_axis_enabled": "objectName=boneLocalAxisCheck",
            "bone.local_x_axis": "objectName=boneLocalXAxisXSpin",
            "bone.local_z_axis": "objectName=boneLocalZAxisXSpin",
        }
        for surface_id in surface_ids:
            selector = selectors[surface_id]
            surface_witness = {
                "surface_id": surface_id,
                "case_id": "gui.bone_apply_values",
                "selector": selector,
                "status": "pass",
                "runtime_witness": {
                    "interaction": (
                        f"Qt edit({selector}); QTest.mouseClick(objectName=boneApplyButton, Qt.LeftButton)"
                    ),
                    "fired_action": "MayaModelAuthoringCoordinator.apply_bone_value_patch",
                    "oracle": oracle,
                    "action_count": action_count,
                },
            }
            self.report["surfaces"].append(surface_witness)
            print(
                "[UI COVERAGE WITNESS] "
                + json.dumps(
                    surface_witness,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )

    def _emit_bone_structural_surface_witnesses(self, surface_ids, oracle, action_count):
        """Emit per-control witnesses after a complete structural Apply oracle."""
        selectors = {
            "bone.external_parent": "objectName=boneExternalParentCheck",
            "bone.external_parent_key": "objectName=boneExternalParentKeySpin",
            "bone.ik_enabled": "objectName=boneIkEnabledCheck",
            "bone.ik_target": "objectName=boneIkTargetEdit",
            "bone.ik_loop": "objectName=boneIkLoopSpin",
            "bone.ik_limit_angle": "objectName=boneIkLimitAngleSpin",
            "bone.ik_links": "objectName=boneIkLinksTable",
            "bone.rotation_grant": "objectName=boneRotationGrantCheck",
            "bone.move_grant": "objectName=boneMoveGrantCheck",
            "bone.grant_parent": "objectName=boneGrantParentEdit",
            "bone.grant_rate": "objectName=boneGrantRateSpin",
            "bone.local_grant": "objectName=boneLocalGrantCheck",
        }
        for surface_id in surface_ids:
            selector = selectors[surface_id]
            surface_witness = {
                "surface_id": surface_id,
                "case_id": "gui.bone_apply_structural",
                "selector": selector,
                "status": "pass",
                "runtime_witness": {
                    "interaction": (
                        f"Qt edit({selector}); QTest.mouseClick(objectName=boneApplyButton, Qt.LeftButton)"
                    ),
                    "fired_action": "MayaModelAuthoringCoordinator.replace_bone_semantic",
                    "oracle": oracle,
                    "action_count": action_count,
                },
            }
            self.report["surfaces"].append(surface_witness)
            print(
                "[UI COVERAGE WITNESS] "
                + json.dumps(
                    surface_witness,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )

    def test_authoring_signals_undo_redo_and_save_reopen(self):
        self._record("authoring.material.value_apply", self._material_case)
        self._record("authoring.bone.value_apply", self._bone_case)
        self._record("authoring.morph.create", self._morph_case)
        self._record("authoring.display_frame.apply", self._display_case)
        self._record("authoring.save_reopen", self._save_reopen_case)
        material_tab_index = self.window.tab_widget.indexOf(self.window.material_presenter.view)
        import_export_tab_index = self.window.tab_widget.indexOf(self.window.import_export_tab)
        self.assertGreaterEqual(material_tab_index, 0)
        self.assertGreaterEqual(import_export_tab_index, 0)
        self.window.tab_widget.setCurrentIndex(material_tab_index)
        QApplication.processEvents()
        tab_changes = []

        def observe_tab_change(index):
            tab_changes.append(index)

        self.window.tab_widget.currentChanged.connect(observe_tab_change)
        try:
            self.window.tab_widget.setCurrentIndex(import_export_tab_index)
            QApplication.processEvents()
        finally:
            self.window.tab_widget.currentChanged.disconnect(observe_tab_change)
        self.assertEqual(tab_changes, [import_export_tab_index])
        self.assertIs(self.window.tab_widget.currentWidget(), self.window.import_export_tab)
        self._emit_surface_witness(
            "import_export.main_tab_widget",
            "gui.authoring_signal_smoke",
            selector="mainTabWidget",
            interaction="QTabWidget.setCurrentIndex(mainTabWidget, import_export)",
            fired_action="MainWindow._on_main_tab_changed",
            oracle="currentWidget_is_import_export_tab",
            action_count=len(tab_changes),
        )
        self.report["status"] = "pass"
        self._write_report()

    def test_material_value_controls_apply_reset_and_undo(self):
        """Exercise every non-texture Material value control through Qt signals."""
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)

        def observe(surface_id, action_spy, control):
            self._surface_action_spies[surface_id] = (action_spy, control)

        observe(
            "material.name_jp",
            QtSignalInvocationSpy(
                "MaterialPresenter._on_value_changed", view.material_jp_name_edit.textChanged
                , view.material_jp_name_edit
            ),
            view.material_jp_name_edit,
        )
        for surface_id, swatch, color_name in (
            ("material.diffuse_color", view.diffuse_color_widget, "diffuse"),
            ("material.specular_color", view.specular_color_widget, "specular"),
            ("material.ambient_color", view.ambient_color_widget, "ambient"),
            ("material.edge_color", view.edge_color_widget, "edge"),
        ):
            swatch_spy = ActionInvocationSpy.wrap(
                "MaterialPresenter.pick_color('{}')".format(color_name),
                swatch.mousePressEvent,
                swatch,
            )
            swatch.mousePressEvent = swatch_spy
            observe(surface_id, swatch_spy, swatch)
        flag_values = (
            (view.both_face_check, True),
            (view.ground_shadow_check, False),
            (view.self_shadow_map_check, True),
            (view.self_shadow_check, False),
            (view.edge_draw_check, True),
            (view.vertex_color_check, False),
            (view.point_draw_check, True),
            (view.line_draw_check, False),
        )
        for surface_id, control, signal in (
            ("material.transparency", view.transparency_spin, view.transparency_spin.valueChanged),
            (
                "material.specular_coefficient",
                view.specular_coefficient_spin,
                view.specular_coefficient_spin.valueChanged,
            ),
            ("material.double_sided", view.both_face_check, view.both_face_check.stateChanged),
            (
                "material.ground_shadow",
                view.ground_shadow_check,
                view.ground_shadow_check.stateChanged,
            ),
            (
                "material.self_shadow_map",
                view.self_shadow_map_check,
                view.self_shadow_map_check.stateChanged,
            ),
            (
                "material.self_shadow",
                view.self_shadow_check,
                view.self_shadow_check.stateChanged,
            ),
            ("material.edge_draw", view.edge_draw_check, view.edge_draw_check.stateChanged),
            (
                "material.vertex_color",
                view.vertex_color_check,
                view.vertex_color_check.stateChanged,
            ),
            ("material.point_draw", view.point_draw_check, view.point_draw_check.stateChanged),
            ("material.line_draw", view.line_draw_check, view.line_draw_check.stateChanged),
            ("material.edge_size", view.edge_size_spin, view.edge_size_spin.valueChanged),
        ):
            for flag_control, expected in flag_values:
                if control is flag_control:
                    control.setChecked(not expected)
                    break
            observe(
                surface_id,
                QtSignalInvocationSpy(
                    "MaterialPresenter._on_value_changed", signal, control
                ),
                control,
            )

        view.material_jp_name_edit.setText("UI材質")
        view.material_en_name_edit.setText("UI Material Values")
        chosen_colors = (
            QColor(51, 102, 153),
            QColor(25, 50, 75),
            QColor(10, 20, 30),
            QColor(80, 90, 100),
        )
        with patch(
            "mmd_tools.ui.presenters.material_presenter.QColorDialog.getColor",
            side_effect=chosen_colors,
        ):
            for swatch in (
                view.diffuse_color_widget,
                view.specular_color_widget,
                view.ambient_color_widget,
                view.edge_color_widget,
            ):
                QTest.mouseClick(swatch, Qt.LeftButton)
        for key, expected in (
            ("diffuse", (0.2, 0.4, 0.6)),
            ("specular", (25 / 255.0, 50 / 255.0, 75 / 255.0)),
            ("ambient", (10 / 255.0, 20 / 255.0, 30 / 255.0)),
            ("edge_color", (80 / 255.0, 90 / 255.0, 100 / 255.0)),
        ):
            for actual, channel in zip(self.window.material_presenter.material_data[key], expected):
                self.assertAlmostEqual(actual, channel, places=6, msg=key)
        view.transparency_spin.setValue(0.25)
        view.specular_coefficient_spin.setValue(0.6)
        for control, checked in flag_values:
            control.setChecked(checked)
        view.edge_size_spin.setValue(1.25)
        # Freeze each direct-edit delta before Apply/Reset repopulates the widgets.
        # Those refresh emissions are lifecycle noise, not another user action.
        for action_spy, _control in self._surface_action_spies.values():
            if isinstance(action_spy, QtSignalInvocationSpy):
                action_spy.stop()
        view.apply_btn.click()
        QApplication.processEvents()

        after = _canonical_payload(self.window, self.root)
        material = after["spec"]["materials"][0]
        self.assertEqual(material["name"], "UI材質")
        self.assertEqual(material["name_english"], "UI Material Values")
        for actual, expected in zip(material["diffuse"], (0.2, 0.4, 0.6, 0.75)):
            self.assertAlmostEqual(actual, expected, places=6)
        for key, expected in (
            ("specular", (25 / 255.0, 50 / 255.0, 75 / 255.0)),
            ("ambient", (10 / 255.0, 20 / 255.0, 30 / 255.0)),
        ):
            for actual, channel in zip(material[key], expected):
                self.assertAlmostEqual(actual, channel, places=6)
        for actual, expected in zip(material["edge_color"][:3], (80 / 255.0, 90 / 255.0, 100 / 255.0)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertAlmostEqual(material["specular_coefficient"], 0.6)
        self.assertAlmostEqual(material["edge_size"], 1.25)
        self.assertEqual(material["draw_flags"], 0x55)
        self.assertEqual(_changed_spec_sections(before, after), ["materials"])

        search_spy = QtSignalInvocationSpy(
            "MaterialPresenter.on_search_text_changed",
            view.search_edit.textChanged,
            view.search_edit,
        )
        observe("material.search", search_spy, view.search_edit)
        view.search_edit.setText("UI材質")
        QApplication.processEvents()
        self.assertFalse(view.material_list.item(0).isHidden())
        search_spy.stop()
        view.search_edit.clear()
        view.material_jp_name_edit.setText("discarded")
        view.transparency_spin.setValue(0.9)
        reset_spy = QtSignalInvocationSpy(
            "MaterialPresenter.reset_changes", view.reset_btn.clicked, view.reset_btn
        )
        observe("material.reset", reset_spy, view.reset_btn)
        view.reset_btn.click()
        QApplication.processEvents()
        self.assertEqual(view.material_jp_name_edit.text(), "UI材質")
        self.assertAlmostEqual(view.transparency_spin.value(), 0.25)

        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        value_oracle = "material_spec_values_draw_flags_colors_undo_redo"
        self._emit_surface_witness(
            "material.name_jp",
            "gui.material_value_controls",
            selector="objectName=materialNameJpEdit",
            interaction="QLineEdit.setText(objectName=materialNameJpEdit, 'UI材質'); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.diffuse_color",
            "gui.material_value_controls",
            selector="objectName=diffuseColorSwatch",
            interaction="QTest.mouseClick(objectName=diffuseColorSwatch); choose QColor; Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter.pick_color('diffuse')",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.transparency",
            "gui.material_value_controls",
            selector="objectName=materialTransparencySpin",
            interaction="QDoubleSpinBox.setValue(objectName=materialTransparencySpin, 0.25); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.specular_color",
            "gui.material_value_controls",
            selector="objectName=specularColorSwatch",
            interaction="QTest.mouseClick(objectName=specularColorSwatch); choose QColor; Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter.pick_color('specular')",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.specular_coefficient",
            "gui.material_value_controls",
            selector="objectName=materialSpecularCoefficientSpin",
            interaction="QDoubleSpinBox.setValue(objectName=materialSpecularCoefficientSpin, 0.6); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.ambient_color",
            "gui.material_value_controls",
            selector="objectName=ambientColorSwatch",
            interaction="QTest.mouseClick(objectName=ambientColorSwatch); choose QColor; Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter.pick_color('ambient')",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.double_sided",
            "gui.material_value_controls",
            selector="objectName=materialDoubleSidedCheck",
            interaction="QCheckBox.setChecked(objectName=materialDoubleSidedCheck, True); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.ground_shadow",
            "gui.material_value_controls",
            selector="objectName=materialGroundShadowCheck",
            interaction="QCheckBox.setChecked(objectName=materialGroundShadowCheck, False); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.self_shadow_map",
            "gui.material_value_controls",
            selector="objectName=materialSelfShadowMapCheck",
            interaction="QCheckBox.setChecked(objectName=materialSelfShadowMapCheck, True); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.self_shadow",
            "gui.material_value_controls",
            selector="objectName=materialSelfShadowCheck",
            interaction="QCheckBox.setChecked(objectName=materialSelfShadowCheck, False); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.edge_draw",
            "gui.material_value_controls",
            selector="objectName=materialEdgeDrawCheck",
            interaction="QCheckBox.setChecked(objectName=materialEdgeDrawCheck, True); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.vertex_color",
            "gui.material_value_controls",
            selector="objectName=materialVertexColorCheck",
            interaction="QCheckBox.setChecked(objectName=materialVertexColorCheck, False); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.point_draw",
            "gui.material_value_controls",
            selector="objectName=materialPointDrawCheck",
            interaction="QCheckBox.setChecked(objectName=materialPointDrawCheck, True); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.line_draw",
            "gui.material_value_controls",
            selector="objectName=materialLineDrawCheck",
            interaction="QCheckBox.setChecked(objectName=materialLineDrawCheck, False); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.edge_color",
            "gui.material_value_controls",
            selector="objectName=edgeColorSwatch",
            interaction="QTest.mouseClick(objectName=edgeColorSwatch); choose QColor; Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter.pick_color('edge')",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.edge_size",
            "gui.material_value_controls",
            selector="objectName=materialEdgeSizeSpin",
            interaction="QDoubleSpinBox.setValue(objectName=materialEdgeSizeSpin, 1.25); Apply + Maya Undo/Redo",
            fired_action="MaterialPresenter._on_value_changed",
            oracle=value_oracle,
        )
        self._emit_surface_witness(
            "material.search",
            "gui.material_value_controls",
            selector="objectName=materialSearchEdit",
            interaction="QLineEdit.setText(objectName=materialSearchEdit, 'UI材質')",
            fired_action="MaterialPresenter.on_search_text_changed",
            oracle="material_list_filter_keeps_matching_material_visible",
        )
        self._emit_surface_witness(
            "material.reset",
            "gui.material_value_controls",
            selector="objectName=materialResetButton",
            interaction="QTest.mouseClick(objectName=materialResetButton)",
            fired_action="MaterialPresenter.reset_changes",
            oracle="pending_material_edit_reset_to_last_applied_values",
        )

    def test_dx11_material_value_apply_undo_redo(self):
        """Apply name, diffuse, and main-texture edits through the DX11 route."""
        from mmd_tools.core import settings
        from mmd_tools.io.mmd_importer import import_mmd_file

        cmds.file(new=True, force=True)
        cmds.loadPlugin("dx11Shader", quiet=True)
        fixture = Path(__file__).resolve().parents[1] / "data" / "mmt_test_model.pmx"
        previous_create = settings.get("import.model.create_mmd_shaders")
        previous_backend = settings.get("import.model.mmd_shader_backend")
        try:
            settings.set("import.model.create_mmd_shaders", True)
            settings.set("import.model.mmd_shader_backend", "dx11")
            root = import_mmd_file(
                str(fixture),
                options={
                    "scale": 1.0,
                    "import_physics": False,
                    "setup_rig": False,
                    "setup_bone_orientation": False,
                    "create_mmd_control_rig": False,
                    "create_mmd_shaders": True,
                    "use_cpp_fast_load": False,
                    "use_native_pmx_parse": False,
                    "require_native_pmx_parse": False,
                },
            )
        finally:
            settings.set("import.model.create_mmd_shaders", previous_create)
            settings.set("import.model.mmd_shader_backend", previous_backend)
        self.assertTrue(root)
        self.root = str(root)
        self.window.app_state.current_model_root = self.root
        QApplication.processEvents()

        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        material = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
        shader = material.binding_identity
        self.assertEqual(cmds.nodeType(shader), "dx11Shader")
        self.assertFalse(cmds.attributeQuery("baseColor", node=shader, exists=True))
        self.assertTrue(cmds.attributeQuery("DiffuseColorRGB", node=shader, exists=True))

        def outline_state():
            result = {}
            for attr in (
                "technique",
                "EdgeSize",
                "mmd_shader_outline_enabled",
                "mmdDoubleSided",
            ):
                exists = cmds.attributeQuery(attr, node=shader, exists=True)
                result[attr] = {
                    "exists": bool(exists),
                    "value": cmds.getAttr(f"{shader}.{attr}") if exists else None,
                }
            return result

        before = _canonical_payload(self.window, self.root)
        before_outline = outline_state()
        outline_enabled = not view.shader_outline_check.isChecked()
        view.material_en_name_edit.setText("DX11 Material Edited")
        view.shader_outline_check.setChecked(outline_enabled)
        view.apply_btn.click()
        QApplication.processEvents()
        after_name = _canonical_payload(self.window, self.root)
        after_outline = outline_state()
        self.assertEqual(after_name["spec"]["materials"][0]["name_english"], "DX11 Material Edited")
        self.assertEqual(
            bool(after_outline["mmd_shader_outline_enabled"]["value"]),
            outline_enabled,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(outline_state(), before_outline)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_name)
        self.assertEqual(outline_state(), after_outline)

        with patch(
            "mmd_tools.ui.presenters.material_presenter.QColorDialog.getColor",
            return_value=QColor(64, 128, 192),
        ):
            QTest.mouseClick(view.diffuse_color_widget, Qt.LeftButton)
        view.apply_btn.click()
        QApplication.processEvents()
        after_diffuse = _canonical_payload(self.window, self.root)
        expected = (64 / 255.0, 128 / 255.0, 192 / 255.0)
        for actual, channel in zip(after_diffuse["spec"]["materials"][0]["diffuse"][:3], expected):
            self.assertAlmostEqual(actual, channel, places=6)
        for actual, channel in zip(cmds.getAttr(f"{shader}.DiffuseColorRGB")[0], expected):
            self.assertAlmostEqual(actual, channel, places=6)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_name)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_diffuse)

        def main_texture_state():
            sources = cmds.listConnections(
                f"{shader}.MainTexture",
                source=True,
                destination=False,
                plugs=True,
            ) or []
            source = str(sources[0]) if sources else None
            file_node = source.rsplit(".", 1)[0] if source and "." in source else None
            return {
                "source": source,
                "file_node": file_node,
                "file_node_type": cmds.nodeType(file_node) if file_node else None,
                "has_main_texture": int(cmds.getAttr(f"{shader}.HasMainTexture")),
            }

        before_texture = _canonical_payload(self.window, self.root)
        before_texture_state = main_texture_state()
        texture_path = (Path(__file__).resolve().parents[1] / "data" / "tex" / "diffuse.png").resolve()
        view.texture_path_edit.setText(str(texture_path))
        view.apply_btn.click()
        QApplication.processEvents()
        after_texture = _canonical_payload(self.window, self.root)
        after_texture_state = main_texture_state()
        self.assertEqual(
            after_texture["spec"]["materials"][0]["resolved_texture_path"],
            str(texture_path),
        )
        self.assertIsNotNone(after_texture_state["source"])
        self.assertEqual(after_texture_state["file_node_type"], "file")
        self.assertEqual(after_texture_state["has_main_texture"], 1)
        self.assertFalse(cmds.attributeQuery("baseColor", node=shader, exists=True))
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_texture)
        self.assertEqual(main_texture_state(), before_texture_state)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_texture)
        self.assertEqual(main_texture_state(), after_texture_state)

        sphere_path = (Path(__file__).resolve().parents[1] / "data" / "tex" / "sph.png").resolve()
        view.sphere_map_path_edit.setText(str(sphere_path))
        view.sphere_mode_combo.setCurrentIndex(1)
        view.apply_btn.click()
        QApplication.processEvents()
        after_sphere = _canonical_payload(self.window, self.root)
        sphere_sources = cmds.listConnections(
            f"{shader}.SphereTexture",
            source=True,
            destination=False,
            plugs=True,
        ) or []
        self.assertEqual(
            after_sphere["spec"]["materials"][0]["resolved_sphere_texture_path"],
            str(sphere_path),
        )
        self.assertEqual(len(sphere_sources), 1)
        self.assertEqual(cmds.nodeType(sphere_sources[0].rsplit(".", 1)[0]), "file")
        self.assertEqual(cmds.getAttr(f"{shader}.HasSphereTexture"), 1)
        self.assertEqual(cmds.getAttr(f"{shader}.SphereMode"), 1)
        self.assertEqual(main_texture_state(), after_texture_state)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_texture)
        self.assertFalse(
            cmds.listConnections(
                f"{shader}.SphereTexture",
                source=True,
                destination=False,
                plugs=True,
            )
        )
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_sphere)
        self.assertEqual(main_texture_state(), after_texture_state)

        view.sphere_map_path_edit.clear()
        view.apply_btn.click()
        QApplication.processEvents()
        after_sphere_clear = _canonical_payload(self.window, self.root)
        self.assertIsNone(
            after_sphere_clear["spec"]["materials"][0]["sphere_texture_path"]
        )
        self.assertIsNone(
            after_sphere_clear["spec"]["materials"][0]["resolved_sphere_texture_path"]
        )
        self.assertFalse(
            cmds.listConnections(
                f"{shader}.SphereTexture",
                source=True,
                destination=False,
                plugs=True,
            )
        )
        self.assertEqual(cmds.getAttr(f"{shader}.HasSphereTexture"), 0)
        self.assertEqual(main_texture_state(), after_texture_state)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_sphere)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_sphere_clear)

        view.texture_path_edit.clear()
        view.apply_btn.click()
        QApplication.processEvents()
        after_clear = _canonical_payload(self.window, self.root)
        after_clear_state = main_texture_state()
        self.assertIsNone(after_clear["spec"]["materials"][0]["resolved_texture_path"])
        self.assertIsNone(after_clear_state["source"])
        self.assertEqual(after_clear_state["has_main_texture"], 0)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_sphere_clear)
        self.assertEqual(main_texture_state(), after_texture_state)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_clear)
        self.assertEqual(main_texture_state(), after_clear_state)

    def test_standard_surface_texture_browse_reuse_and_save_reopen(self):
        """Browse, replace, undo, and persist one standardSurface main texture."""
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        material = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
        shader = material.binding_identity
        self.assertEqual(cmds.nodeType(shader), "standardSurface")
        self.assertFalse(
            cmds.listConnections(
                f"{shader}.baseColor",
                source=True,
                destination=False,
                type="file",
            )
        )

        source_texture = Path(__file__).resolve().parents[1] / "data" / "tex" / "diffuse.png"
        with tempfile.TemporaryDirectory(prefix="mmd_material_texture_browse_") as temp_dir:
            first_path = Path(temp_dir) / "first.png"
            second_path = Path(temp_dir) / "second.png"
            first_path.write_bytes(source_texture.read_bytes())
            second_path.write_bytes(source_texture.read_bytes())

            view.texture_path_edit.setText(str(Path(temp_dir) / "missing.png"))
            view.apply_btn.click()
            QApplication.processEvents()
            self.assertIsNone(
                self.window.authoring_composition.coordinator.read_spec(self.root)
                .materials[0]
                .resolved_texture_path
            )
            self.assertFalse(
                cmds.listConnections(
                    f"{shader}.baseColor",
                    source=True,
                    destination=False,
                    type="file",
                )
            )

            with patch(
                "mmd_tools.ui.presenters.material_presenter.QFileDialog.getOpenFileName",
                return_value=(str(first_path), "Image Files (*.png)"),
            ):
                QTest.mouseClick(view.texture_browse_btn, Qt.LeftButton)
            self.assertEqual(view.texture_path_edit.text(), str(first_path))
            view.apply_btn.click()
            QApplication.processEvents()

            first_material = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
            first_sources = cmds.listConnections(
                f"{shader}.baseColor",
                source=True,
                destination=False,
                plugs=True,
                type="file",
            ) or []
            self.assertEqual(len(first_sources), 1)
            file_node = first_sources[0].rsplit(".", 1)[0]
            self.assertEqual(cmds.nodeType(file_node), "file")
            self.assertEqual(Path(cmds.getAttr(f"{file_node}.fileTextureName")), first_path)
            self.assertEqual(first_material.texture_path, str(first_path))
            self.assertEqual(first_material.resolved_texture_path, str(first_path))

            with patch(
                "mmd_tools.ui.presenters.material_presenter.QFileDialog.getOpenFileName",
                return_value=(str(second_path), "Image Files (*.png)"),
            ):
                QTest.mouseClick(view.texture_browse_btn, Qt.LeftButton)
            view.apply_btn.click()
            QApplication.processEvents()

            second_material = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
            second_sources = cmds.listConnections(
                f"{shader}.baseColor",
                source=True,
                destination=False,
                plugs=True,
                type="file",
            ) or []
            self.assertEqual(second_sources, [f"{file_node}.outColor"])
            self.assertEqual(Path(cmds.getAttr(f"{file_node}.fileTextureName")), second_path)
            self.assertEqual(second_material.resolved_texture_path, str(second_path))

            cmds.undo()
            self.assertEqual(Path(cmds.getAttr(f"{file_node}.fileTextureName")), first_path)
            self.assertEqual(
                self.window.authoring_composition.coordinator.read_spec(self.root)
                .materials[0]
                .resolved_texture_path,
                str(first_path),
            )
            cmds.redo()
            self.assertEqual(Path(cmds.getAttr(f"{file_node}.fileTextureName")), second_path)

            scene_path = Path(temp_dir) / "material_texture_ascii.ma"
            before_reopen = _canonical_payload(self.window, self.root)
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(scene_path), open=True, force=True)
            roots = self.window.app_state.scene_model_service.list_mmd_models()
            self.assertEqual(len(roots), 1)
            self.root = roots[0]
            self.window.app_state.current_model_root = self.root
            QApplication.processEvents()
            reopened = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
            reopened_sources = cmds.listConnections(
                f"{reopened.binding_identity}.baseColor",
                source=True,
                destination=False,
                plugs=True,
                type="file",
            ) or []
            self.assertEqual(_canonical_payload(self.window, self.root), before_reopen)
            self.assertEqual(len(reopened_sources), 1)
            reopened_file = reopened_sources[0].rsplit(".", 1)[0]
            self.assertEqual(Path(cmds.getAttr(f"{reopened_file}.fileTextureName")), second_path)

    def test_standard_surface_texture_browse_pmx_fresh_import(self):
        """Round-trip a newly browsed texture through PMX export and fresh import."""
        from mmd_tools.core import settings

        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        source_texture = Path(__file__).resolve().parents[1] / "data" / "tex" / "diffuse.png"

        with tempfile.TemporaryDirectory(prefix="mmd_material_texture_pmx_") as temp_dir:
            texture_path = Path(temp_dir) / "authored.png"
            export_path = Path(temp_dir) / "material_texture_roundtrip.pmx"
            texture_path.write_bytes(source_texture.read_bytes())
            with patch(
                "mmd_tools.ui.presenters.material_presenter.QFileDialog.getOpenFileName",
                return_value=(str(texture_path), "Image Files (*.png)"),
            ):
                QTest.mouseClick(view.texture_browse_btn, Qt.LeftButton)
            view.apply_btn.click()
            QApplication.processEvents()

            authored = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
            self.assertEqual(authored.texture_path, str(texture_path))
            self.assertEqual(authored.resolved_texture_path, str(texture_path))
            collected = ExportSceneCollector().collect_from_model_root(self.root)
            collected_material = collected["materials"][0]
            self.assertEqual(collected["textures"], [str(texture_path)])
            self.assertEqual(collected_material["texture_index"], 0)
            self.assertNotIn("texture_table", collected_material.get("semantic_missing", []))

            PmxExporter().export_pmx_model(str(export_path), collected)
            parsed = parse_pmx_file(
                str(export_path),
                use_native_pmx_parse=False,
                require_native_pmx_parse=False,
            )
            self.assertEqual(parsed.textures, [str(texture_path)])
            self.assertEqual(parsed.materials[0].texture_index, 0)

            previous_create = settings.get("import.model.create_mmd_shaders")
            previous_backend = settings.get("import.model.mmd_shader_backend")
            try:
                settings.set("import.model.create_mmd_shaders", True)
                settings.set("import.model.mmd_shader_backend", "standard")
                cmds.file(new=True, force=True)
                reopened_root = import_mmd_file(
                    str(export_path),
                    options={
                        "scale": 1.0,
                        "import_physics": False,
                        "setup_rig": False,
                        "setup_bone_orientation": False,
                        "create_mmd_control_rig": False,
                        "create_mmd_shaders": True,
                        "use_cpp_fast_load": False,
                        "use_native_pmx_parse": False,
                        "require_native_pmx_parse": False,
                    },
                )
            finally:
                settings.set("import.model.create_mmd_shaders", previous_create)
                settings.set("import.model.mmd_shader_backend", previous_backend)
            self.assertTrue(reopened_root)
            self.root = str(reopened_root)
            self.window.app_state.current_model_root = self.root
            QApplication.processEvents()
            reopened = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0]
            self.assertEqual(cmds.nodeType(reopened.binding_identity), "standardSurface")
            self.assertEqual(reopened.texture_path, str(texture_path))
            self.assertEqual(reopened.resolved_texture_path, str(texture_path))
            sources = cmds.listConnections(
                f"{reopened.binding_identity}.baseColor",
                source=True,
                destination=False,
                plugs=True,
                type="file",
            ) or []
            self.assertEqual(len(sources), 1)
            file_node = sources[0].rsplit(".", 1)[0]
            self.assertEqual(Path(cmds.getAttr(f"{file_node}.fileTextureName")), texture_path)

    def test_header_refresh_defers_hidden_authoring_tabs(self):
        """Header Refresh must not read hidden authoring graphs eagerly."""
        self.window.tab_widget.setCurrentIndex(0)
        cmds.setAttr(f"{self.root}.{ATTR_MMD_MODEL_NAME}", "UI Refresh Name", type="string")
        generation = self.window.app_state.refresh_generation

        with patch.object(self.window.info_presenter, "load_model_info") as info_load, patch.object(
            self.window.material_presenter, "load_materials"
        ) as material_load, patch.object(self.window.bone_presenter, "load_bones") as bone_load, patch.object(
            self.window.morph_presenter, "load_morphs"
        ) as morph_load, patch.object(self.window.display_pane_presenter, "refresh") as display_refresh, patch.object(
            self.window.physics_presenter, "refresh_physics"
        ) as physics_refresh:
            self._observe_surface_signal(
                "header.refresh",
                "ApplicationState.refresh_model_list(explicit=True)",
                self.window.header_widget.refresh_btn,
            )
            self.window.header_widget.refresh_btn.click()
            QApplication.processEvents()
            self.assertEqual(self.window.app_state.refresh_generation, generation + 1)
            for call in (
                info_load,
                material_load,
                bone_load,
                morph_load,
                display_refresh,
                physics_refresh,
            ):
                self.assertEqual(call.call_count, 0)

            info_index = self.window.tab_widget.indexOf(self.window.info_presenter.view)
            self.window.tab_widget.setCurrentIndex(info_index)
            QApplication.processEvents()
            info_load.assert_called_once_with()

        self.assertIn("UI Refresh Name", self.window.windowTitle())
        self._emit_surface_witness(
            "header.refresh",
            "gui.authoring_refresh_generation",
            selector="objectName=headerRefreshButton",
            interaction="QTest.mouseClick(objectName=headerRefreshButton)",
            fired_action="ApplicationState.refresh_model_list(explicit=True)",
            oracle="hidden_authoring_tabs_zero_graph_reads_until_activation",
        )

    def test_material_toolbar_crud_reindex_and_undo(self):
        """Exercise every supported Material toolbar action through real buttons."""
        view = self.window.material_presenter.view
        self._observe_surface_signal(
            "material.refresh", "MaterialPresenter.load_materials", view.refresh_btn
        )
        view.refresh_btn.click()
        QApplication.processEvents()
        self.assertEqual(view.material_list.count(), 1)
        self._emit_surface_witness(
            "material.refresh",
            "gui.material_toolbar_controls",
            selector="objectName=materialRefreshButton",
            interaction="QTest.mouseClick(objectName=materialRefreshButton)",
            fired_action="MaterialPresenter.load_materials",
            oracle="material_list_count_and_semantic_rows_refreshed",
        )

        before_create = _canonical_payload(self.window, self.root)
        self._observe_surface_signal(
            "material.create", "MaterialPresenter.create_material", view.create_btn
        )
        view.create_btn.click()
        QApplication.processEvents()
        after_create = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_create["spec"]["materials"]), 2)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), "MMD Material Create")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_create)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_create)
        self._emit_surface_witness(
            "material.create",
            "gui.material_toolbar_controls",
            selector="objectName=materialCreateButton",
            interaction="QTest.mouseClick(objectName=materialCreateButton); Maya Undo/Redo",
            fired_action="MaterialPresenter.create_material",
            oracle="material_count_and_semantic_spec_undo_redo",
        )

        view.refresh_btn.click()
        view.material_list.setCurrentRow(1)
        QApplication.processEvents()
        before_duplicate = _canonical_payload(self.window, self.root)
        self._observe_surface_signal(
            "material.duplicate", "MaterialPresenter.duplicate_material", view.duplicate_btn
        )
        view.duplicate_btn.click()
        QApplication.processEvents()
        after_duplicate = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_duplicate["spec"]["materials"]), 3)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_duplicate)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_duplicate)
        self._emit_surface_witness(
            "material.duplicate",
            "gui.material_toolbar_controls",
            selector="objectName=materialDuplicateButton",
            interaction="QTest.mouseClick(objectName=materialDuplicateButton); Maya Undo/Redo",
            fired_action="MaterialPresenter.duplicate_material",
            oracle="duplicated_material_count_and_semantic_spec_undo_redo",
        )

        view.refresh_btn.click()
        view.material_list.setCurrentRow(2)
        QApplication.processEvents()
        before_move_up = _canonical_payload(self.window, self.root)
        self._observe_surface_signal(
            "material.move_up", "MaterialPresenter.move_material(-1)", view.reindex_up_btn
        )
        view.reindex_up_btn.click()
        QApplication.processEvents()
        after_move_up = _canonical_payload(self.window, self.root)
        self.assertNotEqual(after_move_up, before_move_up)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_move_up)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_move_up)
        self._emit_surface_witness(
            "material.move_up",
            "gui.material_toolbar_controls",
            selector="objectName=materialMoveUpButton",
            interaction="QTest.mouseClick(objectName=materialMoveUpButton); Maya Undo/Redo",
            fired_action="MaterialPresenter.move_material(-1)",
            oracle="material_index_order_and_semantic_spec_undo_redo",
        )

        before_move_down = _canonical_payload(self.window, self.root)
        self._observe_surface_signal(
            "material.move_down", "MaterialPresenter.move_material(1)", view.reindex_down_btn
        )
        view.reindex_down_btn.click()
        QApplication.processEvents()
        after_move_down = _canonical_payload(self.window, self.root)
        self.assertEqual(after_move_down, before_move_up)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_move_down)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_move_down)
        self._emit_surface_witness(
            "material.move_down",
            "gui.material_toolbar_controls",
            selector="objectName=materialMoveDownButton",
            interaction="QTest.mouseClick(objectName=materialMoveDownButton); Maya Undo/Redo",
            fired_action="MaterialPresenter.move_material(1)",
            oracle="material_index_order_and_semantic_spec_undo_redo",
        )

        view.refresh_btn.click()
        view.material_list.setCurrentRow(2)
        QApplication.processEvents()
        before_delete = _canonical_payload(self.window, self.root)
        with patch(
            "mmd_tools.ui.qt_compat.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            self._observe_surface_signal(
                "material.delete", "MaterialPresenter.delete_material", view.delete_btn
            )
            view.delete_btn.click()
        QApplication.processEvents()
        after_delete = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_delete["spec"]["materials"]), 2)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_delete)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_delete)
        self._emit_surface_witness(
            "material.delete",
            "gui.material_toolbar_controls",
            selector="objectName=materialDeleteButton",
            interaction="QTest.mouseClick(objectName=materialDeleteButton); confirm Yes; Maya Undo/Redo",
            fired_action="MaterialPresenter.delete_material",
            oracle="material_count_and_semantic_spec_undo_redo",
        )

    def test_bone_reset_restores_pending_ui_edit(self):
        """A Bone Reset click restores pending fields without a Maya write."""
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity
        before = _canonical_payload(self.window, self.root)
        before_semantic = _semantic_topology(self.window, self.root)
        before_maya = _node_footprint(binding)
        original_name = view.bone_name_en_edit.text()
        view.bone_name_en_edit.setText(f"{original_name} pending")
        self.assertNotEqual(view.bone_name_en_edit.text(), original_name)
        self.status_messages.clear()

        self.assertTrue(view.reset_btn.isEnabled())
        QTest.mouseClick(view.reset_btn, Qt.LeftButton)
        QApplication.processEvents()

        self.assertEqual(view.bone_name_en_edit.text(), original_name)
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_semantic_topology(self.window, self.root), before_semantic)
        self.assertEqual(_node_footprint(binding), before_maya)
        self.assertEqual(len(self.status_messages), 1, "boneResetButton handler must emit one status")
        self._emit_bone_action_witness(
            "bone.reset",
            "objectName=boneResetButton",
            "BonePresenter.reset_changes -> AppState.status_message",
            "pending_ui_edit_and_semantic_maya_fingerprint_unchanged",
            len(self.status_messages),
        )

    def test_bone_apply_basic_values_undo_redo(self):
        """Apply basic bone values through one real Qt click and one value patch."""
        view = self.window.bone_presenter.view
        short_root = cmds.ls(self.root, shortNames=True)[0]
        self.assertFalse(short_root.startswith("|"))
        self.window.app_state.current_model_root = short_root
        QApplication.processEvents()
        self.assertEqual(
            self.window.app_state.current_model_root,
            cmds.ls(self.root, long=True)[0],
        )
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        binding = coordinator.read_spec(self.root).bones[0].binding_identity
        self.assertTrue(binding.startswith("|"))
        before = _canonical_payload(self.window, self.root)
        before_maya = _node_footprint(binding)

        view.bone_name_jp_edit.setText("UI Root Basic")
        view.deform_layer_spin.setValue(7)
        for control, checked in (
            (view.rotatable_check, False),
            (view.movable_check, False),
            (view.visible_check, False),
            (view.enabled_check, False),
            (view.after_physics_check, True),
        ):
            control.setChecked(checked)

        invocations = []
        original_apply = coordinator.apply_bone_value_patch

        def observe_apply(*args, **kwargs):
            invocations.append(True)
            return original_apply(*args, **kwargs)

        coordinator.apply_bone_value_patch = observe_apply
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.apply_bone_value_patch = original_apply

        self.assertEqual(len(invocations), 1)
        after = _canonical_payload(self.window, self.root)
        after_bone = coordinator.read_spec(self.root).bones[0]
        self.assertEqual(after_bone.name, "UI Root Basic")
        self.assertEqual(after_bone.transform_layer, 7)
        self.assertEqual(
            int(after_bone.flags),
            int(PmxBoneFlag.DEFORM_AFTER_PHYSICS),
        )
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_NAME}"), "UI Root Basic")
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_DEFORM_LAYER}"), 7)
        self.assertEqual(
            cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_FLAGS}"),
            int(PmxBoneFlag.DEFORM_AFTER_PHYSICS),
        )
        after_maya = _node_footprint(binding)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_NAME}"), "UI Root Basic")
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_DEFORM_LAYER}"), 7)
        self.assertEqual(
            cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_FLAGS}"),
            int(PmxBoneFlag.DEFORM_AFTER_PHYSICS),
        )
        self._emit_bone_value_surface_witnesses(
            (
                "bone.name_jp",
                "bone.deform_layer",
                "bone.rotatable",
                "bone.movable",
                "bone.visible",
                "bone.enabled",
                "bone.after_physics",
            ),
            "basic_values_spec_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_apply_routes_same_leaf_model_by_selected_long_identity(self):
        """A selected child joint from Model A must never write Model B."""
        composition = self.window.authoring_composition
        initializer = composition.model_initializer
        parent_a = cmds.ls(
            cmds.group(empty=True, name="identityModelA"), long=True
        )[0]
        parent_b = cmds.ls(
            cmds.group(empty=True, name="identityModelB"), long=True
        )[0]

        root_a = cmds.parent(self.root, parent_a)[0]
        second = initializer.create(
            "pmx20-basic-v1", "Identity Smoke JP", "Identity Smoke EN"
        )
        root_b = cmds.parent(second.root, parent_b)[0]
        cmds.rename(root_a, "sharedIdentityModel_root")
        cmds.rename(root_b, "sharedIdentityModel_root")
        root_a = cmds.ls(
            f"{parent_a}|sharedIdentityModel_root", long=True
        )[0]
        root_b = cmds.ls(
            f"{parent_b}|sharedIdentityModel_root", long=True
        )[0]
        self.assertNotEqual(root_a, root_b)
        self.assertEqual(root_a.rsplit("|", 1)[-1], root_b.rsplit("|", 1)[-1])

        service = self.window.app_state.scene_model_service
        available = service.list_mmd_models()
        self.assertIn(root_a, available)
        self.assertIn(root_b, available)

        # Keep this focused witness on BonePresenter.  Re-emitting the full
        # current-model change would reload unrelated morph/mesh presenters,
        # whose legacy short-shape probes cannot disambiguate the duplicate
        # template meshes intentionally created here.
        self.window.app_state._available_models = list(available)
        self.window.app_state._current_model_root = root_a
        coordinator = composition.coordinator
        binding_a = coordinator.read_spec(root_a).bones[0].binding_identity
        binding_b = coordinator.read_spec(root_b).bones[0].binding_identity
        before_a = _canonical_payload(self.window, root_a)
        before_b = _canonical_payload(self.window, root_b)
        before_b_binding = _node_footprint(binding_b)

        cmds.select(binding_a, replace=True)
        self.assertTrue(self.window.app_state.select_model_from_maya_selection())
        self.assertEqual(self.window.app_state.current_model_root, root_a)
        self.window.bone_presenter.load_bones()
        QApplication.processEvents()
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        view.bone_name_en_edit.setText("Only Model A")
        self._observe_surface_signal(
            "bone.apply.current_model_identity",
            "BonePresenter.apply_changes",
            view.apply_btn,
        )
        QTest.mouseClick(view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()

        after_a = _canonical_payload(self.window, root_a)
        after_b = _canonical_payload(self.window, root_b)
        self.assertEqual(after_a["spec"]["bones"][0]["name_english"], "Only Model A")
        self.assertNotEqual(after_a, before_a)
        self.assertEqual(after_b, before_b)
        self.assertEqual(_node_footprint(binding_b), before_b_binding)
        self.assertNotEqual(
            cmds.getAttr(f"{binding_a}.{ATTR_MMD_BONE_NAME_EN}"),
            cmds.getAttr(f"{binding_b}.{ATTR_MMD_BONE_NAME_EN}"),
        )
        self._emit_surface_witness(
            "bone.apply.current_model_identity",
            "gui.current_model_identity",
            selector="objectName=boneApplyButton",
            interaction="select(Model A child joint); QTest.mouseClick(boneApplyButton)",
            fired_action="BonePresenter.apply_changes",
            oracle="same_leaf_model_A_only_semantic_and_maya_state_changed",
        )

    def test_bone_apply_fixed_axis_undo_redo(self):
        """Apply a fixed-axis vector through one real Qt click and one value patch."""
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        binding = coordinator.read_spec(self.root).bones[0].binding_identity
        before = _canonical_payload(self.window, self.root)
        before_maya = _node_footprint(binding)

        view.fixed_axis_check.setChecked(True)
        fixed_axis = (0.3, -0.4, 0.5)
        for control, value in zip(
            (
                view.fixed_axis_x_spin,
                view.fixed_axis_y_spin,
                view.fixed_axis_z_spin,
            ),
            fixed_axis,
        ):
            control.setValue(value)

        invocations = []
        original_apply = coordinator.apply_bone_value_patch

        def observe_apply(*args, **kwargs):
            invocations.append(True)
            return original_apply(*args, **kwargs)

        coordinator.apply_bone_value_patch = observe_apply
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.apply_bone_value_patch = original_apply

        self.assertEqual(len(invocations), 1)
        after = _canonical_payload(self.window, self.root)
        after_bone = coordinator.read_spec(self.root).bones[0]
        self.assertTrue(int(after_bone.flags) & int(PmxBoneFlag.AXIS_FIXED))
        for actual, expected in zip(after_bone.fixed_axis, fixed_axis):
            self.assertAlmostEqual(actual, expected, places=6)
        for attr in (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION):
            value = cmds.getAttr(f"{binding}.{attr}")[0]
            for actual, expected in zip(value, fixed_axis):
                self.assertAlmostEqual(actual, expected, places=6)
        after_maya = _node_footprint(binding)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        for attr in (ATTR_MMD_FIXED_AXIS, ATTR_MMD_AXIS_DIRECTION):
            value = cmds.getAttr(f"{binding}.{attr}")[0]
            for actual, expected in zip(value, fixed_axis):
                self.assertAlmostEqual(actual, expected, places=6)
        self._emit_bone_value_surface_witnesses(
            ("bone.fixed_axis_enabled", "bone.fixed_axis"),
            "fixed_axis_spec_vector_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_apply_local_axes_undo_redo(self):
        """Apply local X/Z vectors through one real Qt click and one value patch."""
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        binding = coordinator.read_spec(self.root).bones[0].binding_identity
        before = _canonical_payload(self.window, self.root)
        before_maya = _node_footprint(binding)

        view.local_axis_check.setChecked(True)
        local_x = (0.2, 0.3, 0.4)
        local_z = (-0.5, 0.6, -0.7)
        for controls, values in (
            (
                (
                    view.local_x_axis_x_spin,
                    view.local_x_axis_y_spin,
                    view.local_x_axis_z_spin,
                ),
                local_x,
            ),
            (
                (
                    view.local_z_axis_x_spin,
                    view.local_z_axis_y_spin,
                    view.local_z_axis_z_spin,
                ),
                local_z,
            ),
        ):
            for control, value in zip(controls, values):
                control.setValue(value)

        invocations = []
        original_apply = coordinator.apply_bone_value_patch

        def observe_apply(*args, **kwargs):
            invocations.append(True)
            return original_apply(*args, **kwargs)

        coordinator.apply_bone_value_patch = observe_apply
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.apply_bone_value_patch = original_apply

        self.assertEqual(len(invocations), 1)
        after = _canonical_payload(self.window, self.root)
        after_bone = coordinator.read_spec(self.root).bones[0]
        self.assertTrue(int(after_bone.flags) & int(PmxBoneFlag.LOCAL_AXIS))
        for actual, expected in zip(after_bone.local_axis_x, local_x):
            self.assertAlmostEqual(actual, expected, places=6)
        for actual, expected in zip(after_bone.local_axis_z, local_z):
            self.assertAlmostEqual(actual, expected, places=6)
        for attr, expected in (
            (ATTR_MMD_LOCAL_X_AXIS, local_x),
            (ATTR_MMD_X_AXIS_DIRECTION, local_x),
            (ATTR_MMD_LOCAL_Z_AXIS, local_z),
            (ATTR_MMD_Z_AXIS_DIRECTION, local_z),
        ):
            value = cmds.getAttr(f"{binding}.{attr}")[0]
            for actual, expected_value in zip(value, expected):
                self.assertAlmostEqual(actual, expected_value, places=6)
        after_maya = _node_footprint(binding)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        for attr, expected in (
            (ATTR_MMD_LOCAL_X_AXIS, local_x),
            (ATTR_MMD_X_AXIS_DIRECTION, local_x),
            (ATTR_MMD_LOCAL_Z_AXIS, local_z),
            (ATTR_MMD_Z_AXIS_DIRECTION, local_z),
        ):
            value = cmds.getAttr(f"{binding}.{attr}")[0]
            for actual, expected_value in zip(value, expected):
                self.assertAlmostEqual(actual, expected_value, places=6)
        self._emit_bone_value_surface_witnesses(
            (
                "bone.local_axis_enabled",
                "bone.local_x_axis",
                "bone.local_z_axis",
            ),
            "local_axes_spec_vectors_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_apply_grant_semantic_undo_redo(self):
        """Apply rotation/move grant metadata through one full semantic replace."""
        bindings = self._register_second_bone_fixture()
        presenter = self.window.bone_presenter
        view = presenter.view
        view.bone_list.setCurrentRow(1)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        before_spec = coordinator.read_spec(self.root)
        before = _canonical_payload(self.window, self.root)
        binding = bindings[1]
        before_maya = _node_footprint(binding)
        root_bone = next(bone for bone in before_spec.bones if bone.binding_identity == bindings[0])
        grant_parent_display = presenter._get_bone_display_name(bindings[0])

        view.rotation_grant_check.setChecked(True)
        view.move_grant_check.setChecked(True)
        view.grant_parent_edit.setText(grant_parent_display)
        view.grant_rate_spin.setValue(0.35)
        view.local_grant_check.setChecked(True)
        QApplication.processEvents()
        self.assertEqual(view.grant_parent_edit.text(), grant_parent_display)

        invocations = []
        original_replace = coordinator.replace_bone_semantic

        def observe_replace(*args, **kwargs):
            invocations.append("MayaModelAuthoringCoordinator.replace_bone_semantic")
            return original_replace(*args, **kwargs)

        coordinator.replace_bone_semantic = observe_replace
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.replace_bone_semantic = original_replace

        self.assertEqual(len(invocations), 1, "grant Apply must invoke replace_bone_semantic once")
        after = _canonical_payload(self.window, self.root)
        after_spec = coordinator.read_spec(self.root)
        after_bone = next(bone for bone in after_spec.bones if bone.binding_identity == binding)
        grant_flags = int(PmxBoneFlag.GRANT_PARENT_ROTATE | PmxBoneFlag.GRANT_PARENT_MOVE)
        self.assertEqual(after_bone.grant_parent_index, root_bone.index)
        self.assertAlmostEqual(after_bone.grant_ratio, 0.35, places=6)
        self.assertTrue(after_bone.grant_local)
        self.assertEqual(int(after_bone.flags) & grant_flags, grant_flags)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_GRANT_PARENT_INDEX}"), root_bone.index)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_GRANT_PARENT}"), root_bone.name)
        self.assertAlmostEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_GRANT_RATE}"), 0.35, places=6)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_FLAGS}") & grant_flags, grant_flags)
        after_maya = _node_footprint(binding)

        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        self.assertEqual(
            cmds.getAttr(f"{binding}.{ATTR_MMD_GRANT_PARENT_INDEX}"), root_bone.index
        )
        self.assertAlmostEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_GRANT_RATE}"), 0.35, places=6)
        self._emit_bone_structural_surface_witnesses(
            (
                "bone.rotation_grant",
                "bone.move_grant",
                "bone.grant_parent",
                "bone.grant_rate",
                "bone.local_grant",
            ),
            "grant_parent_index_flags_ratio_local_spec_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_apply_external_parent_semantic_undo_redo(self):
        """Apply external-parent flag/key through one full semantic replace."""
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        binding = coordinator.read_spec(self.root).bones[0].binding_identity
        before = _canonical_payload(self.window, self.root)
        before_maya = _node_footprint(binding)

        view.external_parent_check.setChecked(True)
        view.external_parent_key_spin.setValue(37)
        QApplication.processEvents()
        self.assertFalse(view.external_parent_key_spin.isHidden())

        invocations = []
        original_replace = coordinator.replace_bone_semantic

        def observe_replace(*args, **kwargs):
            invocations.append("MayaModelAuthoringCoordinator.replace_bone_semantic")
            return original_replace(*args, **kwargs)

        coordinator.replace_bone_semantic = observe_replace
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.replace_bone_semantic = original_replace

        self.assertEqual(
            len(invocations), 1, "external-parent Apply must invoke replace_bone_semantic once"
        )
        after = _canonical_payload(self.window, self.root)
        after_bone = coordinator.read_spec(self.root).bones[0]
        self.assertEqual(after_bone.external_parent_key, 37)
        self.assertTrue(int(after_bone.flags) & int(PmxBoneFlag.EXTERNAL_PARENT_DEFORM))
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_EXTERNAL_PARENT_KEY}"), 37)
        self.assertTrue(
            cmds.getAttr(f"{binding}.{ATTR_MMD_BONE_FLAGS}")
            & int(PmxBoneFlag.EXTERNAL_PARENT_DEFORM)
        )
        after_maya = _node_footprint(binding)

        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_EXTERNAL_PARENT_KEY}"), 37)
        self._emit_bone_structural_surface_witnesses(
            ("bone.external_parent", "bone.external_parent_key"),
            "external_parent_flag_key_spec_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_apply_ik_semantic_undo_redo(self):
        """Apply IK target/settings/link limits through one full semantic replace."""
        bindings = self._register_third_bone_fixture()
        presenter = self.window.bone_presenter
        view = presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        coordinator = self.window.authoring_composition.coordinator
        binding = bindings[0]
        before = _canonical_payload(self.window, self.root)
        before_maya = _node_footprint(binding)

        view.ik_enabled_check.setChecked(True)
        QApplication.processEvents()
        cmds.select(bindings[1], replace=True)
        QTest.mouseClick(view.select_ik_target_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertTrue(view.ik_target_edit.text())
        self.assertNotIn("|", view.ik_target_edit.text())
        self.assertEqual(
            view.ik_target_edit.property("mmdBindingIdentity"),
            bindings[1],
        )
        view.ik_loop_spin.setValue(7)
        view.ik_limit_angle_spin.setValue(33.0)

        cmds.select(bindings[2], replace=True)
        QTest.mouseClick(view.add_ik_link_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(view.ik_links_table.rowCount(), 1)
        row = 0
        link_bone_item = view.ik_links_table.item(row, 0)
        self.assertNotIn("|", link_bone_item.text())
        self.assertEqual(link_bone_item.data(Qt.UserRole), bindings[2])
        limit_check = view.ik_links_table.cellWidget(row, 1)
        self.assertIsNotNone(limit_check)
        limit_check.setChecked(True)
        lower_degrees = (-10.0, -20.0, -30.0)
        upper_degrees = (11.0, 22.0, 33.0)
        for col, value in zip(range(2, 5), lower_degrees):
            view.ik_links_table.item(row, col).setText(str(value))
        for col, value in zip(range(5, 8), upper_degrees):
            view.ik_links_table.item(row, col).setText(str(value))

        invocations = []
        original_replace = coordinator.replace_bone_semantic

        def observe_replace(*args, **kwargs):
            invocations.append("MayaModelAuthoringCoordinator.replace_bone_semantic")
            return original_replace(*args, **kwargs)

        coordinator.replace_bone_semantic = observe_replace
        try:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.replace_bone_semantic = original_replace

        self.assertEqual(len(invocations), 1, "IK Apply must invoke replace_bone_semantic once")
        after = _canonical_payload(self.window, self.root)
        after_bone = coordinator.read_spec(self.root).bones[0]
        self.assertTrue(int(after_bone.flags) & int(PmxBoneFlag.IK))
        self.assertEqual(after_bone.ik_target_index, 1)
        self.assertEqual(after_bone.ik_loop_count, 7)
        self.assertAlmostEqual(after_bone.ik_limit_radian, math.radians(33.0), places=6)
        self.assertEqual(len(after_bone.ik_links), 1)
        link = after_bone.ik_links[0]
        self.assertEqual(link["bone"], 2)
        self.assertTrue(link["limit_enabled"])
        for actual, expected in zip(link["lower_limit"], lower_degrees):
            self.assertAlmostEqual(actual, math.radians(expected), places=6)
        for actual, expected in zip(link["upper_limit"], upper_degrees):
            self.assertAlmostEqual(actual, math.radians(expected), places=6)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_TARGET_INDEX}"), 1)
        target_bone = coordinator.read_spec(self.root).bones[1]
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_TARGET}"), target_bone.name)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_LOOP}"), 7)
        self.assertAlmostEqual(
            cmds.getAttr(f"{binding}.{ATTR_MMD_IK_LIMIT_ANGLE}"), math.radians(33.0), places=6
        )
        maya_links = json.loads(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_LINKS}"))
        self.assertEqual(maya_links[0]["bone"], 2)
        self.assertTrue(maya_links[0]["limit_enabled"])
        after_maya = _node_footprint(binding)

        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_node_footprint(binding), before_maya)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertEqual(_node_footprint(binding), after_maya)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_TARGET_INDEX}"), 1)
        self.assertEqual(cmds.getAttr(f"{binding}.{ATTR_MMD_IK_LOOP}"), 7)
        self._emit_bone_structural_surface_witnesses(
            (
                "bone.ik_enabled",
                "bone.ik_target",
                "bone.ik_loop",
                "bone.ik_limit_angle",
                "bone.ik_links",
            ),
            "ik_target_settings_links_limits_spec_maya_attrs_undo_redo",
            len(invocations),
        )

    def test_bone_move_up_reorders_pending_registered_bones(self):
        """Move Up changes only the deterministic pending order."""
        bindings = self._register_second_bone_fixture()
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(1)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        before_semantic = _semantic_topology(self.window, self.root)
        before_maya = self._bone_maya_fingerprint()
        before_order = tuple(view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count()))
        self.assertEqual(before_order, bindings)
        self.status_messages.clear()

        self.assertTrue(view.reindex_up_btn.isEnabled())
        QTest.mouseClick(view.reindex_up_btn, Qt.LeftButton)
        QApplication.processEvents()

        after_order = tuple(view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count()))
        self.assertEqual(after_order, (bindings[1], bindings[0]))
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_semantic_topology(self.window, self.root), before_semantic)
        self.assertEqual(self._bone_maya_fingerprint(), before_maya)
        self.assertEqual(len(self.status_messages), 1, "boneMoveUpButton handler must emit one status")
        self._emit_bone_action_witness(
            "bone.move_up",
            "objectName=boneMoveUpButton",
            "BonePresenter.move_reindex(-1) -> AppState.status_message",
            "pending_bone_order_swapped_once_semantic_maya_fingerprint_unchanged",
            len(self.status_messages),
        )

    def test_bone_move_down_reorders_pending_registered_bones(self):
        """Move Down changes only the deterministic pending order."""
        bindings = self._register_second_bone_fixture()
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        before_semantic = _semantic_topology(self.window, self.root)
        before_maya = self._bone_maya_fingerprint()
        before_order = tuple(view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count()))
        self.assertEqual(before_order, bindings)
        self.status_messages.clear()

        self.assertTrue(view.reindex_down_btn.isEnabled())
        QTest.mouseClick(view.reindex_down_btn, Qt.LeftButton)
        QApplication.processEvents()

        after_order = tuple(view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count()))
        self.assertEqual(after_order, (bindings[1], bindings[0]))
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(_semantic_topology(self.window, self.root), before_semantic)
        self.assertEqual(self._bone_maya_fingerprint(), before_maya)
        self.assertEqual(len(self.status_messages), 1, "boneMoveDownButton handler must emit one status")
        self._emit_bone_action_witness(
            "bone.move_down",
            "objectName=boneMoveDownButton",
            "BonePresenter.move_reindex(1) -> AppState.status_message",
            "pending_bone_order_swapped_once_semantic_maya_fingerprint_unchanged",
            len(self.status_messages),
        )

    def test_bone_reset_authoring_commits_pending_order_and_undo_redo(self):
        """Reset Authoring commits pending order through one coordinator call."""
        bindings = self._register_second_bone_fixture()
        presenter = self.window.bone_presenter
        view = presenter.view
        view.bone_list.setCurrentRow(1)
        QApplication.processEvents()
        self.assertTrue(presenter.move_reindex(-1))
        QApplication.processEvents()
        pending_order = tuple(view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count()))
        self.assertEqual(pending_order, (bindings[1], bindings[0]))
        before = _canonical_payload(self.window, self.root)
        coordinator = self.window.authoring_composition.coordinator
        original_reset_bones = coordinator.reset_bones
        reset_invocations = []

        def observe_reset_bones(*args, **kwargs):
            reset_invocations.append("MayaModelAuthoringCoordinator.reset_bones")
            return original_reset_bones(*args, **kwargs)

        coordinator.reset_bones = observe_reset_bones
        try:
            self.assertTrue(view.reset_authoring_btn.isEnabled())
            QTest.mouseClick(view.reset_authoring_btn, Qt.LeftButton)
            QApplication.processEvents()
        finally:
            coordinator.reset_bones = original_reset_bones

        self.assertEqual(len(reset_invocations), 1, "boneResetAuthoringButton must invoke reset_bones once")
        after = _canonical_payload(self.window, self.root)
        after_spec = coordinator.read_spec(self.root)
        after_bones = sorted(after_spec.bones, key=lambda bone: bone.index)
        self.assertEqual([bone.binding_identity for bone in after_bones], list(pending_order))
        self.assertEqual([bone.index for bone in after_bones], [0, 1])
        self.assertEqual(
            {bone.binding_identity for bone in after_bones},
            set(bindings),
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self._emit_bone_action_witness(
            "bone.reset_authoring",
            "objectName=boneResetAuthoringButton",
            "BonePresenter.reset_authoring -> MayaModelAuthoringCoordinator.reset_bones",
            "spec_order_index_bindings_and_undo_redo",
            len(reset_invocations),
        )

    def _material_case(self, evidence):
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(-1)
        self._observe_surface_signal(
            "material.list",
            "MaterialPresenter.on_material_selected",
            view.material_list,
            view.material_list.currentItemChanged,
        )
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0].binding_identity
        binding_before = _node_footprint(binding)
        self._observe_surface_signal(
            "material.name_en",
            "MaterialPresenter._on_value_changed",
            view.material_en_name_edit,
            view.material_en_name_edit.textChanged,
        )
        view.material_en_name_edit.setText("UI Material")
        self._observe_surface_signal(
            "material.apply", "MaterialPresenter.apply_changes", view.apply_btn
        )
        view.apply_btn.click()
        QApplication.processEvents()
        after = _canonical_payload(self.window, self.root)
        binding_after = _node_footprint(binding)
        self.assertEqual(after["spec"]["materials"][0]["name_english"], "UI Material")
        self.assertEqual(
            self.window.authoring_composition.coordinator.read_spec(self.root).materials[0].binding_identity,
            binding,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        footprint = _footprint_delta(binding_before, binding_after)
        self.assertEqual(_changed_spec_sections(before, after), ["materials"])
        self.assertEqual(footprint["attributes_changed"], [ATTR_MMD_MATERIAL_NAME_EN])
        self.assertFalse(footprint["connections_added"])
        self.assertFalse(footprint["connections_removed"])
        self._emit_surface_witness(
            "material.list",
            "gui.authoring_signal_smoke",
            selector="materialList",
            interaction="QListWidget.setCurrentRow(materialList, 0)",
            fired_action="MaterialPresenter.on_material_selected",
            oracle="selected_material_binding_and_semantic_spec_undo_redo",
        )
        self._emit_surface_witness(
            "material.name_en",
            "gui.authoring_signal_smoke",
            selector="materialNameEnEdit",
            interaction="QLineEdit.setText(materialNameEnEdit, 'UI Material')",
            fired_action="MaterialPresenter._on_value_changed",
            oracle="material_name_english_and_maya_footprint_undo_redo",
        )
        self._emit_surface_witness(
            "material.apply",
            "gui.authoring_signal_smoke",
            selector="materialApplyButton",
            interaction="QTest.mouseClick(materialApplyButton); Maya Undo/Redo",
            fired_action="MaterialPresenter.apply_changes",
            oracle="material_spec_maya_footprint_undo_redo",
        )
        evidence.update(
            selector="materialApplyButton",
            selected_binding=binding,
            before=_fingerprint(before),
            after=_fingerprint(after),
            changed_spec_sections=_changed_spec_sections(before, after),
            footprint=footprint,
        )

    def _bone_case(self, evidence):
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(-1)
        self._observe_surface_signal(
            "bone.list",
            "BonePresenter.on_bone_selected",
            view.bone_list,
            view.bone_list.currentItemChanged,
        )
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity
        binding_before = _node_footprint(binding)
        self._observe_surface_signal(
            "bone.name_en",
            "BonePresenter.apply_changes",
            view.bone_name_en_edit,
            view.bone_name_en_edit.textChanged,
        )
        view.bone_name_en_edit.setText("UI Root")
        coordinator = self.window.authoring_composition.coordinator
        original_apply_bone_value_patch = coordinator.apply_bone_value_patch
        action_invocations = []
        clicked_invocations = []

        def observe_apply_bone_value_patch(*args, **kwargs):
            action_invocations.append("MayaModelAuthoringCoordinator.apply_bone_value_patch")
            return original_apply_bone_value_patch(*args, **kwargs)

        def observe_apply_click(*_args):
            clicked_invocations.append("boneApplyButton.clicked")

        coordinator.apply_bone_value_patch = observe_apply_bone_value_patch
        view.apply_btn.clicked.connect(observe_apply_click)
        try:
            view.apply_btn.click()
            QApplication.processEvents()
        finally:
            view.apply_btn.clicked.disconnect(observe_apply_click)
            coordinator.apply_bone_value_patch = original_apply_bone_value_patch
        self.assertEqual(len(clicked_invocations), 1, "boneApplyButton must emit clicked exactly once")
        self.assertEqual(
            len(action_invocations),
            1,
            "boneApplyButton must invoke apply_bone_value_patch exactly once",
        )
        after = _canonical_payload(self.window, self.root)
        binding_after = _node_footprint(binding)
        self.assertEqual(after["spec"]["bones"][0]["name_english"], "UI Root")
        self.assertEqual(
            self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity,
            binding,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        footprint = _footprint_delta(binding_before, binding_after)
        self.assertEqual(_changed_spec_sections(before, after), ["bones"])
        self.assertEqual(footprint["attributes_changed"], [ATTR_MMD_BONE_NAME_EN])
        self.assertFalse(footprint["connections_added"])
        self.assertFalse(footprint["connections_removed"])
        self._emit_surface_witness(
            "bone.list",
            "gui.authoring_signal_smoke",
            selector="boneList",
            interaction="QListWidget.setCurrentRow(boneList, 0)",
            fired_action="BonePresenter.on_bone_selected",
            oracle="selected_bone_binding_and_semantic_spec_undo_redo",
        )
        self._emit_surface_witness(
            "bone.name_en",
            "gui.authoring_signal_smoke",
            selector="boneNameEnEdit",
            interaction="QLineEdit.setText(boneNameEnEdit, 'UI Root')",
            fired_action="BonePresenter.apply_changes",
            oracle="bone_name_english_and_maya_footprint_undo_redo",
        )
        runtime_witness = {
            "interaction": "click(boneApplyButton)",
            "fired_action": action_invocations[0],
            "oracle": "bone_spec_maya_footprint_undo_redo",
            "action_count": len(action_invocations),
        }
        surface_witness = {
            "surface_id": "bone.apply",
            "case_id": "gui.authoring_signal_smoke",
            "selector": "boneApplyButton",
            "status": "pass",
            "runtime_witness": runtime_witness,
        }
        self.report["surfaces"].append(surface_witness)
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(surface_witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
        evidence.update(
            selector="boneApplyButton",
            selected_binding=binding,
            before=_fingerprint(before),
            after=_fingerprint(after),
            changed_spec_sections=_changed_spec_sections(before, after),
            footprint=footprint,
        )

    def _morph_case(self, evidence):
        view = self.window.morph_presenter.view
        # Morph activation is lazy by design; exercise the production tab
        # activation hook before clicking its authoring toolbar.
        self.window.tab_widget.setCurrentWidget(self.window.import_export_tab)
        QApplication.processEvents()
        self.window.tab_widget.setCurrentWidget(view)
        QApplication.processEvents()
        self.assertTrue(self.window.morph_presenter._authoring_ready)
        self.assertTrue(view.create_morph_btn.isEnabled(), view.create_morph_btn.toolTip())
        before = _canonical_payload(self.window, self.root)
        nodes_before = set(cmds.ls(long=True) or [])
        registry = model_registry.get_model_registry(self.root)
        registry_before = _node_footprint(registry)
        choices = []
        clicks = []

        def choose_group(capabilities):
            choices.append(tuple(capabilities))
            return "group"

        view.create_morph_type_provider = choose_group
        self._observe_surface_signal(
            "morph.create", "MorphPresenter.create_morph", view.create_morph_btn
        )
        view.create_morph_btn.clicked.connect(lambda: clicks.append(True))
        try:
            view.create_morph_btn.click()
            QApplication.processEvents()
        finally:
            view.create_morph_type_provider = None
        self.assertTrue(clicks, "morphCreateButton did not emit clicked")
        self.assertTrue(choices, "morph create-type provider was not invoked")
        after = _canonical_payload(self.window, self.root)
        registry_after = _node_footprint(registry)
        created_nodes = sorted(set(cmds.ls(long=True) or []) - nodes_before)
        self.assertEqual(
            len(after["spec"]["morphs"]),
            len(before["spec"]["morphs"]) + 1,
            "; ".join(self.status_messages),
        )
        self.assertEqual(after["spec"]["morphs"][-1]["morph_type"], "group")
        self.assertEqual(_changed_spec_sections(before, after), ["morphs"])
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertFalse(set(created_nodes) & set(cmds.ls(long=True) or []))
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        self.assertTrue(set(created_nodes) <= set(cmds.ls(long=True) or []))
        self._emit_surface_witness(
            "morph.create",
            "gui.authoring_signal_smoke",
            selector="morphCreateButton",
            interaction="QTest.mouseClick(morphCreateButton); Maya Undo/Redo",
            fired_action="MorphPresenter.create_morph",
            oracle="group_morph_spec_and_registry_nodes_undo_redo",
        )
        evidence.update(
            selector="morphCreateButton",
            before=_fingerprint(before),
            after=_fingerprint(after),
            changed_spec_sections=_changed_spec_sections(before, after),
            footprint={
                **_footprint_delta(registry_before, registry_after),
                "nodes_created": [
                    {"node": node, "node_type": cmds.nodeType(node)} for node in created_nodes
                ],
            },
        )

    def _prepare_morph_preview_fixture(self, count=1):
        presenter = self.window.morph_presenter
        view = presenter.view
        self.window.tab_widget.setCurrentWidget(view)
        QApplication.processEvents()
        view.create_morph_type_provider = lambda _capabilities: "bone"
        try:
            for _index in range(count):
                QTest.mouseClick(view.create_morph_btn, Qt.LeftButton)
                QApplication.processEvents()
        finally:
            view.create_morph_type_provider = None
        presenter.load_morphs()
        QApplication.processEvents()
        self.assertEqual(view.morph_list.count(), count)
        controller = presenter._morph_controller
        self.assertTrue(controller)
        plugs = []
        for row in range(count):
            item = view.morph_list.item(row)
            key = item.data(Qt.UserRole)
            index = int(presenter.morph_data[key]["index"])
            plug = f"{controller}.inputWeight[{index}]"
            self.assertTrue(cmds.objExists(plug), plug)
            plugs.append(plug)
        view.morph_list.setCurrentRow(0)
        QApplication.processEvents()
        self.assertTrue(view.morph_slider.isEnabled())
        return view, tuple(plugs)

    def test_morph_preview_drag_is_one_undo_action(self):
        view, plugs = self._prepare_morph_preview_fixture()
        plug = plugs[0]
        cmds.setAttr(plug, 0.0)
        option = QStyleOptionSlider()
        view.morph_slider.initStyleOption(option)
        handle = view.morph_slider.style().subControlRect(
            QStyle.CC_Slider,
            option,
            QStyle.SC_SliderHandle,
            view.morph_slider,
        )
        QTest.mousePress(view.morph_slider, Qt.LeftButton, pos=handle.center())
        view.morph_slider.setValue(25)
        view.morph_slider.setValue(75)
        QTest.mouseRelease(view.morph_slider, Qt.LeftButton)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.75, places=7)
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.0, places=7)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.75, places=7)

    def test_morph_preview_reset_current_is_one_undo_action(self):
        view, plugs = self._prepare_morph_preview_fixture()
        plug = plugs[0]
        cmds.setAttr(plug, 0.65)
        view.morph_slider.blockSignals(True)
        view.morph_slider.setValue(65)
        view.morph_slider.blockSignals(False)
        QTest.mouseClick(view.reset_slider_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.0, places=7)
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.65, places=7)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.0, places=7)

    def test_morph_preview_reset_all_is_one_undo_action(self):
        view, plugs = self._prepare_morph_preview_fixture(count=2)
        expected = (0.35, 0.8)
        for plug, value in zip(plugs, expected):
            cmds.setAttr(plug, value)
        QTest.mouseClick(view.reset_all_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(tuple(cmds.getAttr(plug) for plug in plugs), (0.0, 0.0))
        cmds.undo()
        for plug, value in zip(plugs, expected):
            self.assertAlmostEqual(cmds.getAttr(plug), value, places=7)
        cmds.redo()
        self.assertEqual(tuple(cmds.getAttr(plug) for plug in plugs), (0.0, 0.0))

    def _display_case(self, evidence):
        view = self.window.display_pane_presenter.view
        self.window.display_pane_presenter.refresh()
        before = _canonical_payload(self.window, self.root)
        root_before = _node_footprint(self.root)
        self._observe_surface_signal(
            "display_pane.add_frame", "DisplayPanePresenter.add_frame", view.add_frame_btn
        )
        view.add_frame_btn.click()
        QApplication.processEvents()
        self._observe_surface_signal(
            "display_pane.frame_name_jp",
            "DisplayPanePresenter.on_frame_properties_changed",
            view.name_jp_edit,
            view.name_jp_edit.textChanged,
        )
        view.name_jp_edit.setText("UI表示枠")
        self._observe_surface_signal(
            "display_pane.frame_name_en",
            "DisplayPanePresenter.on_frame_properties_changed",
            view.name_en_edit,
            view.name_en_edit.textChanged,
        )
        view.name_en_edit.setText("UI Frame")
        self._observe_surface_signal(
            "display_pane.apply", "DisplayPanePresenter.apply", view.apply_btn
        )
        view.apply_btn.click()
        QApplication.processEvents()
        after = _canonical_payload(self.window, self.root)
        root_after = _node_footprint(self.root)
        self.assertEqual(after["spec"], before["spec"])
        self.assertEqual(after["display_frames"][-1]["name_english"], "UI Frame")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        footprint = _footprint_delta(root_before, root_after)
        self.assertEqual(_changed_spec_sections(before, after), [])
        self.assertEqual(footprint["attributes_changed"], [ATTR_MMD_DISPLAY_FRAMES_JSON])
        self.assertFalse(footprint["connections_added"])
        self.assertFalse(footprint["connections_removed"])
        self._emit_surface_witness(
            "display_pane.add_frame",
            "gui.authoring_signal_smoke",
            selector="displayAddFrameButton",
            interaction="QTest.mouseClick(displayAddFrameButton); display frame Apply + Maya Undo/Redo",
            fired_action="DisplayPanePresenter.add_frame",
            oracle="display_frame_added_and_persisted_undo_redo",
        )
        self._emit_surface_witness(
            "display_pane.frame_name_jp",
            "gui.authoring_signal_smoke",
            attribute="name_jp_edit",
            interaction="QLineEdit.setText(name_jp_edit, 'UI表示枠'); display Apply + Maya Undo/Redo",
            fired_action="DisplayPanePresenter.on_frame_properties_changed",
            oracle="display_frame_name_jp_and_maya_json_undo_redo",
        )
        self._emit_surface_witness(
            "display_pane.frame_name_en",
            "gui.authoring_signal_smoke",
            attribute="name_en_edit",
            interaction="QLineEdit.setText(name_en_edit, 'UI Frame'); display Apply + Maya Undo/Redo",
            fired_action="DisplayPanePresenter.on_frame_properties_changed",
            oracle="display_frame_name_en_and_maya_json_undo_redo",
        )
        self._emit_surface_witness(
            "display_pane.apply",
            "gui.authoring_signal_smoke",
            selector="displayApplyButton",
            interaction="QTest.mouseClick(displayApplyButton); Maya Undo/Redo",
            fired_action="DisplayPanePresenter.apply",
            oracle="display_frame_maya_json_undo_redo",
        )
        evidence.update(
            selector="displayApplyButton",
            before=_fingerprint(before),
            after=_fingerprint(after),
            changed_spec_sections=_changed_spec_sections(before, after),
            footprint=footprint,
        )

    def _save_reopen_case(self, evidence):
        before = _canonical_payload(self.window, self.root)
        topology = _semantic_topology(self.window, self.root)
        with tempfile.TemporaryDirectory(prefix="mmd_ui_smoke_") as temp_dir:
            scene_path = Path(temp_dir) / "ui_authoring_ascii.ma"
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(scene_path), open=True, force=True)
        roots = self.window.app_state.scene_model_service.list_mmd_models()
        self.assertEqual(len(roots), 1)
        reopened_root = roots[0]
        self.window.app_state.current_model_root = reopened_root
        QApplication.processEvents()
        after = _canonical_payload(self.window, reopened_root)
        self.assertEqual(after, before)
        self.assertEqual(_semantic_topology(self.window, reopened_root), topology)
        self.root = reopened_root
        evidence.update(
            selector="mainTabWidget",
            fingerprint=_fingerprint(after),
            semantic_topology=topology,
        )


if __name__ == "__main__":
    unittest.main()
