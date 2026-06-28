"""Unit tests for shared Maya path discovery helpers."""

import os
import unittest
from pathlib import Path
from unittest import mock

from tests.common import maya_location


class MayaLocationTest(unittest.TestCase):
    """Maya location resolution should be shared by nox and test runners."""

    def test_version_specific_location_env_wins(self):
        with mock.patch.dict(
            os.environ,
            {"MAYA_LOCATION_2025": "D:/Autodesk/Maya2025", "MAYA_LOCATION": "D:/Autodesk/Common"},
            clear=True,
        ):
            self.assertEqual(maya_location.maya_location(2025), Path("D:/Autodesk/Maya2025"))

    def test_common_location_env_is_fallback(self):
        with mock.patch.dict(os.environ, {"MAYA_LOCATION": "D:/Autodesk/Common"}, clear=True):
            self.assertEqual(maya_location.maya_location("2024"), Path("D:/Autodesk/Common"))

    def test_windows_default_mayapy_adds_exe_suffix(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Windows"):
                self.assertEqual(
                    maya_location.mayapy("2024"),
                    Path("C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe"),
                )

    def test_macos_default_location(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("platform.system", return_value="Darwin"):
                self.assertEqual(
                    maya_location.maya_location("2024"),
                    Path("/Applications/Autodesk/maya2024/Maya.app/Contents"),
                )


if __name__ == "__main__":
    unittest.main()
