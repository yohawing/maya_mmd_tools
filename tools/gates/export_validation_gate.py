"""Run the external MMD-Anim export evidence gate.

The gate records CLI version provenance separately from the checked-out
submodule.  It never changes ``external/mmd-anim``; callers choose the CLI
binary explicitly or use the local release build when present.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, Iterable, Optional, Sequence


DEFAULT_ASSETS = (
    Path("tests/data/mmt_test_model.pmx"),
    Path("tests/data/mmt_test_model_test_motion.vmd"),
)
DEFAULT_MODEL = Path("tests/data/mmt_test_model.pmx")
DEFAULT_MOTION = Path("tests/data/mmt_test_model_test_motion.vmd")
DEFAULT_EXPECTED_CLI_VERSION = "mmd-anim 0.2.0"
MAX_EVIDENCE_LINE_LENGTH = 256
MAX_CASE_ERROR_LENGTH = 256
MAX_JSON_SUMMARY_DEPTH = 3
MAX_JSON_SUMMARY_ITEMS = 32
MAX_JSON_SUMMARY_STRING_LENGTH = 256
JSON_SUMMARY_KEYS = (
    "status",
    "format",
    "mode",
    "summary",
    "counts",
    "metadata",
    "maxFrame",
    "bytesIn",
    "bytesOut",
    "cases",
    "perCase",
    "comparedCases",
    "missing",
    "importErrors",
    "comparedFrames",
    "comparedBones",
    "mismatchCount",
    "maxAbsError",
    "worst",
    "worstBone",
    "worstFrame",
    "worstComponent",
    "skippedTargets",
)
FAILED_JSON_STATUSES = frozenset(("fail", "failed", "failure", "error", "errors"))


def _sha256_text(value: str) -> str:
    """Hash raw subprocess output without storing the full machine dump."""
    return f"sha256:{hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()}"


def _first_line(value: str) -> str:
    """Return a bounded first non-empty line from a CLI response."""
    for line in value.splitlines():
        line = line.strip()
        if line:
            return line[:MAX_EVIDENCE_LINE_LENGTH]
    return ""


def _bounded_json_value(value: Any, depth: int = 0) -> Any:
    """Keep structured CLI evidence useful without retaining arbitrary output."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:MAX_JSON_SUMMARY_STRING_LENGTH]
    if depth >= MAX_JSON_SUMMARY_DEPTH:
        if isinstance(value, dict):
            return {"type": "object", "items": len(value)}
        if isinstance(value, list):
            return {"type": "array", "items": len(value)}
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        bounded = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_JSON_SUMMARY_ITEMS:
                bounded["_truncated_items"] = len(value) - MAX_JSON_SUMMARY_ITEMS
                break
            bounded[str(key)[:MAX_JSON_SUMMARY_STRING_LENGTH]] = _bounded_json_value(item, depth + 1)
        return bounded
    if isinstance(value, list):
        bounded_list = [
            _bounded_json_value(item, depth + 1)
            for item in value[:MAX_JSON_SUMMARY_ITEMS]
        ]
        if len(value) > MAX_JSON_SUMMARY_ITEMS:
            bounded_list.append({"_truncated_items": len(value) - MAX_JSON_SUMMARY_ITEMS})
        return bounded_list
    return {"type": type(value).__name__}


def _json_summary(payload: Any) -> Dict[str, Any]:
    """Extract only bounded, audit-relevant fields from structured CLI output."""
    if not isinstance(payload, dict):
        return {"root_type": type(payload).__name__}
    summary = {
        key: _bounded_json_value(payload[key])
        for key in JSON_SUMMARY_KEYS
        if key in payload
    }
    return summary


def _command_hash(command: Sequence[str]) -> str:
    """Hash the exact argv vector without storing command output."""
    encoded = json.dumps(list(command), ensure_ascii=False, separators=(",", ":"))
    return _sha256_text(encoded)


def _bounded_case_text(value: str) -> str:
    """Keep case errors and blockers bounded in the persisted report."""
    return value[:MAX_CASE_ERROR_LENGTH]


