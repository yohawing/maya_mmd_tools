"""Unit tests for physics_descriptor: validation, identity hash, and transform math."""

import math
import unittest

from pathlib import Path

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.physics_descriptor import (
    _body_from_bone,
    _bone_from_body,
    _quat_from_euler_zyx,
    _quat_mul,
    build_descriptors_from_pmx,
    validate_joint_fields,
    validate_rigid_body_fields,
)
from mmd_tools.core.native.mmd_anim_runtime_types import (
    MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
    MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC_BONE,
    MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_UNKNOWN,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "physics" / "test_hair_physics.pmx"


class TestQuaternionMath(unittest.TestCase):
    """Verify quaternion helpers match expected results."""

    def test_identity_euler(self):
        q = _quat_from_euler_zyx(0.0, 0.0, 0.0)
        self.assertAlmostEqual(q[3], 1.0, places=10)
        self.assertAlmostEqual(q[0], 0.0, places=10)
        self.assertAlmostEqual(q[1], 0.0, places=10)
        self.assertAlmostEqual(q[2], 0.0, places=10)

    def test_90deg_x_rotation(self):
        q = _quat_from_euler_zyx(math.pi / 2, 0.0, 0.0)
        self.assertAlmostEqual(q[0], math.sin(math.pi / 4), places=7)
        self.assertAlmostEqual(q[3], math.cos(math.pi / 4), places=7)
        self.assertAlmostEqual(q[1], 0.0, places=10)
        self.assertAlmostEqual(q[2], 0.0, places=10)

    def test_quat_mul_identity(self):
        identity = (0.0, 0.0, 0.0, 1.0)
        q = _quat_from_euler_zyx(0.3, 0.5, 0.7)
        r = _quat_mul(q, identity)
        for i in range(4):
            self.assertAlmostEqual(r[i], q[i], places=10)


class TestBodyFromBone(unittest.TestCase):
    """Verify body_from_bone / bone_from_body transform computations."""

    def test_identity_when_colocated(self):
        pos = (1.0, 2.0, 3.0)
        bfb_pos, bfb_rot = _body_from_bone(pos, (0.0, 0.0, 0.0), pos)
        self.assertAlmostEqual(bfb_pos[0], 0.0, places=10)
        self.assertAlmostEqual(bfb_pos[1], 0.0, places=10)
        self.assertAlmostEqual(bfb_pos[2], 0.0, places=10)
        self.assertAlmostEqual(bfb_rot[3], 1.0, places=10)

    def test_roundtrip_inverse(self):
        body_pos = (1.0, 2.0, 3.0)
        body_rot = (0.3, 0.5, 0.7)
        bone_pos = (0.5, 1.5, 2.5)
        bfb_pos, bfb_rot = _body_from_bone(body_pos, body_rot, bone_pos)
        bfr_pos, bfr_rot = _bone_from_body(body_pos, body_rot, bone_pos)
        composed_rot = _quat_mul(bfb_rot, bfr_rot)
        self.assertAlmostEqual(abs(composed_rot[3]), 1.0, places=6)
        self.assertAlmostEqual(composed_rot[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(composed_rot[1], 0.0, delta=1e-6)
        self.assertAlmostEqual(composed_rot[2], 0.0, delta=1e-6)


class TestValidation(unittest.TestCase):
    """Verify descriptor validation catches invalid data."""

    def test_valid_rigid_body_passes(self):
        errors = validate_rigid_body_fields(
            0, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            1.0, 0.5, 0.5, 0.5, 0.5, 1, 0xFFFF, 0, 0,
        )
        self.assertEqual(errors, [])

    def test_nan_mass_rejected(self):
        errors = validate_rigid_body_fields(
            0, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            float("nan"), 0.5, 0.5, 0.5, 0.5, 1, 0xFFFF, 0, 0,
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].field, "mass")

    def test_invalid_shape_rejected(self):
        errors = validate_rigid_body_fields(
            0, 99, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            1.0, 0.5, 0.5, 0.5, 0.5, 1, 0xFFFF, 0, 0,
        )
        self.assertTrue(any(e.field == "shape" for e in errors))

    def test_unknown_mode_rejected(self):
        errors = validate_rigid_body_fields(
            0, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            1.0, 0.5, 0.5, 0.5, 0.5, 1, 0xFFFF, 0,
            MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_UNKNOWN,
        )
        self.assertTrue(any(e.field == "mode" for e in errors))

    def test_valid_joint_passes(self):
        errors = validate_joint_fields(
            0, MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
            0, 1, 2,
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
        )
        self.assertEqual(errors, [])

    def test_out_of_range_body_index_rejected(self):
        errors = validate_joint_fields(
            0, MMD_RUNTIME_PHYSICS_JOINT_KIND_GENERIC_6DOF_SPRING,
            5, 1, 2,
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
        )
        self.assertTrue(any(e.field == "rigidbody_a" for e in errors))

    def test_unsupported_joint_kind_rejected(self):
        errors = validate_joint_fields(
            0, MMD_RUNTIME_PHYSICS_JOINT_KIND_UNSUPPORTED,
            0, 1, 2,
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
        )
        self.assertTrue(any(e.field == "kind" for e in errors))


class TestBuildDescriptorsFromPmx(unittest.TestCase):
    """Build descriptors from the hair physics fixture."""

    @classmethod
    def setUpClass(cls):
        cls.pmx = parse_pmx_file(str(FIXTURE_PATH))

    def test_descriptor_count_matches_pmx(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        self.assertEqual(len(desc_set.rigid_bodies), 16)
        self.assertEqual(len(desc_set.joints), 19)

    def test_valid_joints_pass_validation(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        valid_errors = [e for e in desc_set.validation_errors
                        if e.kind == "joint" and e.field not in ("rigidbody_a", "rigidbody_b")]
        self.assertEqual(valid_errors, [])

    def test_invalid_joint_refs_detected(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        invalid_ref_errors = [
            e for e in desc_set.validation_errors
            if e.kind == "joint" and e.field in ("rigidbody_a", "rigidbody_b")
        ]
        self.assertGreater(len(invalid_ref_errors), 0)

    def test_identity_hash_is_deterministic(self):
        desc_set1 = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        desc_set2 = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        self.assertEqual(desc_set1.identity_hash, desc_set2.identity_hash)
        self.assertEqual(len(desc_set1.identity_hash), 64)

    def test_static_body_has_zero_mass(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        static_body = desc_set.rigid_bodies[0]
        self.assertEqual(static_body.mode, 0)

    def test_dynamic_bone_bodies_have_capsule_shape(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        for rb in desc_set.rigid_bodies[1:8]:
            self.assertEqual(rb.shape, 2)
            self.assertEqual(rb.mode, MMD_RUNTIME_PHYSICS_RIGIDBODY_MODE_DYNAMIC_BONE)

    def test_body_from_bone_finite(self):
        desc_set = build_descriptors_from_pmx(
            self.pmx.rigid_bodies, self.pmx.joints, self.pmx.bones,
        )
        for rb in desc_set.rigid_bodies:
            for i in range(3):
                self.assertTrue(math.isfinite(rb.body_from_bone_position_xyz[i]))
                self.assertTrue(math.isfinite(rb.bone_from_body_position_xyz[i]))
            for i in range(4):
                self.assertTrue(math.isfinite(rb.body_from_bone_rotation_xyzw[i]))
                self.assertTrue(math.isfinite(rb.bone_from_body_rotation_xyzw[i]))


if __name__ == "__main__":
    unittest.main()
