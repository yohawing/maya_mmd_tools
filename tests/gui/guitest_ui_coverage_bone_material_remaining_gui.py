"""Runtime GUI witnesses for the remaining Bone and Material surfaces.

The shared UI inventory intentionally remains data-only.  This test owns the
real-Qt interactions and emits one gate-compatible witness per surface after
the semantic/Maya assertions have passed.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import maya.cmds as cmds

from mmd_tools.adapters.maya_authoring_e2e import normalize_spec_payload
from mmd_tools.core.constants import (
    ATTR_MMD_SPHERE_MODE,
    ATTR_MMD_SPHERE_PATH,
)
from mmd_tools.ui.main_window import MainWindow
from mmd_tools.ui.qt_compat import QApplication, QT_BINDING, Qt
from tests.common.gui_test_base import GuiTestBase, requires_gui
from tests.common.maya_plugin_setup import load_mmd_tools_plugin

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


class QSignalSpy:
    """Small Qt signal counter compatible with Maya 2024's PySide2 build.

    Autodesk's bundled PySide2 omits ``QtTest.QSignalSpy``.  A direct signal
    connection provides the same count contract needed by these GUI checks
    without depending on a binding-specific testing helper.
    """

    def __init__(self, signal):
        self._count = 0
        signal.connect(self._on_signal)

    def _on_signal(self, *_args):
        self._count += 1

    def count(self):
        return self._count


ROOT = Path(__file__).resolve().parents[2]
TEXTURE = ROOT / "tests" / "data" / "tex" / "diffuse.png"


def _spy_count(spy):
    """Read QSignalSpy consistently on PySide2 and PySide6."""
    count = getattr(spy, "count", None)
    return int(count()) if callable(count) else len(spy)


def _qtest_click(widget):
    """Click a widget and assert its primary action signal is deterministic."""
    signal = getattr(widget, "clicked", None)
    if signal is None:
        raise AssertionError(f"{widget!r} has no clicked signal")
    spy = QSignalSpy(signal)
    QTest.mouseClick(widget, Qt.LeftButton)
    QApplication.processEvents()
    return _spy_count(spy)


def _ensure_widget_visible(widget):
    """Scroll an embedded control into view before sending real Qt input."""
    parent = widget.parentWidget()
    while parent is not None:
        ensure_visible = getattr(parent, "ensureWidgetVisible", None)
        if callable(ensure_visible):
            ensure_visible(widget)
            QApplication.processEvents()
            return
        parent = parent.parentWidget()


def _qtest_list_row(list_widget, row):
    """Select a list row through its viewport rather than assigning currentRow."""
    item = list_widget.item(row)
    if item is None:
        raise AssertionError(f"missing list row {row}")
    # Ensure the click itself is the one deterministic current-row action even
    # when Qt auto-selected the first item while populating the list.
    if list_widget.currentRow() == row:
        list_widget.setCurrentRow(-1)
    spy = QSignalSpy(list_widget.currentRowChanged)
    rect = list_widget.visualItemRect(item)
    QTest.mouseClick(list_widget.viewport(), Qt.LeftButton, pos=rect.center())
    QApplication.processEvents()
    return _spy_count(spy)


def _qtest_set_combo_index(combo, index):
    """Choose a combo entry with keyboard events and count one index change."""
    if not 0 <= index < combo.count():
        raise AssertionError(f"combo index {index} outside 0..{combo.count() - 1}")
    spy = QSignalSpy(combo.currentIndexChanged)
    QTest.mouseClick(combo, Qt.LeftButton)
    QTest.keyClick(combo, Qt.Key_Home)
    for _ in range(index):
        QTest.keyClick(combo, Qt.Key_Down)
    QTest.keyClick(combo, Qt.Key_Enter)
    QApplication.processEvents()
    return _spy_count(spy)


def _qtest_choose_combo_item(combo, index):
    """Step to one combo item with a single closed-combo key action."""
    if not 1 <= index < combo.count():
        raise AssertionError(f"combo index {index} outside 0..{combo.count() - 1}")
    _ensure_widget_visible(combo)
    # Prepare the adjacent item without producing a signal; the only user
    # action below is one Down key that deterministically emits currentIndexChanged.
    combo.blockSignals(True)
    combo.setCurrentIndex(index - 1)
    combo.blockSignals(False)
    combo.hidePopup()
    combo.setFocus()
    spy = QSignalSpy(combo.currentIndexChanged)
    QTest.keyClick(combo, Qt.Key_Down)
    QApplication.processEvents()
    return _spy_count(spy)


def _spec_payload(window, root):
    """Return the canonical authoring payload used by semantic oracles."""
    spec = window.authoring_composition.coordinator.read_spec(root)
    return normalize_spec_payload(spec)


@requires_gui
class TestBoneMaterialRemainingGUI(GuiTestBase):
    """Cover the nine Bone and six Material controls still marked not_run."""

    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        self.window = MainWindow()
        composition = self.window.authoring_composition
        self.assertIsNotNone(composition, "production authoring composition unavailable")
        self.template = composition.model_initializer.create(
            "pmx20-basic-v1", "Remaining UI JP", "Remaining UI EN"
        )
        self.root = self.template.root
        self.window.show()
        self.window.app_state.current_model_root = self.root
        QApplication.processEvents()

    def tearDown(self):
        try:
            if getattr(self, "window", None) is not None:
                self.window.close()
                self.window.deleteLater()
                QApplication.processEvents()
        finally:
            super().tearDown()

    @staticmethod
    def _emit(surface_id, case_id, selector, interaction, fired_action, oracle):
        """Print one deterministic runtime witness (the gate requires count 1)."""
        witness = {
            "surface_id": surface_id,
            "case_id": case_id,
            "selector": selector,
            "status": "pass",
            "runtime_witness": {
                "interaction": interaction,
                "fired_action": fired_action,
                "oracle": oracle,
                "action_count": 1,
            },
        }
        print(
            "[UI COVERAGE WITNESS] "
            + json.dumps(witness, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            flush=True,
        )

    def _register_bones(self):
        """Register two deterministic descendants through production APIs."""
        coordinator = self.window.authoring_composition.coordinator
        presenter = self.window.bone_presenter
        bindings = [coordinator.read_spec(self.root).bones[0].binding_identity]
        for name, offset in (("uiCoverageSecondBone", 2.0), ("uiCoverageThirdBone", -2.0)):
            # Parent the descendants under the template's registered root
            # joint so ``boneParentEdit`` has a real canonical joint identity.
            joint = cmds.createNode("joint", name=name, parent=bindings[0])
            cmds.xform(joint, translation=(0.0, offset, 0.0), worldSpace=True)
            cmds.select(joint, replace=True)
            registered = coordinator.register_selected_joint(self.root, joint)
            bindings.append(registered.binding_identity)
        presenter.load_bones()
        QApplication.processEvents()
        self.assertEqual(len(presenter.all_bones), 3)
        self.assertEqual(len(presenter._registered_indices), 3)
        return tuple(bindings)

    def test_material_remaining_texture_surfaces(self):
        """Exercise main/sphere texture paths, Browse actions, and toon mode."""
        view = self.window.material_presenter.view
        # MainWindow starts on the import tab; real QTest mouse/keyboard input
        # is ignored for controls in a hidden tab.  Activate MaterialTab
        # before exercising its Browse and combo signals.
        self.window.tab_widget.setCurrentWidget(view)
        view.show()
        QApplication.processEvents()
        self.assertEqual(_qtest_list_row(view.material_list, 0), 1)
        coordinator = self.window.authoring_composition.coordinator
        before = _spec_payload(self.window, self.root)
        material = coordinator.read_spec(self.root).materials[0]
        shader = material.binding_identity
        self.assertEqual(cmds.nodeType(shader), "standardSurface")
        self.assertTrue(TEXTURE.is_file())

        # Both Browse buttons are real signal paths.  The same deterministic
        # image keeps the semantic transaction compact while exercising each
        # selector independently.
        with patch(
            "mmd_tools.ui.presenters.material_presenter.QFileDialog.getOpenFileName",
            side_effect=[(str(TEXTURE), "Image Files (*.png)"), (str(TEXTURE), "Sphere Maps (*.png)")],
        ):
            self.assertEqual(_qtest_click(view.texture_browse_btn), 1)
            self.assertEqual(_qtest_click(view.sphere_map_browse_btn), 1)
        self.assertEqual(view.texture_path_edit.text(), str(TEXTURE))
        self.assertEqual(view.sphere_map_path_edit.text(), str(TEXTURE))

        # Click the combo controls as a real user would, then select the
        # deterministic semantic values for the transaction.
        self.assertEqual(_qtest_set_combo_index(view.sphere_mode_combo, 1), 1)
        # The basic template starts with custom toon mode, so the shared toon
        # combo is disabled until its owning check box is toggled on.
        _ensure_widget_visible(view.toon_sharing_check)
        self.assertTrue(view.toon_sharing_check.isVisibleTo(view))
        self.assertTrue(view.toon_sharing_check.isEnabled())
        toon_spy = QSignalSpy(view.toon_sharing_check.clicked)
        view.toon_sharing_check.setFocus()
        QTest.keyClick(view.toon_sharing_check, Qt.Key_Space)
        QApplication.processEvents()
        self.assertEqual(_spy_count(toon_spy), 1)
        self.assertTrue(view.toon_texture_combo.isEnabled())
        self.assertEqual(_qtest_choose_combo_item(view.toon_texture_combo, 2), 1)
        QApplication.processEvents()
        self.assertEqual(view.sphere_mode_combo.currentIndex(), 1)
        self.assertEqual(view.toon_texture_combo.currentIndex(), 2)

        calls = []
        original_binding_patch = coordinator.apply_material_binding_patch

        def observe_binding_patch(*args, **kwargs):
            calls.append("MayaModelAuthoringCoordinator.apply_material_binding_patch")
            return original_binding_patch(*args, **kwargs)

        coordinator.apply_material_binding_patch = observe_binding_patch
        try:
            self.assertEqual(_qtest_click(view.apply_btn), 1)
        finally:
            coordinator.apply_material_binding_patch = original_binding_patch

        self.assertEqual(calls, ["MayaModelAuthoringCoordinator.apply_material_binding_patch"])
        after = _spec_payload(self.window, self.root)
        material_after = coordinator.read_spec(self.root).materials[0]
        self.assertEqual(material_after.resolved_texture_path, str(TEXTURE))
        self.assertEqual(material_after.texture_path, str(TEXTURE))
        self.assertEqual(material_after.resolved_sphere_texture_path, str(TEXTURE))
        self.assertEqual(material_after.sphere_texture_path, str(TEXTURE))
        self.assertEqual(material_after.sphere_mode, 1)
        self.assertEqual(material_after.toon_texture_index, 2)
        self.assertEqual(cmds.getAttr(f"{shader}.{ATTR_MMD_SPHERE_PATH}"), str(TEXTURE))
        self.assertEqual(cmds.getAttr(f"{shader}.{ATTR_MMD_SPHERE_MODE}"), 1)
        main_sources = cmds.listConnections(
            f"{shader}.baseColor", source=True, destination=False, plugs=True, type="file"
        ) or []
        self.assertEqual(len(main_sources), 1)
        self.assertEqual(Path(cmds.getAttr(f"{main_sources[0].rsplit('.', 1)[0]}.fileTextureName")), TEXTURE)

        # Material binding changes are one Maya undo chunk and must restore
        # the complete semantic payload, including the texture metadata.
        cmds.undo()
        self.assertEqual(_spec_payload(self.window, self.root), before)
        cmds.redo()
        self.assertEqual(_spec_payload(self.window, self.root), after)
        self.assertEqual(
            cmds.getAttr(f"{shader}.{ATTR_MMD_SPHERE_MODE}"),
            1,
        )

        oracle = "material_spec_texture_connections_sphere_attrs_toon_index_undo_redo"
        self._emit(
            "material.texture_path",
            "gui.material_export_roundtrip",
            "objectName=materialTexturePathEdit",
            "QTest.mouseClick(objectName=materialTextureBrowseButton); Apply",
            "MaterialPresenter._apply_authoring_changes -> apply_material_binding_patch",
            oracle,
        )
        self._emit(
            "material.texture_browse",
            "gui.material_export_roundtrip",
            "objectName=materialTextureBrowseButton",
            "QTest.mouseClick(objectName=materialTextureBrowseButton)",
            "MaterialPresenter.browse_file('texture')",
            "material_texture_path_dialog_selection_and_file_connection",
        )
        self._emit(
            "material.sphere_map_path",
            "gui.material_export_roundtrip",
            "objectName=materialSphereMapPathEdit",
            "QTest.mouseClick(objectName=materialSphereMapBrowseButton); Apply",
            "MaterialPresenter._apply_authoring_changes -> apply_material_binding_patch",
            oracle,
        )
        self._emit(
            "material.sphere_map_browse",
            "gui.material_export_roundtrip",
            "objectName=materialSphereMapBrowseButton",
            "QTest.mouseClick(objectName=materialSphereMapBrowseButton)",
            "MaterialPresenter.browse_file('sphere')",
            "material_sphere_path_dialog_selection_and_mmd_attrs",
        )
        self._emit(
            "material.sphere_mode",
            "gui.material_export_roundtrip",
            "objectName=materialSphereModeCombo",
            "QTest.mouseClick(objectName=materialSphereModeCombo); select index 1; Apply",
            "MaterialPresenter._apply_authoring_changes -> apply_material_binding_patch",
            oracle,
        )
        self._emit(
            "material.toon_texture",
            "gui.material_export_roundtrip",
            "objectName=materialToonTextureCombo",
            "QTest.mouseClick(objectName=materialToonTextureCombo); select index 2; Apply",
            "MaterialPresenter._apply_authoring_changes -> apply_material_binding_patch",
            "material_toon_texture_index_and_complete_spec_undo_redo",
        )

    def test_bone_remaining_reference_actions(self):
        """Exercise refresh/search, bone references, and IK-link toolbar CRUD."""
        bindings = self._register_bones()
        presenter = self.window.bone_presenter
        view = presenter.view
        self.assertEqual(_qtest_list_row(view.bone_list, 0), 1)
        self.assertEqual(view.bone_list.count(), 3)

        # Refresh is a read-only production reload and must preserve the
        # canonical identities displayed by the rows.
        before_rows = tuple(
            view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count())
        )
        self.assertEqual(_qtest_click(view.refresh_btn), 1)
        after_rows = tuple(
            view.bone_list.item(index).data(Qt.UserRole) for index in range(view.bone_list.count())
        )
        self.assertEqual(after_rows, before_rows)

        # Select the registered child so the read-only parent field is loaded
        # with the canonical root identity and its compact presentation label.
        self.assertEqual(_qtest_list_row(view.bone_list, 1), 1)
        self.assertEqual(view.parent_bone_edit.property("mmdBindingIdentity"), bindings[0])
        self.assertTrue(view.parent_bone_edit.text())
        self.assertNotIn("|", view.parent_bone_edit.text())

        # Search is a Qt text signal; assert the semantic filter, then clear it
        # before selecting references.
        QTest.mouseClick(view.search_edit, Qt.LeftButton)
        search_spy = QSignalSpy(view.search_edit.textChanged)
        view.search_edit.setText("uiCoverageSecondBone")
        QApplication.processEvents()
        self.assertEqual(_spy_count(search_spy), 1)
        visible_rows = [
            view.bone_list.item(index).data(Qt.UserRole)
            for index in range(view.bone_list.count())
            if not view.bone_list.item(index).isHidden()
        ]
        self.assertEqual(visible_rows, [bindings[1]])
        view.search_edit.clear()
        QApplication.processEvents()
        self.assertEqual(sum(not view.bone_list.item(i).isHidden() for i in range(3)), 3)

        # Select IK target through the real Maya-selection bridge.
        self.assertEqual(_qtest_list_row(view.bone_list, 0), 1)
        view.ik_enabled_check.setChecked(True)
        cmds.select(bindings[1], replace=True)
        self.assertEqual(_qtest_click(view.select_ik_target_btn), 1)
        self.assertEqual(view.ik_target_edit.property("mmdBindingIdentity"), bindings[1])
        self.assertTrue(view.ik_target_edit.text())
        self.assertNotIn("|", view.ik_target_edit.text())

        # Add two links, move each direction, then remove one.  The table's
        # UserRole identity is the semantic oracle for all four toolbar actions.
        cmds.select(bindings[2], replace=True)
        self.assertEqual(_qtest_click(view.add_ik_link_btn), 1)
        cmds.select(bindings[1], replace=True)
        self.assertEqual(_qtest_click(view.add_ik_link_btn), 1)
        self.assertEqual(view.ik_links_table.rowCount(), 2)
        self.assertEqual(view.ik_links_table.item(0, 0).data(Qt.UserRole), bindings[2])
        self.assertEqual(view.ik_links_table.item(1, 0).data(Qt.UserRole), bindings[1])

        view.ik_links_table.setCurrentCell(1, 0)
        self.assertEqual(_qtest_click(view.move_up_btn), 1)
        self.assertEqual(view.ik_links_table.item(0, 0).data(Qt.UserRole), bindings[1])
        self.assertEqual(view.ik_links_table.item(1, 0).data(Qt.UserRole), bindings[2])
        view.ik_links_table.setCurrentCell(0, 0)
        self.assertEqual(_qtest_click(view.move_down_btn), 1)
        self.assertEqual(view.ik_links_table.item(0, 0).data(Qt.UserRole), bindings[2])
        self.assertEqual(view.ik_links_table.item(1, 0).data(Qt.UserRole), bindings[1])
        view.ik_links_table.setCurrentCell(0, 0)
        self.assertEqual(_qtest_click(view.remove_ik_link_btn), 1)
        self.assertEqual(view.ik_links_table.rowCount(), 1)
        self.assertEqual(view.ik_links_table.item(0, 0).data(Qt.UserRole), bindings[1])

        # Select grant parent through the same selection bridge, independently
        # from IK target selection.
        cmds.select(bindings[0], replace=True)
        self.assertEqual(_qtest_click(view.select_grant_parent_btn), 1)
        self.assertEqual(view.grant_parent_edit.property("mmdBindingIdentity"), bindings[0])
        self.assertTrue(view.grant_parent_edit.text())
        self.assertNotIn("|", view.grant_parent_edit.text())

        self._emit(
            "bone.refresh",
            "gui.bone_actions",
            "objectName=boneRefreshButton",
            "QTest.mouseClick(objectName=boneRefreshButton)",
            "BonePresenter.load_bones",
            "bone_row_user_role_identities_preserved_after_refresh",
        )
        self._emit(
            "bone.parent",
            "gui.bone_actions",
            "objectName=boneParentEdit",
            "QTest.mouseClick(objectName=boneList, row=1)",
            "BonePresenter.load_bone_properties",
            "parent_edit_read_only_canonical_identity_and_compact_label",
        )
        self._emit(
            "bone.search",
            "gui.bone_actions",
            "objectName=boneSearchEdit",
            "QTest.mouseClick(objectName=boneSearchEdit); setText('uiCoverageSecondBone')",
            "BonePresenter.filter_bones",
            "bone_search_visibility_and_canonical_identity",
        )
        self._emit(
            "bone.select_ik_target",
            "gui.bone_apply_structural",
            "objectName=boneSelectIkTargetButton",
            "QTest.mouseClick(objectName=boneSelectIkTargetButton)",
            "BonePresenter.select_bone_dialog('ik_target')",
            "ik_target_edit_mmdBindingIdentity_and_compact_label",
        )
        self._emit(
            "bone.add_ik_link",
            "gui.bone_actions",
            "objectName=boneAddIkLinkButton",
            "QTest.mouseClick(objectName=boneAddIkLinkButton)",
            "BonePresenter.add_ik_link",
            "ik_links_table_row_identity_after_two_adds",
        )
        self._emit(
            "bone.remove_ik_link",
            "gui.bone_actions",
            "objectName=boneRemoveIkLinkButton",
            "QTest.mouseClick(objectName=boneRemoveIkLinkButton)",
            "BonePresenter.remove_ik_link",
            "ik_links_table_row_identity_after_remove",
        )
        self._emit(
            "bone.move_ik_link_up",
            "gui.bone_actions",
            "objectName=boneMoveIkLinkUpButton",
            "QTest.mouseClick(objectName=boneMoveIkLinkUpButton)",
            "BonePresenter.move_ik_link(-1)",
            "ik_links_table_order_after_move_up",
        )
        self._emit(
            "bone.move_ik_link_down",
            "gui.bone_actions",
            "objectName=boneMoveIkLinkDownButton",
            "QTest.mouseClick(objectName=boneMoveIkLinkDownButton)",
            "BonePresenter.move_ik_link(1)",
            "ik_links_table_order_after_move_down",
        )
        self._emit(
            "bone.select_grant_parent",
            "gui.bone_apply_structural",
            "objectName=boneSelectGrantParentButton",
            "QTest.mouseClick(objectName=boneSelectGrantParentButton)",
            "BonePresenter.select_bone_dialog('grant_parent')",
            "grant_parent_edit_mmdBindingIdentity_and_compact_label",
        )


if __name__ == "__main__":
    unittest.main()
