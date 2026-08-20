"""Fail-closed checks for the Model Authoring test-layer inventory."""

import ast
import copy
import json
import math
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tools" / "authoring_test_layer_manifest.json"
UI_MANIFEST_PATH = ROOT / "tools" / "ui_coverage_manifest.json"
LAYERS = {"pure_unit", "headless_qt", "real_maya", "persistence"}
MAYA_VERSIONS = {"2024", "2025", "2026"}
REQUIRED_CONTRACT_KEYS = {
    "contract_id",
    "domain",
    "semantic_boundary",
    "primary_layer",
    "representative_test",
    "redundant_test",
    "cost_evidence",
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _test_id_exists(test_id):
    parts = test_id.split("::")
    path = ROOT / parts[0]
    if not path.is_file():
        return False
    if len(parts) == 1:
        return True
    nodes = ast.parse(path.read_text(encoding="utf-8")).body
    for name in parts[1:]:
        match = next(
            (
                node
                for node in nodes
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name
            ),
            None,
        )
        if match is None:
            return False
        nodes = getattr(match, "body", [])
    return True


def _test_file_name(test_id):
    return test_id.split("::", 1)[0]


def _errors(manifest, ui_manifest):
    errors = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version")
    if set(manifest.get("layers", {})) != LAYERS:
        errors.append("layers")

    coverage = manifest.get("surface_coverage", {})
    source_path = coverage.get("source_manifest")
    try:
        resolved_source = (ROOT / source_path).resolve()
        source_from_disk = _load(resolved_source)
    except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
        resolved_source = None
        source_from_disk = None
    if (
        resolved_source != UI_MANIFEST_PATH.resolve()
        or source_from_disk is None
        or source_from_disk != ui_manifest
    ):
        errors.append("source_manifest")
    selector = coverage.get("source_selector", {})
    source_surfaces = [
        surface
        for surface in ui_manifest.get("surfaces", [])
        if all(surface.get(key) == value for key, value in selector.items())
    ]
    surface_ids = [surface.get("id") for surface in source_surfaces]
    if len(source_surfaces) != coverage.get("expected_surface_count"):
        errors.append("surface_count")
    if len(surface_ids) != len(set(surface_ids)) or any(not value for value in surface_ids):
        errors.append("surface_ids")
    if coverage.get("target_primary_layer") != "headless_qt":
        errors.append("surface_target_layer")
    if coverage.get("target_owner") != "headless.authoring_ui_surface_matrix":
        errors.append("surface_owner_rule")
    known_case_ids = {case.get("id") for case in ui_manifest.get("cases", [])}
    if any(
        not surface.get("case_id") or surface.get("case_id") not in known_case_ids
        for surface in source_surfaces
    ):
        errors.append("surface_owner")
    if coverage.get("status") != "complete" or not coverage.get("completion"):
        errors.append("surface_completion")
    current_layer = coverage.get("current_primary_layer")
    if current_layer not in LAYERS:
        errors.append("surface_current_layer")
    if current_layer != "headless_qt":
        errors.append("surface_current_layer")
    cases_by_id = {case.get("id"): case for case in ui_manifest.get("cases", [])}
    if coverage.get("status") != "complete":
        invalid_status_cases = set()
        for surface in source_surfaces:
            case = cases_by_id.get(surface.get("case_id"), {})
            versions = case.get("required_maya_versions")
            if (
                not isinstance(versions, list)
                or not versions
                or any(
                    not isinstance(version, str) or version not in MAYA_VERSIONS
                    for version in versions
                )
            ):
                errors.append("surface_current_evidence")
                break
            if case.get("status") != "current":
                invalid_status_cases.add(case.get("id"))
        declared_missing = coverage.get("current_missing_owner_cases")
        if (
            not isinstance(declared_missing, list)
            or len(declared_missing) != len(set(declared_missing))
            or set(declared_missing) != invalid_status_cases
        ):
            errors.append("surface_current_evidence")

    contracts = manifest.get("contracts", [])
    ids = [contract.get("contract_id") for contract in contracts]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        errors.append("contract_ids")
    semantic_keys = [
        (contract.get("domain"), contract.get("semantic_boundary")) for contract in contracts
    ]
    if len(semantic_keys) != len(set(semantic_keys)) or any(
        not domain or not boundary for domain, boundary in semantic_keys
    ):
        errors.append("semantic_contracts")
    for contract in contracts:
        if not REQUIRED_CONTRACT_KEYS.issubset(contract):
            errors.append("contract_schema")
            continue
        layer = contract["primary_layer"]
        if layer not in LAYERS:
            errors.append("contract_layer")
        representative = contract["representative_test"]
        if not _test_id_exists(representative):
            errors.append("representative_path")
        related = contract.get("related_representative_tests", [])
        if not isinstance(related, list) or any(
            not _test_id_exists(item) for item in related
        ):
            errors.append("representative_path")
        if not isinstance(contract["redundant_test"], list):
            errors.append("redundant_test")
        elif any(not _test_id_exists(item) for item in contract["redundant_test"]):
            errors.append("redundant_path")
        cost = contract["cost_evidence"]
        if not isinstance(cost, dict) or not {"metric", "value", "source"}.issubset(cost):
            errors.append("cost_evidence")
        else:
            value = cost["value"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                errors.append("cost_value")
        if layer == "pure_unit" and not representative.startswith("tests/unit/"):
            errors.append("pure_unit_path")
        if layer == "real_maya":
            allowlist = manifest.get("layers", {}).get("real_maya", {}).get(
                "tool_allowlist", []
            )
            parts = representative.split("::")
            is_gui_test_symbol = (
                representative.startswith("tests/gui/")
                and len(parts) >= 3
                and parts[-1].startswith("test_")
            )
            is_allowlisted_tool = len(parts) == 1 and _test_file_name(representative) in allowlist
            if not (is_gui_test_symbol or is_allowlisted_tool):
                errors.append("real_maya_path")
            if any(token in representative.lower() for token in ("standalone", "offscreen")):
                errors.append("real_maya_environment")
        if layer == "headless_qt" and contract.get("claims_real_maya_evidence") is not None:
            errors.append("headless_claim")
        if layer == "persistence" and not contract.get("state_family"):
            errors.append("persistence_family")

    policy = manifest.get("persistence_policy", {})
    family_contracts = Counter(
        contract.get("state_family")
        for contract in contracts
        if contract.get("primary_layer") == "persistence"
    )
    if set(family_contracts) != set(policy.get("families", {})):
        errors.append("persistence_families")
    maximum = policy.get("max_representatives_per_state_family")
    if not isinstance(maximum, int) or maximum < 1 or any(count > maximum for count in family_contracts.values()):
        errors.append("persistence_family_limit")
    for family, contract_id in policy.get("families", {}).items():
        if not any(
            contract.get("contract_id") == contract_id and contract.get("state_family") == family
            for contract in contracts
        ):
            errors.append("persistence_family_owner")
    return errors


def test_checked_in_inventory_is_valid_and_tracks_all_qt_surfaces():
    assert _errors(_load(MANIFEST_PATH), _load(UI_MANIFEST_PATH)) == []


def test_mixed_persistence_owns_four_domain_setups_and_one_boundary():
    manifest = _load(MANIFEST_PATH)
    contract = next(
        item
        for item in manifest["contracts"]
        if item["contract_id"] == "authoring.persistence.mixed_scene"
    )
    assert contract["setup_domains"] == ["material", "bone", "morph", "display_pane"]
    assert contract["cost_evidence"]["metric"] == "persistence_boundaries"
    assert contract["cost_evidence"]["value"] == 1


def test_schema_and_layer_mutations_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["schema_version"] = 2
    manifest["layers"].pop("real_maya")
    assert {"schema_version", "layers"}.issubset(_errors(manifest, ui_manifest))


def test_surface_count_owner_and_gap_mutations_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["surface_coverage"].update(
        {"expected_surface_count": 228, "target_owner": "manual", "status": "gap"}
    )
    assert {"surface_count", "surface_owner_rule", "surface_completion"}.issubset(
        _errors(manifest, ui_manifest)
    )


def test_source_manifest_must_resolve_to_the_loaded_ui_inventory():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["surface_coverage"]["source_manifest"] = "tools/missing.json"
    assert "source_manifest" in _errors(manifest, ui_manifest)


def test_surface_owner_must_reference_a_known_ui_case():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    target = next(surface for surface in ui_manifest["surfaces"] if surface["disposition"] == "qt_case")
    target["case_id"] = "gui.orphan"
    assert "surface_owner" in _errors(manifest, ui_manifest)


def test_complete_surface_matrix_requires_headless_qt_layer():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["surface_coverage"]["current_primary_layer"] = "real_maya"
    assert "surface_current_layer" in _errors(manifest, ui_manifest)


def test_complete_surface_owner_is_current_headless_case_without_maya_versions():
    ui_manifest = _load(UI_MANIFEST_PATH)
    owned_case_id = next(
        surface["case_id"]
        for surface in ui_manifest["surfaces"]
        if surface["disposition"] == "qt_case"
    )
    owned_case = next(case for case in ui_manifest["cases"] if case["id"] == owned_case_id)
    assert owned_case["status"] == "current"
    assert owned_case["execution_layer"] == "headless_qt"
    assert "required_maya_versions" not in owned_case


def test_duplicate_surface_and_contract_ids_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = copy.deepcopy(_load(UI_MANIFEST_PATH))
    ui_manifest["surfaces"].append(copy.deepcopy(ui_manifest["surfaces"][0]))
    manifest["contracts"].append(copy.deepcopy(manifest["contracts"][0]))
    assert {"surface_count", "surface_ids", "contract_ids"}.issubset(
        _errors(manifest, ui_manifest)
    )


def test_duplicate_domain_semantic_boundary_fails_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    duplicate = copy.deepcopy(manifest["contracts"][0])
    duplicate["contract_id"] = "different.id"
    manifest["contracts"].append(duplicate)
    assert "semantic_contracts" in _errors(manifest, ui_manifest)


def test_missing_representative_and_redundant_paths_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["representative_test"] = "tests/unit/missing.py::test_missing"
    manifest["contracts"][1]["redundant_test"] = ["tests/gui/missing.py::test_missing"]
    assert {"representative_path", "redundant_path"}.issubset(_errors(manifest, ui_manifest))


def test_missing_test_symbol_in_existing_file_fails_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["representative_test"] = (
        "tests/unit/test_ui_action_coverage.py::test_missing"
    )
    assert "representative_path" in _errors(manifest, ui_manifest)


def test_primary_layer_constraints_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["primary_layer"] = "real_maya"
    manifest["contracts"][1]["primary_layer"] = "pure_unit"
    assert {"real_maya_path", "pure_unit_path"}.issubset(_errors(manifest, ui_manifest))


def test_real_maya_rejects_standalone_or_offscreen_representatives():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    contract = manifest["contracts"][1]
    contract["representative_test"] = "tests/gui/guitest_standalone.py"
    assert {"representative_path", "real_maya_environment"}.issubset(
        _errors(manifest, ui_manifest)
    )


def test_real_maya_gui_representative_requires_a_concrete_test_symbol():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    contract = manifest["contracts"][1]
    contract["representative_test"] = "tests/gui/__init__.py"
    assert "real_maya_path" in _errors(manifest, ui_manifest)


def test_headless_qt_cannot_claim_real_maya_evidence():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["claims_real_maya_evidence"] = False
    assert "headless_claim" in _errors(manifest, ui_manifest)


def test_persistence_family_limit_and_owner_fail_closed():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    duplicate = copy.deepcopy(
        next(item for item in manifest["contracts"] if item.get("state_family") == "mixed_authoring_scene")
    )
    duplicate["contract_id"] = "authoring.persistence.mixed_scene.duplicate"
    manifest["contracts"].append(duplicate)
    manifest["persistence_policy"]["families"]["mixed_authoring_scene"] = "missing.owner"
    assert {"persistence_family_limit", "persistence_family_owner"}.issubset(
        _errors(manifest, ui_manifest)
    )


def test_cost_evidence_is_required_and_structured():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["cost_evidence"] = {"metric": "missing fields"}
    assert "cost_evidence" in _errors(manifest, ui_manifest)


def test_cost_evidence_value_must_be_finite_and_nonnegative():
    manifest = _load(MANIFEST_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    manifest["contracts"][0]["cost_evidence"]["value"] = float("nan")
    manifest["contracts"][1]["cost_evidence"]["value"] = -1
    assert "cost_value" in _errors(manifest, ui_manifest)
