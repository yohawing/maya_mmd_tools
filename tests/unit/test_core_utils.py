"""Tests for Maya-independent core utility functions."""

import json
import unittest

from mmd_tools.core.utils import unique_json_object


class TestUniqueJsonObject(unittest.TestCase):
    def test_valid_json_preserves_values(self):
        payload = '{"name": "model", "items": [{"name": "morph"}, {}, 1, true, null]}'

        self.assertEqual(
            json.loads(payload, object_pairs_hook=unique_json_object),
            json.loads(payload),
        )

    def test_duplicate_key_preserves_exception(self):
        for payload in (
            '{"name": 1, "name": 2}',
            '{"items": [{"name": 1, "name": 2}]}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError) as caught:
                    json.loads(payload, object_pairs_hook=unique_json_object)

                self.assertIs(type(caught.exception), ValueError)
                self.assertEqual(str(caught.exception), "duplicate JSON field 'name'")
