"""Declarative command tables for the Nox release-gate tiers."""

from __future__ import annotations

import sys
from pathlib import Path


def tier0_commands() -> list[tuple[str, list[str]]]:
    """Return the always-on release-gate command steps in execution order."""
    return [
        (
            "tier0:ruff",
            ["uvx", "--from", "ruff==0.16.0", "ruff", "check", "--no-fix", "."],
        ),
        ("tier0:diff-check", ["git", "diff", "--check"]),
    ]


def tier1_commands(
    *,
    quick: bool,
    ffi_cargo_target_dir: str,
    ffi_path: str,
) -> list[tuple[str, list[str]]]:
    """Return tier-1 commands, omitting optional native steps in quick mode."""
    commands = [
        ("tier1:ci_unit", ["uvx", "nox", "-s", "ci_unit"]),
        ("tier1:golden_oracle", ["uvx", "nox", "-s", "golden_oracle"]),
        ("tier1:release-package", ["uvx", "nox", "-s", "release_package"]),
    ]
    if quick:
        return commands

    ffi_build_command = ["uvx", "nox", "-s", "ffi_build"]
    native_smoke_command = ["uvx", "nox", "-s", "native_smoke"]
    native_export_smoke_command = [
        "uvx",
        "nox",
        "-s",
        "native_export_smoke",
        "--",
        "--strict",
    ]
    if ffi_cargo_target_dir:
        ffi_build_command.extend(["--", "--release", "--cargo-target-dir", ffi_cargo_target_dir])
    if ffi_path:
        native_smoke_command.extend(["--", "--ffi-path", ffi_path])
        native_export_smoke_command.extend(["--ffi-path", ffi_path])
    commands.extend(
        [
            ("tier1:ffi_build", ffi_build_command),
            ("tier1:native_smoke", native_smoke_command),
            ("tier1:native_export_smoke", native_export_smoke_command),
        ]
    )
    return commands


