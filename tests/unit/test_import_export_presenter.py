"""ImportExportPresenterのMaya非依存ロジックを検証するテスト。

ImportExportPresenter 自体は純粋な分岐ロジックだが、import 連鎖の途中で
``mmd_tools.io.mmd_importer`` → ``pmd_importer`` 等が ``from maya import cmds`` を、
``..qt_compat`` が PySide6/PySide2 を要求するため、何もしないと import 時点で
これらが必要になる。そのため ``install_headless_ui_stubs()`` で maya と Qt を
スタブ化してから presenter を import する。これにより本テストは mayapy / Qt なしの
``nox -s ci_unit`` で実行できる。
"""

import unittest
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.actions.import_model_action import ImportModelResult  # noqa: E402
from mmd_tools.ui.presenters.import_export_presenter import (  # noqa: E402
    ImportExportPresenter,
)


class _FakeSignal:
    def connect(self, _callback):
        pass


class _FakeButton:
    clicked = _FakeSignal()


class _FakeLineEdit:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class _FakeCheckBox:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


class _FakeComboBox:
    def __init__(self, current_index=0, items=None):
        self._current_index = current_index
        self._items = items or {}

    def currentIndex(self):
        return self._current_index

    def itemData(self, index):
        return self._items.get(index)


class _FakeView:
    def __init__(self):
        self.import_path_button = _FakeButton()
        self.export_path_button = _FakeButton()
        self.import_button = _FakeButton()
        self.export_button = _FakeButton()
        self.vmd_path_button = _FakeButton()
        self.import_vmd_button = _FakeButton()
        self.import_path_edit = _FakeLineEdit("model.pmx")
        self.export_path_edit = _FakeLineEdit("out.pmx")
        self.vmd_path_edit = _FakeLineEdit("motion.vmd")
        self.target_model_combo = _FakeComboBox()
        self.new_file_check = _FakeCheckBox(False)

    def get_custom_namespace(self):
        return None

    def refresh_model_list(self):
        pass

    def add_import_path_to_history(self, _path):
        pass

    def add_vmd_path_to_history(self, _path):
        pass


class _FakeAppState:
    def __init__(self):
        self.current_model_root = None
        self.statuses = []
        self.progress = []

    def emit_status(self, message):
        self.statuses.append(message)

    def emit_progress(self, value):
        self.progress.append(value)

    def refresh_model_list(self):
        pass


class _RecordingImportModelAction:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class _FailingImportModelAction:
    def execute(self, _request):
        raise AssertionError("model action must not be used")


