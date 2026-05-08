import unittest
from unittest.mock import Mock, MagicMock

from maya import cmds

from mmd_tools.core.constants import (
    ATTR_MMD_COMMENT,
    ATTR_MMD_COMMENT_EN,
    ATTR_MMD_MODEL_NAME_EN,
    ATTR_MMD_MODEL_NAME,
)
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.info_presenter import InfoPresenter
from mmd_tools.ui.application_state import ApplicationState


class TestInfoPresenter(MayaTestBase):
    """InfoPresenterの単体テスト"""

    def setUp(self):
        super().setUp()

        # テスト用MMDモデルを作成
        self.test_model = self._create_test_mmd_model()

        # Viewのモック
        self.mock_view = self._create_mock_view()

        # ApplicationState
        self.app_state = ApplicationState()
        self.app_state.current_model_root = self.test_model

        # Presenter作成
        self.presenter = InfoPresenter(self.mock_view, self.app_state)

    def _create_test_mmd_model(self):
        """テスト用MMDモデルを作成"""
        root = cmds.group(empty=True, name="test_mmd_model")
        cmds.addAttr(root, ln="mmd_root", at="bool", dv=True)
        cmds.addAttr(root, ln=ATTR_MMD_MODEL_NAME, dt="string")
        cmds.addAttr(root, ln=ATTR_MMD_MODEL_NAME_EN, dt="string")
        cmds.addAttr(root, ln=ATTR_MMD_COMMENT, dt="string")
        cmds.addAttr(root, ln=ATTR_MMD_COMMENT_EN, dt="string")

        # 初期値設定
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "テストモデル", type="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME_EN}", "Test Model", type="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_COMMENT}", "テストコメント", type="string")
        cmds.setAttr(f"{root}.{ATTR_MMD_COMMENT_EN}", "Test Comment", type="string")

        return root

    def _create_mock_view(self):
        """ビューのモックを作成"""
        view = Mock()
        view.model_combo = Mock()
        view.model_combo.currentTextChanged = MagicMock()
        view.refresh_button = Mock()
        view.refresh_button.clicked = MagicMock()
        view.model_name_jp_edit = Mock()
        view.model_name_en_edit = Mock()
        view.comment_jp_edit = Mock()
        view.comment_en_edit = Mock()
        view.set_fields_enabled = Mock()

        # textChangedシグナルのモック
        for widget in [
            view.model_name_jp_edit,
            view.model_name_en_edit,
            view.comment_jp_edit,
            view.comment_en_edit,
        ]:
            widget.textChanged = MagicMock()
            widget.textChanged.disconnect = Mock()
            widget.textChanged.connect = Mock()

        return view

    def test_initialization(self):
        """初期化のテスト"""
        # ApplicationStateのシグナルが接続されているか
        # 注: 実際の接続は内部で行われるため、load_model_infoが呼ばれたかで確認
        self.mock_view.set_fields_enabled.assert_called_with(True)

    def test_load_model_info(self):
        """モデル情報読み込みのテスト"""
        # load_model_infoを明示的に呼び出す
        self.presenter.load_model_info()

        # setText/setPlainTextが正しい値で呼ばれたか確認
        self.mock_view.model_name_jp_edit.setText.assert_called_with("テストモデル")
        self.mock_view.model_name_en_edit.setText.assert_called_with("Test Model")
        self.mock_view.comment_jp_edit.setPlainText.assert_called_with("テストコメント")
        self.mock_view.comment_en_edit.setPlainText.assert_called_with("Test Comment")

    def test_load_model_info_with_no_model(self):
        """モデルが選択されていない場合のload_model_info"""
        # current_model_rootをNoneに設定
        self.app_state.current_model_root = None

        # load_model_info実行
        self.presenter.load_model_info()

        # clearが呼ばれたか確認
        self.mock_view.model_name_jp_edit.clear.assert_called()
        self.mock_view.model_name_en_edit.clear.assert_called()
        self.mock_view.comment_jp_edit.clear.assert_called()
        self.mock_view.comment_en_edit.clear.assert_called()

    def test_update_model_info(self):
        """モデル情報更新のテスト"""
        # モックの戻り値を設定
        self.mock_view.model_name_jp_edit.text.return_value = "新しい名前"
        self.mock_view.model_name_en_edit.text.return_value = "New Name"
        self.mock_view.comment_jp_edit.toPlainText.return_value = "新しいコメント"
        self.mock_view.comment_en_edit.toPlainText.return_value = "New Comment"

        # 更新実行
        self.presenter.update_model_info()

        # Mayaアトリビュートが更新されたか確認
        jp_name = cmds.getAttr(f"{self.test_model}.{ATTR_MMD_MODEL_NAME}")
        en_name = cmds.getAttr(f"{self.test_model}.{ATTR_MMD_MODEL_NAME_EN}")
        jp_comment = cmds.getAttr(f"{self.test_model}.{ATTR_MMD_COMMENT}")
        en_comment = cmds.getAttr(f"{self.test_model}.{ATTR_MMD_COMMENT_EN}")

        self.assertEqual(jp_name, "新しい名前")
        self.assertEqual(en_name, "New Name")
        self.assertEqual(jp_comment, "新しいコメント")
        self.assertEqual(en_comment, "New Comment")

    def test_on_current_model_changed(self):
        """モデル変更時の処理テスト"""
        # 新しいモデルを作成（名前を明示的に指定）
        new_model = cmds.group(empty=True, name="new_test_model")
        cmds.addAttr(new_model, ln="mmd_root", at="bool", dv=True)
        cmds.addAttr(new_model, ln=ATTR_MMD_MODEL_NAME, dt="string")
        cmds.addAttr(new_model, ln=ATTR_MMD_MODEL_NAME_EN, dt="string")
        cmds.addAttr(new_model, ln=ATTR_MMD_COMMENT, dt="string")
        cmds.addAttr(new_model, ln=ATTR_MMD_COMMENT_EN, dt="string")
        cmds.setAttr(f"{new_model}.{ATTR_MMD_MODEL_NAME}", "新モデル", type="string")

        # app_stateのcurrent_model_rootを更新してからテスト
        self.app_state.current_model_root = new_model

        # モデル変更を通知
        self.presenter.on_current_model_changed(new_model)

        # フィールドが有効化されたか
        self.mock_view.set_fields_enabled.assert_called_with(True)

        # 新しいモデルの情報が読み込まれたか
        # 任意の回数setTextが呼ばれた中で「新モデル」が含まれているか確認
        all_calls = [call[0][0] for call in self.mock_view.model_name_jp_edit.setText.call_args_list]
        self.assertIn("新モデル", all_calls)

    def test_on_current_model_changed_to_none(self):
        """モデルがNoneに変更された場合のテスト"""
        # モデルをNoneに変更
        self.presenter.on_current_model_changed(None)

        # フィールドが無効化されたか
        self.mock_view.set_fields_enabled.assert_called_with(False)

        # フィールドがクリアされたか
        self.mock_view.model_name_jp_edit.clear.assert_called()

    def test_update_model_combo(self):
        """モデルコンボボックス更新のテスト"""
        # テスト用に複数のモデルを作成
        model1 = self._create_test_mmd_model()
        model2 = self._create_test_mmd_model()
        cmds.rename(model1, "model1")
        cmds.rename(model2, "model2")

        models = [model1, model2]

        # コンボボックス更新
        self.presenter.update_model_combo(models)

        # コンボボックスがクリアされたか
        self.mock_view.model_combo.clear.assert_called()

        # アイテムが追加されたか
        self.assertEqual(self.mock_view.model_combo.addItem.call_count, 2)

        # フィールドが有効化されたか
        self.mock_view.set_fields_enabled.assert_called_with(True)

    def test_update_model_combo_with_no_models(self):
        """モデルがない場合のコンボボックス更新"""
        # 空のリストで更新
        self.presenter.update_model_combo([])

        # "No MMD models found"が追加されたか
        self.mock_view.model_combo.addItem.assert_called_with("No MMD models found")

        # フィールドが無効化されたか
        self.mock_view.set_fields_enabled.assert_called_with(False)

    def test_on_refresh_clicked(self):
        """リフレッシュボタンクリック時のテスト"""
        # リフレッシュ実行
        self.presenter.on_refresh_clicked()

        # ApplicationStateのrefresh_model_listが呼ばれたか
        # 注: 実際にrefresh_model_listが実行されるので、
        # model_list_updatedシグナルが発行されることで確認
        # ここでは単にエラーが発生しないことを確認

    def test_on_model_selected(self):
        """コンボボックスでモデル選択時のテスト"""
        # モックの設定
        self.mock_view.model_combo.currentIndex.return_value = 0
        self.mock_view.model_combo.itemData.return_value = self.test_model

        # モデル選択
        self.presenter.on_model_selected("Test Model (test_mmd_model)")

        # ApplicationStateのcurrent_model_rootが更新されたか
        self.assertEqual(self.app_state.current_model_root, self.test_model)

    def test_on_model_selected_with_invalid_model(self):
        """存在しないモデルが選択された場合"""
        # モックの設定
        self.mock_view.model_combo.currentIndex.return_value = 0
        self.mock_view.model_combo.itemData.return_value = "non_existent_model"

        # モデル選択
        self.presenter.on_model_selected("Non Existent Model")

        # ApplicationStateのcurrent_model_rootがNoneになったか
        self.assertIsNone(self.app_state.current_model_root)

    def test_signal_connections(self):
        """シグナル接続の確認"""
        # 各ウィジェットのシグナルが接続されているか
        self.mock_view.model_combo.currentTextChanged.connect.assert_called()
        self.mock_view.refresh_button.clicked.connect.assert_called()

        # テキスト変更シグナルが接続されているか
        for widget in [
            self.mock_view.model_name_jp_edit,
            self.mock_view.model_name_en_edit,
            self.mock_view.comment_jp_edit,
            self.mock_view.comment_en_edit,
        ]:
            widget.textChanged.connect.assert_called()


if __name__ == "__main__":
    unittest.main()