def tier2_commands(
    *,
    version: str,
    cpp_versions: list[str],
    cpp_config: str,
    release_maya_versions: tuple[str, ...],
    viewport_matrix: tuple[tuple[str, str, str], ...],
    visual_manifest: Path,
    visual_ports: dict[str, str],
    visual_cases,
    include_cpp: bool,
    verbose: bool,
) -> list[tuple[str, list[str]]]:
    """Return the full Maya/native release-gate command table in order.

    The visual-manifest preflight remains an in-process release-gate result in
    ``noxfile.py``; this table only contributes visual commands when the
    manifest exists.  Keeping that distinction preserves the existing
    pass/fail behavior while making the tier ordering data-driven.
    """
    commands: list[tuple[str, list[str]]] = []
    for maya_version in release_maya_versions:
        commands.extend(
            [
                (
                    f"tier2:mayapy-unit-{maya_version}",
                    [
                        "uvx", "nox", "-s", "tests", "--",
                        "--type", "unit", "--maya", maya_version,
                    ],
                ),
                (
                    f"tier2:mayapy-integration-{maya_version}",
                    [
                        "uvx", "nox", "-s", "tests", "--",
                        "--type", "integration", "--maya", maya_version,
                    ],
                ),
            ]
        )

    for maya_version, shader_backend, vp2_device in viewport_matrix:
        commands.append(
            (
                f"tier2:viewport-{shader_backend}-{maya_version}",
                [
                    "uvx", "nox", "-s", "maya_static_render", "--",
                    "--maya", maya_version,
                    "--shader",
                    "--shader-backend", shader_backend,
                    "--vp2-device", vp2_device,
                    "--out",
                    f"build/release-gate/viewport/maya{maya_version}-{shader_backend}.png",
                    "--diagnostics-out",
                    f"build/release-gate/viewport/maya{maya_version}-{shader_backend}.json",
                ],
            )
        )

    if visual_manifest.is_file():
        visual_outputs: dict[str, str] = {}
        for maya_version, shader_backend, vp2_device in viewport_matrix:
            output = f"build/release-gate/visual/maya{maya_version}-{shader_backend}"
            visual_outputs[shader_backend] = output
            command = [
                "uvx", "nox", "-s", "maya_visual_regression", "--",
                "--maya", maya_version,
                "--port", visual_ports[maya_version],
                "--shader-backend", shader_backend,
                "--vp2-device", vp2_device,
                "--manifest", str(visual_manifest),
                "--out", output,
            ]
            for case in visual_cases(shader_backend):
                command.extend(["--case", case])
            commands.append((f"tier2:generated-pmx-visual-{shader_backend}-{maya_version}", command))
        commands.append(
            (
                "tier2:generated-pmx-glsl-dx11-diff",
                [
                    sys.executable,
                    "tests/viewport/visual_regression_compare.py",
                    "--reference-capture-report",
                    f"{visual_outputs['dx11']}/visual-regression-report.json",
                    "--capture-report",
                    f"{visual_outputs['glsl']}/visual-regression-report.json",
                    "--out",
                    "build/release-gate/visual/glsl-dx11-comparison.json",
                    "--default-threshold",
                    "0.12",
                ],
            )
        )

    commands.extend(
        [
            (
                "tier2:bundled-native-smoke",
                ["uvx", "nox", "-s", "bundled_native_smoke"],
            ),
            (
                "tier2:native-physics-release-gate",
                ["uvx", "nox", "-s", "native_physics_release_gate"],
            ),
            (
                "tier2:pmx-roundtrip-v0_4",
                [
                    "uvx", "nox", "-s", "pmx_roundtrip", "--",
                    "--maya", version,
                    "--manifest", "tests/roundtrip/manifest_v0_4.json",
                    "--require-clean",
                    "--out-dir", "build/release-gate/pmx_roundtrip_v0_4",
                ],
            ),
            (
                "tier2:import-scale-drift",
                [
                    "uvx", "nox", "-s", "import_scale_drift_e2e", "--",
                    "--maya", version, "--expect", "fixed",
                ],
            ),
            (
                "tier2:anim-layer-graph",
                ["uvx", "nox", "-s", "anim_layer_graph_compare", "--", "--maya", version],
            ),
            (
                "tier2:import-order-e2e",
                [
                    "uvx", "nox", "-s", "import_order_e2e", "--",
                    "--maya", version, "--require-zero-fallback",
                ],
            ),
            (
                "tier2:humanik-control-rig",
                [
                    "uvx", "nox", "-s", "humanik_definition_smoke", "--",
                    "--maya", version,
                    "--fixture", "body",
                    "--create-control-rig",
                    "--out", "build/release-gate/humanik_control_rig_smoke.json",
                ],
            ),
        ]
    )
    if include_cpp:
        for cpp_version in cpp_versions:
            commands.append(
                (
                    f"tier2:cpp-verify-{cpp_version}",
                    [
                        "uvx", "nox", "-s", "cpp_verify", "--",
                        "--maya", cpp_version, "--config", cpp_config,
                    ],
                )
            )
    if verbose:
        for name, command in commands:
            if name.startswith("tier2:mayapy-") and "--verbose" not in command:
                command.append("--verbose")
    return commands


def tier3_commands(
    *,
    root: Path,
    version: str,
    local_assets_manifest: str,
    camera_manifest: str,
    local_parity_manifest: str,
    strict_local: bool,
) -> list[tuple[str, list[str], Path]]:
    """Return local-asset, camera, and parity release-gate commands in order."""
    commands = [
        (
            "tier3:local-assets-check",
            [
                "uvx", "nox", "-s", "local_assets_check", "--",
                "--maya", version,
                "--manifest", local_assets_manifest,
                "--out-json", "build/reports/release_gate_local_assets.json",
                "--out-md", "build/reports/release_gate_local_assets.md",
            ],
            root / "build/reports/release_gate_local_assets.json",
        ),
        (
            "tier3:release-camera-motion-oracle",
            [
                "uvx", "nox", "-s", "release_camera_motion_oracle", "--",
                "--maya", version,
                "--manifest", camera_manifest,
                "--skip-addiction-parity",
                "--out-dir", "build/release-gate/camera-motion",
            ],
            root / "build/release-gate/camera-motion/manifest-skip.json",
        ),
        (
            "tier3:local-parity",
            [
                "uvx", "nox", "-s", "local_parity", "--",
                "--maya", version,
                "--manifest", local_parity_manifest,
                "--skip-fbx",
                "--out", "build/reports/release_gate_local_parity.json",
            ],
            root / "build/reports/release_gate_local_parity.json",
        ),
    ]
    if strict_local:
        for _, command, _ in commands:
            command.append("--strict-local")
    return commands
