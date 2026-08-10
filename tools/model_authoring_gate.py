#!/usr/bin/env python
"""Run the v0.7 model-authoring Maya E2E gate.

This gate is deliberately separate from the public export-release gate.  The
host process validates a UTF-8 manifest, performs the host-independent
negative checks, and launches one ASCII-path mayapy worker per requested Maya
version.  The worker composes the production authoring graph with the
standalone E2E driver; an unavailable Maya/runtime dependency is reported as
``blocked`` rather than being treated as a successful no-op.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
REPORT_ROOT = BUILD_ROOT / "reports"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCHEMA_VERSION = 1
DEFAULT_MAYA_VERSIONS = (2024, 2026)
DEFAULT_MANIFEST = {
    "schema_version": SCHEMA_VERSION,
    "template_id": "pmx20-basic-v1",
    "model_name": "モデル作成E2E",
    "model_name_english": "Model Authoring E2E",
    "maya_versions": list(DEFAULT_MAYA_VERSIONS),
    "asset_paths": {},
}
REQUIRED_OPERATIONS = (
    "material.create",
    "material.edit",
    "material.reindex",
    "material.assign",
    "material.delete",
    "bone.register",
    "bone.capture_rest",
    "bone.reindex",
    "bone.unregister",
    "morph.create",
    "morph.edit",
    "morph.reindex",
    "export.pmx",
    "import.fresh_scene",
    "spec.read",
)
_MANIFEST_KEYS = {
    "schema_version",
    "template_id",
    "model_name",
    "model_name_english",
    "maya_versions",
    "asset_paths",
}


class ModelAuthoringGateError(ValueError):
    """Raised when the gate manifest or report contract is malformed."""


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelAuthoringGateError(f"{field} must be a mapping")
    return value


def _require_string(value: Any, *, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ModelAuthoringGateError(f"{field} must be a non-empty string")
    return value


def _require_maya_versions(value: Any) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ModelAuthoringGateError("maya_versions must be a sequence")
    versions: list[int] = []
    for position, raw in enumerate(value):
        if isinstance(raw, bool) or not isinstance(raw, int) or raw not in {2024, 2026}:
            raise ModelAuthoringGateError(
                f"maya_versions[{position}] must be integer 2024 or 2026"
            )
        if raw in versions:
            raise ModelAuthoringGateError(f"maya_versions contains duplicate {raw}")
        versions.append(raw)
    if not versions:
        raise ModelAuthoringGateError("maya_versions must not be empty")
    return tuple(versions)


def _validate_asset_paths(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _require_mapping(value, field="asset_paths")
    result: dict[str, str] = {}
    for key, raw in mapping.items():
        if not isinstance(key, str) or not key.strip():
            raise ModelAuthoringGateError("asset_paths keys must be non-empty strings")
        result[key] = _require_string(raw, field=f"asset_paths[{key!r}]")
    return result


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load and strictly validate one UTF-8 gate manifest.

    Args:
        path: JSON manifest path.  Its contents are always decoded as UTF-8.

    Returns:
        A detached, JSON-shaped manifest dictionary.

    Raises:
        ModelAuthoringGateError: If the mapping, schema, or scalar types are
            invalid.
    """
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ModelAuthoringGateError(f"could not read UTF-8 manifest {source}: {exc}") from exc
    mapping = _require_mapping(payload, field="manifest")
    unknown = sorted(set(mapping) - _MANIFEST_KEYS)
    if unknown:
        raise ModelAuthoringGateError(f"manifest contains unknown fields: {unknown}")
    schema_version = mapping.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ModelAuthoringGateError("schema_version must be an integer")
    if schema_version != SCHEMA_VERSION:
        raise ModelAuthoringGateError(
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )
    template_id = _require_string(mapping.get("template_id"), field="template_id")
    model_name = _require_string(mapping.get("model_name"), field="model_name")
    model_name_english = mapping.get("model_name_english", "")
    _require_string(model_name_english, field="model_name_english", allow_empty=True)
    versions = _require_maya_versions(mapping.get("maya_versions", DEFAULT_MAYA_VERSIONS))
    asset_paths = _validate_asset_paths(mapping.get("asset_paths"))
    return {
        "schema_version": SCHEMA_VERSION,
        "template_id": template_id,
        "model_name": model_name,
        "model_name_english": model_name_english,
        "maya_versions": list(versions),
        "asset_paths": dict(asset_paths),
    }


def _write_default_manifest() -> Path:
    """Materialize the deterministic no-argument manifest at an ASCII path."""
    path = REPORT_ROOT / "model-authoring-gate-default-manifest.json"
    _write_json(path, DEFAULT_MANIFEST)
    return path


