"""Tests for node identity lookup with injected Maya commands."""

import unittest
from unittest.mock import Mock

from mmd_tools.core.maya_identity import unique_long_name, unique_node_uuid


class TestNodeUuid(unittest.TestCase):
    def test_returns_unique_uuid(self):
        adapter = Mock()
        adapter.ls.return_value = ["node-uuid"]
        self.assertEqual(unique_node_uuid(adapter, "node"), "node-uuid")
        adapter.ls.assert_called_once_with("node", uuid=True)

    def test_returns_none_without_one_nonempty_uuid(self):
        for matches in ([], ["first", "second"], None, [""]):
            with self.subTest(matches=matches):
                adapter = Mock()
                adapter.ls.return_value = matches
                self.assertIsNone(unique_node_uuid(adapter, "node"))

    def test_returns_none_when_lookup_raises(self):
        adapter = Mock()
        adapter.ls.side_effect = RuntimeError("lookup failed")
        self.assertIsNone(unique_node_uuid(adapter, "node"))


class TestUniqueLongName(unittest.TestCase):
    def test_returns_name_only_for_one_match(self):
        for matches, expected in [(["|root|node"], "|root|node"), ([], None), (["|a|node", "|b|node"], None)]:
            with self.subTest(matches=matches):
                adapter = Mock()
                adapter.ls.return_value = matches
                self.assertEqual(unique_long_name(adapter, "|root|node"), expected)
                adapter.ls.assert_called_once_with("|root|node", long=True)

    def test_returns_none_when_lookup_raises(self):
        adapter = Mock()
        adapter.ls.side_effect = RuntimeError("lookup failed")
        self.assertIsNone(unique_long_name(adapter, "node"))
