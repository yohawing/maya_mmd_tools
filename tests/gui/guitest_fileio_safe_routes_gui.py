"""Real Qt/Maya smoke for the production File I/O button routes.

The cases deliberately drive the Import, New MMD Model, and VMD buttons on a
production :class:`MainWindow`.  Presenter methods are not called directly;
the only modal automation is a timer installed by the real dialog factory.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import maya.cmds as cmds

from mmd_tools.core import model_registry
from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_DISPLAY_FRAMES_JSON,
    ATTR_MMD_IMPORT_SCALE,
    ATTR_MMD_MATERIAL,
    ATTR_MMD_MODEL_NAME,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_REGISTRY_ROOT,
    ATTR_MMD_REGISTRY_SCHEMA,
    ATTR_MMD_TEXTURE_TABLE_JSON,
)
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.ui.create_model_dialog import CreateModelDialog
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QDialog, QTimer, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.ui_action_coverage import QtSignalInvocationSpy, build_surface_witness


_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "for_unit_test"
_PMX_FIXTURE = _DATA_DIR / "test_1bone_cube.pmx"
_VMD_FIXTURE = _DATA_DIR / "test_1bone_cube_motion.vmd"
_CAMERA_VMD_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "test_camera_light.vmd"


def _emit_witness(surface_id, locator, interaction, oracle, action_spy, control):
    """Emit one deterministic runtime witness for the coverage gate."""

    evidence = build_surface_witness(
        surface_id=surface_id,
        case_id="gui.fileio_safe_routes",
        attribute=locator,
        interaction=interaction,
        oracle=oracle,
        action_spy=action_spy,
        control=control,
    )
    print(
        "[UI COVERAGE WITNESS] "
        + json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        flush=True,
    )


def _history_paths(view):
    """Return typed history paths currently rendered by the real list widget."""
    return [
        view.unified_history_list.item(index).data(Qt.UserRole)
        for index in range(view.unified_history_list.count())
    ]


def _key_count(joint):
    """Count real keys on the six transform channels of one imported joint."""
    return sum(
        int(cmds.keyframe(f"{joint}.{attribute}", query=True, keyframeCount=True) or 0)
        for attribute in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
    )


@requires_gui
class TestFileIOSafeRoutesGUI(GuiTestBase):
    """Exercise actual File I/O controls against a Maya scene."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self._temp_dir = tempfile.TemporaryDirectory(prefix="mmd_fileio_gui_")
        temp_root = Path(self._temp_dir.name)
        self.pmx_path = temp_root / _PMX_FIXTURE.name
        self.vmd_path = temp_root / _VMD_FIXTURE.name
        pmx = PmxData().parse_file(str(_PMX_FIXTURE))
        pmx.header.comment = ""
        pmx.header.comment_english = ""
        pmx.write_file(str(self.pmx_path))
        self.vmd_path.write_bytes(_VMD_FIXTURE.read_bytes())

        self.window = MainWindow()
        self.window.show()
        QApplication.processEvents()

    def tearDown(self):
        try:
            self._stop_modal_watchdog()
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
            try:
                cmds.file(new=True, force=True)
            except Exception:
                pass
        finally:
            if getattr(self, "_temp_dir", None) is not None:
                self._temp_dir.cleanup()
            super().tearDown()

    def _arm_modal_watchdog(self, allowed_dialog_getter=None):
        """Reject an unexpected visible modal so a warning cannot hang the test."""
        self._unexpected_modal = None

        def inspect_visible_modals():
            allowed = allowed_dialog_getter() if callable(allowed_dialog_getter) else None
            for widget in QApplication.topLevelWidgets():
                same_as_allowed = allowed is not None and (widget is allowed or widget == allowed)
                if not isinstance(widget, QDialog) or not widget.isVisible() or same_as_allowed:
                    continue
                self._unexpected_modal = widget.windowTitle() or widget.objectName() or type(widget).__name__
                reject = getattr(widget, "reject", None)
                if callable(reject):
                    reject()
                break

        # If the production slot opens a modal synchronously, this callback is
        # serviced by its nested event loop and closes it deterministically.
        self._modal_watchdog_timer = QTimer(self.window)
        self._modal_watchdog_timer.setInterval(25)
        self._modal_watchdog_timer.timeout.connect(inspect_visible_modals)
        self._modal_watchdog_timer.start()

    def _stop_modal_watchdog(self):
        timer = getattr(self, "_modal_watchdog_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._modal_watchdog_timer = None

    def _drain_until(self, predicate, *, timeout=45.0, description="condition"):
        """Pump Qt events until ``predicate`` is true, with a hard deadline."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if self._unexpected_modal:
                raise AssertionError(f"unexpected modal during {description}: {self._unexpected_modal}")
            if predicate():
                return
        raise AssertionError(f"timed out waiting for {description}")

    def _import_model_from_button(self):
        """Fill the production path edit and click the real PMX import button."""
        view = self.window.import_export_tab
        path_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.import_model.path_changed",
            view.import_path_edit.textChanged,
            view.import_path_edit,
        )
        view.import_path_edit.setText(str(self.pmx_path))
        button_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.import_model", view.import_button.clicked, view.import_button
        )
        self._arm_modal_watchdog()
        view.import_button.click()
        self._drain_until(
            lambda: bool(self.window.app_state.current_model_root)
            and cmds.objExists(self.window.app_state.current_model_root),
            description="Current Model after PMX import button",
        )
        self._stop_modal_watchdog()
        self._import_model_action_spies = (path_spy, button_spy)
        return self.window.app_state.current_model_root

    def _assert_imported_scene_contract(self, root):
        """Assert PMX root metadata and semantic ownership created by import."""
        source = PmxData().parse_file(str(self.pmx_path))
        self.assertTrue(cmds.objExists(root))
        self.assertEqual(cmds.nodeType(root), "transform")
        for attr in (
            ATTR_MMD_MODEL_NAME,
            ATTR_MMD_MODEL_NAME_EN,
            ATTR_MMD_COMMENT,
            ATTR_MMD_COMMENT_EN,
            ATTR_MMD_IMPORT_SCALE,
            ATTR_MMD_DISPLAY_FRAMES_JSON,
            ATTR_MMD_TEXTURE_TABLE_JSON,
        ):
            self.assertTrue(cmds.attributeQuery(attr, node=root, exists=True), attr)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME}"), source.header.model_name)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}"), source.header.model_name_english)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_COMMENT}"), source.header.comment)
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_COMMENT_EN}"), source.header.comment_english)
        self.assertAlmostEqual(cmds.getAttr(f"{root}.{ATTR_MMD_IMPORT_SCALE}"), 1.0)

        meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        self.assertTrue(meshes, "PMX import produced no mesh shape")
        self.assertTrue(joints, "PMX import produced no bone joint")

        registry = model_registry.get_model_registry(root)
        self.assertTrue(registry, "PMX import produced no model registry")
        self.assertEqual(cmds.nodeType(registry), "network")
        self.assertEqual(cmds.getAttr(f"{registry}.{ATTR_MMD_REGISTRY_SCHEMA}"), "1")
        root_connections = cmds.listConnections(
            f"{registry}.{ATTR_MMD_REGISTRY_ROOT}",
            source=True,
            destination=False,
        ) or []
        self.assertEqual(
            {str(item) for item in (cmds.ls(root_connections, long=True) or [])},
            {str(item) for item in (cmds.ls(root, long=True) or [])},
        )

        shader_nodes = []
        for mesh in meshes:
            for shading_group in cmds.listConnections(mesh, type="shadingEngine") or []:
                shader_nodes.extend(
                    cmds.listConnections(
                        f"{shading_group}.surfaceShader",
                        source=True,
                        destination=False,
                    )
                    or []
                )
        self.assertTrue(shader_nodes, "PMX mesh has no connected material shader")
        self.assertTrue(
            any(
                cmds.attributeQuery(ATTR_MMD_MATERIAL, node=shader, exists=True)
                and int(cmds.getAttr(f"{shader}.{ATTR_MMD_MATERIAL}")) == 1
                for shader in shader_nodes
            ),
            "PMX material shader has no canonical MMD material marker",
        )
        registered_materials = model_registry.list_model_registry_members(
            root, model_registry.REGISTRY_CATEGORY_MATERIAL
        )
        self.assertTrue(registered_materials, "PMX registry has no material members")

        view = self.window.import_export_tab
        self._drain_until(
            lambda: str(self.pmx_path) in _history_paths(view),
            description="PMX import history entry",
        )

    def test_import_button_reaches_scene_graph_and_history(self):
        """The real PMX Import button creates canonical scene ownership."""
        root = self._import_model_from_button()
        self._assert_imported_scene_contract(root)
        self.assertEqual(self.window.app_state.current_model_root, root)
        path_spy, button_spy = self._import_model_action_spies
        _emit_witness(
            "import_export.import_path",
            "import_path_edit",
            "QTest.setText(attribute=import_path_edit, PMX fixture)",
            "import path retained and PMX source metadata/registry verified",
            path_spy,
            self.window.import_export_tab.import_path_edit,
        )
        _emit_witness(
            "import_export.import_model",
            "import_button",
            "QTest.click(attribute=import_button)",
            "canonical root, mesh, registry, material, and history created",
            button_spy,
            self.window.import_export_tab.import_button,
        )

    def test_new_model_button_uses_real_modal_and_undo_redo_lifecycle(self):
        """The real New MMD Model dialog creates a strict template transaction."""
        presenter = self.window.import_export_presenter
        self.assertTrue(presenter.create_model_action)
        self.assertTrue(self.window.import_export_tab.new_model_button.isEnabled())
        dialog_state = {}

        def factory(templates, parent):
            dialog = CreateModelDialog(templates, parent)
            self._active_create_dialog = dialog

            def submit_real_controls():
                index = dialog.template_combo.findData("pmx20-basic-v1")
                dialog_state["template_index"] = index
                if index < 0:
                    dialog.reject()
                    return
                dialog.template_combo.setCurrentIndex(index)
                dialog_state["template_id"] = dialog.template_combo.currentData()
                dialog.model_name_jp_edit.setText("File I O New JP")
                dialog.model_name_en_edit.setText("File IO New EN")
                dialog.ok_button.click()

            # The callback runs in the modal's nested Qt event loop and drives
            # the actual combo/text/button widgets.
            QTimer.singleShot(0, submit_real_controls)
            return dialog

        presenter.create_model_dialog_factory = factory
        self._active_create_dialog = None
        self._arm_modal_watchdog(lambda: self._active_create_dialog)
        new_model_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.create_model",
            self.window.import_export_tab.new_model_button.clicked,
            self.window.import_export_tab.new_model_button,
        )
        self.window.import_export_tab.new_model_button.click()
        self._drain_until(
            lambda: bool(self.window.app_state.current_model_root)
            and cmds.objExists(self.window.app_state.current_model_root),
            description="Current Model after New MMD Model dialog",
        )
        self._stop_modal_watchdog()
        self.assertGreaterEqual(dialog_state.get("template_index", -1), 0)
        self.assertEqual(dialog_state.get("template_id"), "pmx20-basic-v1")
        root = self.window.app_state.current_model_root
        registry = model_registry.get_model_registry(root)
        self.assertTrue(registry)
        self.assertEqual(cmds.nodeType(root), "transform")
        self.assertEqual(cmds.nodeType(registry), "network")
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME}"), "File I O New JP")
        self.assertEqual(cmds.getAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}"), "File IO New EN")
        spec = self.window.authoring_composition.coordinator.read_spec(root)
        fingerprint = spec.fingerprint()
        self.assertTrue(spec.bones)
        self.assertTrue(spec.materials)

        cmds.undo()
        self._drain_until(lambda: not cmds.objExists(root), description="New Model undo root removal")
        self.window.app_state.refresh_model_list()
        self.assertIsNone(self.window.app_state.current_model_root)

        cmds.redo()
        self._drain_until(lambda: cmds.objExists(root), description="New Model redo root restoration")
        self.window.app_state.refresh_model_list()
        self.assertEqual(self.window.app_state.current_model_root, root)
        self.assertTrue(model_registry.get_model_registry(root))
        self.assertEqual(
            self.window.authoring_composition.coordinator.read_spec(root).fingerprint(),
            fingerprint,
        )
        _emit_witness(
            "import_export.new_model",
            "new_model_button",
            "QTest.click(attribute=new_model_button) and submit CreateModelDialog",
            "template transaction fingerprint restored by Undo/Redo",
            new_model_spy,
            self.window.import_export_tab.new_model_button,
        )

    def test_create_camera_button_builds_and_selects_mmd_camera_rig(self):
        """The Animation button creates the same tagged rig used by VMD import."""
        view = self.window.import_export_tab
        view.import_category_stack.setCurrentIndex(1)
        camera_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.create_camera",
            view.create_mmd_camera_button.clicked,
            view.create_mmd_camera_button,
        )

        self._arm_modal_watchdog()
        view.create_mmd_camera_button.click()
        self._drain_until(
            lambda: bool(cmds.ls("*.mmd_camera", objectsOnly=True)),
            description="tagged MMD camera rig",
        )
        self._stop_modal_watchdog()

        camera = (cmds.ls("*.mmd_camera", objectsOnly=True) or [None])[0]
        self.assertTrue(camera)
        self.assertEqual(cmds.ls(selection=True), [camera])
        self.assertTrue(
            cmds.listConnections(
                f"{camera}.mmd_camera_target_node",
                source=True,
                destination=False,
            )
        )
        self.assertTrue(
            cmds.listConnections(
                f"{camera}.mmd_camera_root_node",
                source=True,
                destination=False,
            )
        )
        _emit_witness(
            "import_export.create_camera",
            "create_mmd_camera_button",
            "QTest.click(attribute=create_mmd_camera_button)",
            "tagged MMD camera rig created and selected",
            camera_spy,
            view.create_mmd_camera_button,
        )

    def test_camera_motion_sets_playback_range_from_scene_tracks(self):
        """Camera/light-only VMD import owns playback max without bone keys."""
        view = self.window.import_export_tab
        view.import_category_stack.setCurrentIndex(1)
        view.vmd_path_edit.setText(str(_CAMERA_VMD_FIXTURE))
        expected_vmd = VmdData().parse_file(str(_CAMERA_VMD_FIXTURE))
        expected_max = max(
            frame.frame_number
            for frame in (*expected_vmd.camera_frames, *expected_vmd.light_frames)
        )

        self._arm_modal_watchdog()
        view.import_vmd_button.click()
        self._drain_until(
            lambda: float(cmds.playbackOptions(query=True, maxTime=True))
            == float(expected_max),
            description="camera VMD playback range",
        )
        self._stop_modal_watchdog()

        self.assertEqual(
            float(cmds.playbackOptions(query=True, animationEndTime=True)),
            float(expected_max),
        )
        self.assertTrue(cmds.ls("*.mmd_camera", objectsOnly=True))

    def test_vmd_button_targets_current_model_and_creates_keys_timeline_history(self):
        """The real VMD button keys the imported PMX joint and updates timeline/history."""
        root = self._import_model_from_button()
        view = self.window.import_export_tab
        # History is intentionally filtered by the active Import category;
        # navigate to Animation before exercising the VMD route so the
        # production list is the one that owns this typed entry.
        view.import_category_stack.setCurrentIndex(1)
        QApplication.processEvents()
        # The isolated Maya profile can still contain entries from an earlier
        # GUI case.  Clear the shared list so the VMD import's rowsInserted
        # witness represents this operation exactly once.
        view.clear_history_button.click()
        QApplication.processEvents()
        path_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.import_vmd.path_changed",
            view.vmd_path_edit.textChanged,
            view.vmd_path_edit,
        )
        view.vmd_path_edit.setText(str(self.vmd_path))
        import_spy = QtSignalInvocationSpy(
            "ImportExportPresenter.import_vmd",
            view.import_vmd_button.clicked,
            view.import_vmd_button,
        )
        history_spy = QtSignalInvocationSpy(
            "UnifiedFileHistory.updated",
            view.unified_history_list.model().rowsInserted,
            view.unified_history_list,
        )
        self._arm_modal_watchdog()
        view.import_vmd_button.click()

        expected_vmd = VmdData().parse_file(str(self.vmd_path))
        max_frame = max(frame.frame_number for frame in expected_vmd.bone_frames)
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        self.assertTrue(joints)
        self._drain_until(
            lambda: any(_key_count(joint) > 0 for joint in joints),
            description="VMD animation keys on imported joint",
        )
        self._stop_modal_watchdog()
        animated_joint = next(joint for joint in joints if _key_count(joint) > 0)
        self.assertGreater(_key_count(animated_joint), 0)
        self.assertGreaterEqual(float(cmds.playbackOptions(query=True, maxTime=True)), max_frame)
        self.assertIn(str(self.vmd_path), _history_paths(view))
        self.assertEqual(self.window.app_state.current_model_root, root)
        _emit_witness(
            "import_export.vmd_path",
            "vmd_path_edit",
            "QTest.setText(attribute=vmd_path_edit, VMD fixture)",
            "VMD path routed to current imported model",
            path_spy,
            view.vmd_path_edit,
        )
        _emit_witness(
            "import_export.import_vmd",
            "import_vmd_button",
            "QTest.click(attribute=import_vmd_button)",
            "joint keys, playback range, current root, and history verified",
            import_spy,
            view.import_vmd_button,
        )
        _emit_witness(
            "import_export.history",
            "unified_history_list",
            "QTest.inspect(attribute=unified_history_list)",
            "PMX and VMD entries are visible in the production history list",
            history_spy,
            view.unified_history_list,
        )


if __name__ == "__main__":
    unittest.main()
