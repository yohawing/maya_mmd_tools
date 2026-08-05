"""Session implementations for Maya-hosted fixture and plugin smokes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _runner_args_without_maya(posargs: list[str]) -> list[str]:
    """Return child-runner arguments after removing Nox's ``--maya`` option."""
    runner_args: list[str] = []
    skip_next = False
    for arg in posargs:
        if skip_next:
            skip_next = False
            continue
        if arg == "--maya":
            skip_next = True
            continue
        runner_args.append(arg)
    return runner_args


def run_cpp_plugin_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    default_config: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
    scripts: tuple[str, ...],
    require_plugin: bool,
) -> None:
    """Run one or more mayapy probes with the selected C++ plugin environment."""
    version = option(posargs, "--maya", default_maya_version)
    config = option(posargs, "--config", default_config)
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")

    env_values = {"MAYA_VERSION": version, "MMD_TOOLS_CPP_CONFIG": config}
    if require_plugin:
        plugin = root / "plug-ins" / version / config / "mmd_tools_cpp.mll"
        if not plugin.exists():
            session.error(
                f"C++ plugin not found at {plugin}; run 'uvx nox -s cpp_build "
                f"-- --maya {version} --config {config}' first."
            )
        env_values["MMD_TOOLS_CPP_PLUGIN"] = mayapy_arg_path(mayapy_path, plugin)
    env = mayapy_env(mayapy_path, **env_values)
    for script in scripts:
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, script),
            env=env,
            external=True,
        )


def run_yw_test_model_fixture_gate(
    session,
    *,
    posargs: list[str],
    options,
    option,
    default_maya_versions: tuple[str, ...],
    root: Path,
    require_build_path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the checked-in YW test-model fixture gate for each requested Maya version."""
    requested_versions = options(posargs, "--maya")
    versions = requested_versions or list(default_maya_versions)
    unsupported = [version for version in versions if version not in set(default_maya_versions)]
    if unsupported:
        session.error(
            "--maya must be one of "
            + ", ".join(default_maya_versions)
            + " for the YW test-model gate"
        )
    manifest = Path(option(posargs, "--manifest", "tests/data/yw_test_model.fixture.json"))
    if not manifest.is_absolute():
        manifest = root / manifest
    manifest = manifest.resolve()
    if not manifest.is_file():
        session.error(f"Fixture manifest not found: {manifest}")
    out_dir = require_build_path(
        session,
        option(posargs, "--out-dir", "build/yw-test-model-fixture"),
        "--out-dir",
    )
    for version in versions:
        mayapy_path = mayapy(version)
        if not mayapy_path.exists():
            session.error(f"mayapy not found for Maya {version}: {mayapy_path}")
        out_path = out_dir / f"maya-{version}.json"
        env = mayapy_env(mayapy_path, MAYA_VERSION=version, preserve_pythonpath=True)
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, "tests/viewport/yw_test_model_fixture_gate.py"),
            "--manifest",
            mayapy_arg_path(mayapy_path, manifest),
            "--out",
            mayapy_arg_path(mayapy_path, out_path),
            env=env,
            external=True,
        )
        if not out_path.is_file():
            session.error(f"Fixture gate did not write report: {out_path}")


def run_viewport_capture(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the plugin-free offscreen Maya viewport capture smoke."""
    version = option(posargs, "--maya", default_maya_version)
    out = option(posargs, "--out", str(root / "build/captures/viewport_smoke.png"))
    frame = option(posargs, "--frame", "1")
    width = option(posargs, "--width", "640")
    height = option(posargs, "--height", "480")
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/smoke_viewport_capture.py"),
        "--out",
        mayapy_arg_path(mayapy_path, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


def run_maya_batch_import(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
) -> None:
    """Run the Track 6 manifest-driven Maya batch import runner."""
    version = option(posargs, "--maya", default_maya_version)
    runner_args = _runner_args_without_maya(posargs)
    if not runner_args:
        runner_args = [
            "--manifest",
            str(root / "tests/track6/manifest_template.json"),
            "--limit",
            "1",
        ]

    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/track6/track6_runner.py"),
        *convert_mayapy_path_options(
            mayapy_path,
            runner_args,
            {"--manifest", "--out-dir", "--scan-root", "--write-manifest"},
        ),
        env=env,
        external=True,
    )


def run_pmx_roundtrip(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
) -> None:
    """Run the manifest-driven PMX import/export/re-import roundtrip runner."""
    version = option(posargs, "--maya", default_maya_version)
    runner_args = _runner_args_without_maya(posargs)
    if not runner_args:
        runner_args = [
            "--manifest",
            str(root / "tests/roundtrip/manifest_template.json"),
            "--limit",
            "1",
        ]

    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/roundtrip/pmx_roundtrip_runner.py"),
        *convert_mayapy_path_options(mayapy_path, runner_args, {"--manifest", "--out-dir"}),
        env=env,
        external=True,
    )


