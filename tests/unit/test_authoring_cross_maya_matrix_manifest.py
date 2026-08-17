"""Fail-closed contracts for the compact Authoring cross-Maya matrix."""

import ast
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "tools" / "authoring_cross_maya_matrix.json"
UI_MANIFEST_PATH = ROOT / "tools" / "ui_coverage_manifest.json"
SEMANTIC_MANIFEST_PATH = ROOT / "tools" / "authoring_test_layer_manifest.json"

SUPPORTED_VERSIONS = ["2024", "2026"]
PROFILE_POLICY = {
    "focused": (["2024"], "selected_domains", False),
    "version_sensitive": (["2024", "2026"], "selected_domains", False),
    "release_candidate": (["2024", "2026"], "all_representatives", True),
}
CHANGE_KIND_POLICY = {
    "default": "focused",
    "qt6": "version_sensitive",
    "maya_api": "version_sensitive",
    "native": "version_sensitive",
    "serialization": "version_sensitive",
}
REQUIRED_BOUNDARIES = {
    "production_main_window",
    "domain_mutation",
    "undo_redo",
    "blendshape_dg",
    "native_cpp_command",
    "production_modal",
    "save_reopen",
}
REQUIRED_DOMAINS = {
    "main_window",
    "import_export",
    "export",
    "info",
    "material",
    "bone",
    "morph",
    "display_pane",
    "physics",
}
EXPECTED_CASE_IDS = {
    "gui.main_window_refresh",
    "gui.info_focus",
    "gui.material_main_texture",
    "gui.bone_identity",
    "gui.morph_preview",
    "gui.display_atomic",
    "gui.physics_joint_messages",
    "gui.modal_new_model",
    "gui.mixed_save_reopen",
    "gui.material_pmx_fresh_import",
    "maya.vertex_blendshape",
    "native.morph_binding_query",
    "native.morph_weight",
    "native.material_value",
    "native.material_outline",
}
ORIGIN_KINDS = {"matrix_policy", "standalone_smoke", "native_smoke"}
EXPECTED_MAYAPY_CASES = {
    "maya.vertex_blendshape": {
        "script": "tools/maya_vertex_morph_authoring_smoke.py",
        "requires_cpp_plugin": False,
        "domains": ["morph"],
        "proves": ["domain_mutation", "blendshape_dg"],
        "origin_kind": "standalone_smoke",
    },
    "native.morph_binding_query": {
        "script": "tools/maya_morph_binding_query_smoke.py",
        "requires_cpp_plugin": True,
        "domains": ["morph"],
        "proves": ["native_cpp_command"],
        "origin_kind": "native_smoke",
    },
    "native.morph_weight": {
        "script": "tools/maya_morph_weight_command_smoke.py",
        "requires_cpp_plugin": True,
        "domains": ["morph"],
        "proves": ["domain_mutation", "undo_redo", "native_cpp_command"],
        "origin_kind": "native_smoke",
    },
    "native.material_value": {
        "script": "tools/maya_material_value_command_smoke.py",
        "requires_cpp_plugin": True,
        "domains": ["material"],
        "proves": ["domain_mutation", "undo_redo", "native_cpp_command"],
        "origin_kind": "native_smoke",
    },
    "native.material_outline": {
        "script": "tools/maya_material_outline_command_smoke.py",
        "requires_cpp_plugin": True,
        "domains": ["material"],
        "proves": ["domain_mutation", "undo_redo", "native_cpp_command"],
        "origin_kind": "native_smoke",
    },
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _node_named(nodes, name, kinds):
    return next(
        (
            node
            for node in nodes
            if isinstance(node, kinds) and node.name == name
        ),
        None,
    )


def _gui_test_symbol_exists(test_id):
    parts = test_id.split(".")
    if len(parts) != 5 or parts[:2] != ["tests", "gui"]:
        return False
    path = ROOT / "tests" / "gui" / (parts[2] + ".py")
    if not path.is_file():
        return False
    try:
        nodes = ast.parse(path.read_text(encoding="utf-8")).body
    except (OSError, SyntaxError, UnicodeError):
        return False
    class_node = _node_named(nodes, parts[3], (ast.ClassDef,))
    if class_node is None:
        return False
    return _node_named(class_node.body, parts[4], (ast.FunctionDef, ast.AsyncFunctionDef)) is not None


def _gui_test_node_id(test_id):
    parts = test_id.split(".")
    if len(parts) != 5 or parts[:2] != ["tests", "gui"]:
        return None
    return "tests/gui/{}.py::{}::{}".format(parts[2], parts[3], parts[4])


def _gui_test_name_count(name):
    count = 0
    for path in (ROOT / "tests" / "gui").glob("guitest_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        count += sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
            for node in ast.walk(tree)
        )
    return count


def _resolve_source(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        resolved = (ROOT / value).resolve()
        resolved.relative_to(ROOT.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _errors(matrix, ui_manifest, semantic_manifest):
    errors = []
    if matrix.get("schema_version") != 1:
        errors.append("schema_version")
    if matrix.get("matrix_id") != "AUTHORING-CROSS-MAYA-MATRIX-1":
        errors.append("matrix_id")
    if matrix.get("supported_maya_versions") != SUPPORTED_VERSIONS:
        errors.append("supported_versions")

    sources = matrix.get("source_manifests", {})
    expected_sources = {
        "ui_surfaces": UI_MANIFEST_PATH.resolve(),
        "semantic_contracts": SEMANTIC_MANIFEST_PATH.resolve(),
    }
    if set(sources) != set(expected_sources):
        errors.append("source_manifests")
    else:
        for key, expected in expected_sources.items():
            if _resolve_source(sources.get(key)) != expected:
                errors.append("source_manifests")
                break
    if ui_manifest.get("gate_id") != "V070-UI-COVERAGE-1":
        errors.append("ui_source_contract")
    semantic_coverage = semantic_manifest.get("surface_coverage", {})
    if (
        semantic_coverage.get("source_manifest") != sources.get("ui_surfaces")
        or semantic_coverage.get("target_owner") != "headless.authoring_ui_surface_matrix"
        or semantic_coverage.get("status") != "complete"
    ):
        errors.append("semantic_source_contract")
    semantic_contracts = semantic_manifest.get("contracts")
    if not isinstance(semantic_contracts, list) or not semantic_contracts:
        semantic_contracts = []
        errors.append("semantic_contracts_empty")
    semantic_contract_ids = [
        contract.get("contract_id")
        for contract in semantic_contracts
        if isinstance(contract, dict)
    ]
    if (
        len(semantic_contract_ids) != len(semantic_contracts)
        or any(not contract_id for contract_id in semantic_contract_ids)
        or len(semantic_contract_ids) != len(set(semantic_contract_ids))
    ):
        errors.append("semantic_contract_ids")
    semantic_contracts_by_id = {
        contract.get("contract_id"): contract
        for contract in semantic_contracts
        if isinstance(contract, dict) and contract.get("contract_id")
    }

    profiles = matrix.get("profiles", {})
    if set(profiles) != set(PROFILE_POLICY):
        errors.append("profiles")
    for profile_id, (versions, selection, clean) in PROFILE_POLICY.items():
        profile = profiles.get(profile_id, {})
        expected_keys = {
            "required_maya_versions",
            "case_selection",
            "run_version_independent_lane_once",
        }
        if clean:
            expected_keys.add("require_clean_worktree")
        if (
            set(profile) != expected_keys
            or profile.get("required_maya_versions") != versions
            or profile.get("case_selection") != selection
            or profile.get("run_version_independent_lane_once") is not True
            or (clean and profile.get("require_clean_worktree") is not True)
        ):
            errors.append("profile_policy")
            break
    if matrix.get("change_kind_policy") != CHANGE_KIND_POLICY:
        errors.append("change_kind_policy")

    required_domains = matrix.get("required_domains")
    if (
        not isinstance(required_domains, list)
        or len(required_domains) != len(set(required_domains))
        or set(required_domains) != REQUIRED_DOMAINS
    ):
        errors.append("required_domains")
    required_boundaries = matrix.get("required_boundaries")
    if (
        not isinstance(required_boundaries, list)
        or len(required_boundaries) != len(set(required_boundaries))
        or set(required_boundaries) != REQUIRED_BOUNDARIES
    ):
        errors.append("required_boundaries")

    cases = matrix.get("cases")
    if not isinstance(cases, list) or not cases:
        cases = []
        errors.append("cases")
    case_ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(cases) or any(not value for value in case_ids) or len(case_ids) != len(set(case_ids)):
        errors.append("case_ids")
    if set(case_ids) != EXPECTED_CASE_IDS:
        errors.append("representative_cases")
    cases_by_id = {case.get("id"): case for case in cases if isinstance(case, dict)}
    observed_domains = set()
    observed_boundaries = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            continue
        runner = case.get("runner")
        domains = case.get("domains")
        proves = case.get("proves")
        if (
            not isinstance(domains, list)
            or not domains
            or len(domains) != len(set(domains))
            or not set(domains).issubset(REQUIRED_DOMAINS)
        ):
            errors.append("case_domains")
        else:
            observed_domains.update(domains)
        if (
            not isinstance(proves, list)
            or not proves
            or len(proves) != len(set(proves))
            or not set(proves).issubset(REQUIRED_BOUNDARIES)
        ):
            errors.append("case_boundaries")
        else:
            observed_boundaries.update(proves)

        common_keys = {"id", "runner", "domains", "proves"}
        if runner == "gui_batch":
            semantic_keys = {"contract_id"} if "contract_id" in case else {"origin_kind", "rationale"}
            if set(case) != common_keys | semantic_keys | {"test_path", "test_filter", "test_id"}:
                errors.append("gui_case_schema")
                continue
            test_id = case.get("test_id")
            test_filter = case.get("test_filter")
            if case.get("test_path") != "tests/gui":
                errors.append("gui_test_path")
            if not isinstance(test_id, str) or not _gui_test_symbol_exists(test_id):
                errors.append("gui_test_symbol")
            if (
                not isinstance(test_filter, str)
                or not isinstance(test_id, str)
                or test_filter != test_id.rsplit(".", 1)[-1]
                or _gui_test_name_count(test_filter) != 1
            ):
                errors.append("gui_test_filter")
            if "contract_id" in case:
                contract = semantic_contracts_by_id.get(case.get("contract_id"))
                if contract is None:
                    errors.append("missing_semantic_contract")
                else:
                    expected_layer = "persistence" if "save_reopen" in case.get("proves", []) else "real_maya"
                    if contract.get("primary_layer") != expected_layer:
                        errors.append("semantic_contract_layer")
                    contract_domain = contract.get("domain")
                    if contract_domain != "all_authoring_tabs" and contract_domain not in case.get("domains", []):
                        errors.append("semantic_contract_domain")
                    member_tests = [contract.get("representative_test")]
                    related = contract.get("related_representative_tests", [])
                    if isinstance(related, list):
                        member_tests.extend(related)
                    if _gui_test_node_id(test_id) not in member_tests:
                        errors.append("semantic_test_membership")
            elif (
                case.get("origin_kind") != "matrix_policy"
                or not isinstance(case.get("rationale"), str)
                or not case["rationale"].strip()
            ):
                errors.append("case_origin")
        elif runner == "mayapy":
            if set(case) != common_keys | {
                "script",
                "requires_cpp_plugin",
                "origin_kind",
                "rationale",
            }:
                errors.append("mayapy_case_schema")
                continue
            script = _resolve_source(case.get("script"))
            if script is None or script.parent != (ROOT / "tools").resolve() or not script.is_file():
                errors.append("mayapy_script")
            if type(case.get("requires_cpp_plugin")) is not bool:
                errors.append("native_requirement")
            if case.get("id", "").startswith("native.") and case.get("requires_cpp_plugin") is not True:
                errors.append("native_requirement")
            expected_origin = "native_smoke" if case.get("id", "").startswith("native.") else "standalone_smoke"
            if (
                case.get("origin_kind") != expected_origin
                or case.get("origin_kind") not in ORIGIN_KINDS
                or not isinstance(case.get("rationale"), str)
                or not case["rationale"].strip()
            ):
                errors.append("case_origin")
            expected_case = EXPECTED_MAYAPY_CASES.get(case.get("id"))
            if expected_case is None or any(
                case.get(field) != expected
                for field, expected in expected_case.items()
            ):
                errors.append("mayapy_case_identity")
        else:
            errors.append("case_runner")
    if observed_domains != REQUIRED_DOMAINS:
        errors.append("domain_coverage")
    if observed_boundaries != REQUIRED_BOUNDARIES:
        errors.append("boundary_coverage")
    if (
        sum(case.get("runner") == "gui_batch" for case in cases if isinstance(case, dict)) != 10
        or sum(case.get("runner") == "mayapy" for case in cases if isinstance(case, dict)) != 5
    ):
        errors.append("runner_case_counts")

    trace = matrix.get("surface_trace", {})
    expected_trace_keys = {
        "source_selector",
        "expected_surface_count",
        "version_independent_owner",
        "tab_representatives",
        "headless_only_tabs",
    }
    if set(trace) != expected_trace_keys:
        errors.append("surface_trace_schema")
    selector = trace.get("source_selector")
    if selector != {"disposition": "qt_case"}:
        errors.append("surface_selector")
        selector = {}
    surfaces = [
        surface
        for surface in ui_manifest.get("surfaces", [])
        if all(surface.get(key) == value for key, value in selector.items())
    ]
    if trace.get("expected_surface_count") != 230 or len(surfaces) != 230:
        errors.append("surface_count")
    owner_id = trace.get("version_independent_owner")
    owner_cases = [case for case in ui_manifest.get("cases", []) if case.get("id") == owner_id]
    if (
        len(owner_cases) != 1
        or owner_cases[0].get("execution_layer") != "headless_qt"
        or "required_maya_versions" in owner_cases[0]
        or any(surface.get("case_id") != owner_id for surface in surfaces)
        or any(not surface.get("expected_handler") for surface in surfaces)
    ):
        errors.append("version_independent_owner")

    tab_ids = {tab.get("id") for tab in ui_manifest.get("tabs", [])}
    tab_representatives = trace.get("tab_representatives", {})
    headless_only = trace.get("headless_only_tabs", {})
    if set(headless_only) != {"settings"} or not isinstance(headless_only.get("settings"), str) or not headless_only["settings"].strip():
        errors.append("headless_only_settings")
    if (
        not isinstance(tab_representatives, dict)
        or set(tab_representatives) & set(headless_only)
        or set(tab_representatives) | set(headless_only) != tab_ids
    ):
        errors.append("tab_trace")
    else:
        for tab_id, representatives in tab_representatives.items():
            if (
                not isinstance(representatives, list)
                or not representatives
                or len(representatives) != len(set(representatives))
                or any(case_id not in cases_by_id for case_id in representatives)
                or any(tab_id not in cases_by_id[case_id].get("domains", []) for case_id in representatives)
            ):
                errors.append("tab_representatives")
                break
    if any(surface.get("tab") not in tab_ids for surface in surfaces):
        errors.append("surface_tab_trace")

    return errors


def _checked_in_errors():
    return _errors(_load(MATRIX_PATH), _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))


def test_checked_in_cross_maya_matrix_is_complete_and_fail_closed():
    assert _checked_in_errors() == []


def test_profiles_encode_focused_sensitive_and_release_version_policy():
    manifest = _load(MATRIX_PATH)
    assert manifest["profiles"]["focused"]["required_maya_versions"] == ["2024"]
    assert manifest["profiles"]["version_sensitive"]["required_maya_versions"] == ["2024", "2026"]
    assert manifest["profiles"]["release_candidate"]["required_maya_versions"] == ["2024", "2026"]
    assert manifest["profiles"]["release_candidate"]["require_clean_worktree"] is True
    assert manifest["change_kind_policy"] == CHANGE_KIND_POLICY


def test_all_230_surfaces_have_one_versionless_headless_owner_and_tab_trace():
    matrix = _load(MATRIX_PATH)
    ui_manifest = _load(UI_MANIFEST_PATH)
    trace = matrix["surface_trace"]
    surfaces = [surface for surface in ui_manifest["surfaces"] if surface["disposition"] == "qt_case"]
    assert len(surfaces) == 230
    assert {surface["case_id"] for surface in surfaces} == {trace["version_independent_owner"]}
    assert set(trace["headless_only_tabs"]) == {"settings"}
    assert set(trace["tab_representatives"]) | set(trace["headless_only_tabs"]) == {
        tab["id"] for tab in ui_manifest["tabs"]
    }


def test_matrix_has_exactly_ten_gui_and_five_mayapy_representatives():
    manifest = _load(MATRIX_PATH)
    assert {case["id"] for case in manifest["cases"]} == EXPECTED_CASE_IDS
    assert sum(case["runner"] == "gui_batch" for case in manifest["cases"]) == 10
    assert sum(case["runner"] == "mayapy" for case in manifest["cases"]) == 5
    outline = next(case for case in manifest["cases"] if case["id"] == "native.material_outline")
    assert outline["script"] == "tools/maya_material_outline_command_smoke.py"
    assert (ROOT / outline["script"]).is_file()
    assert outline["requires_cpp_plugin"] is True
    assert "native.material_outline" in manifest["surface_trace"]["tab_representatives"]["material"]


def test_schema_profile_and_change_kind_mutations_fail_closed():
    matrix = _load(MATRIX_PATH)
    matrix["schema_version"] = 2
    matrix["profiles"]["focused"]["required_maya_versions"] = ["2024", "2026"]
    matrix["change_kind_policy"]["native"] = "focused"
    errors = _errors(matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))
    assert {"schema_version", "profile_policy", "change_kind_policy"}.issubset(errors)


def test_missing_2026_sensitive_lane_and_release_scope_fail_closed():
    matrix = _load(MATRIX_PATH)
    matrix["profiles"]["version_sensitive"]["required_maya_versions"] = ["2024"]
    matrix["profiles"]["release_candidate"]["case_selection"] = "selected_domains"
    assert "profile_policy" in _errors(
        matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH)
    )


