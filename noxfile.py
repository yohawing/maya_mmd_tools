"""Cross-platform development task runner for maya_mmd_tools.

Nox is used as a thin orchestration layer around existing project tools:
Maya tests still run through mayapy, C++ builds still run through CMake, and
mmd-anim still builds through Cargo. Sessions use the current Python process
instead of creating a separate virtual environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

import nox

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_location import maya_location as _maya_location  # noqa: E402
from tests.common.maya_location import mayapy as _mayapy  # noqa: E402
from tests.common.maya_location import convert_path_options_for_maya_process as _convert_maya_path_options  # noqa: E402
from tests.common.maya_location import path_for_maya_process as _maya_process_path  # noqa: E402
from tests.common.maya_location import pythonpath_for_maya_process as _maya_pythonpath  # noqa: E402
from tests.common.maya_location import resolve_path_for_maya_process as _resolve_maya_path  # noqa: E402
from tests.common.output_hygiene import (  # noqa: E402
    compact_failure_details_from_log as _compact_failure_details_from_log,
)
from tests.common.output_hygiene import (  # noqa: E402
    format_summary as _format_test_summary,
)
from tests.common.output_hygiene import run_logged_subprocess as _run_logged_subprocess  # noqa: E402
from tests.common.output_hygiene import safe_log_name as _safe_log_name  # noqa: E402
from tests.release.package import DEFAULT_MANIFEST_PATH as _PACKAGE_MANIFEST_PATH  # noqa: E402
from tests.release.package import build_and_validate as _build_release_package  # noqa: E402


DEFAULT_MAYA_VERSION = "2024"
DEFAULT_RELEASE_MAYA_VERSIONS = ("2024", "2025", "2026", "2027")
DEFAULT_CMAKE_CONFIG = "Debug"
DEFAULT_CPP_VERIFY_MAYA_VERSIONS = DEFAULT_RELEASE_MAYA_VERSIONS
DEFAULT_RELEASE_VIEWPORT_MATRIX = (
    ("2025", "glsl", "glcore"),
    ("2026", "dx11", "dx11"),
)
DEFAULT_GOLDEN_ORACLE_RENDER_MANIFEST = "F:/Develop/MMDDev/GoldenOracle/manifests/fixture.render.json"
RELEASE_VISUAL_CASES = (
    "fixture-render-generated-visual-mmd-diffuse-lit-box",
    "fixture-render-generated-visual-mmd-toon-ramp-lit-box",
    "fixture-render-generated-visual-mmd-texture-uv-orientation-plane",
    "fixture-render-generated-visual-mmd-sphere-texture-add",
    "fixture-render-generated-visual-mmd-alpha-blend-overlap",
)
MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS = 0x3
RELEASE_CAMERA_CURRENT_EPSILON = "18.25"
RELEASE_ADDICTION_INTERPOLATION_EYE_MAX = "2.0"
RELEASE_ADDICTION_INTERPOLATION_FORWARD_MAX_DEG = "5.0"
RELEASE_ADDICTION_INTERPOLATION_UP_MAX_DEG = "5.0"
RELEASE_ADDICTION_INTERPOLATION_ROTATION_MAX_DEG = "5.0"
RELEASE_ADDICTION_CAMERA_VMD = (
    "F:/MMD/vmd/175_Addictionカメラモーションv1.3/"
    "Addictionカメラモーション/Addictionカメラ用モーション(一人用).vmd"
)
MMD_ANIM_CLI_VERSION = "v0.2.0"
MMD_ANIM_CLI_REPO = "yohawing/mmd-anim"
MMD_ANIM_CLI_ASSETS = {
    "Windows": {
        "archive": "mmd-anim-v0.2.0-x86_64-pc-windows-msvc.zip",
        "sha256": "e50315413aec8525ca4d04f8ea5e2770d1656cb7e7de8b7987db7e3f3218405f",
        "exe": "mmd-anim.exe",
    },
    "Linux": {
        "archive": "mmd-anim-v0.2.0-x86_64-unknown-linux-gnu.tar.gz",
        "sha256": "11dedde4b929aaca53d9e4ce6966627425fcba31beba61686e7a9e7c87c1ae0e",
        "exe": "mmd-anim",
    },
}

nox.options.sessions = ["tests"]


def _release_visual_cases(_shader_backend: str) -> tuple[str, ...]:
    return RELEASE_VISUAL_CASES


def _option(args: list[str], name: str, default: str) -> str:
    """Return a string option value from nox positional arguments."""
    try:
        index = args.index(name)
    except ValueError:
        return default
    try:
        return args[index + 1]
    except IndexError as exc:
        raise ValueError(f"{name} requires a value") from exc


def _options(args: list[str], name: str) -> list[str]:
    """Return all string option values from nox positional arguments."""
    values: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == name:
            if i + 1 >= len(args):
                raise ValueError(f"{name} requires a value")
            values.append(args[i + 1])
            i += 2
            continue
        i += 1
    return values


def _without_option(args: list[str], name: str) -> list[str]:
    """Return args with a single value option removed."""
    filtered: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == name:
            if i + 1 >= len(args):
                raise ValueError(f"{name} requires a value")
            i += 2
            continue
        filtered.append(args[i])
        i += 1
    return filtered


def _has_flag(args: list[str], name: str) -> bool:
    """Return True if a boolean flag is present in positional arguments."""
    return name in args


def _cargo_args_with_physics_feature(args: list[str]) -> list[str]:
    """Return cargo args that enable the native Bullet physics runtime feature."""
    feature = "physics-bullet-native"
    cargo_args = list(args)
    for index, value in enumerate(cargo_args):
        if value == "--features":
            if index + 1 >= len(cargo_args):
                raise ValueError("--features requires a value")
            features = cargo_args[index + 1].replace(",", " ").split()
            if feature not in features:
                features.append(feature)
                cargo_args[index + 1] = " ".join(features)
            return cargo_args
        if value.startswith("--features="):
            features = value.split("=", 1)[1].replace(",", " ").split()
            if feature not in features:
                features.append(feature)
                cargo_args[index] = "--features=" + " ".join(features)
            return cargo_args
    cargo_args.extend(["--features", feature])
    return cargo_args


def _native_runtime_smoke_code() -> str:
    """Return Python code that verifies the runtime ABI and required features."""
    return (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "required = "
        f"{MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS}; "
        "lib = get_mmd_runtime_library(); "
        "path = get_runtime_library_path(); "
        "flags = lib.mmd_runtime_feature_flags() if lib and hasattr(lib, 'mmd_runtime_feature_flags') else 0; "
        "abi = lib.mmd_runtime_abi_version() if lib else 0; "
        "print(path); "
        "print({'abi': abi, 'featureFlags': hex(flags), 'requiredFeatureFlags': hex(required)}); "
        "raise SystemExit(0 if lib and abi == 2 and (flags & required) == required else 1)"
    )


def _configure_bullet3_dir(session: nox.Session, env: dict[str, str]) -> None:
    """Set a local Bullet checkout for physics-enabled mmd-anim builds when available."""
    if env.get("MMD_ANIM_BULLET3_DIR"):
        return
    candidates = [
        ROOT / "external" / "mmd-anim" / "bullet3",
        ROOT.parent / "MMDDev" / "bullet3",
        ROOT.parent / "bullet3",
    ]
    for candidate in candidates:
        if (candidate / "src").exists():
            env["MMD_ANIM_BULLET3_DIR"] = str(candidate.resolve())
            session.log(f"Using Bullet sources: {env['MMD_ANIM_BULLET3_DIR']}")
            return


def _require_build_path(session: nox.Session, value: str, option_name: str) -> Path:
    """Resolve an output path and require it to stay under build/."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    build_root = (ROOT / "build").resolve()
    if path != build_root and build_root not in path.parents:
        session.error(f"{option_name} must resolve under {build_root}: {path}")
    return path


