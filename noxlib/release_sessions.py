"""Session implementations for release-only native verification gates."""

from __future__ import annotations

import sys
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
