"""CR061 preflight rollback error propagation coverage."""

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.converters.vmd_converter import VmdConverter


def _fake_vmd_data():
    """Return VMD-like data with no authored channels for route preflight."""
    return type(
        "FakeVmdData",
        (),
        {
            "bone_frames": [],
            "morph_frames": [],
            "camera_frames": [],
            "light_frames": [],
            "ik_show_hide_frames": [],
        },
    )()


class TestCr061PreflightRollback(unittest.TestCase):
    """Preflight cleanup failures must remain visible to import callers."""

    def setUp(self):
        self.converter = VmdConverter()

    @staticmethod
    def _ls(*_args, **kwargs):
        if kwargs.get("type") == "animLayer":
            return []
        return ["|model"]

    def _patch_prepare_dependencies(self, stack, *, restore, remove):
        spec = MagicMock(can_build_mvp=True)
        stack.enter_context(patch("mmd_tools.converters.vmd_converter.cmds.ls", side_effect=self._ls))
        stack.enter_context(patch("mmd_tools.converters.vmd_converter.cmds.objExists", return_value=False))
        stack.enter_context(
            patch(
                "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
                return_value=None,
            )
        )
        stack.enter_context(
            patch(
                "mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig",
                return_value=spec,
            )
        )
        stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
        stack.enter_context(
            patch(
                "mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit",
            )
        )
        stack.enter_context(
            patch(
                "mmd_tools.core.mmd_control_rig_motion.restore_mmd_control_rig_attached",
                side_effect=restore,
            )
        )
        stack.enter_context(
            patch(
                "mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig",
                side_effect=remove,
            )
        )
        stack.enter_context(patch.object(self.converter, "_capture_mmd_control_rig_scene_snapshot", return_value={}))
        stack.enter_context(
            patch.object(
                self.converter,
                "_validate_mmd_control_rig_ik_routes",
                side_effect=MMDImportException("forced route failure", reason_code="forced_route_failure"),
            )
        )

    def test_restore_and_remove_failures_are_aggregated_and_profiled(self):
        profile = {}
        restore = RuntimeError("restore boom")
        remove = RuntimeError("remove boom")
        with ExitStack() as stack:
            self._patch_prepare_dependencies(stack, restore=restore, remove=remove)
            with self.assertRaises(MMDImportException) as raised:
                self.converter._prepare_mmd_control_rig_import(
                    "|model",
                    profile,
                    vmd_data=_fake_vmd_data(),
                )

        message = str(raised.exception)
        self.assertEqual(raised.exception.reason_code, "forced_route_failure")
        self.assertIn("preflight rollback was incomplete", message)
        self.assertIn("restore attached failed: restore boom", message)
        self.assertIn("remove created rig failed: remove boom", message)
        diagnostic = profile["mmd_control_rig"]["diagnostics"][-1]
        self.assertEqual(diagnostic["rollback_error"], "restore attached failed: restore boom; remove created rig failed: remove boom")
        self.assertEqual(profile["vmd_converter"]["warnings"][-1]["rollback_error"], diagnostic["rollback_error"])

    def test_successful_preflight_rollback_preserves_original_failure(self):
        profile = {}
        with ExitStack() as stack:
            restore = MagicMock()
            remove = MagicMock()
            self._patch_prepare_dependencies(stack, restore=restore, remove=remove)
            with self.assertRaises(MMDImportException) as raised:
                self.converter._prepare_mmd_control_rig_import(
                    "|model",
                    profile,
                    vmd_data=_fake_vmd_data(),
                )

        self.assertEqual(raised.exception.reason_code, "forced_route_failure")
        self.assertEqual(str(raised.exception), "forced route failure")
        self.assertNotIn("rollback", str(raised.exception))
        restore.assert_called_once_with("|model")
        remove.assert_called_once_with("|model")
        self.assertEqual(profile, {})


if __name__ == "__main__":
    unittest.main()