def test_duplicate_case_and_unknown_domain_or_boundary_fail_closed():
    matrix = _load(MATRIX_PATH)
    duplicate = copy.deepcopy(matrix["cases"][0])
    matrix["cases"].append(duplicate)
    matrix["cases"][1]["domains"] = ["settings"]
    matrix["cases"][2]["proves"] = ["unit_only"]
    errors = _errors(matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))
    assert {"case_ids", "case_domains", "case_boundaries"}.issubset(errors)


def test_gui_case_requires_exact_existing_unique_test_symbol():
    matrix = _load(MATRIX_PATH)
    case = next(item for item in matrix["cases"] if item["runner"] == "gui_batch")
    case["test_id"] = "tests.gui.guitest_missing.TestMissing.test_missing"
    case["test_filter"] = "test_missing"
    errors = _errors(matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))
    assert {"gui_test_symbol", "gui_test_filter"}.issubset(errors)


def test_semantic_contract_inventory_cannot_be_empty():
    semantic = _load(SEMANTIC_MANIFEST_PATH)
    semantic["contracts"] = []
    assert "semantic_contracts_empty" in _errors(
        _load(MATRIX_PATH), _load(UI_MANIFEST_PATH), semantic
    )


def test_gui_representative_must_remain_a_member_of_linked_semantic_contract():
    semantic = _load(SEMANTIC_MANIFEST_PATH)
    contract = next(
        item
        for item in semantic["contracts"]
        if item["contract_id"] == "material.values.dg_undo"
    )
    contract["representative_test"] = (
        "tests/gui/guitest_authoring_signal_smoke_gui.py::"
        "TestAuthoringSignalSmokeGUI::test_dx11_material_diffuse_apply_undo_redo"
    )
    contract["related_representative_tests"] = []
    assert "semantic_test_membership" in _errors(
        _load(MATRIX_PATH), _load(UI_MANIFEST_PATH), semantic
    )