def run_model_readme_dialog_e2e(
    session,
    *,
    posargs: list[str],
    options,
    option,
    root: Path,
    require_build_path,
    python_executable: str = sys.executable,
) -> None:
    """Run and validate the Maya model-readme GUI gate for each version."""
    versions = options(posargs, "--maya") or ["2024", "2026"]
    unsupported = [version for version in versions if version not in {"2024", "2026"}]
    if unsupported:
        session.error("--maya must be 2024 or 2026 for the model-readme GUI gate")
    model = option(posargs, "--model", "tests/data/yw_test_model.pmx")
    out_dir = require_build_path(
        session,
        option(posargs, "--out-dir", "build/reports/model-readme-dialog-e2e"),
        "--out-dir",
    )
    for index, version in enumerate(versions):
        report = out_dir / f"maya-{version}.json"
        session.run(
            python_executable,
            str(root / "tests/viewport/model_readme_dialog_e2e.py"),
            "--maya",
            version,
            "--model",
            model,
            "--out",
            str(report),
            "--port",
            str(7731 + index),
            external=True,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        if result.get("status") != "pass":
            session.error(f"Maya {version} model-readme GUI gate failed: {result}")


def run_control_rig_gui_e2e(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    require_build_path,
    read_probe_report,
    clear_probe_report,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
    python_executable: str = sys.executable,
) -> None:
    """Run the GUI Control Rig E2E and its mandatory mmd-anim mesh oracle."""
    args = list(posargs) or ["--maya", default_maya_version]
    maya_version = option(args, "--maya", default_maya_version)
    out_dir = require_build_path(
        session,
        option(args, "--out-dir", "build/e2e"),
        "--out-dir",
    )
    model = option(args, "--model", "tests/data/mmt_test_model.pmx")
    evaluation_mode = option(args, "--evaluation-mode", "default")
    mode_suffix = "" if evaluation_mode == "default" else f"_{evaluation_mode}"
    route_suffix = "_create_on_import" if "--create-on-import" in args else ""
    output_suffix = f"{mode_suffix}{route_suffix}"
    gui_report = out_dir / f"mmd_control_rig_e2e_maya{maya_version}{output_suffix}.json"
    exported_vmd = out_dir / f"mmd_control_rig_e2e_maya{maya_version}{output_suffix}.vmd"

    session.run(
        python_executable,
        str(root / "tests" / "viewport" / "e2e_mmd_control_rig.py"),
        *args,
        external=True,
    )
    gui_report_data = read_probe_report(session, gui_report, "MMD control-rig GUI E2E")
    if gui_report_data.get("status") != "pass":
        session.error(f"Maya GUI control-rig E2E did not pass: {gui_report_data}")
    if not exported_vmd.is_file() or exported_vmd.stat().st_size == 0:
        session.error(f"GUI E2E did not produce a canonical exported VMD: {exported_vmd}")

    mayapy_path = mayapy(maya_version)
    if not mayapy_path.exists():
        session.error(f"mayapy not found for Maya {maya_version}: {mayapy_path}")
    ffi_path = (root / "external" / "mmd-anim" / "target" / "release").resolve()
    if not ffi_path.is_dir():
        session.error(
            "mmd-anim FFI release directory is required for the external oracle: "
            f"{ffi_path}"
        )
    oracle_report = out_dir / f"mmd_anim_mesh_oracle_compare_maya{maya_version}{output_suffix}.json"
    clear_probe_report(session, oracle_report, "mmd-anim mesh oracle")
    oracle_args = [
        "--pmx",
        mayapy_arg_path(mayapy_path, model),
        "--vmd",
        mayapy_arg_path(mayapy_path, exported_vmd),
        "--out",
        mayapy_arg_path(mayapy_path, oracle_report),
        "--mode",
        "rig",
        "--bind-source",
        "pmx",
        "--threshold",
        "0.01",
    ]
    for frame in range(6):
        oracle_args.extend(("--frame", str(frame)))
    oracle_env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=maya_version,
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_CPP_PLUGIN=mayapy_arg_path(
            mayapy_path,
            root / "plug-ins" / maya_version / "Debug" / "mmd_tools_cpp.mll",
        ),
        MMD_ANIM_FFI_PATH=str(ffi_path),
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/mmd_anim_mesh_oracle_compare.py"),
        *oracle_args,
        env=oracle_env,
        external=True,
        success_codes=(0, 1, 2),
    )
    external_report = read_probe_report(session, oracle_report, "mmd-anim mesh oracle")
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


