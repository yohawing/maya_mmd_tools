"""Pure tests for static MMD Control Rig basis quaternion metadata."""

import math
import unittest

from mmd_tools.core.mmd_control_rig_basis import (
    BASIS_SOURCE_IDENTITY,
    BASIS_SOURCE_PMX_TAIL,
    MmdControlRigBasisError,
    basis_from_shape_rotation,
    bone_to_control,
    control_to_bone,
    quaternion_conjugate,
    quaternion_from_shape_rotation,
    quaternion_inverse,
    quaternion_multiply,
    validate_basis_record,
)


class TestMmdControlRigBasis(unittest.TestCase):
    def test_identity_rotation_persists_explicit_identity_source(self):
        basis = basis_from_shape_rotation(None)

        self.assertEqual(basis.source, BASIS_SOURCE_IDENTITY)
        self.assertEqual(basis.quaternion, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(
            basis.to_dict(),
            {"quaternion": [0.0, 0.0, 0.0, 1.0], "source": BASIS_SOURCE_IDENTITY},
        )

    def test_shortest_arc_is_normalized_xyzw_and_tail_sourced(self):
        basis = basis_from_shape_rotation(((0.0, 1.0, 0.0), 0.0, 1.0))

        self.assertEqual(basis.source, BASIS_SOURCE_PMX_TAIL)
        self.assertAlmostEqual(basis.quaternion[1], math.sqrt(0.5), places=12)
        self.assertAlmostEqual(basis.quaternion[3], math.sqrt(0.5), places=12)
        self.assertAlmostEqual(sum(value * value for value in basis.quaternion), 1.0)
        self.assertGreaterEqual(basis.quaternion[3], 0.0)

    def test_opposite_axis_uses_stable_positive_tie_break(self):
        positive = quaternion_from_shape_rotation(((0.0, 1.0, 0.0), -1.0, 0.0))
        negative = quaternion_from_shape_rotation(((0.0, -1.0, 0.0), -1.0, 0.0))

        self.assertEqual(positive, (0.0, 1.0, 0.0, 0.0))
        self.assertEqual(negative, positive)

    def test_malformed_or_nonfinite_basis_fails_closed(self):
        with self.assertRaises(MmdControlRigBasisError):
            quaternion_from_shape_rotation(((0.0, 0.0, 0.0), 1.0, 0.0))
        with self.assertRaises(MmdControlRigBasisError):
            quaternion_from_shape_rotation(((0.0, 1.0, 0.0), float("nan"), 0.0))
        with self.assertRaises(MmdControlRigBasisError):
            validate_basis_record(
                {"source": BASIS_SOURCE_PMX_TAIL, "quaternion": [0.0, 0.0, 0.0]}
            )

    def test_persisted_record_is_validated_without_changing_source(self):
        record = {
            "source": BASIS_SOURCE_PMX_TAIL,
            "quaternion": [0.0, -math.sqrt(0.5), 0.0, -math.sqrt(0.5)],
        }

        basis = validate_basis_record(record)

        self.assertEqual(basis.source, BASIS_SOURCE_PMX_TAIL)
        self.assertEqual(basis.quaternion[1], math.sqrt(0.5))
        self.assertEqual(basis.quaternion[3], math.sqrt(0.5))

    def test_identity_basis_is_exact_and_inverse_matches_conjugate(self):
        q = (0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5))

        self.assertEqual(bone_to_control(q, (0.0, 0.0, 0.0, 1.0)), q)
        self.assertEqual(control_to_bone(q, (0.0, 0.0, 0.0, 1.0)), q)
        self.assertEqual(quaternion_inverse(q), quaternion_conjugate(q))
        self.assertEqual(quaternion_multiply(q, quaternion_inverse(q)), (0.0, 0.0, 0.0, 1.0))

    def test_non_commuting_ninety_degree_basis_conjugation(self):
        basis = quaternion_from_shape_rotation(((0.0, 0.0, 1.0), 0.0, 1.0))
        bone = quaternion_from_shape_rotation(((1.0, 0.0, 0.0), 0.0, 1.0))

        control = bone_to_control(bone, basis)
        expected = (0.0, -math.sqrt(0.5), 0.0, math.sqrt(0.5))

        for actual, target in zip(control, expected):
            self.assertAlmostEqual(actual, target, places=12)
        self.assertNotEqual(control, quaternion_multiply(basis, bone))

    def test_conjugation_roundtrips_both_directions(self):
        basis = quaternion_from_shape_rotation(((0.0, 1.0, 0.0), 0.0, 1.0))
        bone = quaternion_from_shape_rotation(((1.0, 1.0, 0.0), 0.0, 1.0))

        control = bone_to_control(bone, basis)
        restored_bone = control_to_bone(control, basis)
        restored_control = bone_to_control(restored_bone, basis)

        for actual, target in zip(restored_bone, bone):
            self.assertAlmostEqual(actual, target, places=12)
        for actual, target in zip(restored_control, control):
            self.assertAlmostEqual(actual, target, places=12)

    def test_equivalent_opposite_sign_quaternions_canonicalize_identically(self):
        q = (0.0, 1.0, 0.0, 0.0)
        negative = tuple(-component for component in q)

        self.assertEqual(quaternion_multiply(q, (0.0, 0.0, 0.0, 1.0)), q)
        self.assertEqual(quaternion_multiply(negative, (0.0, 0.0, 0.0, 1.0)), q)
        self.assertEqual(bone_to_control(q, negative), (0.0, 1.0, 0.0, 0.0))

    def test_quaternion_operations_fail_closed_for_malformed_inputs(self):
        malformed = (
            None,
            (0.0, 0.0, 0.0, 0.0),
            (float("nan"), 0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
        )
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(MmdControlRigBasisError):
                    quaternion_multiply(value, (0.0, 0.0, 0.0, 1.0))
                with self.assertRaises(MmdControlRigBasisError):
                    bone_to_control((0.0, 0.0, 0.0, 1.0), value)


if __name__ == "__main__":
    unittest.main()
