#!/usr/bin/env python
"""Orchestrate the reproducible v0.7 export release gate.

The gate keeps the release evidence in one bounded JSON/Markdown summary.  It
records every requested step, including explicit ``not_run`` entries, so a
green summary cannot hide an omitted Maya version, GUI run, fail-fixture
matrix, or external MMD-Anim check.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = (ROOT / "build").resolve()
MAX_OUTPUT = 1800
MAYA_VERSIONS = ("2024", "2026")
MORPH_ORACLE_TYPES = (
    "vertex",
    "bone",
    "uv",
    "additional_uv1",
    "additional_uv2",
    "additional_uv3",
    "additional_uv4",
    "material",
    "group",
)
MORPH_ORACLE_FIELDS = {
    "vertex": (
        "index",
        "name",
        "weight_1_object_space_deltas",
        "additional_uv_channel_count",
        "additional_uv_per_vertex_values",
    ),
    "bone": ("index", "name", "name_en", "panel", "raw_offsets"),
    "uv": ("index", "name", "name_en", "panel", "uv_offsets"),
    "additional_uv1": ("index", "name", "name_en", "panel", "additional_uv_offsets"),
    "additional_uv2": ("index", "name", "name_en", "panel", "additional_uv_offsets"),
    "additional_uv3": ("index", "name", "name_en", "panel", "additional_uv_offsets"),
    "additional_uv4": ("index", "name", "name_en", "panel", "additional_uv_offsets"),
    "material": ("index", "name", "name_en", "panel", "offsets"),
    "group": ("index", "name", "name_en", "panel", "offsets", "controller_outputs"),
}
MORPH_ORACLE_BOUNDARIES = ("source", "exported_file", "fresh_import")
MORPH_COMPARISON_BOUNDARIES = ("source_import", "exported_pmx", "fresh_import")
BONE_SEMANTICS_BOUNDARIES = ("source", "source_import", "exported_file", "fresh_import")
BONE_SEMANTICS_COMPARISON_BOUNDARIES = ("source_import", "exported_pmx", "fresh_import")
VMD_MODEL_TRACKS = ("bone", "morph", "ik_show_hide")
VMD_MODEL_TRACK_COMPARISON_BOUNDARIES = ("source_import", "exported_file", "fresh_import")
VMD_MODE_A_COMPARISON_BOUNDARIES = ("source", "source_import", "exported_file", "fresh_import")
VMD_MODE_A_POSE_FRAMES = (0, 9, 19, 29, 39, 49)
VMD_CAMERA_LIGHT_TRACKS = ("camera", "light")
VMD_CAMERA_LIGHT_COMPARISON_BOUNDARIES = ("source", "source_import", "exported_file", "fresh_import")
VMD_CAMERA_LIGHT_KEY_FRAMES = (0, 30, 60)
VMD_CAMERA_LIGHT_DENSE_FRAMES = (0, 15, 30, 45, 60)
VMD_CAMERA_LIGHT_CANONICAL_INTERPOLATION = tuple([20] * 24)
VMD_CAMERA_LIGHT_CAMERA_TOLERANCE = 1.0e-3
VMD_CAMERA_LIGHT_NUMERIC_TOLERANCE = 1.0e-4
BONE_SEMANTICS_FIELDS = (
    "index",
    "name",
    "name_en",
    "position",
    "parent_index",
    "transform_layer",
    "bone_flag",
    "connect_bone_index",
    "connect_position_offset",
    "grant_parent_bone_index",
    "grant_rate",
    "axis_direction",
    "x_axis_direction",
    "z_axis_direction",
    "key_value",
    "ik_target_bone_index",
    "ik_loop_count",
    "ik_limit_angle",
    "ik_links",
)


def _normalize_bone_semantics_value(value: Any) -> Any:
    """Round nested numeric bone payloads to the probe's canonical precision."""
    if isinstance(value, dict):
        return {key: _normalize_bone_semantics_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_bone_semantics_value(item) for item in value]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(float(value), 7)