def run_native_physics_bake(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    resolve_existing_or_repo_path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
    verify_bake_route: bool,
) -> None:
    """Run either the native-physics capture or the dual-route verification smoke."""
    args = list(posargs)
    version = option(args, "--maya", default_maya_version)
    ffi_path = option(args, "--ffi-path", "")
    session_name = "native_physics_bake_route_e2e" if verify_bake_route else "native_physics_bake_capture"
    if not ffi_path:
        raise ValueError(
            f"{session_name} requires --ffi-path pointing to a "
            "physics-feature-enabled mmd-anim-ffi directory or DLL"
        )
    if verify_bake_route:
        pmx = option(args, "--pmx", str(root / "tests/data/physics/test_hair_physics.pmx"))
        vmd = option(args, "--vmd", str(root / "tests/data/mmt_test_model_test_motion.vmd"))
        report = option(
            args,
            "--report",
            str(root / "build/reports/native_physics_bake_route_e2e.json"),
        )
        eval_frames = option(args, "--eval-frames", "0,1,2,3,4,5")
        delta_epsilon = option(args, "--delta-epsilon", "0.001")
        fps = option(args, "--fps", "30")
    else:
        pmx = option(args, "--pmx", str(root / "tests/data/mmt_test_model.pmx"))
        vmd = option(args, "--vmd", str(root / "tests/data/mmt_test_model_test_motion.vmd"))
        out = option(args, "--out", str(root / "build/captures/native_physics_bake.png"))
        report = option(args, "--report", str(root / "build/reports/native_physics_bake_capture.json"))
        frame = option(args, "--frame", "0")
        fps = option(args, "--fps", "30")
        width = option(args, "--width", "640")
        height = option(args, "--height", "480")

    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    resolved_ffi = resolve_existing_or_repo_path(ffi_path)
    env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=version,
        MMD_ANIM_FFI_PATH=str(resolved_ffi),
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    command = [
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/native_physics_bake_capture.py"),
    ]
    if verify_bake_route:
        command.extend(
            [
                "--verify-bake-route",
                "--pmx",
                mayapy_arg_path(mayapy_path, pmx),
                "--vmd",
                mayapy_arg_path(mayapy_path, vmd),
                "--report",
                mayapy_arg_path(mayapy_path, report),
                "--eval-frames",
                eval_frames,
                "--delta-epsilon",
                delta_epsilon,
                "--fps",
                fps,
            ]
        )
    else:
        command.extend(
            [
                "--pmx",
                mayapy_arg_path(mayapy_path, pmx),
                "--vmd",
                mayapy_arg_path(mayapy_path, vmd),
                "--out",
                mayapy_arg_path(mayapy_path, out),
                "--report",
                mayapy_arg_path(mayapy_path, report),
                "--frame",
                frame,
                "--fps",
                fps,
                "--width",
                width,
                "--height",
                height,
            ]
        )
    session.run(*command, env=env, external=True)


def run_physics_solver_cycle_probe(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    clear_probe_report,
    run_mayapy_probe,
    read_probe_report,
) -> None:
    """Capture the Citlali physics-solver cycle evidence report."""
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    out_value = option(args, "--out", str(root / "build/reports/physics_solver_cycle_probe.json"))
    pmx_value = option(
        args,
        "--pmx",
        str(root / "build/fixtures/citlali_ascii_file/citlali.pmx"),
    )
    frames_value = option(args, "--frames", "0,1,2,1,0")
    modes_value = option(args, "--modes", "off,serial,parallel")
    report_path = Path(out_value)
    passthrough = [
        "--pmx", pmx_value,
        "--out", out_value,
        "--frames", frames_value,
        "--modes", modes_value,
    ]
    clear_probe_report(session, report_path, "physics solver cycle probe")
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/physics_solver_cycle_probe.py",
        passthrough,
        {"--pmx", "--out"},
        utf8=True,
    )
    report = read_probe_report(session, report_path, "Physics solver cycle probe")
    if report.get("status") != "pass":
        session.error(
            "Physics solver cycle probe failed: "
            f"errors={report.get('errors')}, solver={report.get('solver')}"
        )


