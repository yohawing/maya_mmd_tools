"""Pure unit coverage for MMD Control Rig channel policy derivation."""

import unittest

from mmd_tools.core.mmd_control_rig_analyzer import MmdControlRigBoneBinding
from mmd_tools.core.mmd_control_rig_channels import (
    ALL_CHANNELS,
    ROTATE_CHANNELS,
    TRANSLATE_CHANNELS,
    derive_mmd_control_rig_channel_policy,
)


def _binding(input_kind, authored_plugs, *, blocked=False):
    return MmdControlRigBoneBinding(
        joint="|model|joint",
        mmd_name="",
        bone_index=0,
        pmx_flags=0,
        input_kind=input_kind,
        authored_plugs=tuple(authored_plugs),
        blockers=("blocked",) if blocked else (),
    )


class TestMmdControlRigChannels(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
