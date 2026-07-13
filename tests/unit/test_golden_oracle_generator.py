"""Pure tests for safe GoldenOracle candidate generation and provenance."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


_SCRIPT = Path(__file__).resolve().parents[1] / "golden-oracle" / "generate_oracle.py"
_SPEC = importlib.util.spec_from_file_location("golden_oracle_generator", _SCRIPT)
generator = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(generator)


class _FakeLibrary:
    def __init__(self, path: Path, abi: int = 2, flags: int = 7):
        self._name = str(path)
        self._abi = abi
        self._flags = flags

    def mmd_runtime_abi_version(self):
        return self._abi

    def mmd_runtime_feature_flags(self):
        return self._flags


class TestGoldenOracleGenerator(unittest.TestCase):
    CASE = {
        "name": "sample",
        "oracle": {"path": "oracle/sample.oracle.jsonl"},
    }

    def test_default_output_is_candidate_directory_by_case_filename(self):
        output = generator._output_path(
            generator.DEFAULT_MANIFEST,
            self.CASE,
            generator.DEFAULT_OUT_DIR,
            write_tracked=False,
        )
        self.assertEqual(output, generator.DEFAULT_OUT_DIR / "sample.oracle.jsonl")
        self.assertNotEqual(output.parent, generator.TRACKED_ORACLE_DIR)

    def test_custom_candidate_output_never_uses_manifest_oracle_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = generator._output_path(
                generator.DEFAULT_MANIFEST,
                self.CASE,
                Path(temporary),
                write_tracked=False,
            )
        self.assertEqual(output.name, "sample.oracle.jsonl")
        self.assertNotEqual(output.parent, generator.TRACKED_ORACLE_DIR)

    def test_tracked_output_requires_loud_flag_and_cannot_combine_out_dir(self):
        tracked = generator._output_path(
            generator.DEFAULT_MANIFEST,
            self.CASE,
            generator.DEFAULT_OUT_DIR,
            write_tracked=True,
        )
        self.assertEqual(tracked, generator.TRACKED_ORACLE_DIR / "sample.oracle.jsonl")
        with self.assertRaises(SystemExit):
            generator._parse_args(["--write-tracked", "--out-dir", "candidate"])

    def test_provenance_records_exact_path_hash_version_abi_and_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_path = Path(temporary) / "mmd_runtime_ffi.dll"
            payload = b"runtime-binary"
            runtime_path.write_bytes(payload)
            library = _FakeLibrary(runtime_path)
            module = SimpleNamespace(
                get_mmd_runtime_library=lambda: library,
                _runtime_loader=SimpleNamespace(_runtime_lib_path=runtime_path),
            )
            with patch.object(generator, "_submodule_commit", return_value="a" * 40):
                provenance = generator._establish_provenance(runtime_path, "0.2.0", module)

        self.assertEqual(provenance["mmdAnimVersion"], "0.2.0")
        self.assertEqual(provenance["runtimeRequestedPath"], str(runtime_path.resolve()))
        self.assertEqual(provenance["runtimeLoadedPath"], str(runtime_path.resolve()))
        self.assertEqual(provenance["runtimeSha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(provenance["runtimeAbi"], 2)
        self.assertEqual(provenance["runtimeFeatureFlags"], 7)
        self.assertEqual(provenance["mmdAnimCommit"], "a" * 40)

    def test_provenance_fails_on_wrong_actual_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            requested = Path(temporary) / "requested.dll"
            actual = Path(temporary) / "actual.dll"
            requested.write_bytes(b"requested")
            actual.write_bytes(b"actual")
            module = SimpleNamespace(
                get_mmd_runtime_library=lambda: _FakeLibrary(actual),
                _runtime_loader=SimpleNamespace(_runtime_lib_path=actual),
            )
            with self.assertRaisesRegex(RuntimeError, "loaded runtime path mismatch"):
                generator._establish_provenance(requested, "0.2.0", module)

    def test_provenance_fails_on_wrong_abi_or_missing_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_path = Path(temporary) / "runtime.dll"
            runtime_path.write_bytes(b"runtime")
            wrong_abi = SimpleNamespace(
                get_mmd_runtime_library=lambda: _FakeLibrary(runtime_path, abi=99),
                _runtime_loader=SimpleNamespace(_runtime_lib_path=runtime_path),
            )
            missing = SimpleNamespace(get_mmd_runtime_library=lambda: None)
            with self.assertRaisesRegex(RuntimeError, "runtime ABI mismatch"):
                generator._establish_provenance(runtime_path, "0.2.0", wrong_abi)
            with self.assertRaisesRegex(RuntimeError, "failed to load"):
                generator._establish_provenance(runtime_path, "0.2.0", missing)

    def test_provenance_fails_when_source_commit_is_unknown(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime_path = Path(temporary) / "runtime.dll"
            runtime_path.write_bytes(b"runtime")
            module = SimpleNamespace(
                get_mmd_runtime_library=lambda: _FakeLibrary(runtime_path),
                _runtime_loader=SimpleNamespace(_runtime_lib_path=runtime_path),
            )
            with patch.object(generator, "_submodule_commit", return_value=None):
                with self.assertRaisesRegex(
                    RuntimeError, "source commit could not be established"
                ):
                    generator._establish_provenance(runtime_path, "0.2.0", module)

    def test_missing_runtime_file_and_invalid_explicit_version_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            generator._establish_provenance(
                Path("definitely-missing-runtime.dll"),
                "0.2.0",
                SimpleNamespace(get_mmd_runtime_library=lambda: None),
            )
        with self.assertRaisesRegex(ValueError, "invalid semantic version"):
            generator._workspace_version(Path("unused"), explicit="latest")

    def test_version_must_come_from_workspace_package_section(self):
        with tempfile.TemporaryDirectory() as temporary:
            cargo_toml = Path(temporary) / "Cargo.toml"
            cargo_toml.write_text(
                '[workspace.package]\nedition = "2024"\n\n[package]\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "could not be established"):
                generator._workspace_version(cargo_toml)


if __name__ == "__main__":
    unittest.main()
