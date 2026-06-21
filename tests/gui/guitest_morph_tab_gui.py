"""
MorphTab の GUI テスト
実際の Maya GUI 環境でのみ実行可能
"""

import unittest

from tests.common.gui_test_base import GuiTestBase, requires_gui
from mmd_tools.ui.tabs.morph_tab import MorphTab


@requires_gui
class TestMorphTabGUI(GuiTestBase):
    """MorphTab の GUI テスト（実際の Qt 環境で実行）"""

    def test_offset_buttons_disabled(self):
        """未配線のオフセット操作ボタンが無効化されていることを確認する（B-3 回帰防止）。

        add/remove/clear_offsets ボタンは presenter にシグナル未接続で、かつ
        オフセット表示自体が未実装のため、無効化されている必要がある。実装して
        シグナル接続したら、このテストを更新して有効状態を検証すること。
        """
        tab = MorphTab()
        try:
            self.assertFalse(tab.add_offset_btn.isEnabled())
            self.assertFalse(tab.remove_offset_btn.isEnabled())
            self.assertFalse(tab.clear_offsets_btn.isEnabled())
        finally:
            tab.deleteLater()


if __name__ == "__main__":
    unittest.main()