def test_orphan_semantic_contract_reference_fails_closed():
    matrix = _load(MATRIX_PATH)
    case = next(item for item in matrix["cases"] if item["id"] == "gui.info_focus")
    case["contract_id"] = "missing.contract"
    assert "missing_semantic_contract" in _errors(
        matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH)
    )


def test_mayapy_case_requires_existing_tools_script_and_native_plugin_identity():
    matrix = _load(MATRIX_PATH)
    case = next(item for item in matrix["cases"] if item["id"] == "native.morph_weight")
    case["script"] = "tests/unit/test_morph_preview_transaction.py"
    case["requires_cpp_plugin"] = False
    errors = _errors(matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))
    assert {"mayapy_script", "native_requirement"}.issubset(errors)


def test_mayapy_case_ids_cannot_swap_scripts_or_semantic_scope():
    matrix = _load(MATRIX_PATH)
    morph = next(item for item in matrix["cases"] if item["id"] == "native.morph_weight")
    outline = next(item for item in matrix["cases"] if item["id"] == "native.material_outline")
    morph["script"], outline["script"] = outline["script"], morph["script"]
    morph["domains"] = ["material"]
    outline["proves"] = ["native_cpp_command"]
    assert "mayapy_case_identity" in _errors(
        matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH)
    )


def test_surface_count_owner_and_settings_only_policy_fail_closed():
    matrix = _load(MATRIX_PATH)
    matrix["surface_trace"]["expected_surface_count"] = 229
    matrix["surface_trace"]["version_independent_owner"] = "real_maya.authoring_representatives"
    matrix["surface_trace"]["headless_only_tabs"]["export"] = "wrong"
    errors = _errors(matrix, _load(UI_MANIFEST_PATH), _load(SEMANTIC_MANIFEST_PATH))
    assert {"surface_count", "version_independent_owner", "headless_only_settings", "tab_trace"}.issubset(errors)
