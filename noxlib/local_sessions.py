"""Session implementations for optional local-asset and parity gates."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def run_local_assets_check(
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
    normalize_local_gate_report,
) -> None:
    """Run local PMX/VMD asset smoke checks from an optional manifest."""
    args = list(posargs)
    version = option(args, "--maya", default_maya_version)
    manifest = Path(option(args, "--manifest", "local-assets-manifest.json"))
    strict = has_flag(args, "--strict-local")
    out_json = require_build_path(
        session,
        option(args, "--out-json", "build/reports/local_assets_check.json"),
        "--out-json",
    )
    out_md = require_build_path(
        session,
        option(args, "--out-md", "build/reports/local_assets_check.md"),
        "--out-md",
    )

    if not manifest.is_absolute():
        manifest = root / manifest
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

    mayapy_path = mayapy(version)
    env = mayapy_env(mayapy_path, MAYA_VERSION=version)
    command = [
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/local/local_assets_check.py"),
        "--manifest",
        mayapy_arg_path(mayapy_path, manifest),
        "--out-json",
        mayapy_arg_path(mayapy_path, out_json),
        "--out-md",
        mayapy_arg_path(mayapy_path, out_md),
    ]
    if strict:
        command.append("--strict-local")
    session.run(*command, env=env, external=True)
    status = normalize_local_gate_report(out_json, strict, out_md)
    session.log(f"Local assets report: {out_md}")
    session.log(f"Local assets JSON: {out_json}")
    if status == "fail":
        session.error("Local assets check failed")


def run_semistandard_name_audit(
    session,
    *,
    posargs: list[str],
    option,
    root: Path,
    require_build_path,
    python_executable: str = sys.executable,
) -> None:
    """Audit local PMX/VMD assets for semistandard bone-name conversion gaps."""
    args = list(posargs)
    out_json = require_build_path(
        session,
        option(args, "--out-json", "build/reports/semistandard_name_audit.json"),
        "--out-json",
    )
    out_md = require_build_path(
        session,
        option(args, "--out-md", "build/reports/semistandard_name_audit.md"),
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
                value = str(path.resolve() if path.is_absolute() else (root / path).resolve())
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
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root), env.get("PYTHONPATH", "")]))
    session.run(
        python_executable,
        "tests/local/semistandard_name_audit.py",
        *passthrough,
        env=env,
        external=True,
    )
    session.log(f"Semistandard name audit report: {out_md}")
    session.log(f"Semistandard name audit JSON: {out_json}")


def run_local_camera_motion_oracle(
    session,
    *,
    posargs: list[str],
    option,
    default_maya_version: str,
    root: Path,
    mayapy,
    mayapy_env,
    mayapy_script,
    maya_process_path,
    convert_mayapy_path_options,
    copy_parity_vmd,
) -> None:
    """Run local-only GoldenOracle camera-motion checks through mayapy."""
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
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
    passthrough = copy_parity_vmd(session, passthrough)

    session.run(
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/local/camera_motion_oracle_runner.py"),
        "--repo-root",
        maya_process_path(mayapy_path, root),
        *convert_mayapy_path_options(
            mayapy_path,
            passthrough,
            {"--manifest", "--out", "--parity-vmd"},
        ),
        env=mayapy_env(mayapy_path),
        external=True,
    )


def run_local_parity(
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
    convert_mayapy_path_options,
) -> None:
    """Run Bake-vs-Rig mesh parity on local non-committed PMX/VMD assets."""
    maya_version = option(posargs, "--maya", default_maya_version)
    mayapy_path = mayapy(maya_version)
    passthrough: list[str] = []
    args = list(posargs)
    manifest = option(args, "--manifest", "")
    out_json = option(args, "--out", "build/reports/local_asset_motion_compare.json")
    if manifest:
        manifest_path = Path(manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        manifest_path = manifest_path.resolve()
        if not manifest_path.exists():
            out_path = require_build_path(session, out_json, "--out")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            md_path = out_path.with_suffix(".md")
            status = "failed" if has_flag(args, "--strict-local") else "skipped"
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
            if has_flag(args, "--strict-local"):
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
        str(mayapy_path),
        mayapy_script(mayapy_path, "tests/viewport/local_asset_motion_compare.py"),
        *convert_mayapy_path_options(mayapy_path, passthrough, {"--out"}),
        env=mayapy_env(mayapy_path, preserve_pythonpath=True),
        external=True,
    )
