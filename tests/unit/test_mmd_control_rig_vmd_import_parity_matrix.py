"""Pure host-runner contracts for the Control Rig VMD parity matrix."""

from __future__ import annotations

from types import SimpleNamespace

from tests.viewport import mmd_control_rig_vmd_import_parity_matrix as matrix


def _green_run(version: str, mode: str, *, coverage=None, export_status="pass") -> dict:
    return {
        "version": version,
        "mode": mode,
        "valid": True,
        "routeParityPass": True,
        "coverageMissing": list(coverage or ("evaluationModes", "append", "externalOracle")),
        "exportFreshImportStatus": export_status,
    }


def test_six_run_green_satisfies_evaluation_mode_coverage_only():
    rows = [_green_run(version, mode) for version in matrix.VERSIONS for mode in matrix.MODES]

    aggregate = matrix._aggregate(rows, versions=matrix.VERSIONS, modes=matrix.MODES, dry_run=False)

    assert aggregate["routeParity"]["pass"] is True
    assert aggregate["evaluationModes"]["pass"] is True
    assert "evaluationModes" not in aggregate["coverage"]["coverageMissingUnion"]
    assert "append" in aggregate["coverage"]["coverageMissingUnion"]
    assert "externalOracle" in aggregate["coverage"]["coverageMissingUnion"]
    assert aggregate["coverage"]["exportFreshImport"] == "pass"


def test_export_gate_red_is_not_coverage_only():
    rows = [
        _green_run(version, mode, coverage=("append", "externalOracle"), export_status="fail")
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(rows, versions=matrix.VERSIONS, modes=matrix.MODES, dry_run=False)

    assert aggregate["status"] == "fail"
    assert aggregate["routeParity"]["pass"] is True
    assert aggregate["coverage"]["exportFreshImport"] == "fail"
    assert "exportFreshImport" not in aggregate["coverage"]["coverageMissingUnion"]


def test_export_gate_not_run_remains_missing():
    rows = [
        _green_run(
            version,
            mode,
            coverage=("evaluationModes", "append", "externalOracle", "exportFreshImport"),
            export_status="not_run",
        )
        for version in matrix.VERSIONS
        for mode in matrix.MODES
    ]

    aggregate = matrix._aggregate(rows, versions=matrix.VERSIONS, modes=matrix.MODES, dry_run=False)

    assert aggregate["coverage"]["exportFreshImport"] == "not_run"
    assert "exportFreshImport" in aggregate["coverage"]["coverageMissingUnion"]


def test_validate_child_accepts_executed_red_export_as_gate_failure(tmp_path):
    payload = {
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
        version="2026",
        mode="dg",
        returncode=1,
        report_path=tmp_path / "child.json",
    )

    assert result["valid"] is True
    assert result["coverageOnlyNonzero"] is False
    assert result["gateFailureNonzero"] is True
    assert result["exportFreshImportStatus"] == "fail"
    assert result["errors"] == []


def test_incomplete_matrix_keeps_evaluation_mode_coverage_missing():
    rows = [_green_run("2024", "dg")]

    aggregate = matrix._aggregate(rows, versions=matrix.VERSIONS, modes=matrix.MODES, dry_run=False)

    assert aggregate["routeParity"]["pass"] is False
    assert aggregate["evaluationModes"]["pass"] is False
    assert "evaluationModes" in aggregate["coverage"]["coverageMissingUnion"]


def test_stale_child_report_is_removed_before_launch(tmp_path, monkeypatch):
    output = tmp_path / "aggregate.json"
    child = matrix._child_path(output, "2024", "dg")
    child.write_text('{"status":"fail","routeParity":{"pass":true}}', encoding="utf-8")
    mayapy = tmp_path / "mayapy.exe"
    mayapy.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(matrix, "_mayapy", lambda _version: mayapy)

    def fake_run(*_args, **_kwargs):
        assert not child.exists()
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(matrix.subprocess, "run", fake_run)
    result = matrix._run_one(
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
    child = matrix._child_path(output, "2024", "dg")
    child.mkdir()
    mayapy = tmp_path / "mayapy.exe"
    mayapy.write_text("stub", encoding="utf-8")

    monkeypatch.setattr(matrix, "_mayapy", lambda _version: mayapy)
    result = matrix._run_one(
        version="2024",
        mode="dg",
        model=tmp_path / "model.pmx",
        motion=tmp_path / "motion.vmd",
        output=output,
        timeout=1.0,
    )

    assert result["valid"] is False
    assert any("cleanup failed" in error for error in result["errors"])
