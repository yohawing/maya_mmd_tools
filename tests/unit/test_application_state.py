import unittest
from unittest.mock import Mock, patch

try:
    from maya import cmds
    from tests.common.maya_test_base import MayaTestBase

    MAYA_AVAILABLE = True
except ImportError:
    cmds = None
    MayaTestBase = unittest.TestCase
    MAYA_AVAILABLE = False

from mmd_tools.core.constants import ATTR_MMD_MODEL_NAME_EN, ATTR_MMD_MODEL_NAME
from mmd_tools.ui import application_state as application_state_module
from mmd_tools.ui.application_state import ApplicationState


class _FakeSceneModelService:
    def __init__(self):
        self.models = []
        self.existing = set()
        self.selection_model = None
        self.info = {}
        self.raise_on_list = False
        self.canonical = {}

    def canonical_node(self, node):
        return self.canonical.get(node, node if node in self.existing else None)

    def object_exists(self, node):
        return node in self.existing

    def list_mmd_models(self):
        if self.raise_on_list:
            raise RuntimeError("list failed")
        return list(self.models)

    def resolve_model_from_selection(self, available_models):
        if self.selection_model in available_models:
            return self.selection_model
        return None

    def get_model_info(self, model_root):
        return self.info.get(model_root, {"root": model_root})


