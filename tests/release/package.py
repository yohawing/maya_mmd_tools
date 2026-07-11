"""Build and statically validate the release ZIP from package_manifest.json.

The manifest is the only package file list.  This module intentionally uses
only the Python standard library so the same assembly and validation path can
run from Nox on Windows, macOS, and the Ubuntu GitHub Actions runner.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_MANIFEST_PATH = Path(__file__).with_name("package_manifest.json")
_VERSION_RE = r"[0-9]+\.[0-9]+\.[0-9]+"
_ABSOLUTE_MEMBER_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


class PackageValidationError(RuntimeError):
    """Raised when a release package or its source markers are invalid."""

    def __init__(self, errors: Sequence[str], checks: Optional[Dict[str, Any]] = None):
        self.errors = tuple(str(error) for error in errors)
        self.checks = checks or {}
        super().__init__("Release package validation failed:\n- " + "\n- ".join(self.errors))


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> Dict[str, Any]:
    """Load and validate the release package manifest structure."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Package manifest must contain a JSON object")
    required_keys = {
        "schema_version",
        "archive_root",
        "required",
        "optional_directories",
        "excluded",
        "maya_versions",
        "platform_policy",
        "platforms",
        "version_markers",
    }
    missing = sorted(required_keys - set(manifest))
    if missing:
        raise ValueError("Package manifest is missing keys: " + ", ".join(missing))
    if manifest["schema_version"] != 1:
        raise ValueError(f"Unsupported package manifest schema: {manifest['schema_version']!r}")
    if not isinstance(manifest["required"], list) or not manifest["required"]:
        raise ValueError("Package manifest required must be a non-empty list")
    if not isinstance(manifest["optional_directories"], list):
        raise ValueError("Package manifest optional_directories must be a list")
    if not isinstance(manifest["maya_versions"], list) or not manifest["maya_versions"]:
        raise ValueError("Package manifest maya_versions must be a non-empty list")
    if not isinstance(manifest["platform_policy"], list) or not manifest["platform_policy"]:
        raise ValueError("Package manifest platform_policy must be a non-empty list")
    if set(manifest["platform_policy"]) - set(manifest["platforms"]):
        unknown = sorted(set(manifest["platform_policy"]) - set(manifest["platforms"]))
        raise ValueError("Package manifest has unknown platforms: " + ", ".join(unknown))
    return manifest