def run_root_move_skin_parity_probe(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    clear_probe_report,
    run_mayapy_probe,
    read_probe_report,
) -> None:
    """Capture root-motion skin and world-space mesh parity evidence."""
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    out_value = option(args, "--out", str(root / "build/reports/root_move_skin_parity_probe.json"))
    pmx_value = option(
        args,
        "--pmx",
        str(root / "build/fixtures/citlali_ascii_file/citlali.pmx"),
    )
    delta_value = option(args, "--delta", "17.5,-8.25,11.0")
    vertices_value = option(args, "--vertices-per-mesh", "8")
    tolerance_value = option(args, "--tolerance", "1.0e-4")
    report_path = Path(out_value)
    passthrough = [
        "--pmx", pmx_value,
        "--out", out_value,
        "--delta", delta_value,
        "--vertices-per-mesh", vertices_value,
        "--expect-parity",
        "--tolerance", tolerance_value,
    ]
    clear_probe_report(session, report_path, "root move parity")
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/root_move_skin_parity_probe.py",
        passthrough,
        {"--pmx", "--out"},
        utf8=True,
    )
    report = read_probe_report(session, report_path, "Root move parity")
    if report.get("status") != "pass":
        session.error(f"Root move parity probe failed: errors={report.get('errors')}")


def run_root_move_ik_target_probe(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    clear_probe_report,
    run_mayapy_probe,
    read_probe_report,
) -> None:
    """Capture root-motion foot and IK-target drift evidence."""
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    out_value = option(args, "--out", str(root / "build/reports/root_move_ik_target_probe.json"))
    pmx_value = option(
        args,
        "--pmx",
        str(root / "build/fixtures/citlali_ascii_file/citlali.pmx"),
    )
    delta_value = option(args, "--delta", "17.5,-8.25,11.0")
    tolerance_value = option(args, "--tolerance", "1.0e-4")
    report_path = Path(out_value)
    passthrough = [
        "--pmx", pmx_value,
        "--out", out_value,
        "--delta", delta_value,
        "--expect-root-parity",
        "--tolerance", tolerance_value,
    ]
    clear_probe_report(session, report_path, "root move IK target")
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/root_move_ik_target_probe.py",
        passthrough,
        {"--pmx", "--out"},
        utf8=True,
    )
    report = read_probe_report(session, report_path, "Root move IK target")
    if report.get("status") != "pass":
        session.error(f"Root move IK target probe failed: errors={report.get('errors')}")


def run_humanik_definition_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    probe_passthrough,
    convert_mayapy_path_options,
    mayapy_script,
    mayapy_env,
) -> None:
    """Create a minimal HumanIK definition under mayapy."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    passthrough = probe_passthrough(
        list(posargs),
        {"--out", "--name", "--fixture"},
        {"--create-control-rig"},
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/humanik_definition_smoke.py"),
        *convert_mayapy_path_options(mayapy_path, passthrough, {"--out"}),
        env=mayapy_env(mayapy_path),
        external=True,
    )


def run_humanik_retarget_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    probe_passthrough,
    run_mayapy_probe,
) -> None:
    """Run the direct HumanIK S0 fixture smoke under mayapy."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
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
    passthrough = probe_passthrough(list(posargs), value_options)
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/humanik_retarget_smoke.py",
        passthrough,
        path_options,
    )


