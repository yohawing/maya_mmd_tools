"""Pure validation contracts for the checked-in UI coverage inventory."""

import copy
import json
from pathlib import Path

import pytest

from tools.ui_coverage_gate import (
    build_report_from_batch_logs,
    build_report_from_evidence,
    main,
    validate_manifest,
    validate_report,
)


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
        "cases": [
            {
                "id": "case.one",
                "execution_layer": "real_maya",
                "required_maya_versions": ["2024", "2026"],
            }
        ],
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
    report_surfaces = []
    for surface in surfaces or []:
        entry = dict(surface)
        entry.setdefault(
            "runtime_witness",
            {
                "interaction": "QTest.click(import_export.surface)",
                "fired_action": "example.Presenter.import_model",
                "oracle": "model_loaded",
                "action_count": 1,
            },
        )
        report_surfaces.append(entry)
    return {
        "schema_version": 1,
        "gate_id": "V070-UI-COVERAGE-1",
        "cases": cases,
        "surfaces": report_surfaces,
    }


def test_checked_in_manifest_is_structurally_valid_and_inventories_all_tabs():
    result = validate_manifest(_load_manifest())
    assert result["valid"]
    assert result["tab_count"] == 9
    assert result["surface_count"] >= 200


def test_case_execution_layer_is_required_and_fail_closed():
    manifest = _minimal_manifest()
    manifest["cases"][0].pop("execution_layer")
    assert "invalid_execution_layer" in _errors(validate_manifest(manifest))


def test_headless_case_must_not_claim_maya_versions():
    manifest = _minimal_manifest()
    manifest["cases"][0]["execution_layer"] = "headless_qt"
    assert "headless_case_has_maya_versions" in _errors(validate_manifest(manifest))


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


def test_qt_case_requires_fully_qualified_expected_handler():
    manifest = _qt_case_manifest()
    manifest["surfaces"][0]["expected_handler"] = "raw_signal"

    assert "invalid_expected_handler" in _errors(validate_manifest(manifest))


def test_report_handler_must_match_manifest_mapping():
    manifest = _qt_case_manifest()
    report = _aggregate_from_inventory(manifest, {"case.one": ("2024", "2026")})
    report["surfaces"][0]["runtime_witness"]["fired_action"] = (
        "example.Presenter.other"
    )

    assert "handler_mismatch" in _errors(validate_report(manifest, report))


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
    manifest["surfaces"][0].update(
        {
            "disposition": "qt_case",
            "case_id": "case.one",
            "expected_handler": "example.Presenter.import_model",
        }
    )
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


