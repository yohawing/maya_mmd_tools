"""Independent PMX body orientation oracle for the legacy native PMX reader.

Production descriptors adapt body Ry*Rx*Rz to Bullet's Rz*Ry*Rx encoding.
The native direct-PMX reader has not made that adaptation. Only its reference
world receives these normalized bytes; source fixtures and model bytes stay raw.
"""

import copy
import math
from pathlib import Path
import tempfile


def _multiply(a, b):
    return tuple(tuple(sum(a[row][k] * b[k][col] for k in range(3))
                       for col in range(3)) for row in range(3))


def pmx_body_matrix(angles):
    """Compose the Saba PMX body matrix with explicit independent axis matrices."""
    x, y, z = angles
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    ry = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    rz = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    return _multiply(_multiply(ry, rx), rz)


def legacy_native_body_angles(angles):
    """Decompose that matrix for Bullet's ZYX constructor, without product math."""
    matrix = pmx_body_matrix(angles)
    y = math.asin(max(-1.0, min(1.0, -matrix[2][0])))
    if abs(matrix[2][0]) < 1.0 - 1e-12:
        x = math.atan2(matrix[2][1], matrix[2][2])
        z = math.atan2(matrix[1][0], matrix[0][0])
    else:
        x = 0.0
        z = math.atan2(-matrix[0][1], matrix[1][1])
    return (x, y, z)


def legacy_native_reference_pmx_bytes(pmx):
    """Serialize a legacy-parsed copy with normalized body angles only.

    Pass parse_pmx_file(..., use_native_pmx_parse=False): its per-record index
    metadata is required by the complete Python writer.
    """
    reference = copy.deepcopy(pmx)
    for body in reference.rigid_bodies:
        body.rotation = legacy_native_body_angles(body.rotation)
    # The hair fixture includes metadata unsupported by native parts export.
    # Use the existing complete writer so only body Euler fields change.
    with tempfile.TemporaryDirectory(prefix="mmd_body_reference_") as directory:
        path = Path(directory) / "reference.pmx"
        reference.write_file(str(path))
        return path.read_bytes()
