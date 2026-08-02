"""Focused tests for the Nox-independent task-runner helpers."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import nox  # noqa: F401
except ModuleNotFoundError:
    nox_stub = types.ModuleType("nox")
    nox_stub.options = types.SimpleNamespace(sessions=[])
    nox_stub.Session = object
    nox_stub.session = lambda **_kwargs: lambda func: func
    sys.modules["nox"] = nox_stub

import noxfile
from tools.nox import common


class NoxCommonTest(unittest.TestCase):
    def test_option_helpers_preserve_positional_edge_cases(self):
        args = ["--name", "first", "--name", "--looks-like-an-option", "--flag"]

        self.assertEqual(common._option(args, "--missing", "fallback"), "fallback")
        self.assertEqual(common._option(args, "--name", "fallback"), "first")
        self.assertEqual(common._options(args, "--name"), ["first", "--looks-like-an-option"])
        self.assertEqual(
            common._without_option(args, "--name"),
            ["--flag"],
        )
        self.assertTrue(common._has_flag(args, "--flag"))
        self.assertFalse(common._has_flag(args, "--absent"))

    def test_option_helpers_reject_missing_values(self):
        for helper in (common._option, common._options, common._without_option):
            with self.subTest(helper=helper.__name__):
                with self.assertRaisesRegex(ValueError, "--value requires a value"):
                    if helper is common._option:
                        helper(["--value"], "--value", "default")
                    else:
                        helper(["--value"], "--value")

        with self.assertRaisesRegex(ValueError, "--features requires a value"):
            common._cargo_args_with_physics_feature(["--features"])

    def test_cargo_feature_helper_handles_separate_and_equals_forms(self):
        self.assertEqual(
            common._cargo_args_with_physics_feature(["build"]),
            ["build", "--features", "physics-bullet-native"],
        )
        self.assertEqual(
            common._cargo_args_with_physics_feature(["--features", "foo,physics-bullet-native"]),
            ["--features", "foo,physics-bullet-native"],
        )
        self.assertEqual(
            common._cargo_args_with_physics_feature(["--features=foo,bar"]),
            ["--features=foo bar physics-bullet-native"],
        )

    def test_resolve_path_uses_repo_root_only_for_relative_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = common._resolve_existing_or_repo_path("nested/input.pmx", root)
            absolute_source = root / "absolute.pmx"
            absolute = common._resolve_existing_or_repo_path(str(absolute_source), root)

        self.assertEqual(relative, (root / "nested/input.pmx").resolve())
        self.assertEqual(absolute, absolute_source.resolve())

    def test_sha256_file_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.bin"
            path.write_bytes(b"nox-common\x00payload")
            self.assertEqual(common._sha256_file(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_mmd_anim_version_reads_first_non_empty_line_and_uses_root(self):
        expected_root = Path("F:/test-repo")
        completed = types.SimpleNamespace(stdout="\n mmd-anim 0.2.0 \nignored\n")
        with mock.patch.object(common.subprocess, "run", return_value=completed) as run:
            version = common._mmd_anim_cli_version(Path("mmd-anim"), expected_root)

        self.assertEqual(version, "mmd-anim 0.2.0")
        self.assertEqual(run.call_args.kwargs["cwd"], expected_root)

    def test_noxfile_keeps_historical_helper_names_and_root_wrappers(self):
        self.assertIs(noxfile._option, common._option)
        self.assertIs(noxfile._options, common._options)
        self.assertIs(noxfile._without_option, common._without_option)
        self.assertIs(noxfile._has_flag, common._has_flag)
        self.assertIs(noxfile._cargo_args_with_physics_feature, common._cargo_args_with_physics_feature)
        self.assertIs(noxfile._sha256_file, common._sha256_file)
        self.assertIs(noxfile._download_file, common._download_file)
        self.assertIs(noxfile._extract_archive, common._extract_archive)

        patched_root = Path("F:/patched-repo")
        with mock.patch.object(noxfile, "ROOT", patched_root):
            self.assertEqual(
                noxfile._resolve_existing_or_repo_path("relative.txt"),
                (patched_root / "relative.txt").resolve(),
            )