def test_report_missing_runtime_witness_fails_closed():
    manifest = _qt_case_manifest()
    report = _report(
        [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
        [
            {
                "surface_id": "import_export.surface",
                "case_id": "case.one",
                "selector": "objectName=surface",
                "status": "pass",
            }
        ],
    )
    report["surfaces"][0].pop("runtime_witness")

    result = validate_report(manifest, report)

    assert "missing_runtime_witness" in _errors(result)
    assert not result["valid"]


@pytest.mark.parametrize("field", ["interaction", "fired_action", "oracle"])
def test_report_runtime_witness_text_fields_must_be_nonempty(field):
    manifest = _qt_case_manifest()
    report = _report(
        [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
        [
            {
                "surface_id": "import_export.surface",
                "case_id": "case.one",
                "selector": "objectName=surface",
                "status": "pass",
            }
        ],
    )
    report["surfaces"][0]["runtime_witness"][field] = " "

    result = validate_report(manifest, report)

    assert "invalid_runtime_witness_field" in _errors(result)
    assert not result["valid"]


@pytest.mark.parametrize("action_count", [0, 2, True, "1", None])
def test_report_runtime_witness_action_count_must_be_exactly_one(action_count):
    manifest = _qt_case_manifest()
    report = _report(
        [{"case_id": "case.one", "status": "pass", "maya_versions": ["2024", "2026"]}],
        [
            {
                "surface_id": "import_export.surface",
                "case_id": "case.one",
                "selector": "objectName=surface",
                "status": "pass",
            }
        ],
    )
    report["surfaces"][0]["runtime_witness"]["action_count"] = action_count

    result = validate_report(manifest, report)

    assert "invalid_runtime_witness_action_count" in _errors(result)
    assert not result["valid"]


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
            "runtime_witness": {
                "interaction": "production control interaction",
                "fired_action": surface["expected_handler"],
                "oracle": "production handler exact once",
                "action_count": 1,
            },
        }
        locator_key = "selector" if "selector" in surface else "attribute"
        evidence[locator_key] = surface[locator_key]
        surfaces.append(evidence)
    return _report(cases, surfaces)


def _required_versions_by_qt_case(manifest):
    case_ids = {
        surface["case_id"]
        for surface in manifest["surfaces"]
        if surface["disposition"] == "qt_case"
    }
    return {
        case["id"]: tuple(case.get("required_maya_versions", ()))
        for case in manifest["cases"]
        if case["id"] in case_ids
    }


def test_checked_in_headless_aggregate_does_not_require_maya_versions():
    manifest = _load_manifest()
    versions = _required_versions_by_qt_case(manifest)
    result = validate_report(manifest, _aggregate_from_inventory(manifest, versions))
    assert result["valid"]
    assert versions == {"headless.authoring_ui_surface_matrix": ()}


def test_headless_case_rejects_required_maya_versions():
    manifest = _load_manifest()
    owner = next(
        case
        for case in manifest["cases"]
        if case["id"] == "headless.authoring_ui_surface_matrix"
    )
    owner["required_maya_versions"] = ["2024"]
    assert "headless_case_has_maya_versions" in _errors(validate_manifest(manifest))


def test_manifest_fails_when_qt_case_count_drops_below_floor():
    manifest = _load_manifest()
    manifest["minimum_qt_case_surfaces"] = sum(
        surface["disposition"] == "qt_case" for surface in manifest["surfaces"]
    ) + 1

    result = validate_manifest(manifest)

    assert "insufficient_qt_case_surfaces" in _errors(result)


def _builder_surface(**witness_overrides):
    witness = {
        "interaction": "click(boneApplyButton)",
        "fired_action": "example.Presenter.import_model",
        "oracle": "bone_spec_maya_footprint_undo_redo",
        "action_count": 1,
    }
    witness.update(witness_overrides)
    return {
        "surface_id": "import_export.surface",
        "case_id": "case.one",
        "selector": "objectName=surface",
        "status": "pass",
        "runtime_witness": witness,
    }


def _write_structured_evidence(tmp_path, manifest, payloads):
    manifest["cases"][0]["evidence_files"] = {}
    for version, payload in payloads.items():
        name = f"case-{version}.json"
        manifest["cases"][0]["evidence_files"][version] = name
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def _batch_marker(surface):
    return "[UI COVERAGE WITNESS] " + json.dumps(
        surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_evidence_report_builder_aggregates_identical_runtime_witness(tmp_path):
    manifest = _qt_case_manifest()
    surface = _builder_surface()
    _write_structured_evidence(
        tmp_path,
        manifest,
        {
            version: {"status": "pass", "maya_version": version, "surfaces": [surface]}
            for version in ("2024", "2026")
        },
    )

    report = build_report_from_evidence(manifest, tmp_path)

    assert report["surfaces"] == [surface]
    assert validate_report(manifest, report)["valid"]


def test_evidence_report_builder_rejects_missing_runtime_witness(tmp_path):
    manifest = _qt_case_manifest()
    surface = _builder_surface()
    _write_structured_evidence(
        tmp_path,
        manifest,
        {
            "2024": {"status": "pass", "maya_version": "2024", "surfaces": [surface]},
            "2026": {"status": "pass", "maya_version": "2026", "surfaces": []},
        },
    )

    with pytest.raises(ValueError, match="missing_runtime_witness"):
        build_report_from_evidence(manifest, tmp_path)


def test_evidence_report_builder_rejects_duplicate_runtime_witness(tmp_path):
    manifest = _qt_case_manifest()
    surface = _builder_surface()
    _write_structured_evidence(
        tmp_path,
        manifest,
        {
            "2024": {"status": "pass", "maya_version": "2024", "surfaces": [surface, surface]},
            "2026": {"status": "pass", "maya_version": "2026", "surfaces": [surface]},
        },
    )

    with pytest.raises(ValueError, match="duplicate_runtime_witness"):
        build_report_from_evidence(manifest, tmp_path)


def test_evidence_report_builder_rejects_cross_version_witness_mismatch(tmp_path):
    manifest = _qt_case_manifest()
    _write_structured_evidence(
        tmp_path,
        manifest,
        {
            "2024": {
                "status": "pass",
                "maya_version": "2024",
                "surfaces": [_builder_surface()],
            },
            "2026": {
                "status": "pass",
                "maya_version": "2026",
                "surfaces": [_builder_surface(oracle="different_oracle")],
            },
        },
    )

    with pytest.raises(ValueError, match="runtime_witness_mismatch"):
        build_report_from_evidence(manifest, tmp_path)


def test_evidence_report_builder_requires_structured_json_witness(tmp_path):
    manifest = _qt_case_manifest()
    manifest["cases"][0]["evidence_files"] = {"2024": "case-2024.log"}
    (tmp_path / "case-2024.log").write_text(
        "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=PASS\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="structured JSON"):
        build_report_from_evidence(manifest, tmp_path)


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


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("case-2024.log", "Ran 0 tests in 0.0s\n//-- GUI TEST FINISHED --// status=PASS\n"),
        ("case-wrong.log", "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=PASS\n"),
    ],
)
def test_evidence_report_builder_rejects_zero_tests_or_wrong_version(tmp_path, name, content):
    manifest = _qt_case_manifest()
    manifest["cases"][0]["required_maya_versions"] = ["2024"]
    manifest["cases"][0]["evidence_files"] = {"2024": name}
    (tmp_path / name).write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="evidence failed"):
        build_report_from_evidence(manifest, tmp_path)


