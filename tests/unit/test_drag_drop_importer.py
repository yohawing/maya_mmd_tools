"""Drag-and-drop importer の Maya 非依存契約を検証するテスト。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui import drag_drop_importer  # noqa: E402


class _FakeSettingsService:
    def build_pmx_import_options(self, custom_namespace=None):
        return {"kind": "pmx", "custom_namespace": custom_namespace}

    def build_vmd_import_options(self, target_model=None):
        return {"kind": "vmd", "target_model": target_model}


class _FakeSceneModelService:
    def __init__(self, *, parent_root=None, models=None):
        self.parent_root = parent_root
        self.models = list(models or [])

    def get_parent_mmd_root(self, _node):
        return self.parent_root

    def object_exists(self, node):
        return bool(node)

    def attribute_exists(self, _node, attr):
        return attr in ("mmd_model_name", "mmd_model_name_en")

    def list_mmd_models(self):
        return list(self.models)


class TestDragDropImporter(unittest.TestCase):
    def test_path_from_drop_url_decodes_file_url(self):
        if os.name == "nt":
            url = "file:///F:/MMD/%E3%83%A2%E3%83%87%E3%83%AB/model.pmx"
            self.assertEqual(
                drag_drop_importer.path_from_drop_url(url),
                os.path.normpath("F:/MMD/モデル/model.pmx"),
            )
        else:
            url = "file:///tmp/%E3%83%A2%E3%83%87%E3%83%AB/model.pmx"
            self.assertEqual(
                drag_drop_importer.path_from_drop_url(url),
                os.path.normpath("/tmp/モデル/model.pmx"),
            )

    def test_supported_mmd_files_filters_extensions_existing_files_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.pmx"
            motion = root / "motion.vmd"
            ignored = root / "readme.txt"
            model.write_text("", encoding="utf-8")
            motion.write_text("", encoding="utf-8")
            ignored.write_text("", encoding="utf-8")

            result = drag_drop_importer.supported_mmd_files(
                [str(model), str(ignored), str(motion), str(model), str(root / "missing.pmd")]
            )

        self.assertEqual(result, [str(model), str(motion)])

    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_files_imports_models_before_motions(self, _display_info):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.pmx"
            motion = root / "motion.vmd"
            model.write_text("", encoding="utf-8")
            motion.write_text("", encoding="utf-8")

            calls = []

            def importer(file_path, options=None):
                calls.append((Path(file_path).name, dict(options)))
                if file_path == str(model):
                    return "|model_root"
                return True

            result = drag_drop_importer.import_dropped_files(
                [str(motion), str(model)],
                importer=importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        self.assertEqual(calls[0], ("model.pmx", {"kind": "pmx", "custom_namespace": None, "profile": {}}))
        self.assertEqual(calls[1][0], "motion.vmd")
        self.assertEqual(calls[1][1]["kind"], "vmd")
        self.assertEqual(calls[1][1]["target_model"], "|model_root")
        self.assertEqual(calls[1][1]["pmx_path"], str(model))

    @patch.object(drag_drop_importer, "_selected_model_root", return_value="|selected_model")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vmd_uses_selected_model_when_model_is_loaded(
        self,
        _display_info,
        _selected_model_root,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            motion = Path(tmp) / "motion.vmd"
            motion.write_text("", encoding="utf-8")
            importer = MagicMock(return_value=True)

            result = drag_drop_importer.import_dropped_files(
                [str(motion)],
                importer=importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        importer.assert_called_once()
        self.assertEqual(importer.call_args.args[0], str(motion))
        self.assertEqual(importer.call_args.kwargs["options"], {"kind": "vmd", "target_model": "|selected_model"})

    @patch.object(drag_drop_importer, "_selected_model_root", return_value=None)
    @patch.object(drag_drop_importer, "_display_warning")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vmd_before_model_load_warns_and_fails(
        self,
        _display_info,
        display_warning,
        _selected_model_root,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            motion = Path(tmp) / "motion.vmd"
            motion.write_text("", encoding="utf-8")
            importer = MagicMock(return_value=True)

            result = drag_drop_importer.import_dropped_files(
                [str(motion)],
                importer=importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertFalse(result)
        importer.assert_not_called()
        display_warning.assert_called_once()
        self.assertIn("before dropping VMD", display_warning.call_args.args[0])

    def test_selected_model_root_uses_scene_model_service_parent_api(self):
        service = _FakeSceneModelService(parent_root="|model_root")
        with patch.object(drag_drop_importer, "SceneModelService", return_value=service):
            with patch.object(drag_drop_importer.cmds, "ls", return_value=["|model_root|joint1"]):
                self.assertEqual(drag_drop_importer._selected_model_root(), "|model_root")

    def test_selected_model_root_falls_back_to_first_mmd_model(self):
        service = _FakeSceneModelService(models=["|first_model", "|second_model"])
        with patch.object(drag_drop_importer, "SceneModelService", return_value=service):
            with patch.object(drag_drop_importer.cmds, "ls", return_value=[]):
                self.assertEqual(drag_drop_importer._selected_model_root(), "|first_model")


if __name__ == "__main__":
    unittest.main()
