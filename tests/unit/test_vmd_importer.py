"""VMD importerのMaya依存しない境界処理を検証するテスト。"""

import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from maya import cmds

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.io.vmd_importer import import_vmd_file
from tests.common.maya_test_base import MayaTestBase


def _logger_message_args(mock_log_method):
    """Extract first positional message arg from logger calls (Py3.7-safe)."""
    # call[0] is args tuple; _Call.args is 3.8+ only.
    return [call[0][0] for call in mock_log_method.call_args_list if call[0]]


class TestVmdImporter(MayaTestBase):
    """VMD importerのruntime bake入力解決を検証する。"""

    def test_target_model_source_file_is_passed_as_pmx_path(self):
        target_model = cmds.group(empty=True, name="mmd_model_root")
        cmds.addAttr(target_model, longName="mmd_source_file", dataType="string")

        temp_root = Path(tempfile.mkdtemp())
        pmx_path = str(temp_root / "source" / "model.pmx")
        vmd_path = str(temp_root / "motion" / "motion.vmd")
        Path(pmx_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vmd_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pmx_path).write_bytes(b"pmx")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")

        self.files_created.extend([pmx_path, vmd_path])
        cmds.setAttr(f"{target_model}.mmd_source_file", pmx_path, type="string")
        self.assertEqual(cmds.getAttr(f"{target_model}.mmd_source_file"), pmx_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {"target_model": target_model, "clear_existing_motion": True},
            )

        self.assertTrue(result)
        self.assertEqual(converter.motion_scale, 1.0)
        kwargs = converter.convert.call_args.kwargs
        self.assertEqual(kwargs["pmx_path"], pmx_path)
        self.assertEqual(kwargs["vmd_bytes"], b"Vocaloid Motion Data 0002\x00")
        self.assertTrue(kwargs["clear_existing_motion"])
        self.assertEqual(kwargs["target_model"], target_model)

    def test_route_detail_logs_are_debug_while_start_completion_remain_info(self):
        """Internal route/detail messages are DEBUG; start/completion stay INFO.

        Reuses the target-model mmd_source_file restore path (same setup as
        test_target_model_source_file_is_passed_as_pmx_path).
        """
        target_model = cmds.group(empty=True, name="mmd_model_root")
        cmds.addAttr(target_model, longName="mmd_source_file", dataType="string")

        temp_root = Path(tempfile.mkdtemp())
        pmx_path = str(temp_root / "source" / "model.pmx")
        vmd_path = str(temp_root / "motion" / "motion.vmd")
        Path(pmx_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vmd_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pmx_path).write_bytes(b"pmx")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")

        self.files_created.extend([pmx_path, vmd_path])
        cmds.setAttr(f"{target_model}.mmd_source_file", pmx_path, type="string")

        mock_logger = MagicMock()
        with patch(
            "mmd_tools.io.vmd_importer.get_logger", return_value=mock_logger
        ), patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.io.vmd_importer.is_mmd_runtime_available", return_value=True
        ), patch(
            "mmd_tools.core.native.mmd_anim_runtime.create_runtime_node_for_model",
            return_value="mmdRuntime1",
        ), patch(
            "mmd_tools.core.native.mmd_anim_runtime.connect_runtime_node_outputs_to_model",
            return_value={
                "connected_bones": ["a", "b"],
                "connected_morphs": ["m"],
                "skipped": [],
                "warnings": [],
            },
        ):
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {
                    "target_model": target_model,
                    "use_live_runtime": True,
                },
            )

        self.assertTrue(result)

        info_msgs = _logger_message_args(mock_logger.info)
        debug_msgs = _logger_message_args(mock_logger.debug)

        # Boundary INFO: start / completion (and aggregate live-runtime summary).
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Starting VMD file import:")
                for msg in info_msgs
            ),
            "expected INFO start log, got %r" % (info_msgs,),
        )
        self.assertIn("VMD file import completed", info_msgs)
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Live runtime DG connection:")
                for msg in info_msgs
            ),
            "expected INFO live-runtime aggregate, got %r" % (info_msgs,),
        )

        # Route/detail must be DEBUG only (not INFO).
        restored = "Restored PMX source from model: %s" % pmx_path
        created = "Created live runtime node: mmdRuntime1"
        self.assertIn(restored, debug_msgs)
        self.assertNotIn(restored, info_msgs)
        self.assertIn(created, debug_msgs)
        self.assertNotIn(created, info_msgs)

        # Start/completion must remain INFO (not demoted to DEBUG).
        self.assertFalse(
            any(
                isinstance(msg, str) and msg.startswith("Starting VMD file import:")
                for msg in debug_msgs
            ),
            "start message must remain INFO, not DEBUG",
        )
        self.assertNotIn("VMD file import completed", debug_msgs)

    def test_target_namespace_route_detail_is_debug_not_info(self):
        """Target namespace / without-namespace routing details stay DEBUG."""
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        mock_logger = MagicMock()
        with patch(
            "mmd_tools.io.vmd_importer.get_logger", return_value=mock_logger
        ), patch(
            "mmd_tools.io.vmd_importer.NamespaceUtils.get_namespace_from_node",
            return_value="demoNS",
        ), patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {"target_model": "demoNS:root"},
            )

        self.assertTrue(result)
        info_msgs = _logger_message_args(mock_logger.info)
        debug_msgs = _logger_message_args(mock_logger.debug)
        ns_msg = "Target namespace: demoNS"
        self.assertIn(ns_msg, debug_msgs)
        self.assertNotIn(ns_msg, info_msgs)
        self.assertTrue(
            any(
                isinstance(msg, str) and msg.startswith("Starting VMD file import:")
                for msg in info_msgs
            ),
        )
        self.assertIn("VMD file import completed", info_msgs)

    def test_scene_only_skips_model_resolution_physics_and_runtime_queries(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.io.vmd_importer.NamespaceUtils.get_namespace_from_node"
        ) as namespace_query, patch(
            "mmd_tools.io.vmd_importer._try_recover_physics_drivers"
        ) as recover_physics, patch(
            "mmd_tools.io.vmd_importer.is_mmd_runtime_available"
        ) as runtime_available, patch(
            "mmd_tools.io.vmd_importer.cmds.ls"
        ) as ls_query:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(object(), vmd_path, {"scene_animation_only": True})

        self.assertTrue(result)
        kwargs = converter.convert.call_args.kwargs
        self.assertTrue(kwargs["scene_animation_only"])
        self.assertNotIn("target_model", kwargs)
        namespace_query.assert_not_called()
        recover_physics.assert_not_called()
        runtime_available.assert_not_called()
        ls_query.assert_not_called()

    def test_model_motion_without_explicit_target_is_rejected_before_conversion(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            with self.assertRaisesRegex(MMDImportException, "explicit target model"):
                import_vmd_file(object(), vmd_path, {})

        converter_class.assert_not_called()

    def test_motion_scale_option_is_applied_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(), vmd_path, {"motion_scale": 2.5, "scene_animation_only": True}
            )

        self.assertTrue(result)
        self.assertEqual(converter.motion_scale, 2.5)

    def test_profile_is_created_for_converter_diagnostics(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {"scene_animation_only": True}

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        self.assertIsInstance(options["profile"], dict)
        self.assertIs(converter.convert.call_args.kwargs["profile"], options["profile"])

    def test_camera_light_import_options_are_applied_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {
                    "import_camera_animation": False,
                    "import_light_animation": False,
                    "scene_animation_only": True,
                },
            )

        self.assertTrue(result)
        self.assertFalse(converter.import_camera_animation)
        self.assertFalse(converter.import_light_animation)

    def test_progress_callback_is_forwarded_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        progress = []
        progress_callback = progress.append

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(
                object(),
                vmd_path,
                {"scene_animation_only": True},
                progress_callback=progress_callback,
            )

        self.assertTrue(result)
        self.assertIs(converter.convert.call_args.kwargs["progress_callback"], progress_callback)
        self.assertIn(15, progress)
        self.assertIn(25, progress)
        self.assertIn(35, progress)

    def test_converter_failure_raises_import_exception(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = False

            with self.assertRaises(MMDImportException):
                import_vmd_file(object(), vmd_path, {"scene_animation_only": True})


if __name__ == "__main__":
    unittest.main()
