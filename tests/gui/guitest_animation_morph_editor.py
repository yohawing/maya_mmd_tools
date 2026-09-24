"""Maya GUI regression tests for the Animator morph grid editor."""

import json
import unittest
from unittest.mock import patch

from maya import cmds

from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.presenters.animation_presenter import AnimationPresenter
from mmd_tools.ui.qt_compat import QApplication, QT_BINDING, Qt
from mmd_tools.ui.tabs.animation_tab import AnimationTab
from tests.common.gui_test_base import GuiTestBase, requires_gui

if QT_BINDING == "PySide6":
    from PySide6.QtTest import QTest
else:
    from PySide2.QtTest import QTest


@requires_gui
class TestAnimationMorphEditor(GuiTestBase):
    def setUp(self):
        super().setUp()
        cmds.file(new=True, force=True)
        self.root = cmds.createNode("transform", name="morphEditorModel")
        cmds.addAttr(self.root, longName="mmdMorphData", dataType="string")
        metadata = [
            {
                "index": 0,
                "name_jp": "とても長い笑顔モーフの表示名",
                "name_en": "Very Long Smile Morph Name",
                "panel": 2,
                "type": 1,
            },
            {
                "index": 1,
                "name_jp": "骨",
                "name_en": "Bone",
                "panel": 2,
                "type": 2,
            },
            {
                "index": 2,
                "name_jp": "材質",
                "name_en": "Material",
                "panel": 2,
                "type": 8,
            },
        ]
        cmds.setAttr(
            f"{self.root}.mmdMorphData",
            json.dumps(metadata, ensure_ascii=False),
            type="string",
        )
        self.controller = cmds.createNode("network", name="morphEditorController")
        cmds.addAttr(
            self.controller,
            longName="inputWeight",
            attributeType="double",
            multi=True,
            keyable=True,
        )
        cmds.addAttr(self.root, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(
            f"{self.controller}.message", f"{self.root}.mmd_morph_controller"
        )
        for index, alias in enumerate(("LongSmile", "BoneMorph", "MaterialMorph")):
            plug = f"{self.controller}.inputWeight[{index}]"
            cmds.setAttr(plug, index * 0.1)
            cmds.aliasAttr(alias, plug)

        self.tab = AnimationTab()
        self.state = ApplicationState()
        self.state._current_model_root = self.root
        self.presenter = AnimationPresenter(self.tab, self.state)
        self.tab.show()
        self.tab.picker_tabs.setCurrentIndex(self.tab.TAB_MORPH)
        QApplication.processEvents()

    def tearDown(self):
        self.presenter.disconnect_signals()
        self.tab.close()
        self.tab.deleteLater()
        cmds.file(new=True, force=True)
        super().tearDown()

    def assert_selected_plug(self, expected):
        selected = cmds.ls(selection=True) or []
        self.assertEqual(len(selected), 1)
        node, attribute = selected[0].split(".", 1)
        aliases = cmds.aliasAttr(node, query=True) or []
        canonical_by_alias = dict(zip(aliases[0::2], aliases[1::2]))
        canonical_attribute = canonical_by_alias.get(attribute, attribute)
        self.assertEqual(f"{node}.{canonical_attribute}", expected)

    def test_grid_columns_numeric_edit_and_external_refresh(self):
        self.assertEqual(len(self.presenter._morph_rows), 3)
        rows = list(self.presenter._morph_rows.values())
        self.assertEqual({row.label.width() for row in rows}, {116})
        self.assertEqual({row.editor.width() for row in rows}, {72})
        self.assertTrue(
            any(
                name in rows[0].label.toolTip()
                for name in ("Very Long Smile Morph Name", "とても長い笑顔モーフの表示名")
            )
        )
        self.assertFalse(rows[0].icon.pixmap().isNull())

        rows[0].editor.setValue(0.625)
        rows[0].editor._finish_edit()
        QApplication.processEvents()
        self.assertAlmostEqual(
            cmds.getAttr(f"{self.controller}.inputWeight[0]"), 0.625
        )

        cmds.setAttr(f"{self.controller}.inputWeight[0]", 0.25)
        self.presenter._refresh_morph_rows()
        self.assertEqual(rows[0].slider.value(), 25)
        self.assertAlmostEqual(rows[0].editor.value(), 0.25)

    def test_tools_live_inside_picker_pages_only(self):
        with patch(
            "mmd_tools.ui.tabs.animation_tab.SettingsService.is_development_mode",
            return_value=True,
        ):
            self.tab.picker_tabs.setCurrentIndex(self.tab.TAB_BODY)
            QApplication.processEvents()
            self.assertIs(self.tab.tools_group.parent(), self.tab.body_page)
            self.assertTrue(self.tab.tools_group.isVisible())

            self.tab.picker_tabs.setCurrentIndex(self.tab.TAB_FINGER)
            QApplication.processEvents()
            self.assertIs(self.tab.tools_group.parent(), self.tab.finger_page)
            self.assertTrue(self.tab.tools_group.isVisible())

            for tab_index in (self.tab.TAB_MORPH, self.tab.TAB_DISPLAY):
                self.tab.picker_tabs.setCurrentIndex(tab_index)
                QApplication.processEvents()
                self.assertFalse(self.tab.tools_group.isVisible())

    def test_key_and_interpolated_states_are_accessible(self):
        plug = f"{self.controller}.inputWeight[0]"
        cmds.setKeyframe(plug, time=1, value=0.2)
        cmds.setKeyframe(plug, time=10, value=0.8)
        row = next(iter(self.presenter._morph_rows.values()))

        cmds.currentTime(1)
        self.presenter._refresh_morph_rows()
        self.assertIn("#71343b", row.editor.styleSheet())
        self.assertIn("key exists", row.editor.accessibleDescription())

        cmds.currentTime(5)
        self.presenter._refresh_morph_rows()
        self.assertIn("#5a4144", row.editor.styleSheet())
        self.assertIn("between keys", row.editor.accessibleDescription())

    def test_row_selection_drives_standard_set_key_for_one_morph(self):
        other_namespace = "morphEditorOther"
        cmds.namespace(add=other_namespace)
        other_root = cmds.createNode(
            "transform", name=f"{other_namespace}:morphEditorModel"
        )
        other_controller = cmds.createNode(
            "network", name=f"{other_namespace}:morphEditorController"
        )
        cmds.addAttr(
            other_controller,
            longName="inputWeight",
            attributeType="double",
            multi=True,
            keyable=True,
        )
        cmds.addAttr(other_root, longName="mmd_morph_controller", attributeType="message")
        cmds.connectAttr(
            f"{other_controller}.message", f"{other_root}.mmd_morph_controller"
        )
        cmds.setAttr(f"{other_controller}.inputWeight[0]", 0.9)

        row = self.presenter._morph_rows[
            "とても長い笑顔モーフの表示名"
        ]
        plug = f"{self.controller}.inputWeight[0]"
        adjacent_plug = f"{self.controller}.inputWeight[1]"
        other_plug = f"{other_controller}.inputWeight[0]"
        frame = int(cmds.currentTime(query=True))

        QTest.mouseClick(row.label, Qt.LeftButton)
        QApplication.processEvents()
        self.assertTrue(row.is_selected)
        self.assert_selected_plug(plug)

        # Clicking the editor's child still activates the row and leaves the
        # normal QDoubleSpinBox line-edit path available for value input.
        line_edit = row.editor.lineEdit()
        QTest.mouseClick(line_edit, Qt.LeftButton)
        QTest.keyClick(line_edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(line_edit, "0.625")
        QTest.keyClick(line_edit, Qt.Key_Enter)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.625)
        self.assertTrue(row.is_selected)
        self.assert_selected_plug(plug)
        self.assertIs(QApplication.focusWidget(), row)

        QTest.keyClick(row, Qt.Key_S)
        QApplication.processEvents()
        self.assertEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), keyframeCount=True),
            1,
        )
        self.assertAlmostEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), valueChange=True)[0],
            0.625,
        )
        self.assertEqual(
            cmds.keyframe(
                adjacent_plug,
                query=True,
                time=(frame, frame),
                keyframeCount=True,
            )
            or 0,
            0,
        )
        self.assertEqual(
            cmds.keyframe(
                other_plug,
                query=True,
                time=(frame, frame),
                keyframeCount=True,
            )
            or 0,
            0,
        )

        row.editor.setValue(0.875)
        row.editor._finish_edit()
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plug), 0.875)
        self.assert_selected_plug(plug)
        row.setFocus(Qt.OtherFocusReason)
        QTest.keyClick(row, Qt.Key_S)
        QApplication.processEvents()
        self.assertEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), keyframeCount=True),
            1,
        )
        self.assertAlmostEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), valueChange=True)[0],
            0.875,
        )

        cmds.undo()
        QApplication.processEvents()
        self.assertEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), keyframeCount=True),
            1,
        )
        self.assertAlmostEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), valueChange=True)[0],
            0.625,
        )
        cmds.redo()
        QApplication.processEvents()
        self.assertAlmostEqual(
            cmds.keyframe(plug, query=True, time=(frame, frame), valueChange=True)[0],
            0.875,
        )

    def test_multi_selection_synchronizes_row_editors_undo_filter_and_model_scope(self):
        names = list(self.presenter._morph_rows)
        first, second, third = [self.presenter._morph_rows[name] for name in names]
        plugs = [f"{self.controller}.inputWeight[{index}]" for index in range(3)]
        QTest.mouseClick(first.label, Qt.LeftButton)
        QTest.mouseClick(second.label, Qt.LeftButton, Qt.ControlModifier)
        QApplication.processEvents()
        self.assertTrue(first.is_selected)
        self.assertTrue(second.is_selected)
        self.assertFalse(third.is_selected)
        self.assertIn("Mixed", self.tab.morph_selection_status.text())

        QTest.mousePress(first.slider, Qt.LeftButton)
        self.assertTrue(first.is_selected and second.is_selected)
        first.slider.setValue(25)
        QTest.mouseRelease(first.slider, Qt.LeftButton)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.25)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.25)
        self.assertAlmostEqual(cmds.getAttr(plugs[2]), 0.2)
        self.assertEqual(second.slider.value(), 25)
        self.assertAlmostEqual(second.editor.value(), 0.25)
        self.assertTrue(first.is_selected and second.is_selected)
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.0)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.1)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.25)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.25)

        editor = second.editor.lineEdit()
        QTest.mouseClick(editor, Qt.LeftButton)
        QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(editor, "0.625")
        QTest.keyClick(editor, Qt.Key_Enter)
        QApplication.processEvents()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.625)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.625)
        self.assertAlmostEqual(cmds.getAttr(plugs[2]), 0.2)
        self.assertEqual(first.slider.value(), round(0.625 * 100))
        self.assertAlmostEqual(first.editor.value(), 0.625)
        cmds.undo()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.25)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.25)
        cmds.redo()
        self.assertAlmostEqual(cmds.getAttr(plugs[0]), 0.625)
        self.assertAlmostEqual(cmds.getAttr(plugs[1]), 0.625)

        self.tab.morph_filter.setText("Type:")
        QApplication.processEvents()
        self.assertTrue(first.is_selected and second.is_selected)
        self.tab.morph_filter.setText("Material")
        QApplication.processEvents()
        self.assertEqual(self.presenter._morph_selected_names, [])
        self.assertTrue(first.isHidden())
        self.assertFalse(third.isHidden())
        self.tab.morph_filter.clear()
        QTest.mouseClick(first.label, Qt.LeftButton)
        QTest.mouseClick(third.label, Qt.LeftButton, Qt.ShiftModifier)
        QApplication.processEvents()
        self.assertTrue(all(row.is_selected for row in (first, second, third)))
        frame = int(cmds.currentTime(query=True))
        first.slider.setFocus(Qt.OtherFocusReason)
        QTest.keyClick(first.slider, Qt.Key_S)
        QApplication.processEvents()
        for plug in plugs:
            self.assertEqual(
                cmds.keyframe(plug, query=True, time=(frame, frame), keyframeCount=True),
                1,
            )
        next_frame = frame + 1
        cmds.currentTime(next_frame)
        previous_auto_key = cmds.autoKeyframe(query=True, state=True)
        try:
            cmds.autoKeyframe(state=True)
            editor = third.editor.lineEdit()
            QTest.mouseClick(editor, Qt.LeftButton)
            QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(editor, "0.5")
            QTest.keyClick(editor, Qt.Key_Enter)
            QApplication.processEvents()
            self.assertTrue(all(row.is_selected for row in (first, second, third)))
            for plug in plugs:
                self.assertAlmostEqual(cmds.getAttr(plug), 0.5)
                self.assertEqual(
                    cmds.keyframe(
                        plug, query=True, time=(next_frame, next_frame), keyframeCount=True
                    ),
                    1,
                )
        finally:
            cmds.autoKeyframe(state=previous_auto_key)

        self.state._current_model_root = None
        self.presenter._clear_morph_tab()
        self.assertEqual(self.presenter._morph_selected_names, [])
        self.assertIn("Select morphs", self.tab.morph_selection_status.text())


if __name__ == "__main__":
    unittest.main()
