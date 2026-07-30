"""Pure unit coverage for MMD Control Rig channel policy derivation."""

import unittest

from mmd_tools.core.mmd_control_rig_analyzer import MmdControlRigBoneBinding
from mmd_tools.core.mmd_control_rig_channels import (
    ALL_CHANNELS,
    ROTATE_CHANNELS,
    TRANSLATE_CHANNELS,
    derive_mmd_control_rig_channel_policy,
    union_mmd_control_rig_channel_policies,
)
from mmd_tools.core.pmx_data.bone import PmxBoneFlag


def _binding(
    input_kind,
    authored_plugs,
    *,
    blocked=False,
    fixed_axis=None,
    pmx_flags=0,
):
    return MmdControlRigBoneBinding(
        joint="|model|joint",
        mmd_name="",
        bone_index=0,
        pmx_flags=pmx_flags,
        input_kind=input_kind,
        authored_plugs=tuple(authored_plugs),
        blockers=("blocked",) if blocked else (),
        fixed_axis=fixed_axis,
    )


class TestMmdControlRigChannels(unittest.TestCase):
    def test_only_fixed_axis_twists_expose_roll(self):
        fixed = _binding(
            "append_base",
            ("append.baseRotate",),
            fixed_axis=(1.0, 0.0, 0.0),
            pmx_flags=int(PmxBoneFlag.AXIS_FIXED),
        )
        free = _binding("append_base", ("append.baseRotate",))

        fixed_policy = derive_mmd_control_rig_channel_policy(
            "left_arm_twist",
            fixed,
        )
        free_policy = derive_mmd_control_rig_channel_policy(
            "left_arm_twist",
            free,
        )

        self.assertEqual(fixed_policy.keyable_channels, ("rotateZ",))
        self.assertEqual(
            fixed_policy.passthrough_channels,
            ("rotateX", "rotateY"),
        )
        self.assertEqual(free_policy.keyable_channels, ROTATE_CHANNELS)
        self.assertEqual(free_policy.passthrough_channels, ())

    def test_fk_and_finger_roles_are_rotate_only(self):
        binding = _binding(
            "direct_channel",
            ("|model|joint.translate", "|model|joint.rotate"),
        )

        for role in ("left_arm", "right_knee", "left_index_1"):
            with self.subTest(role=role):
                policy = derive_mmd_control_rig_channel_policy(role, binding)
                self.assertEqual(policy.allowed_families, ("rotate",))
                self.assertEqual(policy.keyable_channels, ROTATE_CHANNELS)
                self.assertEqual(policy.channel_box_channels, ())
                self.assertEqual(
                    policy.locked_channels,
                    tuple(channel for channel in ALL_CHANNELS if channel not in ROTATE_CHANNELS),
                )

    def test_ik_and_center_roles_follow_authored_route(self):
        binding = _binding(
            "ik_controller",
            ("|model|accum.baseTranslate", "|model|accum.baseRotate"),
        )

        for role in ("center", "left_foot_ik", "right_foot_ik_parent"):
            with self.subTest(role=role):
                policy = derive_mmd_control_rig_channel_policy(role, binding)
                self.assertEqual(policy.allowed_families, ("translate", "rotate"))
                self.assertEqual(
                    policy.keyable_channels,
                    TRANSLATE_CHANNELS + ROTATE_CHANNELS,
                )

    def test_ik_link_input_exposes_rotation_only(self):
        binding = _binding(
            "ik_link_input",
            ("solver.inputRotate[2].inputRotateElementX",),
        )

        policy = derive_mmd_control_rig_channel_policy("left_leg", binding)

        self.assertEqual(policy.allowed_families, ("rotate",))
        self.assertEqual(policy.keyable_channels, ("rotateX",))

    def test_partial_authored_route_exposes_only_its_axis(self):
        binding = _binding("direct_channel", ("|model|joint.translateX",))

        policy = derive_mmd_control_rig_channel_policy("center", binding)

        self.assertEqual(policy.allowed_families, ("translate",))
        self.assertEqual(policy.keyable_channels, ("translateX",))
        self.assertIn("translateY", policy.locked_channels)
        self.assertIn("translateZ", policy.locked_channels)

    def test_malformed_or_blocked_routes_fail_closed(self):
        cases = (
            ("center", _binding("unsupported", ("joint.translate",))),
            ("center", _binding("direct_channel", ("joint.custom",))),
            ("center", _binding("direct_channel", ("joint.translate",), blocked=True)),
            ("unknown_role", _binding("direct_channel", ("joint.translate",))),
        )
        for role, binding in cases:
            with self.subTest(role=role, binding=binding):
                policy = derive_mmd_control_rig_channel_policy(role, binding)
                self.assertEqual(policy.allowed_families, ())
                self.assertEqual(policy.keyable_channels, ())
                self.assertEqual(policy.channel_box_channels, ())
                self.assertEqual(policy.locked_channels, ALL_CHANNELS)

    def test_alias_policy_unions_only_valid_exposed_channels(self):
        translate = derive_mmd_control_rig_channel_policy(
            "center",
            _binding("direct_channel", ("joint.translateX",)),
        )
        rotate = derive_mmd_control_rig_channel_policy(
            "center",
            _binding("direct_channel", ("joint.rotateZ",)),
        )

        policy = union_mmd_control_rig_channel_policies((translate, rotate))

        self.assertEqual(policy.keyable_channels, ("translateX", "rotateZ"))
        self.assertEqual(policy.allowed_families, ("translate", "rotate"))

    def test_alias_union_fails_closed_for_empty_contributor(self):
        valid = derive_mmd_control_rig_channel_policy(
            "center",
            _binding("direct_channel", ("joint.translate",)),
        )
        closed = derive_mmd_control_rig_channel_policy(
            "center",
            _binding("unsupported", ("joint.rotate",)),
        )

        policy = union_mmd_control_rig_channel_policies((valid, closed))

        self.assertEqual(policy.keyable_channels, ())
        self.assertEqual(policy.locked_channels, ALL_CHANNELS)


if __name__ == "__main__":
    unittest.main()