def _resolve_existing_or_repo_path(value: str) -> Path:
    """Resolve an input path from absolute or repository-relative text."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mmd_anim_cli_version(exe: Path) -> str:
    """Return the first non-empty line of the mmd-anim CLI version output."""
    result = subprocess.run(
        [str(exe), "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")


def _download_file(url: str, destination: Path) -> None:
    """Download a URL to a local file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract_archive(archive: Path, destination: Path) -> None:
    """Extract a supported mmd-anim release archive."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(destination)
    elif archive.name.endswith(".tar.gz"):
        with tarfile.open(archive, "r:gz") as tar_file:
            tar_file.extractall(destination)
    else:
        raise ValueError(f"Unsupported archive format: {archive}")


def _windows_processes_locking_module(path: Path) -> list[str]:
    """Return Windows processes that have a DLL loaded, best-effort."""
    if platform.system() != "Windows" or not path.exists():
        return []
    target = str(path).replace("'", "''")
    script = (
        "$target = '"
        + target
        + "'; "
        "$rows = Get-Process | Where-Object { "
        "try { $_.Modules | Where-Object { $_.FileName -eq $target } } catch { $false } "
        "} | Select-Object Id,ProcessName,Path; "
        "$rows | ForEach-Object { \"$($_.Id) $($_.ProcessName) $($_.Path)\" }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _downloaded_mmd_anim_cli(session: nox.Session) -> Path:
    """Return the pinned mmd-anim CLI downloaded from GitHub Releases."""
    expected_version = f"mmd-anim {MMD_ANIM_CLI_VERSION.removeprefix('v')}"
    override = os.environ.get("MMD_ANIM_CLI")
    if override:
        exe = Path(override)
        if not exe.exists():
            session.error(f"MMD_ANIM_CLI does not exist: {exe}")
        version = _mmd_anim_cli_version(exe)
        if version != expected_version:
            session.error(f"MMD_ANIM_CLI has unexpected version: {version}; expected {expected_version}")
        return exe

    system = platform.system()
    asset = MMD_ANIM_CLI_ASSETS.get(system)
    if asset is None:
        session.error(
            f"No prebuilt mmd-anim CLI asset is configured for {system}. "
            "Set MMD_ANIM_CLI to a compatible binary."
        )

    archive_name = asset["archive"]
    expected_sha256 = asset["sha256"]
    exe_name = asset["exe"]
    url = (
        f"https://github.com/{MMD_ANIM_CLI_REPO}/releases/download/"
        f"{MMD_ANIM_CLI_VERSION}/{archive_name}"
    )
    tool_dir = ROOT / "build" / "tools" / "mmd-anim" / MMD_ANIM_CLI_VERSION / system.lower()
    archive = tool_dir / archive_name
    extract_dir = tool_dir / "extract"

    if not archive.exists() or _sha256_file(archive) != expected_sha256:
        session.log(f"Downloading {url}")
        _download_file(url, archive)
    actual_sha256 = _sha256_file(archive)
    if actual_sha256 != expected_sha256:
        session.error(
            f"SHA-256 mismatch for {archive_name}: got {actual_sha256}, expected {expected_sha256}"
        )

    candidates = [p for p in extract_dir.rglob(exe_name) if p.is_file()]
    if not candidates:
        _extract_archive(archive, extract_dir)
        candidates = [p for p in extract_dir.rglob(exe_name) if p.is_file()]
    if not candidates:
        session.error(f"{exe_name} was not found in {archive_name}")
    exe = candidates[0]
    if system != "Windows":
        exe.chmod(exe.stat().st_mode | 0o755)

    version = _mmd_anim_cli_version(exe)
    if version != expected_version:
        shutil.rmtree(extract_dir, ignore_errors=True)
        _extract_archive(archive, extract_dir)
        candidates = [p for p in extract_dir.rglob(exe_name) if p.is_file()]
        if not candidates:
            session.error(f"{exe_name} was not found in {archive_name}")
        exe = candidates[0]
        if system != "Windows":
            exe.chmod(exe.stat().st_mode | 0o755)
        version = _mmd_anim_cli_version(exe)
    if version != expected_version:
        session.error(f"Downloaded mmd-anim CLI has unexpected version: {version}; expected {expected_version}")
    return exe


def _mayapy_env(mayapy: Path, preserve_pythonpath: bool = False, **extra: str) -> dict[str, str]:
    """Return environment values with repo paths suitable for mayapy."""
    env = {
        **os.environ,
        "PYTHONPATH": _maya_pythonpath(
            mayapy,
            ROOT,
            os.environ.get("PYTHONPATH"),
            preserve_existing=preserve_pythonpath,
        ),
    }
    env.update(extra)
    return env


def _mayapy_script(mayapy: Path, relative_script: str) -> str:
    """Return an absolute script path suitable for the resolved mayapy."""
    return _maya_process_path(mayapy, ROOT / relative_script)


def _mayapy_arg_path(mayapy: Path, value: str | Path) -> str:
    """Return a path argument suitable for the resolved mayapy."""
    return _resolve_maya_path(mayapy, ROOT, value)


def _convert_mayapy_path_options(mayapy: Path, args: list[str], path_options: set[str]) -> list[str]:
    """Convert values following path-like options for a mayapy child process."""
    return _convert_maya_path_options(mayapy, ROOT, args, path_options)


def _copy_parity_vmd_for_mayapy(session: nox.Session, args: list[str]) -> list[str]:
    """Copy a non-ASCII --parity-vmd path to an ASCII build path for mayapy argv."""
    if "--parity-vmd" not in args:
        return args
    index = args.index("--parity-vmd")
    if index + 1 >= len(args):
        return args
    source = Path(args[index + 1])
    if not source.exists():
        return args
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    alias_dir = ROOT / "build" / "local-camera-motion-oracle" / "local-assets"
    alias_dir.mkdir(parents=True, exist_ok=True)
    alias = alias_dir / f"camera-parity-{digest}.vmd"
    if not alias.exists() or alias.stat().st_size != source.stat().st_size or alias.stat().st_mtime < source.stat().st_mtime:
        shutil.copy2(source, alias)
        session.log(f"Copied parity VMD for mayapy argv: {source} -> {alias}")
    rewritten = list(args)
    rewritten[index + 1] = str(alias)
    return rewritten


def _release_gate_version_check(expected_version: str | None = None) -> None:
    """Validate release version markers before running expensive gates."""
    import re
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    if expected_version and version != expected_version:
        raise RuntimeError(f"pyproject.toml version {version} does not match requested release version {expected_version}")

    init_text = (ROOT / "mmd_tools" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if not init_match or init_match.group(1) != version:
        raise RuntimeError(f"mmd_tools/__init__.py version does not match pyproject.toml: {version}")

    mod_text = (ROOT / "maya_mmd_tools.mod").read_text(encoding="utf-8")
    mod_versions = set(re.findall(r"maya_mmd_tools\s+([0-9]+\.[0-9]+\.[0-9]+)", mod_text))
    if mod_versions != {version}:
        raise RuntimeError(f"maya_mmd_tools.mod versions {sorted(mod_versions)} do not match {version}")

    plugin_text = (ROOT / "cpp" / "src" / "pluginMain.cpp").read_text(encoding="utf-8")
    plugin_match = re.search(
        r'MFnPlugin\s+plugin\s*\(\s*obj\s*,\s*"[^"]+"\s*,\s*"([^"]+)"',
        plugin_text,
    )
    if not plugin_match or plugin_match.group(1) != version:
        raise RuntimeError(f"cpp/src/pluginMain.cpp version does not match pyproject.toml: {version}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = f"## [{version}]"
    start = changelog.find(heading)
    if start == -1:
        raise RuntimeError(f"CHANGELOG.md is missing {heading}")
    next_heading = changelog.find("\n## [", start + len(heading))
    section = changelog[start: next_heading if next_heading != -1 else len(changelog)]
    body_lines = [
        line.strip()
        for line in section.splitlines()[1:]
        if line.strip() and not line.strip().startswith("[")
    ]
    if not body_lines:
        raise RuntimeError(f"CHANGELOG.md section {heading} is empty")


def _release_gate_mmd_anim_pin_check(root: Path | None = None) -> None:
    """Require the checked-out mmd-anim HEAD to match the parent gitlink."""
    root = ROOT if root is None else root
    relative_path = "external/mmd-anim"
    submodule = root / "external" / "mmd-anim"
    if not submodule.is_dir() or not (submodule / ".git").exists():
        raise RuntimeError(
            f"{relative_path} is not initialized; release provenance cannot be verified. "
            "Initialize the pinned submodule before running release_gate."
        )

    def git_output(arguments: list[str], cwd: Path) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git executable is unavailable; release provenance cannot be verified."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Failed to verify {relative_path} release provenance with "
                f"git {' '.join(arguments)}{suffix}"
            )
        return completed.stdout.rstrip("\r\n")

    gitlink_line = git_output(["ls-tree", "HEAD", "--", relative_path], root)
    gitlink_match = re.fullmatch(
        rf"160000 commit ([0-9a-fA-F]{{40,64}})\t{re.escape(relative_path)}",
        gitlink_line,
    )
    if gitlink_match is None:
        raise RuntimeError(
            f"Parent HEAD does not contain a valid gitlink for {relative_path}; "
            "release provenance cannot be verified."
        )
    parent_head = gitlink_match.group(1).lower()

    checkout_head = git_output(["rev-parse", "--verify", "HEAD"], submodule).lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", checkout_head) is None:
        raise RuntimeError(
            f"{relative_path} returned an invalid checkout HEAD {checkout_head!r}; "
            "release provenance cannot be verified."
        )
    if checkout_head != parent_head:
        raise RuntimeError(
            f"{relative_path} pin mismatch: parent gitlink={parent_head}, "
            f"checkout HEAD={checkout_head}. Restore or initialize the pinned submodule "
            "before running release_gate; automatic checkout/reset is intentionally disabled."
        )
    dirty_status = git_output(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        submodule,
    )
    if dirty_status:
        status_summary = dirty_status.replace("\r\n", "\n").replace("\n", "; ")
        raise RuntimeError(
            f"{relative_path} worktree is dirty; release provenance cannot be verified. "
            f"Git status: {status_summary}. Commit, stash, or remove these changes before "
            "running release_gate; automatic cleanup is intentionally disabled."
        )


def _run_release_gate_command(
    name: str,
    command: list[str],
    results: list[dict[str, object]],
    *,
    result_report: Path | None = None,
    required_local: bool = False,
    strict_local: bool = False,
    verbose: bool = False,
) -> None:
    """Run a command quietly, retain its transcript, and record its result."""
    started = time.perf_counter()
    if result_report is not None and result_report.exists():
        result_report.unlink()
    returncode, log_path, (_, repeated_warnings) = _run_logged_subprocess(
        command,
        log_path=ROOT / "build" / "reports" / "release_gate" / f"{_safe_log_name(name)}.log",
        cwd=ROOT,
        verbose=verbose,
    )
    status = "pass" if returncode == 0 else "fail"
    detail = None
    if result_report is not None and result_report.is_file():
        try:
            child_status = str(json.loads(result_report.read_text(encoding="utf-8")).get("status", "")).lower()
        except (OSError, ValueError, TypeError) as exc:
            status = "fail"
            detail = f"invalid child report {result_report}: {exc}"
        else:
            status_aliases = {"pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail", "skip": "skip", "skipped": "skip"}
            if child_status not in status_aliases:
                status = "fail"
                detail = f"invalid child status in {result_report}: {child_status!r}"
            elif returncode == 0:
                status = status_aliases[child_status]
    if status == "skip" and required_local and strict_local:
        status = "fail"
        detail = "required local gate skipped under --strict-local"
    duration_sec = round(time.perf_counter() - started, 3)
    first_failure, failed_tests = _compact_failure_details_from_log(log_path)
    result = {
        "name": name,
        "command": command,
        "status": status,
        "returncode": returncode,
        "duration_sec": duration_sec,
        "log": str(log_path),
        "repeated_warnings_suppressed": repeated_warnings,
        **({"first_failure": first_failure} if first_failure else {}),
        **({"failed_tests": failed_tests} if failed_tests else {}),
        **({"detail": detail} if detail else {}),
    }
    results.append(result)
    print(
        _format_test_summary(
            name,
            total=1,
            passed=int(status == "pass"),
            skipped=int(status == "skip"),
            failed=int(status == "fail"),
            duration_sec=duration_sec,
        )
    )
    if repeated_warnings and not verbose:
        print(f"[{name}] repeated warnings suppressed from terminal: {repeated_warnings}")
    if status == "fail":
        print(f"[{name}] first failure: {first_failure or name}")
        if failed_tests:
            print(f"[{name}] failed tests: {', '.join(failed_tests)}")
        print(f"[{name}] full log: {log_path}")


def _run_release_gate_callable(
    name: str,
    func,
    results: list[dict[str, object]],
) -> None:
    """Run an in-process release-gate step and append a keep-going result entry."""
    started = time.perf_counter()
    try:
        func()
    except Exception as exc:
        result = {
                "name": name,
                "command": [],
                "status": "fail",
                "returncode": 1,
                "duration_sec": round(time.perf_counter() - started, 3),
                "error": str(exc),
            }
    else:
        result = {
                "name": name,
                "command": [],
                "status": "pass",
                "returncode": 0,
                "duration_sec": round(time.perf_counter() - started, 3),
            }
    results.append(result)
    print(
        _format_test_summary(
            name,
            total=1,
            passed=int(result["status"] == "pass"),
            skipped=0,
            failed=int(result["status"] == "fail"),
            duration_sec=float(result["duration_sec"]),
        )
    )
    if result["status"] == "fail":
        print(f"[{name}] first failure: {result.get('error', name)}")


def _release_gate_failure_label(result: dict[str, object]) -> str:
    """Return the best available compact failure detail for an aggregate gate."""
    return str(
        result.get("first_failure")
        or result.get("error")
        or result.get("name")
        or "unknown failure"
    )


def _write_release_gate_reports(results: list[dict[str, object]], quick: bool) -> tuple[Path, Path]:
    """Write release-gate Markdown and JSON summaries."""
    report_dir = ROOT / "build" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "release_gate.json"
    md_path = report_dir / "release_gate.md"

    counts = {status: sum(result["status"] == status for result in results) for status in ("pass", "fail", "skip")}
    aggregate_status = "fail" if counts["fail"] else "pass" if counts["pass"] else "skip"
    payload = {
        "quick": quick,
        "status": aggregate_status,
        "summary": counts,
        "results": results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Release Gate",
        "",
        f"- Mode: {'quick' if quick else 'full'}",
        f"- Status: {payload['status']}",
        f"- Summary: pass={counts['pass']}, fail={counts['fail']}, skip={counts['skip']}",
        "",
        "| Step | Status | Seconds | Command |",
        "| --- | --- | ---: | --- |",
    ]
    for result in results:
        command = " ".join(str(part) for part in result.get("command") or [])
        if not command:
            command = str(result.get("error", "in-process"))
        lines.append(
            f"| {result['name']} | {result['status']} | {result['duration_sec']} | `{command}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def _normalize_local_gate_report(
    report_path: Path,
    strict_local: bool,
    markdown_path: Path | None = None,
) -> str:
    """Derive and persist a local child gate status in its JSON and Markdown reports."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Local gate report has no results list: {report_path}")
    aliases = {"pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail", "skip": "skip", "skipped": "skip"}
    statuses = []
    for result in results:
        raw_status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if raw_status not in aliases:
            raise ValueError(f"Invalid local gate result status in {report_path}: {raw_status!r}")
        statuses.append(aliases[raw_status])
    if "fail" in statuses or not statuses:
        status = "fail"
    elif "pass" in statuses:
        status = "pass"
    else:
        status = "fail" if strict_local else "skip"
    payload["status"] = status
    summary = {
        candidate: statuses.count(candidate) for candidate in ("pass", "fail", "skip")
    }
    payload["summary"] = summary
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None and markdown_path.is_file():
        lines = markdown_path.read_text(encoding="utf-8").splitlines()
        status_line = f"- Status: {status}"
        summary_line = (
            f"- Summary: pass={summary['pass']}, fail={summary['fail']}, skip={summary['skip']}"
        )
        status_index = next((index for index, line in enumerate(lines) if line.startswith("- Status:")), None)
        if status_index is None:
            lines.extend(["", status_line, summary_line])
        else:
            lines[status_index] = status_line
            if status_index + 1 < len(lines) and lines[status_index + 1].startswith("- Summary:"):
                lines[status_index + 1] = summary_line
            else:
                lines.insert(status_index + 1, summary_line)
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def _import_order_manifest_asset_path(value: str) -> str:
    """Return an absolute path string to store in a generated local manifest."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return str(path.resolve())


def _write_import_order_local_manifest(
    session: nox.Session,
    background_model: str,
    character_model: str,
    character_motion: str,
) -> Path:
    """Write a UTF-8 manifest so mayapy argv stays ASCII for local asset paths."""
    background_model = _import_order_manifest_asset_path(background_model)
    character_model = _import_order_manifest_asset_path(character_model)
    character_motion = _import_order_manifest_asset_path(character_motion)
    manifest_path = _require_build_path(
        session,
        "build/import-order-e2e/generated-local-manifest.json",
        "--manifest",
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "cases": [
            {
                "name": "background_character_permutations",
                "assets": [
                    {
                        "id": "background",
                        "type": "pmx",
                        "role": "background",
                        "path": background_model,
                        "useNamespace": False,
                    },
                    {
                        "id": "character",
                        "type": "pmx",
                        "role": "character",
                        "path": character_model,
                        "useNamespace": False,
                    },
                ],
                "orders": [
                    ["background", "character"],
                    ["character", "background"],
                ],
            },
            {
                "name": "character_motion_camera_clear_permutations",
                "assets": [
                    {
                        "id": "character",
                        "type": "pmx",
                        "role": "character",
                        "path": character_model,
                        "useNamespace": True,
                    },
                    {
                        "id": "character_motion",
                        "type": "vmd",
                        "kind": "model",
                        "path": character_motion,
                        "target": "character",
                    },
                    {
                        "id": "camera_wide",
                        "type": "vmd",
                        "kind": "camera",
                        "generated": {
                            "type": "camera-vmd",
                            "filename": "camera_wide.vmd",
                            "frames": [0, 10],
                        },
                        "expect": {
                            "cameraKeys": [0, 10],
                        },
                    },
                    {
                        "id": "camera_short_clear",
                        "type": "vmd",
                        "kind": "camera",
                        "requires": ["camera_wide"],
                        "clearExistingMotion": True,
                        "generated": {
                            "type": "camera-vmd",
                            "filename": "camera_short.vmd",
                            "frames": [0],
                        },
                        "expect": {
                            "cameraKeys": [0],
                        },
                    },
                    {
                        "id": "light_wide",
                        "type": "vmd",
                        "kind": "light",
                        "generated": {
                            "type": "light-vmd",
                            "filename": "light_wide.vmd",
                            "frames": [0, 10],
                        },
                        "expect": {
                            "lightKeys": [0, 10],
                        },
                    },
                    {
                        "id": "light_short_clear",
                        "type": "vmd",
                        "kind": "light",
                        "requires": ["light_wide"],
                        "clearExistingMotion": True,
                        "generated": {
                            "type": "light-vmd",
                            "filename": "light_short.vmd",
                            "frames": [0],
                        },
                        "expect": {
                            "lightKeys": [0],
                        },
                    },
                ],
                "constraints": [
                    ["character", "before", "character_motion"],
                    ["camera_wide", "before", "camera_short_clear"],
                    ["light_wide", "before", "light_short_clear"],
                ],
                "orders": [
                    [
                        "character",
                        "character_motion",
                        "camera_wide",
                        "camera_short_clear",
                        "light_wide",
                        "light_short_clear",
                    ],
                    [
                        "character",
                        "character_motion",
                        "light_wide",
                        "light_short_clear",
                        "camera_wide",
                        "camera_short_clear",
                    ],
                    [
                        "camera_wide",
                        "camera_short_clear",
                        "light_wide",
                        "light_short_clear",
                        "character",
                        "character_motion",
                    ],
                ],
                "expect": {
                    "characterMotionKeys": True,
                    "cameraKeysAfterClear": [0],
                    "lightKeysAfterClear": [0],
                },
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _maya_devkit_root(version: str) -> Path:
    """Return the Maya devkit root, allowing environment overrides."""
    version_env = os.environ.get(f"MAYA_DEVKIT_ROOT_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_DEVKIT_ROOT")
    if common_env:
        return Path(common_env)

    return _maya_location(version) / "devkit"


def _cpp_build_dir(version: str) -> Path:
    """Return the CMake build directory for a Maya version."""
    return ROOT / "build" / "cpp" / f"maya{version}"


def _vswhere_path() -> Path:
    """Return the default vswhere path."""
    explicit = os.environ.get("VSWHERE_PATH")
    if explicit:
        return Path(explicit)
    return Path("C:/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe")


def _find_vsdevcmd() -> Path | None:
    """Find VsDevCmd.bat for Windows C++ builds."""
    explicit = os.environ.get("VSDEVCMD_PATH")
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    vswhere = _vswhere_path()
    if vswhere.exists():
        try:
            result = subprocess.run(
                [
                    str(vswhere),
                    "-latest",
                    "-prerelease",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationPath",
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            for line in result.stdout.splitlines():
                candidate = Path(line.strip()) / "Common7" / "Tools" / "VsDevCmd.bat"
                if candidate.exists():
                    return candidate
        except OSError:
            pass

    for root in (
        Path("C:/Program Files/Microsoft Visual Studio/18"),
        Path("C:/Program Files/Microsoft Visual Studio/2022"),
        Path("C:/Program Files (x86)/Microsoft Visual Studio/2022"),
    ):
        if not root.exists():
            continue
        for candidate in root.glob("*/Common7/Tools/VsDevCmd.bat"):
            if candidate.exists():
                return candidate

    return None


def _run_in_vs_dev_cmd(session: nox.Session, command: list[str]) -> None:
    """Run a Windows command after initializing Visual Studio C++ tools."""
    vsdevcmd = _find_vsdevcmd()
    if vsdevcmd is None or os.environ.get("MMD_TOOLS_SKIP_VSDEVCMD"):
        session.run(*command, external=True)
        return

    body = subprocess.list2cmdline(command)
    session.log(f"Using Visual Studio developer environment: {vsdevcmd}")
    result = subprocess.run(
        f'"{vsdevcmd}" -arch=x64 -host_arch=x64 >nul && {body}',
        cwd=ROOT,
        shell=True,
    )
    if result.returncode != 0:
        session.error(f"Command failed with exit code {result.returncode}: {body}")


def _cmake_configure(session: nox.Session, version: str, config: str = DEFAULT_CMAKE_CONFIG) -> None:
    """Configure the Maya C++ plugin build."""
    args = [
        "cmake",
        "-S",
        "cpp/src",
        "-B",
        str(_cpp_build_dir(version)),
        f"-DMAYA_VERSION={version}",
        f"-DREPO_ROOT={ROOT}",
        f"-DMAYA_DEVKIT_ROOT={_maya_devkit_root(version)}",
    ]

    if platform.system() == "Windows" and not os.environ.get("CMAKE_GENERATOR"):
        args.extend(["-G", "Ninja", f"-DCMAKE_BUILD_TYPE={config}"])

    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, args)
    else:
        session.run(*args, external=True)


def _cmake_build(session: nox.Session, version: str, config: str) -> None:
    """Build the Maya C++ plugin."""
    command = [
        "cmake",
        "--build",
        str(_cpp_build_dir(version)),
        "--config",
        config,
    ]
    if platform.system() == "Windows":
        _run_in_vs_dev_cmd(session, command)
    else:
        session.run(*command, external=True)


def _cpp_smoke_exe(version: str, config: str) -> Path:
    """Return path to the standalone mmd_runtime_smoke exe produced by cpp build."""
    build_dir = _cpp_build_dir(version) / config
    exe = build_dir / "mmd_runtime_smoke"
    if platform.system() == "Windows":
        exe = exe.with_suffix(".exe")
    return exe


def _run_cli_smoke(
    session: nox.Session,
    version: str,
    config: str,
    manifest: str,
    case: str = "",
    limit: str = "",
) -> None:
    """Run the CLI smoke exe (if manifest provided). Used by cpp_cli_smoke and conditionally by cpp_verify."""
    if not manifest:
        return
    exe = _cpp_smoke_exe(version, config)
    if not exe.exists():
        raise FileNotFoundError(
            f"mmd_runtime_smoke not found at {exe}. "
            f"Run 'uvx nox -s cpp_build -- --maya {version} --config {config}' first."
        )
    smoke_args: list[str] = ["--manifest", manifest]
    if case:
        smoke_args.extend(["--case", case])
    if limit:
        smoke_args.extend(["--limit", limit])
    session.run(str(exe), *smoke_args, external=True)


_EXPECTED_ENVIRONMENT_MODULE_PREFIXES = ("maya", "PySide2", "PySide6")
_TERMINAL_EXCEPTION_RE = re.compile(r"^(?P<type>[\w.]+(?:Error|Exception)):\s*(?P<message>.*)$")
_MISSING_MODULE_RE = re.compile(
    r"No module named ['\"](?P<module>[^'\"]+)['\"](?:;.*)?\.?$"
)


def _is_expected_environment_import_failure(stderr: str) -> bool:
    """Return whether the final exception is an allowlisted missing environment module."""
    for line in reversed(stderr.splitlines()):
        match = _TERMINAL_EXCEPTION_RE.match(line.strip())
        if not match:
            continue
        if match.group("type") != "ModuleNotFoundError":
            return False
        missing = _MISSING_MODULE_RE.fullmatch(match.group("message"))
        if not missing:
            return False
        prefix = missing.group("module").split(".", 1)[0]
        return prefix in _EXPECTED_ENVIRONMENT_MODULE_PREFIXES
    return False


@nox.session(venv_backend="none")
def ci_unit(session: nox.Session) -> None:
    """Run pure-python unit tests without mayapy.

    Dynamically discovers tests/unit/test_*.py files that can be imported
    without Maya, so any new tests added to tests/unit are automatically
    included — no manual listing required.

    A test file is included when it can be imported successfully with a
    plain ``python -c "import tests.unit.<stem>"`` probe (i.e. it has no
    transitive dependency on an allowlisted environment-only module). Files
    that fail for one of those expected dependencies are skipped with a notice;
    other import failures abort the session.

    Examples:
        uvx nox -s ci_unit
    """
    unit_dir = ROOT / "tests" / "unit"
    importable: list[str] = []
    skipped: list[str] = []

    for py_file in sorted(unit_dir.glob("test_*.py")):
        module_name = f"tests.unit.{py_file.stem}"
        probe = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode == 0:
            importable.append(module_name)
            continue
        stderr = probe.stderr or ""
        if _is_expected_environment_import_failure(stderr):
            skipped.append(py_file.name)
        else:
            session.error(
                f"ci_unit: {py_file.name} failed to import for a non-environment reason; "
                "update _EXPECTED_ENVIRONMENT_MODULE_PREFIXES only for an intentional dependency:\n"
                + stderr.strip()[-2000:]
            )

    if skipped:
        session.log(
            f"Skipping {len(skipped)} test file(s) that require environment-only dependencies: "
            + ", ".join(skipped)
        )

    if not importable:
        session.error("No importable pure-python unit tests found in tests/unit/")

    session.log(f"Running {len(importable)} pure-python unit test module(s)")
    command = ["uvx", "--with", "pytest", "--", "python", "-m", "pytest", "--pyargs", *importable]
    returncode, log_path, (_, repeated_warnings) = _run_logged_subprocess(
        command,
        log_path=ROOT / "build" / "reports" / "ci_unit_tests.log",
        cwd=ROOT,
        verbose=False,
    )
    if returncode != 0:
        session.error(f"ci_unit failed with exit code {returncode}; full log: {log_path}")
    detail = f"; repeated warnings suppressed: {repeated_warnings}" if repeated_warnings else ""
    session.log(f"ci_unit passed; full log: {log_path}{detail}")


@nox.session(venv_backend="none")
def release_version(session: nox.Session) -> None:
    """Validate all release version markers, optionally against a tag version."""
    expected_version = _option(session.posargs, "--version", "") or None
    _release_gate_version_check(expected_version=expected_version)
    session.log(f"Release version markers match {expected_version or 'the project version'}")


@nox.session(venv_backend="none")
def tests(session: nox.Session) -> None:
    """Run existing mayapy-backed unit/integration tests.

    Examples:
        uvx nox -s tests
        uvx nox -s tests -- --type integration --test test_maya_utils
    """
    args = session.posargs or ["--type", "unit"]
    session.run(sys.executable, "tests/run_tests.py", *args, external=True)


@nox.session(venv_backend="none")
def gui_tests(session: nox.Session) -> None:
    """Run existing Maya GUI tests."""
    args = session.posargs or ["--maya_version", DEFAULT_MAYA_VERSION]
    session.run(sys.executable, "tests/run_gui_tests.py", *args, external=True)


@nox.session(venv_backend="none")
def ffi_build(session: nox.Session) -> None:
    """Build the mmd-anim FFI library used by Python and C++ integrations.

    Examples:
        uvx nox -s ffi_build
        uvx nox -s ffi_build -- --release --cargo-target-dir build/mmd-anim-unlocked-target
    """
    args = session.posargs or ["--release"]
    cargo_target_dir_raw = _option(args, "--cargo-target-dir", "")
    cargo_args = _without_option(args, "--cargo-target-dir") if cargo_target_dir_raw else list(args)
    cargo_args = _cargo_args_with_physics_feature(cargo_args)
    cargo_target_dir = None
    if cargo_target_dir_raw:
        cargo_target_dir = _require_build_path(session, cargo_target_dir_raw, "--cargo-target-dir")
    profile = "release" if "--release" in args else "debug"
    library_name = {
        "Windows": "mmd_runtime_ffi.dll",
        "Darwin": "libmmd_runtime_ffi.dylib",
    }.get(platform.system(), "libmmd_runtime_ffi.so")
    output_root = cargo_target_dir or (ROOT / "external" / "mmd-anim" / "target")
    locked_by = _windows_processes_locking_module(
        output_root / profile / library_name
    )
    if locked_by:
        session.error(
            "mmd-anim FFI output DLL is currently loaded and cannot be replaced: "
            + "; ".join(locked_by)
        )
    env = os.environ.copy()
    if cargo_target_dir is not None:
        env["CARGO_TARGET_DIR"] = str(cargo_target_dir)
    _configure_bullet3_dir(session, env)
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        *cargo_args,
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def native_smoke(session: nox.Session) -> None:
    """Verify that Python can load mmd-anim-ffi and read its ABI version.

    Examples:
        uvx nox -s native_smoke
        uvx nox -s native_smoke -- --ffi-path build/mmd-anim-unlocked-target/release
    """
    args = list(session.posargs)
    ffi_path = _option(args, "--ffi-path", "")
    env = os.environ.copy()
    if ffi_path:
        env["MMD_ANIM_FFI_PATH"] = str(_resolve_existing_or_repo_path(ffi_path))
    session.run(sys.executable, "-c", _native_runtime_smoke_code(), env=env, external=True)


@nox.session(venv_backend="none")
def bundled_native_smoke(session: nox.Session) -> None:
    """Verify only the native binaries bundled in release distribution paths."""
    out_json = _require_build_path(
        session,
        _option(session.posargs, "--out-json", "build/reports/bundled_native_smoke.json"),
        "--out-json",
    )
    out_md = _require_build_path(
        session,
        _option(session.posargs, "--out-md", "build/reports/bundled_native_smoke.md"),
        "--out-md",
    )
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    session.run(
        sys.executable,
        "tests/release/bundled_native_smoke.py",
        "--root",
        str(ROOT),
        "--expected-version",
        project["project"]["version"],
        "--out-json",
        str(out_json),
        "--out-md",
        str(out_md),
        external=True,
    )


@nox.session(venv_backend="none")
def native_export_smoke(session: nox.Session) -> None:
    """Verify native export writer symbols when the DLL is current.

    PMX parts export is required. VMD/PMD JSON writer symbols are optional in
    newer mmd-anim builds and are exercised only when present.

    Examples:
        uvx nox -s native_export_smoke
        uvx nox -s native_export_smoke -- --strict --ffi-path build/mmd-anim-unlocked-target/release
    """
    args = list(session.posargs)
    ffi_path = _option(args, "--ffi-path", "")
    smoke_args = _without_option(args, "--ffi-path") if ffi_path else args
    env = os.environ.copy()
    if ffi_path:
        env["MMD_ANIM_FFI_PATH"] = str(_resolve_existing_or_repo_path(ffi_path))
    session.run(sys.executable, "tests/native_export_smoke.py", *smoke_args, env=env, external=True)


@nox.session(venv_backend="none")
def release_package(session: nox.Session) -> None:
    """Build and fail-closed validate the release ZIP from the package manifest.

    Examples:
        uvx nox -s release_package
        uvx nox -s release_package -- --version 0.3.1
        uvx nox -s release_package -- --out-dir dist
    """
    manifest = _resolve_existing_or_repo_path(
        _option(session.posargs, "--manifest", str(_PACKAGE_MANIFEST_PATH))
    )
    output_dir = _resolve_existing_or_repo_path(_option(session.posargs, "--out-dir", "dist"))
    root = ROOT.resolve()
    if output_dir != root and root not in output_dir.parents:
        session.error(f"--out-dir must stay inside the repository: {output_dir}")
    result = _build_release_package(
        root,
        manifest_path=manifest,
        output_dir=output_dir,
        expected_version=_option(session.posargs, "--version", "") or None,
    )
    session.log(f"Release package: {result['archive']}")
    session.log("Release package evidence: build/reports/release_package.json and .md")


@nox.session(venv_backend="none")
def cpp_config(session: nox.Session) -> None:
    """Configure the Maya C++ plugin build."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    _cmake_configure(session, version, config)


