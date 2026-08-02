"""Focused tests for declarative release-gate tier command tables."""

from __future__ import annotations

import unittest

from noxlib.release_matrix import tier0_commands, tier1_commands


class ReleaseMatrixTest(unittest.TestCase):
    def test_tier0_commands_keep_the_public_order(self):
        self.assertEqual(
            tier0_commands(),
            [
                (
                    "tier0:ruff",
                    ["uvx", "--from", "ruff==0.16.0", "ruff", "check", "--no-fix", "."],
                ),
                ("tier0:diff-check", ["git", "diff", "--check"]),
            ],
        )

    def test_quick_tier1_contains_only_always_on_steps(self):
        self.assertEqual(
            tier1_commands(quick=True, ffi_cargo_target_dir="target", ffi_path="ffi"),
            [
                ("tier1:ci_unit", ["uvx", "nox", "-s", "ci_unit"]),
                ("tier1:golden_oracle", ["uvx", "nox", "-s", "golden_oracle"]),
                ("tier1:release-package", ["uvx", "nox", "-s", "release_package"]),
            ],
        )

    def test_full_tier1_preserves_optional_native_arguments(self):
        self.assertEqual(
            tier1_commands(
                quick=False,
                ffi_cargo_target_dir="build/custom-target",
                ffi_path="build/custom-target/release",
            ),
            [
                ("tier1:ci_unit", ["uvx", "nox", "-s", "ci_unit"]),
                ("tier1:golden_oracle", ["uvx", "nox", "-s", "golden_oracle"]),
                ("tier1:release-package", ["uvx", "nox", "-s", "release_package"]),
                (
                    "tier1:ffi_build",
                    [
                        "uvx",
                        "nox",
                        "-s",
                        "ffi_build",
                        "--",
                        "--release",
                        "--cargo-target-dir",
                        "build/custom-target",
                    ],
                ),
                (
                    "tier1:native_smoke",
                    ["uvx", "nox", "-s", "native_smoke", "--", "--ffi-path", "build/custom-target/release"],
                ),
                (
                    "tier1:native_export_smoke",
                    [
                        "uvx",
                        "nox",
                        "-s",
                        "native_export_smoke",
                        "--",
                        "--strict",
                        "--ffi-path",
                        "build/custom-target/release",
                    ],
                ),
            ],
        )
