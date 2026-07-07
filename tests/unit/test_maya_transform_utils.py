import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.common.maya_stub import install_maya_stub

install_maya_stub(profile="headless")

from mmd_tools.core import maya_transform_utils  # noqa: E402


class _FakeMMatrix:
    def __init__(self):
        self._values = [[0.0 for _ in range(4)] for _ in range(4)]
        for index in range(4):
            self._values[index][index] = 1.0

    def setElement(self, row, column, value):
        self._values[row][column] = float(value)

    def getElement(self, row, column):
        return self._values[row][column]


class _FakeTransformationMatrix:
    def __init__(self, matrix):
        self._matrix = matrix

    def rotation(self, asQuaternion=False):
        if asQuaternion:
            raise AssertionError("matrix_to_euler should request Euler rotation")

        if (
            self._matrix.getElement(0, 0) == 0.0
            and self._matrix.getElement(0, 1) == 1.0
            and self._matrix.getElement(1, 0) == -1.0
            and self._matrix.getElement(1, 1) == 0.0
        ):
            return SimpleNamespace(x=0.0, y=0.0, z=math.pi / 2)
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)


class TestMayaTransformUtils(unittest.TestCase):
    def setUp(self):
        self._matrix_patch = patch.object(maya_transform_utils.om, "MMatrix", _FakeMMatrix)
        self._transform_patch = patch.object(
            maya_transform_utils.om,
            "MTransformationMatrix",
            _FakeTransformationMatrix,
        )
        self._matrix_patch.start()
        self._transform_patch.start()

    def tearDown(self):
        self._transform_patch.stop()
        self._matrix_patch.stop()

    def test_create_matrix_from_axes_and_matrix_to_euler_identity(self):
        matrix = maya_transform_utils.create_matrix_from_axes(
            x_axis=[1, 0, 0],
            y_axis=[0, 1, 0],
            z_axis=[0, 0, 1],
        )

        self.assertEqual(matrix.getElement(0, 0), 1.0)
        self.assertEqual(matrix.getElement(1, 1), 1.0)
        self.assertEqual(matrix.getElement(2, 2), 1.0)

        euler = maya_transform_utils.matrix_to_euler(matrix)

        self.assertAlmostEqual(euler[0], 0.0, places=5)
        self.assertAlmostEqual(euler[1], 0.0, places=5)
        self.assertAlmostEqual(euler[2], 0.0, places=5)

    def test_create_matrix_from_axes_and_matrix_to_euler_z_rotation(self):
        matrix = maya_transform_utils.create_matrix_from_axes(
            x_axis=[0, 1, 0],
            y_axis=[-1, 0, 0],
            z_axis=[0, 0, 1],
        )

        self.assertEqual(matrix.getElement(0, 0), 0.0)
        self.assertEqual(matrix.getElement(0, 1), 1.0)
        self.assertEqual(matrix.getElement(1, 0), -1.0)
        self.assertEqual(matrix.getElement(1, 1), 0.0)
        self.assertEqual(matrix.getElement(2, 2), 1.0)

        euler = maya_transform_utils.matrix_to_euler(matrix)

        self.assertAlmostEqual(euler[0], 0.0, places=5)
        self.assertAlmostEqual(euler[1], 0.0, places=5)
        self.assertAlmostEqual(euler[2], 90.0, places=5)


if __name__ == "__main__":
    unittest.main()
