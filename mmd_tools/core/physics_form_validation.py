"""Parse and validate PhysicsTab values without importing Maya or Qt."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RigidBodyFormValues:
    name: str
    name_english: str
    shape_type: int
    physics_mode: int
    related_bone_index: int
    shape_size: tuple[float, float, float]
    pmx_position: tuple[float, float, float]
    pmx_rotation_degrees: tuple[float, float, float]
    collision_group: int
    collision_mask: int
    mass: float
    linear_damping: float
    angular_damping: float
    restitution: float
    friction: float


@dataclass(frozen=True)
class JointFormValues:
    name: str
    name_english: str
    joint_type: int
    rigid_body_a_index: int
    rigid_body_b_index: int
    pmx_position: tuple[float, float, float]
    pmx_rotation_degrees: tuple[float, float, float]
    linear_constraint_states: tuple[int, int, int]
    angular_constraint_states: tuple[int, int, int]
    translation_limit_min: tuple[float, float, float]
    translation_limit_max: tuple[float, float, float]
    rotation_limit_min_degrees: tuple[float, float, float]
    rotation_limit_max_degrees: tuple[float, float, float]
    spring_translation: tuple[float, float, float]
    spring_rotation: tuple[float, float, float]
    spring_translation_enabled: tuple[bool, bool, bool]
    spring_rotation_enabled: tuple[bool, bool, bool]


class PhysicsFormValidationError(ValueError):
    """Structured validation failure suitable for localized UI rendering."""

    def __init__(self, field_key: str, message_key: str, **params):
        super().__init__(f"{field_key}: {message_key}")
        self.field_key = field_key
        self.message_key = message_key
        self.params = params


def parse_rigid_body_form(values: Mapping[str, Any]) -> RigidBodyFormValues:
    """Return typed rigid-body values or raise a structured validation error."""
    return RigidBodyFormValues(
        name=_text(values, "name"),
        name_english=_text(values, "name_english"),
        shape_type=_integer(values, "shape", minimum=0, maximum=2),
        physics_mode=_integer(values, "physics_mode", minimum=0, maximum=2),
        related_bone_index=_integer(values, "related_bone", minimum=-1),
        shape_size=_number_vector(values, "shape_size", minimum=0.0),
        pmx_position=_number_vector(values, "pmx_position"),
        pmx_rotation_degrees=_number_vector(values, "pmx_rotation_degrees"),
        collision_group=_integer(values, "collision_group", minimum=0, maximum=15),
        collision_mask=_integer(values, "collision_mask", minimum=0, maximum=0xFFFF),
        mass=_number(values, "mass", minimum=0.0),
        # Backend min/max conversion remains an adapter/clamp concern.
        linear_damping=_number(values, "linear_damping"),
        angular_damping=_number(values, "angular_damping"),
        restitution=_number(values, "restitution"),
        friction=_number(values, "friction"),
    )


def parse_joint_form(values: Mapping[str, Any]) -> JointFormValues:
    """Return typed joint values or raise a structured validation error."""
    translation_limit_min = _number_vector(values, "translation_limit_min")
    translation_limit_max = _number_vector(values, "translation_limit_max")
    rotation_limit_min_degrees = _number_vector(values, "rotation_limit_min_degrees")
    rotation_limit_max_degrees = _number_vector(values, "rotation_limit_max_degrees")
    _validate_componentwise_lower_limits(
        translation_limit_min,
        translation_limit_max,
        "translation_limit_min",
    )
    _validate_componentwise_lower_limits(
        rotation_limit_min_degrees,
        rotation_limit_max_degrees,
        "rotation_limit_min_degrees",
    )
    return JointFormValues(
        name=_text(values, "name"),
        name_english=_text(values, "name_english"),
        joint_type=_integer(values, "joint_type", minimum=0, maximum=6),
        rigid_body_a_index=_integer(values, "rigid_body_a", minimum=-1),
        rigid_body_b_index=_integer(values, "rigid_body_b", minimum=-1),
        pmx_position=_number_vector(values, "pmx_position"),
        pmx_rotation_degrees=_number_vector(values, "pmx_rotation_degrees"),
        linear_constraint_states=_integer_vector(
            values,
            "linear_constraint_states",
            minimum=0,
            maximum=2,
        ),
        angular_constraint_states=_integer_vector(
            values,
            "angular_constraint_states",
            minimum=0,
            maximum=2,
        ),
        translation_limit_min=translation_limit_min,
        translation_limit_max=translation_limit_max,
        rotation_limit_min_degrees=rotation_limit_min_degrees,
        rotation_limit_max_degrees=rotation_limit_max_degrees,
        spring_translation=_number_vector(values, "spring_translation"),
        spring_rotation=_number_vector(values, "spring_rotation"),
        spring_translation_enabled=_bool_vector(values, "spring_translation_enabled"),
        spring_rotation_enabled=_bool_vector(values, "spring_rotation_enabled"),
    )


def _raw(values: Mapping[str, Any], field_key: str):
    if field_key not in values:
        raise PhysicsFormValidationError(field_key, "physics_validation_required")
    return values[field_key]


def _text(values: Mapping[str, Any], field_key: str) -> str:
    value = _raw(values, field_key)
    if not isinstance(value, str):
        raise PhysicsFormValidationError(field_key, "physics_validation_text")
    return value


def _integer(
    values: Mapping[str, Any],
    field_key: str,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    return _parse_integer(_raw(values, field_key), field_key, minimum=minimum, maximum=maximum)


def _parse_integer(value, field_key: str, *, minimum=None, maximum=None) -> int:
    if isinstance(value, bool):
        raise PhysicsFormValidationError(field_key, "physics_validation_integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise PhysicsFormValidationError(field_key, "physics_validation_integer")
    if isinstance(value, float) and not value.is_integer():
        raise PhysicsFormValidationError(field_key, "physics_validation_integer")
    if isinstance(value, str) and str(parsed) != value.strip():
        raise PhysicsFormValidationError(field_key, "physics_validation_integer")
    _validate_range(parsed, field_key, minimum, maximum)
    return parsed


def _number(values: Mapping[str, Any], field_key: str, *, minimum: Optional[float] = None) -> float:
    return _parse_number(_raw(values, field_key), field_key, minimum=minimum)


def _parse_number(value, field_key: str, *, minimum=None) -> float:
    if isinstance(value, bool):
        raise PhysicsFormValidationError(field_key, "physics_validation_number")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise PhysicsFormValidationError(field_key, "physics_validation_number")
    if not math.isfinite(parsed):
        raise PhysicsFormValidationError(field_key, "physics_validation_finite")
    if minimum is not None and parsed < minimum:
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_minimum",
            minimum=minimum,
        )
    return parsed


def _integer_vector(values, field_key, *, minimum=None, maximum=None):
    parts = _vector_parts(_raw(values, field_key), field_key)
    return tuple(
        _parse_integer(part, field_key, minimum=minimum, maximum=maximum)
        for part in parts
    )


def _number_vector(values, field_key, *, minimum=None):
    parts = _vector_parts(_raw(values, field_key), field_key)
    return tuple(_parse_number(part, field_key, minimum=minimum) for part in parts)


def _bool_vector(values, field_key):
    parts = _vector_parts(_raw(values, field_key), field_key)
    return tuple(_parse_bool(part, field_key) for part in parts)


def _validate_componentwise_lower_limits(lower, upper, field_key: str) -> None:
    """Reject a lower limit that exceeds its matching upper-limit component."""
    for lower_value, upper_value in zip(lower, upper):
        if lower_value > upper_value:
            raise PhysicsFormValidationError(
                field_key,
                "physics_validation_maximum",
                maximum=upper_value,
            )


def _vector_parts(value, field_key):
    if isinstance(value, str):
        parts = tuple(part.strip() for part in value.split(","))
    elif isinstance(value, (tuple, list)):
        parts = tuple(value)
    else:
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_vector_length",
            count=3,
        )
    if len(parts) != 3 or any(part == "" for part in parts):
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_vector_length",
            count=3,
        )
    return parts


def _parse_bool(value, field_key):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip() in {"0", "1"}:
        return value.strip() == "1"
    raise PhysicsFormValidationError(field_key, "physics_validation_bool")


def _validate_range(value, field_key, minimum, maximum):
    if minimum is not None and maximum is not None and not minimum <= value <= maximum:
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_range",
            minimum=minimum,
            maximum=maximum,
        )
    if minimum is not None and value < minimum:
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_minimum",
            minimum=minimum,
        )
    if maximum is not None and value > maximum:
        raise PhysicsFormValidationError(
            field_key,
            "physics_validation_maximum",
            maximum=maximum,
        )