class TestImportExportPresenter(unittest.TestCase):
    """ImportExportPresenterのimport options構築を検証する。"""

    def setUp(self):
        self._old_bake_mode = settings.get("import.rig.bake_mode", True)
        self._old_dev_mode = settings.get("ui.general.development_mode", False)
        self._old_texture_dialog = settings.get("import.model.show_texture_issue_dialog", True)

    def tearDown(self):
        settings.set("import.rig.bake_mode", self._old_bake_mode)
        settings.set("ui.general.development_mode", self._old_dev_mode)
        settings.set("import.model.show_texture_issue_dialog", self._old_texture_dialog)

    def test_import_file_passes_no_rig_options_when_bake_mode_enabled(self):
        # Dev mode required so the saved bake_mode setting is respected.
        settings.set("ui.general.development_mode", True)
        settings.set("import.rig.bake_mode", True)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value="model_root",
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertFalse(options["setup_rig"])
        self.assertFalse(options["setup_bone_orientation"])

    def test_import_file_leaves_rig_options_unset_by_default(self):
        # In dev mode with bake_mode=False the rig setup keys must be absent.
        settings.set("ui.general.development_mode", True)
        settings.set("import.rig.bake_mode", False)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value="model_root",
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertNotIn("setup_rig", options)
        self.assertNotIn("setup_bone_orientation", options)

    def test_import_file_vmd_uses_current_model_root(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("motion.vmd")
        app_state = _FakeAppState()
        app_state.current_model_root = "model_root"
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertEqual(options["target_model"], "model_root")

    def test_import_file_passes_profile_and_shows_texture_issue_dialog(self):
        settings.set("import.model.show_texture_issue_dialog", True)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        issue = {"file_node": "file1", "material": "mat1", "reason": "ansi_incompatible_path"}

        def fake_import(_path, options=None):
            options["profile"]["texture_issues"] = [issue]
            return "model_root"

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            side_effect=fake_import,
        ) as mock_import, patch(
            "mmd_tools.ui.texture_issue_dialog.TextureIssueDialog",
        ) as mock_dialog:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertIn("profile", options)
        mock_dialog.assert_called_once_with([issue], model_path="model.pmx", app_state=app_state, parent=view)
        mock_dialog.return_value.exec.assert_called_once()

    def test_import_file_skips_texture_issue_dialog_when_setting_disabled(self):
        settings.set("import.model.show_texture_issue_dialog", False)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        def fake_import(_path, options=None):
            options["profile"]["texture_issues"] = [{"file_node": "file1"}]
            return "model_root"

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            side_effect=fake_import,
        ), patch("mmd_tools.ui.texture_issue_dialog.TextureIssueDialog") as mock_dialog:
            presenter.import_file()

        mock_dialog.assert_not_called()

    def test_import_file_model_branch_uses_injected_action_and_updates_ui_state(self):
        recorded_refreshes = []
        recorded_history = []

        class _RecordingView(_FakeView):
            def refresh_model_list(self):
                recorded_refreshes.append("refresh")

            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        app_state = _FakeAppState()
        action = _RecordingImportModelAction(ImportModelResult(root_node="model_root", succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_model_action=action)

        presenter.import_file()

        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].file_path, "model.pmx")
        self.assertIn("profile", action.requests[0].options)
        self.assertFalse(action.requests[0].create_new_scene)
        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertIn("Import complete: model_root", app_state.statuses)
        self.assertIn(100, app_state.progress)
        self.assertEqual(recorded_refreshes, ["refresh"])
        self.assertEqual(recorded_history, ["model.pmx"])

    def test_import_file_vmd_branch_does_not_use_model_action(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("motion.vmd")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state, import_model_action=_FailingImportModelAction())

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ) as mock_import:
            presenter.import_file()

        mock_import.assert_called_once()

    def test_import_vmd_auto_detect_uses_current_model_root(self):
        view = _FakeView()
        app_state = _FakeAppState()
        app_state.current_model_root = "model_root"
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ) as mock_import:
            presenter.import_vmd_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertEqual(options["target_model"], "model_root")

    def test_import_vmd_explicit_target_overrides_current_model_root(self):
        view = _FakeView()
        view.target_model_combo = _FakeComboBox(1, {1: "explicit_model_root"})
        app_state = _FakeAppState()
        app_state.current_model_root = "current_model_root"
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ) as mock_import:
            presenter.import_vmd_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertEqual(options["target_model"], "explicit_model_root")


class TestImportFileGuards(unittest.TestCase):
    """import_file の早期 return とエラー処理の分岐を検証する。"""

    def test_empty_path_emits_status_and_does_not_import(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("   ")  # 空白のみ → strip 後は空
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
        ) as mock_import:
            presenter.import_file()

        mock_import.assert_not_called()
        self.assertIn("Please enter a file path", app_state.statuses)

    def test_successful_import_updates_app_state(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value="returned_root",
        ):
            presenter.import_file()

        self.assertEqual(app_state.current_model_root, "returned_root")
        self.assertIn(100, app_state.progress)
        self.assertTrue(any("Import complete" in s for s in app_state.statuses))

    def test_import_returning_none_emits_failure(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value=None,
        ):
            presenter.import_file()

        # current_model_root は更新されず、失敗ステータスが出る
        self.assertIsNone(app_state.current_model_root)
        self.assertIn("Import failed", app_state.statuses)

    def test_import_exception_is_caught_and_reported(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            side_effect=RuntimeError("boom"),
        ):
            # 例外は presenter 内で捕捉される
            presenter.import_file()

        self.assertTrue(any("Import error" in s for s in app_state.statuses))

    def test_new_file_check_triggers_cmds_file_new(self):
        # import_file 内の ``from maya import cmds; cmds.file(new=True, ...)`` の検証は
        # スタブ済み maya.cmds (MagicMock) を前提とする。実 maya (mayapy) では
        # cmds.file が本物になり呼び出し検証ができないためスキップする。
        from maya import cmds

        if not isinstance(getattr(cmds, "file", None), MagicMock):
            self.skipTest("real maya.cmds present; new-file call assertion is mayapy-irrelevant")

        view = _FakeView()
        view.new_file_check = _FakeCheckBox(True)
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        # 共有 MagicMock のため、他テストの呼び出し履歴をリセットしてから検証する。
        cmds.file.reset_mock()

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value="root",
        ):
            presenter.import_file()

        cmds.file.assert_called_with(new=True, force=True)


