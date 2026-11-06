"""Independent geometric controls for the synthetic skirt/leg diagnostic."""

import math
import unittest

from tools.probes.skirt_leg_fixture import make_fixture, sphere_capsule_gap


class TestSkirtLegFixture(unittest.TestCase):
    def test_sphere_capsule_side_end_and_overlap(self):
        identity = (0, 0, 0, 1)
        capsule = ((0, 0, 0), identity)
        for center, expected in (((2, 0, 0), 1.05), ((0, 3, 0), 0.55), ((0, 0, 0), -0.95)):
            self.assertAlmostEqual(sphere_capsule_gap((center, identity), capsule), expected)

    def test_rotation_and_translation_preserve_gap(self):
        s = math.sqrt(0.5)
        # Rotating Y onto -X makes (-3, 0, 0) an end-cap query.
        capsule = ((10, 4, 2), (0, 0, s, s))
        self.assertAlmostEqual(sphere_capsule_gap(((7, 4, 2), (0, 0, 0, 1)), capsule), 0.55)

    def test_negative_control_changes_only_masks(self):
        on, motion = make_fixture(True)
        off, other_motion = make_fixture(False)
        self.assertEqual(len(on.rigid_bodies), 4)
        for a, b in zip(on.rigid_bodies, off.rigid_bodies):
            expected = dict(vars(a), collision_mask=0)
            self.assertEqual(expected, vars(b))
        self.assertEqual([vars(j) for j in on.joints], [vars(j) for j in off.joints])
        self.assertEqual([vars(k) for k in motion.bone_frames], [vars(k) for k in other_motion.bone_frames])
        self.assertEqual(motion.bone_frames[0].position, (0, 0, 0))
        self.assertEqual(motion.bone_frames[60].position, (0, 0, 2))
        self.assertEqual(motion.bone_frames[150].position, (0, 0, 0))

    def test_only_leg_skirt_pairs_are_enabled(self):
        pmx, _ = make_fixture()
        enabled = []
        for i, a in enumerate(pmx.rigid_bodies):
            for j, b in enumerate(pmx.rigid_bodies[:i]):
                if a.collision_mask & (1 << b.group) and b.collision_mask & (1 << a.group):
                    enabled.append((j, i))
        self.assertEqual(enabled, [(1, 2), (1, 3)])


if __name__ == "__main__":
    unittest.main()