@nox.session(venv_backend="none")
def cpp_build(session: nox.Session) -> None:
    """Configure and build the Maya C++ plugin."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    _cmake_configure(session, version, config)
    _cmake_build(session, version, config)


@nox.session(venv_backend="none")
def maya_smoke(session: nox.Session) -> None:
    """Load the C++ plugin in mayapy and create the runtime node."""
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(mayapy, MAYA_VERSION=version, MMD_TOOLS_CPP_CONFIG=config)
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/smoke_python_rig_fallback.py"),
        env=env,
        external=True,
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/smoke_runtime_node.py"),
        env=env,
        external=True,
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/focused_physics_solver_world_toggle.py"),
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def maya_viewport_capture(session: nox.Session) -> None:
    """Minimal mayapy offscreen viewport capture smoke (no GUI, no plugin).

    Creates a trivial polyCube + camera + light scene and uses playblast with
    offScreen=True, offScreenViewportUpdate=True, viewer=False, format=image,
    compression=png to produce a PNG. Verifies the file exists with >0 size.

    The script tolerates playblast emitting frame-padded names (e.g. foo.0001.png)
    by detecting the actual written file in the target directory.

    Defaults: Maya 2024, output build/captures/viewport_smoke.png, frame 1, 640x480.

    Does NOT depend on mmd_tools or the C++ plugin (pure Maya standalone smoke).

    Examples:
        uvx nox -s maya_viewport_capture -- --maya 2024
        uvx nox -s maya_viewport_capture -- --maya 2024 --out build/captures/viewport_smoke.png --frame 1 --width 640 --height 480
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    out = _option(session.posargs, "--out", str(ROOT / "build/captures/viewport_smoke.png"))
    frame = _option(session.posargs, "--frame", "1")
    width = _option(session.posargs, "--width", "640")
    height = _option(session.posargs, "--height", "480")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=version,
        # Intentionally no MMD_TOOLS_CPP_* or plugin env; this smoke is plugin-free.
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/smoke_viewport_capture.py"),
        "--out",
        _mayapy_arg_path(mayapy, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def native_physics_bake_capture(session: nox.Session) -> None:
    """Import PMX+VMD with native physics bake and capture a mayapy PNG + report.

    Requires a physics-feature-enabled mmd-anim-ffi via ``--ffi-path`` (sets
    ``MMD_ANIM_FFI_PATH``). Uses mayapy only — no Maya GUI.

    The runner imports with ``bake_mode=True, use_native_physics_bake=True``,
    writes a non-empty PNG playblast and a JSON report with feature flags,
    physics routing outcome, joint matrix samples, and output paths.

    Examples:
        uvx nox -s native_physics_bake_capture -- --ffi-path build/mmd-anim-physics-target/debug
        uvx nox -s native_physics_bake_capture -- --maya 2024 --ffi-path build/mmd-anim-physics-target/debug \\
            --pmx tests/data/mmt_test_model.pmx --vmd tests/data/mmt_test_model_test_motion.vmd \\
            --out build/captures/native_physics_bake.png \\
            --report build/reports/native_physics_bake_capture.json --frame 0
    """
    args = list(session.posargs)
    version = _option(args, "--maya", DEFAULT_MAYA_VERSION)
    ffi_path = _option(args, "--ffi-path", "")
    if not ffi_path:
        raise ValueError(
            "native_physics_bake_capture requires --ffi-path pointing to a "
            "physics-feature-enabled mmd-anim-ffi directory or DLL"
        )
    pmx = _option(args, "--pmx", str(ROOT / "tests/data/mmt_test_model.pmx"))
    vmd = _option(args, "--vmd", str(ROOT / "tests/data/mmt_test_model_test_motion.vmd"))
    out = _option(args, "--out", str(ROOT / "build/captures/native_physics_bake.png"))
    report = _option(args, "--report", str(ROOT / "build/reports/native_physics_bake_capture.json"))
    frame = _option(args, "--frame", "0")
    fps = _option(args, "--fps", "30")
    width = _option(args, "--width", "640")
    height = _option(args, "--height", "480")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    resolved_ffi = _resolve_existing_or_repo_path(ffi_path)
    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=version,
        MMD_ANIM_FFI_PATH=str(resolved_ffi),
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/native_physics_bake_capture.py"),
        "--pmx",
        _mayapy_arg_path(mayapy, pmx),
        "--vmd",
        _mayapy_arg_path(mayapy, vmd),
        "--out",
        _mayapy_arg_path(mayapy, out),
        "--report",
        _mayapy_arg_path(mayapy, report),
        "--frame",
        frame,
        "--fps",
        fps,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def native_physics_bake_route_e2e(session: nox.Session) -> None:
    """Dual-import E2E gate: native physics bake vs baseline bake routing.

    Requires a physics-feature-enabled mmd-anim-ffi via ``--ffi-path`` (sets
    ``MMD_ANIM_FFI_PATH``). Imports a real physics PMX twice in clean scenes:

    1. baseline: ``bake_mode=True``, ``use_native_physics_bake=False``
    2. native:   ``bake_mode=True``, ``use_native_physics_bake=True``

    Fails unless native routing was used and at least one physics-controlled
    bone has a measurable local-transform delta vs baseline.

    Defaults to the hair physics fixture + known short motion. Does not change
    the single-import PNG capture session.

    Examples:
        uvx nox -s native_physics_bake_route_e2e -- --ffi-path build/mmd-anim-physics-target/debug
        uvx nox -s native_physics_bake_route_e2e -- --maya 2024 --ffi-path build/mmd-anim-physics-target/debug \\
            --pmx tests/data/physics/test_hair_physics.pmx \\
            --vmd tests/data/mmt_test_model_test_motion.vmd \\
            --eval-frames 0,1,2,3,4,5 \\
            --report build/reports/native_physics_bake_route_e2e.json
    """
    args = list(session.posargs)
    version = _option(args, "--maya", DEFAULT_MAYA_VERSION)
    ffi_path = _option(args, "--ffi-path", "")
    if not ffi_path:
        raise ValueError(
            "native_physics_bake_route_e2e requires --ffi-path pointing to a "
            "physics-feature-enabled mmd-anim-ffi directory or DLL"
        )
    pmx = _option(args, "--pmx", str(ROOT / "tests/data/physics/test_hair_physics.pmx"))
    vmd = _option(args, "--vmd", str(ROOT / "tests/data/mmt_test_model_test_motion.vmd"))
    report = _option(
        args,
        "--report",
        str(ROOT / "build/reports/native_physics_bake_route_e2e.json"),
    )
    eval_frames = _option(args, "--eval-frames", "0,1,2,3,4,5")
    delta_epsilon = _option(args, "--delta-epsilon", "0.001")
    fps = _option(args, "--fps", "30")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    resolved_ffi = _resolve_existing_or_repo_path(ffi_path)
    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=version,
        MMD_ANIM_FFI_PATH=str(resolved_ffi),
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/native_physics_bake_capture.py"),
        "--verify-bake-route",
        "--pmx",
        _mayapy_arg_path(mayapy, pmx),
        "--vmd",
        _mayapy_arg_path(mayapy, vmd),
        "--report",
        _mayapy_arg_path(mayapy, report),
        "--eval-frames",
        eval_frames,
        "--delta-epsilon",
        delta_epsilon,
        "--fps",
        fps,
        env=env,
        external=True,
    )


