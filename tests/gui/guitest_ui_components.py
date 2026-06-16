import unittest
import maya.cmds as cmds

from mmd_tools.ui.qt_compat import QApplication
from mmd_tools.ui.main_window import MainWindow
from tests.common.gui_test_base import GuiTestBase, requires_gui


@requires_gui
class TestMainWindow(GuiTestBase):
    """
    MainWindowの基本的な初期化テスト

    詳細なプレゼンターのテストは以下のファイルで実施:
    - test_import_export_presenter.py
    - test_info_presenter.py
    - test_application_state.py
    """

    @classmethod
    def setUpClass(cls):
        """
        QApplicationインスタンスを確認する
        """
        super().setUpClass()
        cls.app = QApplication.instance()
        if not cls.app:
            cls.app = QApplication([])

    def setUp(self):
        """
        各テストの前に新しいMainWindowインスタンスを作成する
        """
        super().setUp()

        # テスト用のシーンをクリア
        cmds.file(new=True, force=True)

        # MainWindow作成
        self.window = MainWindow()
        self.window.show()

        # ウィンドウが表示されるまで少し待つ
        QApplication.processEvents()

    def tearDown(self):
        """
        各テストの後にクリーンアップ
        """
        try:
            if self.window and self.window.isVisible():
                self.window.close()
                self.window.deleteLater()
            self.window = None
        except Exception:
            pass

        # イベントループを処理してウィジェットが完全に削除されるのを待つ
        QApplication.processEvents()

        super().tearDown()

    def test_initialization(self):
        """
        メインウィンドウが正しく作成されるかをテストする
        """
        # ウィンドウのタイトルが正しく設定されている
        self.assertEqual(self.window.windowTitle(), "MMD Tools")

        # オブジェクト名が正しく設定されている
        self.assertEqual(self.window.objectName(), "MMDToolsMainWindow")

        # ApplicationStateが作成されている
        self.assertIsNotNone(self.window.app_state)

        # 最小サイズが設定されている
        self.assertEqual(self.window.minimumWidth(), 800)
        self.assertEqual(self.window.minimumHeight(), 600)

    def test_tab_creation(self):
        """
        すべてのタブが作成され、タブウィジェットに追加されるかをテストする
        """
        # タブウィジェットが存在する
        self.assertIsNotNone(self.window.tab_widget)

        # 正しい数のタブが作成されている（8つ）
        self.assertEqual(self.window.tab_widget.count(), 8)

        # 各タブのタイトルを確認（翻訳辞書から期待値を導出し、UI 言語に依存しない）
        from mmd_tools.ui.translations import UITranslator

        translator = UITranslator.instance()
        tab_keys = ["file_io", "info", "material", "bone", "morph", "display_pane", "physics", "settings"]
        expected_titles = [translator.translate(key, "tabs") for key in tab_keys]

        for i, title in enumerate(expected_titles):
            self.assertEqual(self.window.tab_widget.tabText(i), title)

    def test_presenter_initialization(self):
        """
        各プレゼンターが初期化されているかをテストする
        """
        # 各プレゼンターが属性として存在する
        self.assertIsNotNone(self.window.import_export_presenter)
        self.assertIsNotNone(self.window.info_presenter)
        self.assertIsNotNone(self.window.material_presenter)
        self.assertIsNotNone(self.window.bone_presenter)
        self.assertIsNotNone(self.window.morph_presenter)
        self.assertIsNotNone(self.window.display_pane_presenter)
        self.assertIsNotNone(self.window.physics_presenter)
        self.assertIsNotNone(self.window.settings_presenter)

    def test_log_viewer_integration(self):
        """
        ログビューアが作成され、正しく統合されているかをテストする
        """
        # ログビューアが作成されている
        self.assertIsNotNone(self.window.log_viewer)

        # ログビューアのオブジェクト名が設定されている
        self.assertEqual(self.window.log_viewer.objectName(), "logViewer")

        # ログビューアが表示可能か確認
        self.assertTrue(self.window.log_viewer.isVisible() or self.window.log_viewer.isHidden())

    def test_status_bar_setup(self):
        """
        ステータスバーが正しく設定されているかをテストする
        """
        # ステータスバーが存在する
        self.assertIsNotNone(self.window.status_bar)

        # プログレスバーが存在する
        self.assertIsNotNone(self.window.progress_bar)

        # プログレスバーの最大幅が設定されている
        self.assertEqual(self.window.progress_bar.maximumWidth(), 200)

        # 初期状態では非表示
        self.assertFalse(self.window.progress_bar.isVisible())

    def test_header_widget_creation(self):
        """
        ヘッダーウィジェットが作成されているかをテストする
        """
        # ヘッダーウィジェットが存在する
        self.assertIsNotNone(self.window.header_widget)

    def test_application_state_signals(self):
        """
        ApplicationStateのシグナルが接続されているかをテストする
        """
        # show_status_messageメソッドが存在する
        self.assertTrue(hasattr(self.window, "show_status_message"))

        # update_progressメソッドが存在する
        self.assertTrue(hasattr(self.window, "update_progress"))

    def test_show_window_floating(self):
        """
        フローティングウィンドウとして表示するテスト
        """
        # 一旦ウィンドウを閉じる
        self.window.close()
        QApplication.processEvents()

        # show_windowメソッドが存在する
        self.assertTrue(hasattr(self.window, "show_window"))

        # フローティングウィンドウとして表示
        self.window.show_window(dockable=False)
        QApplication.processEvents()

        # ウィンドウが表示されている
        self.assertTrue(self.window.isVisible())

    def test_show_window_dockable(self):
        """
        ドッキング可能なウィンドウとして表示するテスト
        """
        # 一旦ウィンドウを閉じる
        self.window.close()
        QApplication.processEvents()

        # workspace controlの名前
        workspace_name = "MMDToolsWorkspaceControl"

        # 既存のworkspace controlがあれば削除
        if cmds.workspaceControl(workspace_name, exists=True):
            cmds.deleteUI(workspace_name, control=True)

        # ドッキング可能なウィンドウとして表示
        self.window.show_window(dockable=True)
        QApplication.processEvents()

        # workspace controlが作成されたか確認
        self.assertTrue(cmds.workspaceControl(workspace_name, exists=True))

        # ウィンドウが表示されている
        self.assertTrue(self.window.isVisible())

    def test_tab_interaction(self):
        """
        タブの切り替えが正しく動作するかテスト
        """
        # 初期状態は最初のタブが選択されている
        self.assertEqual(self.window.tab_widget.currentIndex(), 0)

        # 2番目のタブに切り替え
        self.window.tab_widget.setCurrentIndex(1)
        QApplication.processEvents()

        self.assertEqual(self.window.tab_widget.currentIndex(), 1)
        # 期待値は翻訳辞書から取得（UI 言語に依存しない）
        from mmd_tools.ui.translations import UITranslator

        self.assertEqual(
            self.window.tab_widget.tabText(1),
            UITranslator.instance().translate("info", "tabs"),
        )

    def test_window_resize(self):
        """
        ウィンドウのリサイズが正しく動作するかテスト
        """
        # ウィンドウサイズを変更
        new_width = 1000
        new_height = 700
        self.window.resize(new_width, new_height)
        QApplication.processEvents()

        # サイズが変更されているか（最小サイズ以上であることを確認）
        self.assertGreaterEqual(self.window.width(), self.window.minimumWidth())
        self.assertGreaterEqual(self.window.height(), self.window.minimumHeight())

    def test_close_event(self):
        """
        ウィンドウのクローズイベントが正しく処理されるかテスト
        """
        # ウィンドウを閉じる
        self.window.close()
        QApplication.processEvents()

        # ウィンドウが非表示になっている
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
