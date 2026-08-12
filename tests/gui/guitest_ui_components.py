import json
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

        # development_mode を保存し、通常モード (False) に固定する
        from mmd_tools import settings as _s
        self._saved_dev_mode = _s.get("ui.general.development_mode", False)
        _s.set("ui.general.development_mode", False)

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

        # development_mode を元の値に戻す
        from mmd_tools import settings as _s
        _s.set("ui.general.development_mode", self._saved_dev_mode)

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
        通常モード（development_mode=False）でのタブ作成をテストする
        """
        # タブウィジェットが存在する
        self.assertIsNotNone(self.window.tab_widget)

        # Physics is available in normal mode as well as Development Mode.
        self.assertEqual(self.window.tab_widget.count(), 9)
        self.assertEqual(self.window.tab_widget.indexOf(self.window.display_pane_tab), 6)
        self.assertEqual(self.window.tab_widget.indexOf(self.window.physics_tab), 7)

        # 各タブのタイトルを確認（翻訳辞書から期待値を導出し、UI 言語に依存しない）
        from mmd_tools.ui.translations import UITranslator

        translator = UITranslator.instance()
        tab_keys = [
            "file_io",
            "export_workflow",
            "info",
            "material",
            "bone",
            "morph",
            "display_pane",
            "physics",
            "settings",
        ]
        expected_titles = [translator.translate(key, "tabs") for key in tab_keys]

        for i, title in enumerate(expected_titles):
            self.assertEqual(self.window.tab_widget.tabText(i), title)

    def test_presenter_initialization(self):
        """
        通常モードのプレゼンター初期化をテストする
        """
        # 各プレゼンターが属性として存在する
        self.assertIsNotNone(self.window.import_export_presenter)
        self.assertIsNotNone(self.window.info_presenter)
        self.assertIsNotNone(self.window.material_presenter)
        self.assertIsNotNone(self.window.bone_presenter)
        self.assertIsNotNone(self.window.morph_presenter)
        self.assertIsNotNone(self.window.display_pane_tab)
        self.assertIsNotNone(self.window.display_pane_presenter)
        self.assertIsNotNone(self.window.physics_tab)
        self.assertIsNotNone(self.window.physics_presenter)
        self.assertIsNotNone(self.window.settings_presenter)

    def test_physics_tab_is_exposed_in_development_mode(self):
        """
        Physics tab remains exposed in Development Mode.
        """
        from mmd_tools import settings as _s

        _s.set("ui.general.development_mode", True)
        dev_window = None
        try:
            dev_window = MainWindow()
            self.assertEqual(dev_window.tab_widget.count(), 9)
            self.assertIsNotNone(dev_window.physics_tab)
            self.assertIsNotNone(dev_window.physics_presenter)

            tab = dev_window.physics_tab
            # Development-mode editor shell: splitter / list tabs / search / scroll
            self.assertIsNotNone(tab.splitter)
            self.assertIsNotNone(tab.list_tabs)
            self.assertEqual(tab.list_tabs.count(), 2)
            self.assertIsNotNone(tab.rigid_body_search_edit)
            self.assertIsNotNone(tab.joint_search_edit)
            self.assertIsNotNone(tab.details_scroll_area)
            self.assertTrue(tab.details_scroll_area.widgetResizable())
            self.assertTrue(hasattr(tab, "apply_btn"))
            self.assertTrue(hasattr(tab, "reset_btn"))
            self.assertFalse(tab.apply_btn.isEnabled())
            self.assertFalse(tab.reset_btn.isEnabled())

            # Current presenter-facing controls
            for attr in (
                "refresh_btn",
                "collider_visible_check",
                "rigid_body_list",
                "joint_list",
            ):
                self.assertTrue(hasattr(tab, attr), f"missing attribute: {attr}")

            # Defaults: collider off and details disabled
            self.assertFalse(tab.collider_visible_check.isChecked())
            self.assertFalse(tab.physics_details_content.isEnabled())
        finally:
            _s.set("ui.general.development_mode", False)
            if dev_window is not None:
                dev_window.close()
                dev_window.deleteLater()
                QApplication.processEvents()

    def test_display_pane_editor_is_separate_and_available_in_normal_mode(self):
        """表示枠editorはAnimator Toolsetと独立した通常タブとして存在する。"""
        tab = self.window.display_pane_tab
        self.assertEqual(tab.objectName(), "DisplayPaneTab")
        self.assertEqual(tab.layout().contentsMargins().left(), 5)
        splitter = tab.layout().itemAt(0).widget()
        for index in range(splitter.count()):
            margins = splitter.widget(index).layout().contentsMargins()
            self.assertEqual(
                (margins.left(), margins.top(), margins.right(), margins.bottom()),
                (0, 0, 0, 0),
            )
        for attr in (
            "frame_list",
            "name_jp_edit",
            "name_en_edit",
            "special_frame_check",
            "item_table",
            "add_frame_btn",
            "add_element_btn",
            "apply_btn",
            "reset_btn",
        ):
            self.assertTrue(hasattr(tab, attr), f"missing attribute: {attr}")

    def test_display_pane_editor_applies_and_undoes_scene_metadata(self):
        """実Qt操作からroot JSONへ保存し、1回のMaya Undoで復元できる。"""
        root = cmds.createNode("transform", name="displayPaneGuiModel")
        cmds.addAttr(root, longName="mmd_display_frames_json", dataType="string")
        cmds.addAttr(root, longName="mmdMorphData", dataType="string")
        original_frames = [
            {
                "name": "Root",
                "name_english": "Root",
                "special_flag": 1,
                "elements": [{"type": 0, "index": 0}],
            },
            {
                "name": "表情",
                "name_english": "Facial",
                "special_flag": 1,
                "elements": [{"type": 1, "index": 0}],
            },
            {
                "name": "操作",
                "name_english": "Controls",
                "special_flag": 0,
                "elements": [],
            },
        ]
        original_json = json.dumps(original_frames, ensure_ascii=False, separators=(",", ":"))
        cmds.setAttr(f"{root}.mmd_display_frames_json", original_json, type="string")
        cmds.setAttr(
            f"{root}.mmdMorphData",
            json.dumps(
                [
                    {"name_jp": "笑い", "name_en": "Smile", "index": 0},
                    {"name_jp": "ウィンク", "name_en": "Wink", "index": 1},
                ],
                ensure_ascii=False,
            ),
            type="string",
        )
        bone = cmds.createNode("joint", name="displayPaneGuiRootBone", parent=root)
        cmds.addAttr(bone, longName="mmd_bone_index", attributeType="long")
        cmds.addAttr(bone, longName="mmd_bone_name", dataType="string")
        cmds.setAttr(f"{bone}.mmd_bone_index", 0)
        cmds.setAttr(f"{bone}.mmd_bone_name", "全ての親", type="string")

        self.window.app_state.current_model_root = root
        tab = self.window.display_pane_tab
        presenter = self.window.display_pane_presenter
        tab.frame_list.setCurrentRow(2)
        tab.name_jp_edit.setText("アクセサリ")
        presenter._choice_provider = lambda _title, choices: choices[-1]
        presenter.add_item()
        self.assertTrue(presenter.apply())

        applied = json.loads(cmds.getAttr(f"{root}.mmd_display_frames_json"))
        self.assertEqual(applied[2]["name"], "アクセサリ")
        self.assertEqual(applied[2]["elements"], [{"type": 1, "index": 1}])

        cmds.undo()
        self.assertEqual(cmds.getAttr(f"{root}.mmd_display_frames_json"), original_json)

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

    def test_info_tab_uses_header_as_the_only_model_selector(self):
        """Infoタブ内に重複したモデル選択UIを持たない。"""
        info_tab = self.window.info_presenter.view
        self.assertTrue(hasattr(self.window.header_widget, "model_combo"))
        self.assertTrue(hasattr(self.window.header_widget, "refresh_btn"))
        self.assertFalse(hasattr(info_tab, "model_combo"))
        self.assertFalse(hasattr(info_tab, "refresh_button"))
        self.assertFalse(hasattr(info_tab, "current_model_label"))

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
            UITranslator.instance().translate("export_workflow", "tabs"),
        )

    def test_retranslate_updates_display_pane_tab_title(self):
        """Live language changes include the Display Pane tab title."""
        from mmd_tools.ui.translations import UITranslator

        translator = UITranslator.instance()
        previous_language = translator.get_language()
        try:
            translator.set_language("en")
            self.window.retranslate_all_tabs()
            english = self.window.tab_widget.tabText(
                self.window.tab_widget.indexOf(self.window.display_pane_tab)
            )
            english_name_label = self.window.display_pane_tab.name_jp_label.text()
            self.assertEqual(english, translator.translate("display_pane", "tabs"))

            translator.set_language("ja")
            self.window.retranslate_all_tabs()
            japanese = self.window.tab_widget.tabText(
                self.window.tab_widget.indexOf(self.window.display_pane_tab)
            )
            japanese_name_label = self.window.display_pane_tab.name_jp_label.text()
            self.assertEqual(japanese, translator.translate("display_pane", "tabs"))
            self.assertNotEqual(japanese, english)
            self.assertNotEqual(japanese_name_label, english_name_label)
        finally:
            translator.set_language(previous_language)
            self.window.retranslate_all_tabs()

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