def _bundled_physics_runtime(system: str | None = None) -> Path:
    """Return the platform-specific bundled runtime used by the physics release gate."""
    current = system or platform.system()
    relative = {
        "Windows": "mmd_tools/native/win64/mmd_runtime_ffi.dll",
        "Darwin": "mmd_tools/native/macos/libmmd_runtime_ffi.dylib",
    }.get(current)
    if relative is None:
        raise RuntimeError(f"Bundled native physics release gate is unsupported on {current}")
    return (ROOT / relative).resolve()


@nox.session(venv_backend="none")
def native_physics_release_gate(session: nox.Session) -> None:
    """Run the bundled native physics bake route twice and compare deterministic outputs."""
    maya_version = "2024"
    mayapy = _mayapy(maya_version)
    pmx = (ROOT / "tests/data/physics/test_hair_physics.pmx").resolve()
    vmd = (ROOT / "tests/data/mmt_test_model_test_motion.vmd").resolve()
    ffi = _bundled_physics_runtime()
    report_dir = (ROOT / "build/reports").resolve()
    run_reports = [report_dir / "native_physics_release_run1.json", report_dir / "native_physics_release_run2.json"]
    comparison_json = report_dir / "native_physics_release_comparison.json"
    comparison_md = report_dir / "native_physics_release_comparison.md"
    for stale_report in (*run_reports, comparison_json, comparison_md):
        if stale_report.exists():
            stale_report.unlink()
    for required in (mayapy, pmx, vmd, ffi):
        if not required.is_file():
            raise FileNotFoundError(f"Native physics release gate input not found: {required}")
    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=maya_version,
        MMD_ANIM_FFI_PATH=str(ffi),
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    for report in run_reports:
        session.run(
            str(mayapy),
            _mayapy_script(mayapy, "tests/viewport/native_physics_bake_capture.py"),
            "--verify-bake-route",
            "--pmx", _maya_process_path(mayapy, pmx),
            "--vmd", _maya_process_path(mayapy, vmd),
            "--report", _maya_process_path(mayapy, report),
            "--eval-frames", "0,1,2,3,4,5",
            env=env,
            external=True,
        )
    session.run(
        sys.executable,
        "tests/release/native_physics_determinism.py",
        "--run1", str(run_reports[0]),
        "--run2", str(run_reports[1]),
        "--ffi", str(ffi),
        "--out-json", str(comparison_json),
        "--out-md", str(comparison_md),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_definition_smoke(session: nox.Session) -> None:
    """Create a minimal HumanIK definition under mayapy.

    Examples:
        uvx nox -s humanik_definition_smoke -- --maya 2024
        uvx nox -s humanik_definition_smoke -- --maya 2024 --out build/reports/humanik_definition_smoke.json
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in {"--out", "--name", "--fixture"} and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] == "--create-control-rig":
            passthrough.append(args[i])
            i += 1
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_definition_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--out"}),
        env=_mayapy_env(mayapy),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_retarget_smoke(session: nox.Session) -> None:
    """Run the direct HumanIK S0 fixture smoke under Maya 2024 mayapy.

    The smoke writes lock state, direct input type, mapped-joint writer census,
    changed connections, and root-locomotion world-matrix evidence.

    Examples:
        uvx nox -s humanik_retarget_smoke -- --maya 2024
        uvx nox -s humanik_retarget_smoke -- --maya 2024 --out build/reports/humanik_retarget_smoke.json
        uvx nox -s humanik_retarget_smoke -- --maya 2024 --pmx <source.pmx> --target-pmx <target.pmx> --vmd <source.vmd>
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    path_options = {"--pmx", "--target-pmx", "--vmd", "--out"}
    value_options = path_options | {
        "--pmx-base64",
        "--target-pmx-base64",
        "--vmd-base64",
        "--name-prefix",
        "--translation",
        "--tolerance",
        "--motion-frames",
        "--evaluation-modes",
    }
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_retarget_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_constraint_report_smoke(session: nox.Session) -> None:
    """Classify MMD rig ownership without modifying scene connections."""
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in {"--pmx", "--out"} and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_constraint_report_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--out"}),
        env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_transaction_smoke(session: nox.Session) -> None:
    """Verify S2 HumanIK rollback and idempotent restore under mayapy."""
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in {"--pmx", "--out"} and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_transaction_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--out"}),
        env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_target_preview_smoke(session: nox.Session) -> None:
    """Verify S3 exclusive TARGET preview and NEUTRAL restore."""
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in {"--pmx", "--out"} and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_target_preview_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--out"}),
        env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
        external=True,
    )


@nox.session(venv_backend="none")
def humanik_bake_smoke(session: nox.Session) -> None:
    """Run the S4 bake smoke in isolated off/serial/parallel Maya scenes."""
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    requested_mode = _option(args, "--evaluation-mode", "")
    modes = [requested_mode] if requested_mode else ["off", "serial", "parallel"]
    out_value = _option(args, "--out", str(ROOT / "build/reports/humanik_bake_smoke.json"))
    passthrough: list[str] = []
    value_options = {"--pmx", "--vmd", "--start", "--end"}
    path_options = {"--pmx", "--vmd", "--out"}
    i = 0
    while i < len(args):
        if args[i] in {"--maya", "--evaluation-mode", "--out"} and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    base_out = Path(out_value)
    for mode in modes:
        mode_out = base_out if requested_mode else base_out.with_name(f"{base_out.stem}.{mode}{base_out.suffix}")
        mode_args = [*passthrough, "--evaluation-mode", mode, "--out", str(mode_out)]
        session.run(
            str(mayapy),
            _mayapy_script(mayapy, "tests/viewport/humanik_bake_smoke.py"),
            *_convert_mayapy_path_options(mayapy, mode_args, path_options),
            env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
            external=True,
        )


