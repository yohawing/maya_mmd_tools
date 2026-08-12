"""Pure validation contracts for the checked-in UI coverage inventory."""

import copy
import json
from pathlib import Path

import pytest

from tools.ui_coverage_gate import build_report_from_evidence, validate_manifest, validate_report


MANIFEST_PATH = Path(__file__).resolve().parents[2] / "tools" / "ui_coverage_manifest.json"


def _load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _minimal_manifest():
    return {
        "schema_version": 1,
        "gate_id": "V070-UI-COVERAGE-1",
        "tabs": [
            {"id": tab_id, "selector": "objectName=" + tab_id}
            for tab_id in (
                "import_export",
                "export",
                "info",
                "material",
                "bone",
                "morph",
                "display_pane",
                "physics",
                "settings",
            )
        ],
        "cases": [{"id": "case.one", "required_maya_versions": ["2024", "2026"]}],
        "surfaces": [
            {
                "id": "import_export.surface",
                "tab": "import_export",
                "kind": "button",
                "selector": "objectName=surface",
                "disposition": "not_run",
                "reason_code": "deferred",
                "reason": "Smoke is deferred.",
            }
        ],
        "unmapped_surfaces": [],
    }


def _errors(result):
    return {item["code"] for item in result["errors"]}


def _report(cases, surfaces=None):
    """Build the CLI-compatible aggregate shape used by report-join tests."""
    return {
        "schema_version": 1,
        "gate_id": "V070-UI-COVERAGE-1",
        "cases": cases,
        "surfaces": list(surfaces or []),
    }


def test_checked_in_manifest_is_structurally_valid_and_inventories_all_tabs():
    result = validate_manifest(_load_manifest())
    assert result["valid"]
    assert result["tab_count"] == 9
    assert result["surface_count"] >= 200


def test_missing_tab_fails_closed():
    manifest = _minimal_manifest()
    manifest["tabs"] = manifest["tabs"][:-1]
    result = validate_manifest(manifest)
    assert "missing_tabs" in _errors(result)


def test_duplicate_surface_id_fails_closed():
    manifest = _minimal_manifest()
    manifest["surfaces"].append(copy.deepcopy(manifest["surfaces"][0]))
    result = validate_manifest(manifest)
    assert "duplicate_surface_id" in _errors(result)


def test_duplicate_nonempty_selector_fails_closed():
    manifest = _minimal_manifest()
    duplicate = copy.deepcopy(manifest["surfaces"][0])
    duplicate["id"] = "export.other"
    duplicate["tab"] = "export"
    manifest["surfaces"].append(duplicate)
    result = validate_manifest(manifest)
    assert "duplicate_selector" in _errors(result)


def test_duplicate_attribute_fails_closed():
    manifest = _minimal_manifest()
    manifest["surfaces"][0].pop("selector")
    manifest["surfaces"][0]["attribute"] = "shared_attribute"
    duplicate = copy.deepcopy(manifest["surfaces"][0])
    duplicate["id"] = "export.other"
    duplicate["tab"] = "export"
    manifest["surfaces"].append(duplicate)
    result = validate_manifest(manifest)
    assert "duplicate_attribute" in _errors(result)


def test_unknown_disposition_is_unmapped():
    manifest = _minimal_manifest()
    manifest["surfaces"][0]["disposition"] = "maybe"
    result = validate_manifest(manifest)
    assert "unmapped_disposition" in _errors(result)


def test_qt_case_requires_known_case_and_no_reason_fields():
    manifest = _minimal_manifest()
    surface = manifest["surfaces"][0]
    surface.update({"disposition": "qt_case", "case_id": "case.unknown", "reason": "wrong"})
    result = validate_manifest(manifest)
    assert "unknown_case" in _errors(result)
    assert "invalid_reason_fields" in _errors(result)


def test_non_qt_disposition_requires_reason_fields():
    manifest = _minimal_manifest()
    surface = manifest["surfaces"][0]
    surface.pop("reason")
    result = validate_manifest(manifest)
    assert "invalid_reason_fields" in _errors(result)


def test_structural_validation_does_not_claim_smoke_evidence():
    result = validate_manifest(_load_manifest())
    assert result["valid"]
    assert "evidence_checked" not in result


def _qt_case_manifest():
    manifest = _minimal_manifest()
    manifest["surfaces"][0].pop("reason_code")
    manifest["surfaces"][0].pop("reason")
    manifest["surfaces"][0].update({"disposition": "qt_case", "case_id": "case.one"})
    return manifest


def test_report_unknown_case_fails():
    result = validate_report(
        _minimal_manifest(), _report([{"case_id": "case.unknown"}])
    )
    assert "unknown_report_case" in _errors(result)


