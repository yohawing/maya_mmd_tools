"""Structural and evidence validation for the MainWindow UI coverage manifest.

The manifest is deliberately independent from Maya.  A normal invocation only
checks that every tab/surface has an unambiguous mapping.  A report is an
optional second input and is the only mode that evaluates Qt-case evidence.

The report input is a small aggregate JSON object::

    {"schema_version": 1, "gate_id": "V070-UI-COVERAGE-1",
     "cases": [{"case_id": "gui.fileio_safe_routes", "status": "pass",
                 "maya_versions": ["2024", "2026"]}],
     "surfaces": [{"surface_id": "import_export.import_model",
                   "case_id": "gui.fileio_safe_routes",
                   "attribute": "import_button", "status": "pass",
                   "runtime_witness": {"interaction": "click",
                       "fired_action": "import", "oracle": "model_loaded",
                       "action_count": 1}}]}

The gate checks report case IDs, required Maya versions, and every manifest
surface marked ``qt_case``.  It does not infer evidence from the number of
tests or from a raw log filename.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


GATE_ID = "V070-UI-COVERAGE-1"
SCHEMA_VERSION = 1
DISPOSITIONS = {"qt_case", "blocked", "not_run", "excluded"}
EXECUTION_LAYERS = {"headless_qt", "real_maya", "persistence"}
TAB_IDS = (
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
_PASS_STATUSES = {"pass", "passed", "ok", "success", "succeeded"}
_RUNTIME_WITNESS_TEXT_FIELDS = ("interaction", "fired_action", "oracle")
_RUNTIME_WITNESS_MARKER = "[UI COVERAGE WITNESS] "
_HANDLER_PATH = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){2,}$")


def _error(code: str, path: str, message: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _normalise_versions(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def _as_entries(value: Any, *, path: str, errors: List[Dict[str, str]]) -> List[Mapping[str, Any]]:
    """Accept the human-friendly list form and reject malformed entries."""
    if not isinstance(value, list):
        errors.append(_error("invalid_entries", path, "expected a list"))
        return []
    entries: List[Mapping[str, Any]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            errors.append(_error("invalid_entry", f"{path}[{index}]", "expected an object"))
            continue
        entries.append(entry)
    return entries


def validate_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate manifest structure and return a JSON-serialisable result.

    This function intentionally does not require Qt or Maya evidence.  That
    distinction keeps a checked-in inventory useful before smoke infrastructure
    exists and prevents a unit-test count from being mistaken for coverage.
    """
    errors: List[Dict[str, str]] = []
    if not isinstance(manifest, Mapping):
        return {"valid": False, "errors": [_error("invalid_manifest", "$", "expected an object")]}

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(_error("invalid_schema_version", "schema_version", "must be 1"))
    if manifest.get("gate_id") != GATE_ID:
        errors.append(_error("invalid_gate_id", "gate_id", f"must be {GATE_ID}"))

    tabs = _as_entries(manifest.get("tabs"), path="tabs", errors=errors)
    tab_ids: List[str] = []
    tab_selectors: Dict[str, str] = {}
    tab_attributes: Dict[str, str] = {}
    for index, tab in enumerate(tabs):
        tab_id = tab.get("id")
        if not isinstance(tab_id, str) or not tab_id.strip():
            errors.append(_error("invalid_tab_id", f"tabs[{index}].id", "must be non-empty"))
            continue
        if tab_id in tab_ids:
            errors.append(_error("duplicate_tab_id", f"tabs[{index}].id", tab_id))
        tab_ids.append(tab_id)
        tab_locators = []
        for field in ("selector", "attribute"):
            value = tab.get(field)
            if isinstance(value, str) and value.strip():
                tab_locators.append((field, value.strip()))
        if len(tab_locators) != 1:
            errors.append(
                _error(
                    "invalid_tab_selector",
                    f"tabs[{index}]",
                    "exactly one non-empty selector or attribute is required",
                )
            )
        else:
            field, locator = tab_locators[0]
            seen = tab_selectors if field == "selector" else tab_attributes
            if locator in seen:
                errors.append(_error("duplicate_selector", f"tabs[{index}].{field}", locator))
            else:
                seen[locator] = tab_id
    missing_tabs = sorted(set(TAB_IDS) - set(tab_ids))
    extra_tabs = sorted(set(tab_ids) - set(TAB_IDS))
    if missing_tabs:
        errors.append(_error("missing_tabs", "tabs", ", ".join(missing_tabs)))
    if extra_tabs:
        errors.append(_error("unknown_tab", "tabs", ", ".join(extra_tabs)))

    cases = _as_entries(manifest.get("cases"), path="cases", errors=errors)
    case_ids: List[str] = []
    case_versions: Dict[str, List[str]] = {}
    for index, case in enumerate(cases):
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(_error("invalid_case_id", f"cases[{index}].id", "must be non-empty"))
            continue
        if case_id in case_ids:
            errors.append(_error("duplicate_case_id", f"cases[{index}].id", case_id))
        case_ids.append(case_id)
        execution_layer = case.get("execution_layer")
        if execution_layer not in EXECUTION_LAYERS:
            errors.append(
                _error(
                    "invalid_execution_layer",
                    f"cases[{index}].execution_layer",
                    "must be headless_qt, real_maya, or persistence",
                )
            )
        has_versions_field = "required_maya_versions" in case
        versions = _normalise_versions(case.get("required_maya_versions"))
        if execution_layer == "headless_qt" and has_versions_field:
            errors.append(
                _error(
                    "headless_case_has_maya_versions",
                    f"cases[{index}].required_maya_versions",
                    "headless_qt evidence must not claim Maya versions",
                )
            )
        elif execution_layer != "headless_qt" and not versions:
            errors.append(
                _error(
                    "invalid_required_versions",
                    f"cases[{index}].required_maya_versions",
                    "must be a non-empty list",
                )
            )
        elif len(versions) != len(set(versions)):
            errors.append(
                _error(
                    "duplicate_required_version",
                    f"cases[{index}].required_maya_versions",
                    case_id,
                )
            )
        case_versions[case_id] = versions

    surfaces = _as_entries(manifest.get("surfaces"), path="surfaces", errors=errors)
    surface_ids: List[str] = []
    # Tab selectors are declared in both ``tabs`` metadata and the surface
    # inventory so that reports can address the selector like any other UI
    # surface.  They are checked for duplicates within their own collections;
    # the two declarations intentionally describe the same tab widget.
    selectors: Dict[str, str] = {}
    attributes: Dict[str, str] = {}
    for index, surface in enumerate(surfaces):
        path = f"surfaces[{index}]"
        surface_id = surface.get("id")
        if not isinstance(surface_id, str) or not surface_id.strip():
            errors.append(_error("invalid_surface_id", f"{path}.id", "must be non-empty"))
        elif surface_id in surface_ids:
            errors.append(_error("duplicate_surface_id", f"{path}.id", surface_id))
        else:
            surface_ids.append(surface_id)

        kind = surface.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            errors.append(_error("invalid_surface_kind", f"{path}.kind", "must be non-empty"))

        tab_id = surface.get("tab")
        if tab_id not in TAB_IDS:
            errors.append(_error("unknown_surface_tab", f"{path}.tab", str(tab_id)))

        nonempty_locators = []
        for field in ("selector", "attribute"):
            value = surface.get(field)
            if isinstance(value, str) and value.strip():
                nonempty_locators.append((field, value.strip()))
        if len(nonempty_locators) != 1:
            errors.append(
                _error(
                    "invalid_locator",
                    path,
                    "exactly one non-empty selector or attribute is required",
                )
            )
        for field, value in nonempty_locators:
            if field == "selector":
                if value in selectors:
                    errors.append(_error("duplicate_selector", f"{path}.selector", value))
                else:
                    selectors[value] = surface_id if isinstance(surface_id, str) else path
            elif value in attributes:
                errors.append(_error("duplicate_attribute", f"{path}.attribute", value))
            else:
                attributes[value] = surface_id if isinstance(surface_id, str) else path

        disposition = surface.get("disposition")
        if disposition not in DISPOSITIONS:
            errors.append(_error("unmapped_disposition", f"{path}.disposition", str(disposition)))
            continue
        has_case = isinstance(surface.get("case_id"), str) and bool(surface.get("case_id", "").strip())
        has_reason_code = isinstance(surface.get("reason_code"), str) and bool(
            surface.get("reason_code", "").strip()
        )
        has_reason = isinstance(surface.get("reason"), str) and bool(surface.get("reason", "").strip())
        if disposition == "qt_case":
            if not has_case:
                errors.append(_error("missing_case_id", f"{path}.case_id", "qt_case requires case_id"))
            elif surface.get("case_id") not in case_ids:
                errors.append(_error("unknown_case", f"{path}.case_id", str(surface.get("case_id"))))
            if has_reason_code or has_reason:
                errors.append(_error("invalid_reason_fields", path, "qt_case cannot have reason fields"))
            expected_handler = surface.get("expected_handler")
            if not isinstance(expected_handler, str) or not _HANDLER_PATH.fullmatch(
                expected_handler.strip()
            ):
                errors.append(
                    _error(
                        "invalid_expected_handler",
                        f"{path}.expected_handler",
                        "qt_case requires a fully-qualified production handler",
                    )
                )
        else:
            if not has_reason_code or not has_reason:
                errors.append(
                    _error(
                        "invalid_reason_fields",
                        path,
                        f"{disposition} requires non-empty reason_code and reason",
                    )
                )
            if has_case:
                errors.append(_error("invalid_reason_fields", path, f"{disposition} cannot have case_id"))

    unmapped = manifest.get("unmapped_surfaces", [])
    if unmapped != []:
        errors.append(_error("unmapped_surfaces", "unmapped_surfaces", "must be an empty list"))

    disposition_counts = {
        disposition: sum(surface.get("disposition") == disposition for surface in surfaces)
        for disposition in sorted(DISPOSITIONS)
    }
    minimum_qt_cases = manifest.get("minimum_qt_case_surfaces")
    if minimum_qt_cases is not None:
        if isinstance(minimum_qt_cases, bool) or not isinstance(minimum_qt_cases, int):
            errors.append(
                _error(
                    "invalid_minimum_qt_case_surfaces",
                    "minimum_qt_case_surfaces",
                    "must be an integer",
                )
            )
        elif disposition_counts["qt_case"] < minimum_qt_cases:
            errors.append(
                _error(
                    "insufficient_qt_case_surfaces",
                    "minimum_qt_case_surfaces",
                    f"requires {minimum_qt_cases}, found {disposition_counts['qt_case']}",
                )
            )

    return {
        "valid": not errors,
        "errors": errors,
        "surface_count": len(surfaces),
        "tab_count": len(tab_ids),
        "case_count": len(case_ids),
        "case_versions": case_versions,
        "disposition_counts": disposition_counts,
    }


