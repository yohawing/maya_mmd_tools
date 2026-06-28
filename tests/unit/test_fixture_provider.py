"""Unit tests for test fixture lookup behavior."""

import tempfile
import unittest

from tests.common.test_fixture_provider import TestFixtureProvider


class TestFixtureProviderMissingFiles(unittest.TestCase):
    """Missing test fixtures should skip tests consistently."""

    def test_missing_model_and_motion_fixtures_raise_skiptest(self):
        with tempfile.TemporaryDirectory() as data_dir:
            provider = TestFixtureProvider(data_dir)

            for getter in (
                provider.get_pmd_file,
                provider.get_pmx_file,
                provider.get_vmd_file,
            ):
                with self.assertRaises(unittest.SkipTest):
                    getter()


if __name__ == "__main__":
    unittest.main()