@nox.session(venv_backend="none")
def humanik_roundtrip_smoke(session: nox.Session) -> None:
    """Run the S5 self-retarget gate in isolated Maya evaluation modes."""
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    requested_mode = _option(args, "--evaluation-mode", "")
    modes = [requested_mode] if requested_mode else ["off", "serial", "parallel"]
    out_value = _option(args, "--out", str(ROOT / "build/reports/humanik_roundtrip_smoke.json"))
    passthrough: list[str] = []
    value_options = {"--pmx", "--vmd", "--start", "--end", "--hik-profile", "--characterization-stance"}
    path_options = {"--pmx", "--vmd", "--out"}
    i = 0
    while i < len(args):
        if args[i] in {"--maya", "--evaluation-mode", "--out"} and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    base_out = Path(out_value)
    failed_modes: list[str] = []
    for mode in modes:
        mode_out = base_out if requested_mode else base_out.with_name(f"{base_out.stem}.{mode}{base_out.suffix}")
        try:
            mode_out.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            session.error(f"Unable to clear stale HumanIK S5 report {mode_out}: {exc}")
        mode_args = [*passthrough, "--evaluation-mode", mode, "--out", str(mode_out)]
        session.run(
            str(mayapy),
            _mayapy_script(mayapy, "tests/viewport/humanik_roundtrip_smoke.py"),
            *_convert_mayapy_path_options(mayapy, mode_args, path_options),
            env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1"),
            success_codes=(0, 1),
            external=True,
        )
        if not mode_out.is_file():
            failed_modes.append(f"{mode}: report missing ({mode_out})")
            continue
        try:
            report = json.loads(mode_out.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failed_modes.append(f"{mode}: invalid report ({exc})")
            continue
        if not isinstance(report, dict):
            failed_modes.append(f"{mode}: report root is not an object")
            continue
        if report.get("evaluationMode") != mode:
            failed_modes.append(
                f"{mode}: evaluationMode={report.get('evaluationMode', 'missing')}"
            )
            continue
        if report.get("status") != "pass":
            failed_modes.append(f"{mode}: status={report.get('status', 'missing')}")
    if failed_modes:
        session.error("HumanIK S5 round-trip matrix failed: " + "; ".join(failed_modes))


@nox.session(venv_backend="none")
def humanik_vmd_parity_smoke(session: nox.Session) -> None:
    """Run the SOURCE/VMD IK reproduction-matrix smoke (HUMANIK-SOURCE-VMD-IK-PARITY-1).

    Diagnosis harness only: reproduces the reported divergence between a clean
    VMD import and VMD import ordered around HumanIK setup_and_characterize /
    enter_source_mode.  Expected initial ``status`` is ``"stop"`` (a divergence
    was found), not ``"pass"``.  Pass ``--allow-stop`` to exit 0 on ``"stop"``
    so evidence can be captured without failing CI; omit it for a strict gate
    once the underlying bug is fixed.

    Pass ``--inject-restore-failure`` to also run the optional
    ``char_fail_restore_then_vmd`` scenario, which engineers a
    ``HumanIkStanceTransaction.restore()`` failure (see the harness module
    docstring) and reports the resulting topology/frame divergence under
    the report's ``injectedScenarios`` key. Off by default; does not affect
    the default scenario list or report shape.
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    requested_mode = _option(args, "--evaluation", "")
    modes = [requested_mode] if requested_mode else ["off", "serial", "parallel"]
    allow_stop = "--allow-stop" in args
    out_value = _option(args, "--out", str(ROOT / "build/reports/humanik_vmd_parity_smoke.json"))
    passthrough: list[str] = []
    value_options = {"--model", "--motion", "--frames"}
    path_options = {"--model", "--motion", "--out"}
    i = 0
    while i < len(args):
        if args[i] in {"--maya", "--evaluation", "--out", "--allow-stop"}:
            i += 1 if args[i] == "--allow-stop" else 2
            continue
        if args[i] == "--inject-restore-failure":
            passthrough.append(args[i])
            i += 1
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    base_out = Path(out_value)
    failed_modes: list[str] = []
    stopped_modes: list[str] = []
    for mode in modes:
        mode_out = base_out if requested_mode else base_out.with_name(f"{base_out.stem}.{mode}{base_out.suffix}")
        try:
            mode_out.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            session.error(f"Unable to clear stale HumanIK VMD parity report {mode_out}: {exc}")
        mode_args = [*passthrough, "--evaluation", mode, "--out", str(mode_out)]
        session.run(
            str(mayapy),
            _mayapy_script(mayapy, "tests/viewport/humanik_vmd_parity_smoke.py"),
            *_convert_mayapy_path_options(mayapy, mode_args, path_options),
            env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1", PYTHONIOENCODING="utf-8"),
            success_codes=(0, 1, 2),
            external=True,
        )
        if not mode_out.is_file():
            failed_modes.append(f"{mode}: report missing ({mode_out})")
            continue
        try:
            report = json.loads(mode_out.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failed_modes.append(f"{mode}: invalid report ({exc})")
            continue
        if not isinstance(report, dict):
            failed_modes.append(f"{mode}: report root is not an object")
            continue
        status = report.get("status")
        if status == "pass":
            continue
        if status == "stop" and allow_stop:
            stopped_modes.append(f"{mode}: status=stop")
            continue
        failed_modes.append(f"{mode}: status={status}, error={report.get('error')}")
    if stopped_modes:
        session.log("HumanIK VMD parity smoke stopped (evidence captured, not failing): " + "; ".join(stopped_modes))
    if failed_modes:
        session.error("HumanIK VMD parity smoke failed: " + "; ".join(failed_modes))


@nox.session(venv_backend="none")
def humanik_vmd_import_gate_smoke(session: nox.Session) -> None:
    """Run the HumanIK VMD-import mode gate smoke (HUMANIK-SOURCE-VMD-IK-PARITY-1).

    Characterizes a Kokomi fixture twice, enters a real HumanIK TARGET preview
    on the second copy, and verifies VMD import onto the TARGET model is
    refused fail-closed (naming the blocking mode and ``Restore MMD Rig``)
    with scene topology/animCurves left untouched, then verifies the same
    import succeeds once ``HumanIkFrontendSession.restore_mmd_rig()`` runs.
    Expected ``status`` is ``"pass"``.

    Examples:
        uvx nox -s humanik_vmd_import_gate_smoke -- --maya 2024
        uvx nox -s humanik_vmd_import_gate_smoke -- --maya 2024 --model "F:/MMD/pmx/.../model.pmx" --motion tests/data/mmt_test_model_test_motion.vmd
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    out_value = _option(args, "--out", str(ROOT / "build/reports/humanik_vmd_import_gate_smoke.json"))
    report_path = Path(out_value)
    path_options = {"--model", "--motion", "--out"}
    passthrough: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in path_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    if "--out" not in passthrough:
        passthrough.extend(["--out", out_value])
    try:
        report_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        session.error(f"Unable to clear stale HumanIK VMD import gate report {report_path}: {exc}")
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_vmd_import_gate_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=_mayapy_env(mayapy, MAYA_SKIP_USERSETUP_PY="1", PYTHONIOENCODING="utf-8"),
        success_codes=(0, 1),
        external=True,
    )
    if not report_path.is_file():
        session.error(f"HumanIK VMD import gate report missing: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        session.error(f"Invalid HumanIK VMD import gate report {report_path}: {exc}")
    status = report.get("status")
    if status != "pass":
        session.error(
            "HumanIK VMD import gate smoke failed: "
            f"status={status}, error={report.get('error')}, "
            f"gateRaised={report.get('gateRaised')}, "
            f"topologyUnchangedAfterRefusal={report.get('topologyUnchangedAfterRefusal')}, "
            f"postRestoreImportSucceeded={report.get('postRestoreImportSucceeded')}"
        )


@nox.session(venv_backend="none")
def humanik_citlali_stance_smoke(session: nox.Session) -> None:
    """Run the strict Citlali HumanIK setup/restore regression gate.

    The gate imports the ASCII-path Citlali fixture, characterizes it through
    the frontend, and verifies rotate, jointOrient, skin-product, and exact
    writer-topology restoration evidence without changing the source PMX.
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    out_value = _option(args, "--out", str(ROOT / "build/reports/humanik_citlali_stance_smoke.json"))
    pmx_value = _option(
        args,
        "--pmx",
        str(ROOT / "build/fixtures/citlali_ascii_file/citlali.pmx"),
    )
    profile = _option(args, "--profile", "body-only")
    report_path = Path(out_value)
    passthrough = [
        "--pmx", pmx_value,
        "--out", out_value,
        "--profile", profile,
    ]
    try:
        report_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        session.error(f"Unable to clear stale Citlali HumanIK report {report_path}: {exc}")
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/humanik_citlali_stance_smoke.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--out"}),
        env=_mayapy_env(
            mayapy,
            MAYA_SKIP_USERSETUP_PY="1",
            PYTHONIOENCODING="utf-8",
        ),
        external=True,
    )
    if not report_path.is_file():
        session.error(f"Citlali HumanIK report missing: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        session.error(f"Invalid Citlali HumanIK report {report_path}: {exc}")
    stance = report.get("stance", {})
    restore = stance.get("restore") or stance.get("stanceEvidence", {}).get("restore", {})
    required = {
        "status": report.get("status"),
        "restorePassed": restore.get("passed"),
        "topologyRestored": restore.get("topologyRestored"),
        "maxRotateResidual": restore.get("maxRotateResidual"),
        "maxJointOrientResidual": restore.get("maxJointOrientResidual"),
        "maxSkinMatrixResidual": restore.get("maxSkinMatrixResidual"),
        "maxAllSkinMatrixResidual": restore.get("maxAllSkinMatrixResidual"),
        "tolerance": restore.get("tolerance"),
        "transformDiffCount": len(report.get("transformDiffs", [])),
    }
    if (
        required["status"] != "pass"
        or required["restorePassed"] is not True
        or required["topologyRestored"] is not True
        or required["transformDiffCount"] != 0
        or any(
            value is None or float(value) > float(required["tolerance"])
            for key, value in required.items()
            if key.endswith("Residual")
        )
    ):
        session.error(f"Citlali HumanIK strict restore gate failed: {required}")


@nox.session(venv_backend="none")
def physics_solver_cycle_probe(session: nox.Session) -> None:
    """Capture Citlali mmdPhysicsSolver cycle evidence without changing solver code.

    A Maya-reported DG cycle is intentionally accepted as probe evidence; the
    mayapy script only fails when clean production import or the reversible
    operation harness itself fails.  The JSON report is the investigation
    artifact consumed by the MMD-PHYSICS-SOLVER-CYCLE-1 queue item.

    Examples:
        uvx nox -s physics_solver_cycle_probe -- --maya 2024
        uvx nox -s physics_solver_cycle_probe -- --maya 2026 --frames 0,1,2,1,0
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    args = list(session.posargs)
    out_value = _option(args, "--out", str(ROOT / "build/reports/physics_solver_cycle_probe.json"))
    pmx_value = _option(
        args,
        "--pmx",
        str(ROOT / "build/fixtures/citlali_ascii_file/citlali.pmx"),
    )
    frames_value = _option(args, "--frames", "0,1,2,1,0")
    report_path = Path(out_value)
    passthrough = [
        "--pmx", pmx_value,
        "--out", out_value,
        "--frames", frames_value,
    ]
    try:
        report_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        session.error(f"Unable to clear stale physics cycle probe report {report_path}: {exc}")
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/physics_solver_cycle_probe.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--out"}),
        env=_mayapy_env(
            mayapy,
            MAYA_SKIP_USERSETUP_PY="1",
            PYTHONIOENCODING="utf-8",
        ),
        external=True,
    )
    if not report_path.is_file():
        session.error(f"Physics solver cycle probe report missing: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        session.error(f"Invalid physics solver cycle probe report {report_path}: {exc}")
    if report.get("status") != "pass":
        session.error(
            "Physics solver cycle probe failed: "
            f"errors={report.get('errors')}, solver={report.get('solver')}"
        )


@nox.session(venv_backend="none")
def maya_shader_override_smoke(session: nox.Session) -> None:
    """Smoke the legacy MMDShader VP2.0 override through mayapy playblast.

    Loads the Python plug-in with shader override registration enabled, creates a
    custom ``MMDShader`` node, assigns it to a cube, and verifies an offscreen
    Viewport 2.0 PNG capture is produced.

    Examples:
        uvx nox -s maya_shader_override_smoke -- --maya 2024
        uvx nox -s maya_shader_override_smoke -- --maya 2024 --out build/captures/shader_override_smoke.png
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    out = _option(session.posargs, "--out", str(ROOT / "build/captures/shader_override_smoke.png"))
    frame = _option(session.posargs, "--frame", "1")
    width = _option(session.posargs, "--width", "640")
    height = _option(session.posargs, "--height", "480")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(mayapy, MAYA_VERSION=version)
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/smoke_shader_override.py"),
        "--out",
        _mayapy_arg_path(mayapy, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def maya_static_render(session: nox.Session) -> None:
    """Import a PMX fixture and capture one frame with fixed camera/light.

    Creates a scene by importing a PMX model via mmd_tools.io.mmd_importer,
    sets up a GoldenOracle-style fixed camera and directional light, and
    captures one frame to PNG via playblast with offScreen=True. Verifies
    the PNG exists with >0 size and is not effectively blank.

    This is a report-only capture baseline, NOT an image-comparison gate.
    No FLIP or pixel-diff comparison is performed.

    Defaults: model tests/data/for_unit_test/test_1bone_cube.pmx, frame 0,
    1024x1024, output build/captures/static_render_1bone_cube.png.

    Requires the mmd_tools package (no C++ plugin needed for PMX import).

    The script explicitly sets View Transform / Display / Rendering Space
    via colorManagementPrefs before capture.  These can be overridden:
        --view-transform 'Un-tone-mapped (sRGB)'  (default)
        --display 'sRGB'                           (default)
        --rendering-space 'ACEScg'                 (default)
    If a requested value is not available in the current Maya environment,
    the operation fails with a RuntimeError listing available values.

    Examples:
        uvx nox -s maya_static_render -- --maya 2024
        uvx nox -s maya_static_render -- --maya 2024 --model tests/data/for_unit_test/test_1bone_cube.pmx --out build/captures/static_render_1bone_cube.png --frame 0 --width 1024 --height 1024
        uvx nox -s maya_static_render -- --maya 2024 --shader --view-transform "Un-tone-mapped (sRGB)" --out build/captures/static_render_1bone_cube_shader_untone.png
        uvx nox -s maya_static_render -- --maya 2024 --shader --view-transform "ACES 1.0 SDR-video (sRGB)" --out build/captures/static_render_1bone_cube_shader_aces.png
        uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend dx11 --out build/captures/static_render_1bone_cube_shader_dx11.png
        uvx nox -s maya_static_render -- --maya 2024 --shader --shader-backend glsl --out build/captures/static_render_1bone_cube_shader_glsl.png
    """
    if _has_flag(session.posargs, "--shader"):
        shader_flag = "--shader"
    else:
        shader_flag = "--no-shader"  # default

    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    model = _option(session.posargs, "--model", str(ROOT / "tests/data/for_unit_test/test_1bone_cube.pmx"))
    out = _option(session.posargs, "--out", str(ROOT / "build/captures/static_render_1bone_cube.png"))
    frame = _option(session.posargs, "--frame", "0")
    width = _option(session.posargs, "--width", "1024")
    height = _option(session.posargs, "--height", "1024")
    shader_backend = _option(session.posargs, "--shader-backend", "auto")
    if shader_backend not in {"auto", "dx11", "glsl", "standard"}:
        session.error(f"Unsupported --shader-backend: {shader_backend}")
    vp2_device = _option(session.posargs, "--vp2-device", "default")
    if vp2_device not in {"default", "gl", "glcore", "dx11"}:
        session.error(f"Unsupported --vp2-device: {vp2_device}")

    view_transform = _option(session.posargs, "--view-transform", "Un-tone-mapped (sRGB)")
    display = _option(session.posargs, "--display", "sRGB")
    rendering_space = _option(session.posargs, "--rendering-space", "ACEScg")
    diagnostics_out = _option(session.posargs, "--diagnostics-out", "")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    diagnostics_args: list[str] = []
    if diagnostics_out:
        diagnostics_path = _require_build_path(session, diagnostics_out, "--diagnostics-out")
        diagnostics_args.extend(["--diagnostics-out", _mayapy_arg_path(mayapy, diagnostics_path)])
    if _has_flag(session.posargs, "--allow-blank"):
        diagnostics_args.append("--allow-blank")

    env = _mayapy_env(mayapy, MAYA_VERSION=version)
    vp2_device_map = {
        "gl": "VirtualDeviceGL",
        "glcore": "VirtualDeviceGLCore",
        "dx11": "VirtualDeviceDx11",
    }
    if vp2_device in vp2_device_map:
        env["MAYA_VP2_DEVICE_OVERRIDE"] = vp2_device_map[vp2_device]

    cmd = [
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/static_render_capture.py"),
        shader_flag,
        "--out",
        _mayapy_arg_path(mayapy, out),
        "--model",
        _mayapy_arg_path(mayapy, model),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        "--shader-backend",
        shader_backend,
        "--view-transform",
        view_transform,
        "--display",
        display,
        "--rendering-space",
        rendering_space,
    ]
    cmd.extend(diagnostics_args)
    session.run(*cmd, env=env, external=True)


@nox.session(venv_backend="none")
def maya_visual_regression(session: nox.Session) -> None:
    """Run manifest-driven Maya GUI viewport visual regression captures.

    This is a report-only visual harness around GoldenOracle-compatible render
    manifests. The manifest path is intentionally required so local asset roots
    are injected by the caller instead of hard-coded in the repository.

    Examples:
        uvx nox -s maya_visual_regression -- --maya 2024 --manifest <render-manifest.json> --case fixture-render-generated-visual-mmd-diffuse-lit-box
        uvx nox -s maya_visual_regression -- --manifest <manifest.json> --tag visual --limit 3 --out build/visual-regression/local
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    manifest = _option(session.posargs, "--manifest", "")
    if not manifest:
        session.error("--manifest is required for maya_visual_regression")

    shader_backend = _option(session.posargs, "--shader-backend", "dx11")
    if shader_backend not in {"dx11", "glsl"}:
        session.error(f"Unsupported --shader-backend: {shader_backend}")
    vp2_device = _option(session.posargs, "--vp2-device", "default")
    if vp2_device not in {"default", "gl", "glcore", "dx11"}:
        session.error(f"Unsupported --vp2-device: {vp2_device}")
    out = _option(session.posargs, "--out", f"build/visual-regression/maya-{shader_backend}")
    out_path = _require_build_path(session, out, "--out")
    port = _option(session.posargs, "--port", "7721")
    width = _option(session.posargs, "--width", "1024")
    height = _option(session.posargs, "--height", "1024")
    timeout = _option(session.posargs, "--timeout", "420")

    forwarded: list[str] = []
    passthrough_flags = {"--keep-maya", "--no-compare", "--attach-existing", "--debug-lambert-control", "--hide-orig-shapes"}
    passthrough_options = {"--case", "--tag", "--limit", "--launch-mode", "--shader-fx"}
    i = 0
    while i < len(session.posargs):
        arg = session.posargs[i]
        if arg in passthrough_flags:
            forwarded.append(arg)
            i += 1
            continue
        if arg in passthrough_options:
            if i + 1 >= len(session.posargs):
                session.error(f"{arg} requires a value")
            forwarded.extend([arg, session.posargs[i + 1]])
            i += 2
            continue
        i += 1

    python = sys.executable
    cmd = [
        python,
        "tests/viewport/visual_regression_capture.py",
        "--maya",
        version,
        "--manifest",
        manifest,
        "--out",
        str(out_path),
        "--port",
        port,
        "--width",
        width,
        "--height",
        height,
        "--timeout",
        timeout,
        "--shader-backend",
        shader_backend,
        "--vp2-device",
        vp2_device,
    ]
    cmd.extend(forwarded)
    session.run(*cmd, external=True)

    if not _has_flag(session.posargs, "--no-compare"):
        comparison_cmd = [
            python,
            "tests/viewport/visual_regression_compare.py",
            "--capture-report",
            str(out_path / "visual-regression-report.json"),
            "--out",
            str(out_path / "visual-regression-comparison.json"),
        ]
        for threshold in _options(session.posargs, "--threshold"):
            comparison_cmd.extend(["--threshold", threshold])
        session.run(*comparison_cmd, external=True)


@nox.session(venv_backend="none")
def maya_asset_probe(session: nox.Session) -> None:
    """Collect Maya GUI Script Editor/log output while importing PMX assets.

    This is the stable local-asset diagnostic entrypoint for real Maya sessions.
    It can attach to an already-open commandPort, or launch Maya GUI and open one.

    Examples:
        uvx nox -s maya_asset_probe -- --attach-existing --port 7721 --asset F:/MMD/pmx/model.pmx
        uvx nox -s maya_asset_probe -- --maya 2026 --asset-list .ai/local_pmx_assets.txt --out-dir build/asset-error-probe/local
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    out = _option(session.posargs, "--out-dir", "build/asset-error-probe")
    out_path = _require_build_path(session, out, "--out-dir")

    forwarded: list[str] = []
    flags = {"--attach-existing", "--keep-maya", "--no-physics", "--reload-mmd-tools"}
    options = {
        "--asset",
        "--asset-list",
        "--port",
        "--timeout",
        "--startup-timeout",
        "--shader-backend",
        "--launch-mode",
    }
    i = 0
    while i < len(session.posargs):
        arg = session.posargs[i]
        if arg in {"--maya", "--out-dir"}:
            i += 2
            continue
        if arg in flags:
            forwarded.append(arg)
            i += 1
            continue
        if arg in options:
            if i + 1 >= len(session.posargs):
                session.error(f"{arg} requires a value")
            forwarded.extend([arg, session.posargs[i + 1]])
            i += 2
            continue
        i += 1

    session.run(
        sys.executable,
        "tests/viewport/maya_asset_error_probe.py",
        "--maya",
        version,
        "--out-dir",
        str(out_path),
        *forwarded,
        external=True,
    )


@nox.session(venv_backend="none")
def maya_batch_import(session: nox.Session) -> None:
    """Run Track 6 manifest-driven Maya batch import checks.

    Examples:
        uvx nox -s maya_batch_import -- --maya 2024 --scan-root F:\\MMD --write-manifest build/batch-import/manifest.json --max-models 20 --max-motions 20
        uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --limit 1
        uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --case failing_case --save-scenes
        uvx nox -s maya_batch_import -- --maya 2024 --manifest build/batch-import/manifest.json --limit 1 --save-scenes --capture
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    runner_args: list[str] = []
    skip_next = False
    for arg in session.posargs:
        if skip_next:
            skip_next = False
            continue
        if arg == "--maya":
            skip_next = True
            continue
        runner_args.append(arg)

    if not runner_args:
        runner_args = [
            "--manifest",
            str(ROOT / "tests/track6/manifest_template.json"),
            "--limit",
            "1",
        ]

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(mayapy, MAYA_VERSION=version)
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/track6/track6_runner.py"),
        *_convert_mayapy_path_options(
            mayapy,
            runner_args,
            {"--manifest", "--out-dir", "--scan-root", "--write-manifest"},
        ),
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def pmx_roundtrip(session: nox.Session) -> None:
    """PMX roundtrip: import \u2192 parse \u2192 export \u2192 re-import.

    For each manifest case:
      1. Parse source PMX via PmxData.
      2. New Maya scene, import source PMX.
      3. Convert PmxData \u2192 exporter dict.
      4. Export to a fresh PMX under --out-dir/exports/.
      5. Parse the exported PMX to verify binary integrity.
      6. New Maya scene, import the exported PMX.

    Results \u2192 --out-dir/results.json.

    Examples:
        uvx nox -s pmx_roundtrip -- --maya 2024
        uvx nox -s pmx_roundtrip -- --maya 2024 --manifest tests/roundtrip/manifest_template.json --limit 1
        uvx nox -s pmx_roundtrip -- --maya 2024 --case 1bone
        uvx nox -s pmx_roundtrip -- --maya 2024 --manifest tests/roundtrip/manifest_supported.json --require-clean --out-dir build/roundtrip/supported-clean
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    runner_args: list[str] = []
    skip_next = False
    for arg in session.posargs:
        if skip_next:
            skip_next = False
            continue
        if arg == "--maya":
            skip_next = True
            continue
        runner_args.append(arg)

    if not runner_args:
        runner_args = [
            "--manifest",
            str(ROOT / "tests/roundtrip/manifest_template.json"),
            "--limit",
            "1",
        ]

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/roundtrip/pmx_roundtrip_runner.py"),
        *_convert_mayapy_path_options(mayapy, runner_args, {"--manifest", "--out-dir"}),
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def flip_report(session: nox.Session) -> None:
    """Run NVIDIA FLIP image comparison (report-only, no pass/fail gate).

    Compares a reference PNG against a test (Maya capture) PNG using the
    external `flip` CLI.  Produces a text report, an error-map PNG, and an
    optional CSV summary.

    This session is **report-only**: the flip mean/weighted-median/max scores
    are NOT used to decide session pass/fail.  Only a flip process crash
    (non-zero exit) causes session failure.

    Default out-dir is rooted under build/flip-reports/.  If --out-dir is
    supplied relative it is resolved relative to the project root.

    Examples:
        uvx nox -s flip_report -- \\
            --reference F:\\Develop\\MMDDev\\GoldenOracle\\runs\\fixture-render\\fixture-render-generated-visual-mmd-diffuse-lit-box\\frame-0.png \\
            --test build/captures/static_render_1bone_cube.0000.png \\
            --out-dir build/flip-reports/static-render-1bone \\
            --basename static_render_1bone_cube \\
            --csv build/flip-reports/static-render-1bone/results.csv
    """
    args = session.posargs

    reference = _option(args, "--reference", "")
    test = _option(args, "--test", "")
    out_dir = _option(args, "--out-dir", "build/flip-reports/report")
    basename = _option(args, "--basename", "flip_result")
    csv = _option(args, "--csv", "")

    if not reference:
        session.error("--reference <path> is required")
    if not test:
        session.error("--test <path> is required")

    out_path = _require_build_path(session, out_dir, "--out-dir")
    out_path.mkdir(parents=True, exist_ok=True)

    csv_arg = ["-c", str(_require_build_path(session, csv, "--csv"))] if csv else []
    flip_exe = shutil.which("flip")
    if not flip_exe:
        session.error("NVIDIA FLIP CLI not found. Install dev dependencies with: python -m pip install -e .[dev]")

    cmd: list[str] = [
        flip_exe,
        "-r",
        reference,
        "-t",
        test,
        "-d",
        str(out_path),
        "-b",
        basename,
        "-txt",
        *csv_arg,
    ]

    session.log(f"FLIP report-only: reference={reference}, test={test}")
    session.log(f"  out-dir={out_path}, basename={basename}")
    session.run(*cmd, external=True)


@nox.session(venv_backend="none")
def cpp_cli_smoke(session: nox.Session) -> None:
    """Run the C++ standalone (no-Maya, no-mayapy) runtime smoke against a GoldenOracle-style manifest.

    Reads JSON manifest subset, resolves relative assets.model / assets.motion, uses
    mmd::RuntimeBridge (via built exe) to load PMX (+VMD), create model/clip/instance,
    evaluate frame(s), and report basic sanity counts (bones, morphs, matrix floats, etc).
    Fails on missing files, creation/eval errors, empty matrices, or NaN/Inf.

    Does NOT compare against oracle JSONL (v1).

    Examples:
        uvx nox -s cpp_cli_smoke -- --manifest F:\\Develop\\MMDDev\\GoldenOracle\\manifests\\fixture.motion.json
        uvx nox -s cpp_cli_smoke -- --manifest ... --case fixture-motion-generated-rest-pose-ik-chain --limit 1

    The exe is produced by 'cpp_build' (placed under build/cpp/maya<ver>/<config>/).
    Use --maya/--config to select which built exe to invoke (defaults 2024/Debug).
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)
    manifest = _option(session.posargs, "--manifest", "")
    case_name = _option(session.posargs, "--case", "")
    limit = _option(session.posargs, "--limit", "")
    if not manifest:
        session.error("--manifest <path> is required for cpp_cli_smoke")
    _run_cli_smoke(session, version, config, manifest, case_name, limit)


