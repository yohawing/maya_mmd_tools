"""Run a bounded Maya roundtrip against the local representative manifest.

The host process owns case and phase timeouts.  Each case is executed in a
dedicated mayapy process, so a hung Maya operation cannot stall the remaining
cases or leave the caller waiting indefinitely.  Asset paths stay in a UTF-8
JSON worker configuration; the command line only carries ASCII build paths.

The worker uses the production Import Actions and ``ExportWorkflowService``
with release-probe scene oracles. It never writes below the manifest scan root.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Any, Callable, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
DEFAULT_MANIFEST = BUILD_ROOT / "reports" / "local_asset_roundtrip" / "representative.json"
DEFAULT_OUT_DIR = BUILD_ROOT / "reports" / "local_asset_roundtrip"
MANIFEST_SCHEMA_VERSION = 2
FLOAT_TOLERANCE = 1.0e-4
VMD_EXPORT_BAKE_TIMELINE_POSE_TOLERANCE = 1.0e-2
DEFAULT_EXPORT_WRITE_BUDGET_SEC = 60.0
FAILURE_CLASSIFICATIONS = (
    "import_failed",
    "edit_failed",
    "validation_blocked",
    "export_failed",
    "parse_failed",
    "structural_mismatch",
    "semantic_mismatch",
    "performance_timeout",
    "environment_blocked",
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class PhaseTimeoutError(RuntimeError):
    """Raised when a worker phase exceeds its configured wall timeout."""


def _require_build_path(value: str | Path, option_name: str) -> Path:
    """Resolve an artifact path and keep it under this repository's build tree."""

    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if resolved != BUILD_ROOT and BUILD_ROOT not in resolved.parents:
        raise ValueError(f"{option_name} must resolve under {BUILD_ROOT}: {resolved}")
    return resolved