MORPH_ORACLE_EXCLUSIONS = (
    "sdef vertex deformation",
    "UV morph runtime evaluation",
    "Impulse morph physics effect",
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.common.maya_location import mayapy as resolve_mayapy  # noqa: E402


def _require_build_path(value: str | Path, label: str) -> Path:
    """Resolve an output path and reject paths outside ``build/``."""
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if path != BUILD_ROOT and BUILD_ROOT not in path.parents:
        raise ValueError(f"{label} must resolve under {BUILD_ROOT}: {path}")
    return path


def _bounded(text: str | None) -> str:
    """Keep subprocess evidence bounded while retaining the useful tail."""
    value = str(text or "")
    if len(value) <= MAX_OUTPUT:
        return value
    return "..." + value[-(MAX_OUTPUT - 3) :]


def _sha256(path: Path) -> str:
    """Hash one report/artifact file for the release summary."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_release_provenance(*, run_id: str | None = None) -> dict[str, Any]:
    """Capture the source/worktree identity before the gate writes artifacts."""
    started_at = datetime.now(timezone.utc)
    provenance: dict[str, Any] = {
        "run_id": run_id or f"{started_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}",
        "timestamp": started_at.isoformat(),
        "branch": "",
        "head_sha": "",
        "dirty": None,
        "git_capture": {
            "branch": False,
            "head_sha": False,
            "status": False,
        },
    }

    def _git(*arguments: str) -> tuple[str, bool, int | None, str]:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            return "", False, None, f"{type(exc).__name__}: {exc}"
        output = (completed.stdout or "").strip()
        detail = (completed.stderr or "").strip()
        return output, completed.returncode == 0, completed.returncode, detail

    branch, branch_ok, branch_code, branch_error = _git(
        "symbolic-ref", "--quiet", "--short", "HEAD"
    )
    if not branch_ok and branch_code == 1:
        branch = "DETACHED"
        branch_ok = True
    head_sha, head_ok, _, head_error = _git("rev-parse", "HEAD")
    status, status_ok, _, status_error = _git("status", "--porcelain=v1", "--untracked-files=all")
    provenance["branch"] = branch
    provenance["head_sha"] = head_sha
    provenance["git_capture"] = {
        "branch": branch_ok,
        "head_sha": head_ok,
        "status": status_ok,
    }
    if status_ok:
        provenance["dirty"] = bool(status)
    errors = []
    if not branch_ok:
        errors.append(f"git branch capture failed: {branch_error or branch_code}")
    if not head_ok:
        errors.append(f"git HEAD capture failed: {head_error or 'unknown'}")
    if not status_ok:
        errors.append(f"git status capture failed: {status_error or 'unknown'}")
    if errors:
        provenance["error"] = "; ".join(errors)
    return provenance


def _validate_release_provenance(value: Any) -> list[str]:
    """Validate the mandatory run/source identity stored in a release summary."""
    failures: list[str] = []
    if not isinstance(value, dict):
        return ["provenance must be an object"]
    if not isinstance(value.get("run_id"), str) or not value["run_id"].strip():
        failures.append("provenance.run_id missing")
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        failures.append("provenance.timestamp missing")
    else:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
            failures.append("provenance.timestamp must be UTC RFC3339")
    if not isinstance(value.get("branch"), str) or not value["branch"].strip():
        failures.append("provenance.branch missing")
    head_sha = value.get("head_sha")
    if not isinstance(head_sha, str) or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None:
        failures.append("provenance.head_sha must be a full SHA-1")
    if not isinstance(value.get("dirty"), bool):
        failures.append("provenance.dirty must be boolean")
    capture = value.get("git_capture")
    if not isinstance(capture, dict):
        failures.append("provenance.git_capture missing")
    else:
        for field in ("branch", "head_sha", "status"):
            if capture.get(field) is not True:
                failures.append(f"provenance.git_capture.{field} must be true")
    return failures


def _validate_binding_gate_artifact(
    step: dict[str, Any],
    report_path: Path,
    *,
    runtime_path: Path | None,
    runtime_sha256: str | None,
) -> None:
    """Require a fresh passing binding-gate artifact for a passing command."""
    step["artifact"] = str(report_path)
    if step.get("status") != "pass":
        return
    if not report_path.is_file():
        step["status"] = "fail"
        step["error"] = f"binding gate did not write {report_path}"
        return
    try:
        report = _load_json(report_path)
    except (OSError, ValueError, TypeError) as exc:
        step["status"] = "fail"
        step["error"] = f"invalid binding gate report: {type(exc).__name__}: {exc}"
        return
    if not isinstance(report, dict):
        step["status"] = "fail"
        step["error"] = "binding gate report must be an object"
        return
    if report.get("schema_version") != 1:
        step["status"] = "fail"
        step["error"] = "binding gate report schema_version must be 1"
        return
    if report.get("status") != "pass":
        step["status"] = "fail"
        step["error"] = f"binding gate report status={report.get('status')!r}"
        return
    nested = report.get("report")
    if (
        not isinstance(nested, dict)
        or nested.get("schema_version") != 1
        or nested.get("status") != "ready"
        or not isinstance(nested.get("issues"), list)
        or nested["issues"]
    ):
        step["status"] = "fail"
        step["error"] = "binding gate report.valid/status must be ready with no issues"
        return
    expected_binding_root = (ROOT / "external" / "mmd-anim" / "bindings" / "python").resolve()
    reported_binding_root = report.get("binding_root")
    if (
        not isinstance(reported_binding_root, str)
        or not Path(reported_binding_root).is_dir()
        or Path(reported_binding_root).resolve() != expected_binding_root
    ):
        step["status"] = "fail"
        step["error"] = "binding gate report.binding_root mismatch"
        return
    frame = report.get("frame")
    if isinstance(frame, bool) or not isinstance(frame, (int, float)) or not math.isfinite(frame):
        step["status"] = "fail"
        step["error"] = "binding gate report.frame must be finite"
        return
    if not math.isclose(float(frame), 0.0, rel_tol=0.0, abs_tol=1.0e-6):
        step["status"] = "fail"
        step["error"] = "binding gate report.frame must be 0.0"
        return
    expected_model = (ROOT / "tests" / "data" / "mmt_test_model.pmx").resolve()
    expected_motion = (ROOT / "tests" / "data" / "mmt_test_model_test_motion.vmd").resolve()
    for field, expected in (("model", expected_model), ("motion", expected_motion)):
        value = report.get(field)
        if not isinstance(value, str) or not Path(value).is_file() or Path(value).resolve() != expected:
            step["status"] = "fail"
            step["error"] = f"binding gate report.{field} does not match expected fixture"
            return
    reported_runtime = report.get("runtime_library")
    if runtime_path is None or runtime_sha256 is None:
        step["status"] = "fail"
        step["error"] = "binding gate runtime identity is unavailable"
        return
    if (
        not isinstance(reported_runtime, str)
        or not Path(reported_runtime).is_file()
        or Path(reported_runtime).resolve() != runtime_path.resolve()
    ):
        step["status"] = "fail"
        step["error"] = "binding gate report.runtime_library mismatch"
        return
    actual_runtime_sha = _sha256(Path(reported_runtime))
    if actual_runtime_sha != runtime_sha256:
        step["status"] = "fail"
        step["error"] = "binding gate runtime SHA mismatch"
        return
    step["runtime_path"] = str(runtime_path)
    step["runtime_sha256"] = actual_runtime_sha
    step["artifact_sha256"] = _sha256(report_path)


def _mmd_anim_runtime_candidates() -> tuple[Path, ...]:
    """Return release FFI artifact candidates for supported host platforms."""
    runtime_dir = ROOT / "external" / "mmd-anim" / "target" / "release"
    suffixes = {
        "Windows": ("mmd_runtime_ffi.dll",),
        "Linux": ("libmmd_runtime_ffi.so",),
        "Darwin": ("libmmd_runtime_ffi.dylib",),
    }
    return tuple(runtime_dir / name for name in suffixes.get(platform.system(), ()))


def _mmd_anim_runtime_path() -> Path | None:
    """Resolve the release FFI artifact emitted by the existing ffi_build step."""
    return next((path for path in _mmd_anim_runtime_candidates() if path.is_file()), None)


def _mmd_anim_source_revision() -> str | None:
    """Read the checked-out mmd-anim source revision without changing it."""
    submodule = ROOT / "external" / "mmd-anim"
    try:
        completed = subprocess.run(
            ["git", "-C", str(submodule), "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    revision = completed.stdout.strip()
    return revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None


def _validate_ffi_build_step(step: dict[str, Any]) -> tuple[Path | None, str | None, str | None]:
    """Require the ffi_build Nox step and a current runtime artifact identity."""
    runtime_path = _mmd_anim_runtime_path()
    source_revision = _mmd_anim_source_revision()
    runtime_sha = _sha256(runtime_path) if runtime_path is not None else None
    step["runtime_path"] = str(runtime_path) if runtime_path is not None else None
    step["runtime_sha256"] = runtime_sha
    step["source_revision"] = source_revision
    if step.get("status") != "pass":
        return runtime_path, runtime_sha, source_revision
    if runtime_path is None:
        step["status"] = "fail"
        step["error"] = "ffi_build did not produce mmd_runtime_ffi release artifact"
        return None, None, source_revision
    if runtime_sha is None:
        step["status"] = "fail"
        step["error"] = "ffi_build runtime artifact SHA could not be computed"
        return None, None, source_revision
    if source_revision is None:
        step["status"] = "fail"
        step["error"] = "mmd-anim source revision is missing or malformed"
        return None, None, None
    step["artifact"] = str(runtime_path)
    step["artifact_sha256"] = runtime_sha
    return runtime_path, runtime_sha, source_revision


def _run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """Run one command and return bounded, deterministic evidence."""
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        status = "pass" if completed.returncode == 0 else "fail"
        return {
            "name": name,
            "status": status,
            "returncode": completed.returncode,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": _bounded(completed.stdout),
            "stderr": _bounded(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "fail",
            "returncode": None,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": _bounded(exc.stdout),
            "stderr": _bounded(exc.stderr),
            "error": f"timeout after {timeout:g}s",
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "fail",
            "returncode": None,
            "duration_sec": round(time.perf_counter() - started, 3),
            "command": command,
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _pytest_command() -> list[str]:
    """Use the current interpreter when pytest is available, otherwise uvx."""
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "pytest"]
    return [sys.executable, "-m", "pytest"]


def _not_run(name: str, reason: str, command: list[str] | None = None) -> dict[str, Any]:
    """Record an explicit omitted step."""
    return {
        "name": name,
        "status": "not_run",
        "reason": reason,
        "command": command or [],
    }


def _valid_model_data() -> dict[str, Any]:
    """Return the smallest payload accepted by both model validators."""
    return {
        "model_name": "v070-fail-fixture",
        "vertices": [
            {
                "position": [0.0, 0.0, 0.0],
                "normal": [0.0, 1.0, 0.0],
                "uv": [0.0, 0.0],
                "bone_indices": [0],
                "bone_weights": [1.0],
            }
        ],
        "faces": [[0, 0, 0]],
        "materials": [{"face_count": 3}],
        "bones": [{"name": "root", "parent_index": -1, "position": [0.0, 0.0, 0.0]}],
    }


class _SpyModelExporter:
    """Writer spy used to prove blocked model cases never reach the writer."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def export_pmx_model(self, path: str, data: Any) -> None:
        self.calls.append((path, data))
        Path(path).write_bytes(b"writer-output")

class _SpyVmdExporter:
    """Writer spy for VMD fail-closed and warning-ack cases."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def to_vmd_data(self, data: Any) -> Any:
        return data

    def export_vmd_animation(self, path: str, data: Any) -> None:
        self.calls.append((path, data))
        Path(path).write_bytes(b"writer-output")


def _report_evidence(directory: Path) -> dict[str, Any]:
    """Return report artifact presence and hashes for one fail fixture."""
    result: dict[str, Any] = {}
    for name in ("report.json", "report.md"):
        path = directory / name
        result[name] = {
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
    return result


def _run_fail_fixture_matrix(out_dir: Path) -> dict[str, Any]:
    """Run fatal/lossy, target-preservation, and warning-ack boundaries."""
    from tests.common.maya_stub import install_maya_stub

    install_maya_stub(profile="headless")
    from mmd_tools.actions.export_model_action import ExportModelAction, ExportModelRequest
    from mmd_tools.actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
    from mmd_tools.core.vmd_data import VmdData
    from mmd_tools.core.vmd_data.bone_frame import VmdBoneFrame

    fixtures: list[dict[str, Any]] = []
    report_paths: list[str] = []
    invalid_model = _valid_model_data()
    invalid_model["faces"] = [[0, 0]]
    for export_format in ("pmx",):
        case_dir = out_dir / f"invalid-{export_format}"
        case_dir.mkdir(parents=True, exist_ok=True)
        target = case_dir / f"existing.{export_format}"
        target.write_bytes(b"preserve-existing-target")
        before = target.read_bytes()
        writer = _SpyModelExporter()
        result = ExportModelAction(
            pmx_exporter=writer,
            output_verifier=None,
        ).execute(
            ExportModelRequest(
                str(target),
                {
                    "export_format": export_format,
                    "model_data": invalid_model,
                    "validation_report_dir": str(case_dir / "report"),
                    "validation_report_evidence": {
                        "gate": "V070-EXPORT-RELEASE-GATE-1",
                        "fixture": f"invalid-{export_format}",
                        "writer_expected": "not_called",
                        "target_expected": "preserved",
                    },
                },
            )
        )
        passed = (
            not result.succeeded
            and not writer.calls
            and target.read_bytes() == before
            and result.validation_report is not None
            and result.validation_report.is_blocking
        )
        fixtures.append(
            {
                "name": f"invalid_{export_format}",
                "status": "pass" if passed else "fail",
                "issue_codes": [issue.code for issue in (result.validation_report.issues if result.validation_report else ())],
                "writer_calls": len(writer.calls),
                "target_preserved": target.read_bytes() == before,
                "report": _report_evidence(case_dir / "report"),
            }
        )
        report_paths.append(str(case_dir / "report" / "report.json"))

    invalid_vmd = VmdData()
    frame = VmdBoneFrame()
    frame.bone_name = "root"
    frame.rotation = (0.0, 0.0, 0.0, 0.0)
    invalid_vmd.bone_frames.append(frame)
    vmd_dir = out_dir / "invalid-vmd"
    vmd_dir.mkdir(parents=True, exist_ok=True)
    vmd_target = vmd_dir / "existing.vmd"
    vmd_target.write_bytes(b"preserve-existing-target")
    vmd_before = vmd_target.read_bytes()
    vmd_writer = _SpyVmdExporter()
    vmd_result = ExportVmdAction(exporter=vmd_writer, output_verifier=None).execute(
        ExportVmdRequest(
            str(vmd_target),
            {
                "vmd_mode": "C",
                "validation_report_dir": str(vmd_dir / "report"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "invalid-vmd-quaternion",
                    "writer_expected": "not_called",
                    "target_expected": "preserved",
                },
            },
            animation_data=invalid_vmd,
        )
    )
    vmd_passed = (
        not vmd_result.succeeded
        and not vmd_writer.calls
        and vmd_target.read_bytes() == vmd_before
        and vmd_result.validation_report is not None
        and vmd_result.validation_report.is_blocking
    )
    fixtures.append(
        {
            "name": "invalid_vmd_quaternion",
            "status": "pass" if vmd_passed else "fail",
            "issue_codes": [issue.code for issue in (vmd_result.validation_report.issues if vmd_result.validation_report else ())],
            "writer_calls": len(vmd_writer.calls),
            "target_preserved": vmd_target.read_bytes() == vmd_before,
            "report": _report_evidence(vmd_dir / "report"),
        }
    )
    report_paths.append(str(vmd_dir / "report" / "report.json"))

    warning_dir = out_dir / "warning-ack"
    warning_dir.mkdir(parents=True, exist_ok=True)

    raw_provenance = {
        "raw_bone_interpolation_complete": True,
        "raw_bone_interpolation": [
            {
                "bone_name": "root",
                "frame_number": 0,
                "interpolation": [20] * 64,
            }
        ],
    }
    warning_writer = _SpyVmdExporter()
    warning_target = warning_dir / "warning.vmd"
    first = ExportVmdAction(
        exporter=warning_writer,
        output_verifier=None,
    ).execute(
        ExportVmdRequest(
            str(warning_target),
            {
                "vmd_mode": "C",
                "raw_provenance": raw_provenance,
                "validation_report_dir": str(warning_dir / "report-no-ack"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "warning-ack-boundary",
                    "ack_expected": "required",
                },
            },
            animation_data=VmdData(),
        )
    )
    second = ExportVmdAction(
        exporter=warning_writer,
        output_verifier=None,
    ).execute(
        ExportVmdRequest(
            str(warning_target),
            {
                "vmd_mode": "C",
                "raw_provenance": raw_provenance,
                "ack_warnings": True,
                "validation_report_dir": str(warning_dir / "report-ack"),
                "validation_report_evidence": {
                    "gate": "V070-EXPORT-RELEASE-GATE-1",
                    "fixture": "warning-ack-boundary",
                    "ack_expected": "accepted",
                },
            },
            animation_data=VmdData(),
        )
    )
    warning_passed = (
        not first.succeeded
        and first.validation_report is not None
        and first.validation_report.requires_warning_ack
        and first.validation_report.issues[0].code == "VMD_MODE_C_RAW_LOSS"
        and len(warning_writer.calls) == 1
        and second.succeeded
    )
    fixtures.append(
        {
            "name": "warning_ack_boundary",
            "status": "pass" if warning_passed else "fail",
            "first_succeeded": first.succeeded,
            "second_succeeded": second.succeeded,
            "writer_calls": len(warning_writer.calls),
            "first_issue_codes": [issue.code for issue in (first.validation_report.issues if first.validation_report else ())],
            "second_issue_codes": [issue.code for issue in (second.validation_report.issues if second.validation_report else ())],
            "reports": {
                "no_ack": _report_evidence(warning_dir / "report-no-ack"),
                "ack": _report_evidence(warning_dir / "report-ack"),
            },
        }
    )
    report_paths.extend(
        [
            str(warning_dir / "report-no-ack" / "report.json"),
            str(warning_dir / "report-ack" / "report.json"),
        ]
    )
    return {
        "status": "pass" if all(fixture["status"] == "pass" for fixture in fixtures) else "fail",
        "fixtures": fixtures,
        "report_paths": report_paths,
    }


def _load_json(path: Path) -> dict[str, Any]:
    """Read one required JSON gate artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_morph_oracle_case(case: Mapping[str, Any]) -> list[str]:
    """Require the structural evidence emitted by the all-type morph probe."""
    failures: list[str] = []
    coverage = case.get("morph_coverage")
    expected_fields = {name: list(fields) for name, fields in MORPH_ORACLE_FIELDS.items()}
    if not isinstance(coverage, dict):
        failures.append("pmx_morph.morph_coverage_missing")
    else:
        if coverage.get("verified_types") != list(MORPH_ORACLE_TYPES):
            failures.append("pmx_morph.morph_coverage.verified_types mismatch")
        if coverage.get("verified_fields") != expected_fields:
            failures.append("pmx_morph.morph_coverage.verified_fields mismatch")
        if coverage.get("excluded_boundaries") != list(MORPH_ORACLE_EXCLUSIONS):
            failures.append("pmx_morph.morph_coverage.excluded_boundaries mismatch")
        if coverage.get("source_oracle") != "PMX parser payload":
            failures.append("pmx_morph.morph_coverage.source_oracle mismatch")
        if coverage.get("scene_oracle") != "direct Maya DAG/network attributes and controller outputs":
            failures.append("pmx_morph.morph_coverage.scene_oracle mismatch")
        if coverage.get("visual_parity_claimed") is not False:
            failures.append("pmx_morph.morph_coverage.visual_parity_claimed must be false")

    oracle = case.get("morph_oracle")
    if not isinstance(oracle, dict):
        return failures + ["pmx_morph.morph_oracle_missing"]
    comparison = oracle.get("comparison")
    if not isinstance(comparison, dict):
        failures.append("pmx_morph.morph_oracle.comparison_missing")
    else:
        if comparison.get("status") != "pass":
            failures.append("pmx_morph.morph_oracle.comparison.status must be pass")
        if comparison.get("checked_types") != list(MORPH_ORACLE_TYPES):
            failures.append("pmx_morph.morph_oracle.comparison.checked_types mismatch")
        if comparison.get("boundaries") != list(MORPH_COMPARISON_BOUNDARIES):
            failures.append("pmx_morph.morph_oracle.comparison.boundaries mismatch")

    expected_types = set(MORPH_ORACLE_TYPES)
    all_indices: set[str] = set()
    vertex_indices: set[str] = set()
    boundary_payloads: dict[str, Mapping[str, Any]] = {}
    boundary_additional_uvs: dict[str, Any] = {}

    def _finite_number(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))

    def _validate_uv_payload(payload: Any, label: str) -> None:
        if not isinstance(payload, dict):
            failures.append(f"pmx_morph.morph_oracle.{label}.additional_uvs_missing")
            return
        if payload.get("channel_count") != 4:
            failures.append(f"pmx_morph.morph_oracle.{label}.additional_uvs.channel_count must be 4")
        vertices = payload.get("vertices")
        source_indices = payload.get("source_indices")
        if not isinstance(vertices, list) or not vertices:
            failures.append(f"pmx_morph.morph_oracle.{label}.additional_uvs.vertices missing")
            return
        if not isinstance(source_indices, list) or len(source_indices) != len(vertices):
            failures.append(f"pmx_morph.morph_oracle.{label}.additional_uvs.source_indices mismatch")
        elif any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in source_indices
        ):
            failures.append(f"pmx_morph.morph_oracle.{label}.additional_uvs.source_indices malformed")
        for vertex_index, channels in enumerate(vertices):
            if not isinstance(channels, list) or len(channels) != 4:
                failures.append(
                    f"pmx_morph.morph_oracle.{label}.additional_uvs.vertices[{vertex_index}] must contain 4 channels"
                )
                continue
            for channel_index, values in enumerate(channels):
                if not isinstance(values, list) or len(values) != 4 or any(
                    not _finite_number(value) for value in values
                ):
                    failures.append(
                        f"pmx_morph.morph_oracle.{label}.additional_uvs.vertices[{vertex_index}]"
                        f"[{channel_index}] malformed"
                    )

    for label in MORPH_ORACLE_BOUNDARIES:
        payload = oracle.get(label)
        if not isinstance(payload, dict):
            failures.append(f"pmx_morph.morph_oracle.{label}_missing")
            continue
        boundary_payloads[label] = payload
        boundary_additional_uvs[label] = payload.get("additional_uvs")
        _validate_uv_payload(boundary_additional_uvs[label], label)
        morphs = payload.get("morphs")
        if not isinstance(morphs, list) or not morphs:
            failures.append(f"pmx_morph.morph_oracle.{label}.morphs_missing")
            continue
        indices: set[str] = set()
        types: set[str] = set()
        for entry_index, entry in enumerate(morphs):
            if not isinstance(entry, dict):
                failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}] malformed")
                continue
            morph_index = entry.get("index")
            morph_type = entry.get("type")
            if isinstance(morph_index, bool) or not isinstance(morph_index, int) or morph_index < 0:
                failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].index malformed")
                continue
            if not isinstance(entry.get("name"), str) or not entry["name"]:
                failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].name malformed")
            if morph_type not in expected_types:
                failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].type malformed")
                continue
            key = str(morph_index)
            if key in indices:
                failures.append(f"pmx_morph.morph_oracle.{label} duplicate morph index {morph_index}")
            indices.add(key)
            types.add(morph_type)
            if morph_type == "vertex":
                vertex_indices.add(key)
            else:
                if not isinstance(entry.get("name_en"), str):
                    failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].name_en malformed")
                panel = entry.get("panel")
                if isinstance(panel, bool) or not isinstance(panel, int):
                    failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].panel malformed")
                if not isinstance(entry.get("offsets"), list):
                    failures.append(f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}].offsets malformed")
                elif morph_type in {
                    "uv",
                    "additional_uv1",
                    "additional_uv2",
                    "additional_uv3",
                    "additional_uv4",
                }:
                    for offset_index, offset in enumerate(entry["offsets"]):
                        if not isinstance(offset, dict):
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}] malformed"
                            )
                            continue
                        vertex_index = offset.get("vertex_index")
                        uv_offset = offset.get("uv_offset")
                        if isinstance(vertex_index, bool) or not isinstance(vertex_index, int) or vertex_index < 0:
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}].vertex_index malformed"
                            )
                        if not isinstance(uv_offset, list) or len(uv_offset) != 4 or any(
                            not _finite_number(value) for value in uv_offset
                        ):
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}].uv_offset malformed"
                            )
                elif morph_type == "group":
                    for offset_index, offset in enumerate(entry["offsets"]):
                        if not isinstance(offset, dict):
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}] malformed"
                            )
                            continue
                        target_index = offset.get("morph_index")
                        rate_key = "morph_rate"
                        if isinstance(target_index, bool) or not isinstance(target_index, int) or target_index < 0:
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}].morph_index malformed"
                            )
                        if not _finite_number(offset.get(rate_key)):
                            failures.append(
                                f"pmx_morph.morph_oracle.{label}.morphs[{entry_index}]"
                                f".offsets[{offset_index}].{rate_key} malformed"
                            )
        if types != expected_types:
            failures.append(
                f"pmx_morph.morph_oracle.{label}.types mismatch: expected {sorted(expected_types)}, "
                f"actual {sorted(types)}"
            )
        if label in {"source", "exported_file"}:
            all_indices = indices
            vertex_offsets = payload.get("vertex_offsets")
            if not isinstance(vertex_offsets, dict):
                failures.append(f"pmx_morph.morph_oracle.{label}.vertex_offsets_missing")
            elif set(vertex_offsets) != vertex_indices:
                failures.append(f"pmx_morph.morph_oracle.{label}.vertex_offsets mismatch")
        else:
            if indices != all_indices:
                failures.append("pmx_morph.morph_oracle source/fresh morph indices mismatch")
            vertex_meshes = payload.get("vertex_meshes")
            if not isinstance(vertex_meshes, list) or not vertex_meshes:
                failures.append("pmx_morph.morph_oracle.fresh_import.vertex_meshes_missing")
            runtime = payload.get("vertex_runtime_deltas")
            if not isinstance(runtime, dict) or set(runtime) != vertex_indices:
                failures.append("pmx_morph.morph_oracle.fresh_import.vertex_runtime_deltas mismatch")
            elif any(not isinstance(value, list) or not value for value in runtime.values()):
                failures.append("pmx_morph.morph_oracle.fresh_import.vertex_runtime_deltas malformed")

        outputs = payload.get("controller_outputs")
        if not isinstance(outputs, dict) or set(outputs) != indices:
            failures.append(f"pmx_morph.morph_oracle.{label}.controller_outputs mismatch")
        elif morphs:
            for key, values in outputs.items():
                if not isinstance(values, list) or len(values) != len(morphs):
                    failures.append(f"pmx_morph.morph_oracle.{label}.controller_outputs[{key}] malformed")
                elif any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in values
                ):
                    failures.append(f"pmx_morph.morph_oracle.{label}.controller_outputs[{key}] non-finite")
        unsupported = payload.get("unsupported_types")
        if unsupported != []:
            failures.append(f"pmx_morph.morph_oracle.{label}.unsupported_types must be empty")

    if boundary_payloads:
        source_indices = set()
        source_payload = boundary_payloads.get("source")
        if isinstance(source_payload, dict):
            source_indices = {
                str(entry.get("index"))
                for entry in source_payload.get("morphs", [])
                if isinstance(entry, dict) and isinstance(entry.get("index"), int)
            }
        for label in MORPH_ORACLE_BOUNDARIES:
            payload = boundary_payloads.get(label)
            if payload is None:
                continue
            morphs = payload.get("morphs")
            if source_payload is not None and morphs != source_payload.get("morphs"):
                failures.append(f"pmx_morph.morph_oracle.source/{label}.morph_payload mismatch")
            if source_indices and {
                str(entry.get("index"))
                for entry in morphs or []
                if isinstance(entry, dict) and isinstance(entry.get("index"), int)
            } != source_indices:
                failures.append(f"pmx_morph.morph_oracle.source/{label}.morph_indices mismatch")
        if isinstance(source_payload, dict):
            source_vertex_offsets = source_payload.get("vertex_offsets")
            exported_payload = boundary_payloads.get("exported_file")
            if (
                isinstance(exported_payload, dict)
                and exported_payload.get("vertex_offsets") != source_vertex_offsets
            ):
                failures.append("pmx_morph.morph_oracle.source/exported_file.vertex_offsets mismatch")
        source_uvs = boundary_additional_uvs.get("source")
        if source_uvs is not None:
            for label in MORPH_ORACLE_BOUNDARIES:
                if label in boundary_additional_uvs and boundary_additional_uvs[label] != source_uvs:
                    failures.append(f"pmx_morph.morph_oracle.source/{label}.additional_uvs mismatch")
    return failures