def run_humanik_roundtrip_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    root: Path,
    probe_passthrough,
    clear_probe_report,
    run_mayapy_probe,
) -> None:
    """Run the HumanIK S5 self-retarget matrix and validate every report."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    requested_mode = option(args, "--evaluation-mode", "")
    modes = [requested_mode] if requested_mode else ["off", "serial", "parallel"]
    out_value = option(args, "--out", str(root / "build/reports/humanik_roundtrip_smoke.json"))
    value_options = {"--pmx", "--vmd", "--start", "--end", "--hik-profile", "--characterization-stance"}
    path_options = {"--pmx", "--vmd", "--out"}
    passthrough = probe_passthrough(args, value_options)
    base_out = Path(out_value)
    failed_modes: list[str] = []
    for mode in modes:
        mode_out = base_out if requested_mode else base_out.with_name(f"{base_out.stem}.{mode}{base_out.suffix}")
        clear_probe_report(session, mode_out, "HumanIK S5")
        mode_args = [*passthrough, "--evaluation-mode", mode, "--out", str(mode_out)]
        run_mayapy_probe(
            session,
            mayapy_path,
            "tests/viewport/humanik_roundtrip_smoke.py",
            mode_args,
            path_options,
            success_codes=(0, 1),
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
            failed_modes.append(f"{mode}: evaluationMode={report.get('evaluationMode', 'missing')}")
            continue
        if report.get("status") != "pass":
            failed_modes.append(f"{mode}: status={report.get('status', 'missing')}")
    if failed_modes:
        session.error("HumanIK S5 round-trip matrix failed: " + "; ".join(failed_modes))


def run_humanik_vmd_parity_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    root: Path,
    probe_passthrough,
    clear_probe_report,
    run_mayapy_probe,
) -> None:
    """Run the HumanIK SOURCE/VMD reproduction matrix with stop evidence policy."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    requested_mode = option(args, "--evaluation", "")
    modes = [requested_mode] if requested_mode else ["off", "serial", "parallel"]
    allow_stop = "--allow-stop" in args
    out_value = option(args, "--out", str(root / "build/reports/humanik_vmd_parity_smoke.json"))
    value_options = {"--model", "--motion", "--frames"}
    path_options = {"--model", "--motion", "--out"}
    passthrough = probe_passthrough(args, value_options, {"--inject-restore-failure"})
    base_out = Path(out_value)
    failed_modes: list[str] = []
    stopped_modes: list[str] = []
    for mode in modes:
        mode_out = base_out if requested_mode else base_out.with_name(f"{base_out.stem}.{mode}{base_out.suffix}")
        clear_probe_report(session, mode_out, "HumanIK VMD parity")
        mode_args = [*passthrough, "--evaluation", mode, "--out", str(mode_out)]
        run_mayapy_probe(
            session,
            mayapy_path,
            "tests/viewport/humanik_vmd_parity_smoke.py",
            mode_args,
            path_options,
            utf8=True,
            success_codes=(0, 1, 2),
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


def run_humanik_vmd_import_gate_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    root: Path,
    probe_passthrough,
    clear_probe_report,
    run_mayapy_probe,
    read_probe_report,
) -> None:
    """Run and validate the fail-closed HumanIK VMD-import mode gate."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    out_value = option(args, "--out", str(root / "build/reports/humanik_vmd_import_gate_smoke.json"))
    report_path = Path(out_value)
    path_options = {"--model", "--motion", "--out"}
    passthrough = probe_passthrough(args, path_options)
    if "--out" not in passthrough:
        passthrough.extend(["--out", out_value])
    clear_probe_report(session, report_path, "HumanIK VMD import gate")
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/humanik_vmd_import_gate_smoke.py",
        passthrough,
        path_options,
        utf8=True,
        success_codes=(0, 1),
    )
    report = read_probe_report(session, report_path, "HumanIK VMD import gate")
    status = report.get("status")
    if status != "pass":
        session.error(
            "HumanIK VMD import gate smoke failed: "
            f"status={status}, error={report.get('error')}, "
            f"gateRaised={report.get('gateRaised')}, "
            f"topologyUnchangedAfterRefusal={report.get('topologyUnchangedAfterRefusal')}, "
            f"postRestoreImportSucceeded={report.get('postRestoreImportSucceeded')}"
        )


def run_humanik_citlali_stance_smoke(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    root: Path,
    clear_probe_report,
    run_mayapy_probe,
    read_probe_report,
) -> None:
    """Run the strict Citlali HumanIK setup/restore evidence gate."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    args = list(posargs)
    out_value = option(args, "--out", str(root / "build/reports/humanik_citlali_stance_smoke.json"))
    pmx_value = option(args, "--pmx", str(root / "build/fixtures/citlali_ascii_file/citlali.pmx"))
    profile = option(args, "--profile", "body-only")
    report_path = Path(out_value)
    passthrough = ["--pmx", pmx_value, "--out", out_value, "--profile", profile]
    clear_probe_report(session, report_path, "Citlali HumanIK")
    run_mayapy_probe(
        session,
        mayapy_path,
        "tests/viewport/humanik_citlali_stance_smoke.py",
        passthrough,
        {"--pmx", "--out"},
        utf8=True,
    )
    report = read_probe_report(session, report_path, "Citlali HumanIK")
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


