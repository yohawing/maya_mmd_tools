"""Pure math and metadata helpers for the static MMD control basis.

This foundation records the same display-only shortest-arc basis currently
used to rotate controller CVs.  It intentionally does not create an
``AIM_SPACE`` transform or alter any Maya transform; later motion conversion
work can consume the persisted quaternion without re-deriving it from shape
geometry.
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
