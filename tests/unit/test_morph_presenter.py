"""MorphPresenterのユニットテスト"""

import unittest
from unittest.mock import MagicMock, patch, call
import maya.cmds as cmds
import json

from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.tabs.morph_tab import MorphTab
from mmd_tools.ui.application_state import ApplicationState


class TestMorphPresenter(MayaTestBase):
    """MorphPresenterクラスのユニットテスト"""

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()
        
        # モックビューとアプリケーションステートを作成
        self.mock_view = MagicMock(spec=MorphTab)
        self.mock_app_state = MagicMock(spec=ApplicationState)
        
        # モックビューの属性を設定
        self.mock_view.morph_list = MagicMock()
        self.mock_view.group_list = MagicMock()
        self.mock_view.morph_slider = MagicMock()
        self.mock_view.morph_value_label = MagicMock()
        self.mock_view.preset_combo = MagicMock()
        self.mock_view.search_edit = MagicMock()
        
        # 基本情報タブ
        self.mock_view.morph_name_jp_edit = MagicMock()
        self.mock_view.morph_name_en_edit = MagicMock()
        self.mock_view.panel_combo = MagicMock()
        self.mock_view.morph_type_combo = MagicMock()
        self.mock_view.group_combo = MagicMock()
        
        # Maya連携タブ
        self.mock_view.blend_shape_edit = MagicMock()
        self.mock_view.target_name_edit = MagicMock()
        self.mock_view.connection_status_label = MagicMock()
        self.mock_view.invert_check = MagicMock()
        self.mock_view.multiplier_spin = MagicMock()
        
        # オフセットタブ
        self.mock_view.offset_table = MagicMock()
        self.mock_view.offset_count_label = MagicMock()
        
        # ボタン
        self.mock_view.refresh_morphs_btn = MagicMock()
        self.mock_view.select_in_maya_btn = MagicMock()
        self.mock_view.add_group_btn = MagicMock()
        self.mock_view.remove_group_btn = MagicMock()
        self.mock_view.reset_slider_btn = MagicMock()
        self.mock_view.reset_all_btn = MagicMock()
        self.mock_view.save_preset_btn = MagicMock()
        self.mock_view.load_preset_btn = MagicMock()
        self.mock_view.delete_preset_btn = MagicMock()
        self.mock_view.connect_btn = MagicMock()
        self.mock_view.disconnect_btn = MagicMock()
        self.mock_view.auto_connect_btn = MagicMock()
        self.mock_view.select_blend_shape_btn = MagicMock()
        self.mock_view.apply_btn = MagicMock()
        self.mock_view.reset_btn = MagicMock()
        
        # clicked属性を持つモックオブジェクトを設定
        for attr in dir(self.mock_view):
            if attr.endswith('_btn'):
                getattr(self.mock_view, attr).clicked = MagicMock()
        
        # その他のウィジェットのシグナル
        self.mock_view.morph_list.currentItemChanged = MagicMock()
        self.mock_view.group_list.currentItemChanged = MagicMock()
        self.mock_view.morph_slider.valueChanged = MagicMock()
        self.mock_view.search_edit.textChanged = MagicMock()
        self.mock_view.morph_type_combo.currentIndexChanged = MagicMock()
        
        # デフォルト値を設定
        self.mock_view.invert_check.isChecked.return_value = False
        self.mock_view.multiplier_spin.value.return_value = 1.0
        self.mock_view.preset_combo.currentText.return_value = "なし"
        self.mock_view.preset_combo.findText.return_value = -1
        
        # プレゼンターを作成
        self.presenter = MorphPresenter(self.mock_view, self.mock_app_state)
        
        # テスト用のモデルとブレンドシェイプを作成
        self.test_model = cmds.group(empty=True, name="test_model")
        self.test_mesh = cmds.polyCube(name="test_mesh")[0]
        cmds.parent(self.test_mesh, self.test_model)
        
        # ブレンドシェイプを作成
        self.target1 = cmds.duplicate(self.test_mesh)[0]
        self.target2 = cmds.duplicate(self.test_mesh)[0]
        cmds.move(1, 0, 0, f"{self.target1}.vtx[*]", relative=True)
        cmds.move(0, 1, 0, f"{self.target2}.vtx[*]", relative=True)
        
        self.blend_shape = cmds.blendShape(
            self.target1, self.target2, self.test_mesh,
            name="test_blendShape"
        )[0]
        
        # エイリアスを設定
        cmds.aliasAttr("smile", f"{self.blend_shape}.weight[0]")
        cmds.aliasAttr("wink", f"{self.blend_shape}.weight[1]")
        
        # ターゲットメッシュを削除
        cmds.delete(self.target1, self.target2)

    def tearDown(self):
        """テスト後のクリーンアップ"""
        if cmds.objExists(self.test_model):
            cmds.delete(self.test_model)
        super().tearDown()

    def test_init(self):
        """初期化のテスト"""
        self.assertIsNone(self.presenter.blend_shape_node)  # 初期値はNone
        self.assertIsNone(self.presenter.current_morph)
        self.assertEqual(self.presenter.morph_data, {})
        self.assertEqual(self.presenter.group_morphs, {})
        self.assertFalse(self.presenter.is_updating)

    def test_load_morphs(self):
        """モーフ読み込みのテスト"""
        # モデルルートを設定
        self.mock_app_state.current_model_root = self.test_model
        
        # モーフデータを追加
        morph_data = {
            "smile": {
                "name_jp": "笑顔",
                "name_en": "smile",
                "panel": 2,
                "type": 0,
                "group": "口"
            },
            "wink": {
                "name_jp": "ウィンク",
                "name_en": "wink",
                "panel": 1,
                "type": 0,
                "group": "目"
            }
        }
        cmds.addAttr(self.test_model, longName="mmdMorphData", dataType="string")
        cmds.setAttr(f"{self.test_model}.mmdMorphData", json.dumps(morph_data), type="string")
        
        # モーフを読み込み
        self.presenter.load_morphs()
        
        # モーフデータが読み込まれたことを確認
        self.assertEqual(len(self.presenter.morph_data), 2)
        self.assertIn("smile", self.presenter.morph_data)
        self.assertIn("wink", self.presenter.morph_data)
        
        # ブレンドシェイプ情報が追加されたことを確認
        self.assertEqual(self.presenter.morph_data["smile"]["blend_shape_node"], self.blend_shape)
        self.assertEqual(self.presenter.morph_data["smile"]["blend_shape_target"], "smile")

    def test_morph_slider_realtime_update(self):
        """スライダーのリアルタイム更新テスト"""
        # モーフデータを設定
        self.presenter.current_morph = "smile"
        self.presenter.morph_data["smile"] = {
            "blend_shape_node": self.blend_shape,
            "blend_shape_target": "smile"
        }
        
        # スライダーを50%に設定
        self.presenter.on_morph_slider_changed(50)
        
        # ブレンドシェイプの値が更新されたことを確認
        weight = cmds.getAttr(f"{self.blend_shape}.smile")
        self.assertAlmostEqual(weight, 0.5, places=3)
        
        # ラベルが更新されたことを確認
        self.mock_view.morph_value_label.setText.assert_called_with("50%")

    def test_reset_all_morphs(self):
        """全モーフリセットのテスト"""
        # モーフに値を設定
        cmds.setAttr(f"{self.blend_shape}.smile", 0.7)
        cmds.setAttr(f"{self.blend_shape}.wink", 0.3)
        
        # モーフデータを設定
        self.presenter.morph_data = {
            "smile": {
                "blend_shape_node": self.blend_shape,
                "blend_shape_target": "smile"
            },
            "wink": {
                "blend_shape_node": self.blend_shape,
                "blend_shape_target": "wink"
            }
        }
        
        # 全モーフをリセット
        self.presenter.reset_all_morphs()
        
        # 値がリセットされたことを確認
        self.assertEqual(cmds.getAttr(f"{self.blend_shape}.smile"), 0)
        self.assertEqual(cmds.getAttr(f"{self.blend_shape}.wink"), 0)
        
        # スライダーもリセットされたことを確認
        self.mock_view.morph_slider.setValue.assert_called_with(0)

    def test_auto_connect_blend_shapes(self):
        """自動連携のテスト"""
        # モデルルートを設定
        self.mock_app_state.current_model_root = self.test_model
        
        # モーフデータを設定（ブレンドシェイプ未連携）
        self.presenter.morph_data = {
            "smile": {
                "name_jp": "笑顔",
                "name_en": "smile",
                "panel": 2,
                "type": 0,
                "group": "口"
            },
            "wink": {
                "name_jp": "ウィンク",
                "name_en": "wink", 
                "panel": 1,
                "type": 0,
                "group": "目"
            }
        }
        
        # 自動連携を実行
        self.presenter.auto_connect_blend_shapes()
        
        # 連携が成功したことを確認
        self.assertEqual(self.presenter.morph_data["smile"]["blend_shape_node"], self.blend_shape)
        self.assertEqual(self.presenter.morph_data["smile"]["blend_shape_target"], "smile")
        self.assertEqual(self.presenter.morph_data["wink"]["blend_shape_node"], self.blend_shape)
        self.assertEqual(self.presenter.morph_data["wink"]["blend_shape_target"], "wink")

    def test_save_and_load_preset(self):
        """プリセット保存・読み込みのテスト"""
        # モデルルートを設定
        self.mock_app_state.current_model_root = self.test_model
        
        # モーフデータを設定
        self.presenter.morph_data = {
            "smile": {
                "blend_shape_node": self.blend_shape,
                "blend_shape_target": "smile"
            },
            "wink": {
                "blend_shape_node": self.blend_shape,
                "blend_shape_target": "wink"
            }
        }
        
        # モーフに値を設定
        cmds.setAttr(f"{self.blend_shape}.smile", 0.8)
        cmds.setAttr(f"{self.blend_shape}.wink", 0.5)
        
        # プリセットを保存
        self.mock_view.preset_combo.currentText.return_value = "test_preset"
        self.presenter.save_preset()
        
        # プリセットが保存されたことを確認
        preset_data = cmds.getAttr(f"{self.test_model}.mmdMorphPresets")
        self.assertIsNotNone(preset_data)
        presets = json.loads(preset_data)
        self.assertIn("test_preset", presets)
        self.assertAlmostEqual(presets["test_preset"]["smile"], 0.8, places=3)
        self.assertAlmostEqual(presets["test_preset"]["wink"], 0.5, places=3)
        
        # モーフ値をリセット
        cmds.setAttr(f"{self.blend_shape}.smile", 0)
        cmds.setAttr(f"{self.blend_shape}.wink", 0)
        
        # プリセットを読み込み
        self.presenter.load_preset()
        
        # 値が復元されたことを確認
        self.assertAlmostEqual(cmds.getAttr(f"{self.blend_shape}.smile"), 0.8, places=3)
        self.assertAlmostEqual(cmds.getAttr(f"{self.blend_shape}.wink"), 0.5, places=3)

    def test_connect_blend_shape_manual(self):
        """手動連携のテスト"""
        # 現在のモーフを設定
        self.presenter.current_morph = "custom_morph"
        self.presenter.morph_data["custom_morph"] = {}
        
        # UI の値を設定
        self.mock_view.blend_shape_edit.text.return_value = self.blend_shape
        self.mock_view.target_name_edit.text.return_value = "smile"
        
        # 手動連携を実行
        self.presenter.connect_blend_shape()
        
        # 連携が設定されたことを確認
        self.assertEqual(
            self.presenter.morph_data["custom_morph"]["blend_shape_node"],
            self.blend_shape
        )
        self.assertEqual(
            self.presenter.morph_data["custom_morph"]["blend_shape_target"],
            "smile"
        )

    def test_filter_morphs_by_group(self):
        """グループフィルタのテスト"""
        # グループごとのモーフを設定
        self.presenter.group_morphs = {
            "眉": ["eyebrow_up", "eyebrow_down"],
            "目": ["wink", "blink"],
            "口": ["smile", "open"],
            "その他": ["other1", "other2"]
        }
        
        # 目グループでフィルタ
        self.presenter.filter_morphs_by_group("目")
        
        # モーフリストがクリアされたことを確認
        self.mock_view.morph_list.clear.assert_called()
        
        # 正しいモーフが追加されたことを確認
        self.assertEqual(self.mock_view.morph_list.addItem.call_count, 2)

    def test_apply_changes(self):
        """変更適用のテスト"""
        # モデルルートとモーフを設定
        self.mock_app_state.current_model_root = self.test_model
        self.presenter.current_morph = "smile"
        self.presenter.morph_data["smile"] = {
            "name_jp": "笑顔",
            "name_en": "smile",
            "panel": 2,
            "type": 0,
            "group": "口"
        }
        
        # UI の値を変更
        self.mock_view.morph_name_jp_edit.text.return_value = "にっこり"
        self.mock_view.morph_name_en_edit.text.return_value = "smile_new"
        self.mock_view.panel_combo.currentIndex.return_value = 3
        self.mock_view.morph_type_combo.currentIndex.return_value = 1
        self.mock_view.group_combo.currentText.return_value = "その他"
        
        # 変更を適用
        self.presenter.apply_changes()
        
        # データが更新されたことを確認
        self.assertEqual(self.presenter.morph_data["smile"]["name_jp"], "にっこり")
        self.assertEqual(self.presenter.morph_data["smile"]["name_en"], "smile_new")
        self.assertEqual(self.presenter.morph_data["smile"]["panel"], 3)
        self.assertEqual(self.presenter.morph_data["smile"]["type"], 1)
        self.assertEqual(self.presenter.morph_data["smile"]["group"], "その他")


if __name__ == "__main__":
    unittest.main()