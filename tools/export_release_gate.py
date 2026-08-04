#!/usr/bin/env python
"""Orchestrate the reproducible v0.7 export release gate.

The gate keeps the release evidence in one bounded JSON/Markdown summary.  It
records every requested step, including explicit ``not_run`` entries, so a
green summary cannot hide an omitted Maya version, GUI run, fail-fixture
matrix, or external MMD-Anim check.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping

from tests.common.maya_location import mayapy as resolve_mayapy


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
MAX_OUTPUT = 1800
MAYA_VERSIONS = ("2024", "2026")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _require_build_path(value: str | Path, label: str) -> Path:
    """Resolve an output path and reject paths outside ``build/``."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{label} must resolve under {BUILD_ROOT}: {path}")
    return path


def _bounded(text: str | None) -> str:
    """Keep subprocess evidence bounded while retaining the useful tail."""
    value = str(text or "")
    if len(value) <= MAX_OUTPUT:
        return value
    return "..." + value[-(MAX_OUTPUT - 3) :]


def _sha256(path: Path) -> str:
    """Hash one report/artifact file for the release summary."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Run one command and return bounded, deterministic evidence."""
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        status = "pass" if completed.returncode == 0 else "fail"
        return {
            "name": name,
            "status": status,
            "returncode": completed.returncode,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": _bounded(completed.stdout),
            "stderr": _bounded(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "fail",
            "returncode": None,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": _bounded(exc.stdout),
            "stderr": _bounded(exc.stderr),
            "error": f"timeout after {timeout:g}s",
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "fail",
            "returncode": None,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _pytest_command() -> list[str]:
    """Use the current interpreter when pytest is available, otherwise uvx."""
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "pytest"]
    return [sys.executable, "-m", "pytest"]


def _not_run(name: str, reason: str, command: list[str] | None = None) -> dict[str, Any]:
    """Record an explicit omitted step."""
    return {
        "name": name,
        "status": "not_run",
        "reason": reason,
        "command": command or [],
    }


def _valid_model_data() -> dict[str, Any]:
    """Return the smallest payload accepted by both model validators."""
    return {
        "model_name": "v070-fail-fixture",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
                "bone_weights": [1.0],
            }
        ],
        "faces": [[0, 0, 0]],
        "materials": [{"face_count": 3}],
        "bones": [{"name": "root", "parent_index": -1, "position": [0.0, 0.0, 0.0]}],
    }


class _SpyModelExporter:
    """Writer spy used to prove blocked model cases never reach the writer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def export_pmx_model(self, path: str, data: Any) -> None:
        self.calls.append((path, data))
        Path(path).write_bytes(b"writer-output")

    def export_pmd_model(self, path: str, data: Any) -> None:
        self.calls.append((path, data))
        Path(path).write_bytes(b"writer-output")


class _SpyVmdExporter:
    """Writer spy for VMD fail-closed and warning-ack cases."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def to_vmd_data(self, data: Any) -> Any:
        return data

    def export_vmd_animation(self, path: str, data: Any) -> None:
        self.calls.append((path, data))
        Path(path).write_bytes(b"writer-output")


