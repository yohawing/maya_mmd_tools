"""Declarative command tables for the Nox release-gate tiers."""

from __future__ import annotations


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
