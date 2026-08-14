"""Runtime GUI witnesses for the remaining Display Pane and Physics surfaces.

The checked-in coverage inventory intentionally remains the ownership of the
release gate.  This test adds the real-Qt/Maya witnesses for the currently
``not_run`` controls without changing that shared inventory: every witness is
printed only after a production ``MainWindow`` signal, a semantic Maya oracle,
and (for scene mutations) an Undo/Redo round trip have succeeded.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_DISPLAY_FRAMES_JSON
from mmd_tools.core.display_frame_metadata import display_frames_from_json
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QT_BINDING, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin
from tests.common.ui_action_coverage import build_surface_witness

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


class QSignalSpy:
    """Small Qt signal counter compatible with Maya's bundled PySide2.

    Autodesk's Maya 2024 PySide2 build does not expose ``QtTest.QSignalSpy``.
    A direct signal connection provides the count contract needed by these
    GUI checks without depending on a binding-specific testing helper.
    """

    def __init__(self, signal, source_control=None, action_name="unassigned"):
        self._count = 0
        self.action_name = action_name
        self.source_control = source_control
        signal.connect(self._on_signal)

    def _on_signal(self, *_args):
        self._count += 1

    def count(self):
        return self._count

    @property
    def action_count(self):
        return self._count


_LAST_ACTION = [None, None]


def _remember_action(spy, control):
    _LAST_ACTION[:] = [spy, control]
    return spy


DISPLAY_SURFACES = {
    "display_pane.delete_frame": "objectName=displayDeleteFrameButton",
    "display_pane.move_frame_up": "objectName=displayMoveFrameUpButton",
    "display_pane.move_frame_down": "objectName=displayMoveFrameDownButton",
    "display_pane.refresh": "objectName=displayRefreshButton",
    "display_pane.frames": "objectName=displayFrameList",
    "display_pane.special_frame": "objectName=displaySpecialFrameCheck",
    "display_pane.add_element": "objectName=displayAddElementButton",
    "display_pane.delete_item": "objectName=displayDeleteItemButton",
    "display_pane.move_item_up": "objectName=displayMoveItemUpButton",
    "display_pane.move_item_down": "objectName=displayMoveItemDownButton",
    "display_pane.items": "objectName=displayItemTable",
    "display_pane.reset": "objectName=displayResetButton",
}

PHYSICS_SURFACES = {
    "physics.refresh": "objectName=physicsRefreshButton",
    "physics.show_colliders": "objectName=physicsShowCollidersCheck",
    "physics.enable_physics": "objectName=physicsEnableCheck",
    "physics.rigid_search": "objectName=rigidBodySearchEdit",
    "physics.joint_search": "objectName=jointSearchEdit",
    "physics.rigid_related_bone": "objectName=rigidRelatedBoneCombo",
    "physics.rigid_shape_size": "objectName=physicsRigidShapeSizeEdit",
    "physics.rigid_position": "objectName=physicsRigidPositionEdit",
    "physics.rigid_rotation": "objectName=physicsRigidRotationEdit",
    "physics.joint_body_a": "objectName=jointRigidBodyACombo",
    "physics.joint_body_b": "objectName=jointRigidBodyBCombo",
    "physics.joint_position": "objectName=physicsJointPositionEdit",
    "physics.joint_rotation": "objectName=physicsJointRotationEdit",
}

DISPLAY_TEST_ID = (
    "tests.gui.guitest_ui_coverage_display_physics_remaining_gui."
    "TestDisplayPaneRemainingGUI.test_display_pane_remaining_surfaces"
)
PHYSICS_TEST_ID = (
    "tests.gui.guitest_ui_coverage_display_physics_remaining_gui."
    "TestPhysicsRemainingGUI.test_physics_remaining_surfaces"
)

DISPLAY_FIXTURE_ID = "pmx20-basic-v1"
PHYSICS_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"
)


def _spy_count(spy):
    """Read QSignalSpy consistently on PySide2 and PySide6."""

    count = getattr(spy, "count", None)
    return int(count()) if callable(count) else len(spy)


def _qtest_click(widget, signal=None):
    """Activate a visible widget and return exactly its emitted signal count.

    Maya 2024's embedded Qt can ignore a synthetic mouse coordinate for a
    checkbox nested in a splitter.  Space-key activation is still a real Qt
    interaction on the same production widget and is used only when the mouse
    click emitted no signal.
    """

    signal = signal or widget.clicked
    spy = QSignalSpy(signal)
    _remember_action(spy, widget)
    widget.setFocus()
    QTest.mouseClick(widget, Qt.LeftButton)
    QApplication.processEvents()
    if _spy_count(spy) == 0:
        QTest.keyClick(widget, Qt.Key_Space)
        QApplication.processEvents()
    return _spy_count(spy)


def _qtest_list_row(list_widget, row):
    """Select a row with one real keyboard navigation signal."""

    item = list_widget.item(row)
    if item is None:
        raise AssertionError(f"missing list row {row}")
    if list_widget.count() < 2:
        raise AssertionError("keyboard row selection requires at least two rows")
    # Move to an adjacent preparation row before installing the spy.  Home or
    # Down then reaches the requested row in exactly one user-level key event;
    # the preparation assignment is intentionally outside the observed action.
    if row == 0:
        list_widget.setCurrentRow(1)
        key = Qt.Key_Home
    else:
        list_widget.setCurrentRow(row - 1)
        key = Qt.Key_Down
    list_widget.setFocus()
    spy = QSignalSpy(list_widget.currentRowChanged)
    _remember_action(spy, list_widget)
    QTest.keyClick(list_widget, key)
    QApplication.processEvents()
    if list_widget.currentRow() != row:
        raise AssertionError(
            f"keyboard row selection reached {list_widget.currentRow()}, expected {row}"
        )
    return _spy_count(spy)


def _qtest_set_combo_index(combo, index, action_name="unassigned"):
    """Choose a combo entry with keyboard events and return signal count."""

    if not 0 <= index < combo.count():
        raise AssertionError(f"combo index {index} outside 0..{combo.count() - 1}")
    current = combo.currentIndex()
    if abs(index - current) != 1:
        raise AssertionError(
            f"combo index transition must be adjacent: current={current}, target={index}"
        )
    spy = QSignalSpy(
        combo.currentIndexChanged,
        source_control=combo,
        action_name=action_name,
    )
    _remember_action(spy, combo)
    QTest.mouseClick(combo, Qt.LeftButton)
    key = Qt.Key_Down if index > current else Qt.Key_Up
    QTest.keyClick(combo, key)
    QTest.keyClick(combo, Qt.Key_Enter)
    QApplication.processEvents()
    return _spy_count(spy)


def _qtest_set_spin(spin, value):
    """Edit a QDoubleSpinBox through its line edit using QTest keyboard input."""

    line_edit = spin.lineEdit()
    QTest.mouseClick(line_edit, Qt.LeftButton)
    QTest.keyClick(line_edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(line_edit, str(value))
    QTest.keyClick(line_edit, Qt.Key_Enter)
    QApplication.processEvents()


def _adjacent_combo_index(combo):
    """Return a valid neighboring index so one key emits one change."""

    current = combo.currentIndex()
    if current < combo.count() - 1:
        return current + 1
    return current - 1


def _display_payload(root):
    return display_frames_from_json(cmds.getAttr(f"{root}.{ATTR_MMD_DISPLAY_FRAMES_JSON}"))


def _import_physics_fixture(path, namespace):
    return import_mmd_file(
        str(path),
        options={
            "import_physics": True,
            "create_mmd_shaders": False,
            "setup_rig": False,
            "use_cpp_fast_load": False,
            "use_native_pmx_parse": False,
            "require_native_pmx_parse": False,
            "use_namespace": True,
            "custom_namespace": namespace,
        },
    )


@requires_gui
class TestDisplayPaneRemainingGUI(GuiTestBase):
    """Exercise every remaining Display Pane control through MainWindow."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(Path(__file__).resolve().parents[2], cmds_module=cmds)
        self.window = MainWindow()
        self.template = self.window.authoring_composition.model_initializer.create(
            DISPLAY_FIXTURE_ID, "Display Coverage JP", "Display Coverage EN"
        )
        self.root = self.template.root
        self.window.show()
        self.window.tab_widget.setCurrentWidget(self.window.display_pane_tab)
        self.window.app_state.current_model_root = self.root
        QApplication.processEvents()

        # Two descendants provide deterministic, distinct bone candidates for
        # the item move-up/down controls.  Registration is the same production
        # path used by the authoring UI, not a direct metadata shortcut.
        coordinator = self.window.authoring_composition.coordinator
        for name, y in (("displayCoverageBoneA", 1.0), ("displayCoverageBoneB", -1.0)):
            joint = cmds.createNode("joint", name=name, parent=self.root)
            cmds.xform(joint, translation=(0.0, y, 0.0), worldSpace=True)
            cmds.select(joint, replace=True)
            coordinator.register_selected_joint(self.root, joint)
        self.window.bone_presenter.load_bones()
        self.presenter = self.window.display_pane_presenter
        self.presenter.refresh()
        QApplication.processEvents()

    def tearDown(self):
        try:
            self.window.close()
            self.window.deleteLater()
            QApplication.processEvents()
        finally:
            super().tearDown()

    def _witness(
        self,
        surface_id,
        interaction,
        fired_action,
        oracle,
        action_spy=None,
        control=None,
    ):
        if action_spy is None or control is None:
            action_spy, control = _LAST_ACTION
            action_spy.action_name = fired_action
        payload = build_surface_witness(
            surface_id=surface_id,
            case_id="gui.display_pane_tab",
            selector=DISPLAY_SURFACES[surface_id],
            interaction=interaction,
            oracle=oracle,
            action_spy=action_spy,
            control=control,
        )
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def test_display_pane_remaining_surfaces(self):
        """One Display Apply owns the metadata JSON and one Maya Undo item."""
        view = self.presenter.view
        view.frame_list.setCurrentRow(0)
        QApplication.processEvents()
        before = _display_payload(self.root)
        view.name_en_edit.setText("Display Atomic Witness")
        apply_spy = QSignalSpy(view.apply_btn.clicked)
        QTest.mouseClick(view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(_spy_count(apply_spy), 1)
        after = _display_payload(self.root)
        self.assertNotEqual(after, before)
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), "Edit Display Frames")
        cmds.undo()
        self.assertEqual(_display_payload(self.root), before)
        cmds.redo()
        self.assertEqual(_display_payload(self.root), after)



