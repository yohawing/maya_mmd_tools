"""Release-gate validation, execution, and report helpers independent of Nox sessions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4


def _release_gate_version_check(root: Path, expected_version: str | None = None) -> None:
    """Validate release version markers before running expensive gates."""
    import tomllib

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    if expected_version and version != expected_version:
        raise RuntimeError(f"pyproject.toml version {version} does not match requested release version {expected_version}")

    init_text = (root / "mmd_tools" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if not init_match or init_match.group(1) != version:
        raise RuntimeError(f"mmd_tools/__init__.py version does not match pyproject.toml: {version}")

    mod_text = (root / "maya_mmd_tools.mod").read_text(encoding="utf-8")
    mod_versions = set(re.findall(r"maya_mmd_tools\s+([0-9]+\.[0-9]+\.[0-9]+)", mod_text))
    if mod_versions != {version}:
        raise RuntimeError(f"maya_mmd_tools.mod versions {sorted(mod_versions)} do not match {version}")

    plugin_text = (root / "cpp" / "src" / "pluginMain.cpp").read_text(encoding="utf-8")
    plugin_match = re.search(
        r'MFnPlugin\s+plugin\s*\(\s*obj\s*,\s*"[^"]+"\s*,\s*"([^"]+)"',
        plugin_text,
    )
    if not plugin_match or plugin_match.group(1) != version:
        raise RuntimeError(f"cpp/src/pluginMain.cpp version does not match pyproject.toml: {version}")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = f"## [{version}]"
    start = changelog.find(heading)
    if start == -1:
        raise RuntimeError(f"CHANGELOG.md is missing {heading}")
    next_heading = changelog.find("\n## [", start + len(heading))
    section = changelog[start: next_heading if next_heading != -1 else len(changelog)]
    body_lines = [
        line.strip()
        for line in section.splitlines()[1:]
        if line.strip() and not line.strip().startswith("[")
    ]
    if not body_lines:
        raise RuntimeError(f"CHANGELOG.md section {heading} is empty")


def _release_gate_mmd_anim_pin_check(root: Path, run_process) -> None:
    """Require the checked-out mmd-anim HEAD to match the parent gitlink."""
    relative_path = "external/mmd-anim"
    submodule = root / "external" / "mmd-anim"
    if not submodule.is_dir() or not (submodule / ".git").exists():
        raise RuntimeError(
            f"{relative_path} is not initialized; release provenance cannot be verified. "
            "Initialize the pinned submodule before running release_gate."
        )

    def git_output(arguments: list[str], cwd: Path) -> str:
        try:
            completed = run_process(
                ["git", *arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git executable is unavailable; release provenance cannot be verified."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Failed to verify {relative_path} release provenance with "
                f"git {' '.join(arguments)}{suffix}"
            )
        return completed.stdout.rstrip("\r\n")

    gitlink_line = git_output(["ls-tree", "HEAD", "--", relative_path], root)
    gitlink_match = re.fullmatch(
        rf"160000 commit ([0-9a-fA-F]{{40,64}})\t{re.escape(relative_path)}",
        gitlink_line,
    )
    if gitlink_match is None:
        raise RuntimeError(
            f"Parent HEAD does not contain a valid gitlink for {relative_path}; "
            "release provenance cannot be verified."
        )
    parent_head = gitlink_match.group(1).lower()

    checkout_head = git_output(["rev-parse", "--verify", "HEAD"], submodule).lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", checkout_head) is None:
        raise RuntimeError(
            f"{relative_path} returned an invalid checkout HEAD {checkout_head!r}; "
            "release provenance cannot be verified."
        )
    if checkout_head != parent_head:
        raise RuntimeError(
            f"{relative_path} pin mismatch: parent gitlink={parent_head}, "
            f"checkout HEAD={checkout_head}. Restore or initialize the pinned submodule "
            "before running release_gate; automatic checkout/reset is intentionally disabled."
        )
    dirty_status = git_output(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        submodule,
    )
    if dirty_status:
        status_summary = dirty_status.replace("\r\n", "\n").replace("\n", "; ")
        raise RuntimeError(
            f"{relative_path} worktree is dirty; release provenance cannot be verified. "
            f"Git status: {status_summary}. Commit, stash, or remove these changes before "
            "running release_gate; automatic cleanup is intentionally disabled."
        )


def _run_release_gate_command(
    name: str,
    command: list[str],
    results: list[dict[str, object]],
    *,
    root: Path,
    run_logged_subprocess,
    safe_log_name,
    compact_failure_details_from_log,
    format_test_summary,
    result_report: Path | None = None,
    required_local: bool = False,
    strict_local: bool = False,
    verbose: bool = False,
) -> None:
    """Run a command quietly, retain its transcript, and record its result."""
    started = perf_counter()
    if result_report is not None and result_report.exists():
        result_report.unlink()
    returncode, log_path, (_, repeated_warnings) = run_logged_subprocess(
        command,
        log_path=root / "build" / "reports" / "release_gate" / f"{safe_log_name(name)}.log",
        cwd=root,
        verbose=verbose,
    )
    status = "pass" if returncode == 0 else "fail"
    detail = None
    if result_report is not None and result_report.is_file():
        try:
            child_status = str(json.loads(result_report.read_text(encoding="utf-8")).get("status", "")).lower()
        except (OSError, ValueError, TypeError) as exc:
            status = "fail"
            detail = f"invalid child report {result_report}: {exc}"
        else:
            status_aliases = {"pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail", "skip": "skip", "skipped": "skip"}
            if child_status not in status_aliases:
                status = "fail"
                detail = f"invalid child status in {result_report}: {child_status!r}"
            elif returncode == 0:
                status = status_aliases[child_status]
    if status == "skip" and required_local and strict_local:
        status = "fail"
        detail = "required local gate skipped under --strict-local"
    duration_sec = round(perf_counter() - started, 3)
    first_failure, failed_tests = compact_failure_details_from_log(log_path)
    result = {
        "name": name,
        "command": command,
        "status": status,
        "returncode": returncode,
        "duration_sec": duration_sec,
        "log": str(log_path),
        "repeated_warnings_suppressed": repeated_warnings,
        **({"first_failure": first_failure} if first_failure else {}),
        **({"failed_tests": failed_tests} if failed_tests else {}),
        **({"detail": detail} if detail else {}),
    }
    results.append(result)
    print(
        format_test_summary(
            name,
            total=1,
            passed=int(status == "pass"),
            skipped=int(status == "skip"),
            failed=int(status == "fail"),
            duration_sec=duration_sec,
        )
    )
    if repeated_warnings and not verbose:
        print(f"[{name}] repeated warnings suppressed from terminal: {repeated_warnings}")
    if status == "fail":
        print(f"[{name}] first failure: {first_failure or name}")
        if failed_tests:
            print(f"[{name}] failed tests: {', '.join(failed_tests)}")
        print(f"[{name}] full log: {log_path}")


def _run_release_gate_callable(
    name: str,
    func,
    results: list[dict[str, object]],
    *,
    format_test_summary,
) -> None:
    """Run an in-process release-gate step and append a keep-going result entry."""
    started = perf_counter()
    try:
        func()
    except Exception as exc:
        result = {
                "name": name,
                "command": [],
                "status": "fail",
                "returncode": 1,
                "duration_sec": round(perf_counter() - started, 3),
                "error": str(exc),
            }
    else:
        result = {
                "name": name,
                "command": [],
                "status": "pass",
                "returncode": 0,
                "duration_sec": round(perf_counter() - started, 3),
            }
    results.append(result)
    print(
        format_test_summary(
            name,
            total=1,
            passed=int(result["status"] == "pass"),
            skipped=0,
            failed=int(result["status"] == "fail"),
            duration_sec=float(result["duration_sec"]),
        )
    )
    if result["status"] == "fail":
        print(f"[{name}] first failure: {result.get('error', name)}")


def _release_gate_failure_label(result: dict[str, object]) -> str:
    """Return the best available compact failure detail for an aggregate gate."""
    return str(
        result.get("first_failure")
        or result.get("error")
        or result.get("name")
        or "unknown failure"
    )


def _new_release_gate_run() -> tuple[str, str]:
    """Return a unique run ID and an RFC 3339 UTC timestamp for reports."""
    started_at = datetime.now(timezone.utc)
    timestamp = started_at.isoformat()
    run_id = f"{started_at.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    return run_id, timestamp


def _write_release_gate_reports(
    root: Path,
    results: list[dict[str, object]],
    quick: bool,
    *,
    run_id: str | None = None,
    timestamp: str | None = None,
    duration_sec: float | None = None,
) -> tuple[Path, Path]:
    """Write release-gate Markdown and JSON summaries."""
    run_id, timestamp = (run_id, timestamp) if run_id and timestamp else _new_release_gate_run()
    report_dir = root / "build" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "release_gate.json"
    md_path = report_dir / "release_gate.md"

    counts = {status: sum(result["status"] == status for result in results) for status in ("pass", "fail", "skip")}
    aggregate_status = "fail" if counts["fail"] else "pass" if counts["pass"] else "skip"
    payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "quick": quick,
        "status": aggregate_status,
        "summary": counts,
        "log_dir": str(report_dir / "release_gate"),
        "results": results,
    }
    if duration_sec is not None:
        payload["duration_sec"] = round(float(duration_sec), 3)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Release Gate",
        "",
        f"- Run ID: {run_id}",
        f"- Timestamp: {timestamp}",
        f"- Mode: {'quick' if quick else 'full'}",
        f"- Status: {payload['status']}",
        f"- Summary: pass={counts['pass']}, fail={counts['fail']}, skip={counts['skip']}",
        f"- Log directory: {payload['log_dir']}",
    ]
    if duration_sec is not None:
        lines.append(f"- Duration (seconds): {payload['duration_sec']}")
    lines.extend(
        [
            "",
        "| Step | Status | Seconds | Command |",
        "| --- | --- | ---: | --- |",
        ]
    )
    for result in results:
        command = " ".join(str(part) for part in result.get("command") or [])
        if not command:
            command = str(result.get("error", "in-process"))
        lines.append(
            f"| {result['name']} | {result['status']} | {result['duration_sec']} | `{command}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def _normalize_local_gate_report(
    report_path: Path,
    strict_local: bool,
    markdown_path: Path | None = None,
) -> str:
    """Derive and persist a local child gate status in its JSON and Markdown reports."""
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Local gate report has no results list: {report_path}")
    aliases = {"pass": "pass", "passed": "pass", "fail": "fail", "failed": "fail", "skip": "skip", "skipped": "skip"}
    statuses = []
    for result in results:
        raw_status = str(result.get("status", "")).lower() if isinstance(result, dict) else ""
        if raw_status not in aliases:
            raise ValueError(f"Invalid local gate result status in {report_path}: {raw_status!r}")
        statuses.append(aliases[raw_status])
    if "fail" in statuses or not statuses:
        status = "fail"
    elif "pass" in statuses:
        status = "pass"
    else:
        status = "fail" if strict_local else "skip"
    payload["status"] = status
    summary = {
        candidate: statuses.count(candidate) for candidate in ("pass", "fail", "skip")
    }
    payload["summary"] = summary
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None and markdown_path.is_file():
        lines = markdown_path.read_text(encoding="utf-8").splitlines()
        status_line = f"- Status: {status}"
        summary_line = (
            f"- Summary: pass={summary['pass']}, fail={summary['fail']}, skip={summary['skip']}"
        )
        status_index = next((index for index, line in enumerate(lines) if line.startswith("- Status:")), None)
        if status_index is None:
            lines.extend(["", status_line, summary_line])
        else:
            lines[status_index] = status_line
            if status_index + 1 < len(lines) and lines[status_index + 1].startswith("- Summary:"):
                lines[status_index + 1] = summary_line
            else:
                lines.insert(status_index + 1, summary_line)
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status
