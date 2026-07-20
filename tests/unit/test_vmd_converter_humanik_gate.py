"""Unit tests for the HumanIK VMD-import mode gate on VmdConverter.convert().

Covers ``HUMANIK-SOURCE-VMD-IK-PARITY-1``: VMD import must stay permitted in
NEUTRAL/SOURCE, must be refused fail-closed while the target model is a
HumanIK TARGET preview or Control Rig, and the refusal must happen strictly
before any scene mutation (name mapping build, clear_existing_motion, etc.).
"""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.humanik_retarget import HumanIkImportLock
from mmd_tools.converters.vmd_converter import VmdConverter


def _fake_vmd_data(**overrides):
    defaults = {
        "bone_frames": [],
        "morph_frames": [],
        "camera_frames": [],
        "light_frames": [],
    }
    defaults.update(overrides)
    return type("FakeVmdData", (), defaults)()


class TestEnforceHumanIkImportGate(unittest.TestCase):
    """Direct tests of the gate helper in isolation."""

    def setUp(self):
        self.converter = VmdConverter()

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        return_value=HumanIkImportLock(blocked=None, character=None),
    )
    def test_unblocked_lock_allows_import(self, describe):
        self.converter._enforce_humanik_import_gate("|model")
        describe.assert_called_once_with("|model")

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        return_value=HumanIkImportLock(blocked="target_preview", character="Character1", input_source="Source"),
    )
    def test_target_preview_lock_raises_with_restore_message(self, describe):
        with self.assertRaises(MMDImportException) as ctx:
            self.converter._enforce_humanik_import_gate("|model")

        message = str(ctx.exception)
        self.assertIn("|model", message)
        self.assertIn("TARGET preview", message)
        self.assertIn("Restore MMD Rig", message)

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        return_value=HumanIkImportLock(blocked="control_rig", character="Character1", has_control_rig=True),
    )
    def test_control_rig_lock_raises_with_restore_message(self, describe):
        with self.assertRaises(MMDImportException) as ctx:
            self.converter._enforce_humanik_import_gate("|model")

        message = str(ctx.exception)
        self.assertIn("Control Rig", message)
        self.assertIn("Restore MMD Rig", message)

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        side_effect=RuntimeError("detection blew up"),
    )
    def test_detection_failure_allows_import(self, describe):
        # Defensive/lazy import contract: any detection failure fails OPEN so
        # VMD import never hard-depends on HumanIK availability.
        self.converter._enforce_humanik_import_gate("|model")

    def test_missing_humanik_module_allows_import(self):
        with patch.dict("sys.modules", {"mmd_tools.core.humanik_retarget": None}):
            self.converter._enforce_humanik_import_gate("|model")


class TestConvertGatesBeforeMutation(unittest.TestCase):
    """convert() must refuse before any scene mutation, and stay side-effect free."""

    def setUp(self):
        self.converter = VmdConverter()

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        return_value=HumanIkImportLock(blocked="target_preview", character="Character1", input_source="Source"),
    )
    def test_blocked_target_raises_before_any_mutating_call(self, describe):
        vmd_data = _fake_vmd_data(bone_frames=[object()])

        with ExitStack() as stack:
            suspend = stack.enter_context(patch.object(self.converter, "_suspend_import_scene_updates"))
            build_names = stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            clear_motion = stack.enter_context(patch.object(self.converter, "_clear_existing_motion"))
            record_bind = stack.enter_context(patch.object(self.converter, "_record_bind_poses"))
            apply_ik = stack.enter_context(patch.object(self.converter, "_apply_ik_enabled_animation"))
            convert_bone = stack.enter_context(patch.object(self.converter, "_convert_bone_animation"))

            with self.assertRaises(MMDImportException):
                self.converter.convert(
                    vmd_data,
                    target_model="|model",
                    clear_existing_motion=True,
                )

        suspend.assert_not_called()
        build_names.assert_not_called()
        clear_motion.assert_not_called()
        record_bind.assert_not_called()
        apply_ik.assert_not_called()
        convert_bone.assert_not_called()

    @patch(
        "mmd_tools.core.humanik_retarget.describe_humanik_import_lock",
        return_value=HumanIkImportLock(blocked=None, character=None),
    )
    def test_unblocked_proceeds_to_mutation_path(self, describe):
        vmd_data = _fake_vmd_data()

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_should_use_mmd_runtime_bake", return_value=True))
            stack.enter_context(patch.object(self.converter, "_convert_using_mmd_runtime", return_value=True))
            build_names = stack.enter_context(patch.object(self.converter, "_build_name_mappings"))

            result = self.converter.convert(vmd_data, target_model="|model")

        self.assertTrue(result)
        build_names.assert_called_once()


if __name__ == "__main__":
    unittest.main()
