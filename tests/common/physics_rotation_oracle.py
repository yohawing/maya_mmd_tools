"""Independent PMX body orientation matrix oracle; no input rewriting."""

import math


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