@nox.session(venv_backend="none")
def cpp_verify(session: nox.Session) -> None:
    """Run the CLI-only C++/native verification chain.

    Always: ffi (release) + native python smoke + cpp_configure + cpp_build.
    If --manifest is supplied: also runs cpp_cli_smoke (C++ standalone exe path)
    *before* the maya_smoke (mayapy) step.
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    config = _option(session.posargs, "--config", DEFAULT_CMAKE_CONFIG)

    env = os.environ.copy()
    _configure_bullet3_dir(session, env)
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        "--release",
        "--features",
        "physics-bullet-native",
        env=env,
        external=True,
    )

    runtime_env = os.environ.copy()
    runtime_env["MMD_ANIM_FFI_PATH"] = str((ROOT / "external" / "mmd-anim" / "target" / "release").resolve())
    session.run(sys.executable, "-c", _native_runtime_smoke_code(), env=runtime_env, external=True)

    _cmake_configure(session, version, config)
    _cmake_build(session, version, config)

    # Insert cpp_cli_smoke before maya_smoke when a manifest is supplied.
    # This exercises the pure C++ CLI path (no mayapy) for runtime eval.
    manifest = _option(session.posargs, "--manifest", "")
    case_name = _option(session.posargs, "--case", "")
    limit = _option(session.posargs, "--limit", "")
    _run_cli_smoke(session, version, config, manifest, case_name, limit)

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = _mayapy_env(
        mayapy,
        MAYA_VERSION=version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_CPP_CONFIG=config,
        MMD_ANIM_FFI_PATH=runtime_env["MMD_ANIM_FFI_PATH"],
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/smoke_runtime_node.py"),
        env=env,
        external=True,
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/focused_physics_solver_world_toggle.py"),
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def golden_oracle(session: nox.Session) -> None:
    """Verify mmd-anim runtime against GoldenOracle numeric manifest.

    Downloads the pinned mmd-anim GitHub Release CLI, verifies its SHA-256 and
    version, then runs ``mmd-anim verify <manifest> --mode numeric``. Set
    ``MMD_ANIM_CLI`` to use an explicit binary with the same version.
    Any regression beyond the manifest epsilon causes session failure.

    Examples:
        uvx nox -s golden_oracle
        uvx nox -s golden_oracle -- --manifest tests/golden-oracle/manifest.json
    """
    manifest = _option(
        session.posargs, "--manifest",
        str(ROOT / "tests/golden-oracle/manifest.json"),
    )
    mmd_anim = _downloaded_mmd_anim_cli(session)

    session.run(str(mmd_anim), "verify", manifest, "--mode", "numeric", external=True)


@nox.session(venv_backend="none")
def release_gate(session: nox.Session) -> None:
    """Run release verification gates with keep-going reporting.

    Examples:
        uvx nox -s release_gate -- --quick
        uvx nox -s release_gate -- --maya 2024
        uvx nox -s release_gate -- --with-cpp
        uvx nox -s release_gate -- --with-cpp --cpp-maya 2024 --cpp-maya 2026 --cpp-config Release
        uvx nox -s release_gate -- --ffi-cargo-target-dir build/mmd-anim-unlocked-target
        uvx nox -s release_gate -- --strict-local --local-parity-manifest F:/local/parity.json --local-physics-manifest F:/local/physics-parity.json
    """
    args = list(session.posargs)
    quick = _has_flag(args, "--quick")
    version = _option(args, "--maya", DEFAULT_MAYA_VERSION)
    cpp_versions = _options(args, "--cpp-maya") or list(DEFAULT_CPP_VERIFY_MAYA_VERSIONS)
    cpp_config = _option(args, "--cpp-config", DEFAULT_CMAKE_CONFIG)
    ffi_cargo_target_dir = _option(args, "--ffi-cargo-target-dir", "")
    ffi_path = _option(args, "--ffi-path", "")
    if ffi_cargo_target_dir and not ffi_path:
        ffi_path = str(Path(ffi_cargo_target_dir) / "release")
    strict_local = _has_flag(args, "--strict-local")
    verbose = _has_flag(args, "--verbose")
    local_assets_manifest = _option(args, "--local-assets-manifest", "local-assets-manifest.json")
    camera_manifest = _option(
        args,
        "--camera-manifest",
        "tests/data/camera_motion/manifest.json",
    )
    local_parity_manifest = _option(args, "--local-parity-manifest", "local-parity-manifest.json")
    visual_manifest = Path(
        _option(
            args,
            "--visual-manifest",
            os.environ.get("GOLDEN_ORACLE_RENDER_MANIFEST", DEFAULT_GOLDEN_ORACLE_RENDER_MANIFEST),
        )
    )
    results: list[dict[str, object]] = []

    if not quick:
        _run_release_gate_callable(
            "tier0:mmd-anim-pin",
            _release_gate_mmd_anim_pin_check,
            results,
        )
        if results[-1]["status"] == "fail":
            md_path, json_path = _write_release_gate_reports(results, quick)
            session.log(f"Release gate report: {md_path}")
            session.log(f"Release gate JSON: {json_path}")
            session.error(
                "Release gate preflight failed: "
                f"{_release_gate_failure_label(results[-1])}"
            )

    tier0_commands = [
        ("tier0:ruff", ["uvx", "ruff", "check", "--no-fix", "."]),
        ("tier0:diff-check", ["git", "diff", "--check"]),
    ]
    for name, command in tier0_commands:
        _run_release_gate_command(name, command, results, verbose=verbose)
    _run_release_gate_callable("tier0:version-markers", _release_gate_version_check, results)

    tier1_commands = [
        ("tier1:ci_unit", ["uvx", "nox", "-s", "ci_unit"]),
        ("tier1:golden_oracle", ["uvx", "nox", "-s", "golden_oracle"]),
        ("tier1:release-package", ["uvx", "nox", "-s", "release_package"]),
    ]
    if not quick:
        ffi_build_command = ["uvx", "nox", "-s", "ffi_build"]
        native_smoke_command = ["uvx", "nox", "-s", "native_smoke"]
        native_export_smoke_command = ["uvx", "nox", "-s", "native_export_smoke", "--", "--strict"]
        if ffi_cargo_target_dir:
            ffi_build_command.extend(["--", "--release", "--cargo-target-dir", ffi_cargo_target_dir])
        if ffi_path:
            native_smoke_command.extend(["--", "--ffi-path", ffi_path])
            native_export_smoke_command.extend(["--ffi-path", ffi_path])
        tier1_commands.extend(
            [
                ("tier1:ffi_build", ffi_build_command),
                ("tier1:native_smoke", native_smoke_command),
                ("tier1:native_export_smoke", native_export_smoke_command),
            ]
        )
    for name, command in tier1_commands:
        _run_release_gate_command(name, command, results, verbose=verbose)

    if not quick:
        tier2_commands = []
        for maya_version in DEFAULT_RELEASE_MAYA_VERSIONS:
            tier2_commands.extend(
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
        for maya_version, shader_backend, vp2_device in DEFAULT_RELEASE_VIEWPORT_MATRIX:
            tier2_commands.append(
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
            visual_outputs = {}
            for maya_version, shader_backend, vp2_device in DEFAULT_RELEASE_VIEWPORT_MATRIX:
                output = f"build/release-gate/visual/maya{maya_version}-{shader_backend}"
                visual_outputs[shader_backend] = output
                command = [
                    "uvx", "nox", "-s", "maya_visual_regression", "--",
                    "--maya", maya_version,
                    "--shader-backend", shader_backend,
                    "--vp2-device", vp2_device,
                    "--manifest", str(visual_manifest),
                    "--out", output,
                ]
                for case in _release_visual_cases(shader_backend):
                    command.extend(["--case", case])
                tier2_commands.append((f"tier2:generated-pmx-visual-{shader_backend}-{maya_version}", command))
            tier2_commands.append(
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
        else:
            _run_release_gate_callable(
                "tier2:generated-pmx-visual-manifest",
                lambda: (_ for _ in ()).throw(FileNotFoundError(
                    f"GoldenOracle render manifest not found: {visual_manifest}. "
                    "Pass --visual-manifest or set GOLDEN_ORACLE_RENDER_MANIFEST."
                )),
                results,
            )
        tier2_commands.extend([
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
                    "uvx",
                    "nox",
                    "-s",
                    "pmx_roundtrip",
                    "--",
                    "--maya",
                    version,
                    "--manifest",
                    "tests/roundtrip/manifest_v0_4.json",
                    "--require-clean",
                    "--out-dir",
                    "build/release-gate/pmx_roundtrip_v0_4",
                ],
            ),
            (
                "tier2:import-scale-drift",
                ["uvx", "nox", "-s", "import_scale_drift_e2e", "--", "--maya", version, "--expect", "fixed"],
            ),
            ("tier2:anim-layer-graph", ["uvx", "nox", "-s", "anim_layer_graph_compare", "--", "--maya", version]),
            (
                "tier2:import-order-e2e",
                ["uvx", "nox", "-s", "import_order_e2e", "--", "--maya", version, "--require-zero-fallback"],
            ),
            (
                "tier2:humanik-control-rig",
                [
                    "uvx",
                    "nox",
                    "-s",
                    "humanik_definition_smoke",
                    "--",
                    "--maya",
                    version,
                    "--fixture",
                    "body",
                    "--create-control-rig",
                    "--out",
                    "build/release-gate/humanik_control_rig_smoke.json",
                ],
            ),
        ])
        if _has_flag(args, "--with-cpp"):
            for cpp_version in cpp_versions:
                tier2_commands.append(
                    (
                        f"tier2:cpp-verify-{cpp_version}",
                        ["uvx", "nox", "-s", "cpp_verify", "--", "--maya", cpp_version, "--config", cpp_config],
                    )
                )
        if verbose:
            for name, command in tier2_commands:
                if name.startswith("tier2:mayapy-") and "--verbose" not in command:
                    command.append("--verbose")
        for name, command in tier2_commands:
            _run_release_gate_command(name, command, results, verbose=verbose)

        tier3_commands = [
            (
                "tier3:local-assets-check",
                [
                    "uvx",
                    "nox",
                    "-s",
                    "local_assets_check",
                    "--",
                    "--maya",
                    version,
                    "--manifest",
                    local_assets_manifest,
                    "--out-json",
                    "build/reports/release_gate_local_assets.json",
                    "--out-md",
                    "build/reports/release_gate_local_assets.md",
                ],
                ROOT / "build/reports/release_gate_local_assets.json",
            ),
            (
                "tier3:release-camera-motion-oracle",
                [
                    "uvx",
                    "nox",
                    "-s",
                    "release_camera_motion_oracle",
                    "--",
                    "--maya",
                    version,
                    "--manifest",
                    camera_manifest,
                    "--skip-addiction-parity",
                    "--out-dir",
                    "build/release-gate/camera-motion",
                ],
                ROOT / "build/release-gate/camera-motion/manifest-skip.json",
            ),
            (
                "tier3:local-parity",
                [
                    "uvx",
                    "nox",
                    "-s",
                    "local_parity",
                    "--",
                    "--maya",
                    version,
                    "--manifest",
                    local_parity_manifest,
                    "--skip-fbx",
                    "--out",
                    "build/reports/release_gate_local_parity.json",
                ],
                ROOT / "build/reports/release_gate_local_parity.json",
            ),
        ]
        if strict_local:
            for _, command, _ in tier3_commands:
                command.append("--strict-local")
        for name, command, result_report in tier3_commands:
            _run_release_gate_command(
                name,
                command,
                results,
                result_report=result_report,
                required_local=True,
                strict_local=strict_local,
                verbose=verbose,
            )

    md_path, json_path = _write_release_gate_reports(results, quick)
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in ("pass", "fail", "skip")
    }
    print(
        _format_test_summary(
            "release_gate",
            total=len(results),
            passed=counts["pass"],
            skipped=counts["skip"],
            failed=counts["fail"],
            duration_sec=sum(float(result["duration_sec"]) for result in results),
        )
    )
    session.log(f"Release gate report: {md_path}")
    session.log(f"Release gate JSON: {json_path}")

    failed = [result for result in results if result["status"] == "fail"]
    if failed:
        failed_names = ", ".join(str(result["name"]) for result in failed)
        print(
            "[release_gate] first failure: "
            f"{_release_gate_failure_label(failed[0])}"
        )
        print(f"[release_gate] failed gates: {failed_names}")
        failed_tests = list(
            dict.fromkeys(
                str(test)
                for result in failed
                for test in result.get("failed_tests", [])
            )
        )
        if failed_tests:
            print(f"[release_gate] failed tests: {', '.join(failed_tests)}")
        failed_logs = [str(result["log"]) for result in failed if result.get("log")]
        if any(not result.get("log") for result in failed):
            failed_logs.append(str(json_path))
        if failed_logs:
            print(f"[release_gate] failure logs: {', '.join(failed_logs)}")
        session.error(f"Release gate failed: {failed_names}")


@nox.session(venv_backend="none")
def local_assets_check(session: nox.Session) -> None:
    """Run local PMX/VMD asset smoke checks from an optional manifest.

    Manifest format:
        {"assets": [{"name": "case", "model": "path/to/model.pmx", "motion": "optional.vmd"}]}

    Examples:
        uvx nox -s local_assets_check
        uvx nox -s local_assets_check -- --manifest F:/local/assets.json --strict-local
    """
    args = list(session.posargs)
    version = _option(args, "--maya", DEFAULT_MAYA_VERSION)
    manifest = Path(_option(args, "--manifest", "local-assets-manifest.json"))
    strict = _has_flag(args, "--strict-local")
    out_json = _require_build_path(
        session,
        _option(args, "--out-json", "build/reports/local_assets_check.json"),
        "--out-json",
    )
    out_md = _require_build_path(
        session,
        _option(args, "--out-md", "build/reports/local_assets_check.md"),
        "--out-md",
    )

    if not manifest.is_absolute():
        manifest = ROOT / manifest
    manifest = manifest.resolve()

    if not manifest.exists():
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "fail" if strict else "skip",
            "results": [
                {
                    "name": str(manifest),
                    "status": "fail" if strict else "skip",
                    "duration_sec": 0.0,
                    "detail": "manifest not found",
                }
            ],
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out_md.write_text(
            "\n".join(
                [
                    "# Local Assets Check",
                    "",
                    f"- Status: {payload['status']}",
                    "",
                    "| Asset | Status | Seconds | Detail |",
                    "| --- | --- | ---: | --- |",
                    f"| {manifest} | {payload['status']} | 0.0 | manifest not found |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        session.log(f"Local assets manifest not found: {manifest}")
        session.log(f"Local assets report: {out_md}")
        if strict:
            session.error("Local assets manifest is required with --strict-local")
        return

    mayapy = _mayapy(version)
    env = _mayapy_env(mayapy, MAYA_VERSION=version)
    command = [
        str(mayapy),
        _mayapy_script(mayapy, "tests/local/local_assets_check.py"),
        "--manifest",
        _mayapy_arg_path(mayapy, manifest),
        "--out-json",
        _mayapy_arg_path(mayapy, out_json),
        "--out-md",
        _mayapy_arg_path(mayapy, out_md),
    ]
    if strict:
        command.append("--strict-local")
    session.run(*command, env=env, external=True)
    status = _normalize_local_gate_report(out_json, strict, out_md)
    session.log(f"Local assets report: {out_md}")
    session.log(f"Local assets JSON: {out_json}")
    if status == "fail":
        session.error("Local assets check failed")


@nox.session(venv_backend="none")
def semistandard_name_audit(session: nox.Session) -> None:
    """Audit local PMX/VMD assets for semistandard bone-name conversion gaps.

    Examples:
        uvx nox -s semistandard_name_audit -- --scan-root F:/MMD --max-files 200
        uvx nox -s semistandard_name_audit -- --manifest build/batch-import/manifest.json --strict-local
    """
    args = list(session.posargs)
    out_json = _require_build_path(
        session,
        _option(args, "--out-json", "build/reports/semistandard_name_audit.json"),
        "--out-json",
    )
    out_md = _require_build_path(
        session,
        _option(args, "--out-md", "build/reports/semistandard_name_audit.md"),
        "--out-md",
    )

    passthrough: list[str] = []
    i = 0
    value_options = {
        "--manifest",
        "--scan-root",
        "--max-files",
        "--out-json",
        "--out-md",
        "--limit-findings",
        "--min-candidate-files",
        "--min-candidate-findings",
    }
    flag_options = {"--strict-local"}
    while i < len(args):
        arg = args[i]
        if arg in value_options and i + 1 < len(args):
            value = args[i + 1]
            if arg in {"--manifest", "--scan-root", "--out-json", "--out-md"}:
                path = Path(value)
                value = str(path.resolve() if path.is_absolute() else (ROOT / path).resolve())
            passthrough.extend([arg, value])
            i += 2
            continue
        if arg in flag_options:
            passthrough.append(arg)
            i += 1
            continue
        passthrough.append(arg)
        i += 1

    if "--out-json" not in passthrough:
        passthrough.extend(["--out-json", str(out_json)])
    if "--out-md" not in passthrough:
        passthrough.extend(["--out-md", str(out_md)])

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    session.run(sys.executable, "tests/local/semistandard_name_audit.py", *passthrough, env=env, external=True)
    session.log(f"Semistandard name audit report: {out_md}")
    session.log(f"Semistandard name audit JSON: {out_json}")


@nox.session(venv_backend="none")
def local_camera_motion_oracle(session: nox.Session) -> None:
    """Run local-only GoldenOracle camera-motion checks against Maya camera import.

    The default manifest path points outside this repository and is expected to
    exist only on the developer machine. This session is not part of CI.

    Examples:
        uvx nox -s local_camera_motion_oracle -- --maya 2024 --case camera-edge-generated-vmd
        uvx nox -s local_camera_motion_oracle -- --mode sparse --limit 2
        uvx nox -s local_camera_motion_oracle -- --current-epsilon 0.0005 --case camera-edge-generated-vmd
        uvx nox -s local_camera_motion_oracle -- --current-report-only --case camera-shake-it-nanoem
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    value_options = {
        "--manifest",
        "--case",
        "--limit",
        "--mode",
        "--max-current-frames",
        "--epsilon",
        "--current-epsilon",
        "--current-frame-zero",
        "--out",
    }
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] in {"--all-frames", "--current-report-only"}:
            passthrough.append(args[i])
            i += 1
            continue
        passthrough.append(args[i])
        i += 1
    passthrough = _copy_parity_vmd_for_mayapy(session, passthrough)

    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/local/camera_motion_oracle_runner.py"),
        "--repo-root",
        _maya_process_path(mayapy, ROOT),
        *_convert_mayapy_path_options(
            mayapy,
            passthrough,
            {"--manifest", "--out", "--parity-vmd"},
        ),
        env=_mayapy_env(mayapy),
        external=True,
    )