def _safe_name(value: str) -> str:
    """Return a stable ASCII case directory name."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe[:80] or "case"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one UTF-8 JSON artifact, creating only build-owned parents."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish a live diagnostics snapshot without exposing partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read a possibly in-progress JSON checkpoint."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _resolve_asset_path(value: str, manifest_path: Path) -> Path:
    """Resolve a manifest asset path without rewriting or copying the asset."""

    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _sha256(path: Path) -> str:
    """Return the streaming SHA-256 digest required by a case manifest."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_asset(raw_case: Mapping[str, Any], suffix: str) -> tuple[str, str]:
    """Read one asset path/hash pair, accepting the legacy flat hash spelling."""

    value = raw_case.get(suffix)
    declared_hash = raw_case.get(f"{suffix}_sha256")
    if isinstance(value, Mapping):
        declared_hash = value.get("sha256", declared_hash)
        value = value.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"case has no {suffix.upper()} path")
    if not isinstance(declared_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", declared_hash):
        raise ValueError(f"case {suffix.upper()} asset requires a 64-character SHA-256")
    return value, declared_hash.lower()


def _verify_asset_hash(path: Path, declared_hash: str) -> str:
    """Verify a manifest source before starting any Maya process."""

    actual = _sha256(path)
    if actual != declared_hash.lower():
        raise ValueError(
            f"asset hash mismatch: {path} expected={declared_hash.lower()} actual={actual}"
        )
    return actual


def _adjustment_recipe(raw_case: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the required deterministic edit recipe from a case."""

    recipe = raw_case.get("adjustment_recipe", raw_case.get("adjustment"))
    if not isinstance(recipe, Mapping) or not recipe:
        raise ValueError(f"case {raw_case.get('name', '<unnamed>')!r} requires adjustment_recipe")
    return recipe


def _motion_evaluation_frames(oracle_frames: Iterable[int], edit_frame: int) -> list[int]:
    """Include oracle, adjacent, and deterministic interpolation frames."""

    oracle_set = {int(frame) for frame in oracle_frames}
    frames = set(oracle_set)
    frames.update({int(edit_frame) - 1, int(edit_frame), int(edit_frame) + 1})
    ordered_oracles = sorted(oracle_set)
    for left, right in zip(ordered_oracles, ordered_oracles[1:]):
        if right - left > 1:
            frames.add(left + (right - left) // 2)
    return sorted(frame for frame in frames if frame >= 0)


def _classify_failure(
    *,
    status: str | None = None,
    error: str | None = None,
    phase: str | None = None,
) -> str:
    """Map a worker/host failure to the fail-closed public classification."""

    error_text = str(error or "").casefold()
    status_text = str(status or "").casefold()
    phase_text = str(phase or "").casefold()
    if "timeout" in error_text or "timed_out" in error_text or "timeout" in status_text:
        return "performance_timeout"
    # The error is more authoritative than the phase name.  In particular,
    # a semantic/edit failure often happens after the ``fresh_import`` phase
    # has started; treating that phase name as an import failure hides the
    # actionable product defect.
    if any(
        token in error_text
        for token in (
            "semantic mismatch",
            "ik mismatch",
            "ik count mismatch",
            "ik state mismatch",
            "ik track mismatch",
            "ik track semantics differ",
            "pose mismatch",
            "oracle mismatch",
        )
    ):
        return "semantic_mismatch"
    if any(
        token in error_text
        for token in (
            "edited morph missing",
            "missing edited morph",
            "edited bone missing",
            "missing edited bone",
            "sentinel",
            "edit failed",
            "recipe",
        )
    ):
        return "edit_failed"
    if any(
        token in error_text
        for token in (
            "structural mismatch",
            "track names differ",
            "required tracks missing",
            "dropped channel",
            "dropped required track",
            "count mismatch",
        )
    ):
        return "structural_mismatch"
    if any(
        token in error_text
        for token in (
            "importmodelaction",
            "importvdmaction",
            "import failed",
            "import produced",
            "no root",
        )
    ):
        return "import_failed"
    if "edit" in error_text:
        return "edit_failed"
    if "validation" in error_text or "blocked" in error_text:
        return "validation_blocked"
    if "export" in error_text or "writer" in error_text:
        return "export_failed"
    if "parse" in error_text or "binary" in error_text:
        return "parse_failed"
    if "semantic" in error_text or "oracle" in error_text or "pose" in error_text:
        return "semantic_mismatch"
    if "structural" in error_text or "track" in error_text or "count" in error_text:
        return "structural_mismatch"
    # A phase name is a fallback only.  ``fresh_import`` by itself is not
    # enough to claim that the import action failed.
    if "source_import" in phase_text:
        return "import_failed"
    if status_text in {"crash", "environment_blocked"}:
        return "environment_blocked"
    return "environment_blocked"


def _worker_failure_classification(document: Mapping[str, Any]) -> str | None:
    """Return the most specific failure reported by a worker run.

    The worker stores the useful classification on an individual ``runs``
    entry.  The host must not replace that evidence with its own generic
    ``environment_blocked`` fallback when it flattens the child result into a
    case summary.
    """

    runs = document.get("runs")
    if isinstance(runs, list):
        for run in reversed(runs):
            if not isinstance(run, Mapping):
                continue
            value = run.get("failure_classification")
            if value in FAILURE_CLASSIFICATIONS:
                return str(value)
    value = document.get("failure_classification")
    return str(value) if value in FAILURE_CLASSIFICATIONS else None


def _allowed_warning_codes(validation: Any, export_format: str) -> tuple[list[str], list[str]]:
    """Return acknowledged and unexpected validation warning codes."""

    report = getattr(validation, "report", None)
    issues = list(getattr(report, "issues", ()) or ())
    warning_codes = [
        str(getattr(issue, "code", ""))
        for issue in issues
        if str(getattr(issue, "severity", "")).casefold() == "warning"
    ]
    del export_format
    allowed = None
    return (
        [code for code in warning_codes if allowed is not None and code == allowed],
        [code for code in warning_codes if code != allowed],
    )


def _assert_execute_warnings(result: Any, export_format: str) -> list[str]:
    """Re-check the final writer report; validation-time evidence is not enough."""

    acknowledged, unexpected = _allowed_warning_codes(result, export_format)
    if unexpected:
        raise RuntimeError(f"unexpected execute warnings: {unexpected}")
    return acknowledged


def _load_manifest(path_value: str | Path) -> tuple[Path, dict[str, Any]]:
    """Load and validate the selector manifest and all selected source paths."""

    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported manifest schema: {document.get('schema_version')!r}"
        )
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"manifest has no cases: {path}")
    normalized: list[dict[str, Any]] = []
    for index, raw_case in enumerate(cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"manifest case {index} is not an object")
        name = str(raw_case.get("name") or f"case_{index:03d}")
        case_kind = raw_case.get("case_kind", raw_case.get("kind"))
        if case_kind not in {"pmx", "pmx_vmd"}:
            raise ValueError(f"case {name!r} has unsupported case kind: {case_kind!r}")
        if not isinstance(raw_case.get("classification"), str) or not raw_case["classification"]:
            raise ValueError(f"case {name!r} requires classification")
        oracle_frames = raw_case.get("oracle_frames")
        if (
            not isinstance(oracle_frames, list)
            or not oracle_frames
            or any(isinstance(frame, bool) or not isinstance(frame, int) for frame in oracle_frames)
        ):
            raise ValueError(f"case {name!r} requires integer oracle_frames")
        recipe = _adjustment_recipe(raw_case)
        pmx_value, pmx_hash = _case_asset(raw_case, "pmx")
        pmx_path = _resolve_asset_path(pmx_value, path)
        if not pmx_path.is_file():
            raise FileNotFoundError(f"case {name!r} PMX not found: {pmx_path}")
        _verify_asset_hash(pmx_path, pmx_hash)
        case = dict(raw_case)
        case["name"] = name
        case["kind"] = case_kind
        case["pmx"] = str(pmx_path)
        case["pmx_sha256"] = pmx_hash
        case["oracle_frames"] = list(oracle_frames)
        case["adjustment_recipe"] = dict(recipe)
        if case_kind == "pmx_vmd":
            vmd_value, vmd_hash = _case_asset(raw_case, "vmd")
            vmd_path = _resolve_asset_path(vmd_value, path)
            if not vmd_path.is_file():
                raise FileNotFoundError(f"case {name!r} VMD not found: {vmd_path}")
            case["vmd"] = str(vmd_path)
            case["vmd_sha256"] = vmd_hash
            _verify_asset_hash(vmd_path, vmd_hash)
        elif raw_case.get("vmd") is not None:
            raise ValueError(f"case {name!r} PMX case cannot contain VMD")
        normalized.append(case)
    return path, {
        "manifest": document,
        "cases": normalized,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "hashes_verified": True,
    }


def _select_cases(
    cases: Iterable[Mapping[str, Any]],
    case_filter: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Select manifest cases without expanding PMX/VMD pairs cartesianly."""

    available = [dict(case) for case in cases]
    if case_filter:
        needle = case_filter.casefold()
        available = [case for case in available if needle in str(case["name"]).casefold()]
    if profile is None:
        return available
    if profile != "dense-hang-and-sparse-interpolation":
        raise ValueError(f"unsupported profile: {profile}")
    selected: list[dict[str, Any]] = []
    for classification in ("dense", "sparse"):
        selected_case = next(
            (
                case
                for case in available
                if str(case.get("classification")) == classification
            ),
            None,
        )
        if selected_case is not None:
            selected.append(selected_case)
    if len(selected) != 2:
        raise ValueError(
            "profile requires one dense and one sparse PMX/VMD case after filtering"
        )
    return selected


def _vmd_payload(data: Any) -> dict[str, Any]:
    """Normalize every VMD section for structural scene-motion checks."""

    def vector(value: Any) -> list[float]:
        return [round(float(item), 7) for item in (value or ())]

    bones = [
        {
            "name": str(frame.bone_name),
            "frame": int(frame.frame_number),
            "position": vector(frame.position),
            "rotation": vector(frame.rotation),
            "interpolation": list(bytes(frame.interpolation)),
        }
        for frame in data.bone_frames
    ]
    bones.sort(key=lambda item: (item["name"], item["frame"]))
    morphs = [
        {
            "name": str(frame.morph_name),
            "frame": int(frame.frame_number),
            "value": round(float(frame.value), 7),
        }
        for frame in data.morph_frames
    ]
    morphs.sort(key=lambda item: (item["name"], item["frame"]))
    cameras = [
        {
            "frame": int(frame.frame_number),
            "distance": round(float(frame.distance), 7),
            "position": vector(frame.position),
            "rotation": vector(frame.rotation),
            "interpolation": list(bytes(frame.interpolation)),
            "viewing_angle": int(frame.viewing_angle),
            "perspective": int(frame.perspective),
        }
        for frame in data.camera_frames
    ]
    cameras.sort(key=lambda item: item["frame"])
    lights = [
        {
            "frame": int(frame.frame_number),
            "color": vector(frame.color),
            "position": vector(frame.position),
        }
        for frame in data.light_frames
    ]
    lights.sort(key=lambda item: item["frame"])
    shadows = [
        {
            "frame": int(frame.frame_number),
            "mode": int(frame.mode),
            "distance": round(float(frame.distance), 7),
        }
        for frame in data.shadow_frames
    ]
    shadows.sort(key=lambda item: item["frame"])
    ik_frames = [
        {
            "frame": int(frame.frame_number),
            "visible": int(frame.visible),
            "states": [[str(name), int(state)] for name, state in frame.ik_states],
        }
        for frame in data.ik_show_hide_frames
    ]
    ik_frames.sort(key=lambda item: item["frame"])
    return {
        "model_name": str(getattr(data.header, "model_name", "") or ""),
        "bone": bones,
        "morph": morphs,
        "camera": cameras,
        "light": lights,
        "shadow": shadows,
        "ik": ik_frames,
    }


def _vmd_payload_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Compare VMD names, key times, values, interpolation and IK states."""

    failures: list[str] = []
    for section in ("bone", "morph", "camera", "light", "shadow", "ik"):
        expected_items = list(expected.get(section, ()))
        actual_items = list(actual.get(section, ()))
        if section == "ik":
            failures.extend(_vmd_ik_semantic_diff(expected_items, actual_items))
            continue
        if len(expected_items) != len(actual_items):
            failures.append(
                f"{section}.count expected={len(expected_items)} actual={len(actual_items)}"
            )
            continue
        if section in {"bone", "morph"}:
            expected_names = [item.get("name") for item in expected_items]
            actual_names = [item.get("name") for item in actual_items]
            if expected_names != actual_names:
                failures.append(f"{section}.track_names differ")
        for index, (source, result) in enumerate(zip(expected_items, actual_items)):
            if source.get("frame") != result.get("frame"):
                failures.append(f"{section}[{index}].frame differs")
            if section == "bone":
                if source.get("interpolation") != result.get("interpolation"):
                    failures.append(f"bone[{index}].interpolation differs")
                for field in ("position", "rotation"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"bone[{index}].{field} differs")
            elif section == "morph":
                if abs(float(source.get("value", 0.0)) - float(result.get("value", 0.0))) > FLOAT_TOLERANCE:
                    failures.append(f"morph[{index}].value differs")
            elif section == "camera":
                if source.get("interpolation") != result.get("interpolation"):
                    failures.append(f"camera[{index}].interpolation differs")
                for field in ("distance", "viewing_angle", "perspective"):
                    if source.get(field) != result.get(field):
                        failures.append(f"camera[{index}].{field} differs")
                for field in ("position", "rotation"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"camera[{index}].{field} differs")
            elif section == "light":
                for field in ("color", "position"):
                    if _max_float_difference(source.get(field, ()), result.get(field, ())) > FLOAT_TOLERANCE:
                        failures.append(f"light[{index}].{field} differs")
            elif section == "shadow":
                if source != result:
                    failures.append(f"shadow[{index}] differs")
    return failures


def _canonical_ik_states(states: Iterable[Any]) -> tuple[tuple[str, bool], ...]:
    """Canonicalize IK states by name while preserving strict bool semantics."""

    canonical: list[tuple[str, bool]] = []
    seen_names: set[str] = set()
    for state in states or ():
        if not isinstance(state, (list, tuple)) or len(state) != 2:
            raise ValueError(f"malformed IK state: {state!r}")
        name = str(state[0])
        if name in seen_names:
            raise ValueError(f"duplicate IK state name: {name!r}")
        seen_names.add(name)
        value = state[1]
        if isinstance(value, bool):
            enabled = value
        elif isinstance(value, int) and value in (0, 1):
            enabled = bool(value)
        else:
            raise ValueError(f"IK state is not boolean: {value!r}")
        canonical.append((name, enabled))
    return tuple(sorted(canonical, key=lambda item: item[0]))


def _vmd_ik_semantic_diff(
    expected: Iterable[Mapping[str, Any]],
    actual: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Compare IK frames strictly, ignoring only per-frame state order."""

    expected_items = list(expected)
    actual_items = list(actual)
    failures: list[str] = []
    if len(expected_items) != len(actual_items):
        return [f"ik.count expected={len(expected_items)} actual={len(actual_items)}"]
    for index, (source, result) in enumerate(zip(expected_items, actual_items)):
        if source.get("frame") != result.get("frame"):
            failures.append(f"ik[{index}].frame differs")
        if source.get("visible") != result.get("visible"):
            failures.append(f"ik[{index}].visible differs")
        try:
            source_states = _canonical_ik_states(source.get("states", ()))
        except ValueError as exc:
            failures.append(f"ik[{index}].expected states invalid: {exc}")
            source_states = None
        try:
            result_states = _canonical_ik_states(result.get("states", ()))
        except ValueError as exc:
            failures.append(f"ik[{index}].actual states invalid: {exc}")
            result_states = None
        if source_states is not None and result_states is not None and source_states != result_states:
            if [name for name, _ in source_states] != [name for name, _ in result_states]:
                failures.append(f"ik[{index}].state_names differ")
            else:
                failures.append(f"ik[{index}].state_values differs")
    return failures


def _vmd_bake_timeline_semantic_diff(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    """Compare Bake Timeline track semantics without requiring sparse/raw key parity."""

    failures: list[str] = []
    for section in ("bone", "morph"):
        expected_names = {str(item.get("name")) for item in expected.get(section, ())}
        actual_names = {str(item.get("name")) for item in actual.get(section, ())}
        missing = sorted(expected_names - actual_names)
        if missing:
            failures.append(f"{section} required tracks missing: {missing[:20]}")
    for section in ("camera", "light", "shadow", "ik"):
        expected_items = list(expected.get(section, ()))
        actual_items = list(actual.get(section, ()))
        if section == "ik":
            failures.extend(_vmd_ik_semantic_diff(expected_items, actual_items))
        elif expected_items and not actual_items:
            failures.append(f"{section} tracks were dropped")
    return failures


def _required_source_vmd_payload(
    source_payload: Mapping[str, Any],
    required_track_names: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    """Filter raw bone/morph tracks to names resolved against the target model."""

    required = {
        section: {_normalized_name(name) for name in required_track_names.get(section, ())}
        for section in ("bone", "morph")
    }
    payload = {section: list(source_payload.get(section, ())) for section in source_payload}
    for section in ("bone", "morph"):
        payload[section] = [
            item
            for item in source_payload.get(section, ())
            if _normalized_name(item.get("name")) in required[section]
        ]
    return payload


def _bake_timeline_identity(section: Any, name: Any) -> tuple[str, str]:
    """Return the canonical identity used by collector omission diagnostics."""

    return (
        str(section or "").strip().lower(),
        " ".join(str(name or "").strip().casefold().split()),
    )


def _bake_timeline_payload_identities(
    payload: Mapping[str, Any],
) -> set[tuple[str, str]]:
    return {
        _bake_timeline_identity(section, item.get("name"))
        for section in ("bone", "morph")
        for item in payload.get(section, ())
        if isinstance(item, Mapping)
    }


def _committed_source_omissions(
    commitment: Mapping[str, Any],
    missing: set[tuple[str, str]],
) -> tuple[set[tuple[str, str]], Optional[str]]:
    """Accept omissions only when their exact count and fingerprint match."""

    count = commitment.get("count")
    fingerprint = commitment.get("fingerprint")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return set(), "source omission commitment count is invalid"
    if not isinstance(fingerprint, str):
        return set(), "source omission commitment fingerprint is invalid"
    identities = [list(identity) for identity in sorted(missing)]
    from mmd_tools.validation.snapshot import fingerprint_payload

    expected_fingerprint = fingerprint_payload(identities)
    if count != len(identities) or fingerprint != expected_fingerprint:
        return set(), (
            "source omission commitment does not exactly match missing identities: "
            f"expected_count={len(identities)} expected_fingerprint={expected_fingerprint}"
        )
    return set(missing), None


def _bake_timeline_track_boundary_diff(
    source_payload: Mapping[str, Any],
    collected_payload: Mapping[str, Any],
    exported_payload: Mapping[str, Any],
    required_track_names: Mapping[str, Iterable[str]],
    source_omission_commitment: Optional[Mapping[str, Any]] = None,
) -> dict[str, list[str]]:
    """Classify collection and writer boundaries in one export operation."""

    required_source = _required_source_vmd_payload(source_payload, required_track_names)
    source_to_collected_payload = required_source
    commitment_error = None
    if source_omission_commitment is not None:
        missing = (
            _bake_timeline_payload_identities(required_source)
            - _bake_timeline_payload_identities(collected_payload)
        )
        allowed, commitment_error = _committed_source_omissions(
            source_omission_commitment,
            missing,
        )
        source_to_collected_payload = dict(required_source)
        for section in ("bone", "morph"):
            source_to_collected_payload[section] = [
                item
                for item in required_source.get(section, ())
                if _bake_timeline_identity(section, item.get("name")) not in allowed
            ]
    source_to_collected = _vmd_bake_timeline_semantic_diff(
        source_to_collected_payload,
        collected_payload,
    )
    if commitment_error is not None:
        source_to_collected.insert(0, commitment_error)
    return {
        "source_to_collected": source_to_collected,
        "collected_to_export": _vmd_payload_diff(
            collected_payload,
            exported_payload,
        ),
    }


def _source_omission_commitment(
    export_evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Read the exact collector omission commitment from export diagnostics."""

    diagnostics = export_evidence.get("diagnostics")
    backend = diagnostics.get("backend") if isinstance(diagnostics, Mapping) else None
    collector = backend.get("collector") if isinstance(backend, Mapping) else None
    selection = collector.get("track_selection") if isinstance(collector, Mapping) else None
    commitment = (
        selection.get("source_omission_identity")
        if isinstance(selection, Mapping)
        else None
    )
    if not isinstance(commitment, Mapping):
        raise RuntimeError("VMD source omission commitment missing from export diagnostics")
    return dict(commitment)


def _vmd_edit_track_witness(
    payload: Mapping[str, Any], adjustment: Mapping[str, Any]
) -> dict[str, Any]:
    """Require the edited frame/value to survive into the exported VMD."""

    frame = int(adjustment["frame"])

    def find_track(section: str, names: Iterable[str]) -> Mapping[str, Any] | None:
        normalized = {_normalized_name(name) for name in names if name}
        return next(
            (
                item for item in payload.get(section, ())
                if int(item.get("frame", -1)) == frame
                and _normalized_name(item.get("name")) in normalized
            ),
            None,
        )

    bone = adjustment.get("bone", {})
    bone_names = bone.get("track_names", (bone.get("track_name", ""),))
    bone_track = find_track("bone", bone_names)
    if bone_track is None:
        raise AssertionError(
            f"exported VMD missing edited bone track at frame {frame}: {list(bone_names)!r}"
        )
    witness: dict[str, Any] = {
        "frame": frame,
        "bone": {
            "track_name": bone_track.get("name"),
            "rotation": bone_track.get("rotation"),
        },
    }
    morph = adjustment.get("morph")
    if isinstance(morph, Mapping):
        morph_names = morph.get("track_names", (morph.get("track_name", ""),))
        morph_track = find_track("morph", morph_names)
        if morph_track is None:
            raise AssertionError(
                f"exported VMD missing edited morph track at frame {frame}: {list(morph_names)!r}"
            )
        expected = float(morph["after"])
        actual = float(morph_track.get("value", 0.0))
        if abs(actual - expected) > FLOAT_TOLERANCE:
            raise AssertionError(
                f"exported VMD morph edit differs at frame {frame}: expected={expected:g} actual={actual:g}"
            )
        witness["morph"] = {
            "track_name": morph_track.get("name"),
            "value": actual,
        }
    return witness


def _max_float_difference(expected: Iterable[Any], actual: Iterable[Any]) -> float:
    """Return the largest absolute difference between two numeric vectors."""

    expected_values = list(expected)
    actual_values = list(actual)
    if len(expected_values) != len(actual_values):
        return float("inf")
    return max(
        (abs(float(left) - float(right)) for left, right in zip(expected_values, actual_values)),
        default=0.0,
    )


def _metric_snapshot() -> dict[str, int | None]:
    """Return current process RSS in bytes where the host exposes it."""

    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_current_process = kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_process_memory_info = psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = (
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            )
            get_process_memory_info.restype = wintypes.BOOL
            result = get_process_memory_info(
                get_current_process(),
                ctypes.byref(counters),
                counters.cb,
            )
        except (AttributeError, OSError, TypeError):
            result = 0
        if result:
            return {
                "rss_bytes": int(counters.WorkingSetSize),
                "peak_rss_bytes": int(counters.PeakWorkingSetSize),
            }
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
    except (AttributeError, FileNotFoundError, IndexError, OSError, ValueError):
        return {"rss_bytes": None, "peak_rss_bytes": None}
    return {"rss_bytes": pages * page_size, "peak_rss_bytes": None}


def _export_write_budget_evidence(
    phases: Iterable[Mapping[str, Any]],
    budget_sec: float,
) -> dict[str, Any] | None:
    """Return fail-closed evidence when completed export writing exceeds budget."""

    if budget_sec <= 0:
        raise ValueError("export write budget must be positive")
    phase = next(
        (item for item in phases if str(item.get("name")) == "export_write"),
        None,
    )
    if phase is None:
        return None
    try:
        actual_sec = float(phase["wall_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if actual_sec <= budget_sec:
        return None
    return {
        "phase": "export_write",
        "classification": "performance_timeout",
        "expected_sec": float(budget_sec),
        "actual_sec": actual_sec,
    }


class _PhaseRecorder:
    """Record one worker phase and asynchronously emit timeout stack samples."""

    def __init__(
        self,
        context: "_WorkerContext",
        name: str,
    ) -> None:
        self.context = context
        self.name = name
        self.started = time.perf_counter()
        self.cpu_started = time.process_time()
        self.rss_started = _metric_snapshot()
        self.rss_peak = self.rss_started.get("peak_rss_bytes") or self.rss_started.get("rss_bytes")
        self.stop = threading.Event()
        self.timed_out = threading.Event()
        self.thread: threading.Thread | None = None
        self.stack_samples: list[str] = []

    def __enter__(self) -> "_PhaseRecorder":
        self.context.write_checkpoint(self.name, "running")
        self.thread = threading.Thread(target=self._watchdog, name=f"phase-timeout-{self.name}", daemon=True)
        self.thread.start()
        return self

    def _watchdog(self) -> None:
        timeout = self.context.phase_timeout_sec
        if timeout <= 0:
            return
        if self.stop.wait(timeout):
            return
        self.timed_out.set()
        for sample_index in range(1, 4):
            stack_path = self.context.stack_dir / f"{_safe_name(self.name)}-{sample_index}.log"
            try:
                stack_path.parent.mkdir(parents=True, exist_ok=True)
                with stack_path.open("w", encoding="utf-8") as handle:
                    faulthandler.dump_traceback(file=handle, all_threads=True)
                self.stack_samples.append(str(stack_path))
            except (OSError, RuntimeError):
                pass
            self.context.write_checkpoint(
                self.name,
                "timed_out",
                timed_out=True,
                stack_samples=self.stack_samples,
            )
            if sample_index < 3:
                time.sleep(0.5)

    def __exit__(self, exc_type: Any, exc: Any, _tb: Any) -> bool:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=0.2)
        ended = time.perf_counter()
        wall_elapsed = ended - self.started
        cpu_ended = time.process_time()
        rss_ended = _metric_snapshot()
        self.rss_peak = max(
            value
            for value in (
                self.rss_peak,
                rss_ended.get("rss_bytes"),
                rss_ended.get("peak_rss_bytes"),
            )
            if value is not None
        ) if any(
            value is not None
            for value in (
                self.rss_peak,
                rss_ended.get("rss_bytes"),
                rss_ended.get("peak_rss_bytes"),
            )
        ) else None
        entry = {
            "name": self.name,
            "wall_sec": round(wall_elapsed, 6),
            "cpu_sec": round(cpu_ended - self.cpu_started, 6),
            "rss_start_bytes": self.rss_started.get("rss_bytes"),
            "rss_end_bytes": rss_ended.get("rss_bytes"),
            "rss_peak_bytes": self.rss_peak,
            "timeout_sec": self.context.phase_timeout_sec,
            "status": "timed_out" if self.timed_out.is_set() else ("failed" if exc else "passed"),
            "stack_samples": list(self.stack_samples),
        }
        self.context.phases.append(entry)
        if self.name == "export_write" and wall_elapsed > self.context.export_write_budget_sec:
            self.context.export_write_budget_violations.append(
                {
                    "phase": "export_write",
                    "classification": "performance_timeout",
                    "expected_sec": float(self.context.export_write_budget_sec),
                    "actual_sec": round(wall_elapsed, 6),
                }
            )
        self.context.write_checkpoint(
            self.name,
            entry["status"],
            timed_out=self.timed_out.is_set(),
            stack_samples=self.stack_samples,
        )
        if self.timed_out.is_set() and exc is None:
            raise PhaseTimeoutError(
                f"phase {self.name!r} exceeded {self.context.phase_timeout_sec:g}s"
            )
        return False


class _WorkerContext:
    """Mutable per-worker evidence state."""

    def __init__(
        self,
        checkpoint: Path,
        stack_dir: Path,
        phase_timeout_sec: float,
        export_write_budget_sec: float = DEFAULT_EXPORT_WRITE_BUDGET_SEC,
    ) -> None:
        if export_write_budget_sec <= 0:
            raise ValueError("export write budget must be positive")
        self.checkpoint = checkpoint
        self.stack_dir = stack_dir
        self.phase_timeout_sec = phase_timeout_sec
        self.export_write_budget_sec = float(export_write_budget_sec)
        self.phases: list[dict[str, Any]] = []
        self.export_write_budget_violations: list[dict[str, Any]] = []

    def write_checkpoint(
        self,
        phase: str,
        status: str,
        *,
        timed_out: bool = False,
        stack_samples: Iterable[str] = (),
    ) -> None:
        """Publish the last known phase for the host watchdog."""

        _write_json(
            self.checkpoint,
            {
                "phase": phase,
                "status": status,
                "timed_out": bool(timed_out),
                "stack_samples": list(stack_samples),
                "updated_at": time.time(),
            },
        )


def _phase(context: _WorkerContext, name: str, function: Callable[[], Any]) -> Any:
    """Run a callable under the worker phase recorder."""

    with _PhaseRecorder(context, name):
        return function()


def _report_summary(validation: Any) -> dict[str, Any]:
    """Normalize ExportWorkflow validation evidence for JSON."""

    report = getattr(validation, "report", None)
    issues = list(getattr(report, "issues", ()) or ())
    issue_details = [
        {
            "code": str(getattr(issue, "code", "")),
            "severity": str(getattr(issue, "severity", "")),
            "blocking": bool(getattr(issue, "blocking", False)),
            "category": str(getattr(issue, "category", "")),
            "message": str(getattr(issue, "message", "")),
            "remediation": str(getattr(issue, "remediation", "")),
        }
        for issue in issues
    ]
    return {
        "state": getattr(validation, "state", None),
        "blocking": bool(getattr(report, "is_blocking", False)),
        "issue_count": len(issues),
        "issue_codes": [str(getattr(issue, "code", "")) for issue in issues],
        "issues": issue_details,
        "severity_counts": {
            severity: sum(1 for issue in issues if getattr(issue, "severity", None) == severity)
            for severity in ("fatal", "error", "warning", "info")
        },
    }


def _compare_morph_structure(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[str]:
    """Compare general-model morph identity without fixture-only runtime probes."""

    failures: list[str] = []
    expected_morphs = list(expected.get("morphs", ()) or ())
    actual_morphs = list(actual.get("morphs", ()) or ())
    if len(expected_morphs) != len(actual_morphs):
        failures.append(
            f"morph count expected={len(expected_morphs)} actual={len(actual_morphs)}"
        )
    for index, (source, result) in enumerate(zip(expected_morphs, actual_morphs)):
        for field in ("index", "name", "name_en", "type", "panel", "offsets"):
            if field not in source and field not in result:
                continue
            source_value = json.dumps(
                source.get(field), ensure_ascii=False, sort_keys=True, default=str
            )
            result_value = json.dumps(
                result.get(field), ensure_ascii=False, sort_keys=True, default=str
            )
            if source_value != result_value:
                failures.append(f"morphs[{index}].{field} differs")
    if expected.get("unsupported_types") != actual.get("unsupported_types"):
        failures.append("morph unsupported type set differs")
    return failures


def _import_options() -> dict[str, Any]:
    """Return the same deterministic import options used by the UI presenter."""

    return {
        "scale": 1.0,
        "import_physics": True,
        "setup_rig": True,
        "setup_bone_orientation": True,
        "create_mmd_control_rig": False,
        "create_mmd_shaders": False,
        "use_cpp_fast_load": False,
        "use_native_pmx_parse": False,
        "require_native_pmx_parse": False,
    }


def _require_import_success(result: Any, action_name: str, *, require_root: bool) -> str | None:
    """Reject partial/warning imports before an oracle can make them look valid."""

    outcome = str(getattr(result, "outcome", "") or "").casefold()
    warnings = list(getattr(result, "warnings", ()) or ())
    if outcome != "success" or warnings:
        raise RuntimeError(
            f"{action_name} did not complete cleanly: outcome={outcome!r} warnings={warnings!r}"
        )
    root = getattr(result, "root_node", None)
    if require_root and not root:
        raise RuntimeError(f"{action_name} returned no model root")
    return str(root) if root else None


def _canonical_imported_model_root(root: str) -> str:
    """Resolve an imported root to one existing, long Maya DAG path.

    Import actions historically returned the short name in some Maya scenes.
    The public export/prepare contracts require the canonical identity, so do
    the same unique ``cmds.ls(..., long=True)`` resolution that the Maya
    adapters use immediately after import.  An unresolved or ambiguous alias
    must stop the user-path smoke rather than allowing a later phase to report
    a misleading target error.
    """

    if not isinstance(root, str) or not root.strip():
        raise RuntimeError(f"ImportModelAction returned an invalid model root: {root!r}")
    try:
        from maya import cmds
    except Exception as exc:
        raise RuntimeError("Maya cmds is unavailable while resolving the imported model root") from exc
    try:
        matches = cmds.ls(root, long=True) or []
    except Exception as exc:
        raise RuntimeError(f"could not resolve imported model root {root!r}") from exc
    if isinstance(matches, (str, bytes, bytearray)) or len(matches) != 1:
        raise RuntimeError(
            f"imported model root is not a unique Maya DAG path: {root!r} -> {matches!r}"
        )
    canonical = matches[0]
    if not isinstance(canonical, str) or not canonical.startswith("|"):
        raise RuntimeError(
            f"imported model root did not resolve to a canonical long DAG path: {canonical!r}"
        )
    return canonical


def _import_model_action(source: Path) -> str:
    """Import a model through the production ImportModelAction boundary."""

    from mmd_tools.actions.import_model_action import ImportModelAction, ImportModelRequest

    result = ImportModelAction().execute(
        ImportModelRequest(
            file_path=str(source),
            options={**_import_options(), "profile": {}},
            create_new_scene=True,
        )
    )
    if not result.succeeded:
        raise RuntimeError(f"ImportModelAction failed: {result.error or result.warnings}")
    root = _require_import_success(result, "ImportModelAction", require_root=True)
    assert root is not None
    return _canonical_imported_model_root(root)


def _import_vmd_action(root: str, model: Path, source: Path) -> str:
    """Apply a VMD through the production ImportVmdAction boundary."""

    from mmd_tools.actions.import_vmd_action import ImportVmdAction, ImportVmdRequest

    options = {
        **_import_options(),
        "target_model": root,
        "pmx_path": str(model),
        "bake_mode": False,
    }
    result = ImportVmdAction().execute(
        ImportVmdRequest(file_path=str(source), options=options, create_new_scene=False)
    )
    if not result.succeeded:
        raise RuntimeError(f"ImportVmdAction failed: {result.error or result.warnings}")
    _require_import_success(result, "ImportVmdAction", require_root=False)
    return root


def _model_recipe_value(recipe: Mapping[str, Any], key: str, default: Any) -> Any:
    """Read model recipe keys from flat or nested manifest layouts."""

    model_recipe = recipe.get("model")
    if isinstance(model_recipe, Mapping) and key in model_recipe:
        return model_recipe[key]
    return recipe.get(key, default)


def _apply_model_adjustment(root: str, case: Mapping[str, Any]) -> dict[str, Any]:
    """Apply deterministic Info/Bone/Material edits through the authoring coordinator."""

    from dataclasses import replace

    from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition
    from mmd_tools.core.constants import ATTR_MMD_COMMENT

    recipe = _adjustment_recipe(case)
    composition = build_maya_authoring_composition()
    coordinator = composition.coordinator
    before = coordinator.read_spec(root)
    comment_suffix = str(_model_recipe_value(recipe, "comment_suffix", " [roundtrip-smoke]"))
    bone_suffix = str(
        _model_recipe_value(recipe, "bone_name_english_suffix", " [roundtrip-smoke]")
    )
    requested_delta = float(_model_recipe_value(recipe, "material_shininess_delta", 0.01))
    if not comment_suffix or not bone_suffix or not math.isfinite(requested_delta) or requested_delta == 0.0:
        raise ValueError("model adjustment recipe contains an invalid sentinel")

    session = coordinator.begin_info_metadata_edit(root, ATTR_MMD_COMMENT)
    coordinator.update_info_metadata_edit(session, before.model.comment + comment_suffix)
    coordinator.commit_info_metadata_edit(session)

    editable_bones = [
        bone for bone in before.bones
        if isinstance(getattr(bone, "binding_identity", None), str)
        and bone.binding_identity
    ]
    if not editable_bones:
        raise ValueError("model adjustment has no editable bone")
    bone = sorted(editable_bones, key=lambda item: item.index)[0]
    bone_target = replace(bone, name_english=bone.name_english + bone_suffix)
    coordinator.replace_bone_semantic(root, bone_target)

    materials = [
        material for material in before.materials
        if isinstance(getattr(material, "binding_identity", None), str)
        and material.binding_identity
    ]
    if not materials:
        raise ValueError("model adjustment has no editable material")
    material = sorted(materials, key=lambda item: item.index)[0]
    delta = requested_delta if material.specular_coefficient + requested_delta >= 0.0 else -abs(requested_delta)
    target_shininess = max(0.0, float(material.specular_coefficient) + delta)
    if abs(target_shininess - float(material.specular_coefficient)) <= FLOAT_TOLERANCE:
        raise ValueError("material shininess edit was clamped and did not change")
    material_target = replace(material, specular_coefficient=target_shininess)
    coordinator.apply_material_value_patch(root, material_target)

    after = coordinator.read_spec(root)
    changed = {
        "comment": after.model.comment,
        "bone": next(item for item in after.bones if item.index == bone.index).name_english,
        "material_shininess": next(
            item for item in after.materials if item.index == material.index
        ).specular_coefficient,
    }
    if changed["comment"] != before.model.comment + comment_suffix:
        raise ValueError("model comment sentinel did not persist")
    if changed["bone"] != bone_target.name_english:
        raise ValueError("bone English-name sentinel did not persist")
    if abs(float(changed["material_shininess"]) - target_shininess) > FLOAT_TOLERANCE:
        raise ValueError("material shininess sentinel did not persist")
    return {
        "before": {
            "comment": before.model.comment,
            "bone": {"index": bone.index, "binding_identity": bone.binding_identity, "name_english": bone.name_english},
            "material": {"index": material.index, "binding_identity": material.binding_identity, "shininess": material.specular_coefficient},
        },
        "after": {
            "comment": changed["comment"],
            "bone": {"index": bone.index, "binding_identity": bone.binding_identity, "name_english": changed["bone"]},
            "material": {"index": material.index, "binding_identity": material.binding_identity, "shininess": changed["material_shininess"]},
        },
    }


def _motion_recipe_value(recipe: Mapping[str, Any], key: str, default: Any) -> Any:
    """Read motion recipe keys from flat or nested manifest layouts."""

    motion_recipe = recipe.get("motion")
    if isinstance(motion_recipe, Mapping) and key in motion_recipe:
        return motion_recipe[key]
    return recipe.get(key, default)


def _normalized_name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _model_resolved_motion_track_names(root: str) -> dict[str, set[str]]:
    """Return bone/morph aliases that uniquely resolve on the imported model."""

    from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition

    spec = build_maya_authoring_composition().coordinator.read_spec(root)

    def unique_aliases(items: Iterable[Any]) -> set[str]:
        aliases: dict[str, set[int]] = {}
        for item in items:
            index = int(item.index)
            for value in (item.name, item.name_english):
                name = _normalized_name(value)
                if name:
                    aliases.setdefault(name, set()).add(index)
        return {name for name, indices in aliases.items() if len(indices) == 1}

    return {
        "bone": unique_aliases(spec.bones),
        "morph": unique_aliases(spec.morphs),
    }


def _required_ik_track_names(ik_frames: Iterable[Any]) -> set[str]:
    """Return the IK bone names required by a VMD IK section."""

    names: set[str] = set()
    for frame in ik_frames or ():
        for state in getattr(frame, "ik_states", ()) or ():
            if isinstance(state, (list, tuple)) and state and str(state[0]).strip():
                names.add(str(state[0]))
    return names


def _capture_ik_import_witness(
    root: str,
    ik_frames: Iterable[Any],
    cmds_module: Any | None = None,
) -> dict[str, Any]:
    """Capture root-owned native IK nodes immediately after VMD import."""

    if cmds_module is None:
        from maya import cmds as cmds_module

    if not root or not cmds_module.objExists(root):
        raise ValueError(f"IK import witness root is invalid: {root!r}")
    root_joints = set(
        str(joint)
        for joint in (
            cmds_module.listRelatives(
                root,
                allDescendents=True,
                type="joint",
                fullPath=True,
            )
            or ()
        )
    )
    if cmds_module.nodeType(root) == "joint":
        root_joints.add(str(root))

    def long_names(nodes: Iterable[Any]) -> set[str]:
        result: set[str] = set()
        for node in nodes:
            resolved = cmds_module.ls(str(node), long=True) or ()
            result.update(str(item) for item in resolved)
        return result

    root_joints = long_names(root_joints) or root_joints
    by_name: dict[str, dict[str, Any]] = {}
    for node_value in cmds_module.ls(type="mmdCcdIk", long=True) or ():
        node = str(node_value)
        connected_joints = long_names(
            cmds_module.listConnections(
                node,
                source=True,
                destination=True,
                type="joint",
            )
            or ()
        )
        if not connected_joints or not connected_joints.issubset(root_joints):
            continue
        if not cmds_module.attributeQuery("mmd_ik_bone_name", node=node, exists=True):
            continue
        name = str(cmds_module.getAttr(f"{node}.mmd_ik_bone_name") or "")
        if not name:
            continue
        name_key = _normalized_name(name)
        if name_key in by_name:
            raise ValueError(f"duplicate root-owned mmdCcdIk track: {name!r}")
        if not cmds_module.attributeQuery("enabled", node=node, exists=True):
            raise ValueError(f"root-owned mmdCcdIk node has no enabled attribute: {node!r}")
        key_times = cmds_module.keyframe(
            f"{node}.enabled",
            query=True,
            timeChange=True,
        ) or ()
        by_name[name_key] = {
            "node": node,
            "name": name,
            "enabled": bool(cmds_module.getAttr(f"{node}.enabled")),
            "enabled_key_times": sorted({float(time) for time in key_times}),
        }

    required_names = _required_ik_track_names(ik_frames)
    if not by_name:
        raise ValueError("VMD import produced no root-owned mmdCcdIk nodes")
    unresolved = sorted(name for name in required_names if _normalized_name(name) not in by_name)
    if unresolved:
        raise ValueError(f"VMD import IK required tracks unresolved: {unresolved[:20]}")
    return {
        "nodes": [by_name[key] for key in sorted(by_name)],
        "names": sorted(item["name"] for item in by_name.values()),
        "required_names": sorted(required_names),
        "unresolved_names": unresolved,
    }


def _select_unique_motion_bone(spec: Any, animated_names: Iterable[str]) -> Any:
    """Select the first uniquely matched model bone in deterministic index order."""

    candidates = []
    names = sorted({_normalized_name(name) for name in animated_names if _normalized_name(name)})
    for name in names:
        matches = [
            bone for bone in spec.bones
            if _normalized_name(bone.name) == name or _normalized_name(bone.name_english) == name
        ]
        if len(matches) == 1:
            candidates.append(matches[0])
    if not candidates:
        raise ValueError("motion adjustment has no uniquely matched animated bone")
    return sorted(candidates, key=lambda item: item.index)[0]


def _select_unique_motion_morph(spec: Any, animated_names: Iterable[str]) -> Any | None:
    """Select the first uniquely matched animated morph, if one exists."""

    names = {_normalized_name(name) for name in animated_names if _normalized_name(name)}
    candidates = [
        morph for morph in sorted(spec.morphs, key=lambda item: item.index)
        if _normalized_name(morph.name) in names or _normalized_name(morph.name_english) in names
    ]
    if len(candidates) > 1 and _normalized_name(candidates[0].name) == _normalized_name(candidates[1].name):
        raise ValueError("motion adjustment morph match is not unique")
    return candidates[0] if candidates else None


def _resolve_morph_controller_input_plug(
    root: str,
    morph_index: int,
    cmds_module: Any | None = None,
) -> str:
    """Resolve one model-owned morph controller input by PMX morph index.

    ``binding_identity`` describes the semantic morph network, but it is not
    the authoring/export authority.  The production collector keys morph
    tracks on the model-owned ``mmdMorphController.inputWeight[index]`` plug;
    use that same ownership boundary for both source and fresh witnesses.
    """

    if cmds_module is None:
        from maya import cmds as cmds_module

    if isinstance(morph_index, bool) or not isinstance(morph_index, int) or morph_index < 0:
        raise ValueError(f"morph index is invalid: {morph_index!r}")
    if not root or not cmds_module.objExists(root):
        raise ValueError(f"morph controller root is invalid: {root!r}")
    if not cmds_module.attributeQuery("mmd_morph_controller", node=root, exists=True):
        raise ValueError(f"morph controller ownership attribute is missing on root: {root!r}")
    controllers = cmds_module.listConnections(
        f"{root}.mmd_morph_controller",
        source=True,
        destination=False,
    ) or []
    if len(controllers) != 1:
        raise ValueError(
            "morph controller ownership must have exactly one connection, "
            f"got {len(controllers)}"
        )
    controller = str(controllers[0])
    if cmds_module.nodeType(controller) != "mmdMorphController":
        raise ValueError(f"morph controller has unexpected node type: {controller!r}")
    if not cmds_module.attributeQuery("inputWeight", node=controller, exists=True):
        raise ValueError(f"morph controller inputWeight attribute is missing: {controller!r}")
    try:
        input_indices = {
            int(index)
            for index in (cmds_module.getAttr(f"{controller}.inputWeight", multiIndices=True) or [])
        }
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"morph controller inputWeight indices are unreadable: {controller!r}") from exc
    if morph_index not in input_indices:
        raise ValueError(
            f"morph controller inputWeight index is missing: controller={controller!r} "
            f"index={morph_index}"
        )
    return f"{controller}.inputWeight[{morph_index}]"


def _morph_weight_limits(plug: str) -> tuple[float, float]:
    """Read the actual Maya attribute limits for one morph weight plug."""

    from maya import cmds

    node, attribute = plug.rsplit(".", 1)
    attribute = attribute.split("[", 1)[0]
    minimum = float("-inf")
    maximum = float("inf")
    if cmds.attributeQuery(attribute, node=node, minExists=True):
        values = cmds.attributeQuery(attribute, node=node, minimum=True) or ()
        if values:
            minimum = float(values[0])
    if cmds.attributeQuery(attribute, node=node, maxExists=True):
        values = cmds.attributeQuery(attribute, node=node, maximum=True) or ()
        if values:
            maximum = float(values[0])
    if not math.isfinite(minimum) and not math.isfinite(maximum):
        return minimum, maximum
    if minimum > maximum:
        raise ValueError(f"morph weight limits are invalid for {plug!r}: {minimum} > {maximum}")
    return minimum, maximum


def _apply_motion_adjustment(
    root: str,
    source_data: Any,
    case: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply a deterministic Timeline/Channel-Box-style edit using Maya keys."""

    from maya import cmds
    from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition

    recipe = _adjustment_recipe(case)
    frame = int(_motion_recipe_value(recipe, "edit_frame", _motion_recipe_value(recipe, "frame", 1)))
    bone_delta = float(_motion_recipe_value(recipe, "bone_rotate_z_degrees", 1.0))
    morph_delta = float(_motion_recipe_value(recipe, "morph_delta", 0.05))
    if frame < 0 or not math.isfinite(bone_delta) or bone_delta == 0.0 or not math.isfinite(morph_delta):
        raise ValueError("motion adjustment recipe contains an invalid sentinel")
    composition = build_maya_authoring_composition()
    spec = composition.coordinator.read_spec(root)
    animated_bone_names = [frame_value.bone_name for frame_value in source_data.bone_frames]
    bone = _select_unique_motion_bone(spec, animated_bone_names)
    binding = str(bone.binding_identity)
    cmds.currentTime(frame, edit=True)
    before_bone = float(cmds.getAttr(f"{binding}.rotateZ"))
    after_bone = before_bone + bone_delta
    cmds.setAttr(f"{binding}.rotateZ", after_bone)
    cmds.setKeyframe(binding, attribute="rotateZ", time=frame)

    morph_names = [frame_value.morph_name for frame_value in source_data.morph_frames]
    morph = _select_unique_motion_morph(spec, morph_names)
    morph_witness = None
    if morph is not None:
        if not isinstance(morph.binding_identity, str) or not morph.binding_identity:
            raise ValueError(f"motion adjustment morph {morph.index} has no binding identity")
        plug = _resolve_morph_controller_input_plug(root, int(morph.index))
        before_morph = float(cmds.getAttr(plug))
        minimum, maximum = _morph_weight_limits(plug)
        after_morph = max(minimum, min(maximum, before_morph + morph_delta))
        if abs(after_morph - before_morph) <= FLOAT_TOLERANCE:
            raise ValueError("motion morph edit was clamped and did not change")
        cmds.setAttr(plug, after_morph)
        cmds.setKeyframe(plug, time=frame)
        morph_witness = {
            "index": morph.index,
            "binding_identity": morph.binding_identity,
            "controller_input_plug": plug,
            "track_name": morph.name,
            "track_names": [morph.name, morph.name_english],
            "before": before_morph,
            "after": after_morph,
            "minimum": minimum,
            "maximum": maximum,
        }
    if abs(after_bone - before_bone) <= FLOAT_TOLERANCE:
        raise ValueError("motion bone edit did not change rotateZ")
    return {
        "frame": frame,
        "bone": {
            "index": bone.index,
            "binding_identity": binding,
            "track_name": bone.name,
            "track_names": [bone.name, bone.name_english],
            "before": before_bone,
            "after": after_bone,
            "delta_degrees": bone_delta,
        },
        "morph": morph_witness,
        "export_strategy": "bake_timeline",
    }


def _capture_motion_witness(root: str, adjustment: Mapping[str, Any], frames: Iterable[int]) -> dict[str, Any]:
    """Capture edited bone world matrices and optional morph values by identity."""

    from maya import cmds

    bone_info = adjustment.get("bone", {})
    bone_index = int(bone_info["index"])
    joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    binding = None
    for joint in joints:
        try:
            indexed = int(cmds.getAttr(f"{joint}.mmd_bone_index"))
        except (RuntimeError, TypeError, ValueError):
            continue
        if indexed == bone_index:
            binding = str(joint)
            break
    if not binding:
        raise ValueError(f"motion witness bone binding missing for index {bone_index}")
    skin_bind = None
    meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
    for mesh in meshes:
        for skin in cmds.listHistory(mesh, pruneDagObjects=True) or ():
            if cmds.nodeType(skin) != "skinCluster":
                continue
            influences = cmds.skinCluster(skin, query=True, influence=True) or []
            if binding not in influences:
                continue
            influence_index = influences.index(binding)
            raw_bind = cmds.getAttr(f"{skin}.bindPreMatrix[{influence_index}]")
            if isinstance(raw_bind, (list, tuple)) and len(raw_bind) == 1 and isinstance(raw_bind[0], (list, tuple)):
                raw_bind = raw_bind[0]
            candidate_bind = list(raw_bind) if isinstance(raw_bind, (list, tuple)) else None
            skin_bind = candidate_bind if candidate_bind and len(candidate_bind) == 16 else None
            break
        if skin_bind is not None:
            break

    def _matrix_product(left: list[float], right: list[float]) -> list[float]:
        return [
            sum(left[row * 4 + index] * right[index * 4 + column] for index in range(4))
            for row in range(4)
            for column in range(4)
        ]

    pose = {}
    for frame in frames:
        cmds.currentTime(int(frame), edit=True)
        world_matrix = [float(value) for value in (cmds.xform(binding, query=True, worldSpace=True, matrix=True) or ())]
        skin_matrix = (
            _matrix_product(world_matrix, [float(value) for value in skin_bind])
            if skin_bind is not None
            else world_matrix
        )
        pose[str(int(frame))] = {
            "world_matrix": [round(value, 7) for value in world_matrix],
            "skin_matrix": [round(value, 7) for value in skin_matrix],
        }
    witness = {"bone_index": bone_index, "binding_identity": binding, "pose": pose}
    morph_info = adjustment.get("morph")
    if isinstance(morph_info, Mapping):
        morph_index = int(morph_info["index"])
        plug = _resolve_morph_controller_input_plug(root, morph_index)
        witness["morph"] = {
            "index": morph_index,
            "binding_identity": morph_info.get("binding_identity"),
            "controller_input_plug": plug,
            "values": {
                str(int(frame)): float(cmds.getAttr(plug, time=int(frame)))
                for frame in frames
            },
        }
    return witness


def _compare_motion_morph_witness_values(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> list[str]:
    """Compare morph witness frames exactly and values within Maya float tolerance."""

    expected_values = {str(key): value for key, value in expected.items()}
    actual_values = {str(key): value for key, value in actual.items()}
    expected_keys = set(expected_values)
    actual_keys = set(actual_values)
    failures: list[str] = []
    if expected_keys != actual_keys:
        failures.append(
            "motion witness morph frame keys differ: "
            f"expected={sorted(expected_keys)!r} actual={sorted(actual_keys)!r}"
        )
    for key in sorted(expected_keys & actual_keys):
        try:
            difference = abs(float(expected_values[key]) - float(actual_values[key]))
        except (TypeError, ValueError):
            failures.append(f"motion witness morph value at frame {key} is not numeric")
            continue
        if difference > FLOAT_TOLERANCE:
            failures.append(
                f"motion witness morph value differs at frame {key}: "
                f"expected={expected_values[key]!r} actual={actual_values[key]!r}"
            )
    return failures


def _export_request(
    output: Path,
    report_dir: Path,
    *,
    export_format: str,
    target_model: str | None,
    start_frame: int | None = None,
    end_frame: int | None = None,
    model_name: str | None = None,
    case: Mapping[str, Any],
) -> Any:
    """Build a release-style ExportWorkflow request for one local case."""

    from mmd_tools.services.export_workflow_service import ExportWorkflowRequest

    options: dict[str, Any] = {
        "export_format": export_format,
        # Keep this request shape aligned with ExportTab.build_request().
        # Current Model is authoritative; the service projects it into the
        # model-track target while camera/light remain scene-level.
        "authoring_semantics": "auto",
        "require_target": True,
        "require_current_model": True,
        "current_model_root": target_model,
        "validation_report_dir": str(report_dir),
        "validation_report_evidence": {
            "gate": "LOCAL-ASSET-REPRESENTATIVE-ROUNDTRIP-HANG-1",
            "case": str(case["name"]),
            "fresh_import": True,
            "authoring_semantics": "auto",
            "metrics": case.get("metrics", {}),
            "oracles": ["parser", "scene", "fresh_scene"],
        },
    }
    if start_frame is not None:
        options["start_frame"] = int(start_frame)
    if end_frame is not None:
        options["end_frame"] = int(end_frame)
    if model_name is not None:
        options["model_name"] = model_name
    if export_format == "vmd":
        options["export_strategy"] = "bake_timeline"
    return ExportWorkflowRequest(str(output), options)


def _run_pmx_case(case: Mapping[str, Any], out_dir: Path, context: _WorkerContext) -> dict[str, Any]:
    """Run PMX parser/import/export/fresh-import semantic parity."""

    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.services.export_workflow_service import ExportWorkflowService
    from tests.roundtrip.pmx_roundtrip_runner import _compare_pmx_supported_content
    from tools.export_release_maya_probe import (
        _build_source_bone_semantics_oracle,
        _capture_bone_semantics_oracle,
        _capture_scene_oracle,
        _compare_bone_semantics,
        _compare_scene_oracles,
    )

    source = Path(str(case["pmx"]))
    output = out_dir / "model.pmx"
    report_dir = out_dir / "report"
    source_data = _phase(context, "source_parse", lambda: PmxData().parse_file(str(source)))
    source_root, source_oracle, source_bones, source_import_bones = _phase(
        context,
        "source_import_oracle",
        lambda: _pmx_source_import(
            source,
            _import_model_action,
            _capture_scene_oracle,
            _build_source_bone_semantics_oracle,
            _capture_bone_semantics_oracle,
        ),
    )
    source_failures = []
    source_failures.extend(_compare_bone_semantics(source_bones, source_import_bones, "source_import"))
    if source_failures:
        raise AssertionError("source import oracle failed: " + "; ".join(source_failures[:20]))
    adjustment = _phase(
        context,
        "model_adjustment",
        lambda: _apply_model_adjustment(source_root, case),
    )
    edited_oracle = _phase(
        context,
        "edited_model_oracle",
        lambda: _capture_scene_oracle(source_root, (0,)),
    )
    edited_bones = _phase(
        context,
        "edited_bone_oracle",
        lambda: _capture_bone_semantics_oracle(source_root),
    )

    request = _export_request(
        output,
        report_dir,
        export_format="pmx",
        target_model=source_root,
        case=case,
    )
    workflow = ExportWorkflowService()
    result = _phase(
        context,
        "export_write",
        lambda: workflow.execute(request, acknowledge_warnings=True),
    )
    if not result.succeeded:
        raise RuntimeError(f"PMX export failed: {result.error or result.report}")
    validation_evidence = _report_summary(result)
    acknowledged_warnings = _assert_execute_warnings(result, "pmx")
    exported_data = _phase(context, "exported_parse", lambda: PmxData().parse_file(str(output)))
    parser_diffs, parser_warnings = _compare_pmx_supported_content(
        source_data,
        exported_data,
        str(case["name"]),
    )
    fresh_root, fresh_oracle, _fresh_bones, fresh_import_bones = _phase(
        context,
        "fresh_import_oracle",
        lambda: _pmx_source_import(
            output,
            _import_model_action,
            _capture_scene_oracle,
            _build_source_bone_semantics_oracle,
            _capture_bone_semantics_oracle,
        ),
    )
    failures: list[str] = []
    failures.extend(
        _compare_scene_oracles(
            edited_oracle,
            fresh_oracle,
            pose=True,
            physics=True,
            morphs=False,
        )
    )
    failures.extend(_compare_morph_structure(source_oracle["morphs"], fresh_oracle["morphs"]))
    failures.extend(_compare_bone_semantics(edited_bones, fresh_import_bones, "fresh_import"))
    if edited_oracle.get("metadata", {}).get("mmd_display_frames_json") != fresh_oracle.get("metadata", {}).get("mmd_display_frames_json"):
        failures.append("metadata.mmd_display_frames_json differs")
    for field in ("mmd_comment",):
        if edited_oracle.get("metadata", {}).get(field) != fresh_oracle.get("metadata", {}).get(field):
            failures.append(f"metadata.{field} differs")
    if not adjustment.get("after", {}).get("comment"):
        failures.append("model adjustment sentinel is empty")
    if failures:
        raise AssertionError("PMX semantic mismatch: " + "; ".join(failures[:30]))
    return {
        "status": "pass",
        "kind": "pmx",
        "source": str(source),
        "output": str(output),
        "validation": validation_evidence,
        "acknowledged_warnings": acknowledged_warnings,
        "adjustment": adjustment,
        "parser_warnings": parser_warnings,
        "parser_normalization_diagnostics": {
            "status": "recorded",
            "diff_count": len(parser_diffs),
            "samples": parser_diffs[:20],
        },
        "semantic": {
            "model": True,
            "geometry": True,
            "materials": True,
            "bones_ik": True,
            "morphs": True,
            "display_frames": True,
            "physics": True,
            "index_references": True,
        },
        "parsed_counts": {
            "vertices": len(exported_data.vertices),
            "faces": len(exported_data.faces),
            "materials": len(exported_data.materials),
            "bones": len(exported_data.bones),
            "morphs": len(exported_data.morphs),
            "rigid_bodies": len(exported_data.rigid_bodies),
            "joints": len(exported_data.joints),
        },
        "fresh_root": fresh_root,
    }


def _pmx_source_import(
    source: Path,
    fresh_import: Callable[..., str],
    capture_scene_oracle: Callable[..., dict[str, Any]],
    build_bone_oracle: Callable[..., dict[str, Any]],
    capture_bone_oracle: Callable[..., dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Import a PMX and capture parser-backed scene, bone, and morph oracles."""

    root = fresh_import(source)
    scene = capture_scene_oracle(root, (0,))
    return (
        root,
        scene,
        build_bone_oracle(source),
        capture_bone_oracle(root),
    )


def _run_warm_vmd_export_samples(
    case: Mapping[str, Any],
    out_dir: Path,
    context: _WorkerContext,
    workflow: Any,
    source_root: str,
    start_frame: int,
    end_frame: int,
    model_name: str,
    warm_runs: int,
) -> list[dict[str, Any]]:
    """Write independent one-shot samples from the edited source scene."""

    samples: list[dict[str, Any]] = []
    for sample_index in range(warm_runs):
        sample_number = sample_index + 1
        output = out_dir / f"motion-warm-{sample_number:02d}.vmd"
        report_dir = out_dir / f"warm-report-{sample_number:02d}"
        request = _export_request(
            output,
            report_dir,
            export_format="vmd",
            target_model=source_root,
            start_frame=start_frame,
            end_frame=end_frame,
            model_name=model_name,
            case=case,
        )
        phase_start = len(context.phases)
        budget_start = len(context.export_write_budget_violations)
        sample: dict[str, Any] = {
            "index": sample_number,
            "temperature": "warm",
            "output": str(output),
            "status": "fail",
        }
        try:
            result = _phase(
                context,
                "export_write",
                lambda: workflow.execute(request, acknowledge_warnings=True),
            )
            if not result.succeeded:
                raise RuntimeError(f"VMD export failed: {result.error or result.report}")
            acknowledged_warnings = _assert_execute_warnings(result, "vmd")
            budget_evidence = list(context.export_write_budget_violations[budget_start:])
            sample.update(
                {
                    "status": "pass" if not budget_evidence else "fail",
                    "validation": _report_summary(result),
                    "acknowledged_warnings": acknowledged_warnings,
                    "performance_evidence": {
                        "export_write_budget_sec": context.export_write_budget_sec,
                        "violations": budget_evidence,
                    },
                }
            )
            if budget_evidence:
                sample["failure_classification"] = "performance_timeout"
                sample["error"] = (
                    "export_write exceeded budget: "
                    f"expected={budget_evidence[0]['expected_sec']:g}s "
                    f"actual={budget_evidence[0]['actual_sec']:g}s"
                )
        except PhaseTimeoutError as exc:
            sample.update(
                {
                    "status": "timeout",
                    "failure_classification": "performance_timeout",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=20),
                }
            )
        except Exception as exc:  # noqa: BLE001 - sample evidence is serialized.
            sample.update(
                {
                    "status": "fail",
                    "failure_classification": _classify_failure(error=str(exc)),
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=30),
                }
            )
        sample["phase_timing"] = list(context.phases[phase_start:])
        samples.append(sample)
        _write_json(out_dir / f"warm-export-{sample_number:02d}.json", sample)
        if sample["status"] != "pass":
            break
    return samples


def _skip_warm_vmd_export_samples(
    out_dir: Path,
    context: _WorkerContext,
    warm_runs: int,
    cold_budget_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Record warm samples skipped after a cold export budget violation."""

    samples: list[dict[str, Any]] = []
    for sample_index in range(warm_runs):
        sample_number = sample_index + 1
        sample = {
            "index": sample_number,
            "temperature": "warm",
            "status": "skipped",
            "skip_reason": "skipped_due_to_cold_budget",
            "failure_classification": "performance_timeout",
            "output": str(out_dir / f"motion-warm-{sample_number:02d}.vmd"),
            "output_written": False,
            "error": (
                "warm export skipped because cold export_write exceeded budget: "
                f"expected={cold_budget_evidence['expected_sec']:g}s "
                f"actual={cold_budget_evidence['actual_sec']:g}s"
            ),
            "performance_evidence": {
                "export_write_budget_sec": context.export_write_budget_sec,
                "cold_violation": dict(cold_budget_evidence),
            },
            "phase_timing": [],
        }
        samples.append(sample)
        _write_json(out_dir / f"warm-export-{sample_number:02d}.json", sample)
    return samples


def _export_diagnostics_sink(path: Path, case_name: str) -> Callable[[Any], None]:
    """Create a bounded atomic live snapshot sink for one export call."""

    def publish(snapshot: Any) -> None:
        _write_atomic_json(
            path,
            {
                "schema_version": 1,
                "case": str(case_name),
                "phase": "export_bake_timeline",
                "updated_at": time.time(),
                "snapshot": snapshot,
            },
        )

    publish({"status": "started"})
    return publish


def _run_vmd_exports(
    case: Mapping[str, Any],
    out_dir: Path,
    context: _WorkerContext,
    workflow: Any,
    request: Any,
    source_root: str,
    source_payload: Mapping[str, Any],
    required_track_names: Mapping[str, Iterable[str]],
    adjustment: dict[str, Any],
    start_frame: int,
    end_frame: int,
    model_name: str,
    warm_runs: int,
    export_evidence: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Write cold and warm exports; each sample owns its own lifecycle."""

    from mmd_tools.core.vmd_data import VmdData

    try:
        source_omissions = None
        collected_payload = source_payload
        cold_export_phase_start = len(context.phases)
        result = _phase(
            context,
            "export_write",
            lambda: workflow.execute(request, acknowledge_warnings=True),
        )
        if not result.succeeded:
            raise RuntimeError(f"VMD export failed: {result.error or result.report}")
        validation_evidence = _report_summary(result)
        acknowledged_warnings = _assert_execute_warnings(result, "vmd")
        exported_data = _phase(
            context,
            "exported_parse",
            lambda: VmdData().parse_file(str(out_dir / "motion.vmd")),
        )
        exported_payload = _vmd_payload(exported_data)
        adjustment["exported_tracks"] = _vmd_edit_track_witness(exported_payload, adjustment)
        track_boundary_failures = _bake_timeline_track_boundary_diff(
            source_payload,
            collected_payload,
            exported_payload,
            required_track_names,
            source_omission_commitment=source_omissions,
        )
        parser_failures = [
            f"{boundary}: {failure}"
            for boundary, boundary_failures in track_boundary_failures.items()
            for failure in boundary_failures
        ]
        source_total_keys = sum(
            len(source_payload[section])
            for section in ("bone", "morph", "camera", "light", "shadow", "ik")
        )
        exported_total_keys = sum(
            len(exported_payload[section])
            for section in ("bone", "morph", "camera", "light", "shadow", "ik")
        )
        cold_export_phases = [
            dict(item)
            for item in context.phases[cold_export_phase_start:]
            if str(item.get("name")) == "export_write"
        ]
        cold_budget_evidence = _export_write_budget_evidence(
            cold_export_phases,
            context.export_write_budget_sec,
        )
        cold_phase_timing = list(context.phases[cold_export_phase_start:])
        warm_samples = (
            _skip_warm_vmd_export_samples(out_dir, context, warm_runs, cold_budget_evidence)
            if cold_budget_evidence
            else _run_warm_vmd_export_samples(
                case,
                out_dir,
                context,
                workflow,
                source_root,
                start_frame,
                end_frame,
                model_name,
                warm_runs,
            )
        )
        return {
            "validation": validation_evidence,
            "acknowledged_warnings": acknowledged_warnings,
            "exported_data": exported_data,
            "exported_payload": exported_payload,
            "collected_payload": collected_payload,
            "track_boundary_failures": track_boundary_failures,
            "parser_failures": parser_failures,
            "source_total_keys": source_total_keys,
            "exported_total_keys": exported_total_keys,
            "cold_export_phases": cold_export_phases,
            "cold_phase_timing": cold_phase_timing,
            "cold_budget_evidence": cold_budget_evidence,
            "warm_samples": warm_samples,
        }
    finally:
        pass


def _motion_phase_evidence(
    context: _WorkerContext,
    export_evidence: Mapping[str, Any],
    cold_phase_timing: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize the one-shot export timing boundaries."""

    def first_phase_entry(name: str, entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        return next(
            (dict(item) for item in entries if str(item.get("name")) == name),
            {},
        )

    edit_start_index = next(
        (index for index, item in enumerate(context.phases) if item.get("name") == "motion_adjustment"),
        None,
    )
    first_export_index = next(
        (
            index
            for index, item in enumerate(context.phases)
            if index >= (edit_start_index if edit_start_index is not None else 0)
            and item.get("name") == "export_write"
        ),
        None,
    )
    edit_to_first_file_phases = (
        context.phases[edit_start_index : first_export_index + 1]
        if edit_start_index is not None and first_export_index is not None
        else []
    )
    edit_to_first_file_sec = round(
        sum(float(item.get("wall_sec", 0.0) or 0.0) for item in edit_to_first_file_phases),
        6,
    )
    return {
        "export_bake_timeline": dict(export_evidence.get("phase_timing", {})),
        "cold_export": first_phase_entry("export_write", cold_phase_timing),
        "edit_to_first_file": {
            "wall_sec": edit_to_first_file_sec,
            "method": "sum recorded phase wall_sec from motion_adjustment through first export_write",
            "from_phase": "motion_adjustment",
            "through_phase": "export_write",
            "phase_names": [str(item.get("name")) for item in edit_to_first_file_phases],
        },
    }


def _run_vmd_case(
    case: Mapping[str, Any],
    out_dir: Path,
    context: _WorkerContext,
    *,
    warm_runs: int = 0,
) -> dict[str, Any]:
    """Run PMX+VMD Action import, Bake Timeline edit/export, and fresh pose parity."""

    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.services.export_workflow_service import ExportWorkflowService
    from mmd_tools.adapters.maya_vmd_prepare_backend import (
        create_maya_bake_timeline_vmd_action,
    )
    from tools.export_release_maya_probe import (
        _capture_camera_light_scene_oracle,
        _capture_scene_oracle,
        _compare_camera_light_semantics,
        _compare_scene_oracles,
    )

    source_model = Path(str(case["pmx"]))
    source_vmd = Path(str(case["vmd"]))
    metrics = case.get("metrics", {})
    oracle_frames = [int(frame) for frame in case.get("oracle_frames", ())]
    if len(oracle_frames) < 2:
        raise ValueError(f"case {case['name']!r} has fewer than two oracle frames")
    recipe = _adjustment_recipe(case)
    edit_frame = int(_motion_recipe_value(recipe, "edit_frame", _motion_recipe_value(recipe, "frame", 1)))
    evaluation_frames = _motion_evaluation_frames(oracle_frames, edit_frame)
    output = out_dir / "motion.vmd"
    report_dir = out_dir / "report"
    source_data = _phase(context, "source_parse", lambda: VmdData().parse_file(str(source_vmd)))
    source_payload = _vmd_payload(source_data)

    def import_source() -> tuple[
        str,
        dict[str, Any],
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        root = _import_model_action(source_model)
        _import_vmd_action(root, source_model, source_vmd)
        ik_witness = (
            _capture_ik_import_witness(root, source_data.ik_show_hide_frames)
            if source_data.ik_show_hide_frames
            else None
        )
        scene = _capture_scene_oracle(root, evaluation_frames)
        camera_scene = None
        if source_data.camera_frames and source_data.light_frames:
            camera_scene = _capture_camera_light_scene_oracle(root, oracle_frames)
        return root, scene, camera_scene, ik_witness

    source_root, source_oracle, source_camera_oracle, source_ik_witness = _phase(
        context,
        "source_import_oracle",
        import_source,
    )
    required_track_names = _phase(
        context,
        "source_track_resolution",
        lambda: _model_resolved_motion_track_names(source_root),
    )
    adjustment = _phase(
        context,
        "motion_adjustment",
        lambda: _apply_motion_adjustment(source_root, source_data, case),
    )
    edited_oracle = _phase(
        context,
        "edited_motion_oracle",
        lambda: _capture_scene_oracle(source_root, evaluation_frames),
    )
    adjustment["witness"] = _phase(
        context,
        "edited_motion_witness",
        lambda: _capture_motion_witness(source_root, adjustment, evaluation_frames),
    )
    start_frame = int(metrics.get("frame_start", oracle_frames[0]))
    end_frame = int(metrics.get("frame_end", oracle_frames[-1]))
    request = _export_request(
        output,
        report_dir,
        export_format="vmd",
        target_model=source_root,
        start_frame=start_frame,
        end_frame=end_frame,
        model_name=str(getattr(source_data.header, "model_name", "") or ""),
        case=case,
    )
    diagnostics_path = out_dir / "export-diagnostics.live.json"
    diagnostics_sink = _export_diagnostics_sink(diagnostics_path, str(case["name"]))
    workflow = ExportWorkflowService(
        vmd_action=create_maya_bake_timeline_vmd_action(
            diagnostics_sink=diagnostics_sink,
        ),
    )
    export_evidence = {"diagnostics_path": str(diagnostics_path)}
    export_result = _run_vmd_exports(
        case,
        out_dir,
        context,
        workflow,
        request,
        source_root,
        source_payload,
        required_track_names,
        adjustment,
        start_frame,
        end_frame,
        str(getattr(source_data.header, "model_name", "") or ""),
        warm_runs,
        export_evidence,
    )
    validation_evidence = export_result["validation"]
    export_evidence.update(
        status="succeeded",
        phase_timing=dict(export_result["cold_export_phases"][0])
        if export_result["cold_export_phases"]
        else {},
    )
    acknowledged_warnings = export_result["acknowledged_warnings"]
    exported_data = export_result["exported_data"]
    exported_payload = export_result["exported_payload"]
    collected_payload = export_result["collected_payload"]
    track_boundary_failures = export_result["track_boundary_failures"]
    parser_failures = export_result["parser_failures"]
    source_total_keys = export_result["source_total_keys"]
    exported_total_keys = export_result["exported_total_keys"]
    key_inflation = exported_total_keys - source_total_keys
    cold_export_phases = export_result["cold_export_phases"]
    cold_budget_evidence = export_result["cold_budget_evidence"]
    warm_samples = export_result["warm_samples"]
    cold_phase_timing = export_result["cold_phase_timing"]
    phase_evidence = _motion_phase_evidence(
        context,
        export_evidence,
        cold_phase_timing,
    )
    def import_fresh() -> tuple[
        str,
        dict[str, Any],
        dict[str, Any] | None,
        dict[str, Any],
        dict[str, Any] | None,
    ]:
        fresh_root = _import_model_action(source_model)
        _import_vmd_action(fresh_root, source_model, output)
        fresh_ik_witness = (
            _capture_ik_import_witness(fresh_root, source_data.ik_show_hide_frames)
            if source_ik_witness is not None
            else None
        )
        scene = _capture_scene_oracle(fresh_root, evaluation_frames)
        camera_scene = None
        if exported_data.camera_frames and exported_data.light_frames:
            camera_scene = _capture_camera_light_scene_oracle(fresh_root, oracle_frames)
        fresh_adjustment = dict(adjustment)
        if isinstance(adjustment.get("morph"), Mapping):
            from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition

            fresh_spec = build_maya_authoring_composition().coordinator.read_spec(fresh_root)
            morph_index = int(adjustment["morph"]["index"])
            fresh_morph = next((item for item in fresh_spec.morphs if item.index == morph_index), None)
            if fresh_morph is None or not fresh_morph.binding_identity:
                raise ValueError(f"fresh motion witness morph binding missing for index {morph_index}")
            fresh_adjustment["morph"] = dict(adjustment["morph"])
            fresh_adjustment["morph"]["binding_identity"] = fresh_morph.binding_identity
        fresh_adjustment["witness"] = _capture_motion_witness(
            fresh_root, fresh_adjustment, evaluation_frames
        )
        return fresh_root, scene, camera_scene, fresh_adjustment, fresh_ik_witness

    fresh_root, fresh_oracle, fresh_camera_oracle, fresh_adjustment, fresh_ik_witness = _phase(
        context,
        "fresh_import_oracle",
        import_fresh,
    )
    failures = list(parser_failures)
    if source_ik_witness is not None:
        if fresh_ik_witness is None:
            failures.append("IK semantic mismatch: fresh import IK witness is missing")
        elif source_ik_witness["names"] != fresh_ik_witness["names"]:
            failures.append("IK semantic mismatch: fresh import IK node names differ")
    failures.extend(
        _compare_scene_oracles(
            edited_oracle,
            fresh_oracle,
            pose=True,
            pose_tolerance=VMD_EXPORT_BAKE_TIMELINE_POSE_TOLERANCE,
            mesh=False,
            materials=False,
            morphs=False,
        )
    )
    failures.extend(_compare_morph_structure(edited_oracle["morphs"], fresh_oracle["morphs"]))
    if source_camera_oracle is not None and fresh_camera_oracle is not None:
        failures.extend(
            _compare_camera_light_semantics(
                source_camera_oracle,
                fresh_camera_oracle,
                "fresh_import",
            )
        )
    expected_witness = adjustment.get("witness", {})
    actual_witness = fresh_adjustment.get("witness", {})
    if expected_witness.get("bone_index") != actual_witness.get("bone_index"):
        failures.append("motion witness bone identity differs")
    if expected_witness.get("pose") != actual_witness.get("pose"):
        failures.append("motion witness world/skin matrices differ")
    if isinstance(expected_witness.get("morph"), Mapping):
        failures.extend(
            _compare_motion_morph_witness_values(
                expected_witness["morph"].get("values", {}),
                actual_witness.get("morph", {}).get("values", {}),
            )
        )
    if failures:
        raise AssertionError("VMD semantic mismatch: " + "; ".join(failures[:30]))
    cold_sample = {
        "index": 1,
        "temperature": "cold",
        "output": str(output),
        "status": "fail" if cold_budget_evidence else "pass",
        "phase_timing": cold_export_phases,
        "performance_evidence": {
            "export_write_budget_sec": context.export_write_budget_sec,
            "violations": [cold_budget_evidence] if cold_budget_evidence else [],
        },
    }
    if cold_budget_evidence:
        cold_sample["failure_classification"] = "performance_timeout"
        cold_sample["error"] = (
            "export_write exceeded budget: "
            f"expected={cold_budget_evidence['expected_sec']:g}s "
            f"actual={cold_budget_evidence['actual_sec']:g}s"
        )
    return {
        "status": "pass",
        "kind": "pmx_vmd",
        "classification": case.get("classification"),
        "source_model": str(source_model),
        "source": str(source_vmd),
        "output": str(output),
        "validation": validation_evidence,
        "export_operation": export_evidence,
        "phase_evidence": phase_evidence,
        "acknowledged_warnings": acknowledged_warnings,
        "adjustment": adjustment,
        "evaluation_frames": evaluation_frames,
        "semantic": {
            "sections": True,
            "track_names": True,
            "key_frames": True,
            "ik_states": True,
            "bake_timeline_dense_semantics": True,
            "fresh_pose": True,
            "fresh_camera_light": source_camera_oracle is not None,
            "track_boundary_failures": track_boundary_failures,
        },
        "key_counts": {
            "source": source_total_keys,
            "exported": exported_total_keys,
            "inflation": key_inflation,
        },
        "track_counts": {
            section: {
                "source": len(source_payload[section]),
                "collected": len(collected_payload[section]),
                "exported": len(exported_payload[section]),
            }
            for section in ("bone", "morph", "camera", "light", "shadow", "ik")
        },
        "source_metrics": metrics,
        "ik_import_witness": {
            "source": source_ik_witness,
            "fresh": fresh_ik_witness,
        },
        "export_samples": {
            "cold": [cold_sample],
            "warm": warm_samples,
        },
    }


def _initialize_maya() -> None:
    """Initialize Maya standalone and register the project plugin."""

    import maya.standalone
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin

    try:
        maya.standalone.initialize(name="python")
    except RuntimeError:
        pass
    load_mmd_tools_plugin(ROOT)


def _run_worker(
    config_path: Path,
    result_path: Path,
    checkpoint: Path,
    phase_timeout_sec: float,
    export_write_budget_sec: float = DEFAULT_EXPORT_WRITE_BUDGET_SEC,
) -> int:
    """Run one case repeatedly in a single warmable mayapy process."""

    config = json.loads(config_path.read_text(encoding="utf-8"))
    case = config["case"]
    is_dense = str(case.get("classification")) == "dense"
    # Dense correctness is one full roundtrip.  Its additional repetitions
    # are independent export-only samples from the same edited source scene.
    repetitions = 1 if is_dense else int(config.get("repetitions", 1))
    warm_runs = int(config.get("warm_runs", 0)) if is_dense else 0
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    context = _WorkerContext(
        checkpoint,
        out_dir / "stacks",
        phase_timeout_sec,
        export_write_budget_sec,
    )
    document: dict[str, Any] = {
        "status": "fail",
        "case": case,
        "out_dir": str(out_dir),
        "repetitions": repetitions,
        "warm_runs": warm_runs,
        "export_sample_policy": (
            "dense: one full roundtrip/cold export plus independent warm samples"
            if is_dense
            else "non-dense: one full roundtrip/cold export"
        ),
        "runs": [],
        "worker_pid": os.getpid(),
    }
    try:
        _initialize_maya()
        for index in range(repetitions):
            run_dir = out_dir / f"run-{index:02d}"
            run_dir.mkdir(parents=True, exist_ok=True)
            context.phases = []
            context.export_write_budget_violations = []
            started = time.perf_counter()
            try:
                if case.get("vmd"):
                    result = _run_vmd_case(
                        case,
                        run_dir,
                        context,
                        warm_runs=warm_runs if index == 0 else 0,
                    )
                else:
                    result = _run_pmx_case(case, run_dir, context)
                run_status = "pass"
                error = None
                traceback_text = None
            except PhaseTimeoutError as exc:
                run_status = "timeout"
                result = None
                error = f"{type(exc).__name__}: {exc}"
                traceback_text = traceback.format_exc(limit=20)
            except Exception as exc:  # noqa: BLE001 - worker serializes evidence.
                run_status = "fail"
                result = None
                error = f"{type(exc).__name__}: {exc}"
                traceback_text = traceback.format_exc(limit=30)
            sample_failure = None
            if run_status == "pass" and isinstance(result, Mapping):
                export_samples = result.get("export_samples", {})
                warm_samples = export_samples.get("warm", []) if isinstance(export_samples, Mapping) else []
                sample_failure = next(
                    (
                        sample
                        for sample in warm_samples
                        if isinstance(sample, Mapping) and sample.get("status") != "pass"
                    ),
                    None,
                )
                if sample_failure is not None:
                    run_status = "fail"
                    error = str(
                        sample_failure.get("error")
                        or f"warm export sample {sample_failure.get('index')} failed"
                    )
                    traceback_text = sample_failure.get("traceback")
            budget_evidence = list(context.export_write_budget_violations)
            if not budget_evidence:
                fallback_budget_evidence = _export_write_budget_evidence(
                    context.phases,
                    context.export_write_budget_sec,
                )
                if fallback_budget_evidence is not None:
                    budget_evidence.append(fallback_budget_evidence)
            budget_only_failure = bool(budget_evidence and run_status == "pass")
            if budget_only_failure:
                run_status = "fail"
                error = (
                    "export_write exceeded budget: "
                    f"expected={budget_evidence[0]['expected_sec']:g}s "
                    f"actual={budget_evidence[0]['actual_sec']:g}s"
                )
            failure_classification = (
                None
                if run_status == "pass"
                else "performance_timeout"
                if budget_only_failure
                else str(sample_failure.get("failure_classification"))
                if isinstance(sample_failure, Mapping)
                and sample_failure.get("failure_classification") in FAILURE_CLASSIFICATIONS
                else _classify_failure(
                    status=run_status,
                    error=error,
                    phase=(context.phases[-1].get("name") if context.phases else None),
                )
            )
            document["runs"].append(
                {
                    "index": index,
                    "temperature": "cold" if index == 0 else "warm",
                    "status": run_status,
                    "elapsed_sec": round(time.perf_counter() - started, 3),
                    "phase_timing": list(context.phases),
                    "result": result,
                    "error": error,
                    "failure_classification": failure_classification,
                    "performance_evidence": {
                        "export_write_budget_sec": context.export_write_budget_sec,
                        "violations": budget_evidence,
                    },
                    "traceback": traceback_text,
                }
            )
            if run_status != "pass":
                break
        document["status"] = "pass" if len(document["runs"]) == repetitions and all(
            run["status"] == "pass" for run in document["runs"]
        ) else "fail"
        if document["status"] != "pass":
            document["failure_classification"] = _worker_failure_classification(document)
    except Exception as exc:  # noqa: BLE001 - include initialization failures.
        document["status"] = "fail"
        document["error"] = f"{type(exc).__name__}: {exc}"
        document["failure_classification"] = _classify_failure(
            status="environment_blocked", error=document["error"]
        )
        document["traceback"] = traceback.format_exc(limit=30)
    _write_json(result_path, document)
    return 0 if document["status"] == "pass" else 1


def _run_child(
    mayapy: Path,
    config_path: Path,
    result_path: Path,
    checkpoint: Path,
    *,
    phase_timeout_sec: float,
    case_timeout_sec: float,
    export_write_budget_sec: float = DEFAULT_EXPORT_WRITE_BUDGET_SEC,
) -> dict[str, Any]:
    """Run and watchdog one mayapy child, returning host-owned case evidence."""

    log_dir = config_path.parent
    stdout_path = log_dir / "mayapy.stdout.log"
    stderr_path = log_dir / "mayapy.stderr.log"
    command = [
        str(mayapy),
        str(Path(__file__).resolve()),
        "--worker-config",
        str(config_path),
        "--worker-result",
        str(result_path),
        "--worker-checkpoint",
        str(checkpoint),
        "--phase-timeout-sec",
        str(phase_timeout_sec),
        "--export-write-budget-sec",
        str(export_write_budget_sec),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        entry for entry in (str(ROOT), env.get("PYTHONPATH", "")) if entry
    )
    env["MAYA_SKIP_USERSETUP_PY"] = "1"
    env["MMD_TOOLS_SKIP_SHADER_OVERRIDE"] = "1"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
        timeout_kind = None
        timeout_checkpoint: dict[str, Any] | None = None
        while process.poll() is None:
            elapsed = time.perf_counter() - started
            current_checkpoint = _read_json(checkpoint)
            if current_checkpoint and current_checkpoint.get("timed_out"):
                timeout_kind = "phase_timeout"
                grace_deadline = time.perf_counter() + 3.0
                timeout_checkpoint = current_checkpoint
                while time.perf_counter() < grace_deadline and process.poll() is None:
                    time.sleep(0.25)
                    timeout_checkpoint = _read_json(checkpoint) or timeout_checkpoint
                process.kill()
                break
            if elapsed >= case_timeout_sec:
                timeout_kind = "case_timeout"
                timeout_checkpoint = current_checkpoint
                process.kill()
                break
            time.sleep(0.25)
        return_code = process.wait()
    child_result = _read_json(result_path)
    if timeout_kind is not None:
        return {
            "status": "timeout",
            "failure_classification": "performance_timeout",
            "timeout_kind": timeout_kind,
            "return_code": return_code,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "last_phase": timeout_checkpoint,
            "result": child_result,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    if child_result is None:
        return {
            "status": "crash",
            "failure_classification": "environment_blocked",
            "return_code": return_code,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": "mayapy exited without a worker result",
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        }
    child_result["return_code"] = return_code
    child_result["stdout"] = str(stdout_path)
    child_result["stderr"] = str(stderr_path)
    nested_failure = _worker_failure_classification(child_result)
    if nested_failure is not None:
        child_result["failure_classification"] = nested_failure
    return child_result


def _repetitions(case: Mapping[str, Any], cold_runs: int, warm_runs: int) -> int:
    """Return full correctness run count; Dense warm runs are export-only."""

    if str(case.get("classification")) == "dense":
        return 1
    return 1


def _summary_markdown(document: Mapping[str, Any]) -> str:
    """Render a compact human-readable summary without hiding failures."""

    lines = [
        "# Local asset roundtrip",
        "",
        f"- status: `{document.get('status')}`",
        f"- Maya: `{document.get('maya')}`",
        f"- run id: `{document.get('run_id')}`",
        f"- profile: `{document.get('profile') or 'all'}`",
        f"- manifest: `{document.get('manifest')}`",
        f"- export_write budget: `{document.get('export_write_budget_sec', 'n/a')}s`",
        "- export samples: Dense uses one full roundtrip/cold sample plus warm samples from the same "
        "edited source scene; non-dense uses one full roundtrip/cold sample.",
        "",
        "| Case | Classification | Status | Failure | Runs | Export samples | Last phase |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for case in document.get("cases", ()):
        runs = case.get("runs", [])
        last_phase = ""
        if runs:
            phase_timing = runs[-1].get("phase_timing", [])
            if phase_timing:
                last_phase = str(phase_timing[-1].get("name", ""))
        if case.get("last_phase"):
            last_phase = str(case["last_phase"].get("phase", last_phase))
        cold_samples = 0
        warm_samples = 0
        for run in runs:
            result = run.get("result", {})
            export_samples = result.get("export_samples", {}) if isinstance(result, Mapping) else {}
            if not isinstance(export_samples, Mapping):
                continue
            cold_samples += len(export_samples.get("cold", ()))
            warm_samples += len(export_samples.get("warm", ()))
        warm_expected = int(case.get("warm_runs", 0) or 0)
        sample_summary = f"cold={cold_samples}/1, warm={warm_samples}/{warm_expected}"
        lines.append(
            f"| {case.get('name')} | {case.get('classification')} | {case.get('status')} | "
            f"{case.get('failure_classification', '')} | {len(runs)} | {sample_summary} | {last_phase} |"
        )
    lines.extend(["", "## Artifacts", ""])
    for case in document.get("cases", ()):
        lines.append(f"- `{case.get('name')}`: `{case.get('out_dir')}`")
    return "\n".join(lines) + "\n"


def _run_host(args: argparse.Namespace) -> int:
    """Run selected cases in isolated mayapy workers and write summary artifacts."""

    report_root = _require_build_path(args.out_dir, "--out-dir")
    run_id = _safe_name(str(getattr(args, "run_id", None) or time.strftime("%Y%m%d-%H%M%S")))
    out_dir = report_root / run_id
    try:
        manifest_path, loaded = _load_manifest(args.manifest)
    except Exception as exc:
        summary = {
            "status": "fail",
            "maya": str(args.maya),
            "manifest": str(Path(args.manifest)),
            "run_id": run_id,
            "export_write_budget_sec": args.export_write_budget_sec,
            "cases": [],
            "failure_classification": _classify_failure(error=str(exc), status="manifest"),
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(out_dir / "summary.json", summary)
        (out_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
        print(f"summary: {out_dir / 'summary.json'}")
        print(f"status: {summary['status']}")
        return 1
    cases = _select_cases(loaded["cases"], args.case, args.profile)
    if not cases:
        raise ValueError("no cases selected")
    if args.cold_runs < 1 or args.warm_runs < 0:
        raise ValueError("cold runs must be positive and warm runs must be non-negative")
    if (
        args.phase_timeout_sec <= 0
        or args.case_timeout_sec <= 0
        or args.export_write_budget_sec <= 0
    ):
        raise ValueError("timeouts must be positive")
    out_dir.mkdir(parents=True, exist_ok=True)
    from tests.common.maya_location import mayapy as mayapy_for_version

    mayapy = mayapy_for_version(args.maya)
    if not mayapy.is_file():
        raise FileNotFoundError(f"mayapy not found for Maya {args.maya}: {mayapy}")
    summary: dict[str, Any] = {
        "status": "fail",
        "maya": str(args.maya),
        "run_id": run_id,
        "profile": args.profile,
        "manifest": str(manifest_path),
        "case_timeout_sec": args.case_timeout_sec,
        "phase_timeout_sec": args.phase_timeout_sec,
        "export_write_budget_sec": args.export_write_budget_sec,
        "cases": [],
    }
    for case in cases:
        case_dir = out_dir / "cases" / _safe_name(str(case["name"]))
        case_dir.mkdir(parents=True, exist_ok=True)
        repetitions = _repetitions(case, args.cold_runs, args.warm_runs)
        warm_runs = args.warm_runs if str(case.get("classification")) == "dense" else 0
        config_path = case_dir / "worker-config.json"
        result_path = case_dir / "worker-result.json"
        checkpoint = case_dir / "phase-status.json"
        _write_json(
            config_path,
            {
                "schema_version": 1,
                "case": case,
                "out_dir": str(case_dir),
                "repetitions": repetitions,
                "warm_runs": warm_runs,
            },
        )
        result = _run_child(
            mayapy,
            config_path,
            result_path,
            checkpoint,
            phase_timeout_sec=args.phase_timeout_sec,
            case_timeout_sec=args.case_timeout_sec,
            export_write_budget_sec=args.export_write_budget_sec,
        )
        result["name"] = case["name"]
        result["classification"] = case.get("classification")
        if result.get("status") != "pass":
            nested_failure = _worker_failure_classification(result)
            result["failure_classification"] = nested_failure or _classify_failure(
                status=str(result.get("status")),
                error=str(result.get("error", "")),
                phase=str((result.get("last_phase") or {}).get("phase", "")),
            )
        result["out_dir"] = str(case_dir)
        summary["cases"].append(result)
        _write_json(out_dir / "summary.json", summary)
    summary["status"] = "pass" if all(case.get("status") == "pass" for case in summary["cases"]) else "fail"
    if summary["status"] != "pass":
        summary["failure_classification"] = next(
            (
                case.get("failure_classification")
                for case in summary["cases"]
                if case.get("status") != "pass" and case.get("failure_classification")
            ),
            "environment_blocked",
        )
    _write_json(out_dir / "summary.json", summary)
    (out_dir / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    print(f"summary: {out_dir / 'summary.json'}")
    print(f"status: {summary['status']}")
    return 0 if summary["status"] == "pass" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse host and hidden mayapy worker options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--case", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--run-id", default=None, help="Stable report run identifier")
    parser.add_argument("--phase-timeout-sec", type=float, default=300.0)
    parser.add_argument("--case-timeout-sec", type=float, default=1800.0)
    parser.add_argument(
        "--export-write-budget-sec",
        type=float,
        default=DEFAULT_EXPORT_WRITE_BUDGET_SEC,
        help="Fail a completed export_write phase when it exceeds this wall-time budget (default: 60s)",
    )
    parser.add_argument("--cold-runs", type=int, default=1)
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--worker-config", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-checkpoint", default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for host orchestration or one mayapy worker."""

    args = parse_args(argv)
    if args.worker_config:
        if not args.worker_result or not args.worker_checkpoint:
            raise SystemExit("worker config, result, and checkpoint are required together")
        return _run_worker(
            Path(args.worker_config),
            Path(args.worker_result),
            Path(args.worker_checkpoint),
            args.phase_timeout_sec,
            args.export_write_budget_sec,
        )
    return _run_host(args)


if __name__ == "__main__":
    raise SystemExit(main())
