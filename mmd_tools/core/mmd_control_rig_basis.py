"""Pure math and metadata helpers for the static MMD control basis.

This module owns the static authoring basis used by MMD-native Control Rigs.
The basis is persisted as an ``xyzw`` quaternion and is deliberately usable
from both pure-Python conversion code and Maya matrix-node authoring.  The
builder stores it on the ``AIM_SPACE`` transform; motion conversion consumes
the same record rather than re-deriving a basis from curve CV geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional, Tuple


BASIS_SOURCE_PMX_TAIL = "pmx_tail"
BASIS_SOURCE_IDENTITY = "identity"
BASIS_SOURCES = frozenset({BASIS_SOURCE_PMX_TAIL, BASIS_SOURCE_IDENTITY})
IDENTITY_QUATERNION = (0.0, 0.0, 0.0, 1.0)
_EPSILON = 1.0e-10


class MmdControlRigBasisError(ValueError):
    """Raised when a static authoring basis is malformed or non-finite."""


@dataclass(frozen=True)
class MmdControlRigBasis:
    """Normalized xyzw quaternion and its deterministic source classification."""

    quaternion: Tuple[float, float, float, float]
    source: str

    def to_dict(self) -> Mapping[str, Any]:
        """Return the compact JSON-safe basis record."""

        return {
            "quaternion": list(self.quaternion),
            "source": self.source,
        }


def basis_from_shape_rotation(
    rotation: Optional[Tuple[Tuple[float, float, float], float, float]],
) -> MmdControlRigBasis:
    """Convert a shortest-arc display rotation into a normalized quaternion.

    ``None`` is the explicit identity basis used by roles without automatic
    CV orientation (including center/master/IK controls and LocalAxis bones).
    The returned quaternion uses xyzw order, prefers a non-negative ``w``,
    and applies a stable first-component tie-break for 180-degree rotations.
    """

    if rotation is None:
        return MmdControlRigBasis(IDENTITY_QUATERNION, BASIS_SOURCE_IDENTITY)
    quaternion = quaternion_from_shape_rotation(rotation)
    return MmdControlRigBasis(quaternion, BASIS_SOURCE_PMX_TAIL)


def quaternion_from_shape_rotation(
    rotation: Tuple[Tuple[float, float, float], float, float],
) -> Tuple[float, float, float, float]:
    """Return a finite normalized xyzw quaternion for a shortest-arc tuple."""

    try:
        axis, cosine, sine = rotation
        axis = tuple(float(value) for value in axis)
        cosine = float(cosine)
        sine = float(sine)
    except (TypeError, ValueError) as exc:
        raise MmdControlRigBasisError("invalid shortest-arc rotation") from exc
    if len(axis) != 3 or not all(math.isfinite(value) for value in (*axis, cosine, sine)):
        raise MmdControlRigBasisError("shortest-arc rotation must be finite")
    axis_length = math.sqrt(sum(value * value for value in axis))
    if not math.isfinite(axis_length) or axis_length <= _EPSILON:
        raise MmdControlRigBasisError("shortest-arc axis is degenerate")
    if abs(cosine) > 1.0 + _EPSILON or abs(sine) > 1.0 + _EPSILON:
        raise MmdControlRigBasisError("shortest-arc trigonometry is out of range")
    trig_length = math.sqrt(cosine * cosine + sine * sine)
    if not math.isfinite(trig_length) or abs(trig_length - 1.0) > 1.0e-6:
        raise MmdControlRigBasisError("shortest-arc trigonometry is not normalized")
    axis = tuple(value / axis_length for value in axis)
    cosine = max(-1.0, min(1.0, cosine))
    sine = max(-1.0, min(1.0, sine))

    # atan2 preserves the signed rotation represented by the existing display
    # helper while keeping the half-angle in a deterministic principal range.
    half_angle = 0.5 * math.atan2(sine, cosine)
    half_sine = math.sin(half_angle)
    half_cosine = math.cos(half_angle)
    quaternion = tuple(value * half_sine for value in axis) + (half_cosine,)
    return _normalize_and_canonicalize(quaternion)


def validate_basis_record(record: Mapping[str, Any]) -> MmdControlRigBasis:
    """Validate and normalize one persisted basis record fail-closed."""

    if not isinstance(record, Mapping):
        raise MmdControlRigBasisError("basis record must be an object")
    source = record.get("source")
    if source not in BASIS_SOURCES:
        raise MmdControlRigBasisError("basis source is unsupported")
    raw_quaternion = record.get("quaternion")
    if isinstance(raw_quaternion, (str, bytes)) or raw_quaternion is None:
        raise MmdControlRigBasisError("basis quaternion is missing")
    try:
        quaternion = tuple(float(value) for value in raw_quaternion)
    except (TypeError, ValueError) as exc:
        raise MmdControlRigBasisError("basis quaternion is invalid") from exc
    if len(quaternion) != 4:
        raise MmdControlRigBasisError("basis quaternion must have four components")
    canonical = _normalize_and_canonicalize(quaternion)
    if source == BASIS_SOURCE_IDENTITY and any(
        abs(actual - expected) > 1.0e-6
        for actual, expected in zip(canonical, IDENTITY_QUATERNION)
    ):
        raise MmdControlRigBasisError("identity basis must contain identity quaternion")
    return MmdControlRigBasis(canonical, str(source))


def quaternion_multiply(
    left: Tuple[float, float, float, float],
    right: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Multiply two finite xyzw quaternions and return canonical output."""

    x1, y1, z1, w1 = _coerce_quaternion(left, "left quaternion")
    x2, y2, z2, w2 = _coerce_quaternion(right, "right quaternion")
    result = (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )
    return _normalize_and_canonicalize(result)