def _validate_bone_semantics_case(case: Mapping[str, Any]) -> list[str]:
    """Require exact PMX->Maya->PMX bone semantic evidence."""
    failures: list[str] = []
    coverage = case.get("bone_semantics_coverage")
    if not isinstance(coverage, dict):
        failures.append("pmx_bone_semantics.bone_semantics_coverage_missing")
    else:
        if coverage.get("verified_fields") != list(BONE_SEMANTICS_FIELDS):
            failures.append("pmx_bone_semantics.bone_semantics_coverage.verified_fields mismatch")
        if coverage.get("source_oracle") != "PMX parser payload":
            failures.append("pmx_bone_semantics.bone_semantics_coverage.source_oracle mismatch")
        if coverage.get("maya_oracle") != "direct Maya bone metadata attributes":
            failures.append("pmx_bone_semantics.bone_semantics_coverage.maya_oracle mismatch")

    semantics = case.get("bone_semantics")
    if not isinstance(semantics, dict):
        return failures + ["pmx_bone_semantics.bone_semantics_missing"]
    comparison = semantics.get("comparison")
    if not isinstance(comparison, dict):
        failures.append("pmx_bone_semantics.bone_semantics.comparison_missing")
    else:
        if comparison.get("status") != "pass":
            failures.append("pmx_bone_semantics.bone_semantics.comparison.status must be pass")
        if comparison.get("boundaries") != list(BONE_SEMANTICS_COMPARISON_BOUNDARIES):
            failures.append("pmx_bone_semantics.bone_semantics.comparison.boundaries mismatch")

    boundary_payloads: dict[str, Mapping[str, Any]] = {}
    source_bones: list[Any] | None = None
    for label in BONE_SEMANTICS_BOUNDARIES:
        payload = semantics.get(label)
        if not isinstance(payload, dict):
            failures.append(f"pmx_bone_semantics.bone_semantics.{label}_missing")
            continue
        boundary_payloads[label] = payload
        bones = payload.get("bones")
        if not isinstance(bones, list) or not bones:
            failures.append(f"pmx_bone_semantics.bone_semantics.{label}.bones_missing")
            continue
        seen_indices: set[str] = set()
        for bone_index, bone in enumerate(bones):
            if not isinstance(bone, dict):
                failures.append(f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}] malformed")
                continue
            missing = [field for field in BONE_SEMANTICS_FIELDS if field not in bone]
            if missing:
                failures.append(
                    f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}] missing {missing}"
                )
                continue
            index = bone.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                failures.append(
                    f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}].index malformed"
                )
            else:
                key = str(index)
                if key in seen_indices:
                    failures.append(f"pmx_bone_semantics.bone_semantics.{label} duplicate bone index {index}")
                seen_indices.add(key)
            for field in ("name", "name_en"):
                if not isinstance(bone.get(field), str):
                    failures.append(
                        f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}].{field} malformed"
                    )
            if isinstance(bone.get("ik_links"), list):
                for link_index, link in enumerate(bone["ik_links"]):
                    if not isinstance(link, dict):
                        failures.append(
                            f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}]"
                            f".ik_links[{link_index}] malformed"
                        )
                        continue
                    for field in ("bone", "limit_enabled", "lower_limit", "upper_limit"):
                        if field not in link:
                            failures.append(
                                f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}]"
                                f".ik_links[{link_index}] missing {field}"
                            )
            elif bone.get("ik_links") is not None:
                failures.append(
                    f"pmx_bone_semantics.bone_semantics.{label}.bones[{bone_index}].ik_links malformed"
                )
        if label == "source":
            source_bones = bones
        elif source_bones is not None and _normalize_bone_semantics_value(bones) != _normalize_bone_semantics_value(source_bones):
            failures.append(f"pmx_bone_semantics.bone_semantics.source/{label}.bones mismatch")

    if source_bones is None:
        return failures
    return failures


