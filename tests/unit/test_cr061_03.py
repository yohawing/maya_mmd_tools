"""Focused CR061-03 VMD Control Rig option and preflight regressions."""

import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import maya.cmds as cmds

from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_METADATA_SCHEMA,
    CONTROL_RIG_METADATA_VERSION,
    CONTROL_RIG_MMD_OWNED,
)
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_import_state import clear_existing_motion
from mmd_tools.services.settings_service import SettingsService
from tests.common.maya_test_base import MayaTestBase


def _fake_vmd_data(bone_frames=None):
    return type(
        "FakeVmdData",
        (),
        {
            "bone_frames": list(bone_frames or []),
            "morph_frames": [],
            "camera_frames": [],
            "light_frames": [],
        },
    )()


class TestControlRigImportPreflight(unittest.TestCase):
    def setUp(self):
        self.converter = VmdConverter()

    def test_default_off_is_forwarded_by_converter_context(self):
        context = self.converter._import_context(_fake_vmd_data(), target_model="|model")
        self.assertFalse(context.create_mmd_control_rig)

    def test_settings_option_defaults_off(self):
        class Store:
            data = {"ui": {"general": {"development_mode": False}}}

            def get(self, key_path, default=None):
                value = self.data
                for key in key_path.split("."):
                    if not isinstance(value, dict) or key not in value:
                        return default
                    value = value[key]
                return value

            def set(self, _key_path, _value):
                return None

            def save(self):
                return None

        self.assertFalse(SettingsService(Store()).build_vmd_import_options()["create_mmd_control_rig"])

    def test_bake_mode_and_control_rig_rejected_before_scene_mutation(self):
        profile = {}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_enforce_humanik_import_gate"))
            suspend = stack.enter_context(patch.object(self.converter, "_suspend_import_scene_updates"))
            prepare = stack.enter_context(patch.object(self.converter, "_prepare_mmd_control_rig_import"))
            with self.assertRaises(MMDImportException) as raised:
                self.converter.convert(
                    _fake_vmd_data(),
                    target_model="|model",
                    bake_mode=True,
                    create_mmd_control_rig=True,
                    profile=profile,
                )
        self.assertEqual(raised.exception.reason_code, "control_rig_bake_mode_conflict")
        suspend.assert_not_called()
        prepare.assert_not_called()
        self.assertEqual(profile["vmd_converter"]["warnings"][0]["code"], "control_rig_bake_mode_conflict")

    def test_control_rig_import_does_not_create_vmd_anim_layer(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_enforce_humanik_import_gate"))
            stack.enter_context(patch.object(self.converter, "_prepare_mmd_control_rig_import"))
            stack.enter_context(patch.object(self.converter, "_suspend_import_scene_updates", return_value=(True, False)))
            stack.enter_context(patch.object(self.converter, "_restore_import_scene_updates"))
            stack.enter_context(patch.object(self.converter, "_capture_anim_layer_selection", return_value={}))
            stack.enter_context(patch.object(self.converter, "_restore_anim_layer_selection"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(patch.object(self.converter, "_record_bind_poses"))
            stack.enter_context(patch.object(self.converter, "_setup_timeline"))
            stack.enter_context(patch.object(self.converter, "_resolve_runtime_bake_sources", return_value=(None, None, None)))
            stack.enter_context(patch.object(self.converter, "_has_live_mmd_rig_for_runtime_target", return_value=False))
            stack.enter_context(patch.object(self.converter, "_restore_import_timeline_state"))
            anim_layer = stack.enter_context(patch("mmd_tools.converters.vmd_converter.cmds.animLayer"))
            self.assertTrue(
                self.converter.convert(
                    _fake_vmd_data(),
                    target_model="|model",
                    create_mmd_control_rig=True,
                )
            )
        anim_layer.assert_not_called()

    def test_existing_non_base_anim_layer_fails_closed_before_rig_build(self):
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.converters.vmd_converter.cmds.ls", return_value=["BaseAnimation", "ExistingLayer"])
            )
            stack.enter_context(
                patch(
                    "mmd_tools.converters.vmd_converter.cmds.animLayer",
                    return_value=["|model|joint.rotateX"],
                )
            )
            stack.enter_context(patch("mmd_tools.converters.vmd_converter.cmds.listConnections", return_value=[]))
            build = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            with self.assertRaises(MMDImportException) as raised:
                self.converter._prepare_mmd_control_rig_import("|model", vmd_data=_fake_vmd_data())
        self.assertEqual(raised.exception.reason_code, "control_rig_anim_layer_unsupported")
        build.assert_not_called()

    def test_compatible_existing_rig_is_reused_without_rebuild(self):
        metadata = {
            "schema": CONTROL_RIG_METADATA_SCHEMA,
            "version": CONTROL_RIG_METADATA_VERSION,
            "state": CONTROL_RIG_ATTACHED,
            "owner": CONTROL_RIG_MMD_OWNED,
            "bindings": {role: {} for role in ("master", "center", "left_foot_ik", "right_foot_ik")},
            "controls": {role: f"uuid-{role}" for role in ("master", "center", "left_foot_ik", "right_foot_ik")},
        }
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=metadata)
            )
            build = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            enter = stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit")
            )
            self.converter._prepare_mmd_control_rig_import("|model", vmd_data=_fake_vmd_data())
        build.assert_not_called()
        enter.assert_called_once_with("|model")

    def test_unmapped_vmd_role_fails_closed_before_keying(self):
        profile = {}
        spec = MagicMock(can_build_mvp=True)
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            build = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.restore_mmd_control_rig_attached"))
            remove = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            self.converter.bone_name_mapping = {}
            with self.assertRaises(MMDImportException) as raised:
                self.converter._prepare_mmd_control_rig_import(
                    "|model",
                    profile,
                    vmd_data=_fake_vmd_data(
                        [{"bone_name": "未対応ボーン", "position": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}]
                    ),
                )
        self.assertEqual(raised.exception.reason_code, "control_rig_unmapped_vmd_roles")
        build.assert_not_called()
        remove.assert_not_called()
        self.assertEqual(
            profile["mmd_control_rig"]["diagnostics"][0]["code"],
            "control_rig_unmapped_vmd_roles",
        )

    def test_identity_only_unmapped_vmd_role_is_ignored_as_noop(self):
        spec = MagicMock(can_build_mvp=True)
        identity = {"bone_name": "未対応ボーン", "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            build = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            enter = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            self.converter.bone_name_mapping = {}
            self.converter._prepare_mmd_control_rig_import(
                "|model",
                vmd_data=_fake_vmd_data([identity]),
            )
        build.assert_called_once()
        enter.assert_called_once_with("|model")


class TestControlRigMotionClear(MayaTestBase):
    def test_clear_existing_motion_cuts_control_owned_curve(self):
        root = cmds.group(empty=True, name="cr061_control_clear_root")
        joint = cmds.joint(name="cr061_control_clear_joint")
        cmds.parent(joint, root)
        control = cmds.createNode("transform", name="cr061_control_clear_ctrl")
        cmds.setKeyframe(control, attribute="rotateX", time=3, value=25.0)
        converter = VmdConverter()
        converter.bone_name_mapping = {"センター": joint}
        context = VmdImportStateContext(
            logger=converter.logger,
            bone_name_mapping=converter.bone_name_mapping,
            bone_bind_poses={},
            morph_name_mapping={},
            collect_append_info=lambda: {},
            iter_morph_mappings=converter._iter_morph_mappings,
            set_refresh_suspended=converter._set_vmd_import_refresh_suspended,
        )
        with patch(
            "mmd_tools.converters.vmd_import_state.read_mmd_control_rig_metadata",
            return_value={"owner": "CONTROL_OWNED"},
        ), patch(
            "mmd_tools.converters.vmd_import_state.control_rig_edit_routes_for_joints",
            return_value={joint: {"rotateX": (control, "rotateX")}},
        ):
            clear_existing_motion(context, "missing_layer", target_model=root)
        self.assertIsNone(cmds.keyframe(control, attribute="rotateX", query=True, timeChange=True))


if __name__ == "__main__":
    unittest.main()
