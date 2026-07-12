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
            "mmd_tools.converters.PhysicsConverter"
        ), patch(
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
        ), patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ):
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

    def test_target_model_without_namespace_route_detail_is_debug_not_info(self):
        """Selection fallback 'Target model without namespace' is DEBUG only."""
        target_model = cmds.group(empty=True, name="mmd_selection_root")
        cmds.select(target_model, replace=True)

        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        mock_logger = MagicMock()
        with patch(
            "mmd_tools.io.vmd_importer.get_logger", return_value=mock_logger
        ), patch(
            "mmd_tools.io.vmd_importer.NamespaceUtils.get_namespace_from_node",
            return_value=None,
        ), patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ):
            converter = converter_class.return_value
            converter.convert.return_value = True
            # No target_model option → selection path.
            result = import_vmd_file(object(), vmd_path, {})

        self.assertTrue(result)
        info_msgs = _logger_message_args(mock_logger.info)
        debug_msgs = _logger_message_args(mock_logger.debug)
        # Maya may return short or long name; match by prefix.
        without_ns = [
            msg
            for msg in debug_msgs
            if isinstance(msg, str) and msg.startswith("Target model without namespace:")
        ]
        self.assertTrue(without_ns, "expected DEBUG without-namespace log, got %r" % (debug_msgs,))
        self.assertFalse(
            any(
                isinstance(msg, str) and msg.startswith("Target model without namespace:")
                for msg in info_msgs
            ),
            "without-namespace route must not be INFO",
        )
        self.assertIn("VMD file import completed", info_msgs)

    def test_motion_scale_option_is_applied_to_converter(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(object(), vmd_path, {"motion_scale": 2.5})

        self.assertTrue(result)
        self.assertEqual(converter.motion_scale, 2.5)

    def test_profile_is_created_for_converter_diagnostics(self):
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {}

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        self.assertIsInstance(options["profile"], dict)
        self.assertIs(converter.convert.call_args.kwargs["profile"], options["profile"])

    def test_normal_mode_success_does_not_repair_physics_preview_feedback(self):
        target_model = cmds.group(empty=True, name="mmd_model_root")
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {"target_model": target_model}

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            physics_converter = physics_converter_class.return_value
            physics_converter.connect_existing_bullet_preview_to_bones.return_value = 3

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter.connect_existing_bullet_preview_to_bones.assert_not_called()
        self.assertEqual(
            options["profile"]["physics_preview_repair_skipped"],
            "maya_bullet_preview_disabled",
        )

    def test_native_physics_bake_used_disables_legacy_preview_feedback(self):
        """Native bake disables existing Bullet-to-bone feedback without deleting setup."""
        target_model = cmds.group(empty=True, name="mmd_model_root")
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {
            "target_model": target_model,
            "use_native_physics_bake": True,
            "bake_mode": True,
            "enable_maya_bullet_preview": True,
        }

        def _convert_side_effect(*_args, **kwargs):
            profile = kwargs["profile"]
            profile.setdefault("vmd_converter", {})["native_physics_bake"] = {
                "requested": True,
                "used": True,
            }
            return True

        with patch("mmd_tools.io.vmd_importer._is_development_mode", return_value=True), patch(
            "mmd_tools.io.vmd_importer.VmdConverter"
        ) as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.side_effect = _convert_side_effect
            physics_converter = physics_converter_class.return_value
            physics_converter.connect_existing_bullet_preview_to_bones.return_value = 3
            physics_converter.set_existing_bullet_preview_feedback_enabled.return_value = 0

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter.connect_existing_bullet_preview_to_bones.assert_not_called()
        physics_converter.set_existing_bullet_preview_feedback_enabled.assert_called_once_with(target_model, False)
        self.assertEqual(
            options["profile"]["physics_preview_repair_skipped"],
            "native_physics_bake_used",
        )
        self.assertEqual(options["profile"]["legacy_physics_preview_disabled"], 0)
        self.assertNotIn("physics_preview_repaired", options["profile"])
        self.assertTrue(converter.convert.call_args.kwargs["use_native_physics_bake"])

    def test_native_physics_bake_requested_but_not_used_repairs_preview(self):
        """When native bake is requested but falls back, retain preview repair."""
        target_model = cmds.group(empty=True, name="mmd_model_root")
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {
            "target_model": target_model,
            "use_native_physics_bake": True,
            "bake_mode": True,
            "enable_maya_bullet_preview": True,
        }

        def _convert_side_effect(*_args, **kwargs):
            profile = kwargs["profile"]
            profile.setdefault("vmd_converter", {})["native_physics_bake"] = {
                "requested": True,
                "used": False,
                "reason": "runtime_unavailable",
            }
            return True

        with patch("mmd_tools.io.vmd_importer._is_development_mode", return_value=True), patch(
            "mmd_tools.io.vmd_importer.VmdConverter"
        ) as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.side_effect = _convert_side_effect
            physics_converter = physics_converter_class.return_value
            physics_converter.connect_existing_bullet_preview_to_bones.return_value = 2
            physics_converter_class.get_legacy_bullet_enabled.return_value = True

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter.set_existing_bullet_preview_feedback_enabled.assert_called_once_with(target_model, True)
        physics_converter.connect_existing_bullet_preview_to_bones.assert_called_once_with(target_model)
        self.assertEqual(options["profile"]["physics_preview_repaired"], 2)
        self.assertNotIn("physics_preview_repair_skipped", options["profile"])
        self.assertTrue(options["profile"]["legacy_bullet_enabled"])
        self.assertTrue(converter.convert.call_args.kwargs["use_native_physics_bake"])

    def test_legacy_bullet_disabled_preference_reapplies_full_suspend_on_vmd_import(self):
        """Root mmd_legacy_bullet_enabled=False restores full legacy OFF after reload."""
        target_model = cmds.group(empty=True, name="mmd_model_root")
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {
            "target_model": target_model,
            "bake_mode": False,
            "enable_maya_bullet_preview": True,
        }

        with patch("mmd_tools.io.vmd_importer._is_development_mode", return_value=True), patch(
            "mmd_tools.io.vmd_importer.VmdConverter"
        ) as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            physics_converter = physics_converter_class.return_value
            physics_converter_class.get_legacy_bullet_enabled.return_value = False
            physics_converter_class.apply_legacy_bullet_enabled_from_attr.return_value = 1

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter_class.ensure_legacy_bullet_enabled_control.assert_called_once_with(
            target_model
        )
        physics_converter_class.get_legacy_bullet_enabled.assert_called_once_with(target_model)
        physics_converter_class.apply_legacy_bullet_enabled_from_attr.assert_called_once_with(
            target_model
        )
        physics_converter.connect_existing_bullet_preview_to_bones.assert_not_called()
        self.assertEqual(options["profile"]["physics_preview_repair_skipped"], "legacy_bullet_disabled")
        self.assertEqual(options["profile"]["legacy_physics_preview_disabled"], 1)
        self.assertFalse(options["profile"]["legacy_bullet_enabled"])
        self.assertNotIn("physics_preview_repaired", options["profile"])
        self.assertNotIn("legacy_physics_preview_restored", options["profile"])

    def test_vmd_import_ensures_legacy_bullet_control_without_overwriting_pref(self):
        """VMD path ensures Root attr/watch; existing user preference is left alone."""
        target_model = cmds.group(empty=True, name="mmd_model_root")
        cmds.addAttr(target_model, longName="mmd_legacy_bullet_enabled", attributeType="bool")
        cmds.setAttr(f"{target_model}.mmd_legacy_bullet_enabled", False)
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {
            "target_model": target_model,
            "bake_mode": False,
            "enable_maya_bullet_preview": True,
        }

        with patch("mmd_tools.io.vmd_importer._is_development_mode", return_value=True), patch(
            "mmd_tools.io.vmd_importer.VmdConverter"
        ) as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.return_value = True
            physics_converter = physics_converter_class.return_value
            # Real preference read would be True without ensure; force disabled path
            # after ensure so we assert ensure was called and value not written by importer.
            physics_converter_class.get_legacy_bullet_enabled.return_value = False
            physics_converter.set_existing_bullet_preview_feedback_enabled.return_value = 0
            physics_converter_class.set_legacy_bullet_enabled.return_value = 0

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter_class.ensure_legacy_bullet_enabled_control.assert_called_once_with(
            target_model
        )
        physics_converter_class.set_legacy_bullet_enabled.assert_not_called()
        # Scene attr must remain the user's False; ensure control is non-destructive.
        self.assertFalse(cmds.getAttr(f"{target_model}.mmd_legacy_bullet_enabled"))
        self.assertEqual(options["profile"]["physics_preview_repair_skipped"], "legacy_bullet_disabled")

    def test_native_physics_bake_does_not_read_or_write_legacy_preference(self):
        """Native bake exclusion is separate from the Root user preference."""
        target_model = cmds.group(empty=True, name="mmd_model_root")
        cmds.addAttr(target_model, longName="mmd_legacy_bullet_enabled", attributeType="bool")
        cmds.setAttr(f"{target_model}.mmd_legacy_bullet_enabled", True)
        temp_root = Path(tempfile.mkdtemp())
        vmd_path = str(temp_root / "motion.vmd")
        Path(vmd_path).write_bytes(b"Vocaloid Motion Data 0002\x00")
        self.files_created.append(vmd_path)
        options = {
            "target_model": target_model,
            "use_native_physics_bake": True,
            "bake_mode": True,
        }

        def _convert_side_effect(*_args, **kwargs):
            profile = kwargs["profile"]
            profile.setdefault("vmd_converter", {})["native_physics_bake"] = {
                "requested": True,
                "used": True,
            }
            return True

        with patch("mmd_tools.io.vmd_importer.VmdConverter") as converter_class, patch(
            "mmd_tools.converters.PhysicsConverter"
        ) as physics_converter_class:
            converter = converter_class.return_value
            converter.convert.side_effect = _convert_side_effect
            physics_converter = physics_converter_class.return_value
            physics_converter.set_existing_bullet_preview_feedback_enabled.return_value = 2
            physics_converter.set_legacy_bullet_enabled.return_value = 0

            result = import_vmd_file(object(), vmd_path, options)

        self.assertTrue(result)
        physics_converter_class.ensure_legacy_bullet_enabled_control.assert_not_called()
        physics_converter_class.get_legacy_bullet_enabled.assert_not_called()
        physics_converter.set_legacy_bullet_enabled.assert_not_called()
        physics_converter.set_existing_bullet_preview_feedback_enabled.assert_called_once_with(
            target_model, False
        )
        # Scene attr must remain the user's value (True); native path never writes it.
        self.assertTrue(cmds.getAttr(f"{target_model}.mmd_legacy_bullet_enabled"))
        self.assertEqual(options["profile"]["physics_preview_repair_skipped"], "native_physics_bake_used")

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
                {},
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
                import_vmd_file(object(), vmd_path, {})


if __name__ == "__main__":
    unittest.main()
