"""Shared helpers for compact test-runner output with complete log retention."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable


_WARNING_MARKERS = ("warning", "warn:", "警告")


def _warning_key(line: str) -> str | None:
    normalized = re.sub(r"\s+", " ", line.strip())
    lowered = normalized.casefold()
    if normalized and any(marker in lowered for marker in _WARNING_MARKERS):
        return normalized
    return None


def write_full_log(path: Path, command: Iterable[str], output: str) -> Path:
    """Write a complete subprocess transcript and return its resolved path."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    command_text = shlex.join(str(part) for part in command)
    transcript = f"Command: {command_text}\n\n{output}"
    if transcript and not transcript.endswith("\n"):
        transcript += "\n"
    path.write_text(transcript, encoding="utf-8", errors="replace")
    return path


def repeated_warning_summary(output: str) -> tuple[int, int]:
    """Return ``(unique warnings, repeated lines hidden from compact output)``."""
    warnings = []
    for line in output.splitlines():
        warning = _warning_key(line)
        if warning is not None:
            warnings.append(warning)
    counts = Counter(warnings)
    return len(counts), sum(count - 1 for count in counts.values())


def repeated_warning_summary_from_log(path: Path) -> tuple[int, int]:
    """Count repeated warnings from a UTF-8 transcript without loading it all."""
    counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            warning = _warning_key(line)
            if warning is not None:
                counts[warning] += 1
    return len(counts), sum(count - 1 for count in counts.values())


@contextmanager
def full_log_writer(path: Path, command: Iterable[str]):
    """Yield a UTF-8 text stream initialized with the executed command."""
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8", errors="replace") as handle:
        handle.write(f"Command: {shlex.join(str(part) for part in command)}\n\n")
        handle.flush()
        yield handle, resolved


def run_logged_subprocess(
    command: list[str],
    *,
    log_path: Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    verbose: bool = False,
) -> tuple[int, Path, tuple[int, int]]:
    """Stream a child process to a complete log with constant output memory."""
    warning_counts: Counter[str] = Counter()
    with full_log_writer(log_path, command) as (handle, resolved):
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                handle.write(line)
                warning = _warning_key(line)
                if warning is not None:
                    warning_counts[warning] += 1
                if verbose:
                    print(line, end="")
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        finally:
            process.stdout.close()
    warning_summary = (
        len(warning_counts),
        sum(count - 1 for count in warning_counts.values()),
    )
    return returncode, resolved, warning_summary


def compact_failure_details_from_log(path: Path) -> tuple[str | None, list[str]]:
    """Extract child-runner failure details from a compact nested transcript."""
    first_failure = None
    first_diagnostic = None
    failed_names: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("Command:") and first_diagnostic is None:
                first_diagnostic = stripped
            if first_failure is None and "] first failure: " in stripped:
                first_failure = stripped.split("] first failure: ", 1)[1]
            for marker in ("] failed tests: ", "] failed gates: "):
                if marker in stripped:
                    failed_names.extend(
                        name.strip()
                        for name in stripped.split(marker, 1)[1].split(",")
                        if name.strip()
                    )
    return first_failure or first_diagnostic, list(dict.fromkeys(failed_names))


def format_summary(
    gate: str,
    *,
    total: int,
    passed: int,
    skipped: int,
    failed: int,
    duration_sec: float,
) -> str:
    """Format one stable, machine-scannable terminal summary line."""
    return (
        f"[{gate}] tests={total} pass={passed} skip={skipped} "
        f"fail={failed} duration={duration_sec:.2f}s"
    )


def safe_log_name(gate: str) -> str:
    """Return a filesystem-safe log stem for a gate name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", gate).strip("-.")
    return name or "gate"


def summarize_unittest_result(result) -> tuple[dict[str, int], list[str]]:
    """Summarize a unittest result by top-level test, not failing subTest count."""
    def top_level_id(test):
        return getattr(test, "test_case", test).id()

    failed_tests = list(
        dict.fromkeys(
            [top_level_id(test) for test, _ in (*result.failures, *result.errors)]
            + [top_level_id(test) for test in result.unexpectedSuccesses]
        )
    )
    failed_set = set(failed_tests)
    skipped_tests = list(
        dict.fromkeys(
            [top_level_id(test) for test, _ in result.skipped]
            + [top_level_id(test) for test, _ in result.expectedFailures]
        )
    )
    skipped = sum(test_id not in failed_set for test_id in skipped_tests)
    failed = len(failed_tests)
    passed = max(0, result.testsRun - skipped - failed)
    return {
        "tests": result.testsRun,
        "pass": passed,
        "skip": skipped,
        "fail": failed,
    }, failed_tests