class TestApplicationStateWithInjectedService(unittest.TestCase):
    def test_structured_progress_ignores_stale_operation_owners(self):
        app_state = ApplicationState(scene_model_service=_FakeSceneModelService())
        observed = []
        app_state.progress_state_changed.connect(observed.append)

        first = app_state.begin_progress("first")
        second = app_state.begin_progress("second")

        self.assertFalse(app_state.update_progress_state(first, "stale"))
        self.assertFalse(app_state.end_progress(first))
        self.assertTrue(app_state.update_progress_state(second, "busy"))
        self.assertTrue(app_state.end_progress(second))
        self.assertEqual(observed[-1].active, False)
        self.assertEqual(observed[-2].label, "busy")

    def test_constructor_accepts_scene_model_service(self):
        service = _FakeSceneModelService()
        app_state = ApplicationState(scene_model_service=service)

        self.assertIs(app_state._scene_model_service, service)

    def test_scene_model_service_property_returns_constructor_service(self):
        service = _FakeSceneModelService()
        app_state = ApplicationState(scene_model_service=service)

        self.assertIs(app_state.scene_model_service, service)
        self.assertIsNone(app_state.current_model_root)
        self.assertEqual(app_state.available_models, [])

    def test_invalid_current_model_emits_empty_string(self):
        service = _FakeSceneModelService()
        app_state = ApplicationState(scene_model_service=service)
        signal_catcher = Mock()
        app_state.current_model_changed.connect(signal_catcher)

        app_state.current_model_root = "missing_root"

        self.assertIsNone(app_state.current_model_root)
        signal_catcher.assert_called_once_with("")

    def test_refresh_model_list_prefers_selection_before_first_model(self):
        service = _FakeSceneModelService()
        service.models = ["a_root", "b_root"]
        service.existing = {"a_root", "b_root"}
        service.selection_model = "b_root"
        app_state = ApplicationState(scene_model_service=service)

        app_state.refresh_model_list()

        self.assertEqual(app_state.available_models, ["a_root", "b_root"])
        self.assertEqual(app_state.current_model_root, "b_root")

    def test_refresh_model_list_auto_selects_first_without_selection(self):
        service = _FakeSceneModelService()
        service.models = ["a_root", "b_root"]
        service.existing = {"a_root", "b_root"}
        app_state = ApplicationState(scene_model_service=service)

        app_state.refresh_model_list()

        self.assertEqual(app_state.current_model_root, "a_root")

    def test_refresh_model_list_clears_current_missing_from_model_list(self):
        service = _FakeSceneModelService()
        service.models = ["old_root"]
        service.existing = {"old_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state.current_model_root = "old_root"
        service.models = []
        signal_catcher = Mock()
        app_state.current_model_changed.connect(signal_catcher)

        app_state.refresh_model_list()

        self.assertIsNone(app_state.current_model_root)
        signal_catcher.assert_called_with("")

    def test_refresh_model_list_preserves_short_root_as_canonical_long_identity(self):
        service = _FakeSceneModelService()
        service.models = ["|MMT_TestModel_root"]
        service.existing = {"MMT_TestModel_root", "|MMT_TestModel_root"}
        service.canonical["MMT_TestModel_root"] = "|MMT_TestModel_root"
        app_state = ApplicationState(scene_model_service=service)

        app_state.current_model_root = "MMT_TestModel_root"
        app_state.refresh_model_list()

        self.assertEqual(app_state.current_model_root, "|MMT_TestModel_root")

    def test_unresolved_selection_does_not_replace_valid_current_model(self):
        service = _FakeSceneModelService()
        service.models = ["|modelA|model_root", "|modelB|model_root"]
        service.existing = set(service.models)
        app_state = ApplicationState(scene_model_service=service)
        app_state.current_model_root = service.models[0]
        service.selection_model = None

        self.assertFalse(app_state.select_model_from_maya_selection())
        self.assertEqual(app_state.current_model_root, service.models[0])

    def test_unresolved_current_identity_preserves_valid_current_model(self):
        service = _FakeSceneModelService()
        service.existing = {"current_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state.current_model_root = "current_root"

        app_state.current_model_root = "ambiguous_root"

        self.assertEqual(app_state.current_model_root, "current_root")

    def test_unresolved_current_identity_preserves_current_when_validation_raises(self):
        service = _FakeSceneModelService()
        service.existing = {"current_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state.current_model_root = "current_root"
        service.object_exists = Mock(side_effect=RuntimeError("Maya query failed"))

        app_state.current_model_root = "ambiguous_root"

        self.assertEqual(app_state.current_model_root, "current_root")

    def test_refresh_model_list_emits_empty_list_on_exception(self):
        service = _FakeSceneModelService()
        service.models = ["old_root"]
        service.existing = {"old_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state.refresh_model_list()
        service.raise_on_list = True
        signal_catcher = Mock()
        app_state.model_list_updated.connect(signal_catcher)

        app_state.refresh_model_list()

        self.assertEqual(app_state.available_models, [])
        signal_catcher.assert_called_with([])

    def test_explicit_refresh_failure_preserves_state_and_generation(self):
        service = _FakeSceneModelService()
        service.models = ["model_root"]
        service.existing = {"model_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state._available_models = ["model_root"]
        app_state._current_model_root = "model_root"
        app_state._model_info_cache = {"model_root": {"display_name": "cached"}}
        refresh_signal = Mock()
        app_state.model_refresh_completed.connect(refresh_signal)
        service.raise_on_list = True

        with self.assertRaises(RuntimeError):
            app_state.refresh_model_list(explicit=True)

        self.assertEqual(app_state.available_models, ["model_root"])
        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertEqual(app_state._model_info_cache, {"model_root": {"display_name": "cached"}})
        self.assertEqual(app_state.refresh_generation, 0)
        refresh_signal.assert_not_called()

    def test_explicit_refresh_replacement_emits_selection_without_eager_hidden_reload(self):
        service = _FakeSceneModelService()
        service.models = ["new_root"]
        service.existing = {"old_root", "new_root"}
        app_state = ApplicationState(scene_model_service=service)
        app_state._current_model_root = "old_root"
        current_signal = Mock()
        refresh_signal = Mock()
        app_state.current_model_changed.connect(current_signal)
        app_state.model_refresh_completed.connect(refresh_signal)

        app_state.refresh_model_list(explicit=True)

        current_signal.assert_called_once_with("new_root")
        refresh_signal.assert_called_once_with(1)
        self.assertEqual(app_state.current_model_root, "new_root")

    def test_get_model_info_uses_service_and_cache(self):
        service = _FakeSceneModelService()
        service.existing = {"model_root"}
        service.info = {"model_root": {"root": "model_root", "vertex_count": 10}}
        app_state = ApplicationState(scene_model_service=service)

        info = app_state.get_model_info("model_root")

        self.assertEqual(info["vertex_count"], 10)
        self.assertIs(app_state.get_model_info("model_root"), info)

    @staticmethod
    def _call_messages(mock_method):
        """Python 3.7 互換: call_args_list から第1位置引数のメッセージを集める。"""
        messages = []
        for call in mock_method.call_args_list:
            args = call[0]
            if args:
                messages.append(args[0])
        return messages

    def test_current_model_changed_logs_at_debug_not_info(self):
        service = _FakeSceneModelService()
        service.existing = {"model_root"}
        app_state = ApplicationState(scene_model_service=service)
        signal_catcher = Mock()
        app_state.current_model_changed.connect(signal_catcher)

        with patch.object(application_state_module, "logger") as mock_logger:
            app_state.current_model_root = "model_root"

        self.assertEqual(app_state.current_model_root, "model_root")
        signal_catcher.assert_called_once_with("model_root")

        expected = "Current model changed: None -> model_root"
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)

    def test_model_list_updated_logs_at_debug_not_info(self):
        service = _FakeSceneModelService()
        service.models = ["a_root", "b_root"]
        service.existing = {"a_root", "b_root"}
        app_state = ApplicationState(scene_model_service=service)
        signal_catcher = Mock()
        app_state.model_list_updated.connect(signal_catcher)

        with patch.object(application_state_module, "logger") as mock_logger:
            app_state.refresh_model_list()

        self.assertEqual(app_state.available_models, ["a_root", "b_root"])
        signal_catcher.assert_called_once_with(["a_root", "b_root"])

        expected = "Model list updated: 2 models found"
        debug_messages = self._call_messages(mock_logger.debug)
        info_messages = self._call_messages(mock_logger.info)
        self.assertIn(expected, debug_messages)
        self.assertNotIn(expected, info_messages)


@unittest.skipUnless(MAYA_AVAILABLE, "Maya is not available")
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
        self._create_mmd_root("model2_root")

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
        cmds.joint(name="joint2")
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
        return (cmds.ls(root, long=True) or [root])[0]


if __name__ == "__main__":
    unittest.main()
