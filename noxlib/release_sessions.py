"""Session implementations for release-only native verification gates."""

from __future__ import annotations

import json
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
