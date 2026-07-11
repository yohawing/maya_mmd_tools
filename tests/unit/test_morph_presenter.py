import unittest
from unittest.mock import MagicMock, patch
import json
from maya import cmds
from mmd_tools.ui.presenters import morph_presenter as morph_presenter_module
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.translations import UITranslator
from tests.common.mock_ui import attach_mocks
from tests.common.maya_test_base import MayaTestBase

UITranslator.instance().set_language("en")


class TestMorphPresenter(MayaTestBase):
    """MorphPresenterのテストクラス"""

    @staticmethod
    def _call_messages(mock_method):
        """Python 3.7 互換: call_args_list から第1位置引数のメッセージを集める。"""
        messages = []
        for call in mock_method.call_args_list:
            args = call[0]
            if args:
                messages.append(args[0])
        return messages

    def setUp(self):
        """テストのセットアップ"""
        super().setUp()

        # モックビューとアプリケーションステートを作成
        self.mock_view = MagicMock()
        self.mock_app_state = MagicMock()
        self.mock_app_state.current_model_root = None

        # ビューの各UI要素をモック
        self._setup_view_mocks()

        # プレゼンターを作成
        self.presenter = MorphPresenter(self.mock_view, self.mock_app_state)

    def _setup_view_mocks(self):
        """ビューのモックを設定"""
        attach_mocks(
            self.mock_view,
            [
                "morph_list",
                "group_filter_combo",
                "refresh_morphs_btn",
                "reset_slider_btn",
                "reset_all_btn",
                "connect_btn",
                "disconnect_btn",
                "auto_connect_btn",
                "select_blend_shape_btn",
                "apply_btn",
                "reset_btn",
                "save_preset_btn",
                "load_preset_btn",
                "delete_preset_btn",
                "search_edit",
                "morph_name_jp_edit",
                "morph_name_en_edit",
                "blend_shape_edit",
                "target_name_edit",
                "panel_combo",
                "morph_type_combo",
                "preset_combo",
                "morph_slider",
                "morph_value_label",
                "connection_status_label",
                "offset_count_label",
                "invert_check",
                "multiplier_spin",
                "offset_table",
                "set_morph_details_enabled",
            ],
            mock_cls=MagicMock,
        )
        self.mock_view.invert_check.isChecked.return_value = False
        self.mock_view.multiplier_spin.value.return_value = 1.0

    def test_init(self):
        """初期化のテスト"""
        # シグナルの接続を確認
        self.mock_app_state.current_model_changed.connect.assert_called_once()
        self.mock_view.morph_list.currentItemChanged.connect.assert_called_once()
        self.mock_view.refresh_morphs_btn.clicked.connect.assert_called_once()

        # 初期状態の確認
        self.assertIsNone(self.presenter.blend_shape_node)
        self.assertIsNone(self.presenter.current_morph)
        self.assertEqual(self.presenter.morph_data, {})
        self.assertEqual(self.presenter.group_morphs, {})
        self.assertFalse(self.presenter.is_updating)

    def test_load_morphs_no_model(self):
        """モデルがない場合のモーフロードのテスト"""
        self.presenter.load_morphs()

        # リストがクリアされることを確認
        self.mock_view.morph_list.clear.assert_called_once()
        self.assertEqual(self.presenter.morph_data, {})
        self.assertEqual(self.presenter.group_morphs, {})
        self.mock_view.set_morph_details_enabled.assert_called_with(False)

    def test_load_morphs_with_model(self):
        """モデルがある場合のモーフロードのテスト"""
        # テストモデルを作成
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model

        # ブレンドシェイプを持つメッシュを作成
        mesh = cmds.polyCube(name="test_mesh")[0]
        cmds.parent(mesh, test_model)

        # ブレンドシェイプを作成
        target = cmds.polyCube(name="test_target")[0]
        blend_shape = cmds.blendShape(target, mesh, name="test_blendShape")[0]
        cmds.delete(target)

        # モーフをロード
        self.mock_view.morph_list.count.return_value = 1
        with patch.object(morph_presenter_module, "logger") as mock_logger:
            self.presenter.load_morphs()

        # 結果を確認
        self.mock_view.morph_list.clear.assert_called()
        self.assertIn("test_target", self.presenter.morph_data)
        self.assertEqual(self.presenter.blend_shape_node, blend_shape)

        # 一覧ロード詳細は DEBUG のみ（INFO には出さない）
        expected = f"Loaded 1 morphs for model: {test_model}"
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_on_morph_selected(self):
        """モーフ選択時の処理のテスト"""
        # モーフデータを設定
        self.presenter.morph_data = {
            "test_morph": {
                "name_jp": "テストモーフ",
                "name_en": "test_morph",
                "panel": 1,
                "type": 0,
                "group": "目",
                "blend_shape_node": None,
                "blend_shape_target": None,
            }
        }

        # モーフ選択をシミュレート
        mock_item = MagicMock()
        mock_item.data.return_value = "test_morph"
        with patch.object(morph_presenter_module, "logger") as mock_logger:
            self.presenter.on_morph_selected(mock_item, None)

        # 結果を確認
        self.assertEqual(self.presenter.current_morph, "test_morph")
        self.mock_view.set_morph_details_enabled.assert_called_with(True)
        self.mock_view.morph_name_jp_edit.setText.assert_called_with("テストモーフ")
        self.mock_view.morph_name_en_edit.setText.assert_called_with("test_morph")
        self.mock_view.panel_combo.setCurrentIndex.assert_called_with(1)
        self.mock_view.morph_type_combo.setCurrentIndex.assert_called_with(0)

        expected = "Selected morph: test_morph"
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_mouth_alias_slider_writes_canonical_weight_plug(self):
        """Mouth_A01 alias の slider は canonical weight[0] を更新する。"""
        # ブレンドシェイプを作成
        mesh = cmds.polyCube(name="test_mesh")[0]
        target = cmds.polyCube(name="test_target")[0]
        blend_shape = cmds.blendShape(target, mesh, name="test_blendShape")[0]

        # ブレンドシェイプのエイリアスを設定
        cmds.aliasAttr("Mouth_A01", f"{blend_shape}.weight[0]")
        cmds.delete(target)

        # モーフデータを設定
        self.presenter.current_morph = "あ"
        self.presenter.morph_data = {
            "あ": {
                "blend_shape_node": blend_shape,
                "blend_shape_target": "Mouth_A01",
                "blend_shape_weight_attr": "weight[0]",
            }
        }

        # スライダー変更をシミュレート
        with patch.object(
            self.presenter.maya_adapter,
            "set_attr",
            wraps=self.presenter.maya_adapter.set_attr,
        ) as set_attr:
            self.presenter.on_morph_slider_changed(50)

        # 結果を確認
        self.mock_view.morph_value_label.setText.assert_called_with("50%")
        set_attr.assert_called_once_with(f"{blend_shape}.weight[0]", 0.5)
        weight = cmds.getAttr(f"{blend_shape}.weight[0]")
        self.assertAlmostEqual(weight, 0.5, places=5)

    def test_reset_all_morphs(self):
        """全モーフリセットのテスト"""
        # 複数のブレンドシェイプを作成
        morphs_data = {}
        for i in range(3):
            mesh = cmds.polyCube(name=f"test_mesh_{i}")[0]
            target = cmds.polyCube(name=f"test_target_{i}")[0]
            blend_shape = cmds.blendShape(target, mesh, name=f"test_blendShape_{i}")[0]

            # エイリアスを設定
            alias_name = f"morph_{i}_alias"
            cmds.aliasAttr(alias_name, f"{blend_shape}.weight[0]")
            cmds.delete(target)

            # 値を設定
            cmds.setAttr(f"{blend_shape}.{alias_name}", 0.5)

            morphs_data[f"morph_{i}"] = {"blend_shape_node": blend_shape, "blend_shape_target": alias_name}

        self.presenter.morph_data = morphs_data

        # リセットを実行
        self.presenter.reset_all_morphs()

        # 結果を確認
        for i in range(3):
            weight = cmds.getAttr(f"test_blendShape_{i}.morph_{i}_alias")
            self.assertAlmostEqual(weight, 0.0, places=5)

        self.mock_view.morph_slider.setValue.assert_called_with(0)
        self.mock_app_state.emit_status.assert_called()

    def test_filter_morphs(self):
        """モーフフィルタリングのテスト"""
        # モーフリストアイテムをモック
        items = []
        for name in ["smile", "wink", "sad", "angry"]:
            item = MagicMock()
            item.data.return_value = name
            items.append(item)

        self.presenter.morph_data = {name: {"name_jp": name} for name in ["smile", "wink", "sad", "angry"]}

        self.mock_view.morph_list.count.return_value = len(items)
        self.mock_view.morph_list.item = lambda i: items[i]

        # フィルタリング実行
        self.presenter.filter_morphs("s")

        # 結果を確認
        items[0].setHidden.assert_called_with(False)  # smile
        items[1].setHidden.assert_called_with(True)  # wink
        items[2].setHidden.assert_called_with(False)  # sad
        items[3].setHidden.assert_called_with(True)  # angry

    def test_connect_blend_shape(self):
        """ブレンドシェイプ連携のテスト"""
        # ブレンドシェイプを作成
        mesh = cmds.polyCube(name="test_mesh")[0]
        target = cmds.polyCube(name="test_target")[0]
        blend_shape = cmds.blendShape(target, mesh, name="test_blendShape")[0]
        cmds.delete(target)

        # UIの値を設定
        self.presenter.current_morph = "test_morph"
        self.presenter.morph_data = {"test_morph": {}}
        self.mock_view.blend_shape_edit.text.return_value = blend_shape
        self.mock_view.target_name_edit.text.return_value = "test_target"

        # 連携実行
        self.presenter.connect_blend_shape()

        # 結果を確認
        self.assertEqual(self.presenter.morph_data["test_morph"]["blend_shape_node"], blend_shape)
        self.assertEqual(self.presenter.morph_data["test_morph"]["blend_shape_target"], "test_target")
        self.mock_app_state.emit_status.assert_called()

    def test_save_and_load_preset(self):
        """プリセットの保存と読み込みのテスト"""
        # モデルとブレンドシェイプを作成
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model

        mesh = cmds.polyCube(name="test_mesh")[0]
        target = cmds.polyCube(name="test_target")[0]
        blend_shape = cmds.blendShape(target, mesh, name="test_blendShape")[0]

        # エイリアスを設定
        cmds.aliasAttr("smile_alias", f"{blend_shape}.weight[0]")
        cmds.delete(target)

        # モーフデータを設定
        self.presenter.morph_data = {"smile": {"blend_shape_node": blend_shape, "blend_shape_target": "smile_alias"}}

        # ブレンドシェイプに値を設定
        cmds.setAttr(f"{blend_shape}.smile_alias", 0.8)

        # プリセット名を設定
        self.mock_view.preset_combo.currentText.return_value = "test_preset"
        self.mock_view.preset_combo.findText.return_value = -1

        # プリセットを保存
        self.presenter.save_preset()

        # 保存されたことを確認
        self.assertTrue(cmds.attributeQuery("mmdMorphPresets", node=test_model, exists=True))
        presets_json = cmds.getAttr(f"{test_model}.mmdMorphPresets")
        presets = json.loads(presets_json)
        self.assertIn("test_preset", presets)
        self.assertAlmostEqual(presets["test_preset"]["smile"], 0.8, places=5)

        # 値をリセット
        cmds.setAttr(f"{blend_shape}.smile_alias", 0)

        # プリセットを読み込み
        self.presenter.load_preset()

        # 値が復元されたことを確認
        weight = cmds.getAttr(f"{blend_shape}.smile_alias")
        self.assertAlmostEqual(weight, 0.8, places=5)

    def test_auto_connect_blend_shapes(self):
        """ブレンドシェイプ自動連携のテスト"""
        # モデルを作成
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model

        # ブレンドシェイプを作成
        mesh = cmds.polyCube(name="test_mesh")[0]
        cmds.parent(mesh, test_model)

        # 複数のターゲットでブレンドシェイプを作成
        targets = []
        target_names = ["smile", "wink", "sad"]
        for name in target_names:
            target = cmds.polyCube(name=f"{name}_target")[0]
            targets.append(target)

        blend_shape = cmds.blendShape(targets, mesh, name="test_blendShape")[0]

        # ターゲットを削除
        for target in targets:
            cmds.delete(target)

        # モーフデータを設定（連携前）
        self.presenter.morph_data = {
            "smile": {"name_jp": "笑顔", "name_en": "smile"},
            "wink": {"name_jp": "ウィンク", "name_en": "wink"},
            "sad": {"name_jp": "悲しみ", "name_en": "sad"},
        }

        # 自動連携を実行
        with patch.object(morph_presenter_module, "logger") as mock_logger:
            self.presenter.auto_connect_blend_shapes()

        # 結果を確認
        for name in target_names:
            self.assertIn("blend_shape_node", self.presenter.morph_data[name])
            self.assertEqual(self.presenter.morph_data[name]["blend_shape_node"], blend_shape)
            self.assertIn("blend_shape_target", self.presenter.morph_data[name])

        self.mock_app_state.emit_status.assert_called()

        # 開始・完了は INFO、per-item 成功は DEBUG のみ
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn("Starting auto-connect", info_messages)
        self.assertIn("Auto-connect complete: connected 3 morph(s)", info_messages)
        per_item = f"Auto-connect succeeded: smile -> {blend_shape}."
        self.assertTrue(any(msg.startswith(per_item) for msg in debug_messages))
        self.assertFalse(any(msg.startswith("Auto-connect succeeded:") for msg in info_messages))

    def test_organize_morphs_by_group(self):
        """PMX panel に基づくモーフ整理のテスト"""
        # モーフデータを設定（stale group は分類に使わない）
        self.presenter.morph_data = {
            "eyebrow_up": {"panel": 1, "group": "カスタム"},
            "eyebrow_down": {"panel": 1, "group": "その他"},
            "eye_close": {"panel": 2, "group": "口"},
            "mouth_open": {"panel": 3, "group": "眉"},
            "cheek_red": {"panel": 4, "group": "カスタム"},
            "system_base": {"panel": 0, "group": "その他"},
            "custom_morph": {"group": "カスタム"},  # missing panel -> Other
        }

        # グループ整理を実行
        self.presenter._organize_morphs_by_group()

        # 結果を確認: panels 1-4 only; custom group strings ignored
        self.assertEqual(len(self.presenter.group_morphs["眉"]), 2)
        self.assertEqual(len(self.presenter.group_morphs["目"]), 1)
        self.assertEqual(len(self.presenter.group_morphs["口"]), 1)
        self.assertEqual(len(self.presenter.group_morphs["その他"]), 2)
        self.assertNotIn("カスタム", self.presenter.group_morphs)
        self.assertNotIn("system_base", self.presenter.group_morphs["その他"])

        self.assertIn("eyebrow_up", self.presenter.group_morphs["眉"])
        self.assertIn("eyebrow_down", self.presenter.group_morphs["眉"])
        self.assertIn("eye_close", self.presenter.group_morphs["目"])
        self.assertIn("custom_morph", self.presenter.group_morphs["その他"])

    def test_apply_changes(self):
        """変更適用のテスト"""
        # モデルを作成
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model

        # モーフデータを設定
        self.presenter.current_morph = "test_morph"
        self.presenter.morph_data = {
            "test_morph": {"name_jp": "旧名前", "name_en": "old_name", "panel": 0, "type": 0, "group": "その他"}
        }

        # UIの値を設定
        self.mock_view.morph_name_jp_edit.text.return_value = "新名前"
        self.mock_view.morph_name_en_edit.text.return_value = "new_name"
        self.mock_view.panel_combo.currentIndex.return_value = 1
        self.mock_view.morph_type_combo.currentIndex.return_value = 2

        # 変更を適用
        self.presenter.apply_changes()

        # 結果を確認
        data = self.presenter.morph_data["test_morph"]
        self.assertEqual(data["name_jp"], "新名前")
        self.assertEqual(data["name_en"], "new_name")
        self.assertEqual(data["panel"], 1)
        self.assertEqual(data["type"], 2)
        self.assertNotIn("group", data)

        # MMDアトリビュートに保存されたことを確認
        self.assertTrue(cmds.attributeQuery("mmdMorphData", node=test_model, exists=True))
        saved_data = json.loads(cmds.getAttr(f"{test_model}.mmdMorphData"))
        self.assertEqual(saved_data["test_morph"]["name_jp"], "新名前")

    def test_delete_preset(self):
        """プリセット削除のテスト"""
        # モデルを作成
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model

        # プリセットを作成
        presets = {
            "test_preset": {"smile": 0.5},
            "笑顔": {"smile": 1.0},  # デフォルトプリセット
        }
        cmds.addAttr(test_model, longName="mmdMorphPresets", dataType="string")
        cmds.setAttr(f"{test_model}.mmdMorphPresets", json.dumps(presets), type="string")

        # カスタムプリセットを削除
        self.mock_view.preset_combo.currentText.return_value = "test_preset"
        self.mock_view.preset_combo.findText.return_value = 1
        self.presenter.delete_preset()

        # 削除されたことを確認
        saved_presets = json.loads(cmds.getAttr(f"{test_model}.mmdMorphPresets"))
        self.assertNotIn("test_preset", saved_presets)
        self.assertIn("笑顔", saved_presets)  # デフォルトは残る

        # デフォルトプリセットの削除を試みる
        self.mock_view.preset_combo.currentText.return_value = "笑顔"
        self.presenter.delete_preset()

        # 削除されていないことを確認
        saved_presets = json.loads(cmds.getAttr(f"{test_model}.mmdMorphPresets"))
        self.assertIn("笑顔", saved_presets)
        self.mock_app_state.emit_status.assert_called_with("Default presets cannot be deleted", "warning")


if __name__ == "__main__":
    unittest.main()