def _write_batch_logs(tmp_path, surface_by_version):
    manifest = _qt_case_manifest()
    test_id = "tests.gui.case.TestCase.test_action"
    manifest["cases"][0]["evidence_tests"] = [test_id]
    logs = {}
    for version, surfaces in surface_by_version.items():
        path = tmp_path / f"gui-{version}.log"
        markers = "".join(_batch_marker(surface) + "\n" for surface in surfaces)
        path.write_text(
            f"[GUI TEST] END {test_id} outcome=success\n"
            "Ran 1 test in 0.1s\n"
            "//-- GUI TEST FINISHED --// status=PASS\n"
            + markers,
            encoding="utf-8",
        )
        logs[version] = path
    return manifest, logs


def _write_headless_report(tmp_path, manifest):
    """Add one headless owner and its fresh structured witness report."""
    headless_surface = _builder_surface()
    headless_surface.update(
        {
            "surface_id": "import_export.headless_surface",
            "case_id": "case.headless",
            "selector": "objectName=headless_surface",
        }
    )
    manifest["cases"].append(
        {
            "id": "case.headless",
            "execution_layer": "headless_qt",
        }
    )
    headless_manifest_surface = copy.deepcopy(manifest["surfaces"][0])
    headless_manifest_surface.update(
        {
            "id": headless_surface["surface_id"],
            "case_id": "case.headless",
            "selector": headless_surface["selector"],
        }
    )
    manifest["surfaces"].append(headless_manifest_surface)
    report_path = tmp_path / "headless.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gate_id": "V070-UI-COVERAGE-1",
                "cases": [{"case_id": "case.headless", "status": "pass", "maya_versions": []}],
                "surfaces": [headless_surface],
            }
        ),
        encoding="utf-8",
    )
    return headless_surface, report_path


def test_batch_report_builder_aggregates_identical_runtime_witness(tmp_path):
    surface = _builder_surface()
    manifest, logs = _write_batch_logs(tmp_path, {"2024": [surface], "2026": [surface]})

    report = build_report_from_batch_logs(manifest, logs)

    assert report["surfaces"] == [surface]
    assert validate_report(manifest, report)["valid"]


def test_batch_report_builder_merges_fresh_headless_evidence(tmp_path):
    runtime_surface = _builder_surface()
    manifest, logs = _write_batch_logs(
        tmp_path, {"2024": [runtime_surface], "2026": [runtime_surface]}
    )
    headless_surface, headless_report = _write_headless_report(tmp_path, manifest)

    report = build_report_from_batch_logs(manifest, logs, headless_report=headless_report)

    assert report["surfaces"] == [headless_surface, runtime_surface]
    assert validate_report(manifest, report)["valid"]


