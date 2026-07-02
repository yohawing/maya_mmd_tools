"""Pure-Python tests for import strategy resolution."""

import unittest

from mmd_tools.core.import_strategy import (
    resolve_model_import_strategy,
    resolve_vmd_runtime_bake_strategy,
)


class TestModelImportStrategy(unittest.TestCase):
    def test_pmx_fast_load_enabled_by_option(self):
        strategy = resolve_model_import_strategy(
            "model.pmx",
            {"use_cpp_fast_load": True, "require_native_pmx_parse": True},
            settings_get=lambda _key, default=None: default,
        )

        self.assertEqual(strategy.suffix, ".pmx")
        self.assertTrue(strategy.use_cpp_fast_load)
        self.assertEqual(strategy.cpp_fast_load_reason, "enabled by option/settings")
        self.assertTrue(strategy.require_native_pmx_parse)

    def test_non_pmx_disables_fast_load_even_when_requested(self):
        strategy = resolve_model_import_strategy(
            "model.pmd",
            {"use_cpp_fast_load": True},
            settings_get=lambda _key, default=None: default,
        )

        self.assertFalse(strategy.use_cpp_fast_load)
        self.assertEqual(strategy.cpp_fast_load_reason, "disabled: suffix .pmd is not .pmx")

    def test_native_parse_defaults_come_from_settings(self):
        def fake_settings(key, default=None):
            if key == "import.native.use_cpp_fast_load":
                return False
            if key == "import.native.require_native_pmx_parse":
                return True
            return default

        strategy = resolve_model_import_strategy("model.pmx", {}, settings_get=fake_settings)

        self.assertFalse(strategy.use_cpp_fast_load)
        self.assertIsNone(strategy.use_native_pmx_parse)
        self.assertTrue(strategy.require_native_pmx_parse)


class TestVmdRuntimeBakeStrategy(unittest.TestCase):
    def test_bake_mode_with_pmx_bytes_uses_runtime(self):
        strategy = resolve_vmd_runtime_bake_strategy(
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            pmx_path=None,
            has_runtime=True,
            runtime_available=lambda: True,
            bake_mode=True,
        )

        self.assertTrue(strategy.use_runtime_bake)
        self.assertEqual(strategy.reason, "enabled: PMX bytes provided")

    def test_bake_mode_with_existing_pmx_path_uses_runtime(self):
        strategy = resolve_vmd_runtime_bake_strategy(
            vmd_bytes=b"vmd",
            pmx_bytes=None,
            pmx_path="model.pmx",
            has_runtime=True,
            runtime_available=lambda: True,
            bake_mode=True,
            path_exists=lambda path: path == "model.pmx",
        )

        self.assertTrue(strategy.use_runtime_bake)
        self.assertEqual(strategy.reason, "enabled: PMX path provided")

    def test_rejects_when_bake_mode_is_off(self):
        strategy = resolve_vmd_runtime_bake_strategy(
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            pmx_path=None,
            has_runtime=True,
            runtime_available=lambda: True,
            bake_mode=False,
        )

        self.assertFalse(strategy.use_runtime_bake)
        self.assertEqual(strategy.reason, "disabled: bake mode is off")

    def test_rejects_missing_runtime_library(self):
        strategy = resolve_vmd_runtime_bake_strategy(
            vmd_bytes=b"vmd",
            pmx_bytes=b"pmx",
            pmx_path=None,
            has_runtime=True,
            runtime_available=lambda: False,
            bake_mode=True,
        )

        self.assertFalse(strategy.use_runtime_bake)
        self.assertEqual(strategy.reason, "disabled: mmd-anim runtime library unavailable")

    def test_rejects_missing_pmx_source(self):
        strategy = resolve_vmd_runtime_bake_strategy(
            vmd_bytes=b"vmd",
            pmx_bytes=None,
            pmx_path="model.pmd",
            has_runtime=True,
            runtime_available=lambda: True,
            bake_mode=True,
            path_exists=lambda _path: True,
        )

        self.assertFalse(strategy.use_runtime_bake)
        self.assertEqual(strategy.reason, "disabled: missing PMX bytes/path")


if __name__ == "__main__":
    unittest.main()