def _command_evidence_error(
    result: Dict[str, Any], command_name: str, required_field: str
) -> Optional[str]:
    """Validate process and command-specific JSON evidence for one case."""
    def failure(message: str) -> str:
        error = _bounded_case_text(message)
        result["evidence_status"] = "fail"
        result["evidence_error"] = error
        return error

    if result.get("status") != "pass":
        detail = result.get("error")
        if not detail:
            detail = "exit code {!r}".format(result.get("returncode", "unknown"))
        return failure("{} command failed: {}".format(command_name, detail))

    payload = result.get("json")
    if not isinstance(payload, dict):
        return failure("{} returned invalid JSON".format(command_name))
    if required_field not in payload or payload[required_field] is None:
        return failure(
            "{} JSON is missing {}".format(command_name, required_field)
        )
    if command_name == "roundtrip" and payload.get("status") != "ok":
        return failure(
            "roundtrip JSON status is {!r}; expected 'ok'".format(payload.get("status"))
        )
    status = payload.get("status")
    if isinstance(status, str) and status.strip().lower() in FAILED_JSON_STATUSES:
        return failure("{} JSON reports failed status {!r}".format(command_name, status))
    result["evidence_status"] = "pass"
    return None


def _record_case_failure(
    report: Dict[str, Any], case: Dict[str, Any], blocker_prefix: str, errors: Iterable[str]
) -> None:
    """Record bounded case and blocker text for command evidence failures."""
    case_error = _bounded_case_text("; ".join(errors))
    case["status"] = "fail"
    case["error"] = case_error
    report["blockers"].append(_bounded_case_text("{}: {}".format(blocker_prefix, case_error)))


def _normalize_output_path(path: Path) -> Path:
    """Return a JSON artifact path while leaving the Markdown sidecar distinct."""
    if path.suffix.lower() == ".json":
        return path
    if path.suffix:
        return path.with_suffix(".json")
    return path.with_name(path.name + ".json")


