"""Focused CR061-03 VMD Control Rig option and preflight regressions."""

import unittest
from contextlib import ExitStack, nullcontext
from unittest.mock import MagicMock, patch

import maya.cmds as cmds
from maya.api import OpenMayaAnim as oma

from mmd_tools.core import maya_animation_utils, mmd_control_rig_motion
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.mmd_control_rig_builder import (
    CONTROL_RIG_ATTACHED,
    CONTROL_RIG_CONTROL_OWNED,
    CONTROL_RIG_METADATA_SCHEMA,
    CONTROL_RIG_METADATA_VERSION,
    CONTROL_RIG_MMD_OWNED,
    MmdControlRigBuildResult,
    MmdControlRigBuildError,
)
from mmd_tools.converters.vmd_converter import VmdConverter
from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_legacy_bone_routes import build_legacy_bone_key_routes
from mmd_tools.converters.vmd_import_state import clear_existing_motion, cut_keyable_attrs
from mmd_tools.io.vmd_importer import _resolve_quaternion_interpolation
from mmd_tools.services.settings_service import SettingsService
from tests.common.maya_test_base import MayaTestBase


def _fake_vmd_data(bone_frames=None, ik_show_hide_frames=None):
    return type(
        "FakeVmdData",
        (),
        {
            "bone_frames": list(bone_frames or []),
            "morph_frames": [],
            "camera_frames": [],
            "light_frames": [],
            "ik_show_hide_frames": list(ik_show_hide_frames or []),
        },
    )()


