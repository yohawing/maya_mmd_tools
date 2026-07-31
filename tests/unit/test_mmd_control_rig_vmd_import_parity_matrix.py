"""Pure host-runner contracts for the Control Rig VMD parity matrix."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.viewport import mmd_control_rig_vmd_import_parity_matrix as matrix


def _green_run(version: str, mode: str, *, case="base", coverage=None, export_status="pass", coverage_statuses=None) -> dict:
    return {
        "case": case,
        "version": version,
        "mode": mode,
        "valid": True,
        "routeParityPass": True,
        "coverageMissing": list(coverage or ("evaluationModes", "append", "externalOracle")),
        "coverageStatuses": dict(coverage_statuses or {}),
        "exportFreshImportStatus": export_status,
    }


def _oracle_run(version: str, mode: str, *, case="base", status="pass") -> dict:
    coverage = ("evaluationModes", "append") if status != "not_run" else ("evaluationModes", "append", "externalOracle")
    route_status = {
        "legacy": {"status": status, "attempted": status != "not_run", "pass": status == "pass"},
        "controlRigDirect": {"status": status, "attempted": status != "not_run", "pass": status == "pass"},
    }
    row = _green_run(
        version,
        mode,
        case=case,
        coverage=coverage,
        coverage_statuses={"append": "covered"},
    )
    row.update(
        {
            "externalOracleStatus": status,
            "externalOraclePass": status == "pass",
            "externalOracleAttempted": status != "not_run",
            "externalOracleRoutes": {name: value["status"] for name, value in route_status.items()},
            "externalOracleFailures": route_status if status == "fail" else None,
        }
    )
    return row


def test_full_two_case_matrix_satisfies_evaluation_mode_coverage_only():
    rows = [
        _green_run(version, mode, case=case)
        for case in matrix.CASES
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["routeParity"]["pass"] is True
    assert aggregate["evaluationModes"]["pass"] is True
    assert "evaluationModes" not in aggregate["coverage"]["coverageMissingUnion"]
    assert "append" in aggregate["coverage"]["coverageMissingUnion"]
    assert "externalOracle" in aggregate["coverage"]["coverageMissingUnion"]
    assert aggregate["coverage"]["exportFreshImport"] == "pass"


def test_export_gate_red_is_not_coverage_only():
    rows = [
        _green_run(version, mode, case=case, coverage=("append", "externalOracle"), export_status="fail")
        for case in matrix.CASES
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["status"] == "fail"
    assert aggregate["routeParity"]["pass"] is True
    assert aggregate["coverage"]["exportFreshImport"] == "fail"
    assert "exportFreshImport" not in aggregate["coverage"]["coverageMissingUnion"]


def test_external_oracle_failure_is_an_independent_gate():
    rows = [
        _oracle_run(version, mode, case=case, status="fail")
        for case in matrix.CASES
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["routeParity"]["pass"] is True
    assert aggregate["coverage"]["externalOracle"] == "fail"
    assert aggregate["coverage"]["externalOracleFailures"]
    assert "externalOracle" not in aggregate["coverage"]["coverageMissingUnion"]
    assert aggregate["status"] == "fail"


def test_export_gate_not_run_remains_missing():
    rows = [
        _green_run(
            version,
            mode,
            case=case,
            coverage=("evaluationModes", "append", "externalOracle", "exportFreshImport"),
            export_status="not_run",
        )
        for case in matrix.CASES
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["coverage"]["exportFreshImport"] == "not_run"
    assert "exportFreshImport" in aggregate["coverage"]["coverageMissingUnion"]


def test_case_coverage_is_recorded_by_explicit_case():
    rows = []
    for case in matrix.CASES:
        for version in matrix.VERSIONS:
            for mode in matrix.MODES:
                if case in {"coverage", "boneMorph"}:
                    statuses = {"append": "covered", "boneMorph": "covered", "ikEnable": "covered"}
                    coverage = ("evaluationModes", "externalOracle")
                else:
                    statuses = {"append": "missing", "boneMorph": "missing", "ikEnable": "missing"}
                    coverage = ("evaluationModes", "append", "boneMorph", "ikEnable", "externalOracle")
                rows.append(
                    _green_run(
                        version,
                        mode,
                        case=case,
                        coverage=coverage,
                        coverage_statuses=statuses,
                    )
                )

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["coverage"]["append"] == "covered"
    assert aggregate["coverage"]["appendCases"] == ["boneMorph", "coverage"]
    assert aggregate["coverage"]["boneMorphCases"] == ["boneMorph", "coverage"]
    assert aggregate["coverage"]["ikEnableCases"] == ["boneMorph", "coverage"]
    assert "append" not in aggregate["coverage"]["coverageMissingUnion"]


def test_case_export_failure_preserves_first_failing_case():
    rows = []
    for case in matrix.CASES:
        for version in matrix.VERSIONS:
            for mode in matrix.MODES:
                rows.append(
                    _green_run(
                        version,
                        mode,
                        case=case,
                        coverage=("evaluationModes", "externalOracle"),
                        export_status="fail" if case == "base" else "pass",
                    )
                )
                rows[-1]["exportFreshImportFirstDivergence"] = {"category": f"{case}-first"}

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["status"] == "fail"
    assert aggregate["coverage"]["exportFreshImport"] == "fail"
    assert aggregate["coverage"]["exportFreshImportFailures"][0]["case"] == "base"


def test_partial_case_coverage_is_not_promoted_to_covered():
    rows = []
    for version in matrix.VERSIONS:
        for mode in matrix.MODES:
            rows.append(
                _green_run(
                    version,
                    mode,
                    case="coverage",
                    coverage=("evaluationModes", "externalOracle"),
                    coverage_statuses={"append": "covered", "boneMorph": "missing", "ikEnable": "missing"},
                )
            )
    rows[0]["valid"] = False
    rows[0]["coverageStatuses"]["append"] = "covered"
    rows.extend(
        _green_run(
            version,
            mode,
            case="base",
            coverage=("evaluationModes", "append", "boneMorph", "ikEnable", "externalOracle"),
            coverage_statuses={"append": "missing", "boneMorph": "missing", "ikEnable": "missing"},
        )
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    )

    aggregate = matrix._aggregate(
        rows,
        cases=matrix.CASES,
        versions=matrix.VERSIONS,
        modes=matrix.MODES,
        dry_run=False,
    )

    assert aggregate["coverage"]["append"] == "partial"
    assert aggregate["coverage"]["appendCases"] == []
    assert aggregate["coverage"]["appendPartialCases"] == ["coverage"]
    assert "append" in aggregate["coverage"]["coverageMissingUnion"]


def test_default_dry_run_expands_two_cases(tmp_path):
    output = tmp_path / "matrix.json"

    assert matrix.main(["--dry-run", "--out", str(output)]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "dry_run"
    assert len(report["runs"]) == len(matrix.CASES) * len(matrix.VERSIONS) * len(matrix.MODES)
    assert {row["case"] for row in report["runs"]} == set(matrix.CASES)
    assert all(
        any(f"_{case}_maya" in row["command"][-1] for case in matrix.CASES)
        for row in report["runs"]
    )


def test_custom_model_motion_invocation_is_explicitly_rejected(tmp_path):
    model = tmp_path / "custom.pmx"
    motion = tmp_path / "custom.vmd"
    with pytest.raises(SystemExit) as error:
        matrix.main(
            [
                "--dry-run",
                "--model",
                str(model),
                "--motion",
                str(motion),
            ]
        )
    assert error.value.code == 2


def test_validate_child_accepts_executed_red_export_as_gate_failure(tmp_path):
    payload = {
        "model": str(tmp_path / "model.pmx"),
        "motion": str(tmp_path / "motion.vmd"),
        "mayaVersion": "2026",
        "evaluationMode": {"requested": "dg", "active": "dg", "pass": True},
        "requiredRunMatrix": {
            "requestedModes": list(matrix.MODES),
            "currentMode": "dg",
            "singleModeReport": True,
        },
        "routeParity": {
            "pass": True,
            "directVsLegacy": {"pass": True},
            "bakedVsLegacy": {"pass": True},
        },
        "coverageMissing": ["append", "externalOracle"],
        "coverage": {
            "items": {
                "append": {"status": "missing"},
                "boneMorph": {"status": "covered"},
                "ikEnable": {"status": "covered"},
                "externalOracle": {"status": "missing"},
            }
        },
        "externalOracle": {
            "identity": "mmd-anim-mesh-oracle",
            "status": "not_run",
            "attempted": False,
            "pass": False,
            "routes": {
                "legacy": {"status": "not_run", "attempted": False, "pass": False},
                "controlRigDirect": {"status": "not_run", "attempted": False, "pass": False},
            },
        },
        "status": "fail",
        "exportFreshImport": {
            "attempted": True,
            "status": "fail",
            "pass": False,
            "firstDivergence": {"category": "export_fresh_bone_interpolation"},
        },
    }

    result = matrix._validate_child(
        payload,
        case="base",
        version="2026",
        mode="dg",
        returncode=1,
        report_path=tmp_path / "child.json",
        model=tmp_path / "model.pmx",
        motion=tmp_path / "motion.vmd",
    )

    assert result["valid"] is True
    assert result["coverageOnlyNonzero"] is False
    assert result["gateFailureNonzero"] is True
    assert result["exportFreshImportStatus"] == "fail"
    assert result["errors"] == []


def test_validate_child_accepts_executed_red_external_oracle_as_gate_failure(tmp_path):
    payload = {
        "model": str(tmp_path / "model.pmx"),
        "motion": str(tmp_path / "motion.vmd"),
        "mayaVersion": "2026",
        "evaluationMode": {"requested": "dg", "active": "dg", "pass": True},
        "requiredRunMatrix": {
            "requestedModes": list(matrix.MODES),
            "currentMode": "dg",
            "singleModeReport": True,
        },
        "routeParity": {
            "pass": True,
            "directVsLegacy": {"pass": True},
            "bakedVsLegacy": {"pass": True},
        },
        "coverageMissing": ["append"],
        "coverage": {
            "items": {
                "append": {"status": "missing"},
                "boneMorph": {"status": "covered"},
                "ikEnable": {"status": "covered"},
                "externalOracle": {"status": "covered", "gatePass": False},
            }
        },
        "externalOracle": {
            "identity": "mmd-anim-mesh-oracle",
            "status": "fail",
            "attempted": True,
            "pass": False,
            "runtimeProvenance": {
                "status": "ready",
                "runtimePath": "C:/runtime/mmd_runtime_ffi.dll",
                "runtimeSha256": "a" * 64,
                "runtimeAbi": 3,
            },
            "routes": {
                "legacy": {"status": "fail", "attempted": True, "pass": False},
                "controlRigDirect": {"status": "fail", "attempted": True, "pass": False},
            },
        },
        "status": "fail",
        "exportFreshImport": {"attempted": True, "status": "pass", "pass": True},
    }

    result = matrix._validate_child(
        payload,
        case="base",
        version="2026",
        mode="dg",
        returncode=1,
        report_path=tmp_path / "child.json",
        model=tmp_path / "model.pmx",
        motion=tmp_path / "motion.vmd",
    )

    assert result["valid"] is True
    assert result["oracleGateFailureNonzero"] is True
    assert result["coverageOnlyNonzero"] is False
    assert result["routeParityPass"] is True
    assert result["externalOracleStatus"] == "fail"
    assert result["errors"] == []


def test_incomplete_matrix_keeps_evaluation_mode_coverage_missing():
    rows = [_green_run("2024", "dg")]

    aggregate = matrix._aggregate(rows, versions=matrix.VERSIONS, modes=matrix.MODES, dry_run=False)

    assert aggregate["routeParity"]["pass"] is False
    assert aggregate["evaluationModes"]["pass"] is False
    assert "evaluationModes" in aggregate["coverage"]["coverageMissingUnion"]


def test_stale_child_report_is_removed_before_launch(tmp_path, monkeypatch):
    output = tmp_path / "aggregate.json"
    child = matrix._child_path(output, "base", "2024", "dg")
    child.write_text('{"status":"fail","routeParity":{"pass":true}}', encoding="utf-8")
    mayapy = tmp_path / "mayapy.exe"
    mayapy.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(matrix, "_mayapy", lambda _version: mayapy)

    def fake_run(*_args, **_kwargs):
        assert not child.exists()
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(matrix.subprocess, "run", fake_run)
    result = matrix._run_one(
        case="base",
        version="2024",
        mode="dg",
        model=tmp_path / "model.pmx",
        motion=tmp_path / "motion.vmd",
        output=output,
        timeout=1.0,
    )

    assert result["valid"] is False
    assert any("report missing" in error for error in result["errors"])


def test_stale_child_report_cleanup_failure_is_fail_closed(tmp_path, monkeypatch):
    output = tmp_path / "aggregate.json"
    child = matrix._child_path(output, "base", "2024", "dg")
    child.mkdir()
    mayapy = tmp_path / "mayapy.exe"
    mayapy.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(matrix, "_mayapy", lambda _version: mayapy)
    result = matrix._run_one(
        case="base",
        version="2024",
        mode="dg",
        model=tmp_path / "model.pmx",
        motion=tmp_path / "motion.vmd",
        output=output,
        timeout=1.0,
    )

    assert result["valid"] is False
    assert any("cleanup failed" in error for error in result["errors"])
