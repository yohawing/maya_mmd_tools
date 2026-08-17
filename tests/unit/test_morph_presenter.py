import unittest
from unittest.mock import MagicMock, patch
from maya import cmds
from mmd_tools.ui.presenters import morph_presenter as morph_presenter_module
from mmd_tools.ui.presenters.morph_presenter import MorphPresenter
from mmd_tools.ui.translations import UITranslator
from mmd_tools.core.morph_topology import (
    MorphTopologyDiagnostic,
    MorphTopologyInspection,
)
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
                "refresh_morphs_btn",
                "reset_slider_btn",
                "reset_all_btn",
                "apply_btn",
                "reset_btn",
                "search_edit",
                "morph_name_jp_edit",
                "morph_name_en_edit",
                "panel_combo",
                "morph_type_combo",
                "morph_slider",
                "morph_value_label",
                "invert_check",
                "multiplier_spin",
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

    def test_topology_diagnostic_enables_explicit_repair_without_mutating_load(self):
        inspection = MorphTopologyInspection(
            {"1": ((0, 0.5),)},
            {},
            (MorphTopologyDiagnostic("stale", "cache differs"),),
        )
        coordinator = MagicMock()
        coordinator.inspect_morph_topology.return_value = inspection
        self.presenter.authoring_coordinator = coordinator

        self.presenter._inspect_morph_topology("|root")

        coordinator.inspect_morph_topology.assert_called_once_with("|root")
        coordinator.repair_morph_topology.assert_not_called()
        self.mock_view.set_topology_repair_state.assert_called_once_with(
            "stale: cache differs", True
        )
        self.assertEqual(self.presenter._controller_topology, {})

    def test_explicit_topology_repair_reloads_only_after_valid_readback(self):
        inspection = MorphTopologyInspection(
            {"1": ((0, 0.5),)}, {"1": ((0, 0.5),)}, ()
        )
        coordinator = MagicMock()
        coordinator.repair_morph_topology.return_value = inspection
        self.presenter.authoring_coordinator = coordinator
        self.mock_app_state.current_model_root = "|root"
        self.presenter.load_morphs = MagicMock()

        self.presenter.repair_morph_topology()

        coordinator.repair_morph_topology.assert_called_once_with("|root")
        self.presenter.load_morphs.assert_called_once_with()

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
        """Coordinator がない Apply はデータも Maya 属性も変更しない。"""
        test_model = cmds.group(empty=True, name="test_model_root")
        self.mock_app_state.current_model_root = test_model
        self.presenter.current_morph = "test_morph"
        self.presenter.morph_data = {
            "test_morph": {
                "name_jp": "旧名前",
                "name_en": "old_name",
                "panel": 0,
                "type": 0,
                "group": "その他",
            }
        }
        self.mock_view.morph_name_jp_edit.text.return_value = "新名前"
        self.mock_view.morph_name_en_edit.text.return_value = "new_name"
        self.mock_view.panel_combo.currentIndex.return_value = 1
        self.mock_view.morph_type_combo.currentIndex.return_value = 2

        self.presenter.apply_changes()

        data = self.presenter.morph_data["test_morph"]
        self.assertEqual(data["name_jp"], "旧名前")
        self.assertEqual(data["name_en"], "old_name")
        self.assertEqual(data["panel"], 0)
        self.assertEqual(data["type"], 0)
        self.assertIn("group", data)
        self.assertFalse(cmds.attributeQuery("mmdMorphData", node=test_model, exists=True))
        self.assertTrue(self.mock_app_state.emit_status.called)


if __name__ == "__main__":
    unittest.main()
