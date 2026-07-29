"""Pure tests for static MMD Control Rig basis quaternion metadata."""

import math
import unittest

from mmd_tools.core.mmd_control_rig_basis import (
    BASIS_SOURCE_IDENTITY,
    BASIS_SOURCE_PMX_TAIL,
    MmdControlRigBasisError,
    basis_from_shape_rotation,
    quaternion_from_shape_rotation,
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


if __name__ == "__main__":
    unittest.main()

