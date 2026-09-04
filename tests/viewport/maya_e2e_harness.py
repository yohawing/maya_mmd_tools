"""Shared host-side Maya commandPort E2E orchestration."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from tests.common import maya_commandport

LOG_POLL_INTERVAL = 0.5


def _print_log_line(line: str) -> None:
    """Stream one log line without letting a narrow Windows console abort E2E."""

    try:
        print(line, end="")
    except UnicodeEncodeError:
        # Escape the complete line rather than retrying the console's partial
        # code page.  This keeps the fallback portable across cp932 and other
        # narrow Windows streams while preserving the UTF-8 report on disk.
        safe_line = line.encode("ascii", errors="backslashreplace").decode("ascii")
        print(safe_line, end="")


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
            _print_log_line(line)
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
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Launch Maya with isolated preferences, run a probe, and close it."""
    maya_commandport.remove_stale_logs(stale_paths)
    proc = None
    maya_process_id: int | None = None
    maya_owned = False
    profile_owned = False
    process_exited = False
    maya_app_dir = (out_dir / f"maya-app-{version}-{port}").resolve()
    commandport_script = (out_dir / f"commandport_{port}.mel").resolve()
    try:
        if maya_commandport.is_port_open(port):
            raise RuntimeError(port_error or f"commandPort :{port} is already open")
        if sys.platform == "win32":
            existing_process_id = maya_commandport.find_maya_process_id(
                commandport_script
            )
            if existing_process_id is not None:
                raise RuntimeError(
                    f"Maya process {existing_process_id} already owns {commandport_script}"
                )
        shutil.rmtree(maya_app_dir, ignore_errors=True)
        profile_owned = True
        maya_commandport.seed_isolated_maya_profile(maya_app_dir, version, project_root)
        launch_env = dict(env_overrides or {})
        launch_env["MAYA_APP_DIR"] = str(maya_app_dir)
        launch_deadline = time.time() + launch_timeout
        proc = maya_commandport.launch_maya(
            version=version,
            project_root=project_root,
            output_dir=out_dir,
            port=port,
            launch_mode="explorer" if sys.platform == "win32" else "direct",
            # Never allow an automated Maya shutdown to rewrite the user's
            # pluginPrefs.mel or other Documents/maya preferences.
            env_overrides=launch_env,
        )
        maya_owned = True
        if sys.platform == "win32":
            if proc is None:
                maya_process_id = maya_commandport.wait_for_maya_process_id(
                    commandport_script,
                    timeout=max(0.0, launch_deadline - time.time()),
                )
            else:
                maya_process_id = int(proc.pid)
        maya_commandport.wait_for_port(
            port,
            timeout=max(0.0, launch_deadline - time.time()),
            process=proc,
        )
        if log_ready is not None:
            log_ready.info("fresh Maya commandPort :%d ready", port)
            if warn_detached and proc is None:
                log_ready.warning(
                    "Explorer launch is detached; commandPort ownership is checked by its exact script PID and TCP listener"
                )
        # Every commandPort probe gets the same process-level QSettings
        # boundary before its production UI imports or widget constructors.
        # The bootstrap is inside Maya, where PySide/Maya are available, and
        # remains active if the probe later times out or is force-terminated.
        settings_bootstrap = (
            "import sys\n"
            "from pathlib import Path\n"
            f"project_root = Path({str(project_root.resolve().as_posix())!r})\n"
            "if str(project_root) not in sys.path:\n"
            "    sys.path.insert(0, str(project_root))\n"
            "from tests.common.qsettings_isolation import activate_qsettings_isolation\n"
            "activate_qsettings_isolation()\n"
        )
        if sys.platform == "win32":
            if maya_process_id is None or maya_commandport.query_maya_process_for_script(
                maya_process_id, commandport_script
            ) is not True:
                raise RuntimeError(
                    f"Maya process ownership is not verified for {commandport_script}"
                )
            if not maya_commandport.is_commandport_owned_by_process(
                port, maya_process_id
            ):
                raise RuntimeError(
                    f"commandPort :{port} is not owned by Maya process {maya_process_id}"
                )
        maya_commandport.send_python(
            port,
            settings_bootstrap + command,
            label=send_label,
        )
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
        pending_error = sys.exc_info()[1]
        if maya_owned:
            try:
                detached_process_owned = True
                detached_listener_owned = True
                if proc is None and sys.platform == "win32":
                    try:
                        detached_process_owned = (
                            maya_process_id is not None
                            and maya_commandport.query_maya_process_for_script(
                                maya_process_id, commandport_script
                            ) is True
                        )
                    except Exception:
                        detached_process_owned = False
                    if detached_process_owned:
                        try:
                            detached_listener_owned = (
                                maya_commandport.is_commandport_owned_by_process(
                                    port, maya_process_id
                                )
                            )
                        except Exception:
                            detached_listener_owned = False
                if detached_process_owned and detached_listener_owned:
                    maya_commandport.quit_maya(port)
                    time.sleep(quit_delay)
            finally:
                try:
                    if (
                        proc is None
                        and maya_process_id is not None
                        and detached_process_owned
                    ):
                        try:
                            process_exited = maya_commandport.wait_for_maya_process_exit(
                                maya_process_id,
                                commandport_script,
                                timeout=30,
                            )
                        except Exception:
                            process_exited = False
                        if not process_exited and terminate_process:
                            try:
                                process_exited = maya_commandport.terminate_maya_process(
                                    maya_process_id,
                                    commandport_script,
                                )
                            except Exception:
                                process_exited = False
                    elif proc is None and sys.platform != "win32":
                        try:
                            maya_commandport.wait_for_port_close(port, timeout=30)
                            process_exited = True
                        except Exception:
                            process_exited = False
                    elif proc is not None and proc.poll() is not None:
                        process_exited = True
                    elif proc is not None and terminate_process:
                        proc.terminate()
                        proc.wait(timeout=30)
                        process_exited = True
                finally:
                    maya_commandport.close_process_logs(proc)
        if profile_owned and (not maya_owned or process_exited):
            shutil.rmtree(maya_app_dir, ignore_errors=True)
        if maya_owned and not process_exited and pending_error is None:
            raise RuntimeError(
                f"Maya process exit was not verified for {commandport_script}"
            )