@nox.session(venv_backend="none")
def release_camera_motion_oracle(session: nox.Session) -> None:
    """Run the local GoldenOracle camera-motion release gate.

    Bake mode gates raw keyframes and playback camera.current pose. Sparse Rig
    mode gates raw keyframes and editable-rig structure while keeping playback
    camera.current deltas as a report, because the Rig path now preserves VMD
    keys as Maya direct animation instead of using dense samples or expression
    evaluation. The runner's default camera.current frame 0 policy skips
    non-generated GoldenOracle dump frame 0.

    Examples:
        uvx nox -s release_camera_motion_oracle -- --maya 2024
        uvx nox -s release_camera_motion_oracle -- --manifest F:\\Develop\\MMDDev\\GoldenOracle\\manifests\\camera_motion.json
        uvx nox -s release_camera_motion_oracle -- --all-cases
        uvx nox -s release_camera_motion_oracle -- --strict-sparse-current
        uvx nox -s release_camera_motion_oracle -- --skip-addiction-parity
        uvx nox -s release_camera_motion_oracle -- --strict-local
    """
    args = list(session.posargs)
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    manifest = _option(
        session.posargs,
        "--manifest",
        "tests/data/camera_motion/manifest.json",
    )
    out_dir = _require_build_path(
        session,
        _option(session.posargs, "--out-dir", "build/local-camera-motion-oracle/release"),
        "--out-dir",
    )
    manifest_path = Path(manifest)
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest_path = manifest_path.resolve()
    if not manifest_path.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        skip_report = out_dir / "manifest-skip.json"
        payload = {
            "status": "fail" if _has_flag(args, "--strict-local") else "skip",
            "summary": {"passed": 0, "failed": 1 if _has_flag(args, "--strict-local") else 0, "skipped": 1},
            "manifest": str(manifest_path),
            "detail": "manifest not found",
        }
        skip_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        session.log(f"Camera motion manifest not found: {manifest_path}")
        session.log(f"Camera motion skip report: {skip_report}")
        if _has_flag(args, "--strict-local"):
            session.error("Camera motion manifest is required with --strict-local")
        return
    default_release_cases = [
        "camera-edge-generated-vmd",
        "camera-interpolation-isolated-vmd",
    ]
    requested_case = _option(session.posargs, "--case", "")
    if _has_flag(session.posargs, "--all-cases"):
        selected_cases = [""]
    elif requested_case:
        selected_cases = [requested_case]
    else:
        selected_cases = default_release_cases
    args = list(session.posargs)
    common_args = ["--manifest", manifest]
    if "--current-epsilon" not in args:
        common_args.extend(["--current-epsilon", RELEASE_CAMERA_CURRENT_EPSILON])
    parity_args: list[str] = [
        "--parity-current-report-only",
        "--all-frames",
        "--parity-interpolation-eye-max",
        _option(session.posargs, "--parity-interpolation-eye-max", RELEASE_ADDICTION_INTERPOLATION_EYE_MAX),
        "--parity-interpolation-forward-max-deg",
        _option(
            session.posargs,
            "--parity-interpolation-forward-max-deg",
            RELEASE_ADDICTION_INTERPOLATION_FORWARD_MAX_DEG,
        ),
        "--parity-interpolation-up-max-deg",
        _option(session.posargs, "--parity-interpolation-up-max-deg", RELEASE_ADDICTION_INTERPOLATION_UP_MAX_DEG),
        "--parity-interpolation-rotation-max-deg",
        _option(
            session.posargs,
            "--parity-interpolation-rotation-max-deg",
            RELEASE_ADDICTION_INTERPOLATION_ROTATION_MAX_DEG,
        ),
    ]
    parity_epsilon = _option(session.posargs, "--parity-epsilon", "")
    if parity_epsilon:
        parity_args.extend(["--parity-epsilon", parity_epsilon])
    i = 0
    passthrough_value_options = {
        "--case",
        "--limit",
        "--max-current-frames",
        "--epsilon",
        "--current-epsilon",
        "--current-frame-zero",
        "--parity-interpolation-eye-max",
        "--parity-interpolation-forward-max-deg",
        "--parity-interpolation-up-max-deg",
        "--parity-interpolation-rotation-max-deg",
    }
    consumed_value_options = {
        "--maya",
        "--manifest",
        "--out-dir",
        "--case",
        "--parity-epsilon",
        "--parity-interpolation-eye-max",
        "--parity-interpolation-forward-max-deg",
        "--parity-interpolation-up-max-deg",
        "--parity-interpolation-rotation-max-deg",
    }
    while i < len(args):
        if args[i] in consumed_value_options and i + 1 < len(args):
            i += 2
            continue
        if args[i] in passthrough_value_options and i + 1 < len(args):
            common_args.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] in {"--all-frames", "--all-cases", "--current-report-only"}:
            if args[i] == "--all-cases":
                i += 1
                continue
            common_args.append(args[i])
            i += 1
            continue
        i += 1

    failed_reports: list[str] = []
    for case_name in selected_cases:
        case_args = list(common_args)
        case_suffix = "all-cases"
        if case_name:
            case_args.extend(["--case", case_name])
            case_suffix = case_name
        for mode in ("bake", "sparse"):
            report_path = out_dir / f"{mode}-{case_suffix}.json"
            runner_args = [
                *case_args,
                "--mode",
                mode,
                "--out",
                str(report_path),
            ]
            if mode == "sparse" and not _has_flag(session.posargs, "--strict-sparse-current"):
                runner_args.append("--current-report-only")
            session.run(
                str(mayapy),
                _mayapy_script(mayapy, "tests/local/camera_motion_oracle_runner.py"),
                "--repo-root",
                _maya_process_path(mayapy, ROOT),
                *_convert_mayapy_path_options(
                    mayapy,
                    runner_args,
                    {"--manifest", "--out"},
                ),
                env=_mayapy_env(mayapy),
                external=True,
                success_codes=[0, 1],
            )
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                failed = int((report.get("summary") or {}).get("failed", 0))
            except Exception:
                failed = 1
            if failed:
                failed_reports.append(str(report_path))
    if not _has_flag(session.posargs, "--skip-addiction-parity"):
        addiction_vmd = Path(RELEASE_ADDICTION_CAMERA_VMD)
        if addiction_vmd.exists():
            report_path = out_dir / "bake-rig-camera-addiction.json"
            addiction_args = _copy_parity_vmd_for_mayapy(
                session,
                ["--parity-vmd", str(addiction_vmd), *parity_args],
            )
            session.run(
                str(mayapy),
                _mayapy_script(mayapy, "tests/local/camera_motion_oracle_runner.py"),
                "--repo-root",
                _maya_process_path(mayapy, ROOT),
                "--parity-case-name",
                "camera-addiction-bake-rig-parity",
                "--out",
                _maya_process_path(mayapy, report_path),
                *_convert_mayapy_path_options(mayapy, addiction_args, {"--parity-vmd"}),
                env=_mayapy_env(mayapy),
                external=True,
                success_codes=[0, 1],
            )
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                failed = int((report.get("summary") or {}).get("failed", 0))
            except Exception:
                failed = 1
            if failed:
                failed_reports.append(str(report_path))
        else:
            session.log(f"Skipping Addiction camera parity; local VMD not found: {addiction_vmd}")
    if failed_reports:
        session.error("Camera motion release gate failed; reports: " + ", ".join(failed_reports))


