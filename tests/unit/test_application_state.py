import unittest
from unittest.mock import Mock

from maya import cmds

from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME_EN, ATTR_MMD_MODEL_NAME
from tests.common.maya_test_base import MayaTestBase
from mmd_tools.ui.application_state import ApplicationState


class TestApplicationState(MayaTestBase):
    """ApplicationStateの単体テスト"""

    def setUp(self):
        super().setUp()
        self.app_state = ApplicationState()

    def test_initialization(self):
        """初期化のテスト"""
        self.assertIsNone(self.app_state.current_model_root)
        self.assertEqual(self.app_state.available_models, [])
        self.assertEqual(self.app_state._model_info_cache, {})

    def test_current_model_root_setter(self):
        """current_model_rootセッターのテスト"""
        # テスト用モデル作成（_rootで終わる名前にする）
        model = self._create_mmd_root("test_model_root")

        # シグナルをキャプチャ
        signal_catcher = Mock()
        self.app_state.current_model_changed.connect(signal_catcher)

        # モデルを設定
        self.app_state.current_model_root = model

        # 値が設定されたか
        self.assertEqual(self.app_state.current_model_root, model)

        # シグナルが発行されたか
        signal_catcher.assert_called_once_with(model)

    def test_current_model_root_setter_with_non_existent_model(self):
        """存在しないモデルを設定した場合"""
        # 存在しないモデルを設定
        self.app_state.current_model_root = "non_existent_model"

        # Noneになっているか
        self.assertIsNone(self.app_state.current_model_root)

    def test_refresh_model_list(self):
        """モデルリスト更新のテスト"""
        # テスト用モデルを複数作成（_rootで終わる名前にする）
        model1 = self._create_mmd_root("model1_root")
        model2 = self._create_mmd_root("model2_root")
        # MMDではないノードも作成
        cmds.group(empty=True, name="non_mmd_group")

        # シグナルをキャプチャ
        signal_catcher = Mock()
        self.app_state.model_list_updated.connect(signal_catcher)

        # モデルリストを更新
        self.app_state.refresh_model_list()

        # モデルが検出されているか
        self.assertIn(model1, self.app_state.available_models)
        self.assertIn(model2, self.app_state.available_models)
        self.assertEqual(len(self.app_state.available_models), 2)

        # シグナルが発行されたか
        signal_catcher.assert_called_once()
        emitted_list = signal_catcher.call_args[0][0]
        self.assertEqual(len(emitted_list), 2)

    def test_refresh_model_list_auto_select_first(self):
        """モデルリスト更新時の自動選択テスト"""
        # current_model_rootが未設定の状態で
        self.assertIsNone(self.app_state.current_model_root)

        # モデルを作成（_rootで終わる名前にする）
        model1 = self._create_mmd_root("model1_root")
        model2 = self._create_mmd_root("model2_root")

        # 明示的にMayaの選択をクリア（select_model_from_maya_selectionがFalseを返すように）
        cmds.select(clear=True)

        self.app_state.refresh_model_list()

        # 最初のモデルが自動選択されているか（アルファベット順でmodel1_rootが先）
        self.assertEqual(self.app_state.current_model_root, model1)

    def test_refresh_model_list_clear_invalid_current(self):
        """無効な現在のモデルがクリアされるテスト"""
        # モデルを作成して設定（_rootで終わる名前にする）
        model = self._create_mmd_root("temp_model_root")
        self.app_state.current_model_root = model

        # モデルを削除
        cmds.delete(model)

        # リフレッシュ
        self.app_state.refresh_model_list()

        # current_model_rootがNoneになっているか
        self.assertIsNone(self.app_state.current_model_root)

    def test_select_model_from_maya_selection(self):
        """Maya選択からモデルを選択するテスト"""
        # モデルを作成（_rootで終わる名前にする）
        model = self._create_mmd_root("selected_model_root")
        # モデルを選択してからジョイントを作成
        cmds.select(model)
        child_joint = cmds.joint(name="child_joint")

        # リフレッシュしてavailable_modelsに追加
        # 選択をクリアしてからリフレッシュ（自動選択を防ぐ）
        cmds.select(clear=True)
        self.app_state.refresh_model_list()

        # 別のモデルを作成して current_model_root を変更
        other_model = self._create_mmd_root("other_model_root")
        self.app_state._available_models.append(other_model)
        self.app_state.current_model_root = other_model

        # 子ジョイントを選択
        cmds.select(child_joint)

        # Maya選択から推測
        result = self.app_state.select_model_from_maya_selection()

        # 成功したか
        self.assertTrue(result)
        # 親のモデルが選択されたか
        self.assertEqual(self.app_state.current_model_root, model)

    def test_select_model_from_maya_selection_no_selection(self):
        """何も選択されていない場合"""
        cmds.select(clear=True)

        result = self.app_state.select_model_from_maya_selection()

        self.assertFalse(result)

    def test_get_model_info(self):
        """モデル情報取得のテスト"""
        # モデルを作成（_rootで終わる名前にする）
        model = self._create_mmd_root("info_test_model_root")

        # メッシュとジョイントを追加
        mesh_transform, mesh_shape = cmds.polyCube(name="test_mesh")
        cmds.parent(mesh_transform, model)

        joint1 = cmds.joint(name="joint1")
        joint2 = cmds.joint(name="joint2")
        cmds.parent(joint1, model)

        self.app_state.current_model_root = model

        # モデル情報を取得
        info = self.app_state.get_model_info()

        # 情報が正しいか確認
        self.assertIsNotNone(info)
        self.assertEqual(info["root"], model)
        self.assertEqual(info["vertex_count"], 8)  # Cubeは8頂点
        self.assertEqual(info["bone_count"], 2)  # 2つのジョイント
        self.assertIn("name_jp", info)
        self.assertIn("name_en", info)

    def test_get_model_info_with_cache(self):
        """キャッシュ機能のテスト"""
        model = self._create_mmd_root("cache_test_model_root")
        self.app_state.current_model_root = model

        # 1回目の取得
        info1 = self.app_state.get_model_info()

        # キャッシュに保存されているか
        self.assertIn(model, self.app_state._model_info_cache)

        # 2回目の取得（キャッシュから）
        info2 = self.app_state.get_model_info()

        # 同じ情報が返されるか
        self.assertEqual(info1, info2)

    def test_clear_cache(self):
        """キャッシュクリアのテスト"""
        model = self._create_mmd_root("cache_clear_test_root")
        self.app_state.current_model_root = model

        # 情報を取得してキャッシュに保存
        self.app_state.get_model_info()
        self.assertIn(model, self.app_state._model_info_cache)

        # キャッシュをクリア
        self.app_state.clear_cache()

        # キャッシュが空になっているか
        self.assertEqual(self.app_state._model_info_cache, {})

    def test_emit_status(self):
        """ステータスメッセージ送信のテスト"""
        # シグナルをキャプチャ
        signal_catcher = Mock()
        self.app_state.status_message.connect(signal_catcher)

        # ステータス送信
        self.app_state.emit_status("テストメッセージ")

        # シグナルが発行されたか
        signal_catcher.assert_called_once_with("テストメッセージ")

    def test_emit_progress(self):
        """進捗送信のテスト"""
        # シグナルをキャプチャ
        signal_catcher = Mock()
        self.app_state.progress_updated.connect(signal_catcher)

        # 正常な値
        self.app_state.emit_progress(50)
        signal_catcher.assert_called_with(50)

        # 範囲外の値（0未満）
        self.app_state.emit_progress(-10)
        signal_catcher.assert_called_with(0)

        # 範囲外の値（100超）
        self.app_state.emit_progress(150)
        signal_catcher.assert_called_with(100)

    def test_model_info_with_materials(self):
        """マテリアル情報を含むモデル情報のテスト"""
        model = self._create_mmd_root("material_test_model_root")

        # メッシュとマテリアルを作成
        mesh_transform, mesh_shape = cmds.polyCube(name="material_mesh")
        cmds.parent(mesh_transform, model)

        # マテリアルを作成して割り当て
        material = cmds.shadingNode("lambert", asShader=True, name="test_material")
        shading_group = cmds.sets(renderable=True, noSurfaceShader=True, empty=True)
        cmds.connectAttr(f"{material}.outColor", f"{shading_group}.surfaceShader")
        cmds.sets(mesh_transform, edit=True, forceElement=shading_group)

        self.app_state.current_model_root = model

        # モデル情報を取得
        info = self.app_state.get_model_info()

        # マテリアル数が正しいか
        self.assertEqual(info["material_count"], 1)

    def test_model_info_with_blend_shapes(self):
        """ブレンドシェイプ（モーフ）情報のテスト"""
        model = self._create_mmd_root("morph_test_model_root")

        # メッシュを作成
        mesh_transform, mesh_shape = cmds.polyCube(name="morph_mesh")
        cmds.parent(mesh_transform, model)

        # ターゲットメッシュを作成
        target1 = cmds.polyCube(name="target1")[0]
        target2 = cmds.polyCube(name="target2")[0]

        # ブレンドシェイプを作成
        blend_shape = cmds.blendShape(target1, target2, mesh_transform, name="test_blendShape")[0]

        # ターゲットを削除（通常のワークフロー）
        cmds.delete(target1, target2)

        self.app_state.current_model_root = model

        # モデル情報を取得
        info = self.app_state.get_model_info()

        # モーフ数が正しいか（ブレンドシェイプの数を確認）
        # 注：削除後のターゲット数を確認
        blend_shape_targets = cmds.blendShape(blend_shape, query=True, target=True) or []
        expected_count = len(blend_shape_targets)
        self.assertEqual(info["morph_count"], expected_count)

    def _create_mmd_root(self, name):
        """MMDルートノードを作成するヘルパーメソッド"""
        root = cmds.group(empty=True, name=name)
        cmds.addAttr(root, ln="mmd_root", at="bool", dv=True)
        cmds.addAttr(root, ln=ATTR_MMD_MODEL_NAME, dt="string")
        cmds.addAttr(root, ln=ATTR_MMD_MODEL_NAME_EN, dt="string")
        # find_all_mmd_modelsが検出できるようにダミー値を設定
        cmds.setAttr(f"{root}.{ATTR_MMD_MODEL_NAME}", "test", type="string")
        return root


if __name__ == "__main__":
    unittest.main()
