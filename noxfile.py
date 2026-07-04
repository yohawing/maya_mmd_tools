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


DEFAULT_MAYA_VERSION = "2024"
DEFAULT_CMAKE_CONFIG = "Debug"
DEFAULT_CPP_VERIFY_MAYA_VERSIONS = ("2024", "2025", "2026", "2027")
RELEASE_CAMERA_CURRENT_EPSILON = "18.25"
RELEASE_ADDICTION_INTERPOLATION_EYE_MAX = "2.0"
RELEASE_ADDICTION_INTERPOLATION_FORWARD_MAX_DEG = "5.0"
RELEASE_ADDICTION_INTERPOLATION_UP_MAX_DEG = "5.0"
RELEASE_ADDICTION_INTERPOLATION_ROTATION_MAX_DEG = "5.0"
RELEASE_ADDICTION_CAMERA_VMD = (
    "F:/MMD/vmd/175_Addictionカメラモーションv1.3/"
    "Addictionカメラモーション/Addictionカメラ用モーション(一人用).vmd"
)
MMD_ANIM_CLI_VERSION = "v0.1.9"
MMD_ANIM_CLI_REPO = "yohawing/mmd-anim"
MMD_ANIM_CLI_ASSETS = {
    "Windows": {
        "archive": "mmd-anim-v0.1.9-x86_64-pc-windows-msvc.zip",
        "sha256": "8fa674e2b8104324aaf84351ec91e857c47a768345a5e806c5da543cca0b2859",
        "exe": "mmd-anim.exe",
    },
    "Linux": {
        "archive": "mmd-anim-v0.1.9-x86_64-unknown-linux-gnu.tar.gz",
        "sha256": "821164e1db3191492303b5290b0a9166fd6bede41384eb805836f4ed3e03a576",
        "exe": "mmd-anim",
    },
}