def _entry_map(entries: Sequence[Mapping[str, Any]], key: str) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            result[value] = entry
    return result


def _validate_runtime_witness(
    evidence: Mapping[str, Any], *, path: str, errors: List[Dict[str, str]]
) -> None:
    """Require a witness produced by the runtime interaction, not a test name."""
    witness = evidence.get("runtime_witness")
    if witness is None:
        errors.append(_error("missing_runtime_witness", f"{path}.runtime_witness", "required"))
        return
    if not isinstance(witness, Mapping):
        errors.append(_error("invalid_runtime_witness", f"{path}.runtime_witness", "must be an object"))
        return
    for field in _RUNTIME_WITNESS_TEXT_FIELDS:
        value = witness.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(
                _error(
                    "invalid_runtime_witness_field",
                    f"{path}.runtime_witness.{field}",
                    "must be a non-empty string",
                )
            )
    action_count = witness.get("action_count")
    if isinstance(action_count, bool) or not isinstance(action_count, int) or action_count != 1:
        errors.append(
            _error(
                "invalid_runtime_witness_action_count",
                f"{path}.runtime_witness.action_count",
                "must be exactly 1",
            )
        )


def validate_report(manifest: Mapping[str, Any], report: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate optional Qt evidence against a structurally valid manifest."""
    structural = validate_manifest(manifest)
    errors: List[Dict[str, str]] = list(structural["errors"])
    if not isinstance(report, Mapping):
        errors.append(_error("invalid_report", "$", "expected an object"))
        return {"valid": False, "errors": errors, "structural": structural, "evidence_checked": True}
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(_error("invalid_report_schema_version", "report.schema_version", "must be 1"))
    if report.get("gate_id") != GATE_ID:
        errors.append(_error("invalid_report_gate_id", "report.gate_id", f"must be {GATE_ID}"))

    manifest_cases = _entry_map(_as_entries(manifest.get("cases"), path="cases", errors=[]), "id")
    manifest_surfaces = _entry_map(_as_entries(manifest.get("surfaces"), path="surfaces", errors=[]), "id")
    case_entries = _as_entries(report.get("cases", []), path="report.cases", errors=errors)
    seen_report_cases = set()
    for index, case in enumerate(case_entries):
        case_id = case.get("case_id")
        if isinstance(case_id, str):
            if case_id in seen_report_cases:
                errors.append(_error("duplicate_report_case", f"report.cases[{index}].case_id", case_id))
            seen_report_cases.add(case_id)
    report_cases = _entry_map(case_entries, "case_id")
    for index, case in enumerate(case_entries):
        case_id = case.get("case_id")
        if case_id not in manifest_cases:
            errors.append(_error("unknown_report_case", f"report.cases[{index}].case_id", str(case_id)))

    # A report may be partial, but every declared case that is needed by a
    # qt_case surface must be present with all of its required Maya versions.
    required_case_ids = {
        surface.get("case_id")
        for surface in manifest_surfaces.values()
        if surface.get("disposition") == "qt_case"
    }
    for case_id, case in manifest_cases.items():
        if case_id not in required_case_ids and case_id not in report_cases:
            continue
        evidence = report_cases.get(case_id)
        if evidence is None:
            errors.append(_error("missing_case_evidence", f"report.cases[{case_id}]", "case evidence is required"))
            continue
        case_status = evidence.get("status")
        if str(case_status or "").lower() not in _PASS_STATUSES:
            errors.append(
                _error(
                    "incomplete_case_evidence",
                    f"report.cases[{case_id}].status",
                    "must be pass",
                )
            )
        required_versions = set(_normalise_versions(case.get("required_maya_versions")))
        observed_versions = set(_normalise_versions(evidence.get("maya_versions")))
        missing_versions = sorted(required_versions - observed_versions)
        if missing_versions:
            errors.append(
                _error(
                    "missing_required_version_evidence",
                    f"report.cases[{case_id}].maya_versions",
                    ", ".join(missing_versions),
                )
            )

    surface_entries = _as_entries(report.get("surfaces", []), path="report.surfaces", errors=errors)
    seen_report_surfaces = set()
    for index, evidence in enumerate(surface_entries):
        surface_id = evidence.get("surface_id")
        if isinstance(surface_id, str):
            if surface_id in seen_report_surfaces:
                errors.append(
                    _error(
                        "duplicate_report_surface",
                        f"report.surfaces[{index}].surface_id",
                        surface_id,
                    )
                )
            seen_report_surfaces.add(surface_id)
    report_surfaces = _entry_map(surface_entries, "surface_id")
    for index, evidence in enumerate(surface_entries):
        surface_id = evidence.get("surface_id")
        if surface_id not in manifest_surfaces:
            errors.append(_error("unknown_report_surface", f"report.surfaces[{index}].surface_id", str(surface_id)))

    for surface_id, surface in manifest_surfaces.items():
        if surface.get("disposition") != "qt_case":
            continue
        evidence = report_surfaces.get(surface_id)
        if evidence is None:
            errors.append(_error("missing_surface_evidence", f"report.surfaces[{surface_id}]", "qt_case evidence is required"))
            continue
        if str(evidence.get("status", "")).lower() not in _PASS_STATUSES:
            errors.append(_error("incomplete_surface_evidence", f"report.surfaces[{surface_id}].status", "must be pass"))
        if evidence.get("case_id") != surface.get("case_id"):
            errors.append(_error("surface_case_mismatch", f"report.surfaces[{surface_id}].case_id", str(evidence.get("case_id"))))
        expected_locator = surface.get("selector") or surface.get("attribute")
        observed_locator = evidence.get("selector") or evidence.get("attribute")
        if observed_locator != expected_locator:
            errors.append(_error("selector_mismatch", f"report.surfaces[{surface_id}]", str(observed_locator)))
        _validate_runtime_witness(
            evidence,
            path=f"report.surfaces[{surface_id}]",
            errors=errors,
        )
        runtime_witness = evidence.get("runtime_witness")
        if isinstance(runtime_witness, Mapping) and runtime_witness.get(
            "fired_action"
        ) != surface.get("expected_handler"):
            errors.append(
                _error(
                    "handler_mismatch",
                    f"report.surfaces[{surface_id}].runtime_witness.fired_action",
                    str(runtime_witness.get("fired_action")),
                )
            )

    return {
        "valid": not errors,
        "errors": errors,
        "structural": structural,
        "evidence_checked": True,
        "required_case_count": len(required_case_ids),
    }


def load_json(path: Path) -> Mapping[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _evidence_passes(path: Path, version: str) -> bool:
    """Verify one real GUI artifact without trusting a hand-authored summary."""
    if path.suffix.lower() == ".json":
        payload = load_json(path)
        return (
            str(payload.get("status", "")).lower() in _PASS_STATUSES
            and str(payload.get("maya_version", "")).startswith(version)
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    test_count = re.search(r"\bRan\s+(\d+)\s+tests?\b", text)
    filename_has_version = version in path.name
    return (
        "//-- GUI TEST FINISHED --// status=PASS" in text
        and test_count is not None
        and int(test_count.group(1)) > 0
        and filename_has_version
    )


def _runtime_witness_contract(evidence: Mapping[str, Any], *, path: str) -> Dict[str, Any]:
    """Return only the stable runtime witness fields used for aggregation."""
    errors: List[Dict[str, str]] = []
    _validate_runtime_witness(evidence, path=path, errors=errors)
    if errors:
        error = errors[0]
        raise ValueError(f"{error['code']} at {error['path']}: {error['message']}")
    witness = evidence["runtime_witness"]
    return {field: witness[field] for field in (*_RUNTIME_WITNESS_TEXT_FIELDS, "action_count")}


def _surface_contract(
    evidence: Mapping[str, Any], *, path: str, expected: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """Validate and return the identity/locator/action contract for one surface."""
    surface_id = evidence.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id.strip():
        raise ValueError(f"invalid_surface_id at {path}.surface_id")
    if str(evidence.get("status", "")).lower() not in _PASS_STATUSES:
        raise ValueError(f"incomplete_surface_evidence at {path}.status: must be pass")
    case_id = evidence.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"invalid_case_id at {path}.case_id")
    locators = [
        (field, evidence.get(field).strip())
        for field in ("selector", "attribute")
        if isinstance(evidence.get(field), str) and evidence.get(field).strip()
    ]
    if len(locators) != 1:
        raise ValueError(f"invalid_locator at {path}: exactly one locator is required")
    locator_key, locator = locators[0]
    contract = {
        "surface_id": surface_id,
        "case_id": case_id,
        "status": "pass",
        locator_key: locator,
        "runtime_witness": _runtime_witness_contract(evidence, path=path),
    }
    if expected is not None:
        if case_id != expected.get("case_id"):
            raise ValueError(
                f"surface_case_mismatch at {path}.case_id: expected {expected.get('case_id')}"
            )
        expected_locator_key = "selector" if "selector" in expected else "attribute"
        if locator_key != expected_locator_key or locator != expected.get(expected_locator_key):
            raise ValueError(
                f"selector_mismatch at {path}: expected {expected_locator_key}={expected.get(expected_locator_key)}"
            )
    return contract


def _manifest_surface_contract(surface: Mapping[str, Any]) -> Dict[str, Any]:
    locator_key = "selector" if "selector" in surface else "attribute"
    return {
        "surface_id": surface.get("id"),
        "case_id": surface.get("case_id"),
        locator_key: surface.get(locator_key),
    }


def _collect_surface_contracts(
    entries: Any,
    *,
    expected_surfaces: Mapping[str, Mapping[str, Any]],
    path: str,
) -> Dict[str, Dict[str, Any]]:
    """Collect known surfaces and reject duplicate or malformed witnesses."""
    if not isinstance(entries, list):
        raise ValueError(f"missing_runtime_witness at {path}: surfaces must be a list")
    result: Dict[str, Dict[str, Any]] = {}
    for index, evidence in enumerate(entries):
        if not isinstance(evidence, Mapping):
            raise ValueError(f"invalid_surface_evidence at {path}[{index}]: expected an object")
        surface_id = evidence.get("surface_id")
        if surface_id not in expected_surfaces:
            raise ValueError(f"unknown_runtime_witness at {path}[{index}].surface_id: {surface_id}")
        if surface_id in result:
            raise ValueError(f"duplicate_runtime_witness at {path}[{index}].surface_id: {surface_id}")
        result[surface_id] = _surface_contract(
            evidence,
            path=f"{path}[{index}]",
            expected=expected_surfaces[surface_id],
        )
    missing = sorted(set(expected_surfaces) - set(result))
    if missing:
        raise ValueError(f"missing_runtime_witness at {path}: {', '.join(missing)}")
    return result


def _parse_runtime_witness_markers(text: str, *, path: str) -> List[Mapping[str, Any]]:
    markers: List[Mapping[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line.startswith(_RUNTIME_WITNESS_MARKER):
            continue
        raw = line[len(_RUNTIME_WITNESS_MARKER) :].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_runtime_witness at {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"invalid_runtime_witness at {path}:{line_number}: expected an object")
        markers.append(payload)
    return markers


def _aggregate_version_surfaces(
    per_version: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> List[Dict[str, Any]]:
    versions = list(per_version)
    if not versions:
        return []
    baseline = per_version[versions[0]]
    for version in versions[1:]:
        for surface_id, expected in baseline.items():
            if per_version[version].get(surface_id) != expected:
                raise ValueError(
                    f"runtime_witness_mismatch for {surface_id}: Maya {versions[0]} vs Maya {version}"
                )
    return [baseline[surface_id] for surface_id in sorted(baseline)]


def build_report_from_evidence(manifest: Mapping[str, Any], repo_root: Path) -> Dict[str, Any]:
    """Build aggregate evidence from structured per-version runtime reports."""
    required_case_ids = {
        surface["case_id"]
        for surface in manifest.get("surfaces", [])
        if surface.get("disposition") == "qt_case"
    }
    cases_by_id = _entry_map(manifest.get("cases", []), "id")
    surfaces_by_case: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for surface in manifest.get("surfaces", []):
        if surface.get("disposition") != "qt_case":
            continue
        surfaces_by_case.setdefault(surface["case_id"], {})[surface["id"]] = _manifest_surface_contract(surface)
    cases = []
    per_version_surfaces: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for case_id in sorted(required_case_ids):
        case = cases_by_id.get(case_id, {})
        versions = _normalise_versions(case.get("required_maya_versions"))
        evidence_files = case.get("evidence_files")
        if not isinstance(evidence_files, Mapping):
            raise ValueError(f"case {case_id} has no evidence_files mapping")
        for version in versions:
            relative_path = evidence_files.get(version)
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError(f"case {case_id} has no evidence file for Maya {version}")
            evidence_path = (repo_root / relative_path).resolve()
            if not evidence_path.is_file() or not _evidence_passes(evidence_path, version):
                raise ValueError(f"case {case_id} evidence failed for Maya {version}: {evidence_path}")
            if evidence_path.suffix.lower() != ".json":
                raise ValueError(
                    f"case {case_id} evidence must be structured JSON for runtime witnesses: {evidence_path}"
                )
            payload = load_json(evidence_path)
            observed = _collect_surface_contracts(
                payload.get("surfaces"),
                expected_surfaces=surfaces_by_case.get(case_id, {}),
                path=f"{evidence_path}.surfaces",
            )
            for surface_id, surface_evidence in observed.items():
                previous = per_version_surfaces.setdefault(version, {}).get(surface_id)
                if previous is not None and previous != surface_evidence:
                    raise ValueError(f"runtime_witness_mismatch for {surface_id} in Maya {version}")
                per_version_surfaces.setdefault(version, {})[surface_id] = surface_evidence
        cases.append({"case_id": case_id, "status": "pass", "maya_versions": versions})

    surfaces = _aggregate_version_surfaces(per_version_surfaces)
    return {
        "schema_version": SCHEMA_VERSION,
        "gate_id": GATE_ID,
        "coverage": validate_manifest(manifest)["disposition_counts"],
        "cases": cases,
        "surfaces": surfaces,
    }


def build_report_from_batch_logs(
    manifest: Mapping[str, Any], batch_logs: Mapping[str, Path]
) -> Dict[str, Any]:
    """Build evidence from fresh logs carrying deterministic runtime markers."""
    required_case_ids = {
        surface["case_id"]
        for surface in manifest.get("surfaces", [])
        if surface.get("disposition") == "qt_case"
    }
    cases_by_id = _entry_map(manifest.get("cases", []), "id")
    expected_surfaces: Dict[str, Mapping[str, Any]] = {}
    for surface in manifest.get("surfaces", []):
        if surface.get("disposition") != "qt_case":
            continue
        expected_surfaces[surface["id"]] = _manifest_surface_contract(surface)
    cases = []
    per_version_surfaces: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for case_id in sorted(required_case_ids):
        case = cases_by_id.get(case_id, {})
        versions = _normalise_versions(case.get("required_maya_versions"))
        test_ids = case.get("evidence_tests")
        if not isinstance(test_ids, list) or not test_ids or not all(
            isinstance(test_id, str) and test_id for test_id in test_ids
        ):
            raise ValueError(f"case {case_id} has no evidence_tests list")
        for version in versions:
            log_path = batch_logs.get(version)
            if log_path is None or not log_path.is_file() or not _evidence_passes(log_path, version):
                raise ValueError(f"full GUI evidence failed for Maya {version}: {log_path}")
            text = log_path.read_text(encoding="utf-8", errors="replace")
            missing = [
                test_id
                for test_id in test_ids
                if f"[GUI TEST] END {test_id} outcome=success" not in text
            ]
            if missing:
                raise ValueError(
                    f"case {case_id} missing successful tests for Maya {version}: {', '.join(missing)}"
                )
        cases.append({"case_id": case_id, "status": "pass", "maya_versions": versions})

    required_versions = sorted(
        {
            version
            for case_id in required_case_ids
            for version in _normalise_versions(cases_by_id.get(case_id, {}).get("required_maya_versions"))
        }
    )
    for version in required_versions:
        log_path = batch_logs.get(version)
        if log_path is None or not log_path.is_file() or not _evidence_passes(log_path, version):
            raise ValueError(f"full GUI evidence failed for Maya {version}: {log_path}")
        observed = _collect_surface_contracts(
            _parse_runtime_witness_markers(
                log_path.read_text(encoding="utf-8", errors="replace"),
                path=str(log_path),
            ),
            expected_surfaces=expected_surfaces,
            path=f"{log_path} runtime witness markers",
        )
        per_version_surfaces[version] = observed

    surfaces = _aggregate_version_surfaces(per_version_surfaces)
    return {
        "schema_version": SCHEMA_VERSION,
        "gate_id": GATE_ID,
        "cases": cases,
        "surfaces": surfaces,
    }


def _parse_batch_logs(values: Sequence[str]) -> Dict[str, Path]:
    result = {}
    for value in values:
        version, separator, raw_path = value.partition("=")
        if not separator or not version or not raw_path or version in result:
            raise ValueError(f"invalid or duplicate --batch-log value: {value}")
        result[version] = Path(raw_path).resolve()
    return result


def _build_cli_result(manifest_result: Dict[str, Any], report_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result = report_result if report_result is not None else manifest_result
    return {
        "gate_id": GATE_ID,
        "status": "pass" if result["valid"] else "fail",
        "structural_valid": bool(manifest_result["valid"]),
        "evidence_status": (
            "not_evaluated" if report_result is None else ("pass" if report_result["valid"] else "fail")
        ),
        "surface_count": manifest_result.get("surface_count", 0),
        "disposition_counts": manifest_result.get("disposition_counts", {}),
        "errors": result["errors"],
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="?",
        default=str(Path(__file__).with_name("ui_coverage_manifest.json")),
        help="checked-in UI coverage manifest",
    )
    parser.add_argument("--report", help="optional Qt evidence report JSON")
    parser.add_argument(
        "--from-evidence",
        action="store_true",
        help="generate the report from manifest-declared build artifacts",
    )
    parser.add_argument("--write-report", help="write the generated evidence report JSON")
    parser.add_argument(
        "--batch-log",
        action="append",
        default=[],
        metavar="VERSION=PATH",
        help="fresh full GUI log used to generate evidence; repeat per Maya version",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        manifest = load_json(Path(args.manifest))
        manifest_result = validate_manifest(manifest)
        report_result = None
        generation_modes = int(bool(args.report)) + int(args.from_evidence) + int(bool(args.batch_log))
        if generation_modes > 1:
            raise ValueError("--report, --from-evidence, and --batch-log are mutually exclusive")
        if args.write_report and not (args.from_evidence or args.batch_log):
            raise ValueError("--write-report requires --from-evidence or --batch-log")
        if args.batch_log:
            generated = build_report_from_batch_logs(manifest, _parse_batch_logs(args.batch_log))
            if args.write_report:
                report_path = Path(args.write_report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(generated, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            report_result = validate_report(manifest, generated)
        elif args.from_evidence:
            generated = build_report_from_evidence(manifest, Path(__file__).resolve().parents[1])
            if args.write_report:
                report_path = Path(args.write_report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(generated, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            report_result = validate_report(manifest, generated)
        elif args.report:
            report_result = validate_report(manifest, load_json(Path(args.report)))
        print(json.dumps(_build_cli_result(manifest_result, report_result), ensure_ascii=False, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"gate_id": GATE_ID, "status": "fail", "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    return 0 if (report_result or manifest_result)["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