def _validate_vmd_model_tracks_case(case: Mapping[str, Any]) -> list[str]:
    """Require field-level Mode C bone/morph/IK model-track evidence."""
    failures: list[str] = []
    coverage = case.get("track_coverage")
    if not isinstance(coverage, dict):
        return ["vmd_model_tracks.track_coverage_missing"]
    if coverage.get("tracks") != list(VMD_MODEL_TRACKS):
        failures.append("vmd_model_tracks.track_coverage.tracks mismatch")
    if coverage.get("checked_frames") != [0, 6, 10, 12, 20]:
        failures.append("vmd_model_tracks.track_coverage.checked_frames mismatch")
    if coverage.get("camera_light_shadow_claimed") is not False:
        failures.append("vmd_model_tracks.camera_light_shadow_claimed must be false")
    if coverage.get("visual_parity_claimed") is not False:
        failures.append("vmd_model_tracks.visual_parity_claimed must be false")
    for boundary in ("source_counts", "exported_counts"):
        counts = coverage.get(boundary)
        if not isinstance(counts, dict):
            failures.append(f"vmd_model_tracks.track_coverage.{boundary}_missing")
            continue
        for field in ("bone_frames", "morph_frames", "ik_show_hide_frames"):
            value = counts.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                failures.append(f"vmd_model_tracks.track_coverage.{boundary}.{field} must be positive")
    for field in ("bone_track_names", "morph_track_names", "ik_track_names"):
        values = coverage.get(field)
        if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
            failures.append(f"vmd_model_tracks.track_coverage.{field} missing")

    tracks = case.get("model_tracks")
    if not isinstance(tracks, dict):
        return failures + ["vmd_model_tracks.model_tracks_missing"]
    comparison = tracks.get("comparison")
    if not isinstance(comparison, dict):
        failures.append("vmd_model_tracks.model_tracks.comparison_missing")
    else:
        if comparison.get("status") != "pass":
            failures.append("vmd_model_tracks.model_tracks.comparison.status must be pass")
        if comparison.get("boundaries") != list(VMD_MODEL_TRACK_COMPARISON_BOUNDARIES):
            failures.append("vmd_model_tracks.model_tracks.comparison.boundaries mismatch")
        if comparison.get("checked_frames") != [0, 6, 10, 12, 20]:
            failures.append("vmd_model_tracks.model_tracks.comparison.checked_frames mismatch")
        if comparison.get("raw_key_interpolation_preserved") is not False:
            failures.append("vmd_model_tracks raw key preservation must be false")

    for boundary in ("source", "source_import", "exported_file", "fresh_import"):
        payload = tracks.get(boundary)
        if not isinstance(payload, dict):
            failures.append(f"vmd_model_tracks.model_tracks.{boundary}_missing")
            continue
        if boundary in ("source", "exported_file"):
            for field in ("bone_track_names", "morph_track_names", "bone_frame_count", "morph_frame_count", "ik_show_hide_frame_count"):
                if field not in payload:
                    failures.append(f"vmd_model_tracks.model_tracks.{boundary}.{field}_missing")
            for field in ("bone_values", "morph_values", "ik_values"):
                if not isinstance(payload.get(field), dict) or not payload[field]:
                    failures.append(f"vmd_model_tracks.model_tracks.{boundary}.{field}_missing")
        else:
            if not isinstance(payload.get("bone_values"), dict) or not payload["bone_values"]:
                failures.append(f"vmd_model_tracks.model_tracks.{boundary}.bone_values_missing")
            if not isinstance(payload.get("morph_values"), dict) or not payload["morph_values"]:
                failures.append(f"vmd_model_tracks.model_tracks.{boundary}.morph_values_missing")
            if not isinstance(payload.get("ik_values"), dict) or not payload["ik_values"]:
                failures.append(f"vmd_model_tracks.model_tracks.{boundary}.ik_values_missing")
    for boundary in ("source_import", "fresh_import"):
        payload = tracks.get(boundary)
        if not isinstance(payload, dict):
            continue
        morph_values = payload.get("morph_values", {})
        flattened_morph = [float(value) for values in morph_values.values() for value in values.values()]
        if not any(abs(value) > 1.0e-6 for value in flattened_morph):
            failures.append(f"vmd_model_tracks.model_tracks.{boundary}.morph_values must include nonzero weight")
        ik_values = payload.get("ik_values", {})
        state_tuples = {tuple(sorted(states.items())) for states in ik_values.values() if isinstance(states, dict)}
        if len(state_tuples) < 2:
            failures.append(f"vmd_model_tracks.model_tracks.{boundary}.ik_values must include a state change")
    return failures


