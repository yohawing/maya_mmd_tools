"""Session implementations for release-only native verification gates."""

from __future__ import annotations

import json
import os
import sys
import shutil
from pathlib import Path


def run_native_physics_release_gate(
    session,
    *,
    root: Path,
    bundled_physics_runtime,
    mayapy,
    mayapy_env,
    mayapy_script,
    maya_process_path,
    python_executable: str = sys.executable,
) -> None:
    """Run the bundled native physics route twice and compare deterministic output."""
    maya_version = "2024"
    mayapy_path = mayapy(maya_version)
    pmx = (root / "tests/data/physics/test_hair_physics.pmx").resolve()
    vmd = (root / "tests/data/mmt_test_model_test_motion.vmd").resolve()
    ffi = bundled_physics_runtime()
    report_dir = (root / "build/reports").resolve()
    run_reports = [
        report_dir / "native_physics_release_run1.json",
        report_dir / "native_physics_release_run2.json",
    ]
    comparison_json = report_dir / "native_physics_release_comparison.json"
    comparison_md = report_dir / "native_physics_release_comparison.md"
    for stale_report in (*run_reports, comparison_json, comparison_md):
        if stale_report.exists():
            stale_report.unlink()
    for required in (mayapy_path, pmx, vmd, ffi):
        if not required.is_file():
            raise FileNotFoundError(f"Native physics release gate input not found: {required}")
    env = mayapy_env(
        mayapy_path,
        MAYA_VERSION=maya_version,
        MMD_ANIM_FFI_PATH=str(ffi),
        MAYA_SKIP_USERSETUP_PY="1",
        MMD_TOOLS_SKIP_SHADER_OVERRIDE="1",
    )
    for report in run_reports:
        session.run(
            str(mayapy_path),
            mayapy_script(mayapy_path, "tests/viewport/native_physics_bake_capture.py"),
            "--verify-bake-route",
            "--pmx",
            maya_process_path(mayapy_path, pmx),
            "--vmd",
            maya_process_path(mayapy_path, vmd),
            "--report",
            maya_process_path(mayapy_path, report),
            "--eval-frames",
            "0,1,2,3,4,5",
            env=env,
            external=True,
        )
    session.run(
        python_executable,
        "tests/release/native_physics_determinism.py",
        "--run1",
        str(run_reports[0]),
        "--run2",
        str(run_reports[1]),
        "--ffi",
        str(ffi),
        "--out-json",
        str(comparison_json),
        "--out-md",
        str(comparison_md),
        external=True,
    )


