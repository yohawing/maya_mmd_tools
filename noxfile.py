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
from pathlib import Path

import nox

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from noxlib.common import (  # noqa: E402
    _cargo_args_with_physics_feature,
    _download_file,
    _extract_archive,
    _has_flag,
    _option,
    _options,
    _sha256_file,
    _without_option,
)
from noxlib.common import _mmd_anim_cli_version as _common_mmd_anim_cli_version  # noqa: E402
from noxlib.common import (  # noqa: E402
    _resolve_existing_or_repo_path as _common_resolve_existing_or_repo_path,
)
from noxlib.maya import (  # noqa: E402
    _convert_mayapy_path_options as _common_convert_mayapy_path_options,
    _mayapy_arg_path as _common_mayapy_arg_path,
    _mayapy_env as _common_mayapy_env,
    _mayapy_script as _common_mayapy_script,
)
from noxlib.native import (  # noqa: E402
    _cmake_build as _common_cmake_build,
    _cmake_configure as _common_cmake_configure,
    _cpp_build_dir as _common_cpp_build_dir,
    _cpp_smoke_exe as _common_cpp_smoke_exe,
    _find_vsdevcmd as _common_find_vsdevcmd,
    _is_expected_environment_import_failure as _common_is_expected_environment_import_failure,
    _maya_devkit_root as _common_maya_devkit_root,
    _run_cli_smoke as _common_run_cli_smoke,
    _run_in_vs_dev_cmd as _common_run_in_vs_dev_cmd,
    _vswhere_path as _common_vswhere_path,
)
from noxlib.release import (  # noqa: E402
    _new_release_gate_run as _common_new_release_gate_run,
    _release_gate_failure_label as _common_release_gate_failure_label,
    _release_gate_mmd_anim_pin_check as _common_release_gate_mmd_anim_pin_check,
    _release_gate_version_check as _common_release_gate_version_check,
    _normalize_local_gate_report as _common_normalize_local_gate_report,
    _run_release_gate_callable as _common_run_release_gate_callable,
    _run_release_gate_command as _common_run_release_gate_command,
    _write_release_gate_reports as _common_write_release_gate_reports,
)
from noxlib.release_matrix import (  # noqa: E402
    tier0_commands as _release_gate_tier0_commands,
    tier1_commands as _release_gate_tier1_commands,
    tier2_commands as _release_gate_tier2_commands,
    tier3_commands as _release_gate_tier3_commands,
)
from noxlib.release_sessions import run_native_physics_release_gate as _run_native_physics_release_gate  # noqa: E402
from noxlib.sessions import (  # noqa: E402
    run_control_rig_vmd_roundtrip as _run_control_rig_vmd_roundtrip,
    run_ci_unit as _run_ci_unit,
    run_gui_tests as _run_gui_tests,
    run_release_package as _run_release_package,
    run_release_version as _run_release_version,
    run_python_module as _run_python_module,
    run_tests as _run_tests,
)
from noxlib.native_sessions import (  # noqa: E402
    run_bundled_native_smoke as _run_bundled_native_smoke,
    run_cpp_build as _run_cpp_build,
    run_cpp_config as _run_cpp_config,
    run_ffi_build as _run_ffi_build,
    run_maya_smoke as _run_maya_smoke,
    run_native_export_smoke as _run_native_export_smoke,
    run_native_smoke as _run_native_smoke,
    run_reduction_abi_probe as _run_reduction_abi_probe,
)
from noxlib.maya_sessions import (  # noqa: E402
    run_cpp_plugin_smoke as _run_cpp_plugin_smoke,
    run_humanik_citlali_stance_smoke as _run_humanik_citlali_stance_smoke,
    run_humanik_definition_smoke as _run_humanik_definition_smoke,
    run_humanik_retarget_smoke as _run_humanik_retarget_smoke,
    run_humanik_roundtrip_smoke as _run_humanik_roundtrip_smoke,
    run_humanik_vmd_import_gate_smoke as _run_humanik_vmd_import_gate_smoke,
    run_humanik_vmd_parity_smoke as _run_humanik_vmd_parity_smoke,
    run_model_readme_dialog_e2e as _run_model_readme_dialog_e2e,
    run_native_physics_bake as _run_native_physics_bake,
    run_physics_solver_cycle_probe as _run_physics_solver_cycle_probe,
    run_root_move_ik_target_probe as _run_root_move_ik_target_probe,
    run_root_move_skin_parity_probe as _run_root_move_skin_parity_probe,
    run_shader_override_smoke as _run_shader_override_smoke,
    run_shader_visual_semantic_gate as _run_shader_visual_semantic_gate,
    run_static_render as _run_static_render,
    run_visual_regression as _run_visual_regression,
    run_viewport_capture as _run_viewport_capture,
    run_yw_test_model_fixture_gate as _run_yw_test_model_fixture_gate,
)
from tests.common.maya_location import mayapy as _mayapy  # noqa: E402
from tests.common.maya_location import path_for_maya_process as _maya_process_path  # noqa: E402
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
DEFAULT_RELEASE_VISUAL_PORTS = {
    "2025": "7825",
    "2026": "7826",
}
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


