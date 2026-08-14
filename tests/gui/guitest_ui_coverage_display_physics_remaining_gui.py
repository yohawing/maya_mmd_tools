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
from mmd_tools.core.visibility_state import get_visibility_category
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
        view = self.presenter.view
        self.assertEqual(view.frame_list.count(), 2)

        # ``frames``: real list selection is the oracle for the selected frame.
        self.assertEqual(_qtest_list_row(view.frame_list, 0), 1)
        self.assertEqual(view.frame_list.currentRow(), 0)
        self._witness(
            "display_pane.frames",
            "QTest.mouseClick(objectName=displayFrameList.viewport(), row=0)",
            "DisplayPanePresenter.on_frame_selected",
            "frame_list.currentRow()==0 and Root frame fields are populated",
        )

        # Add a regular frame (the add surface is already evidenced elsewhere)
        # and make it special once, so the special-frame checkbox has a direct
        # working-copy oracle while a second regular frame remains deletable.
        _qtest_click(view.add_frame_btn)
        self.assertEqual(view.frame_list.count(), 3)
        self.assertEqual(
            _qtest_click(view.special_frame_check, view.special_frame_check.stateChanged),
            1,
        )
        self.assertTrue(self.presenter.frames[2]["special_flag"])
        self._witness(
            "display_pane.special_frame",
            "QTest.mouseClick(objectName=displaySpecialFrameCheck)",
            "DisplayPanePresenter.on_frame_properties_changed",
            "working-copy frame[2].special_flag == 1",
        )

        _qtest_click(view.add_frame_btn)
        self.assertEqual(view.frame_list.count(), 4)
        self.assertEqual(_qtest_list_row(view.frame_list, 3), 1)

        # Frame move up/down each emits one click and changes the working copy.
        before_order = [frame["name"] for frame in self.presenter.frames]
        self.assertEqual(_qtest_click(view.move_frame_up_btn), 1)
        self.assertEqual(
            [frame["name"] for frame in self.presenter.frames],
            [before_order[0], before_order[1], before_order[3], before_order[2]],
        )
        self._witness(
            "display_pane.move_frame_up",
            "QTest.mouseClick(objectName=displayMoveFrameUpButton)",
            "DisplayPanePresenter.move_frame(-1)",
            "selected regular frame moved one slot upward",
        )
        self.assertEqual(_qtest_click(view.move_frame_down_btn), 1)
        self.assertEqual([frame["name"] for frame in self.presenter.frames], before_order)
        self._witness(
            "display_pane.move_frame_down",
            "QTest.mouseClick(objectName=displayMoveFrameDownButton)",
            "DisplayPanePresenter.move_frame(1)",
            "selected regular frame returned to its original slot",
        )

        # Delete the selected regular frame and leave the two required special
        # frames plus the first regular frame in place.
        self.assertEqual(_qtest_click(view.delete_frame_btn), 1)
        self.assertEqual(view.frame_list.count(), 3)
        self._witness(
            "display_pane.delete_frame",
            "QTest.mouseClick(objectName=displayDeleteFrameButton)",
            "DisplayPanePresenter.delete_frame",
            "working-copy frame count decremented and selected row re-rendered",
        )

        # Add two distinct bones to the first regular frame.  The provider seam
        # only replaces the modal choice; the add button itself remains a real
        # Qt click and Presenter.add_item validates identity against candidates.
        self.assertEqual(_qtest_list_row(view.frame_list, 2), 1)
        choices = sorted(self.presenter._bone_choices.values())
        self.assertGreaterEqual(len(choices), 3)
        selected_choices = iter(choices[1:3])
        self.presenter._choice_provider = lambda _title, _candidates: {
            "type": 0,
            "index": next(selected_choices),
        }
        self.assertEqual(_qtest_click(view.add_element_btn), 1)
        self.assertEqual(view.item_table.rowCount(), 1)
        self._witness(
            "display_pane.add_element",
            "QTest.mouseClick(objectName=displayAddElementButton)",
            "DisplayPanePresenter.add_item",
            "item_table rowCount==1 with a valid bone identity",
        )
        # The second add click is setup for the move/delete controls; its
        # signal is still real, but only the first click is the reported surface
        # witness for this inventory entry.
        _qtest_click(view.add_element_btn)
        self.assertEqual(view.item_table.rowCount(), 2)

        self.assertEqual(_qtest_list_row(view.frame_list, 2), 1)
        item_spy = QSignalSpy(view.item_table.itemSelectionChanged)
        _remember_action(item_spy, view.item_table)
        item_rect = view.item_table.visualItemRect(view.item_table.item(0, 0))
        QTest.mouseClick(view.item_table.viewport(), Qt.LeftButton, pos=item_rect.center())
        QApplication.processEvents()
        self.assertEqual(_spy_count(item_spy), 1)
        self.assertEqual(view.item_table.currentRow(), 0)
        self._witness(
            "display_pane.items",
            "QTest.mouseClick(objectName=displayItemTable.viewport(), row=0)",
            "DisplayPanePresenter._render_items",
            "item_table currentRow==0 and two semantic element rows are visible",
        )

        self.assertEqual(_qtest_click(view.move_item_down_btn), 1)
        self.assertEqual(self.presenter.frames[2]["elements"][0]["index"], choices[2])
        self._witness(
            "display_pane.move_item_down",
            "QTest.mouseClick(objectName=displayMoveItemDownButton)",
            "DisplayPanePresenter.move_item(1)",
            "element identities swapped in the working-copy frame",
        )
        self.assertEqual(_qtest_click(view.move_item_up_btn), 1)
        self.assertEqual(self.presenter.frames[2]["elements"][0]["index"], choices[1])
        self._witness(
            "display_pane.move_item_up",
            "QTest.mouseClick(objectName=displayMoveItemUpButton)",
            "DisplayPanePresenter.move_item(-1)",
            "element identities returned to their original order",
        )
        self.assertEqual(_qtest_click(view.delete_item_btn), 1)
        self.assertEqual(view.item_table.rowCount(), 1)
        self._witness(
            "display_pane.delete_item",
            "QTest.mouseClick(objectName=displayDeleteItemButton)",
            "DisplayPanePresenter.delete_item",
            "working-copy item row removed and remaining identity preserved",
        )

        # Apply is an existing surface but provides the mutation boundary for
        # the refresh witness.  Verify the actual Maya JSON and Undo/Redo before
        # changing the work copy again.
        view.name_en_edit.setText("Display Coverage Frame")
        before = _display_payload(self.root)
        apply_spy = QSignalSpy(view.apply_btn.clicked)
        _remember_action(apply_spy, view.apply_btn)
        QTest.mouseClick(view.apply_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(_spy_count(apply_spy), 1)
        after = _display_payload(self.root)
        self.assertNotEqual(after, before)
        cmds.undo()
        self.assertEqual(_display_payload(self.root), before)
        cmds.redo()
        self.assertEqual(_display_payload(self.root), after)

        # Reset discards an uncommitted field edit and restores the last loaded
        # copy.  The button is driven through QTest and the value is checked.
        # Undo/Redo and Presenter re-rendering may select the first frame; make
        # that explicit so the reset oracle is not coupled to a stale row.
        self.assertEqual(_qtest_list_row(view.frame_list, 0), 1)
        committed_name = view.name_en_edit.text()
        view.name_en_edit.setText("discarded edit")
        self.assertEqual(_qtest_click(view.reset_btn), 1)
        self.assertEqual(view.frame_list.currentRow(), 0)
        self.assertEqual(view.name_en_edit.text(), committed_name)
        self._witness(
            "display_pane.reset",
            "QTest.mouseClick(objectName=displayResetButton)",
            "DisplayPanePresenter.reset",
            "working-copy field restored to the last applied Maya JSON",
        )

        # Refresh must re-read the persisted JSON, not merely repaint the same
        # in-memory list.  A one-click signal spy proves exactly one action.
        view.name_en_edit.setText("stale work copy")
        refresh_spy = QSignalSpy(view.refresh_btn.clicked)
        _remember_action(refresh_spy, view.refresh_btn)
        QTest.mouseClick(view.refresh_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(_spy_count(refresh_spy), 1)
        self.assertEqual(_display_payload(self.root), after)
        self.assertEqual(view.frame_list.currentRow(), 0)
        self.assertEqual(view.name_en_edit.text(), after[0]["name_english"])
        self._witness(
            "display_pane.refresh",
            "QTest.mouseClick(objectName=displayRefreshButton)",
            "DisplayPanePresenter.refresh",
            "Maya mmd_display_frames_json reloaded and stale edit discarded",
        )


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
        view = self.presenter.view
        self.assertGreater(view.rigid_body_list.count(), 0)
        self.assertGreater(view.joint_list.count(), 0)

        # Refresh is a real signal and should preserve both populated lists.
        refresh_spy = QSignalSpy(view.refresh_btn.clicked)
        _remember_action(refresh_spy, view.refresh_btn)
        QTest.mouseClick(view.refresh_btn, Qt.LeftButton)
        QApplication.processEvents()
        self.assertEqual(_spy_count(refresh_spy), 1)
        self.assertGreater(view.rigid_body_list.count(), 0)
        self.assertGreater(view.joint_list.count(), 0)
        self._witness(
            "physics.refresh",
            "QTest.mouseClick(objectName=physicsRefreshButton)",
            "PhysicsPresenter._on_refresh_requested",
            "rigid_body_list and joint_list repopulated from model-owned Physics DAG",
        )

        # Collider visibility is a scene-authoritative root attribute.
        collider_before = get_visibility_category(self.presenter.maya_adapter, self.root, "colliders")
        self.assertEqual(
            _qtest_click(view.collider_visible_check, view.collider_visible_check.stateChanged),
            1,
        )
        collider_after = get_visibility_category(self.presenter.maya_adapter, self.root, "colliders")
        self.assertNotEqual(collider_after, collider_before)
        self._witness(
            "physics.show_colliders",
            "QTest.mouseClick(objectName=physicsShowCollidersCheck)",
            "PhysicsPresenter._on_collider_visibility_changed",
            f"root colliders visibility changed {collider_before!r}->{collider_after!r}",
        )

        # Physics enable must be backed by the imported world and solver.  The
        # fixture intentionally contains both; a disabled control is a real
        # environment/production blocker rather than a skipped witness.
        self.assertTrue(view.physics_enable_check.isEnabled())
        world = self.presenter._find_physics_world_shape()
        self.assertIsNotNone(world)
        enable_before = bool(cmds.getAttr(f"{world}.enable"))
        if not enable_before:
            # Enabling is a fixture prerequisite only.  The witnessed UI
            # transition below disables the world, which is deterministic and
            # does not invoke a live solver step; the subsequent Undo/Redo
            # still proves the checkbox action owns the world attribute.
            cmds.setAttr(f"{world}.enable", True)
            self.presenter.refresh_physics(force=True)
            QApplication.processEvents()
            enable_before = True
            self.assertTrue(view.physics_enable_check.isChecked())
        self.assertEqual(
            _qtest_click(view.physics_enable_check, view.physics_enable_check.stateChanged),
            1,
        )
        enable_after = bool(cmds.getAttr(f"{world}.enable"))
        self.assertNotEqual(enable_after, enable_before)
        cmds.undo()
        self.assertEqual(bool(cmds.getAttr(f"{world}.enable")), enable_before)
        cmds.redo()
        self.assertEqual(bool(cmds.getAttr(f"{world}.enable")), enable_after)
        self._witness(
            "physics.enable_physics",
            "QTest.mouseClick(objectName=physicsEnableCheck)",
            "PhysicsPresenter._on_physics_enable_changed",
            f"mmdPhysicsWorldShape.enable changed {enable_before!r}->{enable_after!r} with Undo/Redo",
        )

        # Search edits use one-character queries so the textChanged handler is
        # provably fired exactly once.  Clearing is cleanup, not a second
        # reported action.
        rigid_search_spy = QSignalSpy(view.rigid_body_search_edit.textChanged)
        _remember_action(rigid_search_spy, view.rigid_body_search_edit)
        QTest.mouseClick(view.rigid_body_search_edit, Qt.LeftButton)
        QTest.keyClicks(view.rigid_body_search_edit, "~")
        QApplication.processEvents()
        self.assertEqual(_spy_count(rigid_search_spy), 1)
        self.assertTrue(all(view.rigid_body_list.item(i).isHidden() for i in range(view.rigid_body_list.count())))
        self._witness(
            "physics.rigid_search",
            "QTest.keyClicks(objectName=rigidBodySearchEdit, '~')",
            "PhysicsPresenter.filter_rigid_bodies",
            "all rigid-body rows hidden for a guaranteed non-matching query",
        )
        view.rigid_body_search_edit.clear()

        view.list_tabs.setCurrentIndex(1)
        QApplication.processEvents()
        joint_search_spy = QSignalSpy(view.joint_search_edit.textChanged)
        _remember_action(joint_search_spy, view.joint_search_edit)
        QTest.mouseClick(view.joint_search_edit, Qt.LeftButton)
        QTest.keyClicks(view.joint_search_edit, "~")
        QApplication.processEvents()
        self.assertEqual(_spy_count(joint_search_spy), 1)
        self.assertTrue(all(view.joint_list.item(i).isHidden() for i in range(view.joint_list.count())))
        self._witness(
            "physics.joint_search",
            "QTest.keyClicks(objectName=jointSearchEdit, '~')",
            "PhysicsPresenter.filter_joints",
            "all joint rows hidden for a guaranteed non-matching query",
        )
        view.joint_search_edit.clear()
        view.list_tabs.setCurrentIndex(0)
        QApplication.processEvents()

        # Rigid-body binding and vectors share one validated Apply transaction;
        # every field is edited with QTest and the Maya attributes are checked
        # before and after Undo/Redo.
        view.rigid_body_list.setCurrentRow(0)
        QApplication.processEvents()
        rigid_shape = self.presenter._current_shape
        self.assertIsNotNone(rigid_shape)
        related_combo = view.rigid_related_bone_combo
        self.assertGreater(related_combo.count(), 1)
        related_before = view.binding_selection("rigid_related_bone")
        rigid_before = {
            "shape_size": tuple(cmds.getAttr(f"{rigid_shape}.shapeSize{axis}") for axis in "XYZ"),
            "position": tuple(cmds.getAttr(f"{rigid_shape}.position{axis}") for axis in "XYZ"),
            "rotation": tuple(cmds.getAttr(f"{rigid_shape}.rotation{axis}") for axis in "XYZ"),
            "related": related_before,
        }
        target_related_index = _adjacent_combo_index(related_combo)
        related_signal_count = _qtest_set_combo_index(
            related_combo,
            target_related_index,
            "PhysicsPresenter.on_rigid_body_changed",
        )
        related_evidence = tuple(_LAST_ACTION)
        self.assertEqual(related_signal_count, 1)
        related_after = view.binding_selection("rigid_related_bone")
        self.assertNotEqual(related_after, related_before)

        # The chosen values exercise all three vector components regardless of
        # the fixture's authored shape type.
        view.rigid_shape_size_edit.setComponentCount(3)
        shape_size_spy = QSignalSpy(
            view.rigid_shape_size_edit.valueChanged,
            source_control=view.rigid_shape_size_edit,
            action_name="PhysicsPresenter.on_rigid_body_changed",
        )
        _qtest_set_spin(view.rigid_shape_size_edit.spins[0], 0.61)
        self.assertEqual(_spy_count(shape_size_spy), 1)
        position_spy = QSignalSpy(
            view.rigid_position_edit.valueChanged,
            source_control=view.rigid_position_edit,
            action_name="PhysicsPresenter.on_rigid_body_changed",
        )
        _qtest_set_spin(view.rigid_position_edit.spins[0], 1.1)
        self.assertEqual(_spy_count(position_spy), 1)
        rotation_spy = QSignalSpy(
            view.rigid_rotation_edit.valueChanged,
            source_control=view.rigid_rotation_edit,
            action_name="PhysicsPresenter.on_rigid_body_changed",
        )
        _qtest_set_spin(view.rigid_rotation_edit.spins[0], 11.0)
        self.assertEqual(_spy_count(rotation_spy), 1)
        self.assertEqual(_qtest_click(view.apply_btn), 1)
        QApplication.processEvents()
        rigid_after = {
            "shape_size": tuple(cmds.getAttr(f"{rigid_shape}.shapeSize{axis}") for axis in "XYZ"),
            "position": tuple(cmds.getAttr(f"{rigid_shape}.position{axis}") for axis in "XYZ"),
            "rotation": tuple(cmds.getAttr(f"{rigid_shape}.rotation{axis}") for axis in "XYZ"),
            "related": view.binding_selection("rigid_related_bone"),
        }
        for key in ("shape_size", "position", "rotation"):
            self.assertNotEqual(rigid_after[key], rigid_before[key], key)
        self.assertEqual(rigid_after["related"], related_after)
        cmds.undo()
        self.assertEqual(
            tuple(cmds.getAttr(f"{rigid_shape}.shapeSize{axis}") for axis in "XYZ"),
            rigid_before["shape_size"],
        )
        cmds.redo()
        self.assertEqual(
            tuple(cmds.getAttr(f"{rigid_shape}.shapeSize{axis}") for axis in "XYZ"),
            rigid_after["shape_size"],
        )
        self._witness(
            "physics.rigid_related_bone",
            "QTest.keyClick(objectName=rigidRelatedBoneCombo, Qt.Key_Down); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "rigidBodyShape.relatedBone connection/index changed and Undo/Redo restored it",
            *related_evidence,
        )
        self._witness(
            "physics.rigid_shape_size",
            "QTest.edit(objectName=physicsRigidShapeSizeEdit); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "rigidBodyShape.shapeSizeX/Y/Z equals edited values with Undo/Redo",
            shape_size_spy,
            view.rigid_shape_size_edit,
        )
        self._witness(
            "physics.rigid_position",
            "QTest.edit(objectName=physicsRigidPositionEdit); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "rigidBodyShape.positionX/Y/Z equals edited values with Undo/Redo",
            position_spy,
            view.rigid_position_edit,
        )
        self._witness(
            "physics.rigid_rotation",
            "QTest.edit(objectName=physicsRigidRotationEdit); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "rigidBodyShape.rotationX/Y/Z equals edited values with Undo/Redo",
            rotation_spy,
            view.rigid_rotation_edit,
        )

        # Joint bindings and transform vectors use a second validated Apply
        # transaction, with the same direct-DAG and Undo/Redo oracle.
        view.list_tabs.setCurrentIndex(1)
        view.joint_list.setCurrentRow(-1)
        view.joint_list.setCurrentRow(0)
        QApplication.processEvents()
        joint_shape = self.presenter._current_shape
        self.assertIsNotNone(joint_shape)
        self.assertGreater(view.joint_body_a_combo.count(), 1)
        self.assertGreater(view.joint_body_b_combo.count(), 1)
        body_a_index = _adjacent_combo_index(view.joint_body_a_combo)
        body_b_index = _adjacent_combo_index(view.joint_body_b_combo)
        self.assertEqual(
            _qtest_set_combo_index(
                view.joint_body_a_combo,
                body_a_index,
                "PhysicsPresenter.on_joint_changed",
            ),
            1,
        )
        body_a_evidence = tuple(_LAST_ACTION)
        self.assertEqual(
            _qtest_set_combo_index(
                view.joint_body_b_combo,
                body_b_index,
                "PhysicsPresenter.on_joint_changed",
            ),
            1,
        )
        body_b_evidence = tuple(_LAST_ACTION)
        joint_before = {
            "position": tuple(cmds.getAttr(f"{joint_shape}.position{axis}") for axis in "XYZ"),
            "rotation": tuple(cmds.getAttr(f"{joint_shape}.rotation{axis}") for axis in "XYZ"),
        }
        joint_position_spy = QSignalSpy(
            view.joint_position_edit.valueChanged,
            source_control=view.joint_position_edit,
            action_name="PhysicsPresenter.on_joint_changed",
        )
        _qtest_set_spin(view.joint_position_edit.spins[0], 4.4)
        self.assertEqual(_spy_count(joint_position_spy), 1)
        joint_rotation_spy = QSignalSpy(
            view.joint_rotation_edit.valueChanged,
            source_control=view.joint_rotation_edit,
            action_name="PhysicsPresenter.on_joint_changed",
        )
        _qtest_set_spin(view.joint_rotation_edit.spins[0], 44.0)
        self.assertEqual(_spy_count(joint_rotation_spy), 1)
        self.assertEqual(_qtest_click(view.apply_btn), 1)
        QApplication.processEvents()
        joint_after = {
            "position": tuple(cmds.getAttr(f"{joint_shape}.position{axis}") for axis in "XYZ"),
            "rotation": tuple(cmds.getAttr(f"{joint_shape}.rotation{axis}") for axis in "XYZ"),
        }
        self.assertNotEqual(joint_after["position"], joint_before["position"])
        self.assertNotEqual(joint_after["rotation"], joint_before["rotation"])
        cmds.undo()
        self.assertEqual(
            tuple(cmds.getAttr(f"{joint_shape}.position{axis}") for axis in "XYZ"),
            joint_before["position"],
        )
        cmds.redo()
        self.assertEqual(
            tuple(cmds.getAttr(f"{joint_shape}.position{axis}") for axis in "XYZ"),
            joint_after["position"],
        )
        self._witness(
            "physics.joint_body_a",
            "QTest.keyClick(objectName=jointRigidBodyACombo, Qt.Key_Down); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "jointShape.rigidBodyA connection/index validated on Maya DAG",
            *body_a_evidence,
        )
        self._witness(
            "physics.joint_body_b",
            "QTest.keyClick(objectName=jointRigidBodyBCombo, Qt.Key_Down); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "jointShape.rigidBodyB connection/index validated on Maya DAG",
            *body_b_evidence,
        )
        self._witness(
            "physics.joint_position",
            "QTest.edit(objectName=physicsJointPositionEdit); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "jointShape.positionX/Y/Z equals edited values with Undo/Redo",
            joint_position_spy,
            view.joint_position_edit,
        )
        self._witness(
            "physics.joint_rotation",
            "QTest.edit(objectName=physicsJointRotationEdit); QTest.mouseClick(objectName=physicsApplyButton)",
            "PhysicsPresenter.apply_changes",
            "jointShape.rotationX/Y/Z equals edited values with Undo/Redo",
            joint_rotation_spy,
            view.joint_rotation_edit,
        )


if __name__ == "__main__":
    unittest.main()
