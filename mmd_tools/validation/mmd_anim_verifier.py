"""Optional MMD-Anim CLI adapter for post-export file validation.

The adapter keeps the external parser/runtime behind a subprocess boundary.
It normalizes command availability, exit codes, JSON diagnostics, roundtrip
status, and basic counts into the local ``ExportValidationReport`` contract.
"""

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

from .export_validator import ExportValidationIssue, ExportValidationReport


_TEXT_SEQUENCE_TYPES = (str, bytes, bytearray)
_Runner = Callable[..., Any]


def _is_sequence(value: Any) -> bool:
    """Return whether *value* is a non-text sequence."""
    return isinstance(value, Sequence) and not isinstance(value, _TEXT_SEQUENCE_TYPES)


def _issue(code: str, path: str, message: str) -> ExportValidationIssue:
    """Build one blocking external-verifier issue."""
    return ExportValidationIssue(code, "fatal", True, path, message)


def _triangulated_face_count(faces: Any) -> Optional[int]:
    """Return writer-equivalent triangle count for a collector face table."""
    if not _is_sequence(faces):
        return None
    triangles = 0
    for face in faces:
        if not _is_sequence(face) or len(face) < 3:
            return None
        triangles += 1 if len(face) == 3 else len(face) - 2
    return triangles


def _expected_counts(model_data: Optional[Mapping]) -> Dict[str, int]:
    """Return the count subset that can be compared with MMD-Anim output."""
    if not isinstance(model_data, Mapping):
        return {}
    counts: Dict[str, int] = {}
    vertices = model_data.get("vertices")
    if _is_sequence(vertices):
        counts["vertices"] = len(vertices)
    faces = _triangulated_face_count(model_data.get("faces"))
    if faces is not None:
        counts["faces"] = faces
    materials = model_data.get("materials")
    if _is_sequence(materials):
        counts["materials"] = len(materials) if materials else 1
    bones = model_data.get("bones")
    if bones is None:
        counts["bones"] = 1
    elif _is_sequence(bones):
        counts["bones"] = len(bones)
    for source_name, report_name in (
        ("morphs", "morphs"),
        ("rigid_bodies", "rigidBodies"),
        ("joints", "joints"),
    ):
        value = model_data.get(source_name)
        if _is_sequence(value):
            counts[report_name] = len(value)
    return counts


def _run_json_command(
    cli_path: str,
    subcommand: str,
    asset_path: str,
    *,
    timeout: float,
    runner: _Runner,
) -> Tuple[Optional[Mapping], Optional[ExportValidationIssue]]:
    """Run one MMD-Anim JSON command and normalize process failures."""
    args = [str(cli_path), subcommand, str(asset_path), "--json"]
    try:
        completed = runner(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None, _issue(
            "MMD_ANIM_CLI_UNAVAILABLE",
            "mmd_anim.cli",
            "mmd-anim CLI executable is not available",
        )
    except subprocess.TimeoutExpired:
        return None, _issue(
            "MMD_ANIM_TIMEOUT",
            f"mmd_anim.{subcommand}",
            f"mmd-anim {subcommand} exceeded the configured timeout",
        )
    except OSError as exc:
        return None, _issue(
            "MMD_ANIM_CLI_UNAVAILABLE",
            "mmd_anim.cli",
            f"mmd-anim CLI could not be started: {type(exc).__name__}",
        )

    if completed.returncode != 0:
        return None, _issue(
            "MMD_ANIM_COMMAND_FAILED",
            f"mmd_anim.{subcommand}",
            f"mmd-anim {subcommand} exited with code {completed.returncode}",
        )
    try:
        payload = json.loads(completed.stdout or "")
    except (TypeError, ValueError):
        code = (
            "MMD_ANIM_INSPECT_JSON_INVALID"
            if subcommand == "inspect"
            else "MMD_ANIM_ROUNDTRIP_JSON_INVALID"
        )
        return None, _issue(code, f"mmd_anim.{subcommand}.json", "mmd-anim returned invalid JSON")
    if not isinstance(payload, Mapping):
        code = (
            "MMD_ANIM_INSPECT_JSON_INVALID"
            if subcommand == "inspect"
            else "MMD_ANIM_ROUNDTRIP_JSON_INVALID"
        )
        return None, _issue(code, f"mmd_anim.{subcommand}.json", "mmd-anim JSON root must be an object")
    return payload, None


def verify_mmd_anim_asset(
    asset_path: str,
    *,
    model_data: Optional[Mapping] = None,
    cli_path: str = "mmd-anim",
    timeout: float = 60.0,
    runner: _Runner = subprocess.run,
) -> ExportValidationReport:
    """Verify one exported asset with MMD-Anim ``inspect`` and ``roundtrip``.

    The adapter is intentionally opt-in at the Action boundary because the
    CLI version and binary provenance must be explicit in a release gate.
    """
    export_format = Path(asset_path).suffix.lower().lstrip(".") or None
    issues: List[ExportValidationIssue] = []

    inspect_payload, inspect_issue = _run_json_command(
        cli_path,
        "inspect",
        asset_path,
        timeout=timeout,
        runner=runner,
    )
    if inspect_issue is not None:
        issues.append(inspect_issue)
        return ExportValidationReport(export_format, tuple(issues))

    diagnostics = inspect_payload.get("diagnostics", [])
    if diagnostics:
        issues.append(
            _issue(
                "MMD_ANIM_DIAGNOSTICS",
                "mmd_anim.inspect.diagnostics",
                f"mmd-anim inspect returned {len(diagnostics)} diagnostic(s)",
            )
        )

    roundtrip_payload, roundtrip_issue = _run_json_command(
        cli_path,
        "roundtrip",
        asset_path,
        timeout=timeout,
        runner=runner,
    )
    if roundtrip_issue is not None:
        issues.append(roundtrip_issue)
        return ExportValidationReport(export_format, tuple(issues))

    if roundtrip_payload.get("status") != "ok":
        issues.append(
            _issue(
                "MMD_ANIM_ROUNDTRIP_FAILED",
                "mmd_anim.roundtrip.status",
                f"mmd-anim roundtrip status is {roundtrip_payload.get('status')!r}",
            )
        )

    actual_counts = roundtrip_payload.get("counts")
    expected_counts = _expected_counts(model_data)
    if actual_counts is None:
        issues.append(
            _issue(
                "MMD_ANIM_ROUNDTRIP_JSON_INVALID",
                "mmd_anim.roundtrip.counts",
                "mmd-anim roundtrip JSON does not contain counts",
            )
        )
    elif isinstance(actual_counts, Mapping):
        for name, expected in expected_counts.items():
            actual = actual_counts.get(name)
            if actual is not None and actual != expected:
                issues.append(
                    _issue(
                        "MMD_ANIM_COUNT_MISMATCH",
                        f"mmd_anim.roundtrip.counts.{name}",
                        f"mmd-anim count {actual} does not match expected count {expected}",
                    )
                )
    else:
        issues.append(
            _issue(
                "MMD_ANIM_ROUNDTRIP_JSON_INVALID",
                "mmd_anim.roundtrip.counts",
                "mmd-anim roundtrip counts must be an object",
            )
        )

    return ExportValidationReport(export_format, tuple(issues))


__all__ = ["verify_mmd_anim_asset"]
