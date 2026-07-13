"""Unit tests for the bundled hair physics PMX fixture."""

from pathlib import Path
import unittest

from mmd_tools.core.mmd_parser import parse_pmx_file


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


class TestHairPhysicsFixture(unittest.TestCase):
    """Verify the small PMX fixture keeps its intended hair physics shape."""

    @classmethod
    def setUpClass(cls):
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))

    def test_fixture_has_compact_hair_physics_topology(self):
        self.assertEqual(len(self.pmx.vertices), 442)
        self.assertEqual(len(self.pmx.bones), 21)
        self.assertEqual(len(self.pmx.rigid_bodies), 16)
        self.assertEqual(len(self.pmx.joints), 19)

    def test_rigid_bodies_are_bound_to_left_and_right_hair_bones(self):
        rigid_bodies = self.pmx.rigid_bodies
        bones = self.pmx.bones

        static_anchors = [rigid_bodies[0], rigid_bodies[8]]
        self.assertEqual([body.name for body in static_anchors], ["右髪１", "左髪１"])
        self.assertEqual([body.physics_mode for body in static_anchors], [0, 0])
        self.assertEqual([body.shape_type for body in static_anchors], [0, 0])

        dynamic_bodies = rigid_bodies[1:8] + rigid_bodies[9:16]
        self.assertEqual(len(dynamic_bodies), 14)
        self.assertTrue(all(body.physics_mode == 2 for body in dynamic_bodies))
        self.assertTrue(all(body.shape_type == 2 for body in dynamic_bodies))

        related_bone_names = [bones[body.related_bone_index].name for body in dynamic_bodies]
        self.assertEqual(
            related_bone_names,
            [
                "右髪２",
                "右髪３",
                "右髪４",
                "右髪５",
                "右髪６",
                "右髪７",
                "右髪８",
                "左髪２",
                "左髪３",
                "左髪４",
                "左髪５",
                "左髪６",
                "左髪７",
                "左髪８",
            ],
        )

    def test_joints_include_valid_hair_chains_and_invalid_placeholders(self):
        valid_pairs = [
            (joint.rigid_body_a_index, joint.rigid_body_b_index)
            for joint in self.pmx.joints
            if joint.rigid_body_a_index >= 0 and joint.rigid_body_b_index >= 0
        ]
        invalid_joints = [
            joint
            for joint in self.pmx.joints
            if joint.rigid_body_a_index < 0 or joint.rigid_body_b_index < 0
        ]

        self.assertEqual(valid_pairs, [(i, i + 1) for i in range(7)] + [(i, i + 1) for i in range(8, 15)])
        self.assertEqual([joint.name for joint in invalid_joints], ["右胸１", "右胸２", "左胸１", "左胸２", "前髪２"])


if __name__ == "__main__":
    unittest.main()
