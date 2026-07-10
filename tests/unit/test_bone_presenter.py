"""BonePresenterのユニットテスト"""

import unittest
from unittest.mock import MagicMock, patch
import maya.cmds as cmds

from mmd_tools.core.constants import (
    ATTR_MMD_BONE_NAME,
    ATTR_MMD_BONE_NAME_EN,
    ATTR_MMD_DEFORM_LAYER,
)
from tests.common.mock_ui import attach_mocks
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.bone_presenter import BonePresenter
from mmd_tools.ui.tabs.bone_tab import BoneTab
from mmd_tools.ui.application_state import ApplicationState
from mmd_tools.ui.translations import UITranslator

UITranslator.instance().set_language("en")


class TestBonePresenter(MayaTestBase):
    """BonePresenterクラスのユニットテスト"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()

        # モックビューとアプリケーションステートを作成
        self.mock_view = MagicMock(spec=BoneTab)
        self.mock_app_state = MagicMock(spec=ApplicationState)

        attach_mocks(
            self.mock_view,
            [
                "bone_list",
                "refresh_btn",
                "search_edit",
                "bone_name_jp_edit",
                "bone_name_en_edit",
                "parent_bone_edit",
                "pos_x_spin",
                "pos_y_spin",
                "pos_z_spin",
                "deform_layer_spin",
                "connection_type_combo",
                "connection_bone_edit",
                "offset_x_spin",
                "offset_y_spin",
                "offset_z_spin",
                "rotatable_check",
                "movable_check",
                "visible_check",
                "enabled_check",
                "after_physics_check",
                "external_parent_check",
                "external_parent_key_label",
                "external_parent_key_spin",
                "ik_enabled_check",
                "ik_settings_group",
                "ik_links_group",
                "ik_target_edit",
                "ik_loop_spin",
                "ik_limit_angle_spin",
                "ik_links_table",
                "add_ik_link_btn",
                "remove_ik_link_btn",
                "move_up_btn",
                "move_down_btn",
                "rotation_grant_check",
                "move_grant_check",
                "grant_settings_group",
                "grant_parent_edit",
                "grant_rate_spin",
                "local_grant_check",
                "fixed_axis_check",
                "local_axis_check",
                "fixed_axis_group",
                "local_axis_group",
                "fixed_axis_x_spin",
                "fixed_axis_y_spin",
                "fixed_axis_z_spin",
                "local_x_axis_x_spin",
                "local_x_axis_y_spin",
                "local_x_axis_z_spin",
                "local_z_axis_x_spin",
                "local_z_axis_y_spin",
                "local_z_axis_z_spin",
                "select_parent_btn",
                "select_connection_btn",
                "select_ik_target_btn",
                "select_grant_parent_btn",
                "apply_btn",
                "reset_btn",
            ],
            mock_cls=MagicMock,
        )
        self.mock_view.bone_list.clear = MagicMock()
        self.mock_view.bone_list.addItem = MagicMock()
        self.mock_view.bone_list.selectedItems = MagicMock(return_value=[])

        # clicked属性を持つモックオブジェクトを設定
        button_attrs = [
            "select_parent_btn",
            "select_connection_btn",
            "select_ik_target_btn",
            "select_grant_parent_btn",
            "apply_btn",
            "reset_btn",
        ]
        for attr in button_attrs:
            if hasattr(self.mock_view, attr):
                getattr(self.mock_view, attr).clicked = MagicMock()

        # その他のウィジェットのシグナル
        self.mock_view.bone_list.currentItemChanged = MagicMock()
        self.mock_view.bone_list.itemSelectionChanged = MagicMock()
        self.mock_view.search_edit.textChanged = MagicMock()
        self.mock_view.ik_enabled_check.toggled = MagicMock()
        self.mock_view.rotation_grant_check.toggled = MagicMock()
        self.mock_view.move_grant_check.toggled = MagicMock()
        self.mock_view.fixed_axis_check.toggled = MagicMock()
        self.mock_view.local_axis_check.toggled = MagicMock()
        self.mock_view.external_parent_check.toggled = MagicMock()
        self.mock_view.connection_type_combo.currentIndexChanged = MagicMock()

        # IKリンクテーブルのモック設定
        self.mock_view.ik_links_table.rowCount.return_value = 0
        self.mock_view.ik_links_table.insertRow = MagicMock()
        self.mock_view.ik_links_table.setItem = MagicMock()
        self.mock_view.ik_links_table.setCellWidget = MagicMock()
        self.mock_view.ik_links_table.currentRow.return_value = -1
        self.mock_view.ik_links_table.removeRow = MagicMock()
        self.mock_view.ik_links_table.columnCount.return_value = 8
        self.mock_view.ik_links_table.item = MagicMock(return_value=None)
        self.mock_view.ik_links_table.cellWidget = MagicMock(return_value=None)
        self.mock_view.ik_links_table.setCurrentCell = MagicMock()

        # デフォルト値を設定
        self.mock_view.connection_type_combo.currentIndex.return_value = 0
        self.mock_view.ik_enabled_check.isChecked.return_value = False
        self.mock_view.rotation_grant_check.isChecked.return_value = False
        self.mock_view.move_grant_check.isChecked.return_value = False
        self.mock_view.fixed_axis_check.isChecked.return_value = False
        self.mock_view.local_axis_check.isChecked.return_value = False
        self.mock_view.external_parent_check.isChecked.return_value = False

        # スピンボックスのvalue関数
        spin_attrs = [
            "pos_x_spin",
            "pos_y_spin",
            "pos_z_spin",
            "deform_layer_spin",
            "offset_x_spin",
            "offset_y_spin",
            "offset_z_spin",
            "grant_rate_spin",
            "external_parent_key_spin",
            "fixed_axis_x_spin",
            "fixed_axis_y_spin",
            "fixed_axis_z_spin",
            "local_x_axis_x_spin",
            "local_x_axis_y_spin",
            "local_x_axis_z_spin",
            "local_z_axis_x_spin",
            "local_z_axis_y_spin",
            "local_z_axis_z_spin",
            "ik_loop_spin",
            "ik_limit_angle_spin",
        ]
        for attr in spin_attrs:
            if hasattr(self.mock_view, attr):
                spin = getattr(self.mock_view, attr)
                spin.value = MagicMock(return_value=0.0)
                spin.setValue = MagicMock()

        # エディットのtext関数
        edit_attrs = [
            "bone_name_jp_edit",
            "bone_name_en_edit",
            "parent_bone_edit",
            "connection_bone_edit",
            "ik_target_edit",
            "rotation_grant_parent_edit",
            "move_grant_parent_edit",
            "search_edit",
        ]
        for attr in edit_attrs:
            if hasattr(self.mock_view, attr):
                edit = getattr(self.mock_view, attr)
                edit.text = MagicMock(return_value="")
                edit.setText = MagicMock()

        # チェックボックスのsetChecked関数
        check_attrs = [
            "rotatable_check",
            "movable_check",
            "visible_check",
            "enabled_check",
            "ik_enabled_check",
            "rotation_grant_check",
            "move_grant_check",
            "fixed_axis_check",
            "local_axis_check",
            "after_physics_check",
            "external_parent_check",
            "local_grant_check",
        ]
        for attr in check_attrs:
            if hasattr(self.mock_view, attr):
                check = getattr(self.mock_view, attr)
                check.setChecked = MagicMock()
                check.isChecked.return_value = False

        # QTimerをモック
        with patch("mmd_tools.ui.presenters.bone_presenter.QTimer"):
            # プレゼンターを作成
            self.presenter = BonePresenter(self.mock_view, self.mock_app_state)

        # テスト用のモデルとボーンを作成
        self.test_model = cmds.group(empty=True, name="test_model")
        # app_stateのcurrent_model_rootを設定
        self.mock_app_state.current_model_root = self.test_model

        # ジョイントは作成時に階層構造を作る
        cmds.select(clear=True)
        self.test_bone1 = cmds.joint(name="test_bone1", position=[0, 0, 0])
        self.test_bone2 = cmds.joint(name="test_bone2", position=[0, 5, 0])
        self.test_bone3 = cmds.joint(name="test_bone3", position=[0, 10, 0])
        # test_bone1をtest_modelの子にする
        cmds.select(clear=True)
        cmds.parent(self.test_bone1, self.test_model)

        # MMD属性を追加
        self.presenter._ensure_mmd_attributes(self.test_bone1)
        self.presenter._ensure_mmd_attributes(self.test_bone2)
        self.presenter._ensure_mmd_attributes(self.test_bone3)

        # 基本的なMMD属性を設定
        cmds.setAttr(f"{self.test_bone1}.{ATTR_MMD_BONE_NAME}", "テストボーン1", type="string")
        cmds.setAttr(f"{self.test_bone1}.{ATTR_MMD_BONE_NAME_EN}", "test_bone1", type="string")
        cmds.setAttr(f"{self.test_bone2}.{ATTR_MMD_BONE_NAME}", "テストボーン2", type="string")
        cmds.setAttr(f"{self.test_bone2}.{ATTR_MMD_BONE_NAME_EN}", "test_bone2", type="string")
        cmds.setAttr(f"{self.test_bone3}.{ATTR_MMD_BONE_NAME}", "テストボーン3", type="string")
        cmds.setAttr(f"{self.test_bone3}.{ATTR_MMD_BONE_NAME_EN}", "test_bone3", type="string")

    def tearDown(self):
        """テスト後のクリーンアップ"""
        if cmds.objExists(self.test_model):
            cmds.delete(self.test_model)
        super().tearDown()

    def test_init(self):
        """初期化のテスト"""
        self.assertIsNone(self.presenter.current_bone)
        self.assertEqual(self.presenter.bone_data, {})
        self.assertEqual(self.presenter.bone_list_items, {})
        self.assertEqual(self.presenter.all_bones, [])
        self.assertFalse(self.presenter.is_updating)

    def test_load_bones(self):
        """ボーン読み込みのテスト"""
        # モデルルートを設定
        self.mock_app_state.current_model_root = self.test_model

        # ボーンを読み込み
        with patch("mmd_tools.ui.presenters.bone_presenter.logger") as mock_logger:
            self.presenter.load_bones()

        # リストがクリアされたことを確認
        self.mock_view.bone_list.clear.assert_called()

        # ボーンが読み込まれたことを確認
        self.assertEqual(len(self.presenter.bone_list_items), 3)
        self.assertIn(self.test_bone1, self.presenter.bone_list_items)
        self.assertIn(self.test_bone2, self.presenter.bone_list_items)
        self.assertIn(self.test_bone3, self.presenter.bone_list_items)

        # 一覧ロード詳細は DEBUG のみ（INFO には出さない）
        expected = f"Loaded 3 bones for model: {self.test_model}"
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_bone_flag_calculation(self):
        """ボーンフラグ計算のテスト"""
        # 各フラグを設定
        self.mock_view.connection_type_combo.currentIndex.return_value = 1  # ボーン接続
        self.mock_view.rotatable_check.isChecked.return_value = True
        self.mock_view.movable_check.isChecked.return_value = True
        self.mock_view.visible_check.isChecked.return_value = True
        self.mock_view.enabled_check.isChecked.return_value = True
        self.mock_view.ik_enabled_check.isChecked.return_value = True
        self.mock_view.after_physics_check.isChecked.return_value = True

        # フラグを計算
        flags = self.presenter._calculate_bone_flags()

        # 期待値を確認
        # PmxBoneFlagの値を使用
        from mmd_tools.core.pmx_data.bone import PmxBoneFlag

        expected_flags = (
            PmxBoneFlag.CONNECT_BONE
            | PmxBoneFlag.ROTATABLE
            | PmxBoneFlag.MOVABLE
            | PmxBoneFlag.DISPLAY
            | PmxBoneFlag.OPERATABLE
            | PmxBoneFlag.IK
            | PmxBoneFlag.DEFORM_AFTER_PHYSICS
        )
        self.assertEqual(flags, expected_flags)

    def test_on_bone_selected(self):
        """ボーン選択時: current_bone / details / property load と DEBUG ログ境界。"""
        mock_item = MagicMock()
        mock_item.data = MagicMock(return_value=self.test_bone1)

        with patch("mmd_tools.ui.presenters.bone_presenter.logger") as mock_logger, patch.object(
            self.presenter, "load_bone_properties"
        ) as mock_load:
            self.presenter.on_bone_selected(mock_item, None)

        self.assertEqual(self.presenter.current_bone, self.test_bone1)
        self.mock_view.set_bone_details_enabled.assert_called_with(True)
        mock_load.assert_called_once_with()

        expected = f"Selected bone: {self.test_bone1}"
        # Python 3.7 互換: call[0] で位置引数タプルを取る（_Call.args は使わない）
        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list if call[0]]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list if call[0]]
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_on_selection_changed_maya(self):
        """リスト選択時のMaya選択テスト"""
        # モックアイテムを作成
        mock_item = MagicMock()
        mock_item.data = MagicMock(return_value=self.test_bone1)
        self.mock_view.bone_list.selectedItems.return_value = [mock_item]

        # 選択変更を実行
        self.presenter.on_selection_changed_maya()

        # Mayaで選択されたことを確認
        selected = cmds.ls(selection=True)
        self.assertEqual(selected, [self.test_bone1])

    def test_ik_settings_toggle(self):
        """IK設定のトグルテスト"""
        # IKを有効化
        self.presenter.on_ik_enabled_toggled(True)
        self.mock_view.ik_settings_group.setVisible.assert_called_with(True)
        self.mock_view.ik_links_group.setVisible.assert_called_with(True)

        # IKを無効化
        self.presenter.on_ik_enabled_toggled(False)
        self.mock_view.ik_settings_group.setVisible.assert_called_with(False)
        self.mock_view.ik_links_group.setVisible.assert_called_with(False)

    def test_apply_changes(self):
        """変更適用のテスト"""
        # 現在のボーンを設定
        self.presenter.current_bone = self.test_bone1

        # UI の値を設定
        self.mock_view.bone_name_jp_edit.text.return_value = "新しい名前"
        self.mock_view.bone_name_en_edit.text.return_value = "new_name"
        self.mock_view.deform_layer_spin.value.return_value = 2
        self.mock_view.pos_x_spin.value.return_value = 1.0
        self.mock_view.pos_y_spin.value.return_value = 2.0
        self.mock_view.pos_z_spin.value.return_value = 3.0

        # 変更を適用
        self.presenter.apply_changes()

        # 属性が更新されたことを確認
        self.assertEqual(cmds.getAttr(f"{self.test_bone1}.{ATTR_MMD_BONE_NAME}"), "新しい名前")
        self.assertEqual(cmds.getAttr(f"{self.test_bone1}.{ATTR_MMD_BONE_NAME_EN}"), "new_name")
        self.assertEqual(cmds.getAttr(f"{self.test_bone1}.{ATTR_MMD_DEFORM_LAYER}"), 2)

        # 位置が更新されたことを確認
        pos = cmds.xform(self.test_bone1, query=True, translation=True, worldSpace=True)
        self.assertAlmostEqual(pos[0], 1.0, places=3)
        self.assertAlmostEqual(pos[1], 2.0, places=3)
        self.assertAlmostEqual(pos[2], 3.0, places=3)

    def test_grant_settings_toggle(self):
        """付与設定のトグルテスト"""
        # 付与を有効化
        self.mock_view.rotation_grant_check.isChecked.return_value = True
        self.presenter.on_grant_toggled()
        self.mock_view.grant_settings_group.setVisible.assert_called_with(True)

        # 付与を無効化
        self.mock_view.rotation_grant_check.isChecked.return_value = False
        self.mock_view.move_grant_check.isChecked.return_value = False
        self.presenter.on_grant_toggled()
        self.mock_view.grant_settings_group.setVisible.assert_called_with(False)

    def test_connection_type_change(self):
        """接続タイプ変更のテスト"""
        # 座標オフセットモード
        self.presenter.on_connection_type_changed(0)
        self.mock_view.offset_x_spin.setEnabled.assert_called_with(True)
        self.mock_view.connection_bone_edit.setEnabled.assert_called_with(False)

        # ボーン接続モード
        self.presenter.on_connection_type_changed(1)
        self.mock_view.offset_x_spin.setEnabled.assert_called_with(False)
        self.mock_view.connection_bone_edit.setEnabled.assert_called_with(True)

    def test_filter_bones(self):
        """ボーン検索フィルタのテスト"""
        # リストアイテムのモックを作成
        mock_item1 = MagicMock()
        mock_item1.text.return_value = "1:テストボーン1（test_bone1） [test_bone1]"
        mock_item1.setHidden = MagicMock()

        mock_item2 = MagicMock()
        mock_item2.text.return_value = "2:テストボーン2（test_bone2） [test_bone2]"
        mock_item2.setHidden = MagicMock()

        self.presenter.bone_list_items = {
            self.test_bone1: mock_item1,
            self.test_bone2: mock_item2,
        }

        # "ボーン1"で検索
        self.presenter.filter_bones("ボーン1")

        # 1つ目は表示、2つ目は非表示
        mock_item1.setHidden.assert_called_with(False)
        mock_item2.setHidden.assert_called_with(True)

        # 空文字で検索（全て表示）
        self.presenter.filter_bones("")

        # 両方とも表示
        mock_item1.setHidden.assert_called_with(False)
        mock_item2.setHidden.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
