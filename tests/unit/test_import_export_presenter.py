"""ImportExportPresenterのMaya非依存ロジックを検証するテスト。

ImportExportPresenter 自体は純粋な分岐ロジックだが、import 連鎖の途中で
``mmd_tools.io.mmd_importer`` → model importer 等が ``from maya import cmds`` を、
``..qt_compat`` が PySide6/PySide2 を要求するため、何もしないと import 時点で
これらが必要になる。そのため ``install_headless_ui_stubs()`` で maya と Qt を
スタブ化してから presenter を import する。これにより本テストは mayapy / Qt なしの
``nox -s ci_unit`` で実行できる。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.constants import (  # noqa: E402
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_TEXTURE_CACHE_PATH,
)
from mmd_tools.core.settings import settings  # noqa: E402
from mmd_tools.actions.export_model_action import ExportModelResult  # noqa: E402
from mmd_tools.actions.export_vmd_action import ExportVmdResult  # noqa: E402
from mmd_tools.actions.import_model_action import ImportModelResult  # noqa: E402
from mmd_tools.actions.import_vmd_action import ImportVmdResult  # noqa: E402
from mmd_tools.ui.presenters.import_export_presenter import (  # noqa: E402
    ImportExportPresenter,
)
from mmd_tools.ui.translations.translator import UITranslator  # noqa: E402

UITranslator.instance().set_language("en")


class _FakeSignal:
    def connect(self, _callback):
        pass


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()


class _RecordingSignal:
    def __init__(self):
        self.connected = []

    def connect(self, callback):
        self.connected.append(callback)


class _RecordingButton:
    def __init__(self):
        self.clicked = _RecordingSignal()


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
        self.export_history = []
        self.model_items = None
        self.restore_selection = None

    def get_custom_namespace(self):
        return None

    def refresh_model_list(self):
        pass

    def set_target_model_items(self, model_items, restore_selection=False):
        self.model_items = list(model_items)
        self.restore_selection = restore_selection

    def add_import_path_to_history(self, _path):
        pass

    def add_vmd_path_to_history(self, _path):
        pass

    def add_export_path_to_history(self, path):
        self.export_history.append(path)


class _FakeAppState:
    def __init__(self, scene_model_service=None):
        self.current_model_root = None
        self.statuses = []
        self.progress = []
        self.scene_model_service = scene_model_service

    def emit_status(self, message):
        self.statuses.append(message)

    def emit_progress(self, value):
        self.progress.append(value)

    def refresh_model_list(self):
        pass


class _FakeSceneModelService:
    def __init__(self, models=None, display_names=None, error=None):
        self.models = models or []
        self.display_names = display_names or {}
        self.error = error

    def list_mmd_models(self):
        if self.error:
            raise self.error
        return list(self.models)

    def get_model_display_name(self, model_root):
        return self.display_names.get(model_root, model_root)


class _FakeMayaAdapter:
    def __init__(self, file_nodes=None, existing_attrs=None, connections=None, relatives=None, existing=None):
        self.file_nodes = file_nodes or []
        self.existing_attrs = existing_attrs or {}
        self.connections = connections or {}
        self.relatives = relatives or {}
        self.existing = set(existing or [])
        self.attribute_exists_calls = []
        self.list_connections_calls = []
        self.ls_calls = []

    def ls(self, *args, **kwargs):
        self.ls_calls.append((args, kwargs))
        if kwargs.get("materials") and args:
            return list(args[0])
        return list(self.file_nodes)

    def attribute_exists(self, attr, node):
        self.attribute_exists_calls.append((attr, node))
        return attr in self.existing_attrs.get(node, set())

    def list_connections(self, node, **kwargs):
        self.list_connections_calls.append((node, kwargs))
        if isinstance(node, list):
            merged = []
            for item in node:
                merged.extend(self.connections.get(item, []))
            return merged
        return list(self.connections.get(node, []))

    def list_relatives(self, node, **kwargs):
        return list(self.relatives.get(node, []))

    def object_exists(self, node):
        return node in self.existing


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


class _RecordingImportVmdAction:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class _FailingImportVmdAction:
    def execute(self, _request):
        raise AssertionError("vmd action must not be used")


class _RecordingExportModelAction:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class _FailingExportModelAction:
    def execute(self, _request):
        raise AssertionError("model export action must not be used")


class _RecordingExportVmdAction:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.result


class _FailingExportVmdAction:
    def execute(self, _request):
        raise AssertionError("vmd export action must not be used")


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

    def test_import_file_leaves_rig_options_unset_when_bake_mode_enabled(self):
        # bake_mode only controls VMD animation import; model import still builds rig by default.
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
        self.assertNotIn("setup_rig", options)
        self.assertNotIn("setup_bone_orientation", options)

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
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_file()

        self.assertEqual(len(action.requests), 1)
        options = action.requests[0].options
        self.assertEqual(options["target_model"], "model_root")

    def test_import_file_passes_profile_and_shows_texture_issue_dialog(self):
        settings.set("import.model.show_texture_issue_dialog", True)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        issue = {"file_node": "file1", "material": "mat1", "reason": "non_ascii_path"}

        def fake_import(_path, options=None, progress_callback=None):
            self.assertEqual(progress_callback, app_state.emit_progress)
            options["profile"]["texture_issues"] = [issue]
            return "model_root"

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            side_effect=fake_import,
        ) as mock_import, patch(
            "mmd_tools.ui.texture_issue_dialog.TextureIssueDialog",
        ) as mock_dialog, patch.object(
            presenter,
            "_show_import_partial_warning",
        ) as mock_generic:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertIn("profile", options)
        # Texture-only partial: one texture repair modal, no generic partial modal.
        mock_dialog.assert_called_once_with([issue], model_path="model.pmx", app_state=app_state, parent=view)
        mock_dialog.return_value.exec.assert_called_once()
        mock_generic.assert_not_called()
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))
        self.assertTrue(any("warning" in s.lower() for s in app_state.statuses))

    def test_import_file_skips_texture_issue_dialog_when_setting_disabled(self):
        settings.set("import.model.show_texture_issue_dialog", False)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        def fake_import(_path, options=None, progress_callback=None):
            self.assertEqual(progress_callback, app_state.emit_progress)
            options["profile"]["texture_issues"] = [{"file_node": "file1"}]
            return "model_root"

        with patch(
            "mmd_tools.actions.import_model_action.import_mmd_file",
            side_effect=fake_import,
        ), patch("mmd_tools.ui.texture_issue_dialog.TextureIssueDialog") as mock_dialog, patch.object(
            presenter,
            "_show_import_partial_warning",
        ) as mock_generic:
            presenter.import_file()

        mock_dialog.assert_not_called()
        # Texture dialog setting off: still no generic modal (status-only partial).
        mock_generic.assert_not_called()

    def test_refresh_model_list_uses_scene_model_service_display_names(self):
        view = _FakeView()
        scene_service = _FakeSceneModelService(
            models=["ModelA:miku_root"],
            display_names={"ModelA:miku_root": "Miku"},
        )
        app_state = _FakeAppState(scene_model_service=scene_service)

        ImportExportPresenter(view, app_state)

        self.assertEqual(view.model_items, [("ModelA:miku_root", "Miku")])
        self.assertTrue(view.restore_selection)

    def test_refresh_model_list_handles_scene_service_failure(self):
        view = _FakeView()
        app_state = _FakeAppState(scene_model_service=_FakeSceneModelService(error=RuntimeError("boom")))

        presenter = ImportExportPresenter(view, app_state)
        presenter.refresh_model_list()

        self.assertEqual(view.model_items, [])

    @patch("mmd_tools.ui.presenters.import_export_presenter.maya_material_utils")
    def test_fix_texture_paths_repairs_current_model_and_reports_counts(self, mock_material_utils):
        view = _FakeView()
        app_state = _FakeAppState()
        app_state.current_model_root = "root"
        maya_adapter = _FakeMayaAdapter(
            existing={"root"},
            relatives={"root": ["shape"]},
            connections={
                "shape": ["sg"],
                "sg": ["mat"],
                "mat": ["file1", "file2"],
                "root.message": ["disconnected_file"],
            },
            existing_attrs={
                "file1": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
                "file2": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
                "disconnected_file": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
            },
        )
        presenter = ImportExportPresenter(view, app_state, maya_adapter=maya_adapter)
        mock_material_utils.resolve_scene_mmd_textures.return_value = [
            SimpleNamespace(status="resolved", rebind_status="rebound"),
            SimpleNamespace(status="resolved", rebind_status="failed"),
            SimpleNamespace(status="unrecoverable"),
        ]

        result = presenter.fix_texture_paths()

        mock_material_utils.resolve_scene_mmd_textures.assert_called_once_with(
            file_nodes=["disconnected_file", "file1", "file2"]
        )
        self.assertEqual(result, {"resolved": 1, "unresolved": 2})
        self.assertTrue(any("1" in status for status in app_state.statuses))

    def test_fix_texture_paths_no_current_model_prompts_for_selection(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        result = presenter.fix_texture_paths()

        self.assertEqual(result, {"resolved": 0, "unresolved": 0})
        expected_status = UITranslator.instance().translate("status_select_model", "texture_issues")
        self.assertIn(expected_status, app_state.statuses)

    @patch("mmd_tools.ui.presenters.import_export_presenter.maya_material_utils")
    def test_collect_scene_texture_issues_filters_and_converts_file_nodes(self, mock_maya_material_utils):
        view = _FakeView()
        app_state = _FakeAppState()
        maya_adapter = _FakeMayaAdapter(
            file_nodes=["non_mmd_file", "ok_file", "bad_file", "lost_file"],
            existing_attrs={
                "ok_file": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
                "bad_file": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
                "lost_file": {ATTR_MMD_ORIGINAL_TEXTURE_PATH},
            },
            connections={
                "bad_file": ["mat1"],
                "lost_file": [],
            },
        )
        presenter = ImportExportPresenter(view, app_state, maya_adapter=maya_adapter)
        ok = MagicMock(
            status="ok",
            reason="",
            original_path="ok.png",
            file_texture_path="ok.png",
            source_path="ok.png",
        )
        resolvable = MagicMock(
            status="resolvable",
            reason="non_ascii_path",
            original_path="モデル/髪.png",
            file_texture_path="モデル/髪.png",
            source_path="F:/model/モデル/髪.png",
        )
        unrecoverable = MagicMock(
            status="unrecoverable",
            reason="missing_file",
            original_path="missing.png",
            file_texture_path="missing.png",
            source_path=None,
        )
        mock_maya_material_utils.classify_mmd_texture_file_node.side_effect = {
            "ok_file": ok,
            "bad_file": resolvable,
            "lost_file": unrecoverable,
        }.get

        issues = presenter._collect_scene_texture_issues()

        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["file_node"], "bad_file")
        self.assertEqual(issues[0]["material"], "mat1")
        self.assertEqual(issues[0]["reason"], "non_ascii_path")
        self.assertTrue(issues[0]["resolvable"])
        self.assertEqual(issues[0]["source_path"], "F:/model/モデル/髪.png")
        self.assertEqual(issues[1]["file_node"], "lost_file")
        self.assertEqual(issues[1]["material"], "lost_file")
        self.assertEqual(issues[1]["reason"], "missing_file")
        self.assertFalse(issues[1]["resolvable"])
        self.assertEqual(maya_adapter.ls_calls, [((), {"type": "file"})])
        self.assertIn((ATTR_MMD_ORIGINAL_TEXTURE_PATH, "non_mmd_file"), maya_adapter.attribute_exists_calls)
        self.assertEqual(
            maya_adapter.list_connections_calls,
            [("bad_file", {"destination": True}), ("lost_file", {"destination": True})],
        )

    def test_material_name_for_file_node_uses_injected_adapter_connections(self):
        view = _FakeView()
        app_state = _FakeAppState()
        maya_adapter = _FakeMayaAdapter(connections={"file1": ["mat1"]})
        presenter = ImportExportPresenter(view, app_state, maya_adapter=maya_adapter)

        self.assertEqual(presenter._material_name_for_file_node("file1"), "mat1")
        self.assertEqual(maya_adapter.list_connections_calls, [("file1", {"destination": True})])

    @patch("mmd_tools.ui.presenters.import_export_presenter.maya_attribute_utils.get_attribute")
    def test_texture_resolution_to_issue_prefers_resolution_cache_path(self, mock_get_attribute):
        view = _FakeView()
        app_state = _FakeAppState()
        maya_adapter = _FakeMayaAdapter(
            existing_attrs={"file1": {ATTR_MMD_TEXTURE_CACHE_PATH}},
            connections={"file1": ["mat1"]},
        )
        presenter = ImportExportPresenter(view, app_state, maya_adapter=maya_adapter)
        resolution = MagicMock(
            status="resolvable",
            reason="non_ascii_path",
            original_path="original.png",
            file_texture_path="fileTexture.png",
            cache_path="cache.png",
            source_path="source.png",
        )

        issue = presenter._texture_resolution_to_issue("file1", resolution)

        self.assertEqual(issue["current_path"], "cache.png")
        self.assertEqual(issue["material"], "mat1")
        self.assertNotIn((ATTR_MMD_TEXTURE_CACHE_PATH, "file1"), maya_adapter.attribute_exists_calls)
        mock_get_attribute.assert_not_called()

    def test_import_file_model_branch_uses_injected_action_and_updates_ui_state(self):
        recorded_history = []

        class _RecordingView(_FakeView):
            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        app_state = _FakeAppState()
        action = _RecordingImportModelAction(
            ImportModelResult(root_node="model_root", succeeded=True, outcome="success")
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        presenter.import_file()

        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].file_path, "model.pmx")
        self.assertIn("profile", action.requests[0].options)
        self.assertFalse(action.requests[0].create_new_scene)
        self.assertEqual(action.requests[0].progress_callback, app_state.emit_progress)
        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertIn("Import complete: model_root", app_state.statuses)
        self.assertIn(100, app_state.progress)
        self.assertEqual(view.model_items, [])
        self.assertEqual(recorded_history, ["model.pmx"])

    def test_import_file_model_partial_retains_root_and_one_warning_outcome(self):
        recorded_history = []
        warnings = [{"code": "node_type_unavailable", "reason": "node_type_unavailable"}]

        class _RecordingView(_FakeView):
            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        app_state = _FakeAppState()
        app_state.current_model_root = "previous_root"
        action = _RecordingImportModelAction(
            ImportModelResult(
                root_node="model_root",
                succeeded=True,
                warnings=warnings,
                outcome="partial",
            )
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        with patch.object(presenter, "_present_import_partial_outcome", return_value="partial-msg") as mock_partial:
            presenter.import_file()

        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertEqual(recorded_history, ["model.pmx"])
        self.assertIn(100, app_state.progress)
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))
        mock_partial.assert_called_once_with(
            warnings,
            file_path="model.pmx",
            root_node="model_root",
            kind="model",
            show_dialog=True,
        )

    def test_import_file_model_partial_texture_only_shows_texture_dialog_only(self):
        settings.set("import.model.show_texture_issue_dialog", True)
        issue = {"file_node": "file1", "material": "mat1", "reason": "non_ascii_path"}
        view = _FakeView()
        app_state = _FakeAppState()

        class _TexturePartialAction:
            def __init__(self):
                self.requests = []

            def execute(self, request):
                self.requests.append(request)
                # Mirror real importers: write texture issues into the shared profile.
                request.options.setdefault("profile", {})["texture_issues"] = [issue]
                return ImportModelResult(
                    root_node="model_root",
                    succeeded=True,
                    warnings=[issue],
                    outcome="partial",
                )

        action = _TexturePartialAction()
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        with patch.object(presenter, "_show_import_partial_warning") as mock_generic, patch.object(
            presenter,
            "_show_texture_issue_dialog",
        ) as mock_texture:
            presenter.import_file()

        mock_generic.assert_not_called()
        mock_texture.assert_called_once_with([issue], model_path="model.pmx")
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))
        self.assertTrue(any("warning" in s.lower() for s in app_state.statuses))

    def test_import_file_model_partial_mixed_shows_generic_and_texture_dialogs(self):
        settings.set("import.model.show_texture_issue_dialog", True)
        non_texture = {"code": "node_type_unavailable", "reason": "node_type_unavailable"}
        texture = {"file_node": "file1", "material": "mat1", "reason": "non_ascii_path"}
        view = _FakeView()
        app_state = _FakeAppState()

        class _MixedPartialAction:
            def __init__(self):
                self.requests = []

            def execute(self, request):
                self.requests.append(request)
                # Mixed warnings need the generic summary and the actionable
                # texture repair dialog.
                profile = request.options.setdefault("profile", {})
                profile["texture_issues"] = [texture]
                profile["bone_morph_runtime"] = {"warnings": [non_texture]}
                return ImportModelResult(
                    root_node="model_root",
                    succeeded=True,
                    warnings=[non_texture, texture],
                    outcome="partial",
                )

        action = _MixedPartialAction()
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        with patch.object(presenter, "_show_import_partial_warning") as mock_generic, patch.object(
            presenter,
            "_show_texture_issue_dialog",
        ) as mock_texture:
            presenter.import_file()

        mock_generic.assert_called_once()
        mock_texture.assert_called_once_with([texture], model_path="model.pmx")
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))

    def test_import_file_model_partial_node_type_unavailable_suppresses_texture_dialog(self):
        settings.set("import.model.show_texture_issue_dialog", True)
        non_texture = {"code": "node_type_unavailable", "reason": "node_type_unavailable"}
        view = _FakeView()
        app_state = _FakeAppState()

        class _NonTexturePartialAction:
            def __init__(self):
                self.requests = []

            def execute(self, request):
                self.requests.append(request)
                request.options.setdefault("profile", {})["texture_issues"] = [{"file_node": "file1"}]
                return ImportModelResult(
                    root_node="model_root",
                    succeeded=True,
                    warnings=[non_texture],
                    outcome="partial",
                )

        action = _NonTexturePartialAction()
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        with patch.object(presenter, "_show_import_partial_warning") as mock_generic, patch.object(
            presenter,
            "_show_texture_issue_dialog",
        ) as mock_texture:
            presenter.import_file()

        mock_generic.assert_called_once()
        mock_texture.assert_not_called()

    def test_import_file_model_fatal_does_not_update_model_or_history(self):
        recorded_history = []

        class _RecordingView(_FakeView):
            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        app_state = _FakeAppState()
        app_state.current_model_root = "previous_root"
        action = _RecordingImportModelAction(
            ImportModelResult(root_node=None, succeeded=False, outcome="fatal")
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        with patch.object(presenter, "_present_import_partial_outcome") as mock_partial:
            presenter.import_file()

        self.assertEqual(app_state.current_model_root, "previous_root")
        self.assertEqual(recorded_history, [])
        self.assertIn("Import failed", app_state.statuses)
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))
        self.assertIn(0, app_state.progress)
        mock_partial.assert_not_called()

    def test_import_file_model_fatal_error_does_not_emit_success(self):
        recorded_history = []

        class _RecordingView(_FakeView):
            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        app_state = _FakeAppState()
        app_state.current_model_root = "previous_root"
        action = _RecordingImportModelAction(
            ImportModelResult(error=RuntimeError("boom"), outcome="fatal")
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=action,
            import_vmd_action=_FailingImportVmdAction(),
        )

        presenter.import_file()

        self.assertEqual(app_state.current_model_root, "previous_root")
        self.assertEqual(recorded_history, [])
        self.assertTrue(any("Import error: boom" in s for s in app_state.statuses))
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))

    def test_import_file_vmd_partial_uses_one_warning_outcome(self):
        recorded_history = []
        warnings = [{"message": "runtime fallback"}]

        class _RecordingView(_FakeView):
            def add_import_path_to_history(self, path):
                recorded_history.append(path)

        view = _RecordingView()
        view.import_path_edit = _FakeLineEdit("motion.vmd")
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(
            ImportVmdResult(
                root_node=True,
                succeeded=True,
                warnings=warnings,
                outcome="partial",
            )
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=_FailingImportModelAction(),
            import_vmd_action=action,
        )

        with patch.object(presenter, "_present_import_partial_outcome", return_value="partial-vmd") as mock_partial:
            presenter.import_file()

        self.assertEqual(recorded_history, ["motion.vmd"])
        self.assertEqual(app_state.current_model_root, True)
        self.assertFalse(any(s.startswith("Import complete:") for s in app_state.statuses))
        mock_partial.assert_called_once_with(
            warnings,
            file_path="motion.vmd",
            root_node=True,
            kind="vmd",
            show_dialog=True,
        )

    def test_present_import_partial_outcome_emits_status_and_one_dialog(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)
        warnings = [{"code": "a"}, {"code": "b"}]

        with patch.object(presenter, "_show_import_partial_warning") as mock_dialog:
            message = presenter._present_import_partial_outcome(
                warnings,
                file_path="model.pmx",
                root_node="model_root",
                kind="model",
            )

        self.assertIn("warnings", message.lower())
        self.assertIn("model_root", message)
        self.assertIn(message, app_state.statuses)
        mock_dialog.assert_called_once()
        title, dialog_message, dialog_warnings = mock_dialog.call_args[0]
        self.assertEqual(dialog_message, message)
        self.assertIs(dialog_warnings, warnings)
        self.assertTrue(title)

    def test_present_import_partial_outcome_can_suppress_dialog(self):
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch.object(presenter, "_show_import_partial_warning") as mock_dialog:
            message = presenter._present_import_partial_outcome(
                [{"file_node": "file1"}],
                file_path="model.pmx",
                root_node="model_root",
                kind="model",
                show_dialog=False,
            )

        self.assertIn(message, app_state.statuses)
        mock_dialog.assert_not_called()

    def test_import_file_vmd_branch_does_not_use_model_action(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("motion.vmd")
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(
            view,
            app_state,
            import_model_action=_FailingImportModelAction(),
            import_vmd_action=action,
        )

        presenter.import_file()

        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].file_path, "motion.vmd")
        self.assertEqual(action.requests[0].progress_callback, app_state.emit_progress)

    def test_import_file_vmd_branch_passes_create_new_scene_to_action(self):
        view = _FakeView()
        view.import_path_edit = _FakeLineEdit("motion.vmd")
        view.new_file_check = _FakeCheckBox(True)
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_file()

        self.assertEqual(len(action.requests), 1)
        self.assertTrue(action.requests[0].create_new_scene)
        self.assertEqual(action.requests[0].progress_callback, app_state.emit_progress)

    def test_import_vmd_auto_detect_uses_current_model_root(self):
        view = _FakeView()
        app_state = _FakeAppState()
        app_state.current_model_root = "model_root"
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(len(action.requests), 1)
        options = action.requests[0].options
        self.assertEqual(options["target_model"], "model_root")

    def test_import_vmd_explicit_target_overrides_current_model_root(self):
        view = _FakeView()
        view.target_model_combo = _FakeComboBox(1, {1: "explicit_model_root"})
        app_state = _FakeAppState()
        app_state.current_model_root = "current_model_root"
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(len(action.requests), 1)
        options = action.requests[0].options
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
            "mmd_tools.actions.import_vmd_action.import_mmd_file",
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
            "motion_scale",
            "clear_existing_motion",
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
        with patch("mmd_tools.ui.presenters.import_export_presenter.logger") as mock_logger:
            self.assertEqual(presenter._get_vmd_target_model(), "current_model")
        detail = "Auto-selected current model root for VMD import: current_model"
        self.assertIn(detail, [call[0][0] for call in mock_logger.debug.call_args_list])
        self.assertNotIn(detail, [call[0][0] for call in mock_logger.info.call_args_list])

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
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(action.requests, [])
        self.assertIn("Please enter a VMD file path", app_state.statuses)

    def test_import_vmd_success_adds_history(self):
        recorded = []

        class _RecordingView(_FakeView):
            def add_vmd_path_to_history(self, path):
                recorded.append(path)

        view = _RecordingView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].file_path, "dance.vmd")
        self.assertFalse(action.requests[0].create_new_scene)
        self.assertEqual(action.requests[0].progress_callback, app_state.emit_progress)
        self.assertEqual(recorded, ["dance.vmd"])
        self.assertIn(100, app_state.progress)

    def test_import_vmd_keeps_operation_boundaries_info_and_target_route_debug(self):
        view = _FakeView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        app_state.current_model_root = "current_model"
        action = _RecordingImportVmdAction(ImportVmdResult(root_node=True, succeeded=True))
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        with patch("mmd_tools.ui.presenters.import_export_presenter.logger") as mock_logger:
            presenter.import_vmd_file()

        debug_messages = [call[0][0] for call in mock_logger.debug.call_args_list]
        info_messages = [call[0][0] for call in mock_logger.info.call_args_list]
        for detail in (
            "Auto-selected current model root for VMD import: current_model",
            "Target model: current_model",
        ):
            self.assertIn(detail, debug_messages)
            self.assertNotIn(detail, info_messages)
        self.assertIn("Importing VMD file: dance.vmd", info_messages)
        self.assertIn("VMD import successful.", info_messages)

    def test_import_vmd_failure_result_emits_failure_and_skips_history(self):
        recorded = []

        class _RecordingView(_FakeView):
            def add_vmd_path_to_history(self, path):
                recorded.append(path)

        view = _RecordingView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(
            ImportVmdResult(root_node=None, succeeded=False, outcome="fatal")
        )
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(len(action.requests), 1)
        self.assertEqual(recorded, [])
        self.assertIn("VMD import failed", app_state.statuses)
        self.assertIn(0, app_state.progress)

    def test_import_vmd_error_result_is_reported(self):
        view = _FakeView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        action = _RecordingImportVmdAction(
            ImportVmdResult(error=RuntimeError("boom"), outcome="fatal")
        )
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        presenter.import_vmd_file()

        self.assertEqual(len(action.requests), 1)
        self.assertTrue(any("VMD import error: boom" in s for s in app_state.statuses))
        self.assertIn(0, app_state.progress)

    def test_import_vmd_partial_retains_history_and_one_warning_outcome(self):
        recorded = []
        warnings = [{"message": "curve skipped"}]

        class _RecordingView(_FakeView):
            def add_vmd_path_to_history(self, path):
                recorded.append(path)

        view = _RecordingView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        app_state.current_model_root = "model_root"
        action = _RecordingImportVmdAction(
            ImportVmdResult(
                root_node=True,
                succeeded=True,
                warnings=warnings,
                outcome="partial",
            )
        )
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        with patch.object(presenter, "_present_import_partial_outcome", return_value="partial-vmd") as mock_partial:
            presenter.import_vmd_file()

        self.assertEqual(recorded, ["dance.vmd"])
        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertIn(100, app_state.progress)
        self.assertFalse(any("VMD import complete" in s for s in app_state.statuses))
        mock_partial.assert_called_once_with(
            warnings,
            file_path="dance.vmd",
            root_node=True,
            kind="vmd",
        )

    def test_import_vmd_fatal_leaves_current_model_unchanged(self):
        recorded = []

        class _RecordingView(_FakeView):
            def add_vmd_path_to_history(self, path):
                recorded.append(path)

        view = _RecordingView()
        view.vmd_path_edit = _FakeLineEdit("dance.vmd")
        app_state = _FakeAppState()
        app_state.current_model_root = "model_root"
        action = _RecordingImportVmdAction(
            ImportVmdResult(root_node=None, succeeded=False, error=RuntimeError("bad"), outcome="fatal")
        )
        presenter = ImportExportPresenter(view, app_state, import_vmd_action=action)

        with patch.object(presenter, "_present_import_partial_outcome") as mock_partial:
            presenter.import_vmd_file()

        self.assertEqual(app_state.current_model_root, "model_root")
        self.assertEqual(recorded, [])
        self.assertTrue(any("VMD import error: bad" in s for s in app_state.statuses))
        self.assertFalse(any("VMD import complete" in s for s in app_state.statuses))
        mock_partial.assert_not_called()


class TestExportFile(unittest.TestCase):
    """export_file の PMX/PMD/VMD action 分岐を検証する。"""

    _KEYS_TO_PRESERVE = (
        "export.general.export_format",
        "export.general.apply_scale",
        "ui.general.development_mode",
    )

    def setUp(self):
        self._saved = {k: settings.get(k) for k in self._KEYS_TO_PRESERVE}
        # Export is develop-mode only.
        settings.set("ui.general.development_mode", True)

    def tearDown(self):
        for k, v in self._saved.items():
            settings.set(k, v)

    def test_export_blocked_in_normal_mode(self):
        settings.set("ui.general.development_mode", False)
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.pmx")
        app_state = _FakeAppState()
        action = _RecordingExportModelAction(ExportModelResult(exported_path="out.pmx", succeeded=True))
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=action,
            export_vmd_action=_FailingExportVmdAction(),
        )

        presenter.export_file()

        self.assertEqual(action.requests, [])
        self.assertTrue(any("Development Mode" in s or "開発モード" in s for s in app_state.statuses))

    def test_empty_path_guard(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("")
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=_FailingExportModelAction(),
            export_vmd_action=_FailingExportVmdAction(),
        )
        presenter.export_file()
        self.assertIn("Please enter a file path", app_state.statuses)

    def test_reports_model_action_status_message(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.pmx")
        app_state = _FakeAppState()
        action = _RecordingExportModelAction(
            ExportModelResult(
                status_message="Export failed: Model export requires model_data, target_mesh, or a selected mesh"
            )
        )
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=action,
            export_vmd_action=_FailingExportVmdAction(),
        )
        presenter.export_file()
        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].file_path, "out.pmx")
        self.assertTrue(
            any("Model export requires" in s for s in app_state.statuses)
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

    def test_export_file_reports_model_action_error(self):
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.pmx")
        app_state = _FakeAppState()
        action = _RecordingExportModelAction(ExportModelResult(error=ValueError("boom")))
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=action,
            export_vmd_action=_FailingExportVmdAction(),
        )

        presenter.export_file()

        self.assertEqual(len(action.requests), 1)
        self.assertTrue(any("Export error: boom" in s for s in app_state.statuses))

    def test_export_file_passes_built_options_to_injected_action(self):
        settings.set("export.general.export_format", "pmx")
        settings.set("export.general.apply_scale", False)
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("  out.pmx  ")
        app_state = _FakeAppState()
        action = _RecordingExportModelAction(ExportModelResult(exported_path="out.pmx", succeeded=True))
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=action,
            export_vmd_action=_FailingExportVmdAction(),
        )

        presenter.export_file()

        self.assertEqual(len(action.requests), 1)
        request = action.requests[0]
        self.assertEqual(request.file_path, "out.pmx")
        self.assertEqual(
            request.options,
            {
                "file_path": "out.pmx",
                "export_format": "pmx",
                "apply_scale": False,
            },
        )
        self.assertIn("Export complete: out.pmx", app_state.statuses)
        self.assertEqual(view.export_history, ["out.pmx"])

    def test_export_file_uses_vmd_action_when_format_is_vmd(self):
        settings.set("export.general.export_format", "vmd")
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("  out.vmd  ")
        app_state = _FakeAppState()
        action = _RecordingExportVmdAction(ExportVmdResult(exported_path="out.vmd", succeeded=True))
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=_FailingExportModelAction(),
            export_vmd_action=action,
        )

        presenter.export_file()

        self.assertEqual(len(action.requests), 1)
        request = action.requests[0]
        self.assertEqual(request.file_path, "out.vmd")
        self.assertEqual(
            request.options,
            {
                "file_path": "out.vmd",
                "export_format": "vmd",
                "apply_scale": True,
            },
        )
        self.assertIn("Export complete: out.vmd", app_state.statuses)

    def test_export_file_reports_vmd_action_error(self):
        settings.set("export.general.export_format", "vmd")
        view = _FakeView()
        view.export_path_edit = _FakeLineEdit("out.vmd")
        app_state = _FakeAppState()
        action = _RecordingExportVmdAction(ExportVmdResult(error=RuntimeError("boom")))
        presenter = ImportExportPresenter(
            view,
            app_state,
            export_model_action=_FailingExportModelAction(),
            export_vmd_action=action,
        )

        presenter.export_file()

        self.assertEqual(len(action.requests), 1)
        self.assertTrue(any("Export error: boom" in s for s in app_state.statuses))


class TestDevModeBehaviorGating(unittest.TestCase):
    """通常モードが dev-only 設定を強制デフォルトに上書きすることを検証する。"""

    _KEYS_TO_PRESERVE = (
        "ui.general.development_mode",
        "import.general.scale_factor",
        "import.model.import_models",
        "import.physics.import_physics",
        "import.model.separate_meshes_by_material",
        "import.model.auto_classify_transparency",
        "import.model.auto_resolve_textures",
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
            "mmd_tools.actions.import_vmd_action.import_mmd_file"
            if path.lower().endswith(".vmd")
            else "mmd_tools.actions.import_model_action.import_mmd_file"
        )
        with patch(
            mock_target,
            return_value="root",
        ) as mock_import:
            presenter.import_file()
        return mock_import.call_args.kwargs["options"]

    def test_normal_mode_forces_import_scale_to_default(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.general.scale_factor", 5.0)
        opts = self._run_import()
        self.assertEqual(opts["scale"], 1.0)
        # Policy must not overwrite the persisted development scale.
        self.assertEqual(settings.get("import.general.scale_factor"), 5.0)

    def test_dev_mode_preserves_import_scale(self):
        settings.set("ui.general.development_mode", True)
        settings.set("import.general.scale_factor", 5.0)
        opts = self._run_import()
        self.assertEqual(opts["scale"], 5.0)

    def test_normal_mode_forces_pmd_import_scale_to_default(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.general.scale_factor", 5.0)
        opts = self._run_import(path="model.pmd")
        self.assertEqual(opts["scale"], 1.0)
        self.assertEqual(settings.get("import.general.scale_factor"), 5.0)

    def test_normal_mode_forces_import_models_true(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.import_models", False)
        opts = self._run_import()
        self.assertTrue(opts["import_models"])

    def test_normal_mode_forces_separate_meshes_false(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.separate_meshes_by_material", True)
        opts = self._run_import()
        self.assertFalse(opts["separate_meshes_by_material"])

    def test_normal_mode_bake_mode_does_not_disable_model_rig_options(self):
        # bake_mode is an animation-import setting and must not suppress model rig creation.
        settings.set("ui.general.development_mode", False)
        settings.set("import.rig.bake_mode", True)
        opts = self._run_import()
        self.assertNotIn("setup_rig", opts)
        self.assertNotIn("setup_bone_orientation", opts)

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

    def test_normal_mode_preserves_auto_resolve_textures_option(self):
        settings.set("ui.general.development_mode", False)
        settings.set("import.model.auto_resolve_textures", False)
        opts = self._run_import()
        self.assertFalse(opts["auto_resolve_textures"])

if __name__ == "__main__":
    unittest.main()
