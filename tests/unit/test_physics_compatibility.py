"""Tests for the legacy MMD constraint compatibility check."""

from types import SimpleNamespace

from mmd_tools.validation.physics_compatibility import has_legacy_soft_constraint_pattern


def _joint(body_a, body_b, limit=0.0, joint_type=0):
    return SimpleNamespace(
        joint_type=joint_type,
        rigid_body_a_index=body_a,
        rigid_body_b_index=body_b,
        translation_limit_min=(0.0, limit, 0.0),
        translation_limit_max=(0.0, limit, 0.0),
    )


def test_detects_only_supported_legacy_pattern():
    unbound_dynamic = SimpleNamespace(related_bone_index=-1, physics_mode=1)
    bone_bound = SimpleNamespace(related_bone_index=0, physics_mode=1)
    model = SimpleNamespace(
        rigid_bodies=[unbound_dynamic, bone_bound],
        joints=[_joint(0, 1, 0.3)],
    )
    assert has_legacy_soft_constraint_pattern(model)

    model.joints = [
        _joint(0, 1),
        _joint(1, 1, 0.3),
        _joint(0, 99, 0.3),
        _joint(0, 1, 0.3, joint_type=1),
    ]
    assert not has_legacy_soft_constraint_pattern(model)
