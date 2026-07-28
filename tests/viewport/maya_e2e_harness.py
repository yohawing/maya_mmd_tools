"""Shared host-side Maya commandPort E2E orchestration."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from tests.common import maya_commandport

LOG_POLL_INTERVAL = 0.5


def monitor_result(
    log_path: Path,
    report_path: Path,
    marker: str,
    timeout: float,
    *,
    wait_report_timeout: float = 30.0,
    verify_status: bool = True,
    report_error: str | None = None,
) -> dict[str, Any]:
    """Stream a probe log until ``marker`` and load its JSON report."""
    log_path.touch(exist_ok=True)
    result: dict[str, Any] | None = None
    deadline = time.time() + timeout
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0)
        while True:
            if time.time() >= deadline:
                raise TimeoutError(f"timed out waiting for completion marker: {log_path}")
            line = handle.readline()
            if not line:
                time.sleep(LOG_POLL_INTERVAL)
                continue
            print(line, end="")
            if verify_status and line.strip().startswith("RESULT_JSON:"):
                result = json.loads(line.split("RESULT_JSON:", 1)[1].strip())
            if marker in line:
                break
    deadline = time.time() + wait_report_timeout
    while wait_report_timeout > 0 and not report_path.is_file() and time.time() < deadline:
        time.sleep(LOG_POLL_INTERVAL)
    if not report_path.is_file():
        raise TimeoutError(report_error or f"missing report: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if verify_status and result is not None and result.get("status") != report.get("status"):
        raise RuntimeError("Maya RESULT_JSON and report status disagree")
    return report


def run_maya_e2e(
    *,
    project_root: Path,
    version: str,
    out_dir: Path,
    port: int,
    timeout: float,
    log_path: Path,
    report_path: Path,
    command: str,
    marker: str,
    send_label: str,
    stale_paths: Iterable[Path] = (),
    launch_timeout: float = 120.0,
    wait_report_timeout: float = 30.0,
    verify_status: bool = True,
    terminate_process: bool = True,
    quit_delay: float = 3.0,
    port_error: str | None = None,
    report_error: str | None = None,
    log_ready: Any = None,
    warn_detached: bool = False,
) -> dict[str, Any]:
    """Launch Maya, run one commandPort probe, and close the owned process."""
    maya_commandport.remove_stale_logs(stale_paths)
    proc = None
    maya_owned = False
    try:
        if maya_commandport.is_port_open(port):
            raise RuntimeError(port_error or f"commandPort :{port} is already open")
        proc = maya_commandport.launch_maya(
            version=version,
            project_root=project_root,
            output_dir=out_dir,
            port=port,
            launch_mode="explorer" if sys.platform == "win32" else "direct",
        )
        maya_owned = True
        maya_commandport.wait_for_port(port, timeout=launch_timeout, process=proc)
        if log_ready is not None:
            log_ready.info("fresh Maya commandPort :%d ready", port)
            if warn_detached and proc is None:
                log_ready.warning(
                    "Explorer launch is detached; commandPort ownership is guarded by the preflight only"
                )
        maya_commandport.send_python(port, command, label=send_label)
        return monitor_result(
            log_path,
            report_path,
            marker,
            timeout,
            wait_report_timeout=wait_report_timeout,
            verify_status=verify_status,
            report_error=report_error,
        )
    finally:
        if maya_owned:
            try:
                maya_commandport.quit_maya(port)
                time.sleep(quit_delay)
            finally:
                if terminate_process and proc is not None and proc.poll() is None:
                    proc.terminate()
                maya_commandport.close_process_logs(proc)