def _validate_vmd_mode_a_case(case: Mapping[str, Any]) -> list[str]:
    """Require raw-preserving Mode A bone/provenance/pose evidence."""
    failures: list[str] = []

    def _finite(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))

    def _sha(value: Any) -> bool:
        return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)

    def _records(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not value:
            failures.append(f"vmd_mode_a.bone_oracle.{label}_missing")
            return []
        normalized = []
        seen = set()
        for index, record in enumerate(value):
            if not isinstance(record, dict):
                failures.append(f"vmd_mode_a.bone_oracle.{label}[{index}] malformed")
                continue
            name = record.get("bone_name")
            frame = record.get("frame_number")
            position = record.get("position")
            rotation = record.get("rotation")
            interpolation = record.get("interpolation")
            key = (name, frame)
            valid = (
                isinstance(name, str)
                and bool(name)
                and isinstance(frame, int)
                and not isinstance(frame, bool)
                and frame >= 0
                and isinstance(position, list)
                and len(position) == 3
                and all(_finite(item) for item in position)
                and isinstance(rotation, list)
                and len(rotation) == 4
                and all(_finite(item) for item in rotation)
                and math.sqrt(sum(float(item) ** 2 for item in rotation)) > 1.0e-12
                and isinstance(interpolation, list)
                and len(interpolation) == 64
                and all(
                    isinstance(byte, int) and not isinstance(byte, bool) and 0 <= byte <= 255
                    for byte in interpolation
                )
                and key not in seen
            )
            if not valid:
                failures.append(f"vmd_mode_a.bone_oracle.{label}[{index}] malformed")
                continue
            seen.add(key)
            normalized.append(record)
        return normalized

    if case.get("mode") != "A":
        failures.append("vmd_mode_a.mode must be A")
    if case.get("raw_provenance_required") is not True:
        failures.append("vmd_mode_a.raw_provenance_required must be true")
    identity = case.get("source_identity")
    if not isinstance(identity, dict):
        failures.append("vmd_mode_a.source_identity_missing")
        identity = {}
    for field in ("pmx_sha256", "source_vmd_sha256", "exported_vmd_sha256"):
        if not _sha(identity.get(field)):
            failures.append(f"vmd_mode_a.source_identity.{field} malformed")

    # Recompute each file digest at gate time; report-provided strings alone
    # must not be sufficient evidence because a path can be replaced after the
    # probe has completed.
    for path_field, digest_field in (
        ("source_model", "pmx_sha256"),
        ("source", "source_vmd_sha256"),
        ("output", "exported_vmd_sha256"),
    ):
        raw_path = case.get(path_field)
        if not isinstance(raw_path, str) or not raw_path:
            failures.append(f"vmd_mode_a.{path_field} missing")
            continue
        path = Path(raw_path)
        if not path.is_file():
            failures.append(f"vmd_mode_a.{path_field} missing file")
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            failures.append(f"vmd_mode_a.{path_field} unreadable: {exc}")
            continue
        if digest != identity.get(digest_field):
            failures.append(f"vmd_mode_a.{path_field} SHA mismatch")

    fixture = case.get("fixture")
    if not isinstance(fixture, dict):
        failures.append("vmd_mode_a.fixture_missing")
    else:
        if fixture.get("runtime_copy") is not True:
            failures.append("vmd_mode_a.fixture.runtime_copy must be true")
        if fixture.get("nonzero_translation_verified") is not True:
            failures.append("vmd_mode_a.fixture.nonzero_translation_verified must be true")
        if fixture.get("nonzero_rotation_verified") is not True:
            failures.append("vmd_mode_a.fixture.nonzero_rotation_verified must be true")

    safety = case.get("output_safety")
    if not isinstance(safety, dict):
        failures.append("vmd_mode_a.output_safety_missing")
    else:
        for field in ("target_existed_before", "target_exists_after", "created", "overwritten", "preserved", "writer_called"):
            if not isinstance(safety.get(field), bool):
                failures.append(f"vmd_mode_a.output_safety.{field} must be boolean")
        if safety.get("target_exists_after") is not True:
            failures.append("vmd_mode_a.output_safety.target_exists_after must be true")
        if safety.get("writer_called") is not True:
            failures.append("vmd_mode_a.output_safety.writer_called must be true")
        if safety.get("preserved") is not True:
            failures.append("vmd_mode_a.output_safety.preserved must be true")

    counts = case.get("parsed_counts")
    if not isinstance(counts, dict):
        failures.append("vmd_mode_a.parsed_counts_missing")
    else:
        value = counts.get("bone_frames")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            failures.append("vmd_mode_a.parsed_counts.bone_frames must be positive")
        for field in ("morph_frames", "camera_frames", "light_frames", "shadow_frames"):
            if counts.get(field) != 0:
                failures.append(f"vmd_mode_a.parsed_counts.{field} must be zero")

    oracle = case.get("bone_oracle")
    if not isinstance(oracle, dict):
        failures.append("vmd_mode_a.bone_oracle_missing")
        oracle = {}
    oracle_records = {
        boundary: _records(oracle.get(boundary), boundary)
        for boundary in VMD_MODE_A_COMPARISON_BOUNDARIES
    }
    source_records = oracle_records["source"]
    if source_records:
        for boundary in ("source_import", "exported_file", "fresh_import"):
            if oracle_records[boundary] != source_records:
                failures.append(f"vmd_mode_a.bone_oracle.{boundary} differs from source")
        if not any(
            any(abs(float(value)) > 1.0e-6 for value in record["position"])
            for record in source_records
        ):
            failures.append("vmd_mode_a.bone_oracle.source requires nonzero position")

        def _is_identity_quaternion(values: list[Any]) -> bool:
            norm = math.sqrt(sum(float(value) ** 2 for value in values))
            if not math.isfinite(norm) or norm <= 1.0e-12:
                return False
            normalized = [float(value) / norm for value in values]
            return (
                abs(normalized[0]) <= 1.0e-6
                and abs(normalized[1]) <= 1.0e-6
                and abs(normalized[2]) <= 1.0e-6
                and abs(abs(normalized[3]) - 1.0) <= 1.0e-6
            )

        if not any(not _is_identity_quaternion(record["rotation"]) for record in source_records):
            failures.append("vmd_mode_a.bone_oracle.source requires nonidentity quaternion")
    comparison = oracle.get("comparison")
    if not isinstance(comparison, dict):
        failures.append("vmd_mode_a.bone_oracle.comparison_missing")
    else:
        if comparison.get("status") != "pass":
            failures.append("vmd_mode_a.bone_oracle.comparison.status must be pass")
        if comparison.get("boundaries") != list(VMD_MODE_A_COMPARISON_BOUNDARIES):
            failures.append("vmd_mode_a.bone_oracle.comparison.boundaries mismatch")
        if comparison.get("checked_frames") != list(VMD_MODE_A_POSE_FRAMES):
            failures.append("vmd_mode_a.bone_oracle.comparison.checked_frames mismatch")
        if comparison.get("raw_interpolation_preserved") is not True:
            failures.append("vmd_mode_a.raw_interpolation_preserved must be true")

    provenance = case.get("provenance")
    if not isinstance(provenance, dict):
        failures.append("vmd_mode_a.provenance_missing")
        provenance = {}
    for boundary, expected_sha in (
        ("source_import", identity.get("source_vmd_sha256")),
        ("fresh_import", identity.get("exported_vmd_sha256")),
    ):
        payload = provenance.get(boundary)
        if not isinstance(payload, dict):
            failures.append(f"vmd_mode_a.provenance.{boundary}_missing")
            continue
        if payload.get("status") != "success":
            failures.append(f"vmd_mode_a.provenance.{boundary}.status must be success")
        if payload.get("evaluation_mode") != "authored_sparse_keys":
            failures.append(
                f"vmd_mode_a.provenance.{boundary}.evaluation_mode must be authored_sparse_keys"
            )
        for field in ("raw_bone_interpolation_complete", "raw_bone_transform_complete"):
            if payload.get(field) is not True:
                failures.append(f"vmd_mode_a.provenance.{boundary}.{field} must be true")
        if payload.get("fallback") != "none":
            failures.append(f"vmd_mode_a.provenance.{boundary}.fallback must be none")
        if payload.get("pmx_sha256") != identity.get("pmx_sha256"):
            failures.append(f"vmd_mode_a.provenance.{boundary}.pmx_sha256 mismatch")
        if payload.get("raw_vmd_sha256") != expected_sha:
            failures.append(f"vmd_mode_a.provenance.{boundary}.raw_vmd_sha256 mismatch")
        if payload.get("raw_bone_key_count") != len(source_records):
            failures.append(f"vmd_mode_a.provenance.{boundary}.raw_bone_key_count mismatch")
        if _records(payload.get("raw_bone_interpolation"), f"provenance.{boundary}") != source_records:
            failures.append(f"vmd_mode_a.provenance.{boundary}.raw_bone_interpolation mismatch")
    provenance_comparison = provenance.get("comparison")
    if not isinstance(provenance_comparison, dict):
        failures.append("vmd_mode_a.provenance.comparison_missing")
    else:
        if provenance_comparison.get("status") != "pass":
            failures.append("vmd_mode_a.provenance.comparison.status must be pass")
        if provenance_comparison.get("boundaries") != ["source_import", "fresh_import"]:
            failures.append("vmd_mode_a.provenance.comparison.boundaries mismatch")
        for field in ("pmx_identity_preserved", "raw_vmd_identity_checked", "raw_blocks_complete"):
            if provenance_comparison.get(field) is not True:
                failures.append(f"vmd_mode_a.provenance.comparison.{field} must be true")

    pose = case.get("pose")
    if not isinstance(pose, dict):
        failures.append("vmd_mode_a.pose_missing")
    else:
        pose_boundaries = {}
        for boundary in ("source_import", "fresh_import"):
            payload = pose.get(boundary)
            if not isinstance(payload, dict):
                failures.append(f"vmd_mode_a.pose.{boundary}_missing")
                continue
            frames = payload.get("frames")
            if not isinstance(frames, dict) or set(frames) != {str(frame) for frame in VMD_MODE_A_POSE_FRAMES}:
                failures.append(f"vmd_mode_a.pose.{boundary}.frames mismatch")
                continue
            pose_boundaries[boundary] = payload
            if isinstance(payload.get("joint_count"), bool) or not isinstance(payload.get("joint_count"), int) or payload["joint_count"] <= 0:
                failures.append(f"vmd_mode_a.pose.{boundary}.joint_count malformed")
            for frame, joints in frames.items():
                if not isinstance(joints, list):
                    failures.append(f"vmd_mode_a.pose.{boundary}.frames[{frame}] malformed")
                    continue
                for joint in joints:
                    if (
                        not isinstance(joint, dict)
                        or not isinstance(joint.get("name"), str)
                        or not isinstance(joint.get("index"), int)
                        or not isinstance(joint.get("world_matrix"), list)
                        or len(joint["world_matrix"]) != 16
                        or not all(_finite(item) for item in joint["world_matrix"])
                    ):
                        failures.append(f"vmd_mode_a.pose.{boundary}.frames[{frame}] malformed")
        if pose_boundaries.get("source_import") and pose_boundaries.get("fresh_import"):
            source_frames = pose_boundaries["source_import"]["frames"]
            fresh_frames = pose_boundaries["fresh_import"]["frames"]
            if set(source_frames) == set(fresh_frames):
                for frame in source_frames:
                    source_joints = source_frames[frame]
                    fresh_joints = fresh_frames[frame]
                    if len(source_joints) != len(fresh_joints):
                        failures.append(f"vmd_mode_a.pose frame {frame} joint count mismatch")
                        continue
                    for index, (source_joint, fresh_joint) in enumerate(zip(source_joints, fresh_joints)):
                        if source_joint.get("index") != fresh_joint.get("index") or source_joint.get("name") != fresh_joint.get("name"):
                            failures.append(f"vmd_mode_a.pose frame {frame} joint {index} identity mismatch")
                            continue
                        if any(
                            abs(float(left) - float(right)) > 1.0e-4
                            for left, right in zip(
                                source_joint.get("world_matrix", []),
                                fresh_joint.get("world_matrix", []),
                            )
                        ):
                            failures.append(f"vmd_mode_a.pose frame {frame} joint {index} worldMatrix mismatch")
        comparison = pose.get("comparison")
        if not isinstance(comparison, dict):
            failures.append("vmd_mode_a.pose.comparison_missing")
        else:
            if comparison.get("status") != "pass":
                failures.append("vmd_mode_a.pose.comparison.status must be pass")
            if comparison.get("boundaries") != ["source_import", "fresh_import"]:
                failures.append("vmd_mode_a.pose.comparison.boundaries mismatch")
            if comparison.get("checked_frames") != list(VMD_MODE_A_POSE_FRAMES):
                failures.append("vmd_mode_a.pose.comparison.checked_frames mismatch")
    collection = case.get("collection")
    if not isinstance(collection, dict) or collection.get("edited_raw_block_policy") != "fail-closed":
        failures.append("vmd_mode_a.collection.edited_raw_block_policy must be fail-closed")
    edited_negative = case.get("edited_raw_negative")
    if not isinstance(edited_negative, dict):
        failures.append("vmd_mode_a.edited_raw_negative_missing")
    else:
        if edited_negative.get("status") != "pass":
            failures.append("vmd_mode_a.edited_raw_negative.status must be pass")
        expected_code = "VMD_RAW_PROVENANCE_MISMATCH"
        if edited_negative.get("expected_issue_code") != expected_code:
            failures.append("vmd_mode_a.edited_raw_negative.expected_issue_code mismatch")
        issue_codes = edited_negative.get("issue_codes")
        if not isinstance(issue_codes, list) or expected_code not in issue_codes:
            failures.append("vmd_mode_a.edited_raw_negative.issue_codes missing mismatch")
        if edited_negative.get("writer_called") is not False:
            failures.append("vmd_mode_a.edited_raw_negative.writer_called must be false")
        if edited_negative.get("sentinel_preserved") is not True:
            failures.append("vmd_mode_a.edited_raw_negative.sentinel_preserved must be true")
        before = edited_negative.get("sentinel_sha256_before")
        after = edited_negative.get("sentinel_sha256_after")
        if not _sha(before) or not _sha(after) or before != after:
            failures.append("vmd_mode_a.edited_raw_negative sentinel SHA mismatch")
        sentinel_output = edited_negative.get("output")
        if not isinstance(sentinel_output, str) or not sentinel_output:
            failures.append("vmd_mode_a.edited_raw_negative.output missing")
        else:
            sentinel_path = Path(sentinel_output)
            if not sentinel_path.is_file():
                failures.append("vmd_mode_a.edited_raw_negative.output missing file")
            else:
                try:
                    sentinel_digest = hashlib.sha256(sentinel_path.read_bytes()).hexdigest()
                except OSError as exc:
                    failures.append(f"vmd_mode_a.edited_raw_negative.output unreadable: {exc}")
                else:
                    if sentinel_digest != before or sentinel_digest != after:
                        failures.append("vmd_mode_a.edited_raw_negative.output SHA mismatch")
        for field in ("translation_edited", "rotation_edited"):
            if edited_negative.get(field) is not True:
                failures.append(f"vmd_mode_a.edited_raw_negative.{field} must be true")
    return failures


def _validate_vmd_camera_light_case(case: Mapping[str, Any]) -> list[str]:
    """Require standalone camera/light Mode C field-level evidence."""
    failures: list[str] = []
    coverage = case.get("track_coverage")
    if not isinstance(coverage, dict):
        return ["vmd_camera_light.track_coverage_missing"]
    if coverage.get("tracks") != list(VMD_CAMERA_LIGHT_TRACKS):
        failures.append("vmd_camera_light.track_coverage.tracks mismatch")
    if coverage.get("checked_frames") != list(VMD_CAMERA_LIGHT_DENSE_FRAMES):
        failures.append("vmd_camera_light.track_coverage.checked_frames mismatch")
    if coverage.get("visual_parity_claimed") is not False:
        failures.append("vmd_camera_light.visual_parity_claimed must be false")
    for field in ("bone_frames", "morph_frames", "ik_show_hide_frames", "shadow_frames"):
        if coverage.get(field) != 0:
            failures.append(f"vmd_camera_light.track_coverage.{field} must be zero")
    for boundary in ("source_counts", "exported_counts"):
        counts = coverage.get(boundary)
        if not isinstance(counts, dict):
            failures.append(f"vmd_camera_light.track_coverage.{boundary}_missing")
            continue
        for field in ("camera_frames", "light_frames"):
            value = counts.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                failures.append(f"vmd_camera_light.track_coverage.{boundary}.{field} must be positive")

    normalization = case.get("normalization")
    if not isinstance(normalization, dict):
        failures.append("vmd_camera_light.normalization_missing")
    else:
        excluded_shadow_frames = normalization.get("excluded_shadow_frames")
        if (
            isinstance(excluded_shadow_frames, bool)
            or not isinstance(excluded_shadow_frames, int)
            or excluded_shadow_frames <= 0
        ):
            failures.append("vmd_camera_light.normalization.excluded_shadow_frames must be positive")
        if normalization.get("shadow_support_claimed") is not False:
            failures.append("vmd_camera_light.normalization.shadow_support_claimed must be false")
    if case.get("mode_c_warning_acknowledged") is not True:
        failures.append("vmd_camera_light.mode_c_warning_acknowledged must be true")

    tracks = case.get("camera_light")
    if not isinstance(tracks, dict):
        return failures + ["vmd_camera_light.camera_light_missing"]
    comparison = tracks.get("comparison")
    if not isinstance(comparison, dict):
        failures.append("vmd_camera_light.camera_light.comparison_missing")
    else:
        if comparison.get("status") != "pass":
            failures.append("vmd_camera_light.camera_light.comparison.status must be pass")
        if comparison.get("boundaries") != list(VMD_CAMERA_LIGHT_COMPARISON_BOUNDARIES):
            failures.append("vmd_camera_light.camera_light.comparison.boundaries mismatch")
        if comparison.get("checked_frames") != list(VMD_CAMERA_LIGHT_KEY_FRAMES):
            failures.append("vmd_camera_light.camera_light.comparison.checked_frames mismatch")
        if comparison.get("dense_checked_frames") != list(VMD_CAMERA_LIGHT_DENSE_FRAMES):
            failures.append("vmd_camera_light.camera_light.comparison.dense_checked_frames mismatch")
        if comparison.get("dense_status") != "pass":
            failures.append("vmd_camera_light.camera_light.comparison.dense_status must be pass")
    interpolation = tracks.get("interpolation")
    if not isinstance(interpolation, dict):
        failures.append("vmd_camera_light.camera_light.interpolation_missing")
    else:
        if not isinstance(interpolation.get("source"), dict) or not interpolation["source"]:
            failures.append("vmd_camera_light.camera_light.interpolation.source_missing")
        if not isinstance(interpolation.get("exported_file"), dict) or not interpolation["exported_file"]:
            failures.append("vmd_camera_light.camera_light.interpolation.exported_file_missing")
        if interpolation.get("raw_preserved") is not False:
            failures.append("vmd_camera_light.camera_light.interpolation.raw_preserved must be false")
        if interpolation.get("mode_c_normalized") is not True:
            failures.append("vmd_camera_light.camera_light.interpolation.mode_c_normalized must be true")
        if interpolation.get("canonical_expected") != list(VMD_CAMERA_LIGHT_CANONICAL_INTERPOLATION):
            failures.append("vmd_camera_light.camera_light.interpolation.canonical_expected mismatch")
        if interpolation.get("canonical_length") != len(VMD_CAMERA_LIGHT_CANONICAL_INTERPOLATION):
            failures.append("vmd_camera_light.camera_light.interpolation.canonical_length must be 24")
        if interpolation.get("canonical_exported") is not True:
            failures.append("vmd_camera_light.camera_light.interpolation.canonical_exported must be true")
        for boundary in ("source", "exported_file"):
            values = interpolation.get(boundary)
            if not isinstance(values, dict) or not values:
                continue
            if set(values) != {str(frame) for frame in VMD_CAMERA_LIGHT_KEY_FRAMES}:
                failures.append(
                    f"vmd_camera_light.camera_light.interpolation.{boundary}_frames mismatch"
                )
            for frame, raw in values.items():
                if (
                    not isinstance(raw, list)
                    or len(raw) != len(VMD_CAMERA_LIGHT_CANONICAL_INTERPOLATION)
                    or any(
                        isinstance(byte, bool)
                        or not isinstance(byte, int)
                        or byte < 0
                        or byte > 255
                        for byte in raw
                    )
                ):
                    failures.append(
                        f"vmd_camera_light.camera_light.interpolation.{boundary}.{frame} must contain 24 bytes"
                    )
                if boundary == "exported_file" and raw != list(VMD_CAMERA_LIGHT_CANONICAL_INTERPOLATION):
                    failures.append(
                        f"vmd_camera_light.camera_light.interpolation.exported_file.{frame} must be canonical"
                    )

    for boundary in VMD_CAMERA_LIGHT_COMPARISON_BOUNDARIES:
        payload = tracks.get(boundary)
        if not isinstance(payload, dict):
            failures.append(f"vmd_camera_light.camera_light.{boundary}_missing")
            continue
        for track in VMD_CAMERA_LIGHT_TRACKS:
            values = payload.get(track)
            if not isinstance(values, dict) or set(values) != {"0", "30", "60"}:
                failures.append(f"vmd_camera_light.camera_light.{boundary}.{track}_missing")
    dense = tracks.get("dense")
    if not isinstance(dense, dict):
        failures.append("vmd_camera_light.camera_light.dense_missing")
    else:
        if dense.get("checked_frames") != list(VMD_CAMERA_LIGHT_DENSE_FRAMES):
            failures.append("vmd_camera_light.camera_light.dense.checked_frames mismatch")
        if dense.get("native_comparison_tracks") != ["camera"]:
            failures.append("vmd_camera_light.camera_light.dense.native_comparison_tracks mismatch")
        if dense.get("light_comparison") != "source_import/fresh_import":
            failures.append("vmd_camera_light.camera_light.dense.light_comparison mismatch")
        dense_frames = {str(frame) for frame in VMD_CAMERA_LIGHT_DENSE_FRAMES}
        camera_fields = ("distance", "position", "rotation", "viewing_angle", "perspective")
        light_fields = ("color", "direction")

        def _finite_scalar(value: Any) -> bool:
            return (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
            )

        def _finite_vector(value: Any, length: int) -> bool:
            return (
                isinstance(value, (list, tuple))
                and len(value) == length
                and all(_finite_scalar(item) for item in value)
            )

        def _validate_dense_values(payload: Any, boundary: str) -> dict[str, dict[str, Any]]:
            normalized: dict[str, dict[str, Any]] = {"camera": {}, "light": {}}
            if not isinstance(payload, dict):
                failures.append(f"vmd_camera_light.camera_light.dense.{boundary}_malformed")
                return normalized
            for track, fields in (("camera", camera_fields), ("light", light_fields)):
                values = payload.get(track)
                if not isinstance(values, dict) or set(values) != dense_frames:
                    failures.append(f"vmd_camera_light.camera_light.dense.{boundary}.{track}_missing")
                    continue
                for frame in sorted(dense_frames):
                    entry = values.get(frame)
                    if not isinstance(entry, dict):
                        failures.append(
                            f"vmd_camera_light.camera_light.dense.{boundary}.{track}[{frame}] malformed"
                        )
                        continue
                    normalized[track][frame] = entry
                    for field in fields:
                        if field not in entry:
                            failures.append(
                                f"vmd_camera_light.camera_light.dense.{boundary}.{track}[{frame}].{field} missing"
                            )
                            continue
                        value = entry[field]
                        valid = (
                            _finite_vector(value, 3)
                            if field in ("position", "rotation", "color", "direction")
                            else _finite_scalar(value)
                        )
                        if field == "perspective":
                            valid = isinstance(value, int) and not isinstance(value, bool) and value in (0, 1)
                        if not valid:
                            failures.append(
                                f"vmd_camera_light.camera_light.dense.{boundary}.{track}[{frame}].{field} malformed"
                            )
            return normalized

        for boundary in ("native_expected", "source_import", "exported_file", "fresh_import"):
            payload = dense.get(boundary)
            if not isinstance(payload, dict):
                failures.append(f"vmd_camera_light.camera_light.dense.{boundary}_missing")
                continue
            for track in VMD_CAMERA_LIGHT_TRACKS:
                values = payload.get(track)
                if not isinstance(values, dict) or set(values) != dense_frames:
                    failures.append(f"vmd_camera_light.camera_light.dense.{boundary}.{track}_missing")
        dense_values = {
            boundary: _validate_dense_values(dense.get(boundary), boundary)
            for boundary in ("native_expected", "source_import", "exported_file", "fresh_import")
        }

        def _compare_dense_track(
            expected_boundary: str,
            actual_boundary: str,
            track: str,
            fields: tuple[str, ...],
            tolerance: float,
        ) -> None:
            expected_tracks = dense_values[expected_boundary].get(track, {})
            actual_tracks = dense_values[actual_boundary].get(track, {})
            for frame in sorted(dense_frames):
                expected = expected_tracks.get(frame)
                actual = actual_tracks.get(frame)
                if not expected or not actual:
                    continue
                for field in fields:
                    expected_value = expected.get(field)
                    actual_value = actual.get(field)
                    if isinstance(expected_value, (list, tuple)) and isinstance(actual_value, (list, tuple)):
                        mismatch = (
                            len(expected_value) != len(actual_value)
                            or not all(_finite_scalar(item) for item in expected_value)
                            or not all(_finite_scalar(item) for item in actual_value)
                            or any(
                                abs(float(left) - float(right)) > tolerance
                                for left, right in zip(expected_value, actual_value)
                            )
                        )
                    elif _finite_scalar(expected_value) and _finite_scalar(actual_value):
                        mismatch = abs(float(expected_value) - float(actual_value)) > tolerance
                    else:
                        mismatch = expected_value != actual_value
                    if mismatch:
                        failures.append(
                            f"vmd_camera_light.camera_light.dense.{expected_boundary}_vs_{actual_boundary}"
                            f".{track}[{frame}].{field} mismatch"
                        )

        for boundary in ("source_import", "exported_file", "fresh_import"):
            _compare_dense_track(
                "native_expected",
                boundary,
                "camera",
                camera_fields,
                VMD_CAMERA_LIGHT_CAMERA_TOLERANCE,
            )
        for boundary in ("exported_file", "fresh_import"):
            _compare_dense_track(
                "source_import",
                boundary,
                "light",
                light_fields,
                VMD_CAMERA_LIGHT_NUMERIC_TOLERANCE,
            )
    return failures


def _validate_policy_reject_case(
    case: Mapping[str, Any], export_format: str, policy_code: str, count_fields: tuple[str, ...]
) -> list[str]:
    """Require positive fresh-import provenance and output safety for one policy case."""
    failures: list[str] = []
    if case.get("policy_code") != policy_code:
        failures.append(f"{export_format}.policy_code={policy_code!r} expected")
    import_oracles = case.get("import_oracles")
    if not isinstance(import_oracles, dict):
        failures.append(f"{export_format}.import_oracles_missing")
    else:
        for field in count_fields:
            value = import_oracles.get(field)
            try:
                positive = not isinstance(value, bool) and int(value) > 0
            except (TypeError, ValueError, OverflowError):
                positive = False
            if not positive:
                failures.append(f"{export_format}.import_oracles.{field} must be positive")
    output_safety = case.get("output_safety")
    if export_format in {"pmx_sdef", "pmx_impulse", "pmx_flip"}:
        if not isinstance(output_safety, dict):
            failures.append(f"{export_format}.output_safety_missing")
        else:
            expected_safety = {
                "target_existed_before": True,
                "target_exists_after": True,
                "created": False,
                "overwritten": False,
                "preserved": True,
                "writer_called": False,
            }
            for field, expected in expected_safety.items():
                if output_safety.get(field) is not expected:
                    failures.append(f"{export_format}.output_safety.{field} must be {expected!r}")
    collection = case.get("collection")
    if not isinstance(collection, dict):
        failures.append(f"{export_format}.collection_missing")
    else:
        if collection.get("source_fresh_import") is not True:
            failures.append(f"{export_format}.collection.source_fresh_import must be true")
        if collection.get("export_writer_called") is not False:
            failures.append(f"{export_format}.collection.export_writer_called must be false")
    return failures


def _validate_maya_probe_report(
    step: dict[str, Any],
    report_path: Path,
    expected_version: str,
) -> list[Path]:
    """Fail closed unless the current Maya probe proves every required case."""
    if step["status"] != "pass":
        return []
    if not report_path.is_file():
        step["status"] = "fail"
        step["error"] = f"Maya probe did not write {report_path}"
        return []
    try:
        report = _load_json(report_path)
    except (OSError, ValueError, TypeError) as exc:
        step["status"] = "fail"
        step["error"] = f"invalid Maya probe report: {type(exc).__name__}: {exc}"
        return []

    cases = report.get("cases")
    by_format = (
        {case.get("format"): case for case in cases if isinstance(case, dict)}
        if isinstance(cases, list)
        else {}
    )
    required_formats = {
        "pmx",
        "pmd_import",
        "pmx_morph",
        "pmx_bone_semantics",
        "pmx_physics",
        "pmx_soft_body",
        "pmx_sdef",
        "pmx_impulse",
        "pmx_flip",
        "vmd",
        "vmd_mode_a",
        "vmd_model_tracks",
        "vmd_camera_light",
    }
    failures = []
    if report.get("gate") != "V070-EXPORT-RELEASE-GATE-1":
        failures.append(f"gate={report.get('gate')!r}")
    if str(report.get("maya_version")) != expected_version:
        failures.append(f"maya_version={report.get('maya_version')!r}")
    if report.get("status") != "pass":
        failures.append(f"status={report.get('status')!r}")
    if set(by_format) != required_formats:
        failures.append(f"formats={sorted(by_format)}")
    for export_format in sorted(required_formats):
        case = by_format.get(export_format)
        allowed_statuses = {"pass"}
        if export_format in {"pmx_soft_body", "pmx_sdef", "pmx_impulse", "pmx_flip"}:
            allowed_statuses.add("policy-reject")
        if not isinstance(case, dict) or case.get("status") not in allowed_statuses:
            failures.append(f"{export_format}.status={case.get('status') if isinstance(case, dict) else None!r}")
            continue
        if export_format == "pmd_import":
            import_oracles = case.get("import_oracles")
            if not isinstance(import_oracles, dict):
                failures.append("pmd_import.import_oracles_missing")
            else:
                for field in ("mesh_count", "vertex_count", "face_count", "material_count", "pose_joint_count"):
                    value = import_oracles.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        failures.append(f"pmd_import.import_oracles.{field} must be positive")
                for field in ("morph_count", "rigid_body_count", "joint_count"):
                    value = import_oracles.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        failures.append(f"pmd_import.import_oracles.{field} must be non-negative")
                if not isinstance(import_oracles.get("metadata_field_count"), int) or import_oracles.get(
                    "metadata_field_count", 0
                ) <= 0:
                    failures.append("pmd_import.import_oracles.metadata_field_count must be positive")
            collection = case.get("collection")
            if not isinstance(collection, dict):
                failures.append("pmd_import.collection_missing")
            else:
                if collection.get("source_fresh_import") is not True:
                    failures.append("pmd_import.collection.source_fresh_import must be true")
                if collection.get("export_writer_called") is not False:
                    failures.append("pmd_import.collection.export_writer_called must be false")
            if case.get("output") is not None:
                failures.append("pmd_import.output must be null")
        if export_format == "pmx_soft_body" and case.get("policy_code") != "PMX_SOFT_BODIES_UNSUPPORTED":
            failures.append("pmx_soft_body.policy_code='PMX_SOFT_BODIES_UNSUPPORTED' expected")
        if export_format == "pmx_soft_body":
            import_oracles = case.get("import_oracles")
            if not isinstance(import_oracles, dict) or int(import_oracles.get("soft_body_count", 0) or 0) <= 0:
                failures.append("pmx_soft_body.import_oracles.soft_body_count must be positive")
            collection = case.get("collection")
            if not isinstance(collection, dict):
                failures.append("pmx_soft_body.collection_missing")
            else:
                if collection.get("source_fresh_import") is not True:
                    failures.append("pmx_soft_body.collection.source_fresh_import must be true")
                if collection.get("export_writer_called") is not False:
                    failures.append("pmx_soft_body.collection.export_writer_called must be false")
            if "output" not in case or case.get("output") is not None:
                failures.append("pmx_soft_body.output must be null")
        if export_format == "pmx_sdef":
            failures.extend(
                _validate_policy_reject_case(
                    case,
                    "pmx_sdef",
                    "PMX_VERTEX_SDEF_UNSUPPORTED",
                    (
                        "source_sdef_vertex_count",
                        "fresh_import_sdef_vertex_count",
                        "provenance_vertex_count",
                        "collected_sdef_vertex_count",
                    ),
                )
            )
        if export_format == "pmx_impulse":
            failures.extend(
                _validate_policy_reject_case(
                    case,
                    "pmx_impulse",
                    "MORPH_TYPE_UNSUPPORTED",
                    (
                        "source_impulse_morph_count",
                        "fresh_import_impulse_morph_count",
                        "provenance_offset_count",
                        "collected_impulse_morph_count",
                    ),
                )
            )
        if export_format == "pmx_flip":
            failures.extend(
                _validate_policy_reject_case(
                    case,
                    "pmx_flip",
                    "MORPH_TYPE_UNSUPPORTED",
                    (
                        "source_flip_morph_count",
                        "fresh_import_flip_morph_count",
                        "provenance_offset_count",
                        "collected_flip_morph_count",
                    ),
                )
            )
        if export_format == "pmx_physics":
            parsed_counts = case.get("parsed_counts")
            if not isinstance(parsed_counts, dict):
                failures.append("pmx_physics.parsed_counts_missing")
            else:
                for field in ("rigid_bodies", "joints"):
                    if int(parsed_counts.get(field, 0) or 0) <= 0:
                        failures.append(f"pmx_physics.parsed_counts.{field} must be positive")
            if not isinstance(case.get("input_normalizations"), list):
                failures.append("pmx_physics.input_normalizations_missing")
        if export_format == "pmx_morph":
            parsed_counts = case.get("parsed_counts")
            if not isinstance(parsed_counts, dict) or int(parsed_counts.get("morphs", 0) or 0) <= 0:
                failures.append("pmx_morph.parsed_counts.morphs must be positive")
            failures.extend(_validate_morph_oracle_case(case))
        if export_format == "pmx_bone_semantics":
            parsed_counts = case.get("parsed_counts")
            if not isinstance(parsed_counts, dict) or int(parsed_counts.get("bones", 0) or 0) <= 0:
                failures.append("pmx_bone_semantics.parsed_counts.bones must be positive")
            failures.extend(_validate_bone_semantics_case(case))
        if export_format == "vmd_model_tracks":
            parsed_counts = case.get("parsed_counts")
            if not isinstance(parsed_counts, dict):
                failures.append("vmd_model_tracks.parsed_counts_missing")
            else:
                for field in ("bone_frames", "morph_frames", "ik_show_hide_frames"):
                    value = parsed_counts.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        failures.append(f"vmd_model_tracks.parsed_counts.{field} must be positive")
                for field in ("camera_frames", "light_frames", "shadow_frames"):
                    if parsed_counts.get(field) != 0:
                        failures.append(f"vmd_model_tracks.parsed_counts.{field} must be zero")
            if case.get("mode_c_warning_acknowledged") is not True:
                failures.append("vmd_model_tracks.mode_c_warning_acknowledged must be true")
            failures.extend(_validate_vmd_model_tracks_case(case))
        if export_format == "vmd_mode_a":
            failures.extend(_validate_vmd_mode_a_case(case))
        if export_format == "vmd_camera_light":
            parsed_counts = case.get("parsed_counts")
            if not isinstance(parsed_counts, dict):
                failures.append("vmd_camera_light.parsed_counts_missing")
            else:
                for field in ("camera_frames", "light_frames"):
                    value = parsed_counts.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        failures.append(f"vmd_camera_light.parsed_counts.{field} must be positive")
                for field in ("bone_frames", "morph_frames", "ik_show_hide_frames", "shadow_frames"):
                    if parsed_counts.get(field) != 0:
                        failures.append(f"vmd_camera_light.parsed_counts.{field} must be zero")
            failures.extend(_validate_vmd_camera_light_case(case))
        if not case.get("report_json") or not case.get("report_md"):
            failures.append(f"{export_format}.report_pair_missing")

    if failures:
        step["status"] = "fail"
        step["error"] = "Maya probe report failed validation: " + "; ".join(failures)
        return []

    report_paths = []
    for case in by_format.values():
        report_paths.append(Path(case["report_json"]))
    step["probe_report"] = str(report_path)
    step["probe_cases"] = sorted(by_format)
    return report_paths


def _report_consistency_step(report_paths: Iterable[Path]) -> dict[str, Any]:
    """Validate all generated report bundles with the canonical checker."""
    checker_path = ROOT / "tools" / "export_report_consistency.py"
    if not checker_path.is_file():
        return _not_run("report_consistency", "checker not present yet")
    command = [sys.executable, str(checker_path)]
    failures = []
    checked = []
    for report_json in report_paths:
        report_md = report_json.with_suffix(".md")
        if not report_json.is_file() or not report_md.is_file():
            failures.append(f"missing report pair: {report_json}")
            continue
        completed = _run_command(
            f"report_consistency:{report_json.parent.name}",
            command + [str(report_json), str(report_md)],
            timeout=60.0,
        )
        checked.append(completed)
        if completed["status"] != "pass":
            failures.append(str(report_json))
    return {
        "name": "report_consistency",
        "status": "pass" if not failures and checked else "fail",
        "checked": checked,
        "failures": failures,
    }


def _maya_path(version: str) -> Path:
    """Return the mayapy path resolved by the shared Maya-location helper."""
    return Path(resolve_mayapy(version))


def _mmd_anim_provenance(report_path: Path | None) -> dict[str, Any]:
    """Keep executable and checkout provenance distinct in the release summary."""
    provenance: dict[str, Any] = {
        "evidence_status": "not_run" if report_path is None else "unavailable",
        "validation_report": str(report_path) if report_path is not None else None,
        "validation_status": None,
        "cli": None,
        "cli_version": None,
        "expected_cli_version": None,
        "version_match": None,
        "submodule_revision": None,
        "relationship": {
            "cli_version_compared_to": "expected_cli_version",
            "submodule_revision_role": "checked-out source provenance",
            "cli_submodule_direct_comparison": "not_applicable",
        },
    }
    if report_path is None:
        return provenance
    if not report_path.is_file():
        provenance["reason"] = "MMD-Anim validation report was not written"
        return provenance
    try:
        report = _load_json(report_path)
    except (OSError, ValueError, TypeError) as exc:
        provenance["reason"] = f"invalid MMD-Anim validation report: {type(exc).__name__}: {exc}"
        return provenance
    if not isinstance(report, dict):
        provenance["reason"] = "MMD-Anim validation report must be a JSON object"
        return provenance
    provenance.update(
        {
            "evidence_status": "recorded",
            "validation_status": report.get("status"),
            "cli": report.get("cli"),
            "cli_version": report.get("cli_version"),
            "expected_cli_version": report.get("expected_cli_version"),
            "version_match": report.get("version_match"),
            "submodule_revision": report.get("submodule_revision"),
        }
    )
    return provenance


def build_release_summary(
    *,
    out_dir: Path,
    maya_versions: Iterable[str],
    mmd_anim_cli: str | None,
    skip_gui: bool,
    full_gui: bool,
    skip_focused_tests: bool,
) -> dict[str, Any]:
    """Run all V070 steps and write one release summary."""
    start_provenance = _capture_release_provenance()
    maya_versions = tuple(maya_versions)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []
    if skip_focused_tests:
        steps.append(_not_run("focused_tests", "--skip-focused-tests was supplied"))
    else:
        focused = [
            "tests/unit/test_export_workflow.py",
            "tests/unit/test_export_model_validation.py",
            "tests/unit/test_pmd_parser.py",
            "tests/unit/test_vmd_validation.py",
            "tests/unit/test_validation_report_catalog.py",
            "tests/unit/test_validation_report_artifacts.py",
            "tests/unit/test_validation_console.py",
            "tests/unit/test_export_release_gate.py",
            "tests/unit/test_export_scope.py",
            "tests/unit/test_gui_runner.py",
            "tests/unit/test_ui_coverage_gate.py",
            "tests/unit/test_ui_selector_contract.py",
            "tests/unit/test_vmd_scene_collector.py",
        ]
        consistency_tests = ROOT / "tests" / "unit" / "test_export_report_consistency.py"
        if consistency_tests.is_file():
            focused.append(str(consistency_tests.relative_to(ROOT)))
        steps.append(
            _run_command(
                "focused_tests",
                [*_pytest_command(), "-q", *focused],
                timeout=900.0,
            )
        )

    fail_matrix = _run_fail_fixture_matrix(out_dir / "fail-fixtures")
    steps.append({"name": "fail_fixture_matrix", **fail_matrix})

    report_paths: list[Path] = [Path(path) for path in fail_matrix.get("report_paths", [])]
    gui_log_paths: dict[str, Path] = {}
    for version in maya_versions:
        mayapy = _maya_path(version)
        probe_dir = out_dir / f"maya-{version}"
        if not mayapy.is_file():
            steps.append(_not_run(f"maya_probe_{version}", f"mayapy not found: {mayapy}"))
            continue
        probe_report = probe_dir / "maya-probe.json"
        if probe_report.is_file():
            probe_report.unlink()
        probe_step = _run_command(
            f"maya_probe_{version}",
            [
                str(mayapy),
                str(ROOT / "tools" / "export_release_maya_probe.py"),
                "--out-dir",
                str(probe_dir),
            ],
            env={**os.environ, "MAYA_APP_DIR": str(out_dir / f"maya-profile-{version}")},
            timeout=1200.0,
        )
        report_paths.extend(_validate_maya_probe_report(probe_step, probe_report, version))
        steps.append(probe_step)

        if skip_gui:
            steps.append(_not_run(f"gui_tests_{version}", "--skip-gui was supplied"))
        else:
            gui_log_path = out_dir / f"gui-{version}.log"
            gui_log_paths[version] = gui_log_path
            gui_args = [
                "--maya_version",
                version,
                "--log_path",
                str(gui_log_path),
            ]
            if not full_gui:
                gui_args.extend(
                    [
                        "--test_path",
                        "tests/gui",
                        "--test_filter",
                        "tests.gui.guitest_export_tab_gui",
                    ]
                )
            steps.append(
                _run_command(
                    f"gui_export_workflow_{version}" if not full_gui else f"gui_tests_{version}",
                    [sys.executable, str(ROOT / "tests" / "run_gui_tests.py"), *gui_args],
                    timeout=1200.0,
                )
            )

    if skip_gui:
        steps.append(_not_run("ui_coverage_gate", "--skip-gui was supplied"))
    elif not full_gui:
        steps.append(
            _not_run(
                "ui_coverage_gate",
                "targeted GUI scope does not provide the required nine-tab evidence",
            )
        )
    else:
        ui_coverage_report = out_dir / "ui-coverage.json"
        coverage_args = [
            sys.executable,
            str(ROOT / "tools" / "ui_coverage_gate.py"),
            "--write-report",
            str(ui_coverage_report),
        ]
        for version in maya_versions:
            coverage_args.extend(
                ("--batch-log", f"{version}={gui_log_paths.get(version, out_dir / f'gui-{version}.log')}")
            )
        steps.append(
            _run_command(
                "ui_coverage_gate",
                coverage_args,
                timeout=120.0,
            )
        )

    mmd_report: Path | None = None
    if mmd_anim_cli:
        mmd_report = out_dir / "mmd-anim-validation.json"
        for stale_report in (mmd_report, mmd_report.with_suffix(".md")):
            if stale_report.exists():
                stale_report.unlink()
        steps.append(
            _run_command(
                "mmd_anim_validation",
                [
                    sys.executable,
                    str(ROOT / "tools" / "export_validation_gate.py"),
                    "--cli",
                    mmd_anim_cli,
                    "--strict",
                    "--out",
                    str(mmd_report),
                ],
                timeout=900.0,
            )
        )
    else:
        steps.append(_not_run("mmd_anim_validation", "no --mmd-anim-cli was supplied"))

    uvx = shutil.which("uvx") or "uvx"
    ffi_build_step = _run_command(
        "mmd_anim_ffi_build",
        [uvx, "nox", "-s", "ffi_build", "--", "--release"],
        timeout=1800.0,
    )
    runtime_path, runtime_sha256, source_revision = _validate_ffi_build_step(ffi_build_step)
    steps.append(ffi_build_step)

    binding_report = out_dir / "mmd-anim-binding-gate.json"
    if binding_report.exists():
        binding_report.unlink()
    runtime_args = ["--runtime-library", str(runtime_path)] if runtime_path else []
    steps.append(
        _run_command(
            "mmd_anim_python_bindings",
            [uvx, "nox", "-s", "mmd_anim_python_tests", "--", *runtime_args],
            timeout=900.0,
        )
    )
    binding_gate_step = _run_command(
        "mmd_anim_binding_gate",
        [
            uvx,
            "nox",
            "-s",
            "mmd_anim_binding_gate",
            "--",
            *runtime_args,
            "--out",
            str(binding_report),
        ],
        timeout=900.0,
    )
    _validate_binding_gate_artifact(
        binding_gate_step,
        binding_report,
        runtime_path=runtime_path,
        runtime_sha256=runtime_sha256,
    )
    steps.append(binding_gate_step)

    steps.append(_report_consistency_step(report_paths))
    unexecuted = [step["name"] for step in steps if step["status"] == "not_run"]
    blockers = [
        {
            "name": step["name"],
            "reason": step.get("error") or step.get("stderr") or step.get("reason") or "step failed",
        }
        for step in steps
        if step["status"] == "fail"
    ]
    if not maya_versions:
        blockers.append({"name": "maya_versions", "reason": "at least one Maya version is required"})
    start_failures = _validate_release_provenance(start_provenance)
    if start_provenance.get("dirty") is True:
        start_failures.append("worktree was dirty at gate start")
    end_provenance = _capture_release_provenance(run_id=start_provenance.get("run_id"))
    end_failures = _validate_release_provenance(end_provenance)
    if start_provenance.get("head_sha") != end_provenance.get("head_sha"):
        end_failures.append("HEAD changed during gate")
    if start_provenance.get("run_id") != end_provenance.get("run_id"):
        end_failures.append("run ID changed during gate")
    if start_provenance.get("dirty") != end_provenance.get("dirty"):
        end_failures.append("dirty state changed during gate")
    provenance_failures = start_failures + [f"end: {failure}" for failure in end_failures]
    if provenance_failures:
        blockers.append(
            {
                "name": "release_provenance",
                "reason": "; ".join(provenance_failures),
            }
        )
    mmd_anim_provenance = _mmd_anim_provenance(mmd_report)
    mmd_anim_provenance["ffi_build"] = {
        "step_status": ffi_build_step["status"],
        "source_revision": source_revision,
        "runtime_path": str(runtime_path) if runtime_path else None,
        "runtime_sha256": runtime_sha256,
        "artifact": ffi_build_step.get("artifact"),
        "artifact_sha256": ffi_build_step.get("artifact_sha256"),
    }
    mmd_anim_provenance["binding"] = {
        "python_tests_status": next(
            step["status"] for step in steps if step["name"] == "mmd_anim_python_bindings"
        ),
        "gate_status": binding_gate_step["status"],
        "gate_artifact": str(binding_report),
        "runtime_path": str(runtime_path) if runtime_path else None,
        "runtime_sha256": runtime_sha256,
        "source_revision": source_revision,
    }
    gui_steps = [
        step
        for step in steps
        if step["name"].startswith("gui_export_workflow_") or step["name"].startswith("gui_tests_")
    ]
    gui_scope = "not_run" if skip_gui else "full" if full_gui else "targeted"
    gui_executed = any(step["status"] != "not_run" for step in gui_steps)
    gui_passed = bool(gui_steps) and len(gui_steps) == len(maya_versions) and all(
        step["status"] == "pass" for step in gui_steps
    )
    ui_coverage = {}
    ui_coverage_path = out_dir / "ui-coverage.json"
    if ui_coverage_path.is_file():
        try:
            payload = json.loads(ui_coverage_path.read_text(encoding="utf-8"))
            counts = payload.get("coverage")
            if isinstance(counts, dict):
                ui_coverage = {
                    key: int(counts.get(key, 0))
                    for key in ("qt_case", "not_run", "excluded", "blocked")
                }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            ui_coverage = {}
    coverage_proven = [
        "13-case PMX/VMD export plus PMD import probe with fresh-import or policy-reject evidence",
        "PMX 2.0 additional UV channels 1-4 and UV/additional-UV morph field oracle",
        "PMX IK and non-IK bone semantic fields across Maya import/export boundaries",
        "PMX 2.1 soft-body, SDEF, Flip, and Impulse provenance with stable policy-reject",
        "VMD Mode C bone/morph/IK state tracks and standalone camera/light dense field oracle",
        "VMD Mode A raw provenance, interpolation, pose, SHA, and edited fail-closed oracle",
        "MMD-Anim CLI validation plus Python binding tests and binding export gate",
        "fatal fail-closed, warning acknowledgement, and output-preservation boundaries",
        "canonical JSON/Markdown validation-report consistency",
    ]
    coverage_outside = [
        "full PMX material/morph field parity beyond representative oracle fields",
        "visual/runtime parity claims for UV morphs and VMD camera/light tracks",
        "VMD self-shadow support (explicitly excluded by the camera/light case)",
    ]
    if gui_passed:
        coverage_proven.append(f"{gui_scope} GUI workflow for each requested Maya version")
    else:
        coverage_outside.append(
            f"{gui_scope} GUI workflow was not fully executed and passing for every requested Maya version"
        )
    if ui_coverage:
        total_ui_surfaces = sum(ui_coverage.values())
        coverage_proven.append(
            f"GUI interaction evidence for {ui_coverage['qt_case']} of {total_ui_surfaces} inventoried surfaces"
        )
        if ui_coverage["not_run"]:
            coverage_outside.append(
                f"{ui_coverage['not_run']} inventoried GUI surfaces remain not_run"
            )
    summary = {
        "schema_version": 1,
        "gate": "V070-EXPORT-RELEASE-GATE-1",
        "status": "pass" if not blockers and not unexecuted else "fail",
        "run_id": start_provenance.get("run_id"),
        "timestamp": start_provenance.get("timestamp"),
        "provenance": {
            "start": start_provenance,
            "end": end_provenance,
        },
        "maya_versions": list(maya_versions),
        "gui_scope": gui_scope,
        "gui_requested": not skip_gui,
        "gui_executed": gui_executed,
        "gui_passed": gui_passed,
        "gui_steps": [
            {"name": step["name"], "status": step["status"]} for step in gui_steps
        ],
        "ui_coverage": ui_coverage,
        "coverage": {
            "proven": coverage_proven,
            "outside_this_gate": coverage_outside,
        },
        "mmd_anim_provenance": mmd_anim_provenance,
        "steps": steps,
        "unexecuted": unexecuted,
        "blockers": blockers,
    }
    (out_dir / "release-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# V070 Export Release Summary",
        "",
        f"- Status: `{summary['status'].upper()}`",
        f"- Gate: `{summary['gate']}`",
        f"- Maya versions: `{', '.join(summary['maya_versions'])}`",
        f"- GUI scope: `{summary['gui_scope']}`",
        f"- Run ID: `{summary['provenance']['start']['run_id']}`",
        f"- Timestamp (UTC): `{summary['provenance']['start']['timestamp']}`",
        f"- Branch: `{summary['provenance']['start']['branch'] or 'unavailable'}`",
        f"- HEAD at start: `{summary['provenance']['start']['head_sha'] or 'unavailable'}`",
        f"- HEAD at end: `{summary['provenance']['end']['head_sha'] or 'unavailable'}`",
        f"- Dirty at start: `{str(summary['provenance']['start']['dirty']).lower()}`",
        f"- Dirty at end: `{str(summary['provenance']['end']['dirty']).lower()}`",
        "",
        "## MMD-Anim Provenance",
        "",
        f"- Evidence status: `{mmd_anim_provenance['evidence_status']}`",
        f"- Validation report: `{mmd_anim_provenance['validation_report'] or 'not generated'}`",
        f"- Validation status: `{mmd_anim_provenance['validation_status'] or 'unavailable'}`",
        f"- CLI: `{mmd_anim_provenance['cli'] or 'unavailable'}`",
        f"- Observed CLI version: `{mmd_anim_provenance['cli_version'] or 'unavailable'}`",
        f"- Expected CLI version: `{mmd_anim_provenance['expected_cli_version'] or 'not configured'}`",
        f"- CLI version match: `{str(mmd_anim_provenance['version_match']).lower()}`",
        f"- Checked-out submodule revision: `{mmd_anim_provenance['submodule_revision'] or 'unavailable'}`",
        f"- FFI build status: `{mmd_anim_provenance['ffi_build']['step_status']}`",
        f"- FFI runtime: `{mmd_anim_provenance['ffi_build']['runtime_path'] or 'unavailable'}`",
        f"- FFI runtime SHA-256: `{mmd_anim_provenance['ffi_build']['runtime_sha256'] or 'unavailable'}`",
        f"- Binding tests status: `{mmd_anim_provenance['binding']['python_tests_status']}`",
        f"- Binding gate status: `{mmd_anim_provenance['binding']['gate_status']}`",
        f"- Binding gate artifact: `{mmd_anim_provenance['binding']['gate_artifact']}`",
        "- Relationship: CLI version is compared only with expected CLI version; "
        "the checked-out submodule revision is separate source provenance and is not directly compared.",
        "",
        "## Steps",
        "",
        "| Step | Status | Evidence |",
        "|---|---|---|",
    ]
    for step in steps:
        evidence = step.get("reason") or step.get("error") or step.get("returncode", "")
        lines.append(f"| `{step['name']}` | `{step['status']}` | {str(evidence).replace('|', '/')} |")
    lines.extend(["", "## Unexecuted", ""])
    if unexecuted:
        lines.extend(f"- `{name}`" for name in unexecuted)
    else:
        lines.append("None.")
    lines.extend(["", "## Blockers", ""])
    if blockers:
        lines.extend(f"- `{item['name']}`: {item['reason']}" for item in blockers)
    else:
        lines.append("None.")
    lines.extend(["", "## Coverage", ""])
    if ui_coverage:
        lines.append(
            "UI surfaces: "
            f"`{ui_coverage['qt_case']}` evidenced / "
            f"`{ui_coverage['not_run']}` not run / "
            f"`{ui_coverage['excluded']}` excluded / "
            f"`{ui_coverage['blocked']}` blocked."
        )
        lines.append("")
    lines.append("Proven by this gate:")
    lines.extend(f"- {item}" for item in summary["coverage"]["proven"])
    lines.append("")
    lines.append("Outside this gate and still required for the public Support Matrix:")
    lines.extend(f"- {item}" for item in summary["coverage"]["outside_this_gate"])
    (out_dir / "release-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the release gate CLI and return non-zero for omitted/failed steps."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="build/release-gate/v070")
    parser.add_argument("--maya", dest="maya_versions", action="append", choices=MAYA_VERSIONS)
    parser.add_argument("--mmd-anim-cli")
    parser.add_argument("--skip-gui", action="store_true")
    gui_scope = parser.add_mutually_exclusive_group()
    gui_scope.add_argument("--full-gui", dest="full_gui", action="store_true")
    gui_scope.add_argument(
        "--targeted-gui",
        dest="full_gui",
        action="store_false",
        help="run only ExportTab GUI checks; this is not release-complete",
    )
    parser.set_defaults(full_gui=True)
    parser.add_argument("--skip-focused-tests", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build_release_summary(
            out_dir=_require_build_path(args.out_dir, "--out-dir"),
            maya_versions=tuple(args.maya_versions or MAYA_VERSIONS),
            mmd_anim_cli=args.mmd_anim_cli,
            skip_gui=args.skip_gui,
            full_gui=args.full_gui,
            skip_focused_tests=args.skip_focused_tests,
        )
    except Exception as exc:
        print(f"Export release gate failed to start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": summary["status"], "unexecuted": summary["unexecuted"]}, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
