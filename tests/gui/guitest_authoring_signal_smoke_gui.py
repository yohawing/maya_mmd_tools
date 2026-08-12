"""Structured real-Qt smoke for model-authoring signals and Maya persistence."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import maya.cmds as cmds

from mmd_tools.adapters.maya_authoring_e2e import normalize_spec_payload
from mmd_tools.core import model_registry
from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin


def _canonical_payload(window, root):
    spec = normalize_spec_payload(window.authoring_composition.coordinator.read_spec(root))
    display = display_frames_from_json(cmds.getAttr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))
    return {"spec": spec, "display_frames": display}


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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

    def _material_case(self, evidence):
        view = self.window.material_presenter.view
        view.material_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).materials[0].binding_identity
        view.material_en_name_edit.setText("UI Material")
        view.apply_btn.click()
        QApplication.processEvents()
        after = _canonical_payload(self.window, self.root)
        self.assertEqual(after["spec"]["materials"][0]["name_english"], "UI Material")
        self.assertEqual(
            self.window.authoring_composition.coordinator.read_spec(self.root).materials[0].binding_identity,
            binding,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        evidence.update(selector="materialApplyButton", before=_fingerprint(before), after=_fingerprint(after))

    def _bone_case(self, evidence):
        view = self.window.bone_presenter.view
        view.bone_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _canonical_payload(self.window, self.root)
        binding = self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity
        view.bone_name_en_edit.setText("UI Root")
        view.apply_btn.click()
        QApplication.processEvents()
        after = _canonical_payload(self.window, self.root)
        self.assertEqual(after["spec"]["bones"][0]["name_english"], "UI Root")
        self.assertEqual(
            self.window.authoring_composition.coordinator.read_spec(self.root).bones[0].binding_identity,
            binding,
        )
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        evidence.update(selector="boneApplyButton", before=_fingerprint(before), after=_fingerprint(after))

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
        self.assertEqual(
            len(after["spec"]["morphs"]),
            len(before["spec"]["morphs"]) + 1,
            "; ".join(self.status_messages),
        )
        self.assertEqual(after["spec"]["morphs"][-1]["morph_type"], "group")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        evidence.update(selector="morphCreateButton", before=_fingerprint(before), after=_fingerprint(after))

    def _display_case(self, evidence):
        view = self.window.display_pane_presenter.view
        self.window.display_pane_presenter.refresh()
        before = _canonical_payload(self.window, self.root)
        view.add_frame_btn.click()
        QApplication.processEvents()
        view.name_jp_edit.setText("UI表示枠")
        view.name_en_edit.setText("UI Frame")
        view.apply_btn.click()
        QApplication.processEvents()
        after = _canonical_payload(self.window, self.root)
        self.assertEqual(after["spec"], before["spec"])
        self.assertEqual(after["display_frames"][-1]["name_english"], "UI Frame")
        cmds.undo()
        self.assertEqual(_canonical_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_canonical_payload(self.window, self.root), after)
        evidence.update(selector="displayApplyButton", before=_fingerprint(before), after=_fingerprint(after))

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