def _safe_relative_path(value: str) -> PurePosixPath:
    """Convert a manifest path to a safe, repository-relative POSIX path."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Manifest path must be relative and contained: {value!r}")
    return path


def _source_path(root: Path, value: str) -> Path:
    """Resolve a manifest source path while rejecting paths outside root."""
    relative = _safe_relative_path(value)
    path = (root / Path(*relative.parts)).resolve()
    root = root.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"Manifest path escapes repository root: {value!r}")
    return path


def _excluded(relative: PurePosixPath, manifest: Dict[str, Any]) -> bool:
    """Return whether a source or archive-relative path is forbidden."""
    excluded = manifest["excluded"]
    if not isinstance(excluded, dict):
        raise ValueError("Package manifest excluded must be an object")
    if any(part in set(excluded.get("directory_names", [])) for part in relative.parts):
        return True
    normalized = relative.as_posix()
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in excluded.get("patterns", []))


def _required_entries(manifest: Dict[str, Any]) -> List[PurePosixPath]:
    return [_safe_relative_path(str(value)) for value in manifest["required"]]


def _optional_entries(manifest: Dict[str, Any]) -> List[PurePosixPath]:
    return [_safe_relative_path(str(value)) for value in manifest["optional_directories"]]


def _copy_entry(root: Path, destination_root: Path, relative: PurePosixPath, manifest: Dict[str, Any]) -> None:
    """Copy one required/optional manifest entry while applying exclusions."""
    source = _source_path(root, relative.as_posix())
    if not source.exists():
        raise FileNotFoundError(f"Required package path not found: {relative.as_posix()}")
    if source.is_symlink():
        raise RuntimeError(f"Symlink package paths are not allowed: {relative.as_posix()}")
    if _excluded(relative, manifest):
        raise RuntimeError(f"Manifest entry is excluded by package policy: {relative.as_posix()}")

    destination = destination_root / Path(*relative.parts)
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return

    for candidate in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        candidate_relative = relative / PurePosixPath(candidate.relative_to(source).as_posix())
        if _excluded(candidate_relative, manifest):
            continue
        if candidate.is_symlink():
            raise RuntimeError(f"Symlink package paths are not allowed: {candidate_relative.as_posix()}")
        if not candidate.is_file():
            continue
        target = destination_root / Path(*candidate_relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, target)


def _version_from_pyproject(text: str) -> str:
    """Read project.version with a stdlib TOML parser or a Python 3.7 fallback."""
    try:
        import tomllib
    except ModuleNotFoundError:
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if not match:
            raise ValueError("pyproject.toml project.version is missing")
        return match.group(1)
    project = tomllib.loads(text).get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml project.version is missing")
    return version


def _extract_version(text: str, pattern: str, marker: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"{marker} version marker is missing")
    return match.group(1)


def _extract_cpp_plugin_metadata(text: str, marker: str) -> Tuple[str, str, str]:
    """Read the vendor, version, and API fields from the MFnPlugin marker."""
    match = re.search(
        rf'MFnPlugin\s+plugin\s*\(\s*obj\s*,\s*"([^"]+)"\s*,\s*"({_VERSION_RE})"\s*,\s*"([^"]+)"',
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError(f"{marker} C++ plugin marker is missing")
    return match.group(1), match.group(2), match.group(3)


def _source_markers(root: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Read version markers from the repository source tree."""
    markers = manifest["version_markers"]
    pyproject_path = _source_path(root, markers["pyproject"])
    package_path = _source_path(root, markers["python_package"])
    module_path = _source_path(root, markers["maya_module"])
    cpp_path = _source_path(root, markers["cpp_source"])
    version = _version_from_pyproject(pyproject_path.read_text(encoding="utf-8"))
    package_version = _extract_version(
        package_path.read_text(encoding="utf-8"),
        rf"__version__\s*=\s*[\"']({_VERSION_RE})[\"']",
        markers["python_package"],
    )
    module_matches = re.findall(
        rf"^\+\s+MAYAVERSION:(\d+)\s+maya_mmd_tools\s+({_VERSION_RE})\s+\.",
        module_path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    cpp_vendor, cpp_version, cpp_api = _extract_cpp_plugin_metadata(
        cpp_path.read_text(encoding="utf-8"), markers["cpp_source"]
    )
    expected_maya = [str(version_number) for version_number in manifest["maya_versions"]]
    return {
        "version": version,
        "python_package": package_version,
        "maya_module": module_matches,
        "cpp_source": cpp_version,
        "cpp_vendor": cpp_vendor,
        "cpp_api": cpp_api,
        "maya_versions": expected_maya,
    }


def _embedded_plugin_versions(plugin_bytes: bytes, vendor: str, api: str) -> List[str]:
    """Extract only the NUL-delimited MFnPlugin vendor/version/API marker.

    Binary plugins also contain unrelated SemVer strings from toolchains (for
    example Python 3.11.3).  A candidate is a plugin marker only when it is
    part of the vendor/API tuple emitted by MFnPlugin.  The two layouts cover
    the current Windows and macOS object formats; repeated identical markers
    are harmless, while distinct observed versions remain ambiguous.
    """
    try:
        vendor_bytes = vendor.encode("ascii")
        api_bytes = api.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("C++ plugin vendor/API marker must be ASCII") from exc
    version = rb"([0-9]+\.[0-9]+\.[0-9]+)"
    patterns = (
        re.compile(re.escape(vendor_bytes) + rb"\x00" + version + re.escape(b"\x00" + api_bytes + b"\x00")),
        re.compile(
            re.escape(api_bytes)
            + rb"\x00"
            + version
            + rb"\x00(?:\x00){0,16}"
            + re.escape(vendor_bytes)
            + rb"\x00"
        ),
    )
    observed: List[str] = []
    for pattern in patterns:
        observed.extend(match.group(1).decode("ascii") for match in pattern.finditer(plugin_bytes))
    return observed


def _member_names(archive: zipfile.ZipFile, archive_root: str) -> Tuple[List[str], List[str]]:
    """Return normalized member names and structural archive errors."""
    names: List[str] = []
    errors: List[str] = []
    root = _safe_relative_path(archive_root).as_posix()
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name in names:
            errors.append(f"archive contains an empty or duplicate member: {info.filename!r}")
            continue
        names.append(name)
        if _ABSOLUTE_MEMBER_RE.match(info.filename) or ".." in PurePosixPath(name).parts:
            errors.append(f"archive member is an absolute/path-traversal artifact: {info.filename!r}")
        if name != root and not name.startswith(root + "/"):
            errors.append(f"archive member is outside {root}/: {info.filename!r}")
    if not names:
        errors.append("archive is empty")
    return names, errors


def _archive_has(names: Iterable[str], root: str, relative: PurePosixPath) -> bool:
    target = f"{root}/{relative.as_posix()}"
    return target in names or any(name.startswith(target.rstrip("/") + "/") for name in names)


def _validate_archive_contract(
    archive_path: Path,
    root: Path,
    manifest: Dict[str, Any],
    expected_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate archive structure, artifacts, source markers, and versions."""
    errors: List[str] = []
    checks: Dict[str, Any] = {}
    archive_root = _safe_relative_path(str(manifest["archive_root"])).as_posix()
    source = _source_markers(root, manifest)
    checks["source_markers"] = source
    if expected_version and source["version"] != expected_version:
        errors.append(f"source version {source['version']} does not match requested version {expected_version}")
    if source["python_package"] != source["version"]:
        errors.append(f"source package __version__ {source['python_package']} does not match {source['version']}")
    if source["cpp_source"] != source["version"]:
        errors.append(f"source C++ plugin version {source['cpp_source']} does not match {source['version']}")
    expected_maya = set(source["maya_versions"])
    module_versions = {maya: version for maya, version in source["maya_module"]}
    if set(module_versions) != expected_maya or set(module_versions.values()) != {source["version"]}:
        errors.append(
            f"source maya_mmd_tools.mod entries {sorted(source['maya_module'])} "
            f"do not match Maya {sorted(expected_maya)} at version {source['version']}"
        )

    with zipfile.ZipFile(archive_path) as archive:
        try:
            corrupt = archive.testzip()
        except zipfile.BadZipFile as exc:
            errors.append(f"archive is not a valid ZIP: {exc}")
            corrupt = None
        if corrupt:
            errors.append(f"archive member is corrupt: {corrupt}")
        names, structural_errors = _member_names(archive, archive_root)
        errors.extend(structural_errors)
        root_entries = [name for name in names if name != archive_root and name.startswith(archive_root + "/")]
        if not root_entries:
            errors.append(f"archive root {archive_root}/ has no files")
        allowed_top_level = set()
        for relative in _required_entries(manifest) + _optional_entries(manifest):
            allowed_top_level.add(relative.parts[0])
        for name in root_entries:
            relative_name = name[len(archive_root) + 1 :]
            relative = PurePosixPath(relative_name)
            if relative.parts and relative.parts[0] not in allowed_top_level:
                errors.append(f"unexpected top-level package path: {relative.parts[0]}")
            if _excluded(relative, manifest):
                errors.append(f"forbidden cache/build/test/local artifact in archive: {relative.as_posix()}")

        for required in _required_entries(manifest):
            if not _archive_has(names, archive_root, required):
                errors.append(f"required package path missing: {required.as_posix()}")

        archive_pyproject = f"{archive_root}/{manifest['version_markers']['pyproject']}"
        archive_package = f"{archive_root}/{manifest['version_markers']['python_package']}"
        archive_module = f"{archive_root}/{manifest['version_markers']['maya_module']}"
        for marker_path in (archive_pyproject, archive_package, archive_module):
            if marker_path not in names:
                errors.append(f"packaged version marker missing: {marker_path}")
        if archive_pyproject in names:
            archive_version = _version_from_pyproject(archive.read(archive_pyproject).decode("utf-8"))
            checks["archive_version"] = archive_version
            if archive_version != source["version"]:
                errors.append(f"packaged pyproject version {archive_version} does not match source {source['version']}")
        if archive_package in names:
            archive_package_version = _extract_version(
                archive.read(archive_package).decode("utf-8"),
                rf"__version__\s*=\s*[\"']({_VERSION_RE})[\"']",
                manifest["version_markers"]["python_package"],
            )
            checks["archive_python_package_version"] = archive_package_version
            if archive_package_version != source["version"]:
                errors.append(
                    f"packaged __version__ {archive_package_version} does not match source {source['version']}"
                )
        if archive_module in names:
            archive_module_matches = re.findall(
                rf"^\+\s+MAYAVERSION:(\d+)\s+maya_mmd_tools\s+({_VERSION_RE})\s+\.",
                archive.read(archive_module).decode("utf-8"),
                re.MULTILINE,
            )
            checks["archive_maya_module"] = archive_module_matches
            archive_module_versions = {maya: version for maya, version in archive_module_matches}
            if set(archive_module_versions) != expected_maya or set(archive_module_versions.values()) != {source["version"]}:
                errors.append(
                    f"packaged maya_mmd_tools.mod entries {sorted(archive_module_matches)} do not match "
                    f"Maya {sorted(expected_maya)} at version {source['version']}"
                )

        platform_evidence: Dict[str, Any] = {}
        for platform_name in manifest["platform_policy"]:
            platform = manifest["platforms"][platform_name]
            native_runtime = PurePosixPath(platform["native_runtime"])
            if not _archive_has(names, archive_root, native_runtime) or f"{archive_root}/{native_runtime.as_posix()}" not in names:
                errors.append(f"{platform_name} native runtime missing: {native_runtime.as_posix()}")
            elif archive.getinfo(f"{archive_root}/{native_runtime.as_posix()}").file_size == 0:
                errors.append(f"{platform_name} native runtime is empty: {native_runtime.as_posix()}")
            per_version: Dict[str, Any] = {}
            for maya_version in manifest["maya_versions"]:
                plugin = PurePosixPath(str(platform["plugin"]).format(maya_version=maya_version))
                runtime = PurePosixPath(str(platform["runtime"]).format(maya_version=maya_version))
                plugin_name = f"{archive_root}/{plugin.as_posix()}"
                runtime_name = f"{archive_root}/{runtime.as_posix()}"
                for artifact, artifact_name in (("plugin", plugin_name), ("runtime", runtime_name)):
                    if artifact_name not in names:
                        errors.append(f"{platform_name} Maya {maya_version} Release {artifact} missing: {artifact_name}")
                    elif archive.getinfo(artifact_name).file_size == 0:
                        errors.append(f"{platform_name} Maya {maya_version} Release {artifact} is empty: {artifact_name}")
                if plugin_name in names:
                    plugin_bytes = archive.read(plugin_name)
                    observed_versions = _embedded_plugin_versions(
                        plugin_bytes,
                        source["cpp_vendor"],
                        source["cpp_api"],
                    )
                    distinct_versions = sorted(set(observed_versions))
                    marker_prefix = f"{platform_name} Maya {maya_version} Release plugin embedded version marker"
                    if not distinct_versions:
                        marker_status = "missing"
                        errors.append(
                            f"{marker_prefix} missing/unobservable; expected {source['version']}; observed versions []"
                        )
                    elif len(distinct_versions) > 1:
                        marker_status = "ambiguous"
                        errors.append(
                            f"{marker_prefix} ambiguous; expected {source['version']}; "
                            f"observed versions {distinct_versions}"
                        )
                    elif distinct_versions[0] != source["version"]:
                        marker_status = "mismatch"
                        errors.append(
                            f"{marker_prefix} mismatch; expected {source['version']}; "
                            f"observed versions {distinct_versions}"
                        )
                    else:
                        marker_status = "matched"
                    per_version[maya_version] = {
                        "plugin": plugin.as_posix(),
                        "runtime": runtime.as_posix(),
                        "static_version_marker": {
                            "status": marker_status,
                            "observed_versions": distinct_versions,
                            "occurrence_count": len(observed_versions),
                            "vendor": source["cpp_vendor"],
                            "api": source["cpp_api"],
                        },
                    }
            platform_evidence[platform_name] = {
                "native_runtime": native_runtime.as_posix(),
                "maya": per_version,
            }
        checks["platform_artifacts"] = platform_evidence

    if errors:
        raise PackageValidationError(errors, checks=checks)
    checks["archive_root"] = archive_root
    checks["required_paths"] = [path.as_posix() for path in _required_entries(manifest)]
    checks["optional_directories"] = [path.as_posix() for path in _optional_entries(manifest)]
    checks["excluded_policy"] = manifest["excluded"]
    return checks


def validate_archive(
    archive_path: Path,
    root: Path,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    expected_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate an existing ZIP and return JSON-serializable evidence."""
    manifest = load_manifest(manifest_path)
    return _validate_archive_contract(archive_path, root, manifest, expected_version=expected_version)


def _write_reports(
    root: Path,
    archive_path: Path,
    manifest_path: Path,
    version: Optional[str],
    status: str,
    checks: Optional[Dict[str, Any]],
    errors: Sequence[str],
) -> Tuple[Path, Path]:
    """Write package evidence in the shared build/reports directory."""
    report_dir = root / "build" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "release_package.json"
    md_path = report_dir / "release_package.md"

    def display(path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return str(path)

    payload = {
        "status": status,
        "version": version,
        "archive": display(archive_path),
        "manifest": display(manifest_path),
        "checks": checks or {},
        "errors": list(errors),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Release Package Validation",
        "",
        f"- Status: {status}",
        f"- Version: {version or 'unknown'}",
        f"- Archive: `{payload['archive']}`",
        f"- Manifest: `{payload['manifest']}`",
        "",
        "## Errors",
        "",
    ]
    lines.extend(f"- {error}" for error in errors) if errors else lines.append("- None")
    lines.extend(["", "## Evidence", "", "```json", json.dumps(checks or {}, ensure_ascii=False, indent=2), "```", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_and_validate(
    root: Path,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    output_dir: Optional[Path] = None,
    expected_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the manifest package, validate it, and write release evidence."""
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    output_dir = (output_dir or (root / "dist")).resolve()
    archive_path = output_dir / "maya_mmd_tools-package.zip"
    version: Optional[str] = None
    checks: Optional[Dict[str, Any]] = None
    errors: List[str] = []
    try:
        manifest = load_manifest(manifest_path)
        source_markers = _source_markers(root, manifest)
        version = source_markers["version"]
        archive_path = output_dir / f"maya_mmd_tools-{version}.zip"
        output_dir.mkdir(parents=True, exist_ok=True)
        if archive_path.exists():
            archive_path.unlink()
        with tempfile.TemporaryDirectory(prefix="maya-mmd-tools-package-") as temporary:
            staging_root = Path(temporary) / str(manifest["archive_root"])
            staging_root.mkdir(parents=True, exist_ok=True)
            for relative in _required_entries(manifest):
                _copy_entry(root, staging_root, relative, manifest)
            for relative in _optional_entries(manifest):
                optional = _source_path(root, relative.as_posix())
                if optional.exists():
                    _copy_entry(root, staging_root, relative, manifest)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in sorted(staging_root.rglob("*"), key=lambda item: item.as_posix()):
                    if file_path.is_file():
                        archive.write(file_path, PurePosixPath(file_path.relative_to(Path(temporary)).as_posix()).as_posix())
        checks = _validate_archive_contract(
            archive_path,
            root,
            manifest,
            expected_version=expected_version,
        )
    except Exception as exc:
        if isinstance(exc, PackageValidationError):
            errors.extend(exc.errors)
            checks = exc.checks
        else:
            errors.append(str(exc))
        _write_reports(root, archive_path, manifest_path, version, "fail", checks, errors)
        raise
    _write_reports(root, archive_path, manifest_path, version, "pass", checks, errors)
    return {
        "status": "pass",
        "version": version,
        "archive": archive_path,
        "manifest": manifest_path,
        "checks": checks or {},
    }
