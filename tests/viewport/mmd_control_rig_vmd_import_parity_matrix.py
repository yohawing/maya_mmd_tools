"""Host runner for the Maya Control Rig VMD-import parity matrix.

Launches the existing module-mode harness for Maya 2024/2026 in DG, Serial,
and Parallel evaluation.  Child exit code ``1`` is accepted only when the
    child JSON is valid, route parity is green, and any failure is represented
    by an explicit coverage or export gate.  Missing reports, malformed JSON,
    case/version/mode mismatches, and route-parity failures remain fail-closed.

Usage::

    python -m tests.viewport.mmd_control_rig_vmd_import_parity_matrix --dry-run
    python -m tests.viewport.mmd_control_rig_vmd_import_parity_matrix
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
MODULE = "tests.viewport.mmd_control_rig_vmd_import_parity"
VERSIONS = ("2024", "2026")
MODES = ("dg", "serial", "parallel")
CASE_PATHS = {
    "base": (
        ROOT / "tests" / "data" / "mmt_test_model.pmx",
        ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd",
    ),
    "coverage": (
        ROOT / "tests" / "data" / "yw_test_model.pmx",
        ROOT / "tests" / "data" / "yw_test_model_control_rig_vmd.vmd",
    ),
}
CASES = tuple(CASE_PATHS)


def _parse_csv(value: str, allowed: Sequence[str], label: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value).split(",") if item.strip())
    invalid = sorted(set(items) - set(allowed))
    if invalid or not items:
        raise ValueError(f"invalid {label}: {invalid or value!r}; choices={tuple(allowed)!r}")
    return items


def _mayapy(version: str) -> Path:
    override = os.environ.get(f"MAYAPY_{version}")
    if override:
        return Path(override)
    return Path(f"C:/Program Files/Autodesk/Maya{version}/bin/mayapy.exe")


def _child_path(output: Path, case: str = "base", version: str | None = None, mode: str | None = None) -> Path:
    if mode is None:
        # Preserve the pre-case helper contract for local callers/tests.
        mode = version
        version = case
        case = "base"
    return output.with_name(f"{output.stem}_{case}_maya{version}_{mode}{output.suffix or '.json'}")


def _command(
    *,
    mayapy: Path,
    version: str,
    mode: str,
    model: Path,
    motion: Path,
    report: Path,
) -> list[str]:
    return [
        str(mayapy),
        "-m",
        MODULE,
        "--maya",
        str(version),
        "--evaluation-mode",
        mode,
        "--model",
        str(model),
        "--motion",
        str(motion),
        "--out",
        str(report),
    ]


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _remove_stale_report(report_path: Path) -> str | None:
    """Remove one exact child report before launch, returning cleanup errors."""

    try:
        report_path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"stale child report cleanup failed: {exc}"
    return None


def _validate_child(
    payload: Mapping[str, Any],
    *,
    version: str,
    mode: str,
    returncode: int,
    report_path: Path,
    case: str = "base",
    model: Path | None = None,
    motion: Path | None = None,
) -> dict[str, Any]:
    """Validate one child report without changing its thresholds or fixtures."""

    errors: list[str] = []
    payload_model = payload.get("model")
    payload_motion = payload.get("motion")
    if model is not None and (payload_model is None or Path(str(payload_model)).resolve() != model.resolve()):
        errors.append(f"model mismatch for case {case}: expected {model}, got {payload_model!r}")
    if motion is not None and (payload_motion is None or Path(str(payload_motion)).resolve() != motion.resolve()):
        errors.append(f"motion mismatch for case {case}: expected {motion}, got {payload_motion!r}")
    maya_version = str(payload.get("mayaVersion", ""))
    if maya_version.split(".", 1)[0] != version:
        errors.append(f"maya version mismatch: expected {version}, got {maya_version!r}")

    evaluation = _as_mapping(payload.get("evaluationMode"))
    if evaluation is None:
        errors.append("missing evaluationMode readback")
    else:
        if str(evaluation.get("requested")) != mode:
            errors.append(f"requested mode mismatch: expected {mode}, got {evaluation.get('requested')!r}")
        if str(evaluation.get("active")) != mode or not bool(evaluation.get("pass")):
            errors.append(f"evaluation mode readback is not green: {dict(evaluation)!r}")

    matrix = _as_mapping(payload.get("requiredRunMatrix"))
    if matrix is None:
        errors.append("missing requiredRunMatrix")
    else:
        requested_modes = tuple(str(item) for item in matrix.get("requestedModes", ()))
        if requested_modes != MODES:
            errors.append(f"requiredRunMatrix modes mismatch: {requested_modes!r}")
        if str(matrix.get("currentMode")) != mode:
            errors.append(f"requiredRunMatrix currentMode mismatch: {matrix.get('currentMode')!r}")
        if not bool(matrix.get("singleModeReport")):
            errors.append("requiredRunMatrix must identify a single-mode report")

    parity = _as_mapping(payload.get("routeParity"))
    route_green = bool(parity and parity.get("pass"))
    if not route_green:
        errors.append("routeParity.pass is not true")
    for key in ("directVsLegacy", "bakedVsLegacy"):
        detail = _as_mapping(parity.get(key)) if parity else None
        if not detail or not bool(detail.get("pass")):
            errors.append(f"routeParity.{key}.pass is not true")

    coverage = payload.get("coverageMissing")
    coverage_missing = sorted(str(item) for item in coverage) if isinstance(coverage, list) else None
    if coverage_missing is None:
        errors.append("coverageMissing must be a JSON list")
    coverage_payload = _as_mapping(payload.get("coverage"))
    coverage_items = _as_mapping(coverage_payload.get("items")) if coverage_payload else None
    coverage_statuses: dict[str, str] = {}
    for category in ("append", "boneMorph", "ikEnable"):
        item = _as_mapping(coverage_items.get(category)) if coverage_items else None
        status = str(item.get("status", "")) if item else ""
        if status not in {"covered", "missing"}:
            errors.append(f"coverage.items.{category}.status must be covered/missing")
        else:
            coverage_statuses[category] = status
            listed = coverage_missing is not None and category in coverage_missing
            if status == "missing" and not listed:
                errors.append(f"missing coverage category {category} must be listed")
            if status == "covered" and listed:
                errors.append(f"covered category {category} must not be listed")

    export_gate = _as_mapping(payload.get("exportFreshImport"))
    export_status = "not_run"
    export_pass = False
    export_attempted = False
    if export_gate is None:
        errors.append("missing exportFreshImport gate")
    else:
        export_status = str(export_gate.get("status", ""))
        export_pass = bool(export_gate.get("pass"))
        export_attempted = bool(export_gate.get("attempted"))
        if export_status not in {"pass", "fail", "not_run"}:
            errors.append(f"invalid exportFreshImport.status: {export_status!r}")
        if export_status == "pass" and (not export_attempted or not export_pass):
            errors.append("exportFreshImport pass requires attempted=true and pass=true")
        if export_status == "fail" and (not export_attempted or export_pass):
            errors.append("exportFreshImport fail requires attempted=true and pass=false")
        if export_status == "not_run" and (export_attempted or export_pass):
            errors.append("exportFreshImport not_run requires attempted=false and pass=false")
        if coverage_missing is not None:
            listed = "exportFreshImport" in coverage_missing
            if export_status == "not_run" and not listed:
                errors.append("not_run exportFreshImport must remain in coverageMissing")
            if export_status in {"pass", "fail"} and listed:
                errors.append("executed exportFreshImport must not remain in coverageMissing")

    coverage_only_nonzero = (
        returncode == 1
        and str(payload.get("status")) == "fail"
        and route_green
        and coverage_missing is not None
        and bool(coverage_missing)
        and export_status != "fail"
    )
    gate_failure_nonzero = (
        returncode == 1
        and str(payload.get("status")) == "fail"
        and route_green
        and export_status == "fail"
    )
    if returncode != 0 and not (coverage_only_nonzero or gate_failure_nonzero):
        errors.append(f"child returned nonzero exit={returncode}")
    if returncode == 0 and str(payload.get("status")) != "pass":
        errors.append(f"child returned zero with status={payload.get('status')!r}")

    return {
        "case": case,
        "version": version,
        "mode": mode,
        "report": str(report_path),
        "status": str(payload.get("status", "missing")),
        "returncode": int(returncode),
        "routeParityPass": route_green,
        "coverageMissing": coverage_missing or [],
        "coverageStatuses": coverage_statuses,
        "exportFreshImportStatus": export_status,
        "exportFreshImportPass": export_pass,
        "exportFreshImportAttempted": export_attempted,
        "exportFreshImportFirstDivergence": (
            export_gate.get("firstDivergence") if export_gate is not None else None
        ),
        "coverageOnlyNonzero": coverage_only_nonzero,
        "gateFailureNonzero": gate_failure_nonzero,
        "valid": not errors,
        "errors": errors,
    }


def _run_one(
    *,
    case: str,
    version: str,
    mode: str,
    model: Path,
    motion: Path,
    output: Path,
    timeout: float,
) -> dict[str, Any]:
    report_path = _child_path(output, case, version, mode)
    mayapy = _mayapy(version)
    command = _command(
        mayapy=mayapy,
        version=version,
        mode=mode,
        model=model,
        motion=motion,
        report=report_path,
    )
    result: dict[str, Any] = {
        "case": case,
        "version": version,
        "mode": mode,
        "command": command,
        "report": str(report_path),
        "mayapy": str(mayapy),
    }
    if not mayapy.is_file():
        result.update({"valid": False, "errors": [f"mayapy not found: {mayapy}"], "returncode": None})
        return result
    cleanup_error = _remove_stale_report(report_path)
    if cleanup_error:
        result.update({"valid": False, "errors": [cleanup_error], "returncode": None})
        return result
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result.update({"valid": False, "errors": [f"child process failed: {exc}"], "returncode": None})
        return result
    log_path = report_path.with_suffix(".log")
    log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    result["log"] = str(log_path)
    result["stdoutTail"] = completed.stdout[-1000:]
    result["returncode"] = completed.returncode
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        result.update({"valid": False, "errors": [f"child report missing: {report_path}"]})
        return result
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        result.update({"valid": False, "errors": [f"malformed child report: {exc}"]})
        return result
    if not isinstance(payload, Mapping):
        result.update({"valid": False, "errors": ["child report root must be a JSON object"]})
        return result
    result.update(
        _validate_child(
            payload,
            case=case,
            version=version,
            mode=mode,
            returncode=completed.returncode,
            report_path=report_path,
            model=model,
            motion=motion,
        )
    )
    return result


def _aggregate(
    runs: Iterable[Mapping[str, Any]],
    *,
    versions: Sequence[str],
    modes: Sequence[str],
    dry_run: bool,
    cases: Sequence[str] = CASES,
) -> dict[str, Any]:
    rows = list(runs)
    expected = {(case, version, mode) for case in cases for version in versions for mode in modes}
    actual = {
        (str(row.get("case", "base")), str(row.get("version")), str(row.get("mode")))
        for row in rows
    }
    complete = actual == expected and len(rows) == len(expected)
    reports_valid = complete and all(bool(row.get("valid")) for row in rows)
    route_green = reports_valid and all(bool(row.get("routeParityPass")) for row in rows)
    evaluation_matrix_complete = (
        set(str(case) for case in cases) == set(CASES)
        and set(str(version) for version in versions) == set(VERSIONS)
        and set(str(mode) for mode in modes) == set(MODES)
    )
    evaluation_green = evaluation_matrix_complete and route_green
    coverage_union_set = {item for row in rows for item in row.get("coverageMissing", [])}
    export_statuses = [str(row.get("exportFreshImportStatus", "not_run")) for row in rows]
    if not rows or any(status == "fail" for status in export_statuses):
        export_status = "fail" if rows and any(status == "fail" for status in export_statuses) else "not_run"
    elif all(status == "pass" for status in export_statuses):
        export_status = "pass"
    elif all(status == "not_run" for status in export_statuses):
        export_status = "not_run"
    else:
        export_status = "fail"
    export_failures = [
        {
            "case": str(row.get("case", "base")),
            "version": str(row.get("version")),
            "mode": str(row.get("mode")),
            "firstDivergence": row.get("exportFreshImportFirstDivergence"),
        }
        for row in rows
        if str(row.get("exportFreshImportStatus", "not_run")) == "fail"
    ]
    coverage_categories = {}
    for category in ("append", "boneMorph", "ikEnable"):
        covered_cases = []
        partial_cases = []
        for case in cases:
            case_rows = [row for row in rows if str(row.get("case", "base")) == str(case)]
            case_expected = {(str(case), str(version), str(mode)) for version in versions for mode in modes}
            case_actual = {
                (str(row.get("case", "base")), str(row.get("version")), str(row.get("mode")))
                for row in case_rows
            }
            covered_count = sum(
                1
                for row in case_rows
                if bool(row.get("valid"))
                and str(row.get("coverageStatuses", {}).get(category)) == "covered"
            )
            if case_actual == case_expected and covered_count == len(case_expected):
                covered_cases.append(str(case))
            elif covered_count:
                partial_cases.append(str(case))
        if covered_cases:
            coverage_categories[category] = {
                "status": "covered",
                "cases": sorted(covered_cases),
                "partialCases": sorted(partial_cases),
            }
            coverage_union_set.discard(category)
        elif partial_cases:
            coverage_categories[category] = {
                "status": "partial",
                "cases": [],
                "partialCases": sorted(partial_cases),
            }
            coverage_union_set.add(category)
        else:
            coverage_categories[category] = {"status": "missing", "cases": [], "partialCases": []}
            coverage_union_set.add(category)
    # The complete case/version/mode matrix is the evidence for this coverage item.  Once
    # every expected run has a green route parity, do not leave the satisfied
    # ``evaluationModes`` marker in the aggregate missing union.  All other
    # missing/unaudited categories remain fail-closed and untouched.
    if evaluation_green:
        coverage_union_set.discard("evaluationModes")
    if export_status == "pass":
        coverage_union_set.discard("exportFreshImport")
    coverage_union = sorted(coverage_union_set)
    return {
            "status": "dry_run" if dry_run else (
            "pass"
            if route_green and reports_valid and export_status == "pass" and not coverage_union
            else "fail"
        ),
        "routeParity": {"pass": route_green, "runCount": len(rows), "expectedCount": len(expected)},
        "evaluationModes": {
            "status": "pass" if evaluation_green else ("partial" if route_green else "fail"),
            "pass": evaluation_green,
            "coveredRuns": sorted(
                [f"{c}/maya{v}/{m}" for c, v, m in actual if (c, v, m) in expected]
            ),
        },
        "coverage": {
            "evaluationModes": "pass" if evaluation_green else ("partial" if route_green else "fail"),
            "append": coverage_categories["append"]["status"],
            "appendCases": coverage_categories["append"]["cases"],
            "appendPartialCases": coverage_categories["append"]["partialCases"],
            "boneMorph": coverage_categories["boneMorph"]["status"],
            "boneMorphCases": coverage_categories["boneMorph"]["cases"],
            "boneMorphPartialCases": coverage_categories["boneMorph"]["partialCases"],
            "ikEnable": coverage_categories["ikEnable"]["status"],
            "ikEnableCases": coverage_categories["ikEnable"]["cases"],
            "ikEnablePartialCases": coverage_categories["ikEnable"]["partialCases"],
            "externalOracle": "not_run",
            "exportFreshImport": export_status,
            "exportFreshImportFailures": export_failures,
            "coverageMissingUnion": coverage_union,
        },
        "runs": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=None, help="comma-separated parity cases (default: base,coverage)")
    parser.add_argument("--versions", default=",".join(VERSIONS), help="comma-separated Maya versions")
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated evaluation modes")
    parser.add_argument("--model", default=None, help="single-case model override (legacy CLI compatibility)")
    parser.add_argument("--motion", default=None, help="single-case motion override (legacy CLI compatibility)")
    parser.add_argument("--out", default=str(ROOT / "build" / "reports" / "mmd_control_rig_vmd_import_parity_matrix.json"))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true", help="print all case/version/mode commands and do not launch Maya")
    args = parser.parse_args(argv)
    try:
        versions = _parse_csv(args.versions, VERSIONS, "versions")
        modes = _parse_csv(args.modes, MODES, "modes")
        if args.cases is None:
            cases = ("custom",) if args.model or args.motion else CASES
        else:
            cases = _parse_csv(args.cases, CASES, "cases")
    except ValueError as exc:
        parser.error(str(exc))
    if (args.model is None) != (args.motion is None):
        parser.error("--model and --motion must be supplied together")
    if args.cases is not None and (args.model is not None or args.motion is not None) and len(cases) != 1:
        parser.error("--model/--motion overrides require exactly one --cases entry")
    case_specs = {}
    for case in cases:
        if case == "custom":
            case_specs[case] = (Path(args.model).resolve(), Path(args.motion).resolve())
        elif args.model is not None:
            case_specs[case] = (Path(args.model).resolve(), Path(args.motion).resolve())
        else:
            case_specs[case] = tuple(path.resolve() for path in CASE_PATHS[case])
    output = Path(args.out).resolve()
    runs = []
    for case in cases:
        model, motion = case_specs[case]
        for version in versions:
            for mode in modes:
                if args.dry_run:
                    command = _command(
                        mayapy=_mayapy(version),
                        version=version,
                        mode=mode,
                        model=model,
                        motion=motion,
                        report=_child_path(output, case, version, mode),
                    )
                    runs.append({"case": case, "version": version, "mode": mode, "command": command, "valid": False})
                else:
                    runs.append(
                        _run_one(
                            case=case,
                            version=version,
                            mode=mode,
                            model=model,
                            motion=motion,
                            output=output,
                            timeout=args.timeout,
                        )
                    )
    aggregate = _aggregate(runs, cases=cases, versions=versions, modes=modes, dry_run=args.dry_run)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": aggregate["status"], "report": str(output), "routeParity": aggregate["routeParity"], "evaluationModes": aggregate["evaluationModes"]}, ensure_ascii=False))
    return 0 if aggregate["status"] in {"pass", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