@nox.session(venv_backend="none")
def local_parity(session: nox.Session) -> None:
    """Run Bake-vs-Rig mesh parity on local (non-committed) PMX/VMD assets.

    Non-ASCII asset paths are transparently aliased via Windows junctions
    so that mayapy batch mode can store them in Maya string attributes
    without codepage corruption.

    Examples:
        uvx nox -s local_parity -- --maya 2024
        uvx nox -s local_parity -- --maya 2024 --case alicia_weekender
        uvx nox -s local_parity -- --maya 2024 --manifest F:/local/parity.json
        uvx nox -s local_parity -- --maya 2024 --skip-fbx
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    manifest = _option(args, "--manifest", "")
    out_json = _option(args, "--out", "build/reports/local_asset_motion_compare.json")
    if manifest:
        manifest_path = Path(manifest)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        manifest_path = manifest_path.resolve()
        if not manifest_path.exists():
            out_path = _require_build_path(session, out_json, "--out")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            md_path = out_path.with_suffix(".md")
            status = "failed" if _has_flag(args, "--strict-local") else "skipped"
            payload = {
                "status": status,
                "vertex_threshold": None,
                "fbx_threshold": None,
                "cases": [
                    {
                        "name": str(manifest_path),
                        "status": status,
                        "reason": "manifest_not_found",
                    }
                ],
            }
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            md_path.write_text(
                "\n".join(
                    [
                        "# Local Asset Motion Compare",
                        "",
                        f"- status: `{status}`",
                        "- cases: `1`",
                        "",
                        f"## {manifest_path}",
                        "",
                        f"- status: `{status}`",
                        "- reason: `manifest_not_found`",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            session.log(f"Local parity manifest not found: {manifest_path}")
            session.log(f"Local parity report: {md_path}")
            if _has_flag(args, "--strict-local"):
                session.error("Local parity manifest is required with --strict-local")
            return
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in ("--case", "--frame", "--out", "--manifest") and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] in ("--skip-fbx", "--strict-local"):
            passthrough.append(args[i])
            i += 1
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/local_asset_motion_compare.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--out"}),
        env=_mayapy_env(mayapy, preserve_pythonpath=True),
        external=True,
    )


@nox.session(venv_backend="none")
def import_order_e2e(session: nox.Session) -> None:
    """Run manifest-driven mayapy E2E checks for model/motion import ordering.

    Examples:
        uvx nox -s import_order_e2e -- --maya 2024
        uvx nox -s import_order_e2e -- --maya 2024 --log build/import-order-e2e/run.jsonl
        uvx nox -s import_order_e2e -- --maya 2024 --manifest build/import-order-e2e/local-manifest.json
        uvx nox -s import_order_e2e -- --maya 2024 --background-model F:/MMD/stage/stage.pmx --character-model F:/MMD/pmx/miku.pmx --character-motion F:/MMD/vmd/dance.vmd
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    manifest = _option(args, "--manifest", "")
    path_options = {"--manifest", "--out-dir", "--log"}
    value_options = path_options | {"--background-model", "--character-model", "--character-motion", "--case", "--limit", "--order-limit"}
    flag_options = {"--require-zero-fallback"}
    if not manifest:
        generated_manifest = _write_import_order_local_manifest(
            session,
            _option(args, "--background-model", str(ROOT / "tests/data/for_unit_test/test_1bone_cube.pmx")),
            _option(args, "--character-model", str(ROOT / "tests/data/mmt_test_model.pmx")),
            _option(args, "--character-motion", str(ROOT / "tests/data/mmt_test_model_test_motion.vmd")),
        )
        passthrough.extend(["--manifest", str(generated_manifest)])
    env = _mayapy_env(mayapy, preserve_pythonpath=True)
    if _has_flag(args, "--require-zero-fallback"):
        profile_value = os.environ.get("MMD_TOOLS_VMD_PROFILE_JSONL")
        if profile_value:
            profile_path = Path(profile_value)
            if not profile_path.is_absolute():
                profile_path = ROOT / profile_path
        else:
            out_dir_value = _option(args, "--out-dir", str(ROOT / "build/import-order-e2e"))
            out_dir_path = Path(out_dir_value)
            if not out_dir_path.is_absolute():
                out_dir_path = ROOT / out_dir_path
            profile_path = out_dir_path / "vmd_profile.jsonl"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        if profile_path.exists():
            profile_path.unlink()
        env["MMD_TOOLS_VMD_PROFILE_JSONL"] = str(profile_path)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if not manifest and args[i] in {"--background-model", "--character-model", "--character-motion"}:
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] in flag_options:
            passthrough.append(args[i])
            i += 1
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/import_order_e2e.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def import_scale_drift_e2e(session: nox.Session) -> None:
    """Run mayapy E2E diagnostics for import scale / skin bind drift.

    Examples:
        uvx nox -s import_scale_drift_e2e -- --maya 2024
        uvx nox -s import_scale_drift_e2e -- --maya 2024 --expect fixed
        uvx nox -s import_scale_drift_e2e -- --maya 2024 --scale 1.0 --scale 2.0 --log build/import-scale-drift/run.jsonl
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    path_options = {"--model", "--log"}
    value_options = path_options | {"--scale", "--expect", "--clean-threshold", "--drift-threshold", "--parser"}
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/import_scale_drift_e2e.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=_mayapy_env(mayapy, preserve_pythonpath=True),
        external=True,
    )


@nox.session(venv_backend="none")
def anim_layer_graph_compare(session: nox.Session) -> None:
    """Run mayapy diagnostics comparing setKeyframe and API animLayer graphs.

    Examples:
        uvx nox -s anim_layer_graph_compare -- --maya 2024
        uvx nox -s anim_layer_graph_compare -- --maya 2024 --case joint_translate --case joint_rotate
        uvx nox -s anim_layer_graph_compare -- --maya 2024 --out build/reports/anim_layer_graph_compare.json
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    path_options = {"--out"}
    value_options = path_options | {"--case", "--tolerance"}
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/anim_layer_graph_compare.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=_mayapy_env(mayapy, preserve_pythonpath=True),
        external=True,
    )


@nox.session(venv_backend="none")
def runtime_bake_bench(session: nox.Session) -> None:
    """Measure the Maya runtime-bake import path.

    Examples:
        uvx nox -s runtime_bake_bench -- --maya 2024
        uvx nox -s runtime_bake_bench -- --maya 2024 --repeat 3
        uvx nox -s runtime_bake_bench -- --maya 2024 --case lumine_rabbithole
        uvx nox -s runtime_bake_bench -- --maya 2024 --case eunice_rabbithole
        uvx nox -s runtime_bake_bench -- --pmx tests/data/mmt_test_model.pmx --vmd tests/data/mmt_test_model_test_motion.vmd
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in ("--case", "--pmx", "--vmd", "--out", "--log", "--repeat") and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/runtime_bake_benchmark.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, {"--pmx", "--vmd", "--out", "--log"}),
        env=_mayapy_env(mayapy, preserve_pythonpath=True),
        external=True,
    )