def _native_runtime_smoke_code() -> str:
    """Return Python code that verifies the runtime ABI and required features."""
    return (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "MMD_RUNTIME_ABI_VERSION_CURRENT, get_mmd_runtime_library, get_runtime_library_path; "
        "required = "
        f"{MMD_RUNTIME_REQUIRED_PHYSICS_FEATURE_FLAGS}; "
        "lib = get_mmd_runtime_library(); "
        "path = get_runtime_library_path(); "
        "flags = lib.mmd_runtime_feature_flags() if lib and hasattr(lib, 'mmd_runtime_feature_flags') else 0; "
        "abi = lib.mmd_runtime_abi_version() if lib else 0; "
        "print(path); "
        "print({'abi': abi, 'expectedAbi': MMD_RUNTIME_ABI_VERSION_CURRENT, "
        "'featureFlags': hex(flags), 'requiredFeatureFlags': hex(required)}); "
        "raise SystemExit(0 if lib and abi == MMD_RUNTIME_ABI_VERSION_CURRENT "
        "and (flags & required) == required else 1)"
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
    return _common_resolve_existing_or_repo_path(value, ROOT)


def _mmd_anim_cli_version(exe: Path) -> str:
    """Return the first non-empty line of the mmd-anim CLI version output."""
    return _common_mmd_anim_cli_version(exe, ROOT)


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
    return _common_mayapy_env(mayapy, ROOT, preserve_pythonpath=preserve_pythonpath, **extra)


def _mayapy_script(mayapy: Path, relative_script: str) -> str:
    """Return an absolute script path suitable for the resolved mayapy."""
    return _common_mayapy_script(mayapy, ROOT, relative_script)


def _mayapy_arg_path(mayapy: Path, value: str | Path) -> str:
    """Return a path argument suitable for the resolved mayapy."""
    return _common_mayapy_arg_path(mayapy, ROOT, value)


def _convert_mayapy_path_options(mayapy: Path, args: list[str], path_options: set[str]) -> list[str]:
    """Convert values following path-like options for a mayapy child process."""
    return _common_convert_mayapy_path_options(mayapy, ROOT, args, path_options)


def _probe_passthrough(
    args: list[str],
    value_options: set[str],
    flag_options: set[str] | None = None,
) -> list[str]:
    """Keep only options accepted by a probe script, excluding Nox's ``--maya``."""
    flags = flag_options or set()
    passthrough: list[str] = []
    index = 0
    while index < len(args):
        option = args[index]
        if option == "--maya" and index + 1 < len(args):
            index += 2
        elif option in value_options and index + 1 < len(args):
            passthrough.extend((option, args[index + 1]))
            index += 2
        elif option in flags:
            passthrough.append(option)
            index += 1
        else:
            index += 1
    return passthrough


def _clear_probe_report(session: nox.Session, report_path: Path, label: str) -> None:
    """Remove a stale probe report or fail with a contextual error."""
    try:
        report_path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        session.error(f"Unable to clear stale {label} report {report_path}: {exc}")


def _read_probe_report(session: nox.Session, report_path: Path, label: str) -> dict[str, object]:
    """Load a required JSON object emitted by a probe."""
    if not report_path.is_file():
        session.error(f"{label} report missing: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        session.error(f"Invalid {label} report {report_path}: {exc}")
    if not isinstance(report, dict):
        session.error(f"Invalid {label} report root {report_path}: expected object")
    return report


def _run_mayapy_probe(
    session: nox.Session,
    mayapy: Path,
    script: str,
    args: list[str],
    path_options: set[str],
    *,
    utf8: bool = False,
    success_codes: tuple[int, ...] | None = None,
) -> None:
    """Run a viewport probe with the repository's standard mayapy environment."""
    extra_env = {"MAYA_SKIP_USERSETUP_PY": "1"}
    if utf8:
        extra_env["PYTHONIOENCODING"] = "utf-8"
    run_options: dict[str, object] = {
        "env": _mayapy_env(mayapy, **extra_env),
        "external": True,
    }
    if success_codes is not None:
        run_options["success_codes"] = success_codes
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, script),
        *_convert_mayapy_path_options(mayapy, args, path_options),
        **run_options,
    )


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
    return _common_release_gate_version_check(ROOT, expected_version=expected_version)


def _release_gate_mmd_anim_pin_check(root: Path | None = None) -> None:
    """Require the checked-out mmd-anim HEAD to match the parent gitlink."""
    return _common_release_gate_mmd_anim_pin_check(
        ROOT if root is None else root,
        run_process=subprocess.run,
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
    return _common_run_release_gate_command(
        name,
        command,
        results,
        root=ROOT,
        run_logged_subprocess=_run_logged_subprocess,
        safe_log_name=_safe_log_name,
        compact_failure_details_from_log=_compact_failure_details_from_log,
        format_test_summary=_format_test_summary,
        result_report=result_report,
        required_local=required_local,
        strict_local=strict_local,
        verbose=verbose,
    )


def _run_release_gate_callable(
    name: str,
    func,
    results: list[dict[str, object]],
) -> None:
    """Run an in-process release-gate step and append a keep-going result entry."""
    return _common_run_release_gate_callable(
        name,
        func,
        results,
        format_test_summary=_format_test_summary,
    )


def _release_gate_failure_label(result: dict[str, object]) -> str:
    """Return the best available compact failure detail for an aggregate gate."""
    return _common_release_gate_failure_label(result)


def _write_release_gate_reports(
    results: list[dict[str, object]],
    quick: bool,
    *,
    run_id: str | None = None,
    timestamp: str | None = None,
) -> tuple[Path, Path]:
    """Write release-gate Markdown and JSON summaries."""
    if run_id is None and timestamp is None:
        return _common_write_release_gate_reports(ROOT, results, quick)
    return _common_write_release_gate_reports(
        ROOT,
        results,
        quick,
        run_id=run_id,
        timestamp=timestamp,
    )


def _normalize_local_gate_report(
    report_path: Path,
    strict_local: bool,
    markdown_path: Path | None = None,
) -> str:
    """Derive and persist a local child gate status in its JSON and Markdown reports."""
    return _common_normalize_local_gate_report(report_path, strict_local, markdown_path)


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
    return _common_maya_devkit_root(version)


def _cpp_build_dir(version: str) -> Path:
    """Return the CMake build directory for a Maya version."""
    return _common_cpp_build_dir(ROOT, version)


def _vswhere_path() -> Path:
    """Return the default vswhere path."""
    return _common_vswhere_path()


def _find_vsdevcmd() -> Path | None:
    """Find VsDevCmd.bat for Windows C++ builds."""
    return _common_find_vsdevcmd()


def _run_in_vs_dev_cmd(session: nox.Session, command: list[str]) -> None:
    """Run a Windows command after initializing Visual Studio C++ tools."""
    return _common_run_in_vs_dev_cmd(session, ROOT, command)


def _cmake_configure(session: nox.Session, version: str, config: str = DEFAULT_CMAKE_CONFIG) -> None:
    """Configure the Maya C++ plugin build."""
    return _common_cmake_configure(session, ROOT, version, config)


def _cmake_build(
    session: nox.Session,
    version: str,
    config: str,
    *,
    clean_first: bool = False,
) -> None:
    """Build the Maya C++ plugin, optionally forcing fresh tracked artifacts."""
    return _common_cmake_build(session, ROOT, version, config, clean_first=clean_first)


def _cpp_smoke_exe(version: str, config: str) -> Path:
    """Return path to the standalone mmd_runtime_smoke exe produced by cpp build."""
    return _common_cpp_smoke_exe(ROOT, version, config)


def _run_cli_smoke(
    session: nox.Session,
    version: str,
    config: str,
    manifest: str,
    case: str = "",
    limit: str = "",
) -> None:
    """Run the CLI smoke exe (if manifest provided). Used by cpp_cli_smoke and conditionally by cpp_verify."""
    return _common_run_cli_smoke(session, ROOT, version, config, manifest, case, limit)


def _is_expected_environment_import_failure(stderr: str) -> bool:
    """Return whether the final exception is an allowlisted missing environment module."""
    return _common_is_expected_environment_import_failure(stderr)


@nox.session(venv_backend="none")
def ci_unit(session: nox.Session) -> None:
    """Run pure-python unit tests without mayapy.

    Dynamically discovers tests/unit/test_*.py files that can be imported
    without Maya, so any new tests added to tests/unit are automatically
    included — no manual listing required.

    A test file is included when it can be imported successfully in a
    pytest-enabled ``uvx`` probe (i.e. it has no transitive dependency on an
    allowlisted environment-only module). Files that fail for one of those
    expected dependencies are skipped with a notice; other import failures
    abort the session.

    Examples:
        uvx nox -s ci_unit
    """
    _run_ci_unit(
        session,
        root=ROOT,
        run_process=subprocess.run,
        glob_files=Path.glob,
        is_expected_environment_import_failure=_is_expected_environment_import_failure,
        run_logged_subprocess=_run_logged_subprocess,
    )


@nox.session(venv_backend="none")
def release_version(session: nox.Session) -> None:
    """Validate all release version markers, optionally against a tag version."""
    _run_release_version(session, option=_option, version_check=_release_gate_version_check)


@nox.session(venv_backend="none")
def tests(session: nox.Session) -> None:
    """Run existing mayapy-backed unit/integration tests.

    Examples:
        uvx nox -s tests
        uvx nox -s tests -- --type integration --test test_maya_utils
    """
    _run_tests(session, posargs=session.posargs, python_executable=sys.executable)


@nox.session(venv_backend="none")
def mmd_control_rig_vmd_roundtrip_smoke(session: nox.Session) -> None:
    """Run the focused MMD control-rig import/edit/bake/VMD round-trip gate."""
    _run_control_rig_vmd_roundtrip(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        python_executable=sys.executable,
    )


@nox.session(venv_backend="none")
def mmd_control_rig_vmd_import_parity_matrix(session: nox.Session) -> None:
    """Run the Maya Control Rig VMD-import parity matrix host runner.

    The host runner owns the Maya-version/evaluation-mode matrix and invokes
    each configured ``mayapy`` process itself.  Nox only supplies the current
    Python interpreter and forwards every positional argument unchanged, so
    environment overrides such as ``MMD_TOOLS_CPP_PLUGIN_2026`` remain intact.

    Examples:
        uvx nox -s mmd_control_rig_vmd_import_parity_matrix
        uvx nox -s mmd_control_rig_vmd_import_parity_matrix -- --versions 2024 --modes dg
        uvx nox -s mmd_control_rig_vmd_import_parity_matrix -- --cases coverage --out build/reports/cr-matrix.json --timeout 600
    """
    _run_python_module(
        session,
        module="tests.viewport.mmd_control_rig_vmd_import_parity_matrix",
        posargs=session.posargs,
        python_executable=sys.executable,
        environment=dict(os.environ),
    )


@nox.session(venv_backend="none")
def real_asset_bake_rig_parity(session: nox.Session) -> None:
    """Run the fail-closed five-pair Maya 2024/2026 Control Rig parity matrix.

    Examples::

        uvx nox -s real_asset_bake_rig_parity -- --dry-run
        uvx nox -s real_asset_bake_rig_parity -- --manifest F:/MMD/parity-manifest.json
        uvx nox -s real_asset_bake_rig_parity -- --manifest F:/MMD/parity-manifest.json --resume
    """

    _run_python_module(
        session,
        module="tests.viewport.real_asset_bake_rig_parity",
        posargs=session.posargs,
        python_executable=sys.executable,
        environment=dict(os.environ),
    )


@nox.session(venv_backend="none")
def mmd_control_rig_gui_e2e(session: nox.Session) -> None:
    """Run GUI control-rig E2E followed by the mandatory mesh oracle gate.

    Example::

        uvx nox -s mmd_control_rig_gui_e2e -- --maya 2024
    """

    args = list(session.posargs) or ["--maya", DEFAULT_MAYA_VERSION]
    maya_version = _option(args, "--maya", DEFAULT_MAYA_VERSION)
    out_dir = _require_build_path(
        session,
        _option(args, "--out-dir", "build/e2e"),
        "--out-dir",
    )
    model = _option(args, "--model", "tests/data/mmt_test_model.pmx")
    evaluation_mode = _option(args, "--evaluation-mode", "default")
    mode_suffix = "" if evaluation_mode == "default" else f"_{evaluation_mode}"
    route_suffix = "_create_on_import" if "--create-on-import" in args else ""
    output_suffix = f"{mode_suffix}{route_suffix}"
    gui_report = out_dir / f"mmd_control_rig_e2e_maya{maya_version}{output_suffix}.json"
    exported_vmd = out_dir / f"mmd_control_rig_e2e_maya{maya_version}{output_suffix}.vmd"
    session.run(
        sys.executable,
        str(ROOT / "tests" / "viewport" / "e2e_mmd_control_rig.py"),
        *args,
        external=True,
    )
    gui_report_data = _read_probe_report(session, gui_report, "MMD control-rig GUI E2E")
    if gui_report_data.get("status") != "pass":
        session.error(f"Maya GUI control-rig E2E did not pass: {gui_report_data}")
    if not exported_vmd.is_file() or exported_vmd.stat().st_size == 0:
        session.error(f"GUI E2E did not produce a canonical exported VMD: {exported_vmd}")

    mayapy = _mayapy(maya_version)
    if not mayapy.exists():
        session.error(f"mayapy not found for Maya {maya_version}: {mayapy}")
    ffi_path = (ROOT / "external" / "mmd-anim" / "target" / "release").resolve()
    if not ffi_path.is_dir():
        session.error(
            "mmd-anim FFI release directory is required for the external oracle: "
            f"{ffi_path}"
        )
    oracle_report = out_dir / f"mmd_anim_mesh_oracle_compare_maya{maya_version}{output_suffix}.json"
    _clear_probe_report(session, oracle_report, "mmd-anim mesh oracle")
    oracle_args = [
        "--pmx",
        _mayapy_arg_path(mayapy, model),
        "--vmd",
        _mayapy_arg_path(mayapy, exported_vmd),
        "--out",
        _mayapy_arg_path(mayapy, oracle_report),
        "--mode",
        "rig",
        "--bind-source",
        "pmx",
        "--threshold",
        "0.01",
    ]
    for frame in range(6):
        oracle_args.extend(("--frame", str(frame)))
    oracle_env = _mayapy_env(
        mayapy,
        MAYA_VERSION=maya_version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_CPP_PLUGIN=_mayapy_arg_path(
            mayapy,
            ROOT / "plug-ins" / maya_version / "Debug" / "mmd_tools_cpp.mll",
        ),
        MMD_ANIM_FFI_PATH=str(ffi_path),
    )
    session.run(
        str(mayapy),
        _mayapy_script(mayapy, "tests/viewport/mmd_anim_mesh_oracle_compare.py"),
        *oracle_args,
        env=oracle_env,
        external=True,
        success_codes=(0, 1, 2),
    )
    external_report = _read_probe_report(session, oracle_report, "mmd-anim mesh oracle")
    external_pass = external_report.get("status") == "passed"
    gui_report_data["externalOracle"] = {
        "identity": "mmd_anim_mesh_oracle_compare_rig_pmx_bind",
        "status": "pass" if external_pass else "fail",
        "report": str(oracle_report),
        "threshold": 0.01,
        "frames": list(range(6)),
        "comparison": external_report.get("comparison"),
    }
    gui_report.write_text(
        json.dumps(gui_report_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not external_pass:
        session.error(f"External mmd-anim mesh oracle failed: {external_report}")


@nox.session(venv_backend="none")
def gui_tests(session: nox.Session) -> None:
    """Run existing Maya GUI tests."""
    _run_gui_tests(
        session,
        posargs=session.posargs,
        python_executable=sys.executable,
        default_maya_version=DEFAULT_MAYA_VERSION,
    )


@nox.session(venv_backend="none")
def ffi_build(session: nox.Session) -> None:
    """Build the mmd-anim FFI library used by Python and C++ integrations.

    Examples:
        uvx nox -s ffi_build
        uvx nox -s ffi_build -- --release --cargo-target-dir build/mmd-anim-unlocked-target
    """
    _run_ffi_build(
        session,
        posargs=session.posargs,
        root=ROOT,
        option=_option,
        without_option=_without_option,
        cargo_args_with_physics_feature=_cargo_args_with_physics_feature,
        require_build_path=_require_build_path,
        windows_processes_locking_module=_windows_processes_locking_module,
        configure_bullet3_dir=_configure_bullet3_dir,
        platform_name=platform.system(),
    )


@nox.session(venv_backend="none")
def native_smoke(session: nox.Session) -> None:
    """Verify that Python can load mmd-anim-ffi and read its ABI version.

    Examples:
        uvx nox -s native_smoke
        uvx nox -s native_smoke -- --ffi-path build/mmd-anim-unlocked-target/release
    """
    _run_native_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
        runtime_smoke_code=_native_runtime_smoke_code(),
    )


@nox.session(venv_backend="none")
def reduction_abi_probe(session: nox.Session) -> None:
    """Probe mmd-anim dense-pose reduction and its Maya bake boundary.

    Examples:
        uvx nox -s reduction_abi_probe
        uvx nox -s reduction_abi_probe -- --ffi-path build/mmd-anim-unlocked-target/release
    """
    _run_reduction_abi_probe(
        session,
        posargs=session.posargs,
        option=_option,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
        require_build_path=_require_build_path,
    )


@nox.session(venv_backend="none")
def bundled_native_smoke(session: nox.Session) -> None:
    """Verify only the native binaries bundled in release distribution paths."""
    _run_bundled_native_smoke(
        session,
        posargs=session.posargs,
        root=ROOT,
        option=_option,
        require_build_path=_require_build_path,
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
    _run_native_export_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        without_option=_without_option,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
    )


@nox.session(venv_backend="none")
def release_package(session: nox.Session) -> None:
    """Build and fail-closed validate the release ZIP from the package manifest.

    Examples:
        uvx nox -s release_package
        uvx nox -s release_package -- --version 0.3.1
        uvx nox -s release_package -- --out-dir dist
    """
    _run_release_package(
        session,
        posargs=session.posargs,
        root=ROOT,
        package_manifest_path=_PACKAGE_MANIFEST_PATH,
        option=_option,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
        build_release_package=_build_release_package,
    )


@nox.session(venv_backend="none")
def cpp_config(session: nox.Session) -> None:
    """Configure the Maya C++ plugin build."""
    _run_cpp_config(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        configure=_cmake_configure,
    )


@nox.session(venv_backend="none")
def cpp_build(session: nox.Session) -> None:
    """Configure and build the Maya C++ plugin."""
    _run_cpp_build(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        configure=_cmake_configure,
        build=_cmake_build,
    )


@nox.session(venv_backend="none")
def maya_smoke(session: nox.Session) -> None:
    """Load the C++ plugin in mayapy and create the runtime node."""
    _run_maya_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_script=_mayapy_script,
    )


@nox.session(venv_backend="none")
def ccdik_dirty_smoke(session: nox.Session) -> None:
    """Run the focused mmdCcdIk goal-child dirty propagation regression."""
    _run_cpp_plugin_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        scripts=("tests/cpp/focused_ccdik_goal_dirty.py",),
        require_plugin=False,
    )


@nox.session(venv_backend="none")
def ccdik_cache_smoke(session: nox.Session) -> None:
    """Run the focused mmdCcdIk cache/output-coherence regression."""
    _run_cpp_plugin_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        scripts=("tests/cpp/focused_ccdik_cache.py",),
        require_plugin=True,
    )


@nox.session(venv_backend="none")
def fast_load_normals_smoke(session: nox.Session) -> None:
    """Verify authored normals in mmdFastLoad and its skinned import path."""
    _run_cpp_plugin_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        scripts=(
            "tests/cpp/focused_fast_load_normals.py",
            "tests/cpp/focused_fast_importer_skin.py",
        ),
        require_plugin=True,
    )


@nox.session(venv_backend="none")
def uv_weld_smoke(session: nox.Session) -> None:
    """Verify the Python-callable C++ UV seam topology normalization command."""
    _run_cpp_plugin_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        scripts=("tests/cpp/focused_uv_weld.py",),
        require_plugin=True,
    )


@nox.session(venv_backend="none")
def ccdik_ancestor_residual_smoke(session: nox.Session) -> None:
    """Run the deterministic rotated-ancestor CCD residual probe."""
    _run_cpp_plugin_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        default_config=DEFAULT_CMAKE_CONFIG,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        scripts=("tests/cpp/focused_ccdik_ancestor_residual.py",),
        require_plugin=True,
    )


@nox.session(venv_backend="none")
def yw_test_model_fixture_gate(session: nox.Session) -> None:
    """Run the checked-in YW test-model gate under Maya 2024 and 2026.

    The default matrix is intentionally both supported Maya versions.  Pass
    ``--maya 2024`` (or ``--maya 2026``) for a focused local rerun.  Reports
    and reopened Maya ASCII scenes are written below ``build/``.

    Examples:
        uvx nox -s yw_test_model_fixture_gate
        uvx nox -s yw_test_model_fixture_gate -- --maya 2024
        uvx nox -s yw_test_model_fixture_gate -- --out-dir build/yw-test-model-fixture
    """
    _run_yw_test_model_fixture_gate(
        session,
        posargs=session.posargs,
        options=_options,
        option=_option,
        default_maya_versions=("2024", "2026"),
        root=ROOT,
        require_build_path=_require_build_path,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
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
    _run_viewport_capture(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
    )


@nox.session(venv_backend="none")
def model_readme_dialog_e2e(session: nox.Session) -> None:
    """Run the real Maya GUI model-readme modal gate for Maya 2024/2026."""
    _run_model_readme_dialog_e2e(
        session,
        posargs=session.posargs,
        options=_options,
        option=_option,
        root=ROOT,
        require_build_path=_require_build_path,
        python_executable=sys.executable,
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
    _run_native_physics_bake(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        verify_bake_route=False,
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
    _run_native_physics_bake(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        resolve_existing_or_repo_path=_resolve_existing_or_repo_path,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
        verify_bake_route=True,
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
    _run_native_physics_release_gate(
        session,
        root=ROOT,
        bundled_physics_runtime=_bundled_physics_runtime,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_script=_mayapy_script,
        maya_process_path=_maya_process_path,
        python_executable=sys.executable,
    )


@nox.session(venv_backend="none")
def humanik_definition_smoke(session: nox.Session) -> None:
    """Create a minimal HumanIK definition under mayapy.

    Examples:
        uvx nox -s humanik_definition_smoke -- --maya 2024
        uvx nox -s humanik_definition_smoke -- --maya 2024 --out build/reports/humanik_definition_smoke.json
    """
    _run_humanik_definition_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        probe_passthrough=_probe_passthrough,
        convert_mayapy_path_options=_convert_mayapy_path_options,
        mayapy_script=_mayapy_script,
        mayapy_env=_mayapy_env,
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
    _run_humanik_retarget_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        probe_passthrough=_probe_passthrough,
        run_mayapy_probe=_run_mayapy_probe,
    )


@nox.session(venv_backend="none")
def humanik_roundtrip_smoke(session: nox.Session) -> None:
    """Run the S5 self-retarget gate in isolated Maya evaluation modes."""
    _run_humanik_roundtrip_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        root=ROOT,
        probe_passthrough=_probe_passthrough,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
    )


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
    _run_humanik_vmd_parity_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        root=ROOT,
        probe_passthrough=_probe_passthrough,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
    )


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
    _run_humanik_vmd_import_gate_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        root=ROOT,
        probe_passthrough=_probe_passthrough,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
        read_probe_report=_read_probe_report,
    )


@nox.session(venv_backend="none")
def humanik_citlali_stance_smoke(session: nox.Session) -> None:
    """Run the strict Citlali HumanIK setup/restore regression gate.

    The gate imports the ASCII-path Citlali fixture, characterizes it through
    the frontend, and verifies rotate, jointOrient, skin-product, and exact
    writer-topology restoration evidence without changing the source PMX.
    """
    _run_humanik_citlali_stance_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        mayapy=_mayapy,
        root=ROOT,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
        read_probe_report=_read_probe_report,
    )


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
    _run_physics_solver_cycle_probe(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        mayapy=_mayapy,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
        read_probe_report=_read_probe_report,
    )


@nox.session(venv_backend="none")
def root_move_skin_parity_probe(session: nox.Session) -> None:
    """Measure Citlali root-motion, skin products, and world-space mesh parity.

    The mayapy probe performs one production import, applies a known non-zero
    root translation, records major joints/skinClusters/mesh vertices, then
    saves and reopens the moved scene for parity evidence.  It never zeroes or
    bakes the root and does not modify source code or the PMX fixture.

    Example:
        uvx nox -s root_move_skin_parity_probe -- --maya 2024
    """
    _run_root_move_skin_parity_probe(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        mayapy=_mayapy,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
        read_probe_report=_read_probe_report,
    )


@nox.session(venv_backend="none")
def root_move_ik_target_probe(session: nox.Session) -> None:
    """Diagnose Citlali foot/IK-target drift after a non-zero root move.

    This report-only mayapy probe captures foot joints, mmdCcdIk goal wiring,
    inferred controllers/targets, native ikHandles, and their parent
    ``inheritsTransform`` state before and after moving the imported root.
    It intentionally does not alter production source or reset the root.

    Example:
        uvx nox -s root_move_ik_target_probe -- --maya 2024
    """
    _run_root_move_ik_target_probe(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        mayapy=_mayapy,
        clear_probe_report=_clear_probe_report,
        run_mayapy_probe=_run_mayapy_probe,
        read_probe_report=_read_probe_report,
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
    _run_shader_override_smoke(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
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
    _run_static_render(
        session,
        posargs=session.posargs,
        option=_option,
        has_flag=_has_flag,
        default_maya_version=DEFAULT_MAYA_VERSION,
        root=ROOT,
        require_build_path=_require_build_path,
        mayapy=_mayapy,
        mayapy_env=_mayapy_env,
        mayapy_arg_path=_mayapy_arg_path,
        mayapy_script=_mayapy_script,
    )


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
    _run_visual_regression(
        session,
        posargs=session.posargs,
        option=_option,
        options=_options,
        has_flag=_has_flag,
        default_maya_version=DEFAULT_MAYA_VERSION,
        require_build_path=_require_build_path,
        python_executable=sys.executable,
    )


@nox.session(venv_backend="none")
def shader_visual_semantic_gate(session: nox.Session) -> None:
    """Guard DX11 outline-color leakage and disappearing hair geometry."""
    _run_shader_visual_semantic_gate(
        session,
        posargs=session.posargs,
        option=_option,
        default_maya_version=DEFAULT_MAYA_VERSION,
        require_build_path=_require_build_path,
        python_executable=sys.executable,
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
    _cmake_build(session, version, config, clean_first=True)

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
    run_id, run_timestamp = _common_new_release_gate_run()
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
            md_path, json_path = _write_release_gate_reports(
                results,
                quick,
                run_id=run_id,
                timestamp=run_timestamp,
            )
            session.log(f"Release gate report: {md_path}")
            session.log(f"Release gate JSON: {json_path}")
            session.error(
                "Release gate preflight failed: "
                f"{_release_gate_failure_label(results[-1])}"
            )

    tier0_commands = _release_gate_tier0_commands()
    for name, command in tier0_commands:
        _run_release_gate_command(name, command, results, verbose=verbose)
    _run_release_gate_callable("tier0:version-markers", _release_gate_version_check, results)

    tier1_commands = _release_gate_tier1_commands(
        quick=quick,
        ffi_cargo_target_dir=ffi_cargo_target_dir,
        ffi_path=ffi_path,
    )
    for name, command in tier1_commands:
        _run_release_gate_command(name, command, results, verbose=verbose)

    if not quick:
        if not visual_manifest.is_file():
            _run_release_gate_callable(
                "tier2:generated-pmx-visual-manifest",
                lambda: (_ for _ in ()).throw(FileNotFoundError(
                    f"GoldenOracle render manifest not found: {visual_manifest}. "
                    "Pass --visual-manifest or set GOLDEN_ORACLE_RENDER_MANIFEST."
                )),
                results,
            )
        tier2_commands = _release_gate_tier2_commands(
            version=version,
            cpp_versions=cpp_versions,
            cpp_config=cpp_config,
            release_maya_versions=DEFAULT_RELEASE_MAYA_VERSIONS,
            viewport_matrix=DEFAULT_RELEASE_VIEWPORT_MATRIX,
            visual_manifest=visual_manifest,
            visual_ports=DEFAULT_RELEASE_VISUAL_PORTS,
            visual_cases=_release_visual_cases,
            include_cpp=_has_flag(args, "--with-cpp"),
            verbose=verbose,
        )
        for name, command in tier2_commands:
            _run_release_gate_command(name, command, results, verbose=verbose)

        tier3_commands = _release_gate_tier3_commands(
            root=ROOT,
            version=version,
            local_assets_manifest=local_assets_manifest,
            camera_manifest=camera_manifest,
            local_parity_manifest=local_parity_manifest,
            strict_local=strict_local,
        )
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

    md_path, json_path = _write_release_gate_reports(
        results,
        quick,
        run_id=run_id,
        timestamp=run_timestamp,
    )
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
