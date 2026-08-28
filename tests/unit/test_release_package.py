"""Focused tests for manifest-driven release ZIP assembly and validation."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests.release import package


try:
    import nox  # noqa: F401
except ModuleNotFoundError:
    nox_stub = types.ModuleType("nox")
    nox_stub.options = types.SimpleNamespace(sessions=[])
    nox_stub.Session = object
    nox_stub.session = lambda **_kwargs: lambda func: func
    sys.modules["nox"] = nox_stub


class ReleasePackageTest(unittest.TestCase):
    """Exercise the package contract without Maya or platform-specific tools."""

    version = "9.8.7"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manifest_path = self.root / "package_manifest.json"
        self.manifest_path.write_text(package.DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        self._create_fixture()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative, content=b"fixture"):
        path = self.root / Path(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path

    def _create_fixture(self):
        manifest = package.load_manifest(self.manifest_path)
        self._write("pyproject.toml", f'[project]\nname = "maya_mmd_tools"\nversion = "{self.version}"\n')
        self._write("mmd_tools/__init__.py", f'__version__ = "{self.version}"\n')
        self._write(
            "maya_mmd_tools.mod",
            "\n".join(
                f"+ MAYAVERSION:{maya} maya_mmd_tools {self.version} .\n"
                "scripts: .\nplug-ins: plug-ins\n"
                for maya in manifest["maya_versions"]
            ),
        )
        self._write(
            "cpp/src/pluginMain.cpp",
            f'MFnPlugin plugin(obj, "fixture", "{self.version}", "Any");\n',
        )
        for required in manifest["required"]:
            required_path = Path(required)
            if required in {"mmd_tools", "plug-ins"}:
                (self.root / required_path).mkdir(parents=True, exist_ok=True)
            elif not (self.root / required_path).exists():
                self._write(required, f"{required}\n")
        self._write("docs/README_ja.md", "# 日本語 README\n")
        self._write("README.md", "# README\n")
        self._write("mmd_tools/core.py", "# package\n")
        self._write("plug-ins/mmd_tools_plugin.py", "# plugin\n")
        for platform_name in manifest["platform_policy"]:
            platform_policy = manifest["platforms"][platform_name]
            runtime_bytes = f"{platform_name}-runtime".encode("ascii")
            self._write(platform_policy["native_runtime"], runtime_bytes)
            for maya in manifest["maya_versions"]:
                self._write(
                    platform_policy["plugin"].format(maya_version=maya),
                    f"fixture\0{self.version}\0Any\0toolchain 3.11.3".encode("ascii"),
                )
                self._write(platform_policy["runtime"].format(maya_version=maya), runtime_bytes)
        self._write("resources/icons/icon.txt", "optional\n")
        self._write("mmd_tools/__pycache__/leak.pyc", b"cache")
        self._write("mmd_tools/build/leak.txt", b"build")
        self._write("mmd_tools/tests/leak.py", b"test")

    def _build(self):
        return package.build_and_validate(
            self.root,
            manifest_path=self.manifest_path,
            output_dir=self.root / "dist",
            expected_version=self.version,
        )

    def test_manifest_is_single_source_for_nox_and_workflow(self):
        manifest = package.load_manifest(package.DEFAULT_MANIFEST_PATH)
        self.assertIn("README.md", manifest["required"])
        self.assertIn("docs/README_ja.md", manifest["required"])
        self.assertEqual(manifest["maya_versions"], ["2024", "2025", "2026", "2027"])
        self.assertEqual(package.DEFAULT_MANIFEST_PATH.name, "package_manifest.json")

        import noxfile

        self.assertEqual(noxfile._PACKAGE_MANIFEST_PATH, package.DEFAULT_MANIFEST_PATH)
        workflow = (noxfile.ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("uvx nox -s release_package", workflow)
        self.assertNotIn("cp -R mmd_tools", workflow)
        self.assertNotIn("shutil.make_archive", workflow)

    def test_builds_real_zip_with_minimal_docs_and_exclusions(self):
        result = self._build()
        archive_path = result["archive"]
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertTrue(all(name == "maya_mmd_tools" or name.startswith("maya_mmd_tools/") for name in names))
        self.assertIn("maya_mmd_tools/README.md", names)
        self.assertIn("maya_mmd_tools/docs/README_ja.md", names)
        self.assertIn("maya_mmd_tools/resources/icons/icon.txt", names)
        self.assertNotIn("maya_mmd_tools/CHANGELOG.md", names)
        self.assertFalse(any("__pycache__" in name or "/build/" in name or "/tests/" in name for name in names))
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "pass")
        self.assertTrue((self.root / "build/reports/release_package.md").is_file())
        marker = report["checks"]["platform_artifacts"]["windows"]["maya"]["2024"]["static_version_marker"]
        self.assertEqual(marker["status"], "matched")
        self.assertEqual(marker["observed_versions"], [self.version])

    def test_mismatched_runtime_copy_fails_validation(self):
        manifest = package.load_manifest(self.manifest_path)
        mismatched = self.root / manifest["platforms"]["windows"]["runtime"].format(maya_version="2024")
        mismatched.write_bytes(b"different-runtime")
        with self.assertRaisesRegex(package.PackageValidationError, "windows Maya 2024 runtime digest mismatch"):
            self._build()
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        artifacts = report["checks"]["platform_artifacts"]["windows"]
        self.assertNotEqual(artifacts["native_runtime_sha256"], artifacts["maya"]["2024"]["runtime_sha256"])

    def test_stale_release_plugin_version_fails_validation(self):
        manifest = package.load_manifest(self.manifest_path)
        stale = self.root / manifest["platforms"]["windows"]["plugin"].format(maya_version="2024")
        stale.write_bytes(b"fixture\x000.2.0\x00Any\x00")
        with self.assertRaisesRegex(package.PackageValidationError, "embedded version marker mismatch"):
            self._build()
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        marker = report["checks"]["platform_artifacts"]["windows"]["maya"]["2024"]["static_version_marker"]
        self.assertEqual(marker["status"], "mismatch")
        self.assertEqual(marker["observed_versions"], ["0.2.0"])

    def test_missing_release_plugin_version_marker_fails_validation(self):
        manifest = package.load_manifest(self.manifest_path)
        missing = self.root / manifest["platforms"]["macos"]["plugin"].format(maya_version="2024")
        missing.write_bytes(b"toolchain 3.11.3\x00")
        with self.assertRaisesRegex(package.PackageValidationError, "embedded version marker missing/unobservable"):
            self._build()
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        marker = report["checks"]["platform_artifacts"]["macos"]["maya"]["2024"]["static_version_marker"]
        self.assertEqual(marker["status"], "missing")
        self.assertEqual(marker["observed_versions"], [])

    def test_distinct_plugin_markers_are_ambiguous(self):
        manifest = package.load_manifest(self.manifest_path)
        ambiguous = self.root / manifest["platforms"]["windows"]["plugin"].format(maya_version="2024")
        ambiguous.write_bytes(b"fixture\x009.8.7\x00Any\x00fixture\x000.2.0\x00Any\x00")
        with self.assertRaisesRegex(package.PackageValidationError, "embedded version marker ambiguous"):
            self._build()
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        marker = report["checks"]["platform_artifacts"]["windows"]["maya"]["2024"]["static_version_marker"]
        self.assertEqual(marker["status"], "ambiguous")
        self.assertEqual(marker["observed_versions"], ["0.2.0", self.version])

    def test_missing_release_plugin_fails_closed(self):
        manifest = package.load_manifest(self.manifest_path)
        missing = self.root / manifest["platforms"]["windows"]["plugin"].format(maya_version="2024")
        missing.unlink()
        with self.assertRaisesRegex(package.PackageValidationError, "windows Maya 2024 Release plugin missing"):
            self._build()
        report = json.loads((self.root / "build/reports/release_package.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "fail")

    def test_forbidden_archive_member_fails_validation(self):
        archive_path = self._build()["archive"]
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr("maya_mmd_tools/tests/leak.py", "must not ship")
        with self.assertRaisesRegex(package.PackageValidationError, "forbidden cache/build/test/local artifact"):
            package.validate_archive(
                archive_path,
                self.root,
                manifest_path=self.manifest_path,
                expected_version=self.version,
            )

    def test_cpp_source_version_mismatch_fails_validation(self):
        self._write("cpp/src/pluginMain.cpp", 'MFnPlugin plugin(obj, "fixture", "1.2.3", "Any");\n')
        with self.assertRaisesRegex(package.PackageValidationError, "source C\\+\\+ plugin version"):
            self._build()

    def test_release_gate_runs_package_session_in_tier_one(self):
        import noxfile

        results = []

        def record(name, command, output, **_kwargs):
            results.append((name, command))

        session = mock.Mock(posargs=["--quick"])
        with mock.patch.object(noxfile, "_run_release_gate_command", side_effect=record), mock.patch.object(
            noxfile, "_run_release_gate_callable"
        ), mock.patch.object(
            noxfile,
            "_write_release_gate_reports",
            return_value=(Path("release_gate.md"), Path("release_gate.json")),
        ):
            noxfile.release_gate(session)
        package_commands = [command for name, command in results if name == "tier1:release-package"]
        self.assertEqual(package_commands, [["uvx", "nox", "-s", "release_package"]])

    def test_workflow_runs_required_gates_before_upload(self):
        workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        required_steps = (
            "uvx nox -s ci_unit",
            "uvx nox -s golden_oracle",
            "uvx nox -s release_version",
            "uvx nox -s release_package",
        )
        upload_position = workflow.index("Upload artifact")
        for step in required_steps:
            self.assertLess(workflow.index(step), upload_position)
        self.assertNotIn("cp -R mmd_tools", workflow)


if __name__ == "__main__":
    unittest.main()