def _require_build_path(value: str | Path, *, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve()
    if resolved == BUILD_ROOT or BUILD_ROOT not in resolved.parents:
        raise ModelAuthoringGateError(f"{field} must resolve under {BUILD_ROOT}: {resolved}")
    return resolved


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _status_case(name: str, status: str, *, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    if status not in {"pass", "fail", "blocked", "not_run"}:
        raise ValueError(f"invalid gate case status: {status}")
    result: dict[str, Any] = {"name": name, "status": status}
    if reason:
        result["reason"] = reason
    result.update(extra)
    return result


def _semantic_field_matrix(before: Any, after: Any) -> dict[str, Any]:
    """Compare strict Spec mappings by semantic section and fingerprint."""
    matrix: dict[str, Any] = {}
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {
            section: {"status": "not_run"}
            for section in ("model", "materials", "bones", "morphs", "fingerprint")
        }
    for section in ("model", "materials", "bones", "morphs"):
        left = before.get(section)
        right = after.get(section)
        matrix[section] = {
            "status": "pass" if left == right else "fail",
            "before": left,
            "after": right,
        }
    from mmd_tools.validation.snapshot import fingerprint_payload

    left_fingerprint = before.get("fingerprint") or fingerprint_payload(before)
    right_fingerprint = after.get("fingerprint") or fingerprint_payload(after)
    matrix["fingerprint"] = {
        "status": "pass" if left_fingerprint == right_fingerprint else "fail",
        "before": left_fingerprint,
        "after": right_fingerprint,
    }
    return matrix


def _require_completed_worker_result(
    result: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Validate the composition hook result without inferring missing work."""
    result_map = _require_mapping(result, field="run_authoring_e2e result")
    unknown = sorted(set(result_map) - {"operations", "before", "after", "negative_cases"})
    if unknown:
        raise ModelAuthoringGateError(f"run_authoring_e2e result has unknown fields: {unknown}")
    operations_raw = result_map.get("operations")
    if isinstance(operations_raw, (str, bytes, bytearray)) or not isinstance(operations_raw, Sequence):
        raise ModelAuthoringGateError("run_authoring_e2e result.operations must be a sequence")
    operations = [dict(_require_mapping(item, field="operation")) for item in operations_raw]
    names = [item.get("name") for item in operations]
    if names != list(REQUIRED_OPERATIONS):
        raise ModelAuthoringGateError(
            "run_authoring_e2e result.operations must contain the required ordered operation set"
        )
    if any(item.get("status") != "pass" for item in operations):
        raise ModelAuthoringGateError("run_authoring_e2e result contains a non-pass operation")
    operation_by_name = {item["name"]: item for item in operations}
    required_morph_types = {
        "vertex",
        "bone",
        "group",
        "material",
        "uv",
        "additional_uv1",
    }
    created_types = set(
        operation_by_name["morph.create"].get("created_types", ())
    )
    if created_types != required_morph_types:
        raise ModelAuthoringGateError(
            "morph.create must report every required supported morph type"
        )
    edited_types = set(operation_by_name["morph.edit"].get("edited_types", ()))
    if edited_types != {"vertex", "bone", "group", "material"}:
        raise ModelAuthoringGateError(
            "morph.edit must report vertex, bone, group, and material edits"
        )
    roundtrip_types = set(operation_by_name["morph.edit"].get("roundtrip_types", ()))
    if roundtrip_types != {"uv", "additional_uv1"}:
        raise ModelAuthoringGateError(
            "morph.edit must report UV/additional-UV roundtrip coverage"
        )
    matrix = _semantic_field_matrix(result_map.get("before"), result_map.get("after"))
    if any(item.get("status") != "pass" for item in matrix.values()):
        raise ModelAuthoringGateError("strict semantic field matrix comparison failed")
    negative_raw = result_map.get("negative_cases", _negative_cases())
    if isinstance(negative_raw, (str, bytes, bytearray)) or not isinstance(negative_raw, Sequence):
        raise ModelAuthoringGateError("run_authoring_e2e result.negative_cases must be a sequence")
    negative_cases = [dict(_require_mapping(item, field="negative_case")) for item in negative_raw]
    required_negative_names = {"writer_not_called", "unsupported_flip_impulse_reject"}
    if {item.get("name") for item in negative_cases} != required_negative_names:
        raise ModelAuthoringGateError("run_authoring_e2e result omitted a required negative case")
    if any(item.get("status") != "pass" for item in negative_cases):
        raise ModelAuthoringGateError("run_authoring_e2e result contains a failed negative case")
    return operations, matrix, negative_cases


def _writer_not_called_case() -> dict[str, Any]:
    """Exercise the real export action's no-writer contract without Maya."""
    from mmd_tools.adapters.maya_authoring_e2e import writer_not_called_case

    return writer_not_called_case(
        BUILD_ROOT / "temp" / f"authoring-negative-{os.getpid()}.pmx"
    )


def _unsupported_flip_impulse_case() -> dict[str, Any]:
    """Verify PMX 2.1 Flip and Impulse model-data policy is blocking."""
    from mmd_tools.validation.export_validator import validate_model_data

    base = {
        "model_name": "authoring-negative",
        "vertices": [{"position": [0.0, 0.0, 0.0], "bone_indices": [0]}],
        "faces": [[0, 0, 0]],
        "materials": [{"name": "mat", "face_count": 3}],
        "bones": None,
    }
    blocked_types: list[str] = []
    for morph_type in ("flip", "impulse"):
        report = validate_model_data({**base, "morphs": [{"type": morph_type, "offsets": []}]}, "pmx")
        if report.is_blocking:
            blocked_types.append(morph_type)
    passed = blocked_types == ["flip", "impulse"]
    return _status_case(
        "unsupported_flip_impulse_reject",
        "pass" if passed else "fail",
        reason=None if passed else f"blocking types: {blocked_types!r}",
        blocked_types=blocked_types,
    )


def _negative_cases() -> list[dict[str, Any]]:
    return [_writer_not_called_case(), _unsupported_flip_impulse_case()]


def _maya_report_template(version: int) -> dict[str, Any]:
    return {
        "gate_id": "V070-AUTHORING-E2E-1",
        "maya_version": version,
        "status": "blocked",
        "operations": [_status_case(operation, "not_run") for operation in REQUIRED_OPERATIONS],
        "semantic_field_matrix": {
            section: {"status": "not_run"}
            for section in ("model", "materials", "bones", "morphs", "fingerprint")
        },
        "negative_cases": [],
    }


def _run_maya_worker(manifest: Mapping[str, Any], version: int, report_path: Path) -> int:
    """Run one mayapy-side gate or emit an explicit blocked report."""
    report = _maya_report_template(version)
    try:
        import maya.standalone  # type: ignore[import-not-found]

        maya.standalone.initialize(name="python")
        from maya import cmds  # type: ignore[import-not-found]

        from tests.common.maya_plugin_setup import load_mmd_tools_plugin

        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        from mmd_tools.adapters.maya_authoring_factory import build_maya_authoring_composition
        from mmd_tools.adapters.maya_authoring_e2e import run_authoring_e2e
        from mmd_tools.adapters.maya_model_template_initializer import MayaModelTemplateInitializer

        composition = build_maya_authoring_composition(cmds_module=cmds)
        initializer = MayaModelTemplateInitializer(
            composition.cmds_adapter,
            metadata_backend_factory=lambda adapter: composition.metadata_backend,
        )
        result = run_authoring_e2e(
            initializer=initializer,
            template_id=manifest["template_id"],
            model_name=manifest["model_name"],
            model_name_english=manifest["model_name_english"],
            asset_paths=manifest["asset_paths"],
            coordinator=composition.coordinator,
            metadata_adapter=composition.metadata_adapter,
            cmds_adapter=composition.cmds_adapter,
            material_authoring=composition.material_authoring,
        )
        operations, matrix, negative_cases = _require_completed_worker_result(result)
        report["operations"] = operations
        report["semantic_field_matrix"] = matrix
        report["negative_cases"] = negative_cases
        report["status"] = "pass"
    except (ImportError, ModuleNotFoundError) as exc:
        report["reason"] = f"Maya/runtime dependency unavailable: {exc}"
        report["negative_cases"] = _negative_cases()
        _write_json(report_path, report)
        return 2
    except Exception as exc:  # pragma: no cover - exercised under mayapy
        report["status"] = "fail"
        report["reason"] = f"worker failed: {exc}"
        report["traceback"] = traceback.format_exc(limit=12)
        try:
            report["negative_cases"] = _negative_cases()
        except Exception as negative_exc:
            report["negative_cases"] = [_status_case("negative_cases", "fail", reason=str(negative_exc))]
    finally:
        try:
            import maya.standalone  # type: ignore[import-not-found]

            maya.standalone.uninitialize()
        except Exception:
            pass
    _write_json(report_path, report)
    return {"pass": 0, "fail": 1, "blocked": 2}.get(str(report["status"]), 1)


def _mayapy_path(version: int) -> Path:
    configured = os.environ.get(f"MAYA_LOCATION_{version}") or os.environ.get("MAYA_LOCATION")
    root = Path(configured) if configured else Path(f"C:/Program Files/Autodesk/Maya{version}")
    return root / "bin" / ("mayapy.exe" if os.name == "nt" else "mayapy")


def _run_host(manifest: Mapping[str, Any], output_path: Path) -> int:
    report: dict[str, Any] = {
        "gate_id": "V070-AUTHORING-E2E-1",
        "schema_version": SCHEMA_VERSION,
        "release_closure": False,
        "status": "blocked",
        "manifest": dict(manifest),
        "maya": [],
        "negative_cases": _negative_cases(),
    }
    alias_manifest = REPORT_ROOT / "model-authoring-gate-manifest.json"
    _write_json(alias_manifest, manifest)
    for raw_version in manifest["maya_versions"]:
        version = int(raw_version)
        child_report = REPORT_ROOT / f"model-authoring-gate-maya-{version}.json"
        mayapy = _mayapy_path(version)
        if not mayapy.is_file():
            child = _maya_report_template(version)
            child["reason"] = f"mayapy not found: {mayapy}"
            child["negative_cases"] = list(report["negative_cases"])
            _write_json(child_report, child)
            report["maya"].append(child)
            continue
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "MAYA_SKIP_USERSETUP_PY": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        command = [
            str(mayapy),
            str(Path(__file__).resolve()),
            "--worker",
            "--maya-version",
            str(version),
            "--manifest",
            str(alias_manifest),
            "--report",
            str(child_report),
        ]
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not child_report.is_file():
            child = _maya_report_template(version)
            child["status"] = "fail" if completed.returncode else "blocked"
            child["reason"] = "mayapy worker did not produce a report"
            child["stdout"] = completed.stdout[-4000:]
            child["stderr"] = completed.stderr[-4000:]
            _write_json(child_report, child)
        try:
            child = json.loads(child_report.read_text(encoding="utf-8"))
            child = dict(_require_mapping(child, field=f"maya[{version}] report"))
        except Exception as exc:
            child = _maya_report_template(version)
            child["status"] = "fail"
            child["reason"] = f"invalid mayapy report: {exc}"
        report["maya"].append(child)
    statuses = [case.get("status") for case in report["maya"]]
    negative_statuses = [case.get("status") for case in report["negative_cases"]]
    if any(status == "fail" for status in statuses + negative_statuses):
        report["status"] = "fail"
    elif statuses and all(status == "pass" for status in statuses) and all(
        status == "pass" for status in negative_statuses
    ):
        report["status"] = "pass"
    else:
        report["status"] = "blocked"
    _write_json(output_path, report)
    return {"pass": 0, "fail": 1, "blocked": 2}[report["status"]]


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for host and mayapy worker modes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        help="UTF-8 JSON manifest (defaults to a deterministic build/reports manifest)",
    )
    parser.add_argument("--out", default="build/reports/model-authoring-gate.json")
    parser.add_argument(
        "--maya",
        action="append",
        type=int,
        help="Maya version to run (repeatable; defaults to manifest values)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require every requested Maya worker to complete; blocked is never green",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--maya-version", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--report", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the host gate or one mayapy worker and return a process status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest_path = args.manifest or _write_default_manifest()
        manifest = load_manifest(manifest_path)
        if args.maya:
            manifest = {
                **manifest,
                "maya_versions": list(_require_maya_versions(args.maya)),
            }
        if args.worker:
            if args.maya_version not in DEFAULT_MAYA_VERSIONS or not args.report:
                raise ModelAuthoringGateError("worker requires --maya-version and --report")
            report_path = _require_build_path(args.report, field="report")
            return _run_maya_worker(manifest, int(args.maya_version), report_path)
        output_path = _require_build_path(args.out, field="out")
        return _run_host(manifest, output_path)
    except ModelAuthoringGateError as exc:
        try:
            output_path = _require_build_path(args.out, field="out")
            _write_json(
                output_path,
                {
                    "gate_id": "V070-AUTHORING-E2E-1",
                    "release_closure": False,
                    "status": "fail",
                    "reason": str(exc),
                },
            )
        except Exception:
            pass
        parser.error(str(exc))
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())


__all__ = [
    "ModelAuthoringGateError",
    "REQUIRED_OPERATIONS",
    "SCHEMA_VERSION",
    "build_parser",
    "load_manifest",
    "main",
]
