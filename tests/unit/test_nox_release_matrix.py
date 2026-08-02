"""Focused tests for declarative release-gate tier command tables."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.nox.release_matrix import tier0_commands, tier1_commands, tier2_commands, tier3_commands


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
                (
                    "tier1:mmd-anim-python-bindings",
                    ["uvx", "nox", "-s", "mmd_anim_python_tests"],
                ),
                (
                    "tier1:mmd-anim-binding-gate",
                    ["uvx", "nox", "-s", "mmd_anim_binding_gate"],
                ),
                (
                    "tier1:export-validation",
                    [
                        "uvx",
                        "nox",
                        "-s",
                        "export_validation_gate",
                        "--",
                        "--strict",
                    ],
                ),
            ],
        )

    def test_tier2_preserves_maya_viewport_and_native_order(self):
        commands = tier2_commands(
            version="2024",
            cpp_versions=["2026"],
            cpp_config="Release",
            release_maya_versions=("2024",),
            viewport_matrix=(("2025", "glsl", "glcore"), ("2026", "dx11", "dx11")),
            visual_manifest=Path("missing-render-manifest.json"),
            visual_ports={"2025": "7825", "2026": "7826"},
            visual_cases=lambda _shader_backend: (),
            include_cpp=True,
            verbose=True,
        )
        names = [name for name, _command in commands]
        self.assertEqual(
            names[:4],
            [
                "tier2:mayapy-unit-2024",
                "tier2:mayapy-integration-2024",
                "tier2:viewport-glsl-2025",
                "tier2:viewport-dx11-2026",
            ],
        )
        self.assertLess(names.index("tier2:bundled-native-smoke"), names.index("tier2:cpp-verify-2026"))
        unit_command = dict(commands)["tier2:mayapy-unit-2024"]
        self.assertEqual(unit_command[-1], "--verbose")
        self.assertEqual(
            dict(commands)["tier2:cpp-verify-2026"],
            ["uvx", "nox", "-s", "cpp_verify", "--", "--maya", "2026", "--config", "Release"],
        )

    def test_tier2_adds_visual_commands_only_for_a_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "render.json"
            manifest.write_text("{}", encoding="utf-8")
            commands = tier2_commands(
                version="2024",
                cpp_versions=[],
                cpp_config="Debug",
                release_maya_versions=(),
                viewport_matrix=(("2025", "glsl", "glcore"), ("2026", "dx11", "dx11")),
                visual_manifest=manifest,
                visual_ports={"2025": "7825", "2026": "7826"},
                visual_cases=lambda shader_backend: (f"case-{shader_backend}",),
                include_cpp=False,
                verbose=False,
            )
        commands_by_name = dict(commands)
        self.assertIn("tier2:generated-pmx-visual-glsl-2025", commands_by_name)
        self.assertIn("tier2:generated-pmx-visual-dx11-2026", commands_by_name)
        self.assertIn("tier2:generated-pmx-glsl-dx11-diff", commands_by_name)
        self.assertIn("case-glsl", commands_by_name["tier2:generated-pmx-visual-glsl-2025"])
        self.assertIn("case-dx11", commands_by_name["tier2:generated-pmx-visual-dx11-2026"])

    def test_tier3_preserves_result_reports_and_strict_local(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = tier3_commands(
                root=root,
                version="2024",
                local_assets_manifest="assets.json",
                camera_manifest="camera.json",
                local_parity_manifest="parity.json",
                strict_local=True,
            )
        self.assertEqual(
            [name for name, _command, _report in commands],
            [
                "tier3:local-assets-check",
                "tier3:release-camera-motion-oracle",
                "tier3:local-parity",
            ],
        )
        self.assertTrue(all(command[-1] == "--strict-local" for _name, command, _report in commands))
        self.assertEqual(commands[0][2], root / "build/reports/release_gate_local_assets.json")
        self.assertEqual(commands[1][2], root / "build/release-gate/camera-motion/manifest-skip.json")
