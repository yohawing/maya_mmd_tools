"""Pure PMX Local Axis conversion shared by Maya joints and Control Rigs.

Use :func:`maya_basis_from_pmx_local_axes` whenever PMX X/Z axis metadata must
be reconstructed in Maya coordinates. Keeping validation and orthonormalization
here prevents jointOrient and controller AIM_SPACE bases from drifting apart.
"""

from __future__ import annotations

import math
from typing import Iterable, Tuple


Vector3 = Tuple[float, float, float]
Basis3 = Tuple[Vector3, Vector3, Vector3]


def maya_basis_from_pmx_local_axes(
    x_values: Iterable[float],
    z_values: Iterable[float],
    *,
    epsilon: float = 1.0e-8,
) -> Basis3:
    """Return orthonormal Maya-world X/Y/Z rows from PMX Local Axis vectors.

    Raises:
        ValueError: If either axis is malformed, non-finite, degenerate, or the
            two authored axes are parallel.
    """
    x_axis = _maya_axis_vector(x_values, "X", epsilon)
    z_axis = _maya_axis_vector(z_values, "Z", epsilon)
    y_axis = _cross_product(z_axis, x_axis)
    y_length = _vector_length(y_axis)
    if y_length <= epsilon:
        raise ValueError("LOCAL_AXIS X and Z axes must not be parallel")
    y_axis = tuple(component / y_length for component in y_axis)
    z_axis = _cross_product(x_axis, y_axis)
    z_length = _vector_length(z_axis)
    if z_length <= epsilon:
        raise ValueError("LOCAL_AXIS X and Z axes must not be parallel")
    z_axis = tuple(component / z_length for component in z_axis)
    return x_axis, y_axis, z_axis


def _maya_axis_vector(
    values: Iterable[float],
    label: str,
    epsilon: float,
) -> Vector3:
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"LOCAL_AXIS {label} axis must contain three finite values"
        ) from exc
    if len(vector) != 3 or not all(math.isfinite(value) for value in vector):
        raise ValueError(
            f"LOCAL_AXIS {label} axis must contain three finite values"
        )
    maya_vector = (vector[0], vector[1], -vector[2])
    length = _vector_length(maya_vector)
    if length <= epsilon:
        raise ValueError(
            f"LOCAL_AXIS {label} axis magnitude must be greater than {epsilon}"
        )
    return tuple(component / length for component in maya_vector)


def _vector_length(vector: Vector3) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _cross_product(left: Vector3, right: Vector3) -> Vector3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )
