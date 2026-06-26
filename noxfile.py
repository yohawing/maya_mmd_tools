"""Cross-platform development task runner for maya_mmd_tools.

Nox is used as a thin orchestration layer around existing project tools:
Maya tests still run through mayapy, C++ builds still run through CMake, and
mmd-anim still builds through Cargo. Sessions use the current Python process
instead of creating a separate virtual environment.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import nox


ROOT = Path(__file__).resolve().parent
DEFAULT_MAYA_VERSION = "2024"
DEFAULT_CMAKE_CONFIG = "Debug"

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


def _maya_location(version: str) -> Path:
    """Return Maya installation root for the current platform."""
    version_env = os.environ.get(f"MAYA_LOCATION_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_LOCATION")
    if common_env:
        return Path(common_env)

    system = platform.system()
    if system == "Windows":
        return Path(f"C:/Program Files/Autodesk/Maya{version}")
    if system == "Darwin":
        return Path(f"/Applications/Autodesk/maya{version}/Maya.app/Contents")

    return Path(f"/usr/autodesk/maya{version}")


def _maya_devkit_root(version: str) -> Path:
    """Return the Maya devkit root, allowing environment overrides."""
    version_env = os.environ.get(f"MAYA_DEVKIT_ROOT_{version}")
    if version_env:
        return Path(version_env)

    common_env = os.environ.get("MAYA_DEVKIT_ROOT")
    if common_env:
        return Path(common_env)

    return _maya_location(version) / "devkit"


def _mayapy(version: str) -> Path:
    """Return mayapy executable path for the current platform."""
    executable = _maya_location(version) / "bin" / "mayapy"
    if platform.system() == "Windows":
        executable = executable.with_suffix(".exe")
    return executable


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
    """Build the mmd-anim FFI library used by Python and C++ integrations."""
    args = session.posargs or ["--release"]
    session.run(
        "cargo",
        "build",
        "-p",
        "mmd-anim-ffi",
        "--manifest-path",
        "external/mmd-anim/Cargo.toml",
        *args,
        external=True,
    )


@nox.session(venv_backend="none")
def native_smoke(session: nox.Session) -> None:
    """Verify that Python can load mmd-anim-ffi and read its ABI version."""
    code = (
        "from mmd_tools.core.native.mmd_anim_runtime import "
        "get_mmd_runtime_library, get_runtime_library_path; "
        "lib = get_mmd_runtime_library(); "
        "print(get_runtime_library_path()); "
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 1 else 1)"
    )
    session.run(sys.executable, "-c", code, external=True)


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

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "MMD_TOOLS_CPP_CONFIG": config,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/cpp/smoke_runtime_node.py",
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
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = (ROOT / out_path).resolve()
    frame = _option(session.posargs, "--frame", "1")
    width = _option(session.posargs, "--width", "640")
    height = _option(session.posargs, "--height", "480")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "PYTHONPATH": str(ROOT),
        # Intentionally no MMD_TOOLS_CPP_* or plugin env; this smoke is plugin-free.
    }
    session.run(
        str(mayapy),
        "tests/viewport/smoke_viewport_capture.py",
        "--out",
        str(out_path),
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
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = (ROOT / out_path).resolve()
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
    diagnostics_args: list[str] = []
    if diagnostics_out:
        diagnostics_path = _require_build_path(session, diagnostics_out, "--diagnostics-out")
        diagnostics_args.extend(["--diagnostics-out", str(diagnostics_path)])
    if _has_flag(session.posargs, "--allow-blank"):
        diagnostics_args.append("--allow-blank")

    mayapy = _mayapy(version)
    if not mayapy.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy}")

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "PYTHONPATH": str(ROOT),
    }
    vp2_device_map = {
        "gl": "VirtualDeviceGL",
        "glcore": "VirtualDeviceGLCore",
        "dx11": "VirtualDeviceDx11",
    }
    if vp2_device in vp2_device_map:
        env["MAYA_VP2_DEVICE_OVERRIDE"] = vp2_device_map[vp2_device]

    cmd = [
        str(mayapy),
        "tests/viewport/static_render_capture.py",
        shader_flag,
        "--out",
        str(out_path),
        "--model",
        model,
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

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/track6/track6_runner.py",
        *runner_args,
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

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/roundtrip/pmx_roundtrip_runner.py",
        *runner_args,
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
        "raise SystemExit(0 if lib and lib.mmd_runtime_abi_version() == 1 else 1)"
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

    env = {
        **os.environ,
        "MAYA_VERSION": version,
        "MMD_TOOLS_CPP_CONFIG": config,
        "PYTHONPATH": str(ROOT),
    }
    session.run(
        str(mayapy),
        "tests/cpp/smoke_runtime_node.py",
        env=env,
        external=True,
    )


@nox.session(venv_backend="none")
def golden_oracle(session: nox.Session) -> None:
    """Verify mmd-anim runtime against GoldenOracle numeric manifest.

    Runs ``mmd-anim verify <manifest> --mode numeric`` which compares the
    committed oracle JSONL against a fresh mmd-anim runtime evaluation.
    Any regression beyond the manifest epsilon causes session failure.

    Examples:
        uvx nox -s golden_oracle
        uvx nox -s golden_oracle -- --manifest tests/golden-oracle/manifest.json
    """
    manifest = _option(
        session.posargs, "--manifest",
        str(ROOT / "tests/golden-oracle/manifest.json"),
    )
    mmd_anim = ROOT / "external" / "mmd-anim" / "target" / "release" / "mmd-anim"
    if platform.system() == "Windows":
        mmd_anim = mmd_anim.with_suffix(".exe")

    if not mmd_anim.exists():
        session.log("mmd-anim release binary not found; building via cargo...")
        session.run(
            "cargo", "build", "-p", "mmd-anim-cli",
            "--manifest-path", "external/mmd-anim/Cargo.toml",
            "--release", external=True,
        )

    session.run(str(mmd_anim), "verify", manifest, "--mode", "numeric", external=True)


@nox.session(venv_backend="none")
def local_parity(session: nox.Session) -> None:
    """Run Bake-vs-Rig mesh parity on local (non-committed) PMX/VMD assets.

    Non-ASCII asset paths are transparently aliased via Windows junctions
    so that mayapy batch mode can store them in Maya string attributes
    without codepage corruption.

    Examples:
        uvx nox -s local_parity -- --maya 2024
        uvx nox -s local_parity -- --maya 2024 --case alicia_weekender
        uvx nox -s local_parity -- --maya 2024 --skip-fbx
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
        if args[i] in ("--case", "--frame") and i + 1 < len(args):
            passthrough.extend([args[i], args[i + 1]])
            i += 2
            continue
        if args[i] in ("--skip-fbx",):
            passthrough.append(args[i])
            i += 1
            continue
        i += 1
    session.run(
        str(mayapy),
        str(ROOT / "tests" / "viewport" / "local_asset_motion_compare.py"),
        *passthrough,
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
        str(ROOT / "tests" / "viewport" / "runtime_bake_benchmark.py"),
        *passthrough,
        external=True,
    )
