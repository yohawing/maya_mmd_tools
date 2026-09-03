"""Resolve and run the pure-Python unit-test subset inside one uvx process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from tools.nox.native import _is_expected_environment_import_failure


PROBE_TIMEOUT_SECONDS = 30


def _discover_unit_modules(root: Path) -> list[Path]:
    """Return sorted unit-test module files from the repository checkout."""
    unit_dir = root / "tests" / "unit"
    if not unit_dir.is_dir():
        raise RuntimeError(f"ci_unit: test directory not found: {unit_dir}")
    return sorted(unit_dir.glob("test_*.py"))


def _probe_import(
    module_name: str,
    *,
    root: Path,
    run_process=None,
) -> tuple[bool, str]:
    """Probe one module in a fresh child interpreter and return status/details."""
    process_runner = subprocess.run if run_process is None else run_process
    try:
        probe = process_runner(
            [sys.executable, "-c", f"import {module_name}"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"import probe timed out after {PROBE_TIMEOUT_SECONDS}s: {exc}"
    except OSError as exc:
        return False, f"import probe could not start: {exc}"

    if probe.returncode == 0:
        return True, ""
    stderr = probe.stderr or ""
    if _is_expected_environment_import_failure(stderr):
        return False, "environment-only dependency"
    return False, stderr.strip()[-2000:] or f"import probe exited with code {probe.returncode}"


def run_ci_unit(
    root: Path | None = None,
    *,
    run_process=None,
    pytest_main: Callable[[Sequence[str]], object] | None = None,
) -> int:
    """Classify discovered modules and run pytest once for importable modules."""
    checkout = Path.cwd() if root is None else Path(root)
    try:
        module_files = _discover_unit_modules(checkout)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    importable: list[str] = []
    skipped: list[str] = []
    classification_lines: list[str] = []
    for py_file in module_files:
        module_name = f"tests.unit.{py_file.stem}"
        is_importable, detail = _probe_import(
            module_name,
            root=checkout,
            run_process=run_process,
        )
        if is_importable:
            importable.append(module_name)
            classification_lines.append(f"[ci_unit] {py_file.name}: importable")
            continue
        if detail == "environment-only dependency":
            skipped.append(py_file.name)
            classification_lines.append(
                f"[ci_unit] {py_file.name}: environment-only (skipped)"
            )
            continue
        print(
            f"[ci_unit] {py_file.name}: import failed for a non-environment reason\n{detail}",
            file=sys.stderr,
        )
        return 1

    print(f"[ci_unit] discovered {len(module_files)} test module(s)")
    for line in classification_lines:
        print(line)
    print(
        f"[ci_unit] classified {len(module_files)} module(s): "
        f"importable={len(importable)} environment-only={len(skipped)}"
    )
    if not importable:
        print("No importable pure-python unit tests found in tests/unit/", file=sys.stderr)
        return 1

    print(f"[ci_unit] running pytest for {len(importable)} importable module(s)")
    if pytest_main is None:
        import pytest

        pytest_main = pytest.main
    try:
        return int(pytest_main(["--pyargs", *importable]))
    except Exception as exc:
        print(f"[ci_unit] pytest execution failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    """Run the unit-test subset from the current checkout."""
    return run_ci_unit()


if __name__ == "__main__":
    raise SystemExit(main())