def run_shader_override_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the legacy MMDShader VP2.0 offscreen smoke."""
    version = option(posargs, "--maya", default_maya_version)
    out = option(posargs, "--out", str(root / "build/captures/shader_override_smoke.png"))
    frame = option(posargs, "--frame", "1")
    width = option(posargs, "--width", "640")
    height = option(posargs, "--height", "480")
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/smoke_shader_override.py"),
        "--out",
        mayapy_arg_path(mayapy_path, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


def run_render_override_smoke(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Run the opt-in R1 passthrough render-override mayapy smoke."""
    version = option(posargs, "--maya", default_maya_version)
    out = option(posargs, "--out", str(root / "build/captures/render_override_smoke.png"))
    frame = option(posargs, "--frame", "1")
    width = option(posargs, "--width", "640")
    height = option(posargs, "--height", "480")
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=version,
        MMD_TOOLS_ENABLE_RENDER_OVERRIDE="1",
        MMD_TOOLS_ENABLE_RENDER_OVERRIDE_TARGET_PROBE="0",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/smoke_render_override.py"),
        "--out",
        mayapy_arg_path(mayapy_path, out),
        "--frame",
        frame,
        "--width",
        width,
        "--height",
        height,
        env=env,
        external=True,
    )


def run_render_override_e2e(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
) -> None:
    """Run the GUI R1 gate and optional real-PMX target-routing probe."""
    version = option(posargs, "--maya", default_maya_version)
    out_dir = option(posargs, "--out-dir", str(root / "build" / "render-override-e2e"))
    port = option(posargs, "--port", "7731")
    timeout = option(posargs, "--timeout", "240")
    vp2_device = option(posargs, "--vp2-device", "default")
    target_probe = "--target-probe" in posargs
    r32f_binding_probe = "--r32f-binding-probe" in posargs
    r32f_caster_pass = "--r32f-caster-pass" in posargs
    r32f_receiver_probe = "--r32f-receiver-probe" in posargs
    r32f_light_space_caster = "--r32f-light-space-caster" in posargs
    model = option(posargs, "--model", "")
    if vp2_device not in {"default", "dx11", "gl", "glcore"}:
        session.error(f"Unsupported --vp2-device: {vp2_device}")
    if model and not target_probe:
        session.error("--model requires --target-probe")
    if r32f_binding_probe and not target_probe:
        session.error("--r32f-binding-probe requires --target-probe")
    if r32f_caster_pass and not target_probe:
        session.error("--r32f-caster-pass requires --target-probe")
    if r32f_receiver_probe and not r32f_caster_pass:
        session.error("--r32f-receiver-probe requires --r32f-caster-pass")
    if r32f_light_space_caster and not r32f_caster_pass:
        session.error("--r32f-light-space-caster requires --r32f-caster-pass")
    session.run(
        sys.executable,
        str(root / "tools" / "render_override_e2e.py"),
        "--maya",
        version,
        "--out-dir",
        out_dir,
        "--port",
        port,
        "--timeout",
        timeout,
        "--vp2-device",
        vp2_device,
        *( ["--r32f-binding-probe"] if r32f_binding_probe else []),
        *( ["--r32f-caster-pass"] if r32f_caster_pass else []),
        *( ["--r32f-receiver-probe"] if r32f_receiver_probe else []),
        *( ["--r32f-light-space-caster"] if r32f_light_space_caster else []),
        *(["--model", model] if model else []),
        *(["--target-probe"] if target_probe else []),
        external=True,
    )


