/**
 * MmdCoordinateUtils.h
 *
 * Small, header-only helpers for the MMD/Maya Z-reflection used by the native
 * IK and append nodes.  MMD and Maya use opposite handedness on Z; applying
 * the same reflection twice is therefore its own inverse.
 */

#pragma once

#include <maya/MMatrix.h>

inline MMatrix mmdWorldToMaya(const MMatrix& matrix)
{
    const double signs[3] = {1.0, 1.0, -1.0};
    MMatrix result(matrix);
    for (unsigned int row = 0; row < 3; ++row) {
        for (unsigned int col = 0; col < 3; ++col) {
            result(row, col) = matrix(row, col) * signs[row] * signs[col];
        }
    }
    for (unsigned int col = 0; col < 3; ++col) {
        result(3, col) = matrix(3, col) * signs[col];
    }
    return result;
}

inline MMatrix mayaWorldToMmd(const MMatrix& matrix)
{
    return mmdWorldToMaya(matrix);
}
