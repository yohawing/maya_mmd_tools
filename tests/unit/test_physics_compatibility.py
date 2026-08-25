"""Tests for the narrow legacy MMD constraint compatibility warning."""

from types import SimpleNamespace

from mmd_tools.validation.physics_compatibility import find_legacy_soft_constraint_warnings


def _body(name, bone_index, mode):
    return SimpleNamespace(name=name, related_bone_index=bone_index, physics_mode=mode)


def _joint(name, body_a, body_b, lower=(0.0, 0.0, 0.0), upper=(0.0, 0.0, 0.0), joint_type=0):
    return SimpleNamespace(
        name=name,
        joint_type=joint_type,
        rigid_body_a_index=body_a,
        rigid_body_b_index=body_b,
        translation_limit_min=lower,
        translation_limit_max=upper,
    )


def test_warns_once_for_affected_connected_component():
    model = SimpleNamespace(
        bones=[SimpleNamespace(name="anchor_bone"), SimpleNamespace(name="breast_bone")],
        rigid_bodies=[
            _body("anchor", 0, 0),
            _body("unbound_a", -1, 1),
            _body("unbound_b", -1, 2),
            _body("driven", 1, 1),
        ],
        joints=[
            _joint("anchor_link", 0, 1),
            _joint("locked_a", 1, 3, (0.0, 0.3, 0.0), (0.0, 0.3, 0.0)),
            _joint("locked_b", 2, 1, (0.0, 0.33, 0.0), (0.0, 0.33, 0.0)),
        ],
    )

    warnings = find_legacy_soft_constraint_warnings(model)

    assert len(warnings) == 1
    assert warnings[0]["code"] == "legacy_soft_constraint_behavior"
    assert warnings[0]["joint_names"] == ["locked_a", "locked_b"]
    assert warnings[0]["affected_bone_indices"] == [1]
    assert warnings[0]["affected_bone_names"] == ["breast_bone"]
    assert warnings[0]["fallback"] == "none"


def test_does_not_warn_for_zero_locked_translation():
    model = SimpleNamespace(
        rigid_bodies=[_body("anchor", 0, 0), _body("unbound", -1, 1), _body("driven", 1, 1)],
        joints=[
            _joint("anchor_link", 0, 1),
            _joint("zero_lock", 1, 2),
        ],
    )

    assert find_legacy_soft_constraint_warnings(model) == []


def test_does_not_warn_for_nonfinite_locked_translation():
    model = SimpleNamespace(
        rigid_bodies=[_body("anchor", 0, 0), _body("unbound", -1, 1), _body("driven", 1, 1)],
        joints=[
            _joint("anchor_link", 0, 1),
            _joint("nonfinite_lock", 1, 2, (0.0, float("inf"), 0.0), (0.0, float("inf"), 0.0)),
        ],
    )

    assert find_legacy_soft_constraint_warnings(model) == []


def test_does_not_warn_without_unbound_dynamic_body():
    model = SimpleNamespace(
        rigid_bodies=[_body("anchor", 0, 0), _body("driven", 1, 1)],
        joints=[_joint("locked", 0, 1, (0.0, 0.3, 0.0), (0.0, 0.3, 0.0))],
    )

    assert find_legacy_soft_constraint_warnings(model) == []


def test_does_not_warn_without_bone_follow_anchor():
    model = SimpleNamespace(
        rigid_bodies=[_body("unbound", -1, 1), _body("driven", 1, 1)],
        joints=[_joint("locked", 0, 1, (0.0, 0.3, 0.0), (0.0, 0.3, 0.0))],
    )

    assert find_legacy_soft_constraint_warnings(model) == []


def test_ignores_invalid_body_indices_and_unsupported_joint_types():
    model = SimpleNamespace(
        rigid_bodies=[_body("anchor", 0, 0), _body("unbound", -1, 1), _body("driven", 1, 1)],
        joints=[
            _joint("invalid", 1, 99, (0.0, 0.3, 0.0), (0.0, 0.3, 0.0)),
            _joint("unsupported", 1, 2, (0.0, 0.3, 0.0), (0.0, 0.3, 0.0), joint_type=1),
            _joint("anchor_link", 0, 1),
        ],
    )

    assert find_legacy_soft_constraint_warnings(model) == []