@requires_gui
class TestPhysicsRemainingGUI(GuiTestBase):
    """Exercise the remaining Physics controls against imported Maya nodes."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(Path(__file__).resolve().parents[2], cmds_module=cmds)
        self.root = _import_physics_fixture(PHYSICS_FIXTURE_PATH, "PhysicsCoverage")
        self.window = MainWindow()
        self.window.show()
        self.window.tab_widget.setCurrentWidget(self.window.physics_tab)
        self.window.app_state.current_model_root = self.root
        QApplication.processEvents()
        self.presenter = self.window.physics_presenter
        self.presenter.refresh_physics(force=True)
        QApplication.processEvents()

    def tearDown(self):
        try:
            self.window.close()
            self.window.deleteLater()
            QApplication.processEvents()
        finally:
            super().tearDown()

    def _witness(
        self,
        surface_id,
        interaction,
        fired_action,
        oracle,
        action_spy=None,
        control=None,
    ):
        if action_spy is None or control is None:
            action_spy, control = _LAST_ACTION
            action_spy.action_name = fired_action
        payload = build_surface_witness(
            surface_id=surface_id,
            case_id="gui.physics_tab",
            selector=PHYSICS_SURFACES[surface_id],
            interaction=interaction,
            oracle=oracle,
            action_spy=action_spy,
            control=control,
        )
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def test_physics_remaining_surfaces(self):
        """One rigid-body binding Apply reaches Maya and remains undoable."""
        view = self.presenter.view
        view.rigid_body_list.setCurrentRow(0)
        QApplication.processEvents()
        shape = self.presenter._current_shape
        combo = view.rigid_related_bone_combo
        self.assertIsNotNone(shape)
        self.assertGreater(combo.count(), 1)
        before = view.binding_selection("rigid_related_bone")
        combo.setCurrentIndex(_adjacent_combo_index(combo))
        expected = view.binding_selection("rigid_related_bone")
        self.assertNotEqual(expected, before)
        QTest.mouseClick(view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(view.binding_selection("rigid_related_bone"), expected)
        cmds.undo()
        self.assertEqual(
            int(cmds.getAttr(f"{shape}.relatedBoneIndex")),
            int(before[1]),
        )
        cmds.redo()
        self.presenter.refresh_physics(force=True)
        view.rigid_body_list.setCurrentRow(0)
        QApplication.processEvents()
        self.assertEqual(view.binding_selection("rigid_related_bone"), expected)

    def test_physics_joint_binding_message(self):
        """One joint Apply owns both message edges and one Undo/Redo item."""
        view = self.presenter.view
        view.list_tabs.setCurrentIndex(1)
        view.joint_list.setCurrentRow(0)
        QApplication.processEvents()
        shape = self.presenter._current_shape
        combo_a = view.joint_body_a_combo
        combo_b = view.joint_body_b_combo
        self.assertIsNotNone(shape)
        self.assertGreater(combo_a.count(), 2)

        def message_state():
            return {
                key: tuple(
                    cmds.ls(node, long=True)[0]
                    for node in (
                        cmds.listConnections(
                            f"{shape}.{key}",
                            source=True,
                            destination=False,
                        )
                        or ()
                    )
                )
                for key in ("rigidBodyA", "rigidBodyB")
            }

        before_selection = {
            "joint_body_a": view.binding_selection("joint_body_a"),
            "joint_body_b": view.binding_selection("joint_body_b"),
        }
        before_messages = message_state()
        combo_a.setCurrentIndex(_adjacent_combo_index(combo_a))
        expected_a = view.binding_selection("joint_body_a")
        target_b = next(
            index
            for index in range(combo_b.count() - 1, -1, -1)
            if combo_b.itemData(index) not in {before_selection["joint_body_b"], expected_a}
        )
        combo_b.setCurrentIndex(target_b)
        expected_b = view.binding_selection("joint_body_b")
        QTest.mouseClick(view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()
        after_messages = message_state()
        self.assertNotEqual(after_messages, before_messages)
        self.assertEqual(tuple(cmds.ls(expected_a[0], long=True) or ()), after_messages["rigidBodyA"])
        self.assertEqual(tuple(cmds.ls(expected_b[0], long=True) or ()), after_messages["rigidBodyB"])
        self.assertEqual(cmds.undoInfo(query=True, undoName=True), "MMD Physics Edit")
        cmds.undo()
        self.assertEqual(message_state(), before_messages)
        cmds.redo()
        self.assertEqual(message_state(), after_messages)
        self.presenter.refresh_physics(force=True)
        view.list_tabs.setCurrentIndex(1)
        view.joint_list.setCurrentRow(0)
        QApplication.processEvents()
        self.assertEqual(view.binding_selection("joint_body_a"), expected_a)
        self.assertEqual(view.binding_selection("joint_body_b"), expected_b)



if __name__ == "__main__":
    unittest.main()
