"""Transform and matrix helpers for Maya API paths."""

import math

from maya.api import OpenMaya as om


def create_matrix_from_axes(x_axis, y_axis, z_axis):
    """
    3つの軸ベクトルから回転行列を作成する。

    Args:
        x_axis (list): X軸ベクトル [x, y, z]
        y_axis (list): Y軸ベクトル [x, y, z]
        z_axis (list): Z軸ベクトル [x, y, z]

    Returns:
        om.MMatrix: 回転行列
    """
    matrix = om.MMatrix()
    matrix.setElement(0, 0, x_axis[0])
    matrix.setElement(0, 1, x_axis[1])
    matrix.setElement(0, 2, x_axis[2])
    matrix.setElement(1, 0, y_axis[0])
    matrix.setElement(1, 1, y_axis[1])
    matrix.setElement(1, 2, y_axis[2])
    matrix.setElement(2, 0, z_axis[0])
    matrix.setElement(2, 1, z_axis[1])
    matrix.setElement(2, 2, z_axis[2])
    return matrix


def matrix_to_euler(matrix):
    """
    回転行列をオイラー角に変換する。

    Args:
        matrix (om.MMatrix): 回転行列

    Returns:
        list: オイラー角 [x, y, z] 度数法
    """
    transform_matrix = om.MTransformationMatrix(matrix)
    euler = transform_matrix.rotation(asQuaternion=False)
    return [math.degrees(euler.x), math.degrees(euler.y), math.degrees(euler.z)]