def _run(command: Sequence[str], root: Path, timeout: float) -> Dict[str, Any]:
    """Run one evidence command and retain bounded machine metadata."""
    command_list = list(command)
    result_base = {
        "command": command_list,
        "command_sha256": _command_hash(command_list),
    }
    try:
        completed = subprocess.run(
            command_list,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            **result_base,
            "status": "fail",
            "error": type(exc).__name__,
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    result: Dict[str, Any] = {
        **result_base,
        "status": "pass" if completed.returncode == 0 else "fail",
        "returncode": completed.returncode,
        "stdout_sha256": _sha256_text(stdout),
        "stdout_bytes": len(stdout.encode("utf-8", "replace")),
        "stderr_sha256": _sha256_text(stderr),
        "stderr_bytes": len(stderr.encode("utf-8", "replace")),
        "stdout_first_line": _first_line(stdout) or None,
    }
    if stdout.strip():
        try:
            payload = json.loads(stdout)
        except (TypeError, ValueError):
            result["json"] = None
        else:
            result["json"] = _json_summary(payload)
    if stderr.strip():
        result["stderr_first_line"] = _first_line(stderr)
    return result


def _resolve_cli(root: Path, explicit: Optional[str]) -> Path:
    """Resolve the configured CLI without downloading or changing anything."""
    if explicit:
        return Path(explicit)
    local_name = "mmd-anim.exe" if sys.platform.startswith("win") else "mmd-anim"
    return root / "external" / "mmd-anim" / "target" / "release" / local_name


def _submodule_revision(root: Path) -> str:
    """Read the external submodule revision for evidence only."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root / "external" / "mmd-anim"), "describe", "--tags", "--always", "--dirty"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return "unavailable"
    return _first_line(completed.stdout) or "unavailable"


def _markdown(report: Dict[str, Any]) -> str:
    """Render a concise human-audit view of the JSON evidence."""
    lines = [
        "# Export Validation — MMD-Anim Evidence",
        "",
        f"- Status: `{str(report['status']).upper()}`",
        f"- CLI: `{report['cli']}`",
        f"- CLI version: `{report.get('cli_version') or 'unavailable'}`",
        f"- Expected pinned version: `{report.get('expected_cli_version') or 'not configured'}`",
        f"- Submodule revision: `{report.get('submodule_revision')}`",
        f"- Version match: `{str(report.get('version_match')).lower()}`",
        "",
        "## Commands",
        "",
    ]
    for case in report.get("cases", []):
        lines.append(f"### `{case['kind']}` — `{case.get('asset') or case.get('model')}`")
        lines.append("")
        lines.append(f"- Case status: `{case.get('status', 'unknown')}`")
        lines.append("")
        for command_result in case.get("commands", []):
            command_status = command_result.get("evidence_status", command_result["status"])
            lines.append(
                f"- `{command_result['command'][1] if len(command_result['command']) > 1 else command_result['command'][0]}`: "
                f"`{command_status}` (exit `{command_result.get('returncode', 'n/a')}`)"
            )
            if command_result.get("evidence_error"):
                lines.append(f"  - Evidence error: `{command_result['evidence_error']}`")
        lines.append("")
    if report.get("blockers"):
        lines.extend(["## Blockers", ""])
        lines.extend(f"- {blocker}" for blocker in report["blockers"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_report(
    root: Path,
    cli: Path,
    assets: Iterable[Path],
    model: Optional[Path],
    motion: Optional[Path],
    expected_cli_version: Optional[str],
    timeout: float,
) -> Dict[str, Any]:
    """Run inspect/roundtrip and optional PMX+VMD runtime evidence."""
    report: Dict[str, Any] = {
        "schema_version": 1,
        "status": "pass",
        "cli": str(cli),
        "expected_cli_version": expected_cli_version,
        "submodule_revision": _submodule_revision(root),
        "cases": [],
        "blockers": [],
    }
    version_result = _run([str(cli), "--version"], root, timeout)
    report["version_command"] = version_result
    report["cli_version"] = version_result.get("stdout_first_line")
    report["version_match"] = bool(
        report["cli_version"]
        and expected_cli_version
        and report["cli_version"] == expected_cli_version
    ) if expected_cli_version else None
    if version_result.get("status") != "pass":
        report["blockers"].append("mmd-anim CLI could not be executed")
    elif expected_cli_version and not report["version_match"]:
        report["blockers"].append(
            f"CLI version {report['cli_version']!r} does not match expected {expected_cli_version!r}"
        )

    for asset in assets:
        asset_path = (root / asset).resolve() if not asset.is_absolute() else asset.resolve()
        case = {"kind": "asset", "asset": str(asset_path), "commands": []}
        if not asset_path.is_file():
            case["status"] = "fail"
            case["error"] = "asset missing"
            report["blockers"].append(f"asset missing: {asset_path}")
        else:
            inspect_result = _run([str(cli), "inspect", str(asset_path), "--json"], root, timeout)
            roundtrip_result = _run([str(cli), "roundtrip", str(asset_path), "--json"], root, timeout)
            case["commands"] = [inspect_result, roundtrip_result]
            errors = []
            inspect_error = _command_evidence_error(inspect_result, "inspect", "metadata")
            if inspect_error:
                errors.append(inspect_error)
            roundtrip_error = _command_evidence_error(roundtrip_result, "roundtrip", "status")
            if roundtrip_error:
                errors.append(roundtrip_error)
            if errors:
                _record_case_failure(report, case, "inspect/roundtrip evidence failed", errors)
            else:
                case["status"] = "pass"
        report["cases"].append(case)

    if model is not None and motion is not None:
        model_path = (root / model).resolve() if not model.is_absolute() else model.resolve()
        motion_path = (root / motion).resolve() if not motion.is_absolute() else motion.resolve()
        case = {
            "kind": "runtime_import",
            "model": str(model_path),
            "motion": str(motion_path),
            "commands": [],
        }
        if not model_path.is_file() or not motion_path.is_file():
            case["status"] = "fail"
            case["error"] = "model or motion missing"
            report["blockers"].append("PMX+VMD runtime import fixture is missing")
        else:
            import_result = _run(
                [str(cli), "import", str(model_path), str(motion_path), "--frames", "0", "--json"],
                root,
                timeout,
            )
            case["commands"] = [import_result]
            import_error = _command_evidence_error(import_result, "runtime_import", "summary")
            if import_error:
                _record_case_failure(report, case, "PMX+VMD runtime import failed", [import_error])
            else:
                case["status"] = "pass"
        report["cases"].append(case)

    if report["blockers"]:
        report["status"] = "fail"
    case_counts = {"total": len(report["cases"]), "pass": 0, "fail": 0}
    for case in report["cases"]:
        case_status = case.get("status")
        if case_status in ("pass", "fail"):
            case_counts[case_status] += 1
    report["summary"] = {
        "cases": case_counts,
        "blockers": len(report["blockers"]),
    }
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point for local or release-gate evidence generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", help="mmd-anim executable path")
    parser.add_argument("--asset", action="append", type=Path, help="asset to inspect and roundtrip (repeatable)")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--motion", type=Path, default=DEFAULT_MOTION)
    parser.add_argument("--expected-cli-version", default=DEFAULT_EXPECTED_CLI_VERSION)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path, default=Path("build/reports/export_validation/mmd_anim_gate.json"))
    parser.add_argument("--strict", action="store_true", help="return non-zero when any blocker is recorded")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    cli = _resolve_cli(root, args.cli)
    assets = tuple(args.asset or DEFAULT_ASSETS)
    report = build_report(
        root,
        cli,
        assets,
        args.model,
        args.motion,
        args.expected_cli_version,
        args.timeout,
    )
    output_path = args.out if args.out.is_absolute() else root / args.out
    output_path = _normalize_output_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "out": str(output_path), "blockers": report["blockers"]}, ensure_ascii=False))
    return 1 if args.strict and report["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())