nox.options.sessions = ["tests"]


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
    """Return the mmd-anim CLI version string."""
    result = subprocess.run(
        [str(exe), "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


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


def _release_gate_version_check() -> None:
    """Validate release version markers before running expensive gates."""
    import re
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    init_text = (ROOT / "mmd_tools" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if not init_match or init_match.group(1) != version:
        raise RuntimeError(f"mmd_tools/__init__.py version does not match pyproject.toml: {version}")

    mod_text = (ROOT / "maya_mmd_tools.mod").read_text(encoding="utf-8")
    mod_versions = set(re.findall(r"maya_mmd_tools\s+([0-9]+\.[0-9]+\.[0-9]+)", mod_text))
    if mod_versions != {version}:
        raise RuntimeError(f"maya_mmd_tools.mod versions {sorted(mod_versions)} do not match {version}")

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


def _run_release_gate_command(
    name: str,
    command: list[str],
    results: list[dict[str, object]],
) -> None:
    """Run a release-gate command and append a keep-going result entry."""
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, text=True, check=False)
    results.append(
        {
            "name": name,
            "command": command,
            "status": "pass" if completed.returncode == 0 else "fail",
            "returncode": completed.returncode,
            "duration_sec": round(time.perf_counter() - started, 3),
        }
    )


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
        results.append(
            {
                "name": name,
                "command": [],
                "status": "fail",
                "returncode": 1,
                "duration_sec": round(time.perf_counter() - started, 3),
                "error": str(exc),
            }
        )
    else:
        results.append(
            {
                "name": name,
                "command": [],
                "status": "pass",
                "returncode": 0,
                "duration_sec": round(time.perf_counter() - started, 3),
            }
        )


def _write_release_gate_reports(results: list[dict[str, object]], quick: bool) -> tuple[Path, Path]:
    """Write release-gate Markdown and JSON summaries."""
    report_dir = ROOT / "build" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "release_gate.json"
    md_path = report_dir / "release_gate.md"

    failed = [result for result in results if result["status"] != "pass"]
    payload = {
        "quick": quick,
        "status": "fail" if failed else "pass",
        "results": results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Release Gate",
        "",
        f"- Mode: {'quick' if quick else 'full'}",
        f"- Status: {payload['status']}",
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


@nox.session(venv_backend="none")
def ci_unit(session: nox.Session) -> None:
    """Run pure-python unit tests without mayapy.

    Dynamically discovers tests/unit/test_*.py files that can be imported
    without Maya, so any new tests added to tests/unit are automatically
    included — no manual listing required.

    A test file is included when it can be imported successfully with a
    plain ``python -c "import tests.unit.<stem>"`` probe (i.e. it has no
    transitive dependency on ``maya``).  Files that fail this probe are
    skipped with a notice; they require mayapy and belong to the ``tests``
    session instead.

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
        else:
            skipped.append(py_file.name)

    if skipped:
        session.log(
            f"Skipping {len(skipped)} test file(s) that require mayapy: "
            + ", ".join(skipped)
        )

    if not importable:
        session.error("No importable pure-python unit tests found in tests/unit/")

    session.log(f"Running {len(importable)} pure-python unit test module(s)")
    session.run(
        sys.executable,
        "-m",
        "unittest",
        *importable,
        external=True,
    )


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
    code = (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "lib = get_mmd_runtime_library(); "
        "print(get_runtime_library_path()); "
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 2 else 1)"
    )
    session.run(sys.executable, "-c", code, env=env, external=True)


@nox.session(venv_backend="none")
def native_export_smoke(session: nox.Session) -> None:
    """Verify native VMD/PMD/PMX export writer symbols when the DLL is current.

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
    """Run manifest-driven Maya GUI / DX11 viewport visual regression captures.

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

    out = _option(session.posargs, "--out", "build/visual-regression/maya-dx11")
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
    ]
    cmd.extend(forwarded)
    session.run(*cmd, external=True)


@nox.session(venv_backend="none")
def maya_physics_collider_capture(session: nox.Session) -> None:
    """Capture MMD physics collider locator drawing in Maya GUI / DX11.

    The harness imports a physics fixture with Bullet enabled, verifies
    ``mmdRigidBodyLocator`` shapes exist, captures one VP2 PNG, and validates
    that the DX11 device plus cyan-ish wire pixels are present.

    Examples:
        uvx nox -s maya_physics_collider_capture -- --maya 2026
        uvx nox -s maya_physics_collider_capture -- --maya 2026 --attach-existing --out build/captures/gui-physics-collider/physics_collider.png
    """
    version = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    model = _option(session.posargs, "--model", str(ROOT / "tests/data/physics/test_hair_physics.pmx"))
    out = _option(session.posargs, "--out", str(ROOT / "build/captures/gui-physics-collider/physics_collider.png"))
    port = _option(session.posargs, "--port", "7726")
    width = _option(session.posargs, "--width", "1280")
    height = _option(session.posargs, "--height", "720")

    forwarded: list[str] = []
    for flag in ("--attach-existing", "--leave-open"):
        if flag in session.posargs:
            forwarded.append(flag)

    session.run(
        sys.executable,
        "tests/viewport/gui_physics_collider_capture.py",
        "--maya",
        version,
        "--model",
        model,
        "--out",
        out,
        "--port",
        port,
        "--width",
        width,
        "--height",
        height,
        *forwarded,
        external=True,
    )


@nox.session(venv_backend="none")
def maya_physics_preview_numeric(session: nox.Session) -> None:
    """Sample representative Bullet physics preview transforms under mayapy.

    Examples:
        uvx nox -s maya_physics_preview_numeric -- --maya 2024
        uvx nox -s maya_physics_preview_numeric -- --maya 2024 --frames 1,30,60 --out build/reports/physics_preview_numeric.json
    """
    maya_ver = _option(session.posargs, "--maya", DEFAULT_MAYA_VERSION)
    mayapy = _mayapy(maya_ver)
    passthrough: list[str] = []
    args = list(session.posargs)
    path_options = {"--model", "--out"}
    value_options = path_options | {
        "--frames",
        "--min-dynamic-movers",
        "--min-tip-rotate-deg",
        "--max-displacement",
    }
    i = 0
    while i < len(args):
        if args[i] == "--maya" and i + 1 < len(args):
            i += 2
            continue
        if args[i] in value_options and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] == "--dgdirty":
            passthrough.append(args[i])
            i += 1
            continue
        i += 1
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/physics_preview_numeric_harness.py"),
        *_convert_mayapy_path_options(mayapy, passthrough, path_options),
        env=_mayapy_env(mayapy, preserve_pythonpath=True),
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

    env = _mayapy_env(mayapy, MAYA_VERSION=version)
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

    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        "--release",
        external=True,
    )

    code = (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "lib = get_mmd_runtime_library(); "
        "print(get_runtime_library_path()); "
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 2 else 1)"
    )
    session.run(sys.executable, "-c", code, external=True)

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

    env = _mayapy_env(mayapy, MAYA_VERSION=version, MMD_TOOLS_CPP_CONFIG=config)
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/cpp/smoke_runtime_node.py"),
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
        uvx nox -s release_gate -- --strict-local --local-parity-manifest F:/local/parity.json
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
    local_assets_manifest = _option(args, "--local-assets-manifest", "local-assets-manifest.json")
    camera_manifest = _option(
        args,
        "--camera-manifest",
        "F:/Develop/MMDDev/GoldenOracle/manifests/camera_motion.json",
    )
    local_parity_manifest = _option(args, "--local-parity-manifest", "local-parity-manifest.json")
    results: list[dict[str, object]] = []

    tier0_commands = [
        ("tier0:ruff", ["uvx", "ruff", "check", "--no-fix", "."]),
        ("tier0:diff-check", ["git", "diff", "--check"]),
    ]
    for name, command in tier0_commands:
        _run_release_gate_command(name, command, results)
    _run_release_gate_callable("tier0:version-markers", _release_gate_version_check, results)

    tier1_commands = [
        ("tier1:ci_unit", ["uvx", "nox", "-s", "ci_unit"]),
        ("tier1:golden_oracle", ["uvx", "nox", "-s", "golden_oracle"]),
    ]
    if not quick:
        ffi_build_command = ["uvx", "nox", "-s", "ffi_build"]
        native_smoke_command = ["uvx", "nox", "-s", "native_smoke"]
        native_export_smoke_command = ["uvx", "nox", "-s", "native_export_smoke"]
        if ffi_cargo_target_dir:
            ffi_build_command.extend(["--", "--release", "--cargo-target-dir", ffi_cargo_target_dir])
        if ffi_path:
            native_smoke_command.extend(["--", "--ffi-path", ffi_path])
            native_export_smoke_command.extend(["--", "--strict", "--ffi-path", ffi_path])
        tier1_commands.extend(
            [
                ("tier1:ffi_build", ffi_build_command),
                ("tier1:native_smoke", native_smoke_command),
                ("tier1:native_export_smoke", native_export_smoke_command),
            ]
        )
    for name, command in tier1_commands:
        _run_release_gate_command(name, command, results)

    if not quick:
        tier2_commands = [
            ("tier2:mayapy-unit", ["uvx", "nox", "-s", "tests", "--", "--type", "unit"]),
            ("tier2:mayapy-integration", ["uvx", "nox", "-s", "tests", "--", "--type", "integration"]),
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
        ]
        if _has_flag(args, "--with-cpp"):
            for cpp_version in cpp_versions:
                tier2_commands.append(
                    (
                        f"tier2:cpp-verify-{cpp_version}",
                        ["uvx", "nox", "-s", "cpp_verify", "--", "--maya", cpp_version, "--config", cpp_config],
                    )
                )
        for name, command in tier2_commands:
            _run_release_gate_command(name, command, results)

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
                ],
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
                ],
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
                ],
            ),
        ]
        if strict_local:
            for _, command in tier3_commands:
                command.append("--strict-local")
        for name, command in tier3_commands:
            _run_release_gate_command(name, command, results)

    md_path, json_path = _write_release_gate_reports(results, quick)
    session.log(f"Release gate report: {md_path}")
    session.log(f"Release gate JSON: {json_path}")

    failed = [result for result in results if result["status"] != "pass"]
    if failed:
        failed_names = ", ".join(str(result["name"]) for result in failed)
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
    session.log(f"Local assets report: {out_md}")
    session.log(f"Local assets JSON: {out_json}")


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
        "F:/Develop/MMDDev/GoldenOracle/manifests/camera_motion.json",
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