class TestVmdImportOptions(unittest.TestCase):
    """VMD import option 構築と target model 解決を検証する。"""

    def _make_presenter(self, view=None, app_state=None):
        view = view or _FakeView()
        app_state = app_state or _FakeAppState()
        return ImportExportPresenter(view, app_state), view, app_state

    def test_vmd_branch_includes_animation_keys(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("motion.VMD")  # 大文字拡張子も許容
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        for key in (
            "start_frame",
            "vmd_fps",
            "import_bone_animation",
            "import_morph_animation",
            "import_camera_animation",
            "import_light_animation",
            "resample_curves",
            "target_model",
        ):
            self.assertIn(key, options)

    def test_pmx_branch_excludes_animation_keys(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("model.pmx")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            return_value="root",
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertNotIn("start_frame", options)
        self.assertNotIn("vmd_fps", options)

    def test_get_vmd_target_model_prefers_explicit_combo(self):
        view = _FakeView()
        view.target_model_combo = _FakeComboBox(2, {2: "combo_model"})
        app_state = _FakeAppState()
        app_state.current_model_root = "current_model"
        presenter, _, _ = self._make_presenter(view, app_state)
        self.assertEqual(presenter._get_vmd_target_model(), "combo_model")

    def test_get_vmd_target_model_falls_back_to_current_root(self):
        view = _FakeView()
        view.target_model_combo = _FakeComboBox(0, {})  # index 0 = Auto Detect
        app_state = _FakeAppState()
        app_state.current_model_root = "current_model"
        presenter, _, _ = self._make_presenter(view, app_state)
        self.assertEqual(presenter._get_vmd_target_model(), "current_model")

    def test_get_vmd_target_model_returns_none_when_nothing_selected(self):
        view = _FakeView()
        view.target_model_combo = _FakeComboBox(0, {})
        app_state = _FakeAppState()  # current_model_root = None
        presenter, _, _ = self._make_presenter(view, app_state)
        self.assertIsNone(presenter._get_vmd_target_model())

    def test_import_vmd_empty_path_guard(self):
        view = _FakeView()
        view.vmd_path_edit = _FakeLineEdit("")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
        ) as mock_import:
            presenter.import_vmd_file()

        mock_import.assert_not_called()
        self.assertIn("Please enter a VMD file path", app_state.statuses)

    def test_import_vmd_success_adds_history(self):
        recorded = []

        class _RecordingView(_FakeView):
            def add_vmd_path_to_history(self, path):
                recorded.append(path)

        view = _RecordingView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value=True,
        ):
            presenter.import_vmd_file()

        self.assertEqual(recorded, ["dance.vmd"])
        self.assertIn(100, app_state.progress)