def test_report_missing_required_version_evidence_fails():
    result = validate_report(
        _qt_case_manifest(),
        _report(
            [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024"]}],
            [
                {
                    "surface_id": "import_export.surface",
                    "case_id": "case.one",
                    "selector": "objectName=surface",
                    "status": "pass",
                }
            ],
        ),
    )
    assert "missing_required_version_evidence" in _errors(result)


def test_report_blocked_case_fails_even_when_versions_are_present():
    manifest = _qt_case_manifest()
    result = validate_report(
        manifest,
        _report(
            [
                {
                    "case_id": "case.one",
                    "status": "blocked",
                    "maya_versions": ["2024", "2026"],
                }
            ],
            [
                {
                    "surface_id": "import_export.surface",
                    "case_id": "case.one",
                    "selector": "objectName=surface",
                    "status": "blocked",
                }
            ],
        ),
    )
    assert "incomplete_case_evidence" in _errors(result)


@pytest.mark.parametrize("status", ["blocked", "not_run"])
def test_report_incomplete_qt_surface_fails(status):
    manifest = _qt_case_manifest()
    result = validate_report(
        manifest,
        _report(
            [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
            [
                {
                    "surface_id": "import_export.surface",
                    "case_id": "case.one",
                    "selector": "objectName=surface",
                    "status": status,
                }
            ],
        ),
    )
    assert "incomplete_surface_evidence" in _errors(result)


def test_report_selector_mismatch_fails():
    manifest = _qt_case_manifest()
    result = validate_report(
        manifest,
        _report(
            [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
            [
                {
                    "surface_id": "import_export.surface",
                    "case_id": "case.one",
                    "selector": "objectName=wrong",
                    "status": "pass",
                }
            ],
        ),
    )
    assert "selector_mismatch" in _errors(result)


def test_report_green_fixture_requires_case_versions_and_matching_selector():
    manifest = _qt_case_manifest()
    result = validate_report(
        manifest,
        _report(
            [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
            [
                {
                    "surface_id": "import_export.surface",
                    "case_id": "case.one",
                    "selector": "objectName=surface",
                    "status": "pass",
                }
            ],
        ),
    )
    assert result["valid"]


def test_report_missing_case_status_fails_closed():
    manifest = _qt_case_manifest()
    report = _aggregate_from_inventory(manifest, {"case.one": ("2024", "2026")})
    report["cases"][0].pop("status")

    result = validate_report(manifest, report)

    assert "incomplete_case_evidence" in _errors(result)


def test_report_duplicate_case_and_surface_ids_fail_closed():
    manifest = _qt_case_manifest()
    report = _aggregate_from_inventory(manifest, {"case.one": ("2024", "2026")})
    report["cases"].append(copy.deepcopy(report["cases"][0]))
    report["surfaces"].append(copy.deepcopy(report["surfaces"][0]))

    result = validate_report(manifest, report)

    assert "duplicate_report_case" in _errors(result)
    assert "duplicate_report_surface" in _errors(result)


def _aggregate_from_inventory(manifest, versions_by_case):
    """Schema fixture mirroring the four checked-in GUI report families.

    This deliberately exercises only report-join shape; the source paths for
    runtime evidence are declared in the manifest case entries.
    """
    case_ids = sorted(
        {
            surface["case_id"]
            for surface in manifest["surfaces"]
            if surface["disposition"] == "qt_case"
        }
    )
    cases = [
        {
            "case_id": case_id,
            "status": "pass",
            "maya_versions": list(versions_by_case[case_id]),
        }
        for case_id in case_ids
    ]
    surfaces = []
    for surface in manifest["surfaces"]:
        if surface["disposition"] != "qt_case":
            continue
        evidence = {
            "surface_id": surface["id"],
            "case_id": surface["case_id"],
            "status": "pass",
        }
        locator_key = "selector" if "selector" in surface else "attribute"
        evidence[locator_key] = surface[locator_key]
        surfaces.append(evidence)
    return _report(cases, surfaces)


def test_real_gui_case_aggregate_reports_missing_info_2026_evidence():
    manifest = _load_manifest()
    versions = {
        "gui.authoring_signal_smoke": ("2024", "2026"),
        "gui.fileio_safe_routes": ("2024", "2026"),
        "gui.info_undo": ("2024",),
        "gui.settings_side_effects": ("2024", "2026"),
    }
    result = validate_report(manifest, _aggregate_from_inventory(manifest, versions))
    assert "missing_required_version_evidence" in _errors(result)
    assert not any(error["code"] == "missing_surface_evidence" for error in result["errors"])


def test_real_gui_case_aggregate_passes_when_both_maya_versions_are_present():
    manifest = _load_manifest()
    versions = {
        "gui.authoring_signal_smoke": ("2024", "2026"),
        "gui.fileio_safe_routes": ("2024", "2026"),
        "gui.info_undo": ("2024", "2026"),
        "gui.settings_side_effects": ("2024", "2026"),
    }
    result = validate_report(manifest, _aggregate_from_inventory(manifest, versions))
    assert result["valid"]


def test_evidence_report_builder_reads_declared_artifacts(tmp_path):
    manifest = _qt_case_manifest()
    manifest["cases"][0]["evidence_files"] = {
        "2024": "reports/case-2024.log",
        "2026": "reports/case-2026.json",
    }
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "case-2024.log").write_text(
        "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=PASS\n",
        encoding="utf-8",
    )
    (reports / "case-2026.json").write_text(
        json.dumps({"status": "pass", "maya_version": "2026"}),
        encoding="utf-8",
    )

    report = build_report_from_evidence(manifest, tmp_path)

    assert validate_report(manifest, report)["valid"]


def test_evidence_report_builder_rejects_failed_artifact(tmp_path):
    manifest = _qt_case_manifest()
    manifest["cases"][0]["evidence_files"] = {
        "2024": "case-2024.log",
        "2026": "case-2026.log",
    }
    for version in ("2024", "2026"):
        (tmp_path / f"case-{version}.log").write_text(
            "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=FAIL\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="evidence failed"):
        build_report_from_evidence(manifest, tmp_path)
