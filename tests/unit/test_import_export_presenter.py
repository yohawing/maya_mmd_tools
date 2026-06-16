"""ImportExportPresenterのMaya非依存ロジックを検証するテスト。"""

import unittest
from unittest.mock import patch

from mmd_tools.core.settings import settings
from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter


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


class TestImportExportPresenter(unittest.TestCase):
    """ImportExportPresenterのimport options構築を検証する。"""

    def setUp(self):
        self._old_bake_mode = settings.get("import.rig.bake_mode", False)

    def tearDown(self):
        settings.set("import.rig.bake_mode", self._old_bake_mode)

    def test_import_file_passes_no_rig_options_when_bake_mode_enabled(self):
        settings.set("import.rig.bake_mode", True)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
            return_value="model_root",
        ) as mock_import:
            presenter.import_file()

        options = mock_import.call_args.kwargs["options"]
        self.assertFalse(options["setup_rig"])
        self.assertFalse(options["setup_bone_orientation"])

    def test_import_file_leaves_rig_options_unset_by_default(self):
        settings.set("import.rig.bake_mode", False)
        view = _FakeView()
        app_state = _FakeAppState()
        presenter = ImportExportPresenter(view, app_state)

        with patch(
            "mmd_tools.ui.presenters.import_export_presenter.import_mmd_file",
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


if __name__ == "__main__":
    unittest.main()
