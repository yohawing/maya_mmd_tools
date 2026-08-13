"""Structured real-Qt smoke for model-authoring signals and Maya persistence."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.adapters.maya_authoring_e2e import normalize_spec_payload
from mmd_tools.core import model_registry
from mmd_tools.core.constants import (
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_MATERIAL_NAME_EN,
)
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QColor, QMessageBox, QT_BINDING, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


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
            "status": "running",
        }

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

    def test_authoring_signals_undo_redo_and_save_reopen(self):
        self._record("authoring.material.value_apply", self._material_case)
        self._record("authoring.bone.value_apply", self._bone_case)
        self._record("authoring.morph.create", self._morph_case)
        self._record("authoring.display_frame.apply", self._display_case)
        self._record("authoring.save_reopen", self._save_reopen_case)
        self.report["status"] = "pass"
        self._write_report()

    def test_material_value_controls_apply_reset_and_undo(self):
        """Exercise every non-texture Material value control through Qt signals."""
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)

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
        for control, checked in flag_values:
            control.setChecked(checked)
        view.edge_size_spin.setValue(1.25)
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

        view.search_edit.setText("UI材質")
        QApplication.processEvents()
        self.assertFalse(view.material_list.item(0).isHidden())
        view.search_edit.clear()
        view.material_jp_name_edit.setText("discarded")
        view.transparency_spin.setValue(0.9)
        view.reset_btn.click()
        QApplication.processEvents()
        self.assertEqual(view.material_jp_name_edit.text(), "UI材質")
        self.assertAlmostEqual(view.transparency_spin.value(), 0.25)

        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)

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
        before = _canonical_payload(self.window, self.root)

        view.material_en_name_edit.setText("DX11 Material Edited")
        view.apply_btn.click()
        QApplication.processEvents()
        after_name = _canonical_payload(self.window, self.root)
        self.assertEqual(after_name["spec"]["materials"][0]["name_english"], "DX11 Material Edited")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_name)

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

    def test_material_toolbar_crud_reindex_and_undo(self):
        """Exercise every supported Material toolbar action through real buttons."""
        view = self.window.material_presenter.view
        view.refresh_btn.click()
        QApplication.processEvents()
        self.assertEqual(view.material_list.count(), 1)

        before_create = _canonical_payload(self.window, self.root)
        view.create_btn.click()
        QApplication.processEvents()
        after_create = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_create["spec"]["materials"]), 2)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), "MMD Material Create")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_create)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_create)

        view.refresh_btn.click()
        view.material_list.setCurrentRow(1)
        QApplication.processEvents()
        before_duplicate = _canonical_payload(self.window, self.root)
        view.duplicate_btn.click()
        QApplication.processEvents()
        after_duplicate = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_duplicate["spec"]["materials"]), 3)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_duplicate)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_duplicate)

        view.refresh_btn.click()
        view.material_list.setCurrentRow(2)
        QApplication.processEvents()
        before_move_up = _canonical_payload(self.window, self.root)
        view.reindex_up_btn.click()
        QApplication.processEvents()
        after_move_up = _canonical_payload(self.window, self.root)
        self.assertNotEqual(after_move_up, before_move_up)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_move_up)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_move_up)

        before_move_down = _canonical_payload(self.window, self.root)
        view.reindex_down_btn.click()
        QApplication.processEvents()
        after_move_down = _canonical_payload(self.window, self.root)
        self.assertEqual(after_move_down, before_move_up)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_move_down)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_move_down)

        view.refresh_btn.click()
        view.material_list.setCurrentRow(2)
        QApplication.processEvents()
        before_delete = _canonical_payload(self.window, self.root)
        with patch(
            "mmd_tools.ui.qt_compat.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            view.delete_btn.click()
        QApplication.processEvents()
        after_delete = _canonical_payload(self.window, self.root)
        self.assertEqual(len(after_delete["spec"]["materials"]), 2)
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_delete)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_delete)

    def _material_case(self, evidence):
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0].binding_identity
        binding_before = _node_footprint(binding)
        view.material_en_name_edit.setText("UI Material")
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
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity
        binding_before = _node_footprint(binding)
        view.bone_name_en_edit.setText("UI Root")
        view.apply_btn.click()
        QApplication.processEvents()
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

    def _display_case(self, evidence):
        view = self.window.display_pane_presenter.view
        self.window.display_pane_presenter.refresh()
        before = _canonical_payload(self.window, self.root)
        root_before = _node_footprint(self.root)
        view.add_frame_btn.click()
        QApplication.processEvents()
        view.name_jp_edit.setText("UI表示枠")
        view.name_en_edit.setText("UI Frame")
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
