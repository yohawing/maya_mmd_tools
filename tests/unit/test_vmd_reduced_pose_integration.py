"""Unit tests for runtime reduced-bake routing and explicit fallback decisions."""

from __future__ import annotations

import unittest
import sys
import types
from unittest import mock

from mmd_tools.converters.vmd_reduced_pose_integration import (
    ReducedPoseIntegrationOutcome,
    author_reduced_pose_from_runtime_cache,
    prepare_reduced_pose_inputs,
    _static_target_is_settable,
)
from mmd_tools.converters.vmd_reduced_pose_scene import ReducedPoseSceneAuthoringOutcome
from mmd_tools.core.native.mmd_anim_runtime_types import MmdRuntimeGenericCurve, MmdRuntimeGenericCurveDescriptor
from tests.unit.test_vmd_reduced_pose_adapter import _pose


class _Cache:
    dense_batch_result = object()
    baked_frames = [0.0, 1.0, 2.0]
    joint_channel_values = {
        "root": {
            "translateX": [0.0, 1.0, 2.0],
            "translateY": [0.0, 0.0, 0.0],
            "translateZ": [0.0, 0.0, 0.0],
            "rotateX": [0.0, 0.0, 0.0],
            "rotateY": [0.0, 0.0, 0.0],
            "rotateZ": [0.0, 0.0, 0.0],
        }
    }
    joint_channel_static = {
        "root": {
            channel: {"first": 0.0, "is_static": False, "count": 3}
            for channel in ("translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ")
        }
    }
    morph_cache = [(0.0, [0.0]), (1.0, [1.0]), (2.0, [0.0])]


class _Converter:
    bone_index_to_joint = {0: "root"}
    anim_layer = None
    morph_name_mapping = {"smile": [("blendShape", "weight", "smile"), ("network", "weight", "smile")]}

    @staticmethod
    def _collect_mmd_ik_passthrough_info():
        return {}

    @staticmethod
    def _collect_append_info():
        return {}

    @staticmethod
    def _decompose_append_rotations_for_scene(*_args):
        return {}

    @staticmethod
    def _decompose_append_translations_for_scene(*_args):
        return {}

    @staticmethod
    def _iter_morph_mappings(entry):
        return iter(entry or ())