class TestControlRigImportPreflight(unittest.TestCase):
    def setUp(self):
        self.converter = VmdConverter()

    def test_default_off_is_forwarded_by_converter_context(self):
        context = self.converter._import_context(_fake_vmd_data(), target_model="|model")
        self.assertFalse(context.create_mmd_control_rig)

    def test_sparse_quaternion_policy_defaults_to_control_rig_only(self):
        self.assertFalse(_resolve_quaternion_interpolation({}))
        self.assertTrue(_resolve_quaternion_interpolation({"create_mmd_control_rig": True}))
        self.assertFalse(
            _resolve_quaternion_interpolation(
                {"create_mmd_control_rig": True, "use_quaternion_interpolation": False}
            )
        )
        self.assertTrue(
            _resolve_quaternion_interpolation(
                {"create_mmd_control_rig": False, "use_quaternion_interpolation": True}
            )
        )

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

    def test_control_rig_failure_reraises_and_rolls_back_transaction(self):
        profile = {}
        transaction = {
            "root": "|model",
            "created": False,
            "entered_here": False,
            "prior_animation_snapshot": [],
        }
        frame = {
            "bone_name": "センター",
            "frame_number": 0,
            "position": [1.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        }
        self.converter.bone_name_mapping = {"センター": "|model|center"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.converter, "_enforce_humanik_import_gate"))
            stack.enter_context(patch.object(self.converter, "_prepare_mmd_control_rig_import", return_value=transaction))
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
            stack.enter_context(patch.object(self.converter, "_apply_mmd_control_rig_ik_enabled_animation"))
            rollback = stack.enter_context(patch.object(self.converter, "_rollback_mmd_control_rig_import", return_value=None))
            stack.enter_context(patch.object(self.converter, "_convert_bone_animation", side_effect=RuntimeError("forced keying failure")))
            with self.assertRaises(MMDImportException) as raised:
                self.converter.convert(
                    _fake_vmd_data([frame]),
                    target_model="|model",
                    create_mmd_control_rig=True,
                    clear_existing_motion=False,
                    profile=profile,
                )
        self.assertEqual(raised.exception.reason_code, "control_rig_bone_keying_failed")
        rollback.assert_called_once_with(transaction)
        self.assertIn("forced keying failure", profile["mmd_control_rig"]["diagnostics"][-1]["message"])

    def test_ik_visibility_keys_owned_controller(self):
        frame = {"frame_number": 7, "ik_states": [("右足IK", False)]}
        with patch.object(
            self.converter,
            "_resolve_mmd_control_rig_ik_controls",
            return_value={"右足IK": "|model|right_leg_CTRL"},
        ), patch("mmd_tools.converters.vmd_converter.cmds.setAttr") as set_attr, patch(
            "mmd_tools.converters.vmd_converter.cmds.setKeyframe"
        ) as set_key:
            self.converter._apply_mmd_control_rig_ik_enabled_animation(
                _fake_vmd_data(ik_show_hide_frames=[frame]), target_model="|model"
            )
        set_attr.assert_called_once_with("|model|right_leg_CTRL.ikEnabled", False)
        set_key.assert_called_once()
        self.assertEqual(set_key.call_args.kwargs["attribute"], "ikEnabled")

    def test_ik_visibility_keys_target_owned_legacy_solver_when_control_is_absent(self):
        frame = {"frame_number": 7, "ik_states": [("右髪ＩＫ", False)]}
        with patch.object(
            self.converter,
            "_resolve_mmd_control_rig_ik_routes",
            return_value=(
                {"右足IK": "|model|right_foot_CTRL"},
                {"右髪ＩＫ": "|model|hair_ik_mmdCcdIk"},
            ),
        ), patch("mmd_tools.converters.vmd_converter.cmds.setAttr") as set_attr, patch(
            "mmd_tools.converters.vmd_converter.cmds.setKeyframe"
        ) as set_key:
            self.converter._apply_mmd_control_rig_ik_enabled_animation(
                _fake_vmd_data(ik_show_hide_frames=[frame]), target_model="|model"
            )
        set_attr.assert_called_once_with("|model|hair_ik_mmdCcdIk.enabled", False)
        set_key.assert_called_once_with(
            "|model|hair_ik_mmdCcdIk",
            attribute="enabled",
            time=7.0,
            value=0,
        )

    def test_ik_visibility_skips_names_absent_from_control_and_legacy_routes(self):
        frame = {
            "frame_number": 7,
            "ik_states": [("右足IK", True), ("右髪ＩＫ", False), ("ﾈｸﾀｲＩＫ", False)],
        }
        profile = {}
        with patch.object(
            self.converter,
            "_resolve_mmd_control_rig_ik_routes",
            return_value=(
                {"右足IK": "|model|right_foot_CTRL"},
                {"右髪ＩＫ": "|model|hair_ik_mmdCcdIk"},
            ),
        ):
            self.converter._validate_mmd_control_rig_ik_routes(
                "|model",
                _fake_vmd_data(ik_show_hide_frames=[frame]),
                profile=profile,
            )
        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(warning["code"], "control_rig_skipped_unmapped_vmd_ik")
        self.assertEqual(warning["reason"], "target_model_ik_route_missing")
        self.assertEqual(warning["skipped_ik_names"], ["ﾈｸﾀｲＩＫ"])

    def test_ik_visibility_mixed_routes_do_not_double_key_control_owned_solver(self):
        frame = {
            "frame_number": 7,
            "ik_states": [("右足IK", False), ("右髪ＩＫ", True)],
        }
        with patch.object(
            self.converter,
            "_resolve_mmd_control_rig_ik_routes",
            return_value=(
                {"右足IK": "|model|right_foot_CTRL"},
                {
                    "右足IK": "|model|right_foot_mmdCcdIk",
                    "右髪ＩＫ": "|model|hair_ik_mmdCcdIk",
                },
            ),
        ), patch("mmd_tools.converters.vmd_converter.cmds.setAttr") as set_attr, patch(
            "mmd_tools.converters.vmd_converter.cmds.setKeyframe"
        ) as set_key:
            self.converter._apply_mmd_control_rig_ik_enabled_animation(
                _fake_vmd_data(ik_show_hide_frames=[frame]), target_model="|model"
            )
        self.assertEqual(
            [call.args[0] for call in set_attr.call_args_list],
            ["|model|right_foot_CTRL.ikEnabled", "|model|hair_ik_mmdCcdIk.enabled"],
        )
        self.assertEqual(
            [call.args[0] for call in set_key.call_args_list],
            ["|model|right_foot_CTRL", "|model|hair_ik_mmdCcdIk"],
        )

    def test_ik_visibility_without_property_frames_defaults_control_and_legacy_routes_on(self):
        with patch.object(
            self.converter,
            "_resolve_mmd_control_rig_ik_routes",
            return_value=(
                {"右足IK": "|model|right_foot_CTRL"},
                {
                    "右足IK": "|model|right_foot_mmdCcdIk",
                    "右髪ＩＫ": "|model|hair_ik_mmdCcdIk",
                },
            ),
        ), patch.object(self.converter, "_get_animation_frame_range", return_value=(3, 12)), patch(
            "mmd_tools.converters.vmd_converter.cmds.setAttr"
        ) as set_attr, patch("mmd_tools.converters.vmd_converter.cmds.setKeyframe") as set_key:
            self.converter._apply_mmd_control_rig_ik_enabled_animation(
                _fake_vmd_data(bone_frames=[object()]), target_model="|model"
            )
        self.assertEqual(
            [call.args[0] for call in set_attr.call_args_list],
            ["|model|right_foot_CTRL.ikEnabled", "|model|hair_ik_mmdCcdIk.enabled"],
        )
        self.assertEqual(
            [call.args[0] for call in set_key.call_args_list],
            ["|model|right_foot_CTRL", "|model|hair_ik_mmdCcdIk"],
        )

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

    def test_clear_existing_motion_precedes_new_control_rig_basis_capture(self):
        """A legacy pose is cleared before a newly-created rig samples joints."""
        spec = MagicMock(can_build_mvp=True)
        events = []

        def clear_motion(*_args, **_kwargs):
            events.append("clear")

        def build_rig(*_args, **_kwargs):
            events.append("build")

        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig", side_effect=build_rig)
            )
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(
                patch.object(self.converter, "_capture_mmd_control_rig_scene_snapshot", return_value={})
            )
            stack.enter_context(patch.object(self.converter, "_clear_existing_motion", side_effect=clear_motion))
            stack.enter_context(
                patch.object(self.converter, "_detect_vmd_motion_kind", return_value="model")
            )
            stack.enter_context(patch.object(self.converter, "_validate_mmd_control_rig_ik_routes"))
            transaction = self.converter._prepare_mmd_control_rig_import(
                "|model",
                vmd_data=_fake_vmd_data(),
                clear_existing_motion=True,
                layer_name="VMD_Motion",
            )

        self.assertEqual(events, ["clear", "build"])
        self.assertTrue(transaction["motion_cleared"])

    def test_clear_existing_motion_preflight_failure_restores_captured_scene(self):
        """A failed new-rig build restores motion cleared for basis capture."""
        spec = MagicMock(can_build_mvp=True)
        scene_snapshot = {"channels": [{"node": "|model|joint", "attribute": "translateX"}]}
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            stack.enter_context(
                patch(
                    "mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig",
                    side_effect=MmdControlRigBuildError("forced basis build failure"),
                )
            )
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(
                patch.object(self.converter, "_capture_mmd_control_rig_scene_snapshot", return_value=scene_snapshot)
            )
            clear = stack.enter_context(patch.object(self.converter, "_clear_existing_motion"))
            stack.enter_context(
                patch.object(self.converter, "_detect_vmd_motion_kind", return_value="model")
            )
            restore = stack.enter_context(
                patch.object(self.converter, "_restore_mmd_control_rig_scene_snapshot", return_value=None)
            )
            with self.assertRaises(MMDImportException) as raised:
                self.converter._prepare_mmd_control_rig_import(
                    "|model",
                    vmd_data=_fake_vmd_data(),
                    clear_existing_motion=True,
                    layer_name="VMD_Motion",
                )

        clear.assert_called_once_with(
            "VMD_Motion",
            None,
            target_model="|model",
            preserve_curve_nodes=True,
        )
        restore.assert_called_once_with(scene_snapshot)
        self.assertEqual(raised.exception.reason_code, "control_rig_edit_failed")

    def test_control_owned_rotation_route_disables_solver_input_route(self):
        converter = MagicMock()
        converter.bone_name_mapping = {"右足": "|model|right_leg"}
        converter._collect_append_info.return_value = {}
        converter._collect_ik_link_joints.return_value = {
            "|model|right_leg": {"solver": "|model|right_leg_ik_mmdCcdIk", "slot": 6}
        }
        with patch(
            "mmd_tools.converters.vmd_legacy_bone_routes.control_rig_edit_routes_for_joints",
            return_value={
                "|model|right_leg": {
                    "rotateX": ("|model|right_leg_CTRL", "rotateX"),
                    "rotateY": ("|model|right_leg_CTRL", "rotateY"),
                    "rotateZ": ("|model|right_leg_CTRL", "rotateZ"),
                }
            },
        ):
            routes = build_legacy_bone_key_routes(converter)
        route = routes["|model|right_leg"]
        self.assertFalse(route["skip_rotate"])
        self.assertIsNone(route["ik_solver_rotate"])
        self.assertTrue(route["control_owned"])
        self.assertFalse(route["quaternion_interpolation_safe"])
        self.assertEqual(route["attr_targets"]["rotateX"], ("|model|right_leg_CTRL", "rotateX"))

    def test_control_route_discovers_namespaced_metadata_recursively(self):
        joint = "|ns:model|ns:center"

        class FakeCmds:
            def __init__(self):
                self.root_query = None

            def ls(self, value, **kwargs):
                if str(value).startswith("*."):
                    self.root_query = kwargs
                    return ["|ns:model"]
                return [str(value)]

        fake_cmds = FakeCmds()
        metadata = {
            "owner": "CONTROL_OWNED",
            "bindings": {"center": {}},
            "controls": {"center": "control-uuid"},
        }
        with patch.object(
            mmd_control_rig_motion,
            "read_mmd_control_rig_metadata",
            return_value=metadata,
        ), patch.object(
            mmd_control_rig_motion,
            "resolve_mmd_control_rig_binding_joint",
            return_value=joint,
        ), patch.object(
            mmd_control_rig_motion,
            "_expanded_authored_plugs",
            return_value=(f"{joint}.translateX",),
        ), patch.object(
            mmd_control_rig_motion,
            "_resolve_uuid",
            return_value="|ns:center_CTRL",
        ):
            routes = mmd_control_rig_motion.control_rig_edit_routes_for_joints(
                [joint], cmds_module=fake_cmds
            )

        self.assertTrue(fake_cmds.root_query["recursive"])
        self.assertEqual(
            routes[joint]["translateX"],
            ("|ns:center_CTRL", "translateX"),
        )

    def test_active_role_keeps_identity_frame_zero_but_identity_only_role_is_dropped(self):
        frames = [
            {"bone_name": "右足", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
            {"bone_name": "右足", "position": [1, 0, 0], "rotation": [0, 0, 0, 1]},
            {"bone_name": "任意の未使用ボーン", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
        ]
        retained = VmdConverter._control_rig_bone_frames_for_import(frames, mapped_names={"右足"})
        self.assertEqual([frame["bone_name"] for frame in retained], ["右足", "右足"])
        self.assertEqual(
            VmdConverter._vmd_bone_frame_channels(frames[1]),
            {"translateX", "translateY", "translateZ"},
        )

    def test_active_unmapped_role_is_filtered_before_bone_conversion(self):
        frames = [
            {"bone_name": "右足", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
            {"bone_name": "右足", "position": [1, 0, 0], "rotation": [0, 0, 0, 1]},
            {"bone_name": "袖", "position": [0.2, 0, 0], "rotation": [0, 0, 0, 1]},
        ]
        retained = VmdConverter._control_rig_bone_frames_for_import(frames, mapped_names={"右足"})
        self.assertEqual([frame["bone_name"] for frame in retained], ["右足", "右足"])

    def test_mapped_vmd_role_without_control_rig_route_falls_back_to_joint_channels(self):
        profile = {}
        spec = MagicMock(can_build_mvp=True)
        frame = {
            "bone_name": "右足",
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.1, 0.2, 0.3, 0.9],
        }
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            build = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            enter = stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit")
            )
            remove = stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(
                patch(
                    "mmd_tools.core.mmd_control_rig_motion.control_rig_edit_routes_for_joints",
                    return_value={
                        "|model|right_leg": {
                            "rotateX": ("|model|right_leg_CTRL", "rotateX"),
                            "rotateY": ("|model|right_leg_CTRL", "rotateY"),
                            "rotateZ": ("|model|right_leg_CTRL", "rotateZ"),
                        }
                    },
                )
            )
            self.converter.bone_name_mapping = {"右足": "|model|right_leg"}
            transaction = self.converter._prepare_mmd_control_rig_import(
                "|model",
                profile,
                vmd_data=_fake_vmd_data([frame]),
            )
        self.assertEqual(transaction["root"], "|model")
        build.assert_called_once()
        enter.assert_called_once_with("|model")
        remove.assert_not_called()
        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(warning["source"], "vmd_converter")
        self.assertEqual(warning["code"], "control_rig_legacy_bone_route_fallback")
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(warning["reason"], "control_route_missing")
        self.assertEqual(
            warning["fallback_channels"],
            {
                "右足": ["translateX", "translateY", "translateZ"],
            },
        )
        self.assertEqual(warning["fallback"], "legacy_bone_channels")

    def test_ik_solver_rotation_is_reported_as_legacy_fallback(self):
        """IK solver inputRotate writes rotation channels outside Control Rig ownership."""
        profile = {}
        spec = MagicMock(can_build_mvp=True)
        frame = {
            "bone_name": "右足",
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.1, 0.2, 0.3, 0.9],
        }
        joint = "|model|right_leg"
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(
                patch(
                    "mmd_tools.core.mmd_control_rig_motion.control_rig_edit_routes_for_joints",
                    return_value={},
                )
            )
            stack.enter_context(
                patch.object(
                    self.converter,
                    "_build_legacy_bone_key_routes",
                    return_value={
                        joint: {
                            "skip_rotate": True,
                            "ik_solver_rotate": {"solver": "|model|right_leg_ik_mmdCcdIk", "slot": 6},
                            "attr_targets": {},
                        }
                    },
                )
            )
            self.converter.bone_name_mapping = {"右足": joint}
            self.converter._prepare_mmd_control_rig_import(
                "|model",
                profile,
                vmd_data=_fake_vmd_data([frame]),
            )

        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(
            warning["fallback_channels"]["右足"],
            ["rotateX", "rotateY", "rotateZ", "translateX", "translateY", "translateZ"],
        )

    def test_append_route_remains_legacy_fallback_in_warning(self):
        """Append attr_targets are legacy-owned, not authored Control Rig channels."""
        profile = {}
        spec = MagicMock(can_build_mvp=True)
        frame = {
            "bone_name": "右足",
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.1, 0.2, 0.3, 0.9],
        }
        joint = "|model|right_leg"
        append_attrs = {
            attr: ("|model|right_leg_append", attr)
            for attr in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
        with ExitStack() as stack:
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata", return_value=None)
            )
            stack.enter_context(
                patch("mmd_tools.core.mmd_control_rig_analyzer.analyze_mmd_control_rig", return_value=spec)
            )
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.build_mmd_control_rig"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_motion.enter_mmd_control_rig_edit"))
            stack.enter_context(patch("mmd_tools.core.mmd_control_rig_builder.remove_mmd_control_rig"))
            stack.enter_context(patch.object(self.converter, "_build_name_mappings"))
            stack.enter_context(
                patch(
                    "mmd_tools.core.mmd_control_rig_motion.control_rig_edit_routes_for_joints",
                    return_value={},
                )
            )
            stack.enter_context(
                patch.object(
                    self.converter,
                    "_build_legacy_bone_key_routes",
                    return_value={joint: {"attr_targets": append_attrs, "skip_rotate": False}},
                )
            )
            self.converter.bone_name_mapping = {"右足": joint}
            self.converter._prepare_mmd_control_rig_import(
                "|model",
                profile,
                vmd_data=_fake_vmd_data([frame]),
            )

        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(
            warning["fallback_channels"]["右足"],
            ["rotateX", "rotateY", "rotateZ", "translateX", "translateY", "translateZ"],
        )

    def test_unmapped_vmd_role_is_skipped_with_structured_warning(self):
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
            self.converter._prepare_mmd_control_rig_import(
                "|model",
                profile,
                vmd_data=_fake_vmd_data(
                    [{"bone_name": "未対応ボーン", "position": [1.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}]
                ),
            )
        build.assert_called_once()
        remove.assert_not_called()
        warning = profile["vmd_converter"]["warnings"][0]
        self.assertEqual(warning["source"], "vmd_converter")
        self.assertEqual(warning["code"], "control_rig_skipped_unmapped_vmd_bones")
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(warning["message"], "Skipped VMD bone roles absent from target model")
        self.assertEqual(warning["reason"], "target_model_bone_missing")
        self.assertEqual(warning["skipped_bones"], ["未対応ボーン"])
        self.assertEqual(warning["fallback"], "skip_missing_target_bones")

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
    def test_preserved_curve_clear_keeps_non_animation_input_connected(self):
        """Rollback curve retention must not detach solver/constraint inputs."""
        target = cmds.createNode("transform", name="cr061_non_anim_input_target")
        driver = cmds.createNode("multiplyDivide", name="cr061_non_anim_input_driver")
        source = f"{driver}.outputX"
        destination = f"{target}.translateX"
        cmds.connectAttr(source, destination, force=True)
        detached = []

        cut_keyable_attrs(
            target,
            ("translateX",),
            preserve_curve_nodes=True,
            detached_curve_nodes=detached,
        )

        self.assertTrue(cmds.isConnected(source, destination))
        self.assertEqual(detached, [])

    def test_sparse_quaternion_rotation_bake_preserves_keys_and_pose(self):
        """Compound bake keeps sparse authored times and quaternion state."""
        control = cmds.createNode("transform", name="cr061_sparse_quaternion_control")
        joint = cmds.createNode("transform", name="cr061_sparse_quaternion_joint")
        times = (547.0, 550.0)
        values = {
            "X": (179.0, -179.0),
            "Y": (0.0, 0.0),
            "Z": (0.0, 0.0),
        }
        control_sources = {}
        mmd_sources = {}
        for axis in "XYZ":
            control_curve = cmds.createNode("animCurveTA")
            mmd_curve = cmds.createNode("animCurveTA")
            for time, value in zip(times, values[axis]):
                cmds.setKeyframe(control_curve, time=time, value=value)
                cmds.setKeyframe(mmd_curve, time=time, value=0.0)
            control_plug = f"{control}.rotate{axis}"
            target_plug = f"{joint}.rotate{axis}"
            control_source = f"{control_curve}.output"
            mmd_source = f"{mmd_curve}.output"
            cmds.connectAttr(control_source, control_plug, force=True)
            cmds.connectAttr(control_plug, target_plug, force=True)
            control_sources[control_plug] = control_source
            mmd_sources[control_plug] = mmd_source

        cmds.rotationInterpolation(
            *(f"{control}.rotate{axis}" for axis in "XYZ"),
            convert="quaternionSlerp",
        )
        cmds.currentTime(548.5, edit=True)
        before = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
        rows = [
            {
                "control": f"{control}.rotate{axis}",
                "target": f"{joint}.rotate{axis}",
                "source": mmd_sources[f"{control}.rotate{axis}"],
                "controlSource": control_sources[f"{control}.rotate{axis}"],
                "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
            }
            for axis in ("X", "Y", "Z")
        ]
        mmd_control_rig_motion._commit_control_rotation_group(
            cmds,
            rows,
            control_sources,
        )
        after = cmds.xform(joint, query=True, worldSpace=True, matrix=True)
        self.assertEqual(
            cmds.keyframe(mmd_sources[f"{control}.rotateX"].split(".", 1)[0], query=True, timeChange=True),
            list(times),
        )
        self.assertAlmostEqual(max(abs(a - b) for a, b in zip(before, after)), 0.0, places=7)
        for axis in "XYZ":
            curve = mmd_sources[f"{control}.rotate{axis}"].split(".", 1)[0]
            self.assertEqual(cmds.rotationInterpolation(curve, query=True), "quaternionSlerp")
        self.assertEqual(cmds.keyframe(mmd_sources[f"{control}.rotateX"].split(".", 1)[0], query=True, timeChange=True), list(times))
        cmds.delete(control, joint)

    def test_bake_rotation_group_requires_one_standard_xyz_transform(self):
        rows = [
            {
                "control": f"|ctrl.rotate{axis}",
                "target": f"|joint.rotate{axis}",
                "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
            }
            for axis in ("X", "Y", "Z")
        ]
        rows.append(
            {
                "control": "|ctrl.rotateX",
                "target": "|append.baseRotateX",
                "routeClass": mmd_control_rig_motion.ROUTE_SAMPLED,
            }
        )
        groups = mmd_control_rig_motion._rotation_channel_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            [row["target"] for row in groups[0]],
            ["|joint.rotateX", "|joint.rotateY", "|joint.rotateZ"],
        )

    def test_bake_rotation_group_excludes_sampled_xyz_route(self):
        rows = [
            {
                "control": f"|ctrl.rotate{axis}",
                "target": f"|joint.rotate{axis}",
                "routeClass": mmd_control_rig_motion.ROUTE_SAMPLED,
            }
            for axis in ("X", "Y", "Z")
        ]
        self.assertEqual(mmd_control_rig_motion._rotation_channel_groups(rows), [])

    def test_bake_rotation_group_requires_quaternion_controller_curves(self):
        control = cmds.createNode("transform", name="cr061_euler_opt_out_control")
        joint = cmds.createNode("transform", name="cr061_euler_opt_out_joint")
        rows = []
        sources = {}
        for axis in "XYZ":
            cmds.setKeyframe(control, attribute=f"rotate{axis}", time=1, value=0.0)
            source = cmds.listConnections(
                f"{control}.rotate{axis}", source=True, destination=False, plugs=True
            )[0]
            row = {
                "control": f"{control}.rotate{axis}",
                "target": f"{joint}.rotate{axis}",
                "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
            }
            rows.append(row)
            sources[row["control"]] = source

        group = mmd_control_rig_motion._rotation_channel_groups(rows)[0]
        self.assertFalse(
            mmd_control_rig_motion._rotation_group_uses_quaternion(cmds, group, sources)
        )
        cmds.rotationInterpolation(
            *(f"{control}.rotate{axis}" for axis in "XYZ"),
            convert="quaternionSlerp",
        )
        self.assertTrue(
            mmd_control_rig_motion._rotation_group_uses_quaternion(cmds, group, sources)
        )
        cmds.delete(control, joint)

    def test_rotation_interpolation_state_restores_after_transaction_failure(self):
        control = cmds.createNode("transform", name="cr061_rollback_rotation_control")
        joint = cmds.createNode("transform", name="cr061_rollback_rotation_joint")
        rows = []
        for axis in "XYZ":
            cmds.setKeyframe(joint, attribute=f"rotate{axis}", time=1, value=0.0)
            source = cmds.listConnections(
                f"{joint}.rotate{axis}", source=True, destination=False, plugs=True
            )[0]
            cmds.disconnectAttr(source, f"{joint}.rotate{axis}")
            cmds.connectAttr(f"{control}.rotate{axis}", f"{joint}.rotate{axis}")
            rows.append(
                {
                    "control": f"{control}.rotate{axis}",
                    "target": f"{joint}.rotate{axis}",
                    "source": source,
                    "routeClass": mmd_control_rig_motion.ROUTE_SAME_BASIS,
                }
            )
        states = mmd_control_rig_motion._capture_rotation_interpolation_states(
            cmds, [rows]
        )
        for row in rows:
            cmds.disconnectAttr(row["control"], row["target"])
            cmds.connectAttr(row["source"], row["target"])
        cmds.rotationInterpolation(
            *(row["target"] for row in rows),
            convert="quaternionSlerp",
        )
        mmd_control_rig_motion._restore_rotation_interpolation_states(cmds, states)
        for axis in "XYZ":
            curve = cmds.listConnections(
                f"{joint}.rotate{axis}", source=True, destination=False
            )[0]
            self.assertNotEqual(
                cmds.rotationInterpolation(curve, query=True), "quaternionSlerp"
            )
        cmds.delete(control, joint)

    def test_quaternion_interpolation_targets_control_owned_rotation_curves(self):
        """Control-owned XYZ curves receive the same quaternion mode as bone curves."""
        joint = cmds.joint(name="cr061_quaternion_joint")
        control = cmds.createNode("transform", name="cr061_quaternion_control")
        converter = VmdConverter()
        converter.use_animation_layers = False
        converter.use_quaternion_interpolation = True
        converter._bone_bind_poses["下半身"] = (0.0, 0.0, 0.0)
        frames = [
            {
                "frame_number": 0,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.98, 0.0, 0.2],
            },
            {
                "frame_number": 3,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.99, 0.0, -0.05],
            },
        ]
        route = {
            "control_owned": True,
            "quaternion_interpolation_safe": True,
            "attr_targets": {
                attr: (control, attr) for attr in ("rotateX", "rotateY", "rotateZ")
            },
        }

        with patch(
            "mmd_tools.converters.vmd_bone_animation._apply_quaternion_interpolation"
        ) as apply_quaternion:
            converter._set_bone_keyframes(joint, frames, "下半身", route)

        apply_quaternion.assert_called_once()
        self.assertEqual(
            apply_quaternion.call_args.args[1],
            [f"{control}.rotateX", f"{control}.rotateY", f"{control}.rotateZ"],
        )
        cmds.delete(joint, control)

    def test_control_rig_default_keeps_legacy_fallback_on_vmd_bezier(self):
        """Unowned route gaps must not inherit the control-only quaternion policy."""
        joint = cmds.joint(name="cr061_legacy_quaternion_fallback_joint")
        converter = VmdConverter()
        converter.use_animation_layers = False
        converter.use_quaternion_interpolation = True
        converter._bone_bind_poses["右袖"] = (0.0, 0.0, 0.0)
        frames = [
            {
                "frame_number": 0,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.98, 0.0, 0.2],
            },
            {
                "frame_number": 3,
                "position": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.99, 0.0, -0.05],
            },
        ]
        with patch(
            "mmd_tools.converters.vmd_bone_animation._apply_quaternion_interpolation"
        ) as apply_quaternion:
            converter._set_bone_keyframes(joint, frames, "右袖", {})

        apply_quaternion.assert_not_called()
        cmds.delete(joint)

    def test_rotation_only_vmd_frame_keys_legacy_joint_translate_fallback(self):
        """Legacy writer retains all six channels even for a rotation-only VMD frame."""
        joint = cmds.joint(name="cr061_legacy_fallback_joint")
        control = cmds.createNode("transform", name="cr061_legacy_fallback_control")
        converter = VmdConverter()
        converter.use_animation_layers = False
        converter._bone_bind_poses["右足"] = (0.0, 0.0, 0.0)
        frame = {
            "frame_number": 7,
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.1, 0.2, 0.3, 0.9],
        }

        converter._set_bone_keyframes(
            joint,
            [frame],
            "右足",
            {
                "attr_targets": {
                    "rotateX": (control, "rotateX"),
                    "rotateY": (control, "rotateY"),
                    "rotateZ": (control, "rotateZ"),
                }
            },
        )

        for attr in ("translateX", "translateY", "translateZ"):
            self.assertEqual(cmds.keyframe(joint, attribute=attr, query=True, timeChange=True), [7.0])
        for attr in ("rotateX", "rotateY", "rotateZ"):
            self.assertEqual(cmds.keyframe(control, attribute=attr, query=True, timeChange=True), [7.0])
        cmds.delete(joint, control)

    def test_api_keying_reuses_existing_control_curve_uuid(self):
        cmds.select(clear=True)
        control = cmds.createNode("transform", name="cr061_existing_curve_ctrl")
        cmds.setKeyframe(control, attribute="rotateX", time=3, value=25.0)
        curve = (cmds.listConnections(f"{control}.rotateX", source=True, destination=False) or [None])[0]
        curve_uuid = (cmds.ls(curve, uuid=True) or [None])[0]

        curves = maya_animation_utils.create_animation_curves(
            control,
            ["rotateX"],
            tangent_type=oma.MFnAnimCurve.kTangentLinear,
        )

        self.assertEqual(curves["rotateX"].name(), curve)
        self.assertEqual((cmds.ls(curve, uuid=True) or [None])[0], curve_uuid)

    def test_failed_reimport_restores_existing_curve_uuid_and_payload(self):
        cmds.select(clear=True)
        control = cmds.createNode("transform", name="cr061_rollback_curve_ctrl")
        cmds.setKeyframe(control, attribute="rotateX", time=1, value=10.0)
        cmds.setKeyframe(control, attribute="rotateX", time=5, value=30.0)
        curve = (cmds.listConnections(f"{control}.rotateX", source=True, destination=False) or [None])[0]
        curve_uuid = (cmds.ls(curve, uuid=True) or [None])[0]
        metadata = {"controls": {"center": (cmds.ls(control, uuid=True) or [None])[0]}}
        converter = VmdConverter()
        snapshot = converter._capture_mmd_control_rig_animation_snapshot(metadata)
        cmds.setKeyframe(control, attribute="rotateX", time=1, value=99.0)
        cmds.setKeyframe(control, attribute="rotateX", time=2, value=99.0)

        error = converter._restore_mmd_control_rig_animation_snapshot(snapshot)

        self.assertIsNone(error)
        self.assertEqual((cmds.ls(curve, uuid=True) or [None])[0], curve_uuid)
        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [1.0, 5.0])
        restored_values = cmds.keyframe(curve, query=True, valueChange=True)
        self.assertAlmostEqual(restored_values[0], 10.0, places=9)
        self.assertAlmostEqual(restored_values[1], 30.0, places=9)

    def test_curve_payload_capture_failure_uses_copy_paste_without_clearing_destination(self):
        """A keyed source survives payload-query failures through Maya's native fallback."""
        source_node = cmds.createNode("animCurveTA", name="cr061_capture_failure_source")
        destination_node = cmds.createNode("animCurveTA", name="cr061_capture_failure_destination")
        source = f"{source_node}.output"
        destination = f"{destination_node}.output"
        cmds.setKeyframe(source_node, time=2, value=4.0)
        cmds.setKeyframe(source_node, time=8, value=9.0)
        cmds.setKeyframe(destination_node, time=5, value=123.0)

        with patch.object(
            mmd_control_rig_motion,
            "_capture_animation_curve_payload",
            return_value={"captureFailed": True},
        ), patch.object(cmds, "copyKey", wraps=cmds.copyKey) as copy_key, patch.object(
            cmds, "pasteKey", wraps=cmds.pasteKey
        ) as paste_key:
            mmd_control_rig_motion._copy_animation_curve(cmds, source, destination)

        copy_key.assert_called_once_with(source_node, option="curve")
        paste_key.assert_called_once_with(destination_node, option="replaceCompletely")
        self.assertEqual(cmds.keyframe(destination_node, query=True, timeChange=True), [2.0, 8.0])
        self.assertEqual(cmds.keyframe(destination_node, query=True, valueChange=True), [4.0, 9.0])

    def test_empty_curve_payload_still_clears_destination(self):
        """A successfully captured empty curve intentionally removes stale keys."""
        source_node = cmds.createNode("animCurveTA", name="cr061_empty_capture_source")
        destination_node = cmds.createNode("animCurveTA", name="cr061_empty_capture_destination")
        cmds.setKeyframe(destination_node, time=5, value=123.0)

        mmd_control_rig_motion._copy_animation_curve(
            cmds,
            f"{source_node}.output",
            f"{destination_node}.output",
        )

        self.assertIsNone(cmds.keyframe(destination_node, query=True, timeChange=True))

    def test_known_empty_curve_clears_when_metadata_capture_fails(self):
        """An empty source stays destructive-clear even if tangent metadata is unavailable."""
        source_node = cmds.createNode("animCurveTA", name="cr061_empty_metadata_failure_source")
        destination_node = cmds.createNode("animCurveTA", name="cr061_empty_metadata_failure_destination")
        cmds.setKeyframe(destination_node, time=5, value=123.0)

        with patch.object(cmds, "keyTangent", side_effect=RuntimeError("unsupported tangent query")), patch.object(
            cmds, "copyKey", wraps=cmds.copyKey
        ) as copy_key:
            mmd_control_rig_motion._copy_animation_curve(
                cmds,
                f"{source_node}.output",
                f"{destination_node}.output",
            )

        copy_key.assert_not_called()
        self.assertIsNone(cmds.keyframe(destination_node, query=True, timeChange=True))

    def test_channel_snapshot_capture_failure_keeps_surviving_curve_keys(self):
        """Unknown payloads restore edges without destructively clearing keys."""
        control = cmds.createNode("transform", name="cr061_channel_capture_failure_ctrl")
        curve = cmds.createNode("animCurveTA", name="cr061_channel_capture_failure_curve")
        cmds.setKeyframe(curve, time=2, value=4.0)
        cmds.connectAttr(f"{curve}.output", f"{control}.rotateX", force=True)
        row = {
            "incoming": [f"{curve}.output"],
            "curve_node": curve,
            "curve_type": "animCurveTA",
            "curve_payload": {"captureFailed": True, "times": [2.0]},
            "value": 4.0,
        }
        cmds.setKeyframe(curve, time=8, value=9.0)

        mmd_control_rig_motion._restore_animation_channel_snapshot(
            cmds,
            row,
            destination=f"{control}.rotateX",
        )

        self.assertEqual(cmds.keyframe(curve, query=True, timeChange=True), [2.0, 8.0])

    def test_channel_snapshot_known_empty_curve_clears_surviving_keys(self):
        """A successful empty payload remains intentionally destructive-clear."""
        control = cmds.createNode("transform", name="cr061_channel_empty_ctrl")
        curve = cmds.createNode("animCurveTA", name="cr061_channel_empty_curve")
        cmds.setKeyframe(curve, time=2, value=4.0)
        cmds.connectAttr(f"{curve}.output", f"{control}.rotateX", force=True)
        row = {
            "incoming": [f"{curve}.output"],
            "curve_node": curve,
            "curve_type": "animCurveTA",
            "curve_payload": {"captureFailed": False, "times": [], "keys": []},
            "value": 0.0,
        }

        mmd_control_rig_motion._restore_animation_channel_snapshot(
            cmds,
            row,
            destination=f"{control}.rotateX",
        )

        self.assertIsNone(cmds.keyframe(curve, query=True, timeChange=True))

    def test_channel_snapshot_restores_compound_numeric_value(self):
        """Maya's one-row compound wrapper is expanded for setAttr restore."""
        control = cmds.createNode("transform", name="cr061_compound_snapshot_ctrl")
        cmds.setAttr(f"{control}.translate", 1.0, 2.0, 3.0)
        row = mmd_control_rig_motion._capture_animation_channel_snapshot(
            cmds,
            f"{control}.translate",
        )
        cmds.setAttr(f"{control}.translate", 7.0, 8.0, 9.0)

        mmd_control_rig_motion._restore_animation_channel_snapshot(
            cmds,
            row,
            destination=f"{control}.translate",
        )

        self.assertEqual(cmds.getAttr(f"{control}.translate")[0], (1.0, 2.0, 3.0))

    def test_channel_snapshot_capture_failure_cannot_recreate_deleted_curve(self):
        """A deleted curve plus an unknown payload fails closed instead of recreating empty."""
        control = cmds.createNode("transform", name="cr061_channel_deleted_ctrl")
        curve = cmds.createNode("animCurveTA", name="cr061_channel_deleted_curve")
        cmds.setKeyframe(curve, time=2, value=4.0)
        cmds.connectAttr(f"{curve}.output", f"{control}.rotateX", force=True)
        row = {
            "incoming": [f"{curve}.output"],
            "curve_node": curve,
            "curve_type": "animCurveTA",
            "curve_payload": {"captureFailed": True, "times": [2.0]},
            "value": 4.0,
        }
        cmds.delete(curve)

        with self.assertRaisesRegex(RuntimeError, "payload is unavailable"):
            mmd_control_rig_motion._restore_animation_channel_snapshot(
                cmds,
                row,
                destination=f"{control}.rotateX",
                recreate_curve=True,
            )

    def test_curve_restore_failure_marks_edit_exit_rollback_incomplete(self):
        """A failed snapshot copy must surface as an incomplete transaction rollback."""
        fake_cmds = MagicMock()
        metadata_before = '{"owner":"CONTROL_OWNED"}'
        snapshots = [("destination.output", "backup.output")]
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(mmd_control_rig_motion, "_capture_plug_states", return_value={})
            )
            stack.enter_context(
                patch.object(mmd_control_rig_motion, "_raw_metadata", return_value=metadata_before)
            )
            stack.enter_context(
                patch.object(mmd_control_rig_motion, "_capture_curve_snapshots", return_value=snapshots)
            )
            stack.enter_context(patch.object(mmd_control_rig_motion, "_write_metadata"))
            stack.enter_context(
                patch.object(mmd_control_rig_motion, "_undo_chunk", return_value=nullcontext())
            )
            stack.enter_context(patch.object(mmd_control_rig_motion, "_restore_plug_states"))
            stack.enter_context(patch.object(mmd_control_rig_motion, "_restore_raw_metadata"))
            stack.enter_context(patch.object(mmd_control_rig_motion, "_discard_curve_snapshots"))
            copy = stack.enter_context(
                patch.object(
                    mmd_control_rig_motion,
                    "_copy_animation_curve",
                    side_effect=RuntimeError("restore copy failure"),
                )
            )
            with self.assertRaisesRegex(MmdControlRigBuildError, "rollback was incomplete"):
                with mmd_control_rig_motion._edit_exit_transaction(
                    fake_cmds,
                    "|model",
                    "Test Edit Exit",
                    "restore",
                    [],
                    [],
                    curve_plugs=("destination.output",),
                ):
                    raise RuntimeError("edit action failure")

        copy.assert_called_once_with(fake_cmds, "backup.output", "destination.output")

    def test_clear_existing_motion_cuts_control_owned_curve(self):
        cmds.select(clear=True)
        root = cmds.group(empty=True, name="cr061_control_clear_root")
        cmds.select(clear=True)
        joint = cmds.joint(name="cr061_control_clear_joint")
        cmds.parent(joint, root)
        control = cmds.createNode("transform", name="cr061_control_clear_ctrl")
        cmds.setKeyframe(control, attribute="rotateX", time=3, value=25.0)
        control_curve = (cmds.listConnections(f"{control}.rotateX", source=True, destination=False) or [None])[0]
        control_curve_uuid = (cmds.ls(control_curve, uuid=True) or [None])[0]
        cmds.addAttr(control, longName="ikEnabled", attributeType="bool", keyable=True)
        cmds.setKeyframe(control, attribute="ikEnabled", time=4, value=1.0)
        ik_curve = (cmds.listConnections(f"{control}.ikEnabled", source=True, destination=False) or [None])[0]
        ik_curve_uuid = (cmds.ls(ik_curve, uuid=True) or [None])[0]
        foreign_root = cmds.group(empty=True, name="cr061_foreign_clear_root")
        foreign_control = cmds.createNode("transform", name="cr061_foreign_clear_ctrl")
        cmds.parent(foreign_control, foreign_root)
        cmds.addAttr(foreign_control, longName="ikEnabled", attributeType="bool", keyable=True)
        cmds.setKeyframe(foreign_control, attribute="ikEnabled", time=4, value=1.0)
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
        ), patch(
            "mmd_tools.converters.vmd_import_state.control_rig_edit_ik_enabled_plugs_for_model",
            return_value=(f"{control}.ikEnabled",),
        ):
            clear_existing_motion(context, "missing_layer", target_model=root)
        self.assertIsNone(cmds.keyframe(control, attribute="rotateX", query=True, timeChange=True))
        self.assertEqual((cmds.ls(control_curve, uuid=True) or [None])[0], control_curve_uuid)
        self.assertEqual(
            cmds.listConnections(f"{control}.rotateX", source=True, destination=False),
            [control_curve],
        )
        self.assertIsNone(cmds.keyframe(control, attribute="ikEnabled", query=True, timeChange=True))
        self.assertEqual((cmds.ls(ik_curve, uuid=True) or [None])[0], ik_curve_uuid)
        self.assertEqual(
            cmds.listConnections(f"{control}.ikEnabled", source=True, destination=False),
            [ik_curve],
        )
        self.assertEqual(
            cmds.keyframe(foreign_control, attribute="ikEnabled", query=True, timeChange=True),
            [4.0],
        )
        self.assertTrue(cmds.objExists(foreign_root))

    def test_control_rig_ik_enabled_resolver_uses_validated_target_controls(self):
        """IK clear ignores foreign/corrupt UUID rows outside inspected topology."""
        target_control = cmds.createNode("transform", name="cr061_ik_route_target_ctrl")
        cmds.addAttr(target_control, longName="ikEnabled", attributeType="bool", keyable=True)
        foreign_control = cmds.createNode("transform", name="cr061_ik_route_foreign_ctrl")
        cmds.addAttr(foreign_control, longName="ikEnabled", attributeType="bool", keyable=True)

        target_control_uuid = cmds.ls(target_control, uuid=True)[0]
        foreign_control_uuid = cmds.ls(foreign_control, uuid=True)[0]
        target_control_long = cmds.ls(target_control, long=True)[0]
        metadata = {
            "owner": CONTROL_RIG_CONTROL_OWNED,
            "controls": {"right_foot_ik": target_control_uuid},
            "bindings": {
                "right_foot_ik": {"inputKind": "ik_controller"},
                "foreign_foot_ik": {
                    "inputKind": "ik_controller",
                    "controlUuid": foreign_control_uuid,
                },
                "corrupt_foot_ik": {
                    "inputKind": "ik_controller",
                    "controlUuid": "missing-control-uuid",
                },
            },
        }

        inspected = MmdControlRigBuildResult(
            model_root="|cr061_target_model",
            control_group="|cr061_target_model|Controls",
            selection_set="cr061_target_modelControls_SET",
            controls={"right_foot_ik": target_control_long},
            zero_groups={},
            state="EDIT",
            owner=CONTROL_RIG_CONTROL_OWNED,
            created=False,
        )
        with patch.object(mmd_control_rig_motion, "inspect_mmd_control_rig", return_value=inspected), patch.object(
            mmd_control_rig_motion,
            "read_mmd_control_rig_metadata",
            return_value=metadata,
        ):
            routes = mmd_control_rig_motion.control_rig_edit_ik_enabled_plugs_for_model(
                "|cr061_target_model",
            )

        self.assertEqual(routes, (f"{target_control_long}.ikEnabled",))


if __name__ == "__main__":
    unittest.main()