def run_flip_report(
    session,
    *,
    posargs: list[str],
    option,
    require_build_path,
) -> None:
    """Run the report-only NVIDIA FLIP image comparison CLI."""
    reference = option(posargs, "--reference", "")
    test = option(posargs, "--test", "")
    out_dir = option(posargs, "--out-dir", "build/flip-reports/report")
    basename = option(posargs, "--basename", "flip_result")
    csv = option(posargs, "--csv", "")

    if not reference:
        session.error("--reference <path> is required")
    if not test:
        session.error("--test <path> is required")

    out_path = require_build_path(session, out_dir, "--out-dir")
    out_path.mkdir(parents=True, exist_ok=True)
    csv_arg = ["-c", str(require_build_path(session, csv, "--csv"))] if csv else []
    flip_exe = shutil.which("flip")
    if not flip_exe:
        session.error(
            "NVIDIA FLIP CLI not found. Install dev dependencies with: "
            "python -m pip install -e .[dev]"
        )

    command: list[str] = [
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
    session.run(*command, external=True)


def run_golden_oracle(
    session,
    *,
    posargs: list[str],
    option,
    root: Path,
    downloaded_mmd_anim_cli,
) -> None:
    """Verify the pinned mmd-anim CLI against the numeric GoldenOracle manifest."""
    manifest = option(posargs, "--manifest", str(root / "tests/golden-oracle/manifest.json"))
    mmd_anim = downloaded_mmd_anim_cli(session)
    session.run(str(mmd_anim), "verify", manifest, "--mode", "numeric", external=True)


def run_release_camera_motion_oracle(
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
    mayapy_script,
    maya_process_path,
    convert_mayapy_path_options,
    copy_parity_vmd,
    current_epsilon: str,
    addiction_camera_vmd: str,
    interpolation_eye_max: str,
    interpolation_forward_max_deg: str,
    interpolation_up_max_deg: str,
    interpolation_rotation_max_deg: str,
) -> None:
    """Run the local GoldenOracle camera-motion release gate."""
    args = list(posargs)
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    manifest = option(posargs, "--manifest", "tests/data/camera_motion/manifest.json")
    out_dir = require_build_path(
        session,
        option(posargs, "--out-dir", "build/local-camera-motion-oracle/release"),
        "--out-dir",
    )
    manifest_path = Path(manifest)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve()
    strict_local = has_flag(args, "--strict-local")
    if not manifest_path.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        skip_report = out_dir / "manifest-skip.json"
        payload = {
            "status": "fail" if strict_local else "skip",
            "summary": {"passed": 0, "failed": 1 if strict_local else 0, "skipped": 1},
            "manifest": str(manifest_path),
            "detail": "manifest not found",
        }
        skip_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        session.log(f"Camera motion manifest not found: {manifest_path}")
        session.log(f"Camera motion skip report: {skip_report}")
        if strict_local:
            session.error("Camera motion manifest is required with --strict-local")
        return

    default_release_cases = [
        "camera-edge-generated-vmd",
        "camera-interpolation-isolated-vmd",
    ]
    requested_case = option(posargs, "--case", "")
    if has_flag(posargs, "--all-cases"):
        selected_cases = [""]
    elif requested_case:
        selected_cases = [requested_case]
    else:
        selected_cases = default_release_cases

    common_args = ["--manifest", manifest]
    if "--current-epsilon" not in args:
        common_args.extend(["--current-epsilon", current_epsilon])
    parity_args: list[str] = [
        "--parity-current-report-only",
        "--all-frames",
        "--parity-interpolation-eye-max",
        option(posargs, "--parity-interpolation-eye-max", interpolation_eye_max),
        "--parity-interpolation-forward-max-deg",
        option(posargs, "--parity-interpolation-forward-max-deg", interpolation_forward_max_deg),
        "--parity-interpolation-up-max-deg",
        option(posargs, "--parity-interpolation-up-max-deg", interpolation_up_max_deg),
        "--parity-interpolation-rotation-max-deg",
        option(posargs, "--parity-interpolation-rotation-max-deg", interpolation_rotation_max_deg),
    ]
    parity_epsilon = option(posargs, "--parity-epsilon", "")
    if parity_epsilon:
        parity_args.extend(["--parity-epsilon", parity_epsilon])

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
    i = 0
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

    def report_failed(report_path: Path) -> bool:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            return int((report.get("summary") or {}).get("failed", 0)) != 0
        except Exception:
            return True

    failed_reports: list[str] = []
    for case_name in selected_cases:
        case_args = list(common_args)
        case_suffix = "all-cases"
        if case_name:
            case_args.extend(["--case", case_name])
            case_suffix = case_name
        for mode in ("bake", "sparse"):
            report_path = out_dir / f"{mode}-{case_suffix}.json"
            runner_args = [*case_args, "--mode", mode, "--out", str(report_path)]
            if mode == "sparse" and not has_flag(posargs, "--strict-sparse-current"):
                runner_args.append("--current-report-only")
            session.run(
                str(mayapy_path),
                mayapy_script(mayapy_path, "tests/local/camera_motion_oracle_runner.py"),
                "--repo-root",
                maya_process_path(mayapy_path, root),
                *convert_mayapy_path_options(mayapy_path, runner_args, {"--manifest", "--out"}),
                env=mayapy_env(mayapy_path),
                external=True,
                success_codes=[0, 1],
            )
            if report_failed(report_path):
                failed_reports.append(str(report_path))

    if not has_flag(posargs, "--skip-addiction-parity"):
        addiction_vmd = Path(addiction_camera_vmd)
        if addiction_vmd.exists():
            report_path = out_dir / "bake-rig-camera-addiction.json"
            addiction_args = copy_parity_vmd(
                session,
                ["--parity-vmd", str(addiction_vmd), *parity_args],
            )
            session.run(
                str(mayapy_path),
                mayapy_script(mayapy_path, "tests/local/camera_motion_oracle_runner.py"),
                "--repo-root",
                maya_process_path(mayapy_path, root),
                "--parity-case-name",
                "camera-addiction-bake-rig-parity",
                "--out",
                maya_process_path(mayapy_path, report_path),
                *convert_mayapy_path_options(mayapy_path, addiction_args, {"--parity-vmd"}),
                env=mayapy_env(mayapy_path),
                external=True,
                success_codes=[0, 1],
            )
            if report_failed(report_path):
                failed_reports.append(str(report_path))
        else:
            session.log(f"Skipping Addiction camera parity; local VMD not found: {addiction_vmd}")

    if failed_reports:
        session.error("Camera motion release gate failed; reports: " + ", ".join(failed_reports))


def run_release_gate(
    session,
    *,
    posargs: list[str],
    option,
    options,
    has_flag,
    root: Path,
    default_maya_version: str,
    default_cpp_config: str,
    default_cpp_versions: tuple[str, ...],
    release_maya_versions: tuple[str, ...],
    viewport_matrix: tuple[tuple[str, str, str], ...],
    default_visual_manifest: str,
    release_visual_ports: dict[str, str],
    release_visual_cases,
    new_release_gate_run,
    release_gate_pin_check,
    release_gate_version_check,
    release_gate_tier0_commands,
    release_gate_tier1_commands,
    release_gate_tier2_commands,
    release_gate_tier3_commands,
    run_release_gate_callable,
    run_release_gate_command,
    write_release_gate_reports,
    release_gate_failure_label,
    format_test_summary,
    environment=None,
) -> None:
    """Run release verification tiers with keep-going reporting."""
    run_id, run_timestamp = new_release_gate_run()
    args = list(posargs)
    quick = has_flag(args, "--quick")
    version = option(args, "--maya", default_maya_version)
    cpp_versions = options(args, "--cpp-maya") or list(default_cpp_versions)
    cpp_config = option(args, "--cpp-config", default_cpp_config)
    ffi_cargo_target_dir = option(args, "--ffi-cargo-target-dir", "")
    ffi_path = option(args, "--ffi-path", "")
    if ffi_cargo_target_dir and not ffi_path:
        ffi_path = str(Path(ffi_cargo_target_dir) / "release")
    strict_local = has_flag(args, "--strict-local")
    verbose = has_flag(args, "--verbose")
    local_assets_manifest = option(args, "--local-assets-manifest", "local-assets-manifest.json")
    camera_manifest = option(args, "--camera-manifest", "tests/data/camera_motion/manifest.json")
    local_parity_manifest = option(args, "--local-parity-manifest", "local-parity-manifest.json")
    env = os.environ if environment is None else environment
    visual_manifest = Path(
        option(
            args,
            "--visual-manifest",
            env.get("GOLDEN_ORACLE_RENDER_MANIFEST", default_visual_manifest),
        )
    )
    results: list[dict[str, object]] = []

    if not quick:
        run_release_gate_callable("tier0:mmd-anim-pin", release_gate_pin_check, results)
        if results[-1]["status"] == "fail":
            md_path, json_path = write_release_gate_reports(
                results,
                quick,
                run_id=run_id,
                timestamp=run_timestamp,
            )
            session.log(f"Release gate report: {md_path}")
            session.log(f"Release gate JSON: {json_path}")
            session.error(
                "Release gate preflight failed: "
                f"{release_gate_failure_label(results[-1])}"
            )

    for name, command in release_gate_tier0_commands():
        run_release_gate_command(name, command, results, verbose=verbose)
    run_release_gate_callable("tier0:version-markers", release_gate_version_check, results)

    for name, command in release_gate_tier1_commands(
        quick=quick,
        ffi_cargo_target_dir=ffi_cargo_target_dir,
        ffi_path=ffi_path,
    ):
        run_release_gate_command(name, command, results, verbose=verbose)

    if not quick:
        if not visual_manifest.is_file():
            run_release_gate_callable(
                "tier2:generated-pmx-visual-manifest",
                lambda: (_ for _ in ()).throw(
                    FileNotFoundError(
                        f"GoldenOracle render manifest not found: {visual_manifest}. "
                        "Pass --visual-manifest or set GOLDEN_ORACLE_RENDER_MANIFEST."
                    )
                ),
                results,
            )
        for name, command in release_gate_tier2_commands(
            version=version,
            cpp_versions=cpp_versions,
            cpp_config=cpp_config,
            release_maya_versions=release_maya_versions,
            viewport_matrix=viewport_matrix,
            visual_manifest=visual_manifest,
            visual_ports=release_visual_ports,
            visual_cases=release_visual_cases,
            include_cpp=has_flag(args, "--with-cpp"),
            verbose=verbose,
        ):
            run_release_gate_command(name, command, results, verbose=verbose)

        for name, command, result_report in release_gate_tier3_commands(
            root=root,
            version=version,
            local_assets_manifest=local_assets_manifest,
            camera_manifest=camera_manifest,
            local_parity_manifest=local_parity_manifest,
            strict_local=strict_local,
        ):
            run_release_gate_command(
                name,
                command,
                results,
                result_report=result_report,
                required_local=True,
                strict_local=strict_local,
                verbose=verbose,
            )

    md_path, json_path = write_release_gate_reports(
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
        format_test_summary(
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
        print("[release_gate] first failure: " f"{release_gate_failure_label(failed[0])}")
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