class TestExportFile(unittest.TestCase):
    """export_file は未実装である旨を明示する分岐を検証する。"""

    _KEYS_TO_PRESERVE = (
        "export.general.export_format",
        "export.general.apply_scale",
    )

    def setUp(self):
        self._saved = {k: settings.get(k) for k in self._KEYS_TO_PRESERVE}

    def tearDown(self):
        for k, v in self._saved.items():
            settings.set(k, v)

    def test_empty_path_guard(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        presenter.export_file()
        self.assertIn("Please enter a file path", app_state.statuses)

    def test_reports_not_implemented(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.pmx")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        presenter.export_file()
        self.assertTrue(
            any("not implemented" in s.lower() for s in app_state.statuses)
        )

    def test_build_export_options_includes_file_path(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("  out.pmx  ")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        opts = presenter._build_export_options()

        self.assertEqual(opts["file_path"], "out.pmx")

    def test_build_export_options_reads_format_from_settings(self):
        settings.set("export.general.export_format", "pmd")
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        opts = presenter._build_export_options()

        self.assertEqual(opts["export_format"], "pmd")

    def test_build_export_options_reads_apply_scale_from_settings(self):
        settings.set("export.general.apply_scale", False)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        opts = presenter._build_export_options()

        self.assertFalse(opts["apply_scale"])

    def test_export_file_does_not_call_pmx_exporter_before_scene_collection_exists(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.pmx")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch("mmd_tools.io.pmx_exporter.PmxExporter") as mock_exporter:
            presenter.export_file()

        mock_exporter.assert_not_called()
        self.assertTrue(
            any("not implemented" in s.lower() for s in app_state.statuses)
        )


class TestDevModeBehaviorGating(unittest.TestCase):
    """通常モードが dev-only 設定を強制デフォルトに上書きすることを検証する。"""

    _KEYS_TO_PRESERVE = (
        "ui.general.development_mode",
        "import.model.import_models",
        "import.physics.import_physics",
        "import.model.separate_meshes_by_material",
        "import.model.split_meshes_by_morph_groups",
        "import.model.hide_hidden_geometry",
        "import.model.auto_classify_transparency",
        "import.model.disable_backface_culling",
        "import.model.uv_set_name",
        "import.model.texture_search_path",
        "import.rig.add_semi_standard_bones",
        "import.naming.translate_names",
        "import.rig.bake_mode",
        "import.animation.resample_curves",
    )

    def setUp(self):
        self._saved = {k: settings.get(k) for k in self._KEYS_TO_PRESERVE}

    def tearDown(self):
        for k, v in self._saved.items():
            settings.set(k, v)

    def _run_import(self, path="model.pmx"):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit(path)
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        mock_target = (
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file"
            if path.lower().endswith(".vmd")
            else "mmd_tools.actions.import_model_action.import_mmd_file"
        )
        with patch(
            mock_target,
            return_value="root",
        ) as mock_import:
            presenter.import_file()
        return mock_import.call_args.kwargs["options"]

    def test_normal_mode_forces_import_models_true(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.import_models", False)
        opts = self._run_import()
        self.assertTrue(opts["import_models"])

    def test_normal_mode_forces_import_physics_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.physics.import_physics", True)
        opts = self._run_import()
        self.assertFalse(opts["import_physics"])

    def test_normal_mode_forces_separate_meshes_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.separate_meshes_by_material", True)
        opts = self._run_import()
        self.assertFalse(opts["separate_meshes_by_material"])

    def test_normal_mode_forces_hide_hidden_geometry_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.hide_hidden_geometry", True)
        opts = self._run_import()
        self.assertFalse(opts["hide_hidden_geometry"])

    def test_normal_mode_bake_mode_forced_true_produces_no_rig_options(self):
        # In normal mode bake_mode is always True regardless of the saved setting.
        settings.set("ui.general.development_mode", False)
        settings.set("import.rig.bake_mode", False)
        opts = self._run_import()
        self.assertFalse(opts.get("setup_rig", True))
        self.assertFalse(opts.get("setup_bone_orientation", True))

    def test_dev_mode_preserves_non_default_separate_meshes(self):
        settings.set("ui.general.development_mode", True)
        settings.set("import.model.separate_meshes_by_material", True)
        opts = self._run_import()
        self.assertTrue(opts["separate_meshes_by_material"])

    def test_normal_mode_vmd_forces_resample_curves_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.animation.resample_curves", True)
        opts = self._run_import(path="motion.vmd")
        self.assertFalse(opts["resample_curves"])

    def test_dev_mode_vmd_preserves_resample_curves_true(self):
        settings.set("ui.general.development_mode", True)
        settings.set("import.animation.resample_curves", True)
        opts = self._run_import(path="motion.vmd")
        self.assertTrue(opts["resample_curves"])

    def test_normal_mode_forces_add_semi_standard_bones_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.rig.add_semi_standard_bones", True)
        opts = self._run_import()
        self.assertFalse(opts["add_semi_standard_bones"])

    def test_normal_mode_forces_translate_names_true(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.naming.translate_names", False)
        opts = self._run_import()
        self.assertTrue(opts["translate_names"])

    def test_normal_mode_forces_disable_backface_culling_true(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.disable_backface_culling", False)
        opts = self._run_import()
        self.assertTrue(opts["disable_backface_culling"])

    def test_normal_mode_forces_uv_set_name_default(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.uv_set_name", "customUV")
        opts = self._run_import()
        self.assertEqual(opts["uv_set_name"], "map#")

    def test_normal_mode_forces_texture_search_path_empty(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.texture_search_path", "/some/path")
        opts = self._run_import()
        self.assertEqual(opts["texture_search_path"], "")

    def test_normal_mode_forces_auto_classify_transparency_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.auto_classify_transparency", True)
        opts = self._run_import()
        self.assertFalse(opts["auto_classify_transparency"])

    def test_normal_mode_forces_split_meshes_by_morph_groups_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.split_meshes_by_morph_groups", True)
        opts = self._run_import()
        self.assertFalse(opts["split_meshes_by_morph_groups"])

    def test_dev_mode_preserves_split_meshes_by_morph_groups_true(self):
        settings.set("ui.general.development_mode", True)
        settings.set("import.model.split_meshes_by_morph_groups", True)
        opts = self._run_import()
        self.assertTrue(opts["split_meshes_by_morph_groups"])


if __name__ == "__main__":
    unittest.main()
