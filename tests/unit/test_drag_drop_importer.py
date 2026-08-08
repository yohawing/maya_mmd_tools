"""Drag-and-drop importer の Maya 非依存契約を検証するテスト。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui import drag_drop_importer, qt_compat  # noqa: E402


class _FakeSettingsService:
    def __init__(self, pmx_options=None, vmd_options=None):
        self.pmx_options = dict(pmx_options or {"kind": "pmx"})
        self.vmd_options = dict(vmd_options or {"kind": "vmd"})
        self.pmx_calls = []
        self.vmd_calls = []

    def build_pmx_import_options(self, custom_namespace=None):
        self.pmx_calls.append(custom_namespace)
        options = dict(self.pmx_options)
        options["custom_namespace"] = custom_namespace
        return options

    def build_vmd_import_options(self, target_model=None):
        self.vmd_calls.append(target_model)
        options = dict(self.vmd_options)
        options["target_model"] = target_model
        return options


class _FakeSceneModelService:
    def __init__(self, *, parent_root=None, models=None, attrs=None):
        self.parent_root = parent_root
        self.models = list(models or [])
        self.attrs = dict(attrs or {})

    def get_parent_mmd_root(self, _node):
        return self.parent_root

    def object_exists(self, node):
        return bool(node)

    def attribute_exists(self, _node, attr):
        return attr in ("mmd_model_name", "mmd_model_name_en")

    def list_mmd_models(self):
        return list(self.models)

    def get_attr_safe(self, node, attr, default=None):
        return self.attrs.get(node, {}).get(attr, default)


class _RecordingReadmeAdapter:
    def __init__(self):
        self.calls = []

    def show(self, readme, *, model_path="", parent=None):
        self.calls.append((readme, model_path, parent))
        return True


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
            pose = root / "pose.vpd"
            ignored = root / "readme.txt"
            model.write_text("", encoding="utf-8")
            motion.write_text("", encoding="utf-8")
            pose.write_text("", encoding="utf-8")
            ignored.write_text("", encoding="utf-8")

            result = drag_drop_importer.supported_mmd_files(
                [str(model), str(ignored), str(motion), str(pose), str(model), str(root / "missing.pmd")]
            )

        self.assertEqual(result, [str(model), str(motion), str(pose)])

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
        self.assertEqual(
            calls[0],
            ("model.pmx", {"kind": "pmx", "custom_namespace": None, "profile": {}}),
        )
        self.assertEqual(calls[1][0], "motion.vmd")
        self.assertEqual(calls[1][1]["kind"], "vmd")
        self.assertEqual(calls[1][1]["target_model"], "|model_root")
        self.assertEqual(calls[1][1]["pmx_path"], str(model))

    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_model_uses_settings_service_policy_scale(self, _display_info):
        """D&D モデル import は SettingsService.build_pmx_import_options の scale を使う。"""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.pmx"
            model.write_text("", encoding="utf-8")
            calls = []

            def importer(file_path, options=None):
                calls.append(dict(options or {}))
                return "|model_root"

            settings_service = _FakeSettingsService(
                pmx_options={"kind": "pmx", "scale": 1.0},
            )
            result = drag_drop_importer.import_dropped_files(
                [str(model)],
                importer=importer,
                settings_service=settings_service,
            )

        self.assertTrue(result)
        self.assertEqual(settings_service.pmx_calls, [None])
        self.assertEqual(calls[0]["scale"], 1.0)
        self.assertEqual(calls[0]["kind"], "pmx")

    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_model_forwards_dev_scale_from_settings_service(self, _display_info):
        """D&D は build_pmx_import_options が返す dev scale を改変せず渡す。"""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.pmd"
            model.write_text("", encoding="utf-8")
            calls = []

            def importer(file_path, options=None):
                calls.append((Path(file_path).suffix.lower(), dict(options or {})))
                return "|model_root"

            settings_service = _FakeSettingsService(
                pmx_options={"kind": "pmx", "scale": 2.5},
            )
            result = drag_drop_importer.import_dropped_files(
                [str(model)],
                importer=importer,
                settings_service=settings_service,
            )

        self.assertTrue(result)
        self.assertEqual(calls[0][0], ".pmd")
        self.assertEqual(calls[0][1]["scale"], 2.5)

    @patch.object(drag_drop_importer, "_display_error")
    @patch.object(drag_drop_importer, "_display_info")
    def test_native_vp2_failure_is_reported_without_success(self, _display_info, display_error):
        """A failed native VP2 drop must not be reported as an imported model."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.pmx"
            model.write_text("", encoding="utf-8")

            def importer(_file_path, options=None):
                self.assertTrue(options["use_cpp_fast_load"])
                self.assertTrue(options["use_cpp_vp2_ownership"])
                raise RuntimeError("NATIVE_VP2_OWNERSHIP_UNAVAILABLE")

            result = drag_drop_importer.import_dropped_files(
                [str(model)],
                importer=importer,
                settings_service=_FakeSettingsService(
                    pmx_options={
                        "use_cpp_fast_load": True,
                        "use_cpp_vp2_ownership": True,
                    }
                ),
            )

        self.assertFalse(result)
        display_error.assert_called_once()
        self.assertIn("NATIVE_VP2_OWNERSHIP_UNAVAILABLE", display_error.call_args.args[0])

    @patch.object(drag_drop_importer, "_display_error")
    @patch.object(drag_drop_importer, "_display_info")
    def test_model_exception_aborts_followup_motion_for_same_drop(self, _display_info, display_error):
        """A failed PMX must not send a companion VMD to another model."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.pmx"
            motion = Path(tmp) / "motion.vmd"
            model.write_text("", encoding="utf-8")
            motion.write_text("", encoding="utf-8")

            importer = MagicMock(side_effect=RuntimeError("NATIVE_VP2_OWNERSHIP_UNAVAILABLE"))
            result = drag_drop_importer.import_dropped_files(
                [str(model), str(motion)],
                importer=importer,
                settings_service=_FakeSettingsService(
                    pmx_options={
                        "use_cpp_fast_load": True,
                        "use_cpp_vp2_ownership": True,
                    }
                ),
            )

        self.assertFalse(result)
        importer.assert_called_once_with(
            str(model),
            options={
                "use_cpp_fast_load": True,
                "use_cpp_vp2_ownership": True,
                "custom_namespace": None,
                "profile": {},
            },
        )
        display_error.assert_called_once()

    @patch.object(drag_drop_importer, "_display_warning")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_model_root_with_control_rig_warning_is_partial(
        self,
        display_info,
        display_warning,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.pmx"
            model.write_text("", encoding="utf-8")

            def importer(_file_path, options=None):
                options["profile"] = {"warnings": [{"code": "control_rig_create_failed"}]}
                return "|model_root"

            result = drag_drop_importer.import_dropped_files(
                [str(model)],
                importer=importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        display_warning.assert_called_once()
        self.assertIn("control_rig_create_failed", display_warning.call_args.args[0])
        self.assertEqual(display_info.call_count, 1)  # initial batch status only

    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_pmx_and_pmd_show_each_model_readme_once(self, _display_info):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pmx = root / "model.pmx"
            pmd = root / "legacy.pmd"
            pmx.write_text("", encoding="utf-8")
            pmd.write_text("", encoding="utf-8")
            importer = MagicMock(side_effect=["|pmx_root", "|pmd_root"])
            scene_service = _FakeSceneModelService(
                attrs={
                    "|pmx_root": {"mmd_comment": "PMX JP", "mmd_comment_en": "PMX EN"},
                    "|pmd_root": {"mmd_comment": "PMD JP", "mmd_comment_en": ""},
                }
            )
            readme_adapter = _RecordingReadmeAdapter()

            result = drag_drop_importer.import_dropped_files(
                [str(pmx), str(pmd)],
                importer=importer,
                settings_service=_FakeSettingsService(),
                scene_model_service=scene_service,
                model_readme_adapter=readme_adapter,
            )

        self.assertTrue(result)
        self.assertEqual(len(readme_adapter.calls), 2)
        self.assertEqual(readme_adapter.calls[0][0].to_plain_text(), "Japanese (JP):\nPMX JP\n\nEnglish (EN):\nPMX EN")
        self.assertEqual(readme_adapter.calls[1][0].to_plain_text(), "Japanese (JP):\nPMD JP")
        self.assertEqual([call[1] for call in readme_adapter.calls], [str(pmx), str(pmd)])

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

    @patch.object(drag_drop_importer, "_selected_model_root", return_value="|selected_model")
    @patch.object(drag_drop_importer, "_display_warning")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vmd_profile_warning_is_partial(
        self,
        display_info,
        display_warning,
        _selected_model_root,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            motion = Path(tmp) / "motion.vmd"
            motion.write_text("", encoding="utf-8")

            def importer(_file_path, options=None):
                options["profile"] = {
                    "vmd_converter": {"warnings": [{"code": "runtime_fallback"}]}
                }
                return True

            result = drag_drop_importer.import_dropped_files(
                [str(motion)],
                importer=importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        display_warning.assert_called_once()
        self.assertIn("runtime_fallback", display_warning.call_args.args[0])
        self.assertEqual(display_info.call_count, 1)  # initial batch status only

    @patch.object(drag_drop_importer, "_selected_model_root", return_value="|selected_model")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vpd_applies_pose_to_selected_model(
        self,
        _display_info,
        _selected_model_root,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            pose = Path(tmp) / "pose.vpd"
            pose.write_text("", encoding="utf-8")
            parser = MagicMock(return_value="parsed_vpd")
            pose_importer = MagicMock(return_value=True)

            result = drag_drop_importer.import_dropped_files(
                [str(pose)],
                parser=parser,
                pose_importer=pose_importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        parser.assert_called_once_with(str(pose))
        pose_importer.assert_called_once_with(
            "parsed_vpd",
            str(pose),
            {"target_model": "|selected_model", "create_keyframe": True},
        )

    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_model_and_vpd_applies_pose_to_imported_model(self, _display_info):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model.pmx"
            pose = root / "pose.vpd"
            model.write_text("", encoding="utf-8")
            pose.write_text("", encoding="utf-8")
            importer = MagicMock(return_value="|new_model")
            parser = MagicMock(return_value="parsed_vpd")
            pose_importer = MagicMock(return_value=True)

            result = drag_drop_importer.import_dropped_files(
                [str(pose), str(model)],
                importer=importer,
                parser=parser,
                pose_importer=pose_importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertTrue(result)
        importer.assert_called_once()
        pose_importer.assert_called_once_with(
            "parsed_vpd",
            str(pose),
            {"target_model": "|new_model", "create_keyframe": True},
        )

    @patch.object(drag_drop_importer, "_selected_model_root", return_value=None)
    @patch.object(drag_drop_importer, "_display_warning")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vpd_before_model_load_warns_and_fails(
        self,
        _display_info,
        display_warning,
        _selected_model_root,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            pose = Path(tmp) / "pose.vpd"
            pose.write_text("", encoding="utf-8")
            parser = MagicMock(return_value="parsed_vpd")
            pose_importer = MagicMock(return_value=True)

            result = drag_drop_importer.import_dropped_files(
                [str(pose)],
                parser=parser,
                pose_importer=pose_importer,
                settings_service=_FakeSettingsService(),
            )

        self.assertFalse(result)
        parser.assert_not_called()
        pose_importer.assert_not_called()
        display_warning.assert_called_once()
        self.assertIn("before dropping VPD", display_warning.call_args.args[0])

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

    def test_selected_model_root_rejects_ambiguous_loaded_models(self):
        service = _FakeSceneModelService(models=["|first_model", "|second_model"])
        with patch.object(drag_drop_importer, "SceneModelService", return_value=service):
            with patch.object(drag_drop_importer.cmds, "ls", return_value=[]):
                self.assertIsNone(drag_drop_importer._selected_model_root())

    def test_selected_model_root_falls_back_to_sole_mmd_model(self):
        service = _FakeSceneModelService(models=["|only_model"])
        with patch.object(drag_drop_importer.cmds, "ls", return_value=[]):
            self.assertEqual(drag_drop_importer._selected_model_root(service), "|only_model")

    @patch.object(drag_drop_importer, "_display_warning")
    @patch.object(drag_drop_importer, "_display_info")
    def test_import_dropped_vmd_requires_selection_when_multiple_models_are_loaded(
        self,
        _display_info,
        display_warning,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            motion = Path(tmp) / "motion.vmd"
            motion.write_text("", encoding="utf-8")
            importer = MagicMock(return_value=True)
            service = _FakeSceneModelService(models=["|model_a", "|ns:model_b"])
            with patch.object(drag_drop_importer.cmds, "ls", return_value=[]):
                result = drag_drop_importer.import_dropped_files(
                    [str(motion)],
                    importer=importer,
                    settings_service=_FakeSettingsService(),
                    scene_model_service=service,
                )

        self.assertFalse(result)
        importer.assert_not_called()
        self.assertIn("select one MMD model", display_warning.call_args.args[0])
        self.assertIn("2 models", display_warning.call_args.args[0])

    def test_drop_filter_uses_global_qt_filter_on_maya_2026(self):
        event_filter = drag_drop_importer._MmdDropEventFilter()
        window = object()
        app = object()

        with patch.object(drag_drop_importer, "_maya_version", return_value=2026), patch.object(
            qt_compat, "QApplication"
        ) as application:
            application.instance.return_value = app
            self.assertEqual(event_filter._drop_targets(window), [app, window])

    def test_drop_filter_avoids_global_qt_filter_on_maya_2027(self):
        event_filter = drag_drop_importer._MmdDropEventFilter()
        window = object()

        with patch.object(drag_drop_importer, "_maya_version", return_value=2027), patch.object(
            qt_compat, "QApplication"
        ) as application:
            self.assertEqual(event_filter._drop_targets(window), [window])
            application.instance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
