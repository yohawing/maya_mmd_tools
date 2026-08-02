"""Session implementations for Maya-hosted fixture and plugin smokes."""

from __future__ import annotations

import json
import sys
from pathlib import Path


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
