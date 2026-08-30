"""Fail-closed host runner for the VMD clear-existing-motion matrix.

The matrix has four route checks for each of Maya 2024/2026 and DG/Serial/
Parallel evaluation: 24 child processes by default.  Each child owns one
disposable ``MAYA_APP_DIR`` and runs the sibling standalone probe in a fresh
scene.  A missing, skipped, ``not_run``, blocked, malformed, or failed child
never contributes a green result.

Usage::

    python -m tests.viewport.vmd_clear_existing_motion_e2e_matrix --dry-run
    python -m tests.viewport.vmd_clear_existing_motion_e2e_matrix \
        --versions 2024,2026 --modes dg,serial,parallel
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
MODULE = "tests.viewport.vmd_clear_existing_motion_e2e"
VERSIONS = ("2024", "2026")
MODES = ("dg", "serial", "parallel")
ROUTES = ("legacy", "animation_layer", "control_rig", "bake")


def _parse_csv(value: str, choices: Sequence[str], label: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value).split(",") if item.strip())
    invalid = sorted(set(items) - set(choices))
    if not items or invalid:
        raise ValueError(f"invalid {label}: {invalid or value!r}; choices={tuple(choices)!r}")
    return items


def _mayapy(version: str) -> Path:
    override = os.environ.get(f"MAYAPY_{version}")
    return Path(override) if override else Path(f"C:/Program Files/Autodesk/Maya{version}/bin/mayapy.exe")


def _child_path(output: Path, version: str, mode: str, route: str) -> Path:
    suffix = output.suffix or ".json"
    return output.with_name(f"{output.stem}_maya{version}_{mode}_{route}{suffix}")


def _command(*, mayapy: Path, version: str, mode: str, route: str, report: Path) -> list[str]:
    return [
        str(mayapy),
        "-m",
        MODULE,
        "--maya",
        str(version),
        "--evaluation-mode",
        mode,
        "--route",
        route,
        "--out",
        str(report),
    ]


def _contains_forbidden_status(value: Any) -> bool:
    """Reject nested evidence that accidentally converted a missing gate green."""

    if isinstance(value, Mapping):
        status = str(value.get("status", "")).lower()
        if status in {"skip", "skipped", "not_run", "blocked"}:
            return True
        return any(_contains_forbidden_status(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_status(item) for item in value)
    return False


def _validate_child(
    payload: Mapping[str, Any],
    *,
    version: str,
    mode: str,
    route: str,
    returncode: int,
    report: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    requested = payload.get("requested")
    if not isinstance(requested, Mapping):
        errors.append("missing requested matrix coordinates")
    else:
        if str(requested.get("maya")) != version:
            errors.append(f"Maya request mismatch: {requested.get('maya')!r}")
        if str(requested.get("evaluationMode")) != mode:
            errors.append(f"evaluation request mismatch: {requested.get('evaluationMode')!r}")
        if str(requested.get("route")) != route:
            errors.append(f"route request mismatch: {requested.get('route')!r}")
    if str(payload.get("mayaVersion", "")).split(".", 1)[0] != version:
        errors.append(f"Maya version mismatch: {payload.get('mayaVersion')!r}")
    evaluation = payload.get("evaluationMode")
    if not isinstance(evaluation, Mapping) or str(evaluation.get("active")) != mode or not bool(evaluation.get("pass")):
        errors.append("evaluation mode was not read back green")
    if str(payload.get("status")) != "pass":
        errors.append(f"child status is not pass: {payload.get('status')!r}")
    checks = payload.get("checks")
    if not isinstance(checks, Mapping):
        errors.append("missing checks object")
    else:
        for name, check in checks.items():
            if not isinstance(check, Mapping) or not bool(check.get("pass")):
                errors.append(f"check failed: {name}")
    profile = payload.get("profile")
    motion_clear = profile.get("motion_clear") if isinstance(profile, Mapping) else None
    if not isinstance(motion_clear, Mapping):
        errors.append("missing profile.motion_clear")
    else:
        for field in ("requested", "effective", "before", "after"):
            if not isinstance(motion_clear.get(field), Mapping):
                errors.append(f"profile.motion_clear.{field} missing")
        if str(motion_clear.get("status")) != "success":
            errors.append(f"profile.motion_clear.status is not success: {motion_clear.get('status')!r}")
    if _contains_forbidden_status(payload):
        errors.append("child evidence contains skip/not_run/blocked status")
    if returncode != 0:
        errors.append(f"child returned exit={returncode}")
    return {
        "version": version,
        "mode": mode,
        "route": route,
        "report": str(report),
        "status": str(payload.get("status", "missing")),
        "returncode": int(returncode),
        "valid": not errors,
        "errors": errors,
        "checks": checks if isinstance(checks, Mapping) else {},
    }


def _run_one(*, version: str, mode: str, route: str, output: Path, timeout: float) -> dict[str, Any]:
    report = _child_path(output, version, mode, route)
    mayapy = _mayapy(version)
    command = _command(mayapy=mayapy, version=version, mode=mode, route=route, report=report)
    row: dict[str, Any] = {
        "version": version,
        "mode": mode,
        "route": route,
        "report": str(report),
        "mayapy": str(mayapy),
        "command": command,
    }
    if not mayapy.is_file():
        row.update({"valid": False, "returncode": None, "errors": [f"mayapy not found: {mayapy}"]})
        return row
    try:
        report.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        row.update({"valid": False, "returncode": None, "errors": [f"stale report cleanup failed: {exc}"]})
        return row
    host_dir = Path(tempfile.mkdtemp(prefix=f"mmd_vmd_clear_maya{version}_"))
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "MAYA_APP_DIR": str(host_dir),
        "MAYA_SKIP_USERSETUP_PY": "1",
    }
    log = report.with_suffix(".log")
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text((exc.stdout or "") + (exc.stderr or ""), encoding="utf-8")
        row.update({"valid": False, "returncode": None, "log": str(log), "errors": [f"child timeout after {timeout}s"]})
        return row
    except OSError as exc:
        row.update({"valid": False, "returncode": None, "errors": [f"child launch failed: {exc}"]})
        return row
    finally:
        try:
            shutil.rmtree(str(host_dir))
        except OSError:
            # A locked disposable Maya preference file is diagnostic only;
            # never broaden cleanup to another directory.
            row.setdefault("cleanupWarnings", []).append(f"could not remove disposable MAYA_APP_DIR: {host_dir}")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    row.update({"returncode": completed.returncode, "log": str(log), "stdoutTail": completed.stdout[-1000:]})
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except FileNotFoundError:
        row.update({"valid": False, "errors": [f"child report missing: {report}"]})
        return row
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        row.update({"valid": False, "errors": [f"malformed child report: {exc}"]})
        return row
    if not isinstance(payload, Mapping):
        row.update({"valid": False, "errors": ["child report root is not a JSON object"]})
        return row
    row.update(_validate_child(payload, version=version, mode=mode, route=route, returncode=completed.returncode, report=report))
    return row


def _aggregate(rows: Sequence[Mapping[str, Any]], *, versions: Sequence[str], modes: Sequence[str], routes: Sequence[str], dry_run: bool) -> dict[str, Any]:
    expected = {(version, mode, route) for version in versions for mode in modes for route in routes}
    actual = {(str(row.get("version")), str(row.get("mode")), str(row.get("route"))) for row in rows}
    complete = len(rows) == len(expected) and actual == expected
    valid = complete and all(bool(row.get("valid")) for row in rows)
    return {
        "kind": "vmd-clear-existing-motion-e2e-matrix",
        "schema": 1,
        "status": "dry_run" if dry_run else ("pass" if valid else "fail"),
        "expectedCount": len(expected),
        "runCount": len(rows),
        "complete": complete,
        "valid": valid,
        "coordinates": {
            "versions": list(versions),
            "modes": list(modes),
            "routes": list(routes),
        },
        "runs": list(rows),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versions", default=",".join(VERSIONS))
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--routes", default=",".join(ROUTES))
    parser.add_argument("--out", default=str(ROOT / "build" / "reports" / "vmd_clear_existing_motion_e2e_matrix.json"))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        versions = _parse_csv(args.versions, VERSIONS, "versions")
        modes = _parse_csv(args.modes, MODES, "modes")
        routes = _parse_csv(args.routes, ROUTES, "routes")
    except ValueError as exc:
        parser.error(str(exc))
    output = Path(args.out).resolve()
    rows = []
    for version in versions:
        for mode in modes:
            for route in routes:
                report = _child_path(output, version, mode, route)
                command = _command(mayapy=_mayapy(version), version=version, mode=mode, route=route, report=report)
                if args.dry_run:
                    rows.append({"version": version, "mode": mode, "route": route, "command": command, "report": str(report), "valid": False})
                else:
                    rows.append(_run_one(version=version, mode=mode, route=route, output=output, timeout=args.timeout))
    aggregate = _aggregate(rows, versions=versions, modes=modes, routes=routes, dry_run=args.dry_run)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": aggregate["status"], "report": str(output), "runCount": aggregate["runCount"], "expectedCount": aggregate["expectedCount"]}, ensure_ascii=False))
    return 0 if aggregate["status"] in {"pass", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
