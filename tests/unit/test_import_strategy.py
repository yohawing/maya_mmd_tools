"""Pure-Python tests for import strategy resolution."""

import unittest

from mmd_tools.core.import_strategy import (
    resolve_model_import_strategy,
    resolve_vmd_runtime_bake_strategy,
)


class TestModelImportStrategy(unittest.TestCase):
    def test_legacy_fast_switch_does_not_affect_strategy(self):
        for suffix in (".pmx", ".PMX", ".pmd", ".vmd"):
            baseline = resolve_model_import_strategy("model" + suffix, {}, settings_get=lambda k, d: d)
            for saved in (False, True):
                for requested in (False, True):
                    with self.subTest(suffix=suffix, saved=saved, requested=requested):
                        strategy = resolve_model_import_strategy(
                            "model" + suffix, {"use_cpp_fast_load": requested},
                            settings_get=lambda k, d: saved if k == "import.native.use_cpp_fast_load" else d,
                        )
                        self.assertEqual(strategy, baseline)
                        self.assertEqual(strategy.suffix, suffix.lower())

    def test_explicit_parser_options_are_preserved(self):
        strategy = resolve_model_import_strategy(
            "model.pmd", {"use_native_pmx_parse": False, "require_native_pmx_parse": True},
            settings_get=lambda k, d: d,
        )
        self.assertFalse(strategy.use_native_pmx_parse)
        self.assertTrue(strategy.require_native_pmx_parse)

    def test_native_parse_defaults_are_development_only(self):
        for development in (False, True):
            with self.subTest(development=development):
                strategy = resolve_model_import_strategy(
                    "model.pmd", {}, settings_get=lambda k, d: {
                        "ui.general.development_mode": development,
                        "import.native.require_native_pmx_parse": True,
                    }.get(k, d),
                )
                self.assertIsNone(strategy.use_native_pmx_parse)
                self.assertEqual(strategy.require_native_pmx_parse, development)


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