def run_static_render(
    session,
    *,
    posargs: list[str],
    option,
    has_flag,
    default_maya_version: str,
    root: Path,
    require_build_path,
    mayapy,
    mayapy_env,
    mayapy_arg_path,
    mayapy_script,
) -> None:
    """Construct and run the fixed-camera PMX static-render capture."""
    shader_flag = "--shader" if has_flag(posargs, "--shader") else "--no-shader"
    version = option(posargs, "--maya", default_maya_version)
    model = option(posargs, "--model", str(root / "tests/data/for_unit_test/test_1bone_cube.pmx"))
    out = option(posargs, "--out", str(root / "build/captures/static_render_1bone_cube.png"))
    frame = option(posargs, "--frame", "0")
    width = option(posargs, "--width", "1024")
    height = option(posargs, "--height", "1024")
    shader_backend = option(posargs, "--shader-backend", "auto")
    if shader_backend not in {"auto", "dx11", "glsl", "standard"}:
        session.error(f"Unsupported --shader-backend: {shader_backend}")
    vp2_device = option(posargs, "--vp2-device", "default")
    if vp2_device not in {"default", "gl", "glcore", "dx11"}:
        session.error(f"Unsupported --vp2-device: {vp2_device}")
    view_transform = option(posargs, "--view-transform", "Un-tone-mapped (sRGB)")
    display = option(posargs, "--display", "sRGB")
    rendering_space = option(posargs, "--rendering-space", "ACEScg")
    diagnostics_out = option(posargs, "--diagnostics-out", "")
    mayapy_path = mayapy(version)
    if not mayapy_path.exists():
        raise FileNotFoundError(f"mayapy not found: {mayapy_path}")
    diagnostics_args: list[str] = []
    if diagnostics_out:
        diagnostics_path = require_build_path(session, diagnostics_out, "--diagnostics-out")
        diagnostics_args.extend(["--diagnostics-out", mayapy_arg_path(mayapy_path, diagnostics_path)])
    if has_flag(posargs, "--allow-blank"):
        diagnostics_args.append("--allow-blank")
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    vp2_device_map = {"gl": "VirtualDeviceGL", "glcore": "VirtualDeviceGLCore", "dx11": "VirtualDeviceDx11"}
    if vp2_device in vp2_device_map:
        env["MAYA_VP2_DEVICE_OVERRIDE"] = vp2_device_map[vp2_device]
    command = [
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/static_render_capture.py"),
        shader_flag,
        "--out", mayapy_arg_path(mayapy_path, out),
        "--model", mayapy_arg_path(mayapy_path, model),
        "--frame", frame,
        "--width", width,
        "--height", height,
        "--shader-backend", shader_backend,
        "--view-transform", view_transform,
        "--display", display,
        "--rendering-space", rendering_space,
        *diagnostics_args,
    ]
    session.run(*command, env=env, external=True)


def run_visual_regression(
    session,
    *,
    posargs: list[str],
    option,
    options,
    has_flag,
    default_maya_version: str,
    require_build_path,
    python_executable: str = sys.executable,
) -> None:
    """Run manifest-driven viewport capture and its optional comparison."""
    version = option(posargs, "--maya", default_maya_version)
    manifest = option(posargs, "--manifest", "")
    if not manifest:
        session.error("--manifest is required for maya_visual_regression")
    shader_backend = option(posargs, "--shader-backend", "dx11")
    if shader_backend not in {"dx11", "glsl"}:
        session.error(f"Unsupported --shader-backend: {shader_backend}")
    vp2_device = option(posargs, "--vp2-device", "default")
    if vp2_device not in {"default", "gl", "glcore", "dx11"}:
        session.error(f"Unsupported --vp2-device: {vp2_device}")
    display_textures = option(posargs, "--display-textures", "on")
    if display_textures not in {"on", "off"}:
        session.error(f"Unsupported --display-textures: {display_textures}")
    out = option(posargs, "--out", f"build/visual-regression/maya-{shader_backend}")
    out_path = require_build_path(session, out, "--out")
    port = option(posargs, "--port", "7721")
    width = option(posargs, "--width", "1024")
    height = option(posargs, "--height", "1024")
    timeout = option(posargs, "--timeout", "420")
    forwarded: list[str] = []
    passthrough_flags = {
        "--keep-maya", "--no-compare", "--attach-existing", "--debug-lambert-control",
        "--debug-outline-sentinel", "--hide-orig-shapes",
    }
    passthrough_options = {"--case", "--tag", "--limit", "--launch-mode", "--shader-fx"}
    i = 0
    while i < len(posargs):
        arg = posargs[i]
        if arg in passthrough_flags:
            forwarded.append(arg)
            i += 1
            continue
        if arg in passthrough_options:
            if i + 1 >= len(posargs):
                session.error(f"{arg} requires a value")
            forwarded.extend([arg, posargs[i + 1]])
            i += 2
            continue
        i += 1
    command = [
        python_executable,
        "tests/viewport/visual_regression_capture.py",
        "--maya", version,
        "--manifest", manifest,
        "--out", str(out_path),
        "--port", port,
        "--width", width,
        "--height", height,
        "--timeout", timeout,
        "--shader-backend", shader_backend,
        "--vp2-device", vp2_device,
        "--display-textures", display_textures,
        *forwarded,
    ]
    session.run(*command, external=True)
    if not has_flag(posargs, "--no-compare"):
        comparison_command = [
            python_executable,
            "tests/viewport/visual_regression_compare.py",
            "--capture-report", str(out_path / "visual-regression-report.json"),
            "--out", str(out_path / "visual-regression-comparison.json"),
        ]
        for threshold in options(posargs, "--threshold"):
            comparison_command.extend(["--threshold", threshold])
        session.run(*comparison_command, external=True)