def quaternion_conjugate(
    quaternion: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Return the normalized canonical conjugate of an xyzw quaternion."""

    x, y, z, w = _coerce_quaternion(quaternion, "quaternion")
    return _normalize_and_canonicalize((-x, -y, -z, w))


def quaternion_inverse(
    quaternion: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Return the normalized canonical inverse of an xyzw quaternion."""

    return quaternion_conjugate(quaternion)


def matrix_from_quaternion(
    quaternion: Tuple[float, float, float, float],
) -> Tuple[float, ...]:
    """Return a row-major 4x4 rotation matrix for a normalized xyzw quaternion.

    Maya's ``setAttr(..., type="matrix")`` and ``xform -matrix`` consume the
    same row-major sixteen-value payload.  Keeping this conversion here makes
    the persisted basis contract independent of Maya's optional API stubs.
    """

    x, y, z, w = _coerce_quaternion(quaternion, "quaternion")
    # Maya matrices use row-vector transform convention. This is the
    # transpose of the common column-vector quaternion formula and matches
    # ``maya.api.OpenMaya.MQuaternion.asMatrix()``.
    return (
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y + z * w),
        2.0 * (x * z - y * w),
        0.0,
        2.0 * (x * y - z * w),
        1.0 - 2.0 * (x * x + z * z),
        2.0 * (y * z + x * w),
        0.0,
        2.0 * (x * z + y * w),
        2.0 * (y * z - x * w),
        1.0 - 2.0 * (x * x + y * y),
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )


def conjugate_rotation(
    quaternion: Tuple[float, float, float, float],
    basis: Any,
) -> Tuple[float, float, float, float]:
    """Apply the authoring-basis conjugation to one local rotation.

    This named helper is intentionally explicit at conversion call sites:
    ``bone_to_control`` and ``control_to_bone`` remain the directional API,
    while callers that handle a live matrix can use the same contract without
    accidentally swapping the inverse order.
    """

    return bone_to_control(quaternion, basis)


def bone_to_control(
    q_bone: Tuple[float, float, float, float],
    basis: Any,
) -> Tuple[float, float, float, float]:
    """Convert a bone-local rotation into the persisted control basis.

    For basis quaternion ``B`` this applies ``inverse(B) * q_bone * B``.
    """

    bone = _coerce_quaternion(q_bone, "bone quaternion")
    basis_quaternion = _coerce_quaternion(basis, "basis quaternion")
    if basis_quaternion == IDENTITY_QUATERNION:
        return bone
    inverse = quaternion_inverse(basis_quaternion)
    return quaternion_multiply(quaternion_multiply(inverse, bone), basis_quaternion)


def control_to_bone(
    q_control: Tuple[float, float, float, float],
    basis: Any,
) -> Tuple[float, float, float, float]:
    """Convert a control-basis rotation back into bone-local space.

    For basis quaternion ``B`` this applies ``B * q_control * inverse(B)``.
    """

    control = _coerce_quaternion(q_control, "control quaternion")
    basis_quaternion = _coerce_quaternion(basis, "basis quaternion")
    if basis_quaternion == IDENTITY_QUATERNION:
        return control
    inverse = quaternion_inverse(basis_quaternion)
    return quaternion_multiply(quaternion_multiply(basis_quaternion, control), inverse)


def _coerce_quaternion(value: Any, description: str) -> Tuple[float, float, float, float]:
    if isinstance(value, MmdControlRigBasis):
        value = value.quaternion
    elif isinstance(value, Mapping):
        if "source" in value:
            value = validate_basis_record(value).quaternion
        else:
            value = value.get("quaternion")
    if isinstance(value, (str, bytes)) or value is None:
        raise MmdControlRigBasisError(f"{description} is invalid")
    try:
        quaternion = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise MmdControlRigBasisError(f"{description} is invalid") from exc
    if len(quaternion) != 4:
        raise MmdControlRigBasisError(f"{description} must have four components")
    try:
        return _normalize_and_canonicalize(quaternion)
    except MmdControlRigBasisError as exc:
        raise MmdControlRigBasisError(f"{description} is invalid") from exc


def _normalize_and_canonicalize(
    quaternion: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    if len(quaternion) != 4 or not all(math.isfinite(value) for value in quaternion):
        raise MmdControlRigBasisError("basis quaternion must be finite")
    length = math.sqrt(sum(value * value for value in quaternion))
    if not math.isfinite(length) or length <= _EPSILON:
        raise MmdControlRigBasisError("basis quaternion is degenerate")
    normalized = tuple(value / length for value in quaternion)
    # Prefer w >= 0.  At exactly 180 degrees w is zero, so choose the sign of
    # the first non-zero xyz component to avoid platform-dependent ties.
    sign = 1.0
    if normalized[3] < -_EPSILON:
        sign = -1.0
    elif abs(normalized[3]) <= _EPSILON:
        for value in normalized[:3]:
            if abs(value) > _EPSILON:
                if value < 0.0:
                    sign = -1.0
                break
    canonical = tuple(sign * value for value in normalized)
    if abs(canonical[3]) <= _EPSILON:
        canonical = canonical[:3] + (0.0,)
    return canonical
