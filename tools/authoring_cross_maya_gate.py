"""Plan and validate the compact Model Authoring cross-Maya matrix.

The module is deliberately Maya-free.  Nox owns process execution while this
module owns fail-closed selection, checked-in manifest links, fresh artifact
validation, and the aggregate evidence schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
MATRIX_ID = "AUTHORING-CROSS-MAYA-MATRIX-1"
REPORT_SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = ("2024", "2026")
SENSITIVE_CHANGE_KINDS = frozenset(("qt6", "maya_api", "native", "serialization"))
FRESHNESS_TOLERANCE_NS = 2_000_000_000
EXPECTED_PROFILES = {
    "focused": {
        "required_maya_versions": ["2024"],
        "case_selection": "selected_domains",
        "run_version_independent_lane_once": True,
    },
    "version_sensitive": {
        "required_maya_versions": ["2024", "2026"],
        "case_selection": "selected_domains",
        "run_version_independent_lane_once": True,
    },
    "release_candidate": {
        "required_maya_versions": ["2024", "2026"],
        "case_selection": "all_representatives",
        "run_version_independent_lane_once": True,
        "require_clean_worktree": True,
    },
}
EXPECTED_CHANGE_KIND_POLICY = {
    "default": "focused",
    "qt6": "version_sensitive",
    "maya_api": "version_sensitive",
    "native": "version_sensitive",
    "serialization": "version_sensitive",
}
EXPECTED_DOMAINS = (
    "main_window",
    "import_export",
    "export",
    "info",
    "material",
    "bone",
    "morph",
    "display_pane",
    "physics",
)
EXPECTED_BOUNDARIES = (
    "production_main_window",
    "domain_mutation",
    "undo_redo",
    "blendshape_dg",
    "native_cpp_command",
    "production_modal",
    "save_reopen",
)
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
EXPECTED_MAYAPY_CASES = {
    "maya.vertex_blendshape": (
        "tools/maya_vertex_morph_authoring_smoke.py",
        False,
        ["morph"],
        ["domain_mutation", "blendshape_dg"],
    ),
    "native.morph_binding_query": (
        "tools/maya_morph_binding_query_smoke.py",
        True,
        ["morph"],
        ["native_cpp_command"],
    ),
    "native.morph_weight": (
        "tools/maya_morph_weight_command_smoke.py",
        True,
        ["morph"],
        ["domain_mutation", "undo_redo", "native_cpp_command"],
    ),
    "native.material_value": (
        "tools/maya_material_value_command_smoke.py",
        True,
        ["material"],
        ["domain_mutation", "undo_redo", "native_cpp_command"],
    ),
    "native.material_outline": (
        "tools/maya_material_outline_command_smoke.py",
        True,
        ["material"],
        ["domain_mutation", "undo_redo", "native_cpp_command"],
    ),
}


class CrossMayaGateError(RuntimeError):
    """Raised when selection or evidence cannot prove the matrix contract."""


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CrossMayaGateError("invalid JSON artifact: {}".format(path)) from exc
    if not isinstance(payload, Mapping):
        raise CrossMayaGateError("JSON artifact must contain an object: {}".format(path))
    return payload


def _resolve_repo_path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CrossMayaGateError("{} must be a non-empty repository path".format(field))
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CrossMayaGateError("{} escapes the repository: {}".format(field, value)) from exc
    return candidate


def _gui_node_id(test_id: str) -> str:
    parts = test_id.split(".")
    if len(parts) != 5 or parts[:2] != ["tests", "gui"]:
        raise CrossMayaGateError("invalid GUI test_id: {}".format(test_id))
    return "tests/gui/{}.py::{}::{}".format(parts[2], parts[3], parts[4])


def load_matrix(root: Path, path: Optional[Path] = None) -> Mapping[str, Any]:
    """Load the checked-in matrix and validate all source links."""
    root = root.resolve()
    matrix_path = path.resolve() if path is not None else root / "tools" / "authoring_cross_maya_matrix.json"
    matrix = load_json(matrix_path)
    if matrix.get("schema_version") != SCHEMA_VERSION or matrix.get("matrix_id") != MATRIX_ID:
        raise CrossMayaGateError("unsupported Authoring cross-Maya matrix schema or id")
    if tuple(matrix.get("supported_maya_versions", ())) != SUPPORTED_VERSIONS:
        raise CrossMayaGateError("supported Maya versions must be exactly 2024 and 2026")
    if matrix.get("profiles") != EXPECTED_PROFILES:
        raise CrossMayaGateError("matrix profile policy is not the exact focused/sensitive/release contract")
    if matrix.get("change_kind_policy") != EXPECTED_CHANGE_KIND_POLICY:
        raise CrossMayaGateError("matrix change-kind escalation policy is not exact")
    if tuple(matrix.get("required_domains", ())) != EXPECTED_DOMAINS:
        raise CrossMayaGateError("matrix required domain set is not exact")
    if tuple(matrix.get("required_boundaries", ())) != EXPECTED_BOUNDARIES:
        raise CrossMayaGateError("matrix required boundary set is not exact")

    sources = matrix.get("source_manifests")
    if not isinstance(sources, Mapping) or set(sources) != {"ui_surfaces", "semantic_contracts"}:
        raise CrossMayaGateError("source_manifests must name UI and semantic inventories")
    ui = load_json(_resolve_repo_path(root, sources["ui_surfaces"], "ui_surfaces"))
    semantic = load_json(
        _resolve_repo_path(root, sources["semantic_contracts"], "semantic_contracts")
    )

    cases = matrix.get("cases")
    if not isinstance(cases, list) or len(cases) != 15 or not all(isinstance(case, Mapping) for case in cases):
        raise CrossMayaGateError("matrix must contain exactly 15 representative cases")
    case_ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
        raise CrossMayaGateError("representative case ids must be unique and non-empty")
    if set(case_ids) != EXPECTED_CASE_IDS:
        raise CrossMayaGateError("matrix representative case identity set is not exact")
    cases_by_id = {case["id"]: case for case in cases}

    trace = matrix.get("surface_trace")
    if not isinstance(trace, Mapping) or trace.get("expected_surface_count") != 230:
        raise CrossMayaGateError("surface trace must require exactly 230 Qt cases")
    selector = trace.get("source_selector")
    if selector != {"disposition": "qt_case"}:
        raise CrossMayaGateError("surface trace selector must be the Qt-case inventory")
    surfaces = [surface for surface in ui.get("surfaces", ()) if surface.get("disposition") == "qt_case"]
    owner = trace.get("version_independent_owner")
    owner_cases = [case for case in ui.get("cases", ()) if case.get("id") == owner]
    if (
        len(surfaces) != 230
        or len(owner_cases) != 1
        or owner_cases[0].get("execution_layer") != "headless_qt"
        or "required_maya_versions" in owner_cases[0]
        or any(surface.get("case_id") != owner for surface in surfaces)
        or any(not surface.get("expected_handler") for surface in surfaces)
    ):
        raise CrossMayaGateError("230-surface headless owner trace is incomplete")
    tab_ids = {tab.get("id") for tab in ui.get("tabs", ())}
    tab_representatives = trace.get("tab_representatives")
    headless_only = trace.get("headless_only_tabs")
    if (
        not isinstance(tab_representatives, Mapping)
        or not isinstance(headless_only, Mapping)
        or set(headless_only) != {"settings"}
        or set(tab_representatives) | set(headless_only) != tab_ids
        or set(tab_representatives) & set(headless_only)
    ):
        raise CrossMayaGateError("each UI tab must trace to a representative or Settings rationale")
    for tab_id, representative_ids in tab_representatives.items():
        if not isinstance(representative_ids, list) or not representative_ids:
            raise CrossMayaGateError("tab {} has no representative".format(tab_id))
        for case_id in representative_ids:
            case = cases_by_id.get(case_id)
            if case is None or tab_id not in case.get("domains", ()):
                raise CrossMayaGateError("tab {} has an invalid representative {}".format(tab_id, case_id))

    contracts = semantic.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise CrossMayaGateError("semantic contract inventory is empty")
    contracts_by_id = {
        contract.get("contract_id"): contract
        for contract in contracts
        if isinstance(contract, Mapping) and contract.get("contract_id")
    }
    for case in cases:
        runner = case.get("runner")
        if runner == "gui_batch":
            test_id = case.get("test_id")
            if not isinstance(test_id, str):
                raise CrossMayaGateError("GUI case has no exact test_id: {}".format(case.get("id")))
            if case.get("test_filter") != test_id.rsplit(".", 1)[-1]:
                raise CrossMayaGateError("GUI case filter does not match its exact test id")
            if "contract_id" in case:
                contract = contracts_by_id.get(case.get("contract_id"))
                if contract is None:
                    raise CrossMayaGateError("GUI case references a missing semantic contract")
                members = [contract.get("representative_test")]
                related = contract.get("related_representative_tests", ())
                if isinstance(related, list):
                    members.extend(related)
                if _gui_node_id(test_id) not in members:
                    raise CrossMayaGateError("GUI case is not a member of its semantic contract")
            elif case.get("origin_kind") != "matrix_policy" or not case.get("rationale"):
                raise CrossMayaGateError("uncontracted GUI case has no matrix-policy rationale")
        elif runner == "mayapy":
            script = _resolve_repo_path(root, case.get("script"), "mayapy script")
            if not script.is_file() or script.parent != (root / "tools").resolve():
                raise CrossMayaGateError("mayapy representative script is missing: {}".format(script))
            if case.get("origin_kind") not in {"standalone_smoke", "native_smoke"} or not case.get("rationale"):
                raise CrossMayaGateError("mayapy case has no explicit origin rationale")
            if type(case.get("requires_cpp_plugin")) is not bool:
                raise CrossMayaGateError("mayapy case has no exact plugin requirement")
            expected = EXPECTED_MAYAPY_CASES.get(case.get("id"))
            observed = (
                case.get("script"),
                case.get("requires_cpp_plugin"),
                case.get("domains"),
                case.get("proves"),
            )
            if expected is None or observed != expected:
                raise CrossMayaGateError("mayapy case identity or semantic scope is not exact")
        else:
            raise CrossMayaGateError("unknown matrix runner: {}".format(runner))
    return matrix


def build_plan(
    matrix: Mapping[str, Any],
    requested_profile: str,
    domains: Sequence[str],
    change_kinds: Sequence[str],
) -> Mapping[str, Any]:
    """Resolve profile escalation, versions, and representative cases."""
    profiles = matrix.get("profiles", {})
    if requested_profile not in profiles:
        raise CrossMayaGateError("unknown profile: {}".format(requested_profile))
    change_policy = matrix.get("change_kind_policy", {})
    unknown_kinds = sorted(set(change_kinds) - set(change_policy) - {"default"})
    if unknown_kinds:
        raise CrossMayaGateError("unknown change kind(s): {}".format(", ".join(unknown_kinds)))
    effective_profile = requested_profile
    if requested_profile == "focused" and set(change_kinds) & SENSITIVE_CHANGE_KINDS:
        effective_profile = "version_sensitive"
    profile = profiles[effective_profile]
    selection = profile.get("case_selection")
    required_domains = set(EXPECTED_DOMAINS)
    selected_domains = tuple(dict.fromkeys(domains))
    if selection == "selected_domains":
        if not selected_domains:
            raise CrossMayaGateError("{} requires at least one --domain".format(effective_profile))
        unknown_domains = sorted(set(selected_domains) - required_domains)
        if unknown_domains:
            raise CrossMayaGateError("unknown domain(s): {}".format(", ".join(unknown_domains)))
        selected_cases = [
            case
            for case in matrix["cases"]
            if case.get("id") == "gui.main_window_refresh"
            or set(case.get("domains", ())) & set(selected_domains)
        ]
    elif selection == "all_representatives":
        if selected_domains:
            raise CrossMayaGateError("release_candidate does not accept domain filtering")
        selected_cases = list(matrix["cases"])
    else:
        raise CrossMayaGateError("unknown case selection policy: {}".format(selection))
    versions = tuple(profile.get("required_maya_versions", ()))
    if not versions or any(version not in SUPPORTED_VERSIONS for version in versions):
        raise CrossMayaGateError("profile has unsupported or empty Maya versions")
    if tuple(versions) not in (("2024",), SUPPORTED_VERSIONS):
        raise CrossMayaGateError("profile may not omit Maya 2024 or reorder the matrix")
    return {
        "requested_profile": requested_profile,
        "effective_profile": effective_profile,
        "versions": versions,
        "domains": selected_domains,
        "change_kinds": tuple(dict.fromkeys(change_kinds)),
        "cases": tuple(selected_cases),
        "run_version_independent_lane_once": profile.get("run_version_independent_lane_once") is True,
        "require_clean_worktree": profile.get("require_clean_worktree") is True,
    }


def gui_batch_manifest(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    """Build the strict existing GUI-runner manifest for selected cases."""
    cases = [
        {
            "id": case["id"],
            "test_path": case["test_path"],
            "test_filter": case["test_filter"],
        }
        for case in plan["cases"]
        if case.get("runner") == "gui_batch"
    ]
    if not cases:
        raise CrossMayaGateError("selected matrix has no GUI representative")
    return {"schema_version": 1, "cases": cases}


def validate_gui_timing_report(
    report: Mapping[str, Any], version: str, selected_cases: Sequence[Mapping[str, Any]]
) -> None:
    """Require exact one-test PASS evidence for every selected GUI case."""
    expected = [case for case in selected_cases if case.get("runner") == "gui_batch"]
    if (
        report.get("schema_version") != 1
        or report.get("runner") != "maya_gui_batch"
        or str(report.get("maya_version")) != version
        or report.get("status") != "PASS"
    ):
        raise CrossMayaGateError("Maya {} GUI batch report is not an exact PASS".format(version))
    observed = report.get("cases")
    if not isinstance(observed, list) or [case.get("id") for case in observed] != [case["id"] for case in expected]:
        raise CrossMayaGateError("Maya {} GUI batch case identity/order mismatch".format(version))
    if report.get("case_counts") != {"PASS": len(expected)}:
        raise CrossMayaGateError("Maya {} GUI batch contains failed, skipped, or unrun cases".format(version))
    for expected_case, observed_case in zip(expected, observed):
        tests = observed_case.get("tests")
        if (
            observed_case.get("status") != "PASS"
            or observed_case.get("test_counts") != {"success": 1}
            or not isinstance(tests, list)
            or len(tests) != 1
            or tests[0].get("id") != expected_case["test_id"]
            or tests[0].get("status") != "success"
        ):
            raise CrossMayaGateError(
                "Maya {} GUI case {} did not run exactly one expected test".format(
                    version, expected_case["id"]
                )
            )


def expected_headless_test_identities(
    root: Path, matrix: Mapping[str, Any]
) -> tuple[tuple[str, str], ...]:
    """Derive the exact 230 parametrized tests plus their owner contract test."""
    sources = matrix.get("source_manifests")
    if not isinstance(sources, Mapping):
        raise CrossMayaGateError("matrix has no source manifests for headless identities")
    ui = load_json(_resolve_repo_path(root.resolve(), sources.get("ui_surfaces"), "ui_surfaces"))
    surfaces = [
        surface
        for surface in ui.get("surfaces", ())
        if isinstance(surface, Mapping) and surface.get("disposition") == "qt_case"
    ]
    surface_ids = [surface.get("id") for surface in surfaces]
    if (
        len(surface_ids) != 230
        or any(not isinstance(surface_id, str) or not surface_id for surface_id in surface_ids)
        or len(set(surface_ids)) != 230
    ):
        raise CrossMayaGateError("headless identity source is not exactly 230 unique Qt cases")
    classname = "tests.unit.test_authoring_ui_surface_matrix"
    identities = [
        (classname, "test_authoring_surface_dispatches_exactly_once[{}]".format(surface_id))
        for surface_id in surface_ids
    ]
    identities.append(
        (classname, "test_headless_matrix_owns_all_230_qt_cases_without_maya_claims")
    )
    return tuple(identities)


def validate_headless_junit(
    path: Path, root: Path, matrix: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Require exact JUnit identities and PASS status for all 231 headless tests."""
    try:
        document = ET.parse(str(path))
    except (OSError, ET.ParseError) as exc:
        raise CrossMayaGateError("invalid headless JUnit artifact: {}".format(path)) from exc
    xml_root = document.getroot()
    suites = [element for element in xml_root.iter() if element.tag.rsplit("}", 1)[-1] == "testsuite"]
    if len(suites) != 1:
        raise CrossMayaGateError("headless JUnit must contain exactly one test suite")
    suite = suites[0]
    expected = expected_headless_test_identities(root, matrix)
    expected_set = set(expected)
    expected_counts = {
        "tests": str(len(expected)),
        "errors": "0",
        "failures": "0",
        "skipped": "0",
    }
    if any(suite.get(key) != value for key, value in expected_counts.items()):
        raise CrossMayaGateError("headless JUnit counts are not exactly 231 PASS")
    testcases = [
        element for element in suite if element.tag.rsplit("}", 1)[-1] == "testcase"
    ]
    observed = [(case.get("classname"), case.get("name")) for case in testcases]
    if len(observed) != len(expected) or len(set(observed)) != len(expected) or set(observed) != expected_set:
        raise CrossMayaGateError("headless JUnit test identities are not the exact 231-test contract")
    forbidden = {"failure", "error", "skipped"}
    if any(
        child.tag.rsplit("}", 1)[-1] in forbidden
        for case in testcases
        for child in case
    ):
        raise CrossMayaGateError("headless JUnit contains a failed, errored, or skipped test")
    surface_prefix = "test_authoring_surface_dispatches_exactly_once["
    return {
        "test_count": len(observed),
        "surface_test_count": sum(name.startswith(surface_prefix) for _classname, name in observed),
        "test_identities": ["{}::{}".format(classname, name) for classname, name in expected],
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_identity(path: Path, started_ns: int, kind: str) -> Mapping[str, Any]:
    """Return provenance for one fresh, non-empty artifact."""
    path = path.resolve()
    try:
        stat = path.stat()
    except OSError as exc:
        raise CrossMayaGateError("missing {} artifact: {}".format(kind, path)) from exc
    if not path.is_file() or stat.st_size <= 0:
        raise CrossMayaGateError("empty {} artifact: {}".format(kind, path))
    if stat.st_mtime_ns + FRESHNESS_TOLERANCE_NS < started_ns:
        raise CrossMayaGateError("stale {} artifact: {}".format(kind, path))
    return {
        "kind": kind,
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def plugin_identity(path: Path, version: str) -> Mapping[str, Any]:
    """Require the exact Debug plugin path and record its immutable digest."""
    path = path.resolve()
    expected_tail = ("plug-ins", version, "Debug", "mmd_tools_cpp.mll")
    if tuple(path.parts[-4:]) != expected_tail or not path.is_file() or path.stat().st_size <= 0:
        raise CrossMayaGateError("missing exact Maya {} Debug plugin: {}".format(version, path))
    return {
        "maya_version": version,
        "config": "Debug",
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def source_identity(root: Path) -> Mapping[str, Any]:
    """Capture the exact Git HEAD and current porcelain paths."""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.STDOUT
        ).strip()
        porcelain = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True, stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CrossMayaGateError("could not capture Git source identity") from exc
    dirty_paths = sorted(line[3:] for line in porcelain.splitlines() if len(line) >= 4)
    if len(head) != 40:
        raise CrossMayaGateError("Git HEAD is not a full SHA")
    return {"head": head, "dirty_paths": dirty_paths}


def write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def parse_request(argv: Sequence[str]) -> Mapping[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("focused", "version_sensitive", "release_candidate"),
        default="focused",
    )
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--change-kind", action="append", default=[])
    parser.add_argument("--out-dir", default="build/reports/authoring-cross-maya")
    parser.add_argument("--verbose", action="store_true")
    values = parser.parse_args(list(argv))
    return vars(values)


def new_report(plan: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "matrix_id": MATRIX_ID,
        "status": "running",
        "started_ns": time.time_ns(),
        "source": dict(source),
        "selection": {
            "requested_profile": plan["requested_profile"],
            "effective_profile": plan["effective_profile"],
            "versions": list(plan["versions"]),
            "domains": list(plan["domains"]),
            "change_kinds": list(plan["change_kinds"]),
            "case_ids": [case["id"] for case in plan["cases"]],
        },
        "headless": None,
        "versions": [],
    }
