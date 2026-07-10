"""
MorphTab の GUI テスト
実際の Maya GUI 環境でのみ実行可能
"""

import unittest

from maya import cmds

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.tabs.morph_tab import MorphTab


@requires_gui
class TestMorphTabGUI(GuiTestBase):
    """MorphTab の GUI テスト（実際の Qt 環境で実行）"""

    def test_offset_edit_controls_not_exposed(self):
        """未実装のオフセット編集コントロールを公開しない（B-3 回帰防止）。

        オフセット表示・編集は未実装なので、操作できそうに見えるボタン自体を
        MorphTab に生成しない。実装してシグナル接続したら、このテストを更新する。
        """
        tab = MorphTab()
        try:
            self.assertFalse(hasattr(tab, "add_offset_btn"))
            self.assertFalse(hasattr(tab, "remove_offset_btn"))
            self.assertFalse(hasattr(tab, "clear_offsets_btn"))
        finally:
            tab.deleteLater()

    def test_mouth_alias_slider_updates_canonical_blendshape_weight(self):
        """MorphTab の実 slider signal が Mouth_A01 の weight[0] を更新する。"""
        cmds.file(new=True, force=True)
        root = cmds.group(empty=True, name="morphGuiModel")
        mesh = cmds.polyCube(name="morphGuiMesh")[0]
        target = cmds.polyCube(name="Mouth_A01_target")[0]
        cmds.parent(mesh, root)
        blend_shape = cmds.blendShape(target, mesh, name="morphGuiBlendShape")[0]
        cmds.aliasAttr("Mouth_A01", "{0}.weight[0]".format(blend_shape))
        cmds.delete(target)

        tab = MorphTab()
        presenter = None
        try:
            app_state = ApplicationState()
            app_state._current_model_root = root
            presenter = MorphPresenter(tab, app_state)
            presenter.load_morphs()
            QApplication.processEvents()

            self.assertEqual(tab.morph_list.count(), 1)
            self.assertEqual(tab.morph_list.item(0).text(), "Mouth_A01")
            tab.morph_list.setCurrentRow(0)
            QApplication.processEvents()
            tab.morph_slider.setValue(65)
            QApplication.processEvents()

            self.assertAlmostEqual(
                cmds.getAttr("{0}.weight[0]".format(blend_shape)),
                0.65,
                places=5,
            )
        finally:
            presenter = None
            tab.deleteLater()
            QApplication.processEvents()
            cmds.file(new=True, force=True)


if __name__ == "__main__":
    unittest.main()