def test_batch_report_builder_rejects_failed_supplied_log_for_headless_only_manifest(tmp_path):
    manifest = _qt_case_manifest()
    manifest["cases"][0]["execution_layer"] = "headless_qt"
    manifest["cases"][0].pop("required_maya_versions")
    surface = _builder_surface()
    report_path = tmp_path / "headless.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gate_id": "V070-UI-COVERAGE-1",
                "cases": [{"case_id": "case.one", "status": "pass", "maya_versions": []}],
                "surfaces": [surface],
            }
        ),
        encoding="utf-8",
    )
    failed_log = tmp_path / "gui-2024.log"
    failed_log.write_text(
        "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=FAIL\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="full GUI evidence failed"):
        build_report_from_batch_logs(
            manifest, {"2024": failed_log}, headless_report=report_path
        )


def test_batch_report_builder_rejects_missing_or_duplicate_headless_evidence(tmp_path):
    runtime_surface = _builder_surface()
    manifest, logs = _write_batch_logs(
        tmp_path, {"2024": [runtime_surface], "2026": [runtime_surface]}
    )
    _, report_path = _write_headless_report(tmp_path, manifest)
    report_path.unlink()

    with pytest.raises(ValueError, match="fresh headless UI coverage report is missing"):
        build_report_from_batch_logs(manifest, logs, headless_report=report_path)

    _, report_path = _write_headless_report(tmp_path, manifest)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["surfaces"].append(copy.deepcopy(report["surfaces"][0]))
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_runtime_witness"):
        build_report_from_batch_logs(manifest, logs, headless_report=report_path)


def test_batch_report_builder_requires_headless_evidence_case_owner(tmp_path):
    runtime_surface = _builder_surface()
    manifest, logs = _write_batch_logs(
        tmp_path, {"2024": [runtime_surface], "2026": [runtime_surface]}
    )
    _, report_path = _write_headless_report(tmp_path, manifest)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["cases"][0]["case_id"] = "case.wrong"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="headless case evidence mismatch"):
        build_report_from_batch_logs(manifest, logs, headless_report=report_path)


def test_batch_report_cli_reads_fresh_headless_evidence(tmp_path, capsys):
    runtime_surface = _builder_surface()
    manifest, logs = _write_batch_logs(
        tmp_path, {"2024": [runtime_surface], "2026": [runtime_surface]}
    )
    _, headless_report = _write_headless_report(tmp_path, manifest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = main(
        [
            str(manifest_path),
            "--headless-report",
            str(headless_report),
            "--batch-log",
            "2024=" + str(logs["2024"]),
            "--batch-log",
            "2026=" + str(logs["2026"]),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_batch_report_builder_rejects_missing_runtime_witness(tmp_path):
    surface = _builder_surface()
    manifest, logs = _write_batch_logs(tmp_path, {"2024": [surface], "2026": []})

    with pytest.raises(ValueError, match="missing_runtime_witness"):
        build_report_from_batch_logs(manifest, logs)


def test_batch_report_builder_rejects_duplicate_runtime_witness(tmp_path):
    surface = _builder_surface()
    manifest, logs = _write_batch_logs(tmp_path, {"2024": [surface, surface], "2026": [surface]})

    with pytest.raises(ValueError, match="duplicate_runtime_witness"):
        build_report_from_batch_logs(manifest, logs)


def test_batch_report_builder_rejects_cross_version_witness_mismatch(tmp_path):
    manifest, logs = _write_batch_logs(
        tmp_path,
        {"2024": [_builder_surface()], "2026": [_builder_surface(oracle="different_oracle")]},
    )

    with pytest.raises(ValueError, match="runtime_witness_mismatch"):
        build_report_from_batch_logs(manifest, logs)


def test_batch_report_builder_still_requires_named_test_success(tmp_path):
    surface = _builder_surface()
    manifest, logs = _write_batch_logs(tmp_path, {"2024": [surface], "2026": [surface]})

    logs["2026"].write_text(
        "Ran 1 test in 0.1s\n//-- GUI TEST FINISHED --// status=PASS\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing successful tests"):
        build_report_from_batch_logs(manifest, logs)