def run_shader_visual_semantic_gate(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    require_build_path,
    python_executable: str = sys.executable,
) -> None:
    """Capture the DX11 semantic cases and run their report-only gate."""
    from tests.viewport.shader_visual_semantic_gate import CASE_MIN_FOREGROUND

    version = option(posargs, "--maya", default_maya_version)
    manifest = option(posargs, "--manifest", "")
    if not manifest:
        session.error("--manifest <fixture.render.json> is required")
    out_path = require_build_path(
        session,
        option(posargs, "--out", "build/visual-regression/shader-semantic"),
        "--out",
    )
    port = option(posargs, "--port", "7721")
    timeout = option(posargs, "--timeout", "240")
    capture_command = [
        python_executable,
        "tests/viewport/visual_regression_capture.py",
        "--maya", version,
        "--manifest", manifest,
        "--out", str(out_path),
        "--port", port,
        "--timeout", timeout,
        "--shader-backend", "dx11",
        "--vp2-device", "dx11",
        "--display-textures", "on",
        "--debug-outline-sentinel",
        "--no-compare",
    ]
    for case_name in CASE_MIN_FOREGROUND:
        capture_command.extend(["--case", case_name])
    session.run(*capture_command, external=True)
    session.run(
        python_executable,
        "tests/viewport/shader_visual_semantic_gate.py",
        "--capture-report",
        str(out_path / "visual-regression-report.json"),
        "--out",
        str(out_path / "shader-semantic-report.json"),
        external=True,
    )


def run_import_order_e2e(
    session,
    *,
    posargs: list[str],
    option,
    has_flag,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
    write_local_manifest,
) -> None:
    """Run manifest-driven mayapy checks for model/motion import ordering."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
    manifest = option(args, "--manifest", "")
    path_options = {"--manifest", "--out-dir", "--log"}
    value_options = path_options | {
        "--background-model",
        "--character-model",
        "--character-motion",
        "--case",
        "--limit",
        "--order-limit",
    }
    flag_options = {"--require-zero-fallback"}
    if not manifest:
        generated_manifest = write_local_manifest(
            session,
            option(args, "--background-model", str(root / "tests/data/for_unit_test/test_1bone_cube.pmx")),
            option(args, "--character-model", str(root / "tests/data/mmt_test_model.pmx")),
            option(args, "--character-motion", str(root / "tests/data/mmt_test_model_test_motion.vmd")),
        )
        passthrough.extend(["--manifest", str(generated_manifest)])
    env = mayapy_env(mayapy_path, preserve_pythonpath=True)
    if has_flag(args, "--require-zero-fallback"):
        profile_value = os.environ.get("MMD_TOOLS_VMD_PROFILE_JSONL")
        if profile_value:
            profile_path = Path(profile_value)
            if not profile_path.is_absolute():
                profile_path = root / profile_path
        else:
            out_dir_value = option(args, "--out-dir", str(root / "build/import-order-e2e"))
            out_dir_path = Path(out_dir_value)
            if not out_dir_path.is_absolute():
                out_dir_path = root / out_dir_path
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
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/import_order_e2e.py"),
        *convert_mayapy_path_options(mayapy_path, passthrough, path_options),
        env=env,
        external=True,
    )


def run_import_scale_drift_e2e(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
) -> None:
    """Run mayapy diagnostics for import scale and skin bind drift."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
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
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/import_scale_drift_e2e.py"),
        *convert_mayapy_path_options(mayapy_path, passthrough, path_options),
        env=mayapy_env(mayapy_path, preserve_pythonpath=True),
        external=True,
    )


def run_anim_layer_graph_compare(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
) -> None:
    """Run mayapy diagnostics comparing setKeyframe and API animLayer graphs."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
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
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/anim_layer_graph_compare.py"),
        *convert_mayapy_path_options(mayapy_path, passthrough, path_options),
        env=mayapy_env(mayapy_path, preserve_pythonpath=True),
        external=True,
    )


def run_runtime_bake_bench(
    session,
    *,
    posargs: list[str],
    option,
    mayapy,
    mayapy_env,
    mayapy_script,
    convert_mayapy_path_options,
) -> None:
    """Measure the Maya runtime-bake import path."""
    maya_version = option(posargs, "--maya", "2024")
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
    value_options = {"--case", "--pmx", "--vmd", "--out", "--log", "--repeat"}
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
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/runtime_bake_benchmark.py"),
        *convert_mayapy_path_options(
            mayapy_path,
            passthrough,
            {"--pmx", "--vmd", "--out", "--log"},
        ),
        env=mayapy_env(mayapy_path, preserve_pythonpath=True),
        external=True,
    )
