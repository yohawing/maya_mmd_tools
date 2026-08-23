"""Focused tests for the local motion-parity runner setup."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.viewport import local_asset_motion_compare


class LocalAssetMotionCompareTest(unittest.TestCase):
    @patch("tests.common.maya_plugin_setup.load_mmd_tools_plugin")
    @patch("tests.viewport.local_asset_motion_compare.maya.standalone.initialize")
    def test_initialize_maya_loads_production_plugin(
        self,
        initialize_mock,
        load_plugin_mock,
    ):
        local_asset_motion_compare._initialize_maya()

        initialize_mock.assert_called_once_with(name="python")
        load_plugin_mock.assert_called_once_with(local_asset_motion_compare.ROOT)


if __name__ == "__main__":
    unittest.main()
