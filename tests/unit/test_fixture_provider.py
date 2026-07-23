"""Unit tests for test fixture lookup behavior."""

import tempfile
import unittest
from pathlib import Path

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


class TestRegisteredFixtureManifest(unittest.TestCase):
    """Manifest-backed fixtures must resolve and fail closed on drift."""

    def test_yw_test_model_is_registered_and_verified(self):
        provider = TestFixtureProvider()

        self.assertIn("yw_test_model", provider.get_registered_fixture_names())
        verified = provider.get_verified_fixture("yw_test_model")
        self.assertEqual(
            verified["manifest"]["license"]["evidence"]["comment_english"],
            "CC0",
        )
        self.assertEqual(
            Path(provider.get_verified_pmx_file()).name,
            "yw_test_model.pmx",
        )

    def test_manifest_hash_mismatch_is_an_error(self):
        with tempfile.TemporaryDirectory() as data_dir:
            root = Path(data_dir)
            (root / "yw_test_model.pmx").write_bytes(b"fixture")
            (root / "yw_test_model.fixture.json").write_text(
                "{\"name\":\"yw_test_model\",\"files\":["
                "{\"path\":\"yw_test_model.pmx\",\"kind\":\"pmx\","
                "\"size\":7,\"sha256\":\"00\"}]}\n",
                encoding="utf-8",
            )
            provider = TestFixtureProvider(str(root))
            with self.assertRaises(ValueError):
                provider.get_verified_fixture("yw_test_model")


if __name__ == "__main__":
    unittest.main()
