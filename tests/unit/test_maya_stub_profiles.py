"""Unit tests for named Maya stub profiles."""

import sys
import unittest
from unittest.mock import MagicMock

from tests.common import maya_stub


@unittest.skipIf(maya_stub._is_real_maya_present(), "requires stub-only Python environment")
class MayaStubProfilesTest(unittest.TestCase):
    """Named profiles should make maya.cmds defaults explicit."""

    def setUp(self):
        self._saved_modules = {
            name: sys.modules[name]
            for name in maya_stub._STUBBED_MODULE_NAMES
            if name in sys.modules
        }
        for name in maya_stub._STUBBED_MODULE_NAMES:
            sys.modules.pop(name, None)

    def tearDown(self):
        for name in maya_stub._STUBBED_MODULE_NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(self._saved_modules)

    def test_default_minimal_profile_keeps_plain_magicmock_results(self):
        self.assertTrue(maya_stub.install_maya_stub())

        import maya.cmds as cmds

        self.assertIsInstance(cmds.ls(), MagicMock)
        self.assertIsInstance(cmds.objExists("anything"), MagicMock)

    def test_headless_profile_sets_query_safe_values(self):
        self.assertTrue(maya_stub.install_maya_stub(profile="headless"))

        import maya.cmds as cmds

        self.assertEqual(cmds.ls(), [])
        self.assertEqual(cmds.listRelatives(), [])
        self.assertFalse(cmds.objExists("missing"))
        self.assertEqual(cmds.namespaceInfo(currentNamespace=True), ":")

    def test_minimal_profile_keeps_plain_magicmock_results(self):
        self.assertTrue(maya_stub.install_maya_stub(profile="minimal"))

        import maya.cmds as cmds

        self.assertIsInstance(cmds.ls(), MagicMock)
        self.assertIsInstance(cmds.objExists("anything"), MagicMock)

    def test_reapplying_minimal_resets_headless_defaults(self):
        self.assertTrue(maya_stub.install_maya_stub(profile="headless"))

        import maya.cmds as cmds

        self.assertEqual(cmds.ls(), [])

        self.assertTrue(maya_stub.install_maya_stub(profile="minimal"))
        self.assertIsInstance(cmds.ls(), MagicMock)
        self.assertIsInstance(cmds.namespaceInfo(currentNamespace=True), MagicMock)

    def test_omitted_profile_does_not_downgrade_existing_stub(self):
        self.assertTrue(maya_stub.install_maya_stub(profile="headless"))

        import maya.cmds as cmds

        self.assertEqual(cmds.ls(), [])

        self.assertTrue(maya_stub.install_maya_stub())
        self.assertEqual(cmds.ls(), [])

    def test_reapplying_headless_restores_query_safe_values(self):
        self.assertTrue(maya_stub.install_maya_stub(profile="minimal"))

        import maya.cmds as cmds

        self.assertIsInstance(cmds.ls(), MagicMock)

        self.assertTrue(maya_stub.install_maya_stub(profile="headless"))
        self.assertEqual(cmds.ls(), [])
        self.assertFalse(cmds.objExists("missing"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            maya_stub.install_maya_stub(profile="unknown")


if __name__ == "__main__":
    unittest.main()
