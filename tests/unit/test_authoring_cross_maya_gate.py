"""Pure selection and evidence checks for the Authoring cross-Maya gate."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.gates import authoring_cross_maya_gate as gate


ROOT = Path(__file__).resolve().parents[2]


def _matrix():
    return gate.load_matrix(ROOT)


def _plan(profile="focused", domains=("material",), change_kinds=()):
    return gate.build_plan(_matrix(), profile, domains, change_kinds)


def _gui_report(plan, version="2024"):
    cases = []
    for case in plan["cases"]:
        if case["runner"] != "gui_batch":
            continue
        cases.append(
            {
                "id": case["id"],
                "status": "PASS",
                "test_counts": {"success": 1},
                "tests": [{"id": case["test_id"], "status": "success"}],
            }
        )
    return {
        "schema_version": 1,
        "runner": "maya_gui_batch",
        "maya_version": version,
        "status": "PASS",
        "case_counts": {"PASS": len(cases)},
        "cases": cases,
    }


def _write_headless_junit(path, identities, **counts):
    suites = ET.Element("testsuites", {"name": "pytest tests"})
    attributes = {
        "name": "pytest",
        "tests": str(counts.get("tests", len(identities))),
        "errors": str(counts.get("errors", 0)),
        "failures": str(counts.get("failures", 0)),
        "skipped": str(counts.get("skipped", 0)),
    }
    suite = ET.SubElement(suites, "testsuite", attributes)
    for classname, name in identities:
        ET.SubElement(suite, "testcase", {"classname": classname, "name": name})
    ET.ElementTree(suites).write(str(path), encoding="utf-8", xml_declaration=True)


def test_checked_in_matrix_loads_with_live_source_links():
    matrix = _matrix()
    assert matrix["matrix_id"] == gate.MATRIX_ID
    assert len(matrix["cases"]) == 15


def test_runtime_loader_rejects_profile_downgrade_and_mayapy_script_swap(monkeypatch):
    checked_in = _matrix()
    original_load = gate.load_json

    for mutation in ("profile", "script"):
        modified = copy.deepcopy(checked_in)
        if mutation == "profile":
            modified["profiles"]["version_sensitive"]["required_maya_versions"] = ["2024"]
            expected = "profile policy"
        else:
            morph = next(case for case in modified["cases"] if case["id"] == "native.morph_weight")
            outline = next(case for case in modified["cases"] if case["id"] == "native.material_outline")
            morph["script"], outline["script"] = outline["script"], morph["script"]
            expected = "mayapy case identity"

        def fake_load(path, payload=modified):
            if path.name == "authoring_cross_maya_matrix.json":
                return payload
            return original_load(path)

        monkeypatch.setattr(gate, "load_json", fake_load)
        with pytest.raises(gate.CrossMayaGateError, match=expected):
            gate.load_matrix(ROOT)
        monkeypatch.setattr(gate, "load_json", original_load)


@pytest.mark.parametrize("field", ["required_domains", "required_boundaries"])
@pytest.mark.parametrize("mutation", ["missing", "bogus"])
def test_runtime_loader_rejects_non_exact_required_sets(monkeypatch, field, mutation):
    modified = copy.deepcopy(_matrix())
    if mutation == "missing":
        modified[field].pop()
    else:
        modified[field].append("bogus")
    original_load = gate.load_json

    def fake_load(path):
        if path.name == "authoring_cross_maya_matrix.json":
            return modified
        return original_load(path)

    monkeypatch.setattr(gate, "load_json", fake_load)
    with pytest.raises(gate.CrossMayaGateError, match="required .* set is not exact"):
        gate.load_matrix(ROOT)


def test_focused_runs_selected_domain_on_2024_with_main_window_representative():
    plan = _plan(domains=("info",))
    assert plan["effective_profile"] == "focused"
    assert plan["versions"] == ("2024",)
    assert [case["id"] for case in plan["cases"]] == [
        "gui.main_window_refresh",
        "gui.info_focus",
    ]
    assert plan["run_version_independent_lane_once"] is True


@pytest.mark.parametrize("kind", sorted(gate.SENSITIVE_CHANGE_KINDS))
def test_sensitive_change_kind_escalates_focused_to_both_versions(kind):
    plan = _plan(domains=("morph",), change_kinds=(kind,))
    assert plan["effective_profile"] == "version_sensitive"
    assert plan["versions"] == ("2024", "2026")


def test_release_candidate_is_exact_dual_full_matrix_and_clean_required():
    plan = _plan(profile="release_candidate", domains=())
    assert plan["versions"] == ("2024", "2026")
    assert len(plan["cases"]) == 15
    assert plan["require_clean_worktree"] is True


@pytest.mark.parametrize(
    ("profile", "domains", "change_kinds", "message"),
    [
        ("focused", (), (), "requires at least one --domain"),
        ("focused", ("bogus",), (), "unknown domain"),
        ("release_candidate", ("material",), (), "does not accept domain filtering"),
        ("focused", ("material",), ("unknown",), "unknown change kind"),
    ],
)
def test_invalid_selection_fails_closed(profile, domains, change_kinds, message):
    with pytest.raises(gate.CrossMayaGateError, match=message):
        _plan(profile=profile, domains=domains, change_kinds=change_kinds)


def test_gui_batch_manifest_contains_only_strict_existing_runner_fields():
    payload = gate.gui_batch_manifest(_plan(domains=("info",)))
    assert payload == {
        "schema_version": 1,
        "cases": [
            {
                "id": "gui.main_window_refresh",
                "test_path": "tests/gui",
                "test_filter": "test_header_refresh_defers_hidden_authoring_tabs",
            },
            {
                "id": "gui.info_focus",
                "test_path": "tests/gui",
                "test_filter": "test_focus_session_immediate_write_and_undo_redo",
            },
        ],
    }


def test_gui_timing_requires_exact_test_identity_count_status_and_version():
    plan = _plan(domains=("info",))
    gate.validate_gui_timing_report(_gui_report(plan), "2024", plan["cases"])

    mutations = []
    wrong_id = _gui_report(plan)
    wrong_id["cases"][0]["tests"][0]["id"] = "wrong.test"
    mutations.append(wrong_id)
    two_tests = _gui_report(plan)
    two_tests["cases"][0]["tests"].append(copy.deepcopy(two_tests["cases"][0]["tests"][0]))
    mutations.append(two_tests)
    no_tests = _gui_report(plan)
    no_tests["cases"][0].update({"status": "NO_TESTS", "test_counts": {}, "tests": []})
    mutations.append(no_tests)
    wrong_version = _gui_report(plan, "2026")
    mutations.append(wrong_version)
    for report in mutations:
        with pytest.raises(gate.CrossMayaGateError):
            gate.validate_gui_timing_report(report, "2024", plan["cases"])


def test_gui_timing_rejects_missing_reordered_or_non_pass_case():
    plan = _plan(domains=("info",))
    missing = _gui_report(plan)
    missing["cases"].pop()
    missing["case_counts"] = {"PASS": 1}
    with pytest.raises(gate.CrossMayaGateError, match="identity/order"):
        gate.validate_gui_timing_report(missing, "2024", plan["cases"])

    failed = _gui_report(plan)
    failed["case_counts"] = {"PASS": 1, "FAIL": 1}
    with pytest.raises(gate.CrossMayaGateError, match="failed, skipped, or unrun"):
        gate.validate_gui_timing_report(failed, "2024", plan["cases"])


def test_headless_junit_requires_exact_surface_ids_and_owner(tmp_path):
    matrix = _matrix()
    surface_count = matrix["surface_trace"]["expected_surface_count"]
    total_count = surface_count + 1
    identities = gate.expected_headless_test_identities(ROOT, matrix)
    assert identities[0] == (
        "tests.unit.test_authoring_ui_surface_matrix",
        "test_authoring_surface_dispatches_exactly_once[import_export.tab_selector]",
    )
    assert identities[-1] == (
        "tests.unit.test_authoring_ui_surface_matrix",
        "test_headless_matrix_owns_all_declared_safe_qt_cases_without_maya_claims",
    )
    junit = tmp_path / "headless.xml"
    _write_headless_junit(junit, identities)

    result = gate.validate_headless_junit(junit, ROOT, matrix)
    assert result["test_count"] == total_count
    assert result["surface_test_count"] == surface_count
    assert len(result["test_identities"]) == total_count


def test_headless_junit_rejects_rc_zero_subset_duplicate_and_non_pass(tmp_path):
    matrix = _matrix()
    total_count = matrix["surface_trace"]["expected_surface_count"] + 1
    identities = gate.expected_headless_test_identities(ROOT, matrix)
    junit = tmp_path / "headless.xml"

    _write_headless_junit(junit, identities[:1])
    with pytest.raises(
        gate.CrossMayaGateError, match="{} PASS".format(total_count)
    ):
        gate.validate_headless_junit(junit, ROOT, matrix)

    duplicated = list(identities)
    duplicated[-1] = duplicated[0]
    _write_headless_junit(junit, duplicated)
    with pytest.raises(gate.CrossMayaGateError, match="identities"):
        gate.validate_headless_junit(junit, ROOT, matrix)

    _write_headless_junit(junit, identities, skipped=1)
    with pytest.raises(
        gate.CrossMayaGateError, match="{} PASS".format(total_count)
    ):
        gate.validate_headless_junit(junit, ROOT, matrix)

    _write_headless_junit(junit, identities)
    document = ET.parse(str(junit))
    document.getroot().find("testsuite").attrib.pop("errors")
    document.write(str(junit), encoding="utf-8", xml_declaration=True)
    with pytest.raises(
        gate.CrossMayaGateError, match="{} PASS".format(total_count)
    ):
        gate.validate_headless_junit(junit, ROOT, matrix)


def test_artifact_identity_rejects_missing_empty_and_stale(tmp_path):
    missing = tmp_path / "missing.log"
    with pytest.raises(gate.CrossMayaGateError, match="missing"):
        gate.artifact_identity(missing, 0, "test")
    empty = tmp_path / "empty.log"
    empty.touch()
    with pytest.raises(gate.CrossMayaGateError, match="empty"):
        gate.artifact_identity(empty, 0, "test")
    stale = tmp_path / "stale.log"
    stale.write_text("old\n", encoding="utf-8")
    started = stale.stat().st_mtime_ns + gate.FRESHNESS_TOLERANCE_NS + 1
    with pytest.raises(gate.CrossMayaGateError, match="stale"):
        gate.artifact_identity(stale, started, "test")


def test_artifact_and_plugin_identity_record_exact_sha_and_debug_version(tmp_path):
    artifact = tmp_path / "fresh.log"
    artifact.write_bytes(b"fresh evidence\n")
    identity = gate.artifact_identity(artifact, 0, "log")
    assert identity["sha256"] == gate.sha256_file(artifact)
    assert identity["size"] == len(b"fresh evidence\n")

    plugin = tmp_path / "plug-ins" / "2024" / "Debug" / "mmd_tools_cpp.mll"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"plugin")
    plugin_record = gate.plugin_identity(plugin, "2024")
    assert plugin_record["config"] == "Debug"
    assert plugin_record["sha256"] == gate.sha256_file(plugin)
    with pytest.raises(gate.CrossMayaGateError, match="2026"):
        gate.plugin_identity(plugin, "2026")


def test_source_identity_records_full_head_and_dirty_paths(monkeypatch):
    def fake_check_output(command, **_kwargs):
        if command[1] == "rev-parse":
            return "a" * 40 + "\n"
        return " M tracked.py\n?? new.py\n"

    monkeypatch.setattr(gate.subprocess, "check_output", fake_check_output)
    identity = gate.source_identity(ROOT)
    assert identity == {"head": "a" * 40, "dirty_paths": ["new.py", "tracked.py"]}


def test_parse_request_preserves_repeatable_domain_and_change_kind():
    request = gate.parse_request(
        [
            "--profile",
            "focused",
            "--domain",
            "material",
            "--domain",
            "morph",
            "--change-kind",
            "native",
            "--out-dir",
            "build/reports/custom",
        ]
    )
    assert request["domain"] == ["material", "morph"]
    assert request["change_kind"] == ["native"]
    assert request["out_dir"] == "build/reports/custom"