def _report_evidence(directory: Path) -> dict[str, Any]:
    """Return report artifact presence and hashes for one fail fixture."""
    result: dict[str, Any] = {}
    for name in ("report.json", "report.md"):
        path = directory / name
        result[name] = {
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return result


def _run_fail_fixture_matrix(out_dir: Path) -> dict[str, Any]:
    """Run fatal/lossy, target-preservation, and warning-ack boundaries."""
    from tests.common.maya_stub import install_maya_stub

    install_maya_stub(profile="headless")
    from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
    from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame
    from mmd_tools.validation.export_validator import ExportValidationIssue, ExportValidationReport

    fixtures: list[dict[str, Any]] = []
    report_paths: list[str] = []
    invalid_model = _valid_model_data()
    invalid_model["faces"] = [[0, 0]]
    for export_format in ("pmx", "pmd"):
        case_dir = out_dir / f"invalid-{export_format}"
        case_dir.mkdir(parents=True, exist_ok=True)
        target = case_dir / f"existing.{export_format}"
        target.write_bytes(b"preserve-existing-target")
        before = target.read_bytes()
        writer = _SpyModelExporter()
        result = ExportModelAction(
            pmx_exporter=writer,
            pmd_exporter=writer,
            output_verifier=None,
        ).execute(
            ExportModelRequest(
                str(target),
                {
                    "export_format": export_format,
                    "model_data": invalid_model,
                    "validation_report_dir": str(case_dir / "report"),
                    "validation_report_evidence": {
                        "gate": "V070-EXPORT-RELEASE-GATE-1",
                        "fixture": f"invalid-{export_format}",
                        "writer_expected": "not_called",
                        "target_expected": "preserved",
                    },
                },
            )
        )
        passed = (
            not result.succeeded
            and not writer.calls
            and target.read_bytes() == before
            and result.validation_report is not None
            and result.validation_report.is_blocking
        )
        fixtures.append(
            {
                "name": f"invalid_{export_format}",
                "status": "pass" if passed else "fail",
                "issue_codes": [issue.code for issue in (result.validation_report.issues if result.validation_report else ())],
                "writer_calls": len(writer.calls),
                "target_preserved": target.read_bytes() == before,
                "report": _report_evidence(case_dir / "report"),
            }
        )
        report_paths.append(str(case_dir / "report" / "report.json"))

    invalid_vmd = VmdData()
    frame = VmdBoneFrame()
    frame.bone_name = "root"
    frame.rotation = (0.0, 0.0, 0.0, 0.0)
    invalid_vmd.bone_frames.append(frame)
    vmd_dir = out_dir / "invalid-vmd"
    vmd_dir.mkdir(parents=True, exist_ok=True)
    vmd_target = vmd_dir / "existing.vmd"
    vmd_target.write_bytes(b"preserve-existing-target")
    vmd_before = vmd_target.read_bytes()
    vmd_writer = _SpyVmdExporter()
    vmd_result = ExportVmdAction(exporter=vmd_writer, output_verifier=None).execute(
        ExportVmdRequest(
            str(vmd_target),
            {
                "vmd_mode": "C",
                "validation_report_dir": str(vmd_dir / "report"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "invalid-vmd-quaternion",
                    "writer_expected": "not_called",
                    "target_expected": "preserved",
                },
            },
            animation_data=invalid_vmd,
        )
    )
    vmd_passed = (
        not vmd_result.succeeded
        and not vmd_writer.calls
        and vmd_target.read_bytes() == vmd_before
        and vmd_result.validation_report is not None
        and vmd_result.validation_report.is_blocking
    )
    fixtures.append(
        {
            "name": "invalid_vmd_quaternion",
            "status": "pass" if vmd_passed else "fail",
            "issue_codes": [issue.code for issue in (vmd_result.validation_report.issues if vmd_result.validation_report else ())],
            "writer_calls": len(vmd_writer.calls),
            "target_preserved": vmd_target.read_bytes() == vmd_before,
            "report": _report_evidence(vmd_dir / "report"),
        }
    )
    report_paths.append(str(vmd_dir / "report" / "report.json"))

    warning_dir = out_dir / "warning-ack"
    warning_dir.mkdir(parents=True, exist_ok=True)

    def warning_validator(_data: Any, _export_format: str) -> ExportValidationReport:
        return ExportValidationReport(
            _export_format,
            (ExportValidationIssue("PMD_TEXTURES_UNSUPPORTED", "warning", False, "textures", "fixture warning"),),
        )

    warning_writer = _SpyModelExporter()
    warning_target = warning_dir / "warning.pmx"
    first = ExportModelAction(
        pmx_exporter=warning_writer,
        pmd_exporter=warning_writer,
        output_verifier=None,
        validator=warning_validator,
    ).execute(
        ExportModelRequest(
            str(warning_target),
            {
                "export_format": "pmx",
                "model_data": _valid_model_data(),
                "validation_report_dir": str(warning_dir / "report-no-ack"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "warning-ack-boundary",
                    "ack_expected": "required",
                },
            },
        )
    )
    second = ExportModelAction(
        pmx_exporter=warning_writer,
        pmd_exporter=warning_writer,
        output_verifier=None,
        validator=warning_validator,
    ).execute(
        ExportModelRequest(
            str(warning_target),
            {
                "export_format": "pmx",
                "model_data": _valid_model_data(),
                "ack_warnings": True,
                "validation_report_dir": str(warning_dir / "report-ack"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "warning-ack-boundary",
                    "ack_expected": "accepted",
                },
            },
        )
    )
    warning_passed = (
        not first.succeeded
        and first.validation_report is not None
        and first.validation_report.requires_warning_ack
        and len(warning_writer.calls) == 1
        and second.succeeded
    )
    fixtures.append(
        {
            "name": "warning_ack_boundary",
            "status": "pass" if warning_passed else "fail",
            "first_succeeded": first.succeeded,
            "second_succeeded": second.succeeded,
            "writer_calls": len(warning_writer.calls),
            "first_issue_codes": [issue.code for issue in (first.validation_report.issues if first.validation_report else ())],
            "second_issue_codes": [issue.code for issue in (second.validation_report.issues if second.validation_report else ())],
            "reports": {
                "no_ack": _report_evidence(warning_dir / "report-no-ack"),
                "ack": _report_evidence(warning_dir / "report-ack"),
            },
        }
    )
    report_paths.extend(
        [
            str(warning_dir / "report-no-ack" / "report.json"),
            str(warning_dir / "report-ack" / "report.json"),
        ]
    )
    return {
        "status": "pass" if all(fixture["status"] == "pass" for fixture in fixtures) else "fail",
        "fixtures": fixtures,
        "report_paths": report_paths,
    }


def _load_json(path: Path) -> dict[str, Any]:
    """Read one required JSON gate artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_maya_probe_report(
    step: dict[str, Any],
    report_path: Path,
    expected_version: str,
) -> list[Path]:
    """Fail closed unless the current Maya probe proves every required case."""
    if step["status"] != "pass":
        return []
    if not report_path.is_file():
        step["status"] = "fail"
        step["error"] = f"Maya probe did not write {report_path}"
        return []
    try:
        report = _load_json(report_path)
    except (OSError, ValueError, TypeError) as exc:
        step["status"] = "fail"
        step["error"] = f"invalid Maya probe report: {type(exc).__name__}: {exc}"
        return []

    cases = report.get("cases")
    by_format = (
        {case.get("format"): case for case in cases if isinstance(case, dict)}
        if isinstance(cases, list)
        else {}
    )
    required_formats = {"pmx", "pmd", "vmd"}
    failures = []
    if report.get("gate") != "V070-EXPORT-RELEASE-GATE-1":
        failures.append(f"gate={report.get('gate')!r}")
    if str(report.get("maya_version")) != expected_version:
        failures.append(f"maya_version={report.get('maya_version')!r}")
    if report.get("status") != "pass":
        failures.append(f"status={report.get('status')!r}")
    if set(by_format) != required_formats:
        failures.append(f"formats={sorted(by_format)}")
    for export_format in sorted(required_formats):
        case = by_format.get(export_format)
        allowed_statuses = {"pass"}
        if export_format == "pmd":
            allowed_statuses.add("policy-reject")
        if not isinstance(case, dict) or case.get("status") not in allowed_statuses:
            failures.append(f"{export_format}.status={case.get('status') if isinstance(case, dict) else None!r}")
            continue
        if export_format == "pmd" and case.get("policy_code") != "PMD_EXPORT_POLICY_REJECT":
            failures.append("pmd.policy_code='PMD_EXPORT_POLICY_REJECT' expected")
        if not case.get("report_json") or not case.get("report_md"):
            failures.append(f"{export_format}.report_pair_missing")

    if failures:
        step["status"] = "fail"
        step["error"] = "Maya probe report failed validation: " + "; ".join(failures)
        return []

    report_paths = []
    for case in by_format.values():
        report_paths.append(Path(case["report_json"]))
    step["probe_report"] = str(report_path)
    step["probe_cases"] = sorted(by_format)
    return report_paths


def _report_consistency_step(report_paths: Iterable[Path]) -> dict[str, Any]:
    """Validate all generated report bundles with the canonical checker."""
    checker_path = ROOT / "tools" / "export_report_consistency.py"
    if not checker_path.is_file():
        return _not_run("report_consistency", "checker not present yet")
    command = [sys.executable, str(checker_path)]
    failures = []
    checked = []
    for report_json in report_paths:
        report_md = report_json.with_suffix(".md")
        if not report_json.is_file() or not report_md.is_file():
            failures.append(f"missing report pair: {report_json}")
            continue
        completed = _run_command(
            f"report_consistency:{report_json.parent.name}",
            command + [str(report_json), str(report_md)],
            timeout=60.0,
        )
        checked.append(completed)
        if completed["status"] != "pass":
            failures.append(str(report_json))
    return {
        "name": "report_consistency",
        "status": "pass" if not failures and checked else "fail",
        "checked": checked,
        "failures": failures,
    }


def _maya_path(version: str) -> Path:
    """Return the mayapy path resolved by the shared Maya-location helper."""
    return Path(resolve_mayapy(version))


def _mmd_anim_provenance(report_path: Path | None) -> dict[str, Any]:
    """Keep executable and checkout provenance distinct in the release summary."""
    provenance: dict[str, Any] = {
        "evidence_status": "not_run" if report_path is None else "unavailable",
        "validation_report": str(report_path) if report_path is not None else None,
        "validation_status": None,
        "cli": None,
        "cli_version": None,
        "expected_cli_version": None,
        "version_match": None,
        "submodule_revision": None,
        "relationship": {
            "cli_version_compared_to": "expected_cli_version",
            "submodule_revision_role": "checked-out source provenance",
            "cli_submodule_direct_comparison": "not_applicable",
        },
    }
    if report_path is None:
        return provenance
    if not report_path.is_file():
        provenance["reason"] = "MMD-Anim validation report was not written"
        return provenance
    try:
        report = _load_json(report_path)
    except (OSError, ValueError, TypeError) as exc:
        provenance["reason"] = f"invalid MMD-Anim validation report: {type(exc).__name__}: {exc}"
        return provenance
    if not isinstance(report, dict):
        provenance["reason"] = "MMD-Anim validation report must be a JSON object"
        return provenance
    provenance.update(
        {
            "evidence_status": "recorded",
            "validation_status": report.get("status"),
            "cli": report.get("cli"),
            "cli_version": report.get("cli_version"),
            "expected_cli_version": report.get("expected_cli_version"),
            "version_match": report.get("version_match"),
            "submodule_revision": report.get("submodule_revision"),
        }
    )
    return provenance


def build_release_summary(
    *,
    out_dir: Path,
    maya_versions: Iterable[str],
    mmd_anim_cli: str | None,
    skip_gui: bool,
    full_gui: bool,
    skip_focused_tests: bool,
) -> dict[str, Any]:
    """Run all V070 steps and write one release summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    if skip_focused_tests:
        steps.append(_not_run("focused_tests", "--skip-focused-tests was supplied"))
    else:
        focused = [
            "tests/unit/test_export_workflow.py",
            "tests/unit/test_export_model_validation.py",
            "tests/unit/test_pmd_parser.py",
            "tests/unit/test_pmd_export.py",
            "tests/unit/test_vmd_validation.py",
            "tests/unit/test_validation_report_catalog.py",
            "tests/unit/test_validation_report_artifacts.py",
            "tests/unit/test_validation_console.py",
            "tests/unit/test_export_release_gate.py",
            "tests/unit/test_export_scope.py",
            "tests/unit/test_gui_runner.py",
            "tests/unit/test_vmd_scene_collector.py",
        ]
        consistency_tests = ROOT / "tests" / "unit" / "test_export_report_consistency.py"
        if consistency_tests.is_file():
            focused.append(str(consistency_tests.relative_to(ROOT)))
        steps.append(
            _run_command(
                "focused_tests",
                [*_pytest_command(), "-q", *focused],
                timeout=900.0,
            )
        )

    fail_matrix = _run_fail_fixture_matrix(out_dir / "fail-fixtures")
    steps.append({"name": "fail_fixture_matrix", **fail_matrix})

    report_paths: list[Path] = [Path(path) for path in fail_matrix.get("report_paths", [])]
    for version in maya_versions:
        mayapy = _maya_path(version)
        probe_dir = out_dir / f"maya-{version}"
        if not mayapy.is_file():
            steps.append(_not_run(f"maya_probe_{version}", f"mayapy not found: {mayapy}"))
            continue
        probe_report = probe_dir / "maya-probe.json"
        if probe_report.is_file():
            probe_report.unlink()
        probe_step = _run_command(
            f"maya_probe_{version}",
            [
                str(mayapy),
                str(ROOT / "tools" / "export_release_maya_probe.py"),
                "--out-dir",
                str(probe_dir),
            ],
            env={**os.environ, "MAYA_APP_DIR": str(out_dir / f"maya-profile-{version}")},
            timeout=1200.0,
        )
        report_paths.extend(_validate_maya_probe_report(probe_step, probe_report, version))
        steps.append(probe_step)

        if skip_gui:
            steps.append(_not_run(f"gui_tests_{version}", "--skip-gui was supplied"))
        else:
            gui_args = [
                "--maya_version",
                version,
            ]
            if not full_gui:
                gui_args.extend(
                    [
                        "--test_path",
                        "tests/gui",
                        "--test_filter",
                        "tests.gui.guitest_export_tab_gui",
                    ]
                )
            steps.append(
                _run_command(
                    f"gui_export_workflow_{version}" if not full_gui else f"gui_tests_{version}",
                    [sys.executable, str(ROOT / "tests" / "run_gui_tests.py"), *gui_args],
                    timeout=1200.0,
                )
            )

    mmd_report: Path | None = None
    if mmd_anim_cli:
        mmd_report = out_dir / "mmd-anim-validation.json"
        for stale_report in (mmd_report, mmd_report.with_suffix(".md")):
            if stale_report.exists():
                stale_report.unlink()
        steps.append(
            _run_command(
                "mmd_anim_validation",
                [
                    sys.executable,
                    str(ROOT / "tools" / "export_validation_gate.py"),
                    "--cli",
                    mmd_anim_cli,
                    "--strict",
                    "--out",
                    str(mmd_report),
                ],
                timeout=900.0,
            )
        )
    else:
        steps.append(_not_run("mmd_anim_validation", "no --mmd-anim-cli was supplied"))

    steps.append(_report_consistency_step(report_paths))
    unexecuted = [step["name"] for step in steps if step["status"] == "not_run"]
    blockers = [
        {
            "name": step["name"],
            "reason": step.get("error") or step.get("stderr") or step.get("reason") or "step failed",
        }
        for step in steps
        if step["status"] == "fail"
    ]
    mmd_anim_provenance = _mmd_anim_provenance(mmd_report)
    summary = {
        "schema_version": 1,
        "gate": "V070-EXPORT-RELEASE-GATE-1",
        "status": "pass" if not blockers and not unexecuted else "fail",
        "maya_versions": list(maya_versions),
        "coverage": {
            "proven": [
                "PMX/VMD parseable output and PMD import/policy-reject",
                "Maya fresh-import mesh/pose/metadata oracle",
                "fatal fail-closed and warning acknowledgement boundaries",
                "focused ExportTab format/mode UI, button routing, and Validation Console catalog rendering",
                "canonical JSON/Markdown validation-report consistency",
            ],
            "outside_this_gate": [
                "full PMX/PMD material/morph/physics field parity",
                "VMD camera/light/morph and raw interpolation provenance parity",
                "full legacy GUI regression suite",
            ],
        },
        "mmd_anim_provenance": mmd_anim_provenance,
        "steps": steps,
        "unexecuted": unexecuted,
        "blockers": blockers,
    }
    (out_dir / "release-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# V070 Export Release Summary",
        "",
        f"- Status: `{summary['status'].upper()}`",
        f"- Gate: `{summary['gate']}`",
        f"- Maya versions: `{', '.join(summary['maya_versions'])}`",
        "",
        "## MMD-Anim Provenance",
        "",
        f"- Evidence status: `{mmd_anim_provenance['evidence_status']}`",
        f"- Validation report: `{mmd_anim_provenance['validation_report'] or 'not generated'}`",
        f"- Validation status: `{mmd_anim_provenance['validation_status'] or 'unavailable'}`",
        f"- CLI: `{mmd_anim_provenance['cli'] or 'unavailable'}`",
        f"- Observed CLI version: `{mmd_anim_provenance['cli_version'] or 'unavailable'}`",
        f"- Expected CLI version: `{mmd_anim_provenance['expected_cli_version'] or 'not configured'}`",
        f"- CLI version match: `{str(mmd_anim_provenance['version_match']).lower()}`",
        f"- Checked-out submodule revision: `{mmd_anim_provenance['submodule_revision'] or 'unavailable'}`",
        "- Relationship: CLI version is compared only with expected CLI version; "
        "the checked-out submodule revision is separate source provenance and is not directly compared.",
        "",
        "## Steps",
        "",
        "| Step | Status | Evidence |",
        "|---|---|---|",
    ]
    for step in steps:
        evidence = step.get("reason") or step.get("error") or step.get("returncode", "")
        lines.append(f"| `{step['name']}` | `{step['status']}` | {str(evidence).replace('|', '/')} |")
    lines.extend(["", "## Unexecuted", ""])
    if unexecuted:
        lines.extend(f"- `{name}`" for name in unexecuted)
    else:
        lines.append("None.")
    lines.extend(["", "## Blockers", ""])
    if blockers:
        lines.extend(f"- `{item['name']}`: {item['reason']}" for item in blockers)
    else:
        lines.append("None.")
    lines.extend(["", "## Coverage", ""])
    lines.append("Proven by this gate:")
    lines.extend(f"- {item}" for item in summary["coverage"]["proven"])
    lines.append("")
    lines.append("Outside this gate and still required for the public Support Matrix:")
    lines.extend(f"- {item}" for item in summary["coverage"]["outside_this_gate"])
    (out_dir / "release-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the release gate CLI and return non-zero for omitted/failed steps."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="build/release-gate/v070")
    parser.add_argument("--maya", dest="maya_versions", action="append", choices=MAYA_VERSIONS)
    parser.add_argument("--mmd-anim-cli")
    parser.add_argument("--skip-gui", action="store_true")
    parser.add_argument("--full-gui", action="store_true")
    parser.add_argument("--skip-focused-tests", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build_release_summary(
            out_dir=_require_build_path(args.out_dir, "--out-dir"),
            maya_versions=tuple(args.maya_versions or MAYA_VERSIONS),
            mmd_anim_cli=args.mmd_anim_cli,
            skip_gui=args.skip_gui,
            full_gui=args.full_gui,
            skip_focused_tests=args.skip_focused_tests,
        )
    except Exception as exc:
        print(f"Export release gate failed to start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": summary["status"], "unexecuted": summary["unexecuted"]}, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
