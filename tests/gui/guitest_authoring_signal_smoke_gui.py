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
    ATTR_MMD_MODEL_NAME,
)
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QColor, QT_BINDING, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin
from tests.common.ui_action_coverage import QtSignalInvocationSpy

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

    def _author_mixed_persistence_state(self):
        """Author one production mutation in each persisted domain family."""
        material_view = self.window.material_presenter.view
        self.window.tab_widget.setCurrentWidget(material_view)
        self.window.material_presenter.load_materials()
        material_view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        material_view.material_en_name_edit.setText("Persistence Material")
        QTest.mouseClick(material_view.apply_btn, Qt.LeftButton)

        bone_view = self.window.bone_presenter.view
        self.window.tab_widget.setCurrentWidget(bone_view)
        self.window.bone_presenter.load_bones()
        bone_view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        bone_view.bone_name_en_edit.setText("Persistence Bone")
        QTest.mouseClick(bone_view.apply_btn, Qt.LeftButton)

        morph_view = self.window.morph_presenter.view
        self.window.tab_widget.setCurrentWidget(morph_view)
        QApplication.processEvents()
        morph_view.create_morph_type_provider = lambda _capabilities: "group"
        try:
            QTest.mouseClick(morph_view.create_morph_btn, Qt.LeftButton)
        finally:
            morph_view.create_morph_type_provider = None

        display_view = self.window.display_pane_presenter.view
        self.window.tab_widget.setCurrentWidget(display_view)
        self.window.display_pane_presenter.refresh()
        QTest.mouseClick(display_view.add_frame_btn, Qt.LeftButton)
        display_view.name_en_edit.setText("Persistence Display")
        QTest.mouseClick(display_view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()

    def test_authoring_signals_undo_redo_and_save_reopen(self):
        """One mixed authoring scene survives an independent save/reopen boundary."""
        self._author_mixed_persistence_state()
        before = _canonical_payload(self.window, self.root)
        self.assertEqual(before["spec"]["materials"][0]["name_english"], "Persistence Material")
        self.assertEqual(before["spec"]["bones"][0]["name_english"], "Persistence Bone")
        self.assertEqual(before["spec"]["morphs"][-1]["morph_type"], "group")
        self.assertEqual(before["display_frames"][-1]["name_english"], "Persistence Display")
        self._record("authoring.save_reopen", self._save_reopen_case)
        self.report["status"] = "pass"
        self._write_report()



    def _load_dx11_material_fixture(self):
        """Return one production Material view and its DX11 shader binding."""
        from mmd_tools.core import settings

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

        return view, shader

    @staticmethod
    def _outline_state(shader):
        result = {}
        for attr in ("technique", "EdgeSize", "mmd_shader_outline_enabled", "mmdDoubleSided"):
            exists = cmds.attributeQuery(attr, node=shader, exists=True)
            result[attr] = {
                "exists": bool(exists),
                "value": cmds.getAttr(f"{shader}.{attr}") if exists else None,
            }
        return result

    @staticmethod
    def _main_texture_state(shader):
        sources = cmds.listConnections(
            f"{shader}.MainTexture", source=True, destination=False, plugs=True
        ) or []
        source = str(sources[0]) if sources else None
        file_node = source.rsplit(".", 1)[0] if source and "." in source else None
        return {
            "source": source,
            "file_node_type": cmds.nodeType(file_node) if file_node else None,
            "has_main_texture": int(cmds.getAttr(f"{shader}.HasMainTexture")),
        }

    def test_dx11_material_name_outline_apply_undo_redo(self):
        """One Apply owns the name and outline semantic patch."""
        view, shader = self._load_dx11_material_fixture()
        before = _canonical_payload(self.window, self.root)
        before_outline = self._outline_state(shader)
        outline_enabled = not view.shader_outline_check.isChecked()
        view.material_en_name_edit.setText("DX11 Material Edited")
        view.shader_outline_check.setChecked(outline_enabled)
        view.apply_btn.click()
        QApplication.processEvents()
        after_name = _canonical_payload(self.window, self.root)
        after_outline = self._outline_state(shader)
        self.assertEqual(after_name["spec"]["materials"][0]["name_english"], "DX11 Material Edited")
        self.assertEqual(
            bool(after_outline["mmd_shader_outline_enabled"]["value"]),
            outline_enabled,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        self.assertEqual(self._outline_state(shader), before_outline)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_name)
        self.assertEqual(self._outline_state(shader), after_outline)

    def test_dx11_material_diffuse_apply_undo_redo(self):
        """One Apply owns the diffuse semantic and DX11 plug mutation."""
        view, shader = self._load_dx11_material_fixture()
        before = _canonical_payload(self.window, self.root)
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
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_diffuse)

    def test_dx11_material_main_texture_apply_undo_redo(self):
        """One Apply owns the main-texture semantic and DG edge."""
        view, shader = self._load_dx11_material_fixture()
        before_texture = _canonical_payload(self.window, self.root)
        before_texture_state = self._main_texture_state(shader)
        texture_path = (Path(__file__).resolve().parents[1] / "data" / "tex" / "diffuse.png").resolve()
        view.texture_path_edit.setText(str(texture_path))
        view.apply_btn.click()
        QApplication.processEvents()
        after_texture = _canonical_payload(self.window, self.root)
        after_texture_state = self._main_texture_state(shader)
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
        self.assertEqual(self._main_texture_state(shader), before_texture_state)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_texture)
        self.assertEqual(self._main_texture_state(shader), after_texture_state)

    def test_dx11_material_sphere_texture_apply_undo_redo(self):
        """One Apply owns the sphere-texture semantic and DG edge."""
        view, shader = self._load_dx11_material_fixture()
        before = _canonical_payload(self.window, self.root)
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
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
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

    def test_dx11_material_sphere_texture_clear_apply_undo_redo(self):
        """One primary clear Apply removes an existing sphere DG edge."""
        view, shader = self._load_dx11_material_fixture()
        sphere_path = (Path(__file__).resolve().parents[1] / "data" / "tex" / "sph.png").resolve()
        view.sphere_map_path_edit.setText(str(sphere_path))
        view.sphere_mode_combo.setCurrentIndex(1)
        view.apply_btn.click()
        QApplication.processEvents()
        before_clear = _canonical_payload(self.window, self.root)
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
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before_clear)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after_sphere_clear)



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

    def test_bone_list_click_keyboard_routes_canonical_selection_to_one_apply(self):
        """A real row click and keyboard move route one canonical Bone Apply."""
        presenter = self.window.bone_presenter
        view = presenter.view
        self.window.tab_widget.setCurrentWidget(view)
        bindings = self._register_second_bone_fixture()
        QApplication.processEvents()

        self.assertEqual(view.bone_list.count(), 2)
        first_item = view.bone_list.item(0)
        second_item = view.bone_list.item(1)
        first_identity = first_item.data(Qt.UserRole)
        second_identity = second_item.data(Qt.UserRole)
        self.assertEqual((first_identity, second_identity), bindings)
        for item, identity in (
            (first_item, first_identity),
            (second_item, second_identity),
        ):
            self.assertTrue(identity.startswith("|"), identity)
            self.assertNotEqual(item.text(), identity)
            self.assertNotIn(identity, item.text())

        view.bone_list.scrollToItem(first_item)
        QApplication.processEvents()
        first_rect = view.bone_list.visualItemRect(first_item)
        self.assertTrue(first_rect.isValid())
        self.assertTrue(view.bone_list.viewport().rect().contains(first_rect.center()))
        QTest.mouseClick(
            view.bone_list.viewport(),
            Qt.LeftButton,
            pos=first_rect.center(),
        )
        QApplication.processEvents()
        self.assertIs(view.bone_list.currentItem(), first_item)
        self.assertEqual(presenter.current_bone, first_identity)
        self.assertEqual(cmds.ls(selection=True, long=True), [first_identity])

        QTest.keyClick(view.bone_list, Qt.Key_Down)
        QApplication.processEvents()
        self.assertIs(view.bone_list.currentItem(), second_item)
        self.assertEqual(presenter.current_bone, second_identity)
        selected = tuple(cmds.ls(selection=True, long=True) or ())
        self.assertEqual(selected, (second_identity,))
        self.assertEqual(self.window.app_state.current_model_root, self.root)
        resolved_root = self.window.app_state.scene_model_service.resolve_model_from_selection(
            (self.root,)
        )
        self.assertEqual(resolved_root, self.root)
        self.assertEqual(self.window.app_state.current_model_root, self.root)

        before_first = cmds.getAttr(f"{first_identity}.{ATTR_MMD_BONE_NAME_EN}")
        before_second = cmds.getAttr(f"{second_identity}.{ATTR_MMD_BONE_NAME_EN}")
        edited_name = "Keyboard Selected Bone"
        view.bone_name_en_edit.setText(edited_name)
        coordinator = self.window.authoring_composition.coordinator
        with patch.object(
            coordinator,
            "apply_bone_value_patch",
            wraps=coordinator.apply_bone_value_patch,
        ) as apply_action:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
            self.assertEqual(apply_action.call_count, 1)

        self.assertEqual(cmds.getAttr(f"{first_identity}.{ATTR_MMD_BONE_NAME_EN}"), before_first)
        self.assertEqual(cmds.getAttr(f"{second_identity}.{ATTR_MMD_BONE_NAME_EN}"), edited_name)
        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{second_identity}.{ATTR_MMD_BONE_NAME_EN}"), before_second)
        cmds.redo()
        self.assertEqual(cmds.getAttr(f"{second_identity}.{ATTR_MMD_BONE_NAME_EN}"), edited_name)

    def test_material_list_click_keyboard_routes_canonical_selection_to_one_apply(self):
        """Real row navigation routes one Apply to only the selected material."""
        presenter = self.window.material_presenter
        view = presenter.view
        coordinator = self.window.authoring_composition.coordinator
        self.window.tab_widget.setCurrentWidget(view)

        coordinator.create_material(self.root)
        cmds.namespace(add="uiMaterialSelection")
        for material in coordinator.read_spec(self.root).materials:
            leaf = material.binding_identity.rsplit(":", 1)[-1]
            cmds.rename(
                material.binding_identity,
                "uiMaterialSelection:{}".format(leaf),
            )
        presenter.load_materials()
        QApplication.processEvents()

        projection = presenter._material_list_projection
        self.assertIsNotNone(projection)
        self.assertEqual(projection.root_identity, self.root)
        self.assertEqual(view.material_list.count(), 2)
        projected = projection.items
        self.assertEqual(tuple(item.index for item in projected), (0, 1))
        rows = (view.material_list.item(0), view.material_list.item(1))
        for row, item in zip(rows, projected):
            identity = row.data(Qt.UserRole)
            index = row.data(Qt.UserRole + 1)
            self.assertEqual(identity, item.binding_identity)
            self.assertEqual(index, item.index)
            self.assertTrue(identity.startswith("uiMaterialSelection:"), identity)
            self.assertNotIn(identity, row.text())
            self.assertNotEqual(row.toolTip(), identity)

        first_item, second_item = rows
        first_identity = projected[0].binding_identity
        second_identity = projected[1].binding_identity
        view.material_list.scrollToItem(first_item)
        QApplication.processEvents()
        first_rect = view.material_list.visualItemRect(first_item)
        self.assertTrue(first_rect.isValid())
        self.assertTrue(view.material_list.viewport().rect().contains(first_rect.center()))
        QTest.mouseClick(
            view.material_list.viewport(),
            Qt.LeftButton,
            pos=first_rect.center(),
        )
        QApplication.processEvents()
        self.assertIs(view.material_list.currentItem(), first_item)
        self.assertEqual(presenter.current_material, first_identity)
        self.assertEqual(presenter.current_material_index, 0)
        self.assertEqual(tuple(cmds.ls(selection=True, long=True) or ()), (first_identity,))

        QTest.keyClick(view.material_list, Qt.Key_Down)
        QApplication.processEvents()
        self.assertIs(view.material_list.currentItem(), second_item)
        self.assertEqual(presenter.current_material, second_identity)
        self.assertEqual(presenter.current_material_index, 1)
        self.assertEqual(tuple(cmds.ls(selection=True, long=True) or ()), (second_identity,))

        before_first = cmds.getAttr(f"{first_identity}.{ATTR_MMD_MATERIAL_NAME_EN}")
        before_second = cmds.getAttr(f"{second_identity}.{ATTR_MMD_MATERIAL_NAME_EN}")
        edited_name = "Keyboard Selected Material"
        view.material_en_name_edit.setText(edited_name)
        with patch.object(
            coordinator,
            "apply_material_value_patch",
            wraps=coordinator.apply_material_value_patch,
        ) as apply_action:
            QTest.mouseClick(view.apply_btn, Qt.LeftButton)
            QApplication.processEvents()
            self.assertEqual(apply_action.call_count, 1)

        self.assertEqual(
            cmds.getAttr(f"{first_identity}.{ATTR_MMD_MATERIAL_NAME_EN}"),
            before_first,
        )
        self.assertEqual(
            cmds.getAttr(f"{second_identity}.{ATTR_MMD_MATERIAL_NAME_EN}"),
            edited_name,
        )
        cmds.undo()
        self.assertEqual(
            cmds.getAttr(f"{second_identity}.{ATTR_MMD_MATERIAL_NAME_EN}"),
            before_second,
        )
        cmds.redo()
        self.assertEqual(
            cmds.getAttr(f"{second_identity}.{ATTR_MMD_MATERIAL_NAME_EN}"),
            edited_name,
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


    def _save_reopen_case(self, evidence):
        before = _canonical_payload(self.window, self.root)
        topology = _semantic_topology(self.window, self.root)
        spec = self.window.authoring_composition.coordinator.read_spec(self.root)
        dg_before = {
            "material": _node_footprint(spec.materials[0].binding_identity),
            "bone": _node_footprint(spec.bones[0].binding_identity),
            "morph": _node_footprint(spec.morphs[-1].binding_identity),
            "display": _node_footprint(self.root),
        }
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
        reopened_spec = self.window.authoring_composition.coordinator.read_spec(reopened_root)
        dg_after = {
            "material": _node_footprint(reopened_spec.materials[0].binding_identity),
            "bone": _node_footprint(reopened_spec.bones[0].binding_identity),
            "morph": _node_footprint(reopened_spec.morphs[-1].binding_identity),
            "display": _node_footprint(reopened_root),
        }
        for domain in ("material", "bone", "morph", "display"):
            self.assertEqual(dg_after[domain]["attributes"], dg_before[domain]["attributes"])
            self.assertEqual(dg_after[domain]["connections"], dg_before[domain]["connections"])
        self.root = reopened_root
        evidence.update(
            selector="mainTabWidget",
            fingerprint=_fingerprint(after),
            semantic_topology=topology,
            authored_domains=["material", "bone", "morph", "display"],
        )


if __name__ == "__main__":
    unittest.main()