class ReducedPoseIntegrationTest(unittest.TestCase):
    @staticmethod
    def _two_morph_pose():
        base = _pose(3, (0, 2), (0, 2))
        descriptor = MmdRuntimeGenericCurveDescriptor(40, 1, 1, 1, -1, 4, 2, 0, 2)
        info = base.info._replace(morph_count=2)
        extra = MmdRuntimeGenericCurve(descriptor, base.curves[1].keys)
        return base._replace(info=info, curves=base.curves + (extra,))

    def test_morph_fanout_expands_concrete_targets_explicitly(self):
        plan, bone_targets, morph_targets, fanout_count, reason = prepare_reduced_pose_inputs(
            _Converter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
        )

        self.assertIsNone(reason)
        self.assertEqual(bone_targets[(0, "translateX")], "root.translateX")
        morph_curves = [curve for curve in plan.curves if curve.owner_kind == "morph"]
        self.assertEqual(len(morph_curves), 1)
        self.assertEqual(fanout_count, 1)
        self.assertEqual(morph_targets[0], ("blendShape.weight", "network.weight"))
        self.assertEqual(plan.report.source_key_count, 21)

    def test_mixed_mapped_and_unmapped_morphs_drop_only_unmapped_curve(self):
        class MixedCache(_Cache):
            morph_cache = [(0.0, [0.0, 0.0]), (1.0, [1.0, 0.5]), (2.0, [0.0, 0.0])]

        plan, _bone_targets, morph_targets, _fanout_count, reason = prepare_reduced_pose_inputs(
            _Converter(), MixedCache(), self._two_morph_pose(), ["smile", "unmapped"]
        )

        self.assertIsNone(reason)
        self.assertEqual(len([curve for curve in plan.curves if curve.owner_kind == "morph"]), 2)
        self.assertEqual(plan.report.source_key_count, 24)
        self.assertEqual(morph_targets[0], ("blendShape.weight", "network.weight"))
        self.assertEqual(morph_targets[1], ())

    def test_all_unmapped_morphs_keep_bone_reduction(self):
        plan, _bone_targets, morph_targets, _fanout_count, reason = prepare_reduced_pose_inputs(
            _Converter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["unmapped"]
        )

        self.assertIsNone(reason)
        self.assertEqual(len([curve for curve in plan.curves if curve.owner_kind == "morph"]), 1)
        self.assertEqual(len(plan.curves), 7)
        self.assertEqual(plan.report.source_key_count, 21)
        self.assertEqual(morph_targets, {0: ()})

    def test_detached_append_routes_directly_to_joint_channels(self):
        class AppendConverter(_Converter):
            @staticmethod
            def _collect_append_info():
                return {
                    "root": {
                        "node": "mmdAppend1",
                        "attr_map": {"rotateX": "baseRotateX"},
                        "affect_rotation": True,
                    }
                }

            @staticmethod
            def _decompose_append_rotations_for_scene(*_args):
                return {"root": {"rotateX": [0.25, 0.6, 0.75]}}

        plan, bone_targets, _morph_targets, _fanout_count, reason = prepare_reduced_pose_inputs(
            AppendConverter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
        )

        self.assertIsNone(reason)
        rotate = next(curve for curve in plan.curves if curve.channel == "rotateX")
        self.assertEqual([key.value for key in rotate.keys], [0.0, 0.0])
        self.assertEqual(bone_targets[(0, "rotateX")], "root.rotateX")

    def test_detached_append_long_alias_still_routes_directly_to_joint(self):
        class AliasAppendConverter(_Converter):
            @staticmethod
            def _collect_append_info():
                return {
                    "|model|root": {
                        "node": "mmdAppend1",
                        "attr_map": {"rotateX": "baseRotateX"},
                        "affect_rotation": True,
                    }
                }

            @staticmethod
            def _decompose_append_rotations_for_scene(*_args):
                return {"|model|root": {"rotateX": [0.25, 0.6, 0.75]}}

        plan, bone_targets, _morph_targets, _fanout_count, reason = prepare_reduced_pose_inputs(
            AliasAppendConverter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
        )

        self.assertIsNone(reason)
        rotate = next(curve for curve in plan.curves if curve.channel == "rotateX")
        self.assertEqual([key.value for key in rotate.keys], [0.0, 0.0])
        self.assertEqual(bone_targets[(0, "rotateX")], "root.rotateX")

    def test_ik_rig_is_already_detached_and_targets_joint_directly(self):
        class IkConverter(_Converter):
            @staticmethod
            def _collect_mmd_ik_passthrough_info():
                return {"root": {"node": "mmdCcdIk1", "input_slot": 0}}

        plan, bone_targets, morph_targets, fanout_count, reason = prepare_reduced_pose_inputs(
            IkConverter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
        )

        self.assertIsNone(reason)
        self.assertIsNotNone(plan)
        self.assertEqual(bone_targets[(0, "rotateX")], "root.rotateX")
        self.assertEqual(bone_targets[(0, "rotateY")], "root.rotateY")
        self.assertEqual(bone_targets[(0, "rotateZ")], "root.rotateZ")

    def test_authoring_failure_is_returned_for_dense_fallback(self):
        failed = ReducedPoseSceneAuthoringOutcome(False, (), "forced author failure", True)
        with mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration.author_reduced_pose_channel_plan",
            return_value=failed,
        ):
            result = author_reduced_pose_from_runtime_cache(
                _Converter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
            )

        self.assertFalse(result.success)
        self.assertIn("forced author failure", result.reason)
        self.assertIsInstance(result, ReducedPoseIntegrationOutcome)

    def test_ik_authoring_uses_direct_joint_targets(self):
        class IkConverter(_Converter):
            @staticmethod
            def _collect_mmd_ik_passthrough_info():
                return {"root": {"node": "mmdCcdIk1", "input_slot": 0}}

        authored = ReducedPoseSceneAuthoringOutcome(True, (), None, False)
        with mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration.author_reduced_pose_channel_plan",
            return_value=authored,
        ) as author:
            result = author_reduced_pose_from_runtime_cache(
                IkConverter(), _Cache(), _pose(3, (0, 2), (0, 2)), ["smile"]
            )

        self.assertTrue(result.success)
        self.assertEqual(author.call_args.kwargs["static_values"], {})

    def test_static_channels_are_not_authored_as_curves(self):
        class StaticCache(_Cache):
            joint_channel_values = {"root": dict(_Cache.joint_channel_values["root"], translateY=None)}
            joint_channel_static = {
                "root": dict(
                    _Cache.joint_channel_static["root"],
                    translateY={"first": 2.5, "is_static": True, "count": 3},
                )
            }

        authored = ReducedPoseSceneAuthoringOutcome(True, (), None, False)
        with mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration._static_target_is_settable",
            return_value=True,
        ), mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration.author_reduced_pose_channel_plan",
            return_value=authored,
        ) as author:
            result = author_reduced_pose_from_runtime_cache(
                _Converter(), StaticCache(), _pose(3, (0, 2), (0, 2)), ["smile"]
            )

        self.assertTrue(result.success)
        self.assertEqual(author.call_args.kwargs["static_values"], {"root.translateY": 2.5})
        plan = author.call_args.args[0]
        self.assertFalse(any(curve.channel == "translateY" for curve in plan.curves))

    def test_non_settable_static_channel_is_skipped_for_dense_parity(self):
        class StaticCache(_Cache):
            joint_channel_values = {"root": dict(_Cache.joint_channel_values["root"], translateY=None)}
            joint_channel_static = {
                "root": dict(
                    _Cache.joint_channel_static["root"],
                    translateY={"first": 2.5, "is_static": True, "count": 3},
                )
            }

        authored = ReducedPoseSceneAuthoringOutcome(True, (), None, False)
        with mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration._static_target_is_settable",
            return_value=False,
        ), mock.patch(
            "mmd_tools.converters.vmd_reduced_pose_integration.author_reduced_pose_channel_plan",
            return_value=authored,
        ) as author:
            result = author_reduced_pose_from_runtime_cache(
                _Converter(), StaticCache(), _pose(3, (0, 2), (0, 2)), ["smile"]
            )

        self.assertTrue(result.success)
        self.assertEqual(author.call_args.kwargs["static_values"], {})

    def test_static_settable_probe_requires_explicit_true(self):
        maya_module = types.ModuleType("maya")
        cmds_module = types.ModuleType("maya.cmds")
        calls = []

        def get_attr(target, **kwargs):
            calls.append((target, kwargs))
            return kwargs.get("settable") is True

        cmds_module.getAttr = get_attr
        maya_module.cmds = cmds_module
        with mock.patch.dict(sys.modules, {"maya": maya_module, "maya.cmds": cmds_module}):
            self.assertTrue(_static_target_is_settable("joint.translateY"))
            self.assertEqual(calls, [("joint.translateY", {"settable": True})])

if __name__ == "__main__":
    unittest.main()
