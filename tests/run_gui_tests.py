"""
Maya GUI Test Runner using commandPort.

This script launches a Maya instance, executes GUI tests via a commandPort,
and streams the results from a log file.
"""

import sys
import time
import argparse
import json
import logging
import os
import re
import shutil
import secrets
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.common import maya_commandport

# --- Constants ---
DEFAULT_MAYA_VERSION = "2024"
COMMAND_PORT = 7720
LOG_FILE_NAME = "ui_test_results.log"
MAYA_START_TIMEOUT = 120  # seconds
# Maya 2026 can spend more than ten minutes on first-run Flow plugin
# initialization even when the focused GUI suite ultimately passes.
TEST_EXECUTION_TIMEOUT = 900  # seconds
LOG_POLL_INTERVAL = 1  # second
MAYA_PYTHON_READY_TIMEOUT = 120  # seconds
MAYA_PYTHON_READY_POLL_INTERVAL = 0.25  # second
ATTACH_HANDSHAKE_PROTOCOL = "maya_mmd_tools_gui_attach_v1"
# Existing-session attach requires an explicit opt-in from the Maya process;
# clean scene state alone is not treated as proof of a dedicated test session.
ATTACH_SESSION_ENVIRONMENT = "MAYA_MMD_TOOLS_GUI_TEST_SESSION"
ATTACH_SESSION_ID = "maya_mmd_tools_gui_test_v1"
GUI_TEST_FINISHED_MARKER = "//-- GUI TEST FINISHED --//"
GUI_TEST_STATUSES = frozenset(("PASS", "FAIL", "NO_TESTS", "ERROR"))
TIMING_REPORT_SCHEMA_VERSION = 1
BATCH_MANIFEST_SCHEMA_VERSION = 1
BATCH_REPORT_SCHEMA_VERSION = 1
_COMPLETION_RE = re.compile(
    rf"^{re.escape(GUI_TEST_FINISHED_MARKER)}\s+status=(?P<status>[A-Z_]+)\s*$"
)

# --- Logger Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def new_timing_report(maya_version, test_path, test_filter):
    """Return an unstarted, versioned GUI timing report."""
    return {
        "schema_version": TIMING_REPORT_SCHEMA_VERSION,
        "runner": "maya_gui",
        "maya_version": str(maya_version),
        "test_path": test_path,
        "test_filter": test_filter,
        "status": "ERROR",
        "phases": {
            "startup": {"status": "blocked", "elapsed_seconds": None},
            "discovery": {"status": "blocked", "elapsed_seconds": None},
            "tests": {"status": "blocked", "elapsed_seconds": None},
            "shutdown": {"status": "blocked", "elapsed_seconds": None},
        },
        "tests": [],
        "test_counts": {},
        "slowest_tests": [],
    }


def load_batch_manifest(path, project_root):
    """Load one strict, versioned GUI batch manifest."""
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid GUI batch manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "cases"}:
        raise ValueError("GUI batch manifest must contain only schema_version and cases")
    version = payload["schema_version"]
    if type(version) is not int or version != BATCH_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported GUI batch manifest schema_version: {version!r}")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("GUI batch manifest cases must be a non-empty list")

    root = Path(project_root).resolve()
    cases = []
    case_ids = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict) or set(raw_case) != {"id", "test_path", "test_filter"}:
            raise ValueError(f"GUI batch case {index} must contain only id, test_path, and test_filter")
        case_id = raw_case["id"]
        test_path = raw_case["test_path"]
        test_filter = raw_case["test_filter"]
        if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id):
            raise ValueError(f"GUI batch case {index} has an invalid id")
        if case_id in case_ids:
            raise ValueError(f"duplicate GUI batch case id: {case_id}")
        if not isinstance(test_path, str) or not test_path.strip():
            raise ValueError(f"GUI batch case {case_id} has an invalid test_path")
        candidate = (root / test_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"GUI batch case {case_id} test_path escapes project root") from exc
        if Path(test_path).is_absolute():
            raise ValueError(f"GUI batch case {case_id} test_path must be relative")
        if not candidate.is_dir():
            raise ValueError(f"GUI batch case {case_id} test_path is not a directory")
        if not isinstance(test_filter, str) or not test_filter.strip():
            raise ValueError(f"GUI batch case {case_id} has an invalid test_filter")
        case_ids.add(case_id)
        cases.append({"id": case_id, "test_path": test_path, "test_filter": test_filter})
    return cases


def new_batch_report(maya_version, cases):
    """Return the initial host/Maya batch timing report."""
    return {
        "schema_version": BATCH_REPORT_SCHEMA_VERSION,
        "runner": "maya_gui_batch",
        "maya_version": str(maya_version),
        "status": "ERROR",
        "phases": {
            "startup": {"status": "blocked", "elapsed_seconds": None},
            "shutdown": {"status": "blocked", "elapsed_seconds": None},
        },
        "cases": [
            {
                "id": case["id"],
                "test_path": case["test_path"],
                "test_filter": case["test_filter"],
                "status": "NOT_RUN",
                "elapsed_seconds": None,
            }
            for case in cases
        ],
        "case_counts": {"NOT_RUN": len(cases)},
    }


def finalize_batch_report(report):
    """Add deterministic aggregate counts for batch case statuses."""
    counts = {}
    for case in report.get("cases", []):
        status = case.get("status", "NOT_RUN")
        counts[status] = counts.get(status, 0) + 1
    report["case_counts"] = counts
    return report


def finalize_batch_timeout(report, host_elapsed_seconds):
    """Freeze active and unstarted cases after the host owns a batch timeout."""
    report["host_timeout_elapsed_seconds"] = host_elapsed_seconds
    for case in report.get("cases", []):
        if case.get("status") == "RUNNING":
            case["status"] = "TIMEOUT"
            # The aggregate partial does not own a per-case start timestamp.
            # Host elapsed includes earlier completed cases, so leave this
            # unknown instead of double-counting their time.
            case["elapsed_seconds"] = None
            case["timeout_reason"] = "host timed out waiting for this batch case"
        elif case.get("status") == "NOT_RUN":
            case["status"] = "BLOCKED"
            case["not_run"] = True
            case["blocked_reason"] = "an earlier batch case timed out"
    report["status"] = "TIMEOUT"
    return finalize_batch_report(report)


def read_timing_report(path, fallback):
    """Read a timing report, returning *fallback* if Maya wrote no valid JSON."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    if report.get("schema_version") != TIMING_REPORT_SCHEMA_VERSION:
        return fallback
    return report


def read_batch_report(path, fallback):
    """Read a batch report, returning *fallback* for invalid JSON/schema."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    if report.get("schema_version") != BATCH_REPORT_SCHEMA_VERSION or report.get("runner") != "maya_gui_batch":
        return fallback
    return report


def finalize_timing_report(report):
    """Add deterministic counts and the 20 slowest executed tests."""
    counts = {}
    timed_tests = []
    for test in report.get("tests", []):
        status = test.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        elapsed = test.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            timed_tests.append(test)
    timed_tests.sort(key=lambda item: (-item["elapsed_seconds"], item["id"]))
    report["test_counts"] = counts
    report["slowest_tests"] = [dict(item) for item in timed_tests[:20]]
    return report


def write_timing_report(path, report):
    """Atomically persist a GUI timing report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def parse_completion_status(line):
    """Return the explicit GUI-test status encoded in *line*, if present."""
    match = _COMPLETION_RE.match(line.strip())
    if match and match.group("status") in GUI_TEST_STATUSES:
        return match.group("status")
    return None


def monitor_log_file(log_path, timeout):
    """
    Monitors the log file for test output and a completion marker.
    """
    logger.info(f"Monitoring log file: {log_path}")
    if not log_path.is_file():
        log_path.touch()

    start_time = time.time()
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        # The stale log is removed before dispatch. Read from the beginning so
        # a fast NO_TESTS/ERROR completion written before this open is not lost.
        while time.time() - start_time < timeout:
            line = f.readline()
            if line:
                print(line, end="")
                status = parse_completion_status(line)
                if status is not None:
                    logger.info("GUI test completion marker found: %s", status)
                    return status
            else:
                time.sleep(LOG_POLL_INTERVAL)

    raise TimeoutError("Timed out waiting for test completion.")


def wait_for_maya_python_ready(marker_path, timeout=MAYA_PYTHON_READY_TIMEOUT):
    """Wait until Maya has executed the commandPort readiness payload."""
    marker_path = Path(marker_path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if marker_path.is_file() and marker_path.read_text(encoding="utf-8") == "ready":
                return
        except (OSError, UnicodeError):
            pass
        remaining = max(0.0, deadline - time.time())
        time.sleep(min(MAYA_PYTHON_READY_POLL_INTERVAL, remaining))
    raise TimeoutError(f"Timed out waiting for Maya Python readiness marker: {marker_path}")


def maya_python_ready_timeout(version):
    """Return the readiness timeout for a Maya version's cold-start behavior."""
    try:
        if int(str(version).split(".", 1)[0]) >= 2026:
            return TEST_EXECUTION_TIMEOUT
    except (TypeError, ValueError):
        pass
    return MAYA_PYTHON_READY_TIMEOUT


def verify_attached_maya(port, marker_path, maya_version, token=None, timeout=None):
    """Verify that *port* executes this runner's Maya handshake.

    An occupied TCP port is not sufficient evidence that the endpoint is the
    intended Maya Python commandPort.  The nonce-bound response also checks the
    requested Maya major version before any GUI test command is dispatched.
    """
    marker_path = Path(marker_path)
    handshake_timeout = maya_python_ready_timeout(maya_version) if timeout is None else timeout
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass
    handshake_token = token or secrets.token_hex(32)
    handshake_command = f"""
import json
import os
from pathlib import Path
import maya.cmds as cmds
Path({str(marker_path)!r}).write_text(json.dumps({{
    "protocol": {ATTACH_HANDSHAKE_PROTOCOL!r},
    "token": {handshake_token!r},
    "maya_major": str(int(cmds.about(apiVersion=True)) // 10000),
    "session_identity": os.environ.get({ATTACH_SESSION_ENVIRONMENT!r}),
    "scene_path": str(cmds.file(query=True, sceneName=True) or ""),
    "modified": bool(cmds.file(query=True, modified=True)),
    "selection": [str(item) for item in (cmds.ls(selection=True, long=True) or [])],
}}, sort_keys=True), encoding="utf-8")
"""
    try:
        maya_commandport.send_python(
            port,
            handshake_command,
            label="<maya-gui-attach-handshake>",
        )
    except OSError as exc:
        raise RuntimeError(f"refusing attach: commandPort :{port} handshake failed") from exc

    deadline = time.time() + handshake_timeout
    response = None
    invalid_response = False
    while time.time() < deadline:
        try:
            response = json.loads(marker_path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            time.sleep(MAYA_PYTHON_READY_POLL_INTERVAL)
        except (OSError, UnicodeError, ValueError):
            # A host read can race the Maya-side write. Keep the endpoint
            # untrusted and allow one bounded interval for a complete response.
            invalid_response = True
            time.sleep(MAYA_PYTHON_READY_POLL_INTERVAL)
    if response is None:
        if invalid_response:
            raise RuntimeError(f"refusing attach: commandPort :{port} returned an invalid handshake")
        raise RuntimeError(f"refusing attach: commandPort :{port} did not prove runner ownership")
    if not isinstance(response, dict):
        raise RuntimeError(f"refusing attach: commandPort :{port} returned an invalid handshake")

    ownership = {
        "protocol": ATTACH_HANDSHAKE_PROTOCOL,
        "token": handshake_token,
        "maya_major": str(maya_version).split(".", 1)[0],
    }
    if any(response.get(key) != value for key, value in ownership.items()):
        raise RuntimeError(f"refusing attach: commandPort :{port} ownership response did not match")

    expected = {
        **ownership,
        "session_identity": ATTACH_SESSION_ID,
        "scene_path": "",
        "modified": False,
        "selection": [],
    }
    if response != expected:
        raise RuntimeError(
            f"refusing attach: commandPort :{port} did not prove a dedicated, clean test session"
        )


def main():
    """
    Main function to orchestrate the test run.
    """
    # Do not replace stdout: test runners and IDEs can provide streams without
    # ``buffer``.  ``reconfigure`` is both safer and sufficient for UTF-8 logs.
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Maya GUI Test Runner")
    parser.add_argument(
        "--maya_version", default=DEFAULT_MAYA_VERSION, help=f"Maya version to use (default: {DEFAULT_MAYA_VERSION})"
    )
    parser.add_argument("--test_path", default="tests/gui", help="Path to the test directory (relative to project root)")
    parser.add_argument("--test_filter", default=None, help="Optional substring matched against discovered test IDs")
    parser.add_argument(
        "--vp2_device_override",
        default=None,
        help="Optional MAYA_VP2_DEVICE_OVERRIDE for backend-specific GUI tests",
    )
    parser.add_argument(
        "--log_path",
        default=None,
        help="Path to the GUI test log (relative to project root; default: logs/ui_test_results.log)",
    )
    parser.add_argument(
        "--timing_report",
        default=None,
        help="Path to timing JSON (default: <log stem>.timing.json beside the log)",
    )
    parser.add_argument(
        "--batch_manifest",
        default=None,
        help="Versioned JSON manifest whose focused cases share one Maya session",
    )
    parser.add_argument(
        "--attach-existing",
        action="store_true",
        help=(
            "Attach only to a dedicated clean Maya session with "
            f"{ATTACH_SESSION_ENVIRONMENT}={ATTACH_SESSION_ID}; leave Maya running"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=COMMAND_PORT,
        help=f"Maya Python commandPort (default: {COMMAND_PORT})",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.attach_existing and args.vp2_device_override:
        parser.error("--vp2_device_override cannot be applied to --attach-existing Maya")
    if args.batch_manifest and args.test_filter:
        parser.error("--test_filter cannot be combined with --batch_manifest")

    project_root = Path(__file__).resolve().parent.parent
    batch_cases = None
    if args.batch_manifest:
        try:
            batch_cases = load_batch_manifest(args.batch_manifest, project_root)
        except ValueError as exc:
            parser.error(str(exc))
    log_file_path = Path(args.log_path) if args.log_path else Path("logs") / LOG_FILE_NAME
    if not log_file_path.is_absolute():
        log_file_path = project_root / log_file_path
    log_file_path = log_file_path.resolve()
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    timing_report_path = Path(args.timing_report) if args.timing_report else log_file_path.with_suffix(".timing.json")
    if not timing_report_path.is_absolute():
        timing_report_path = project_root / timing_report_path
    timing_report_path = timing_report_path.resolve()
    maya_timing_report_path = timing_report_path.with_name(
        timing_report_path.name + ".maya-partial"
    )
    log_dir = log_file_path.parent
    readiness_marker = log_dir / f".maya_commandport_ready_{args.maya_version}_{args.port}_{os.getpid()}.txt"
    attach_marker = log_dir / f".maya_commandport_attach_{args.maya_version}_{args.port}_{os.getpid()}.json"
    commandport_script = (log_dir / f"commandport_{args.port}.mel").resolve()

    # Clean up old log file
    if log_file_path.exists():
        log_file_path.unlink()

    timing_report = (
        new_batch_report(args.maya_version, batch_cases)
        if batch_cases is not None
        else new_timing_report(args.maya_version, args.test_path, args.test_filter)
    )
    write_timing_report(timing_report_path, timing_report)
    write_timing_report(maya_timing_report_path, timing_report)

    maya_process = None
    maya_process_id = None
    command_port_ready = False
    maya_app_dir = None
    maya_launched = False
    maya_exited = False
    completion_status = "ERROR"
    startup_started = None
    shutdown_started = None
    shutdown_failed = False
    tests_dispatched = False
    tests_dispatched_started = None
    execution_timed_out = False
    try:
        startup_started = time.perf_counter()
        # 1. Verify an explicitly attached Maya, or launch an isolated one.
        if args.attach_existing:
            if not maya_commandport.is_port_open(args.port):
                raise RuntimeError(f"refusing attach: commandPort :{args.port} is not open")
            verify_attached_maya(args.port, attach_marker, args.maya_version)
            command_port_ready = True
            logger.info("Verified attached Maya commandPort :%s; lifecycle remains external", args.port)
        else:
            maya_exe = maya_commandport.maya_exe(args.maya_version)
            logger.info("Maya executable: %s", maya_exe)
            maya_commandport.ensure_port_available(args.port)
            maya_app_dir = Path(tempfile.mkdtemp(prefix=f"maya_mmd_tools_gui_{args.maya_version}_"))
            env_overrides = {
                "MAYA_APP_DIR": str(maya_app_dir),
                "MAYA_PLUG_IN_PATH": str(project_root / "mmd_tools"),
            }
            if args.vp2_device_override:
                env_overrides["MAYA_VP2_DEVICE_OVERRIDE"] = args.vp2_device_override
            maya_process = maya_commandport.launch_maya(
                version=args.maya_version,
                project_root=project_root,
                output_dir=log_dir,
                port=args.port,
                # On Windows this deliberately uses launch_maya's Explorer route:
                # direct child startup is unreliable for Autodesk license checkout.
                launch_mode="explorer" if sys.platform == "win32" else "direct",
                # Never let automated Maya startup read or rewrite the user's
                # Documents/maya preferences (pluginPrefs.mel, userPrefs.mel, etc.).
                env_overrides=env_overrides,
            )
            maya_launched = True
            maya_commandport.wait_for_port(args.port, MAYA_START_TIMEOUT, maya_process)
            if maya_process is None and sys.platform == "win32":
                maya_process_id = maya_commandport.wait_for_maya_process_id(commandport_script, MAYA_START_TIMEOUT)
            command_port_ready = True

            try:
                readiness_marker.unlink()
            except FileNotFoundError:
                pass
            readiness_command = f"""
from pathlib import Path
Path({str(readiness_marker)!r}).write_text("ready", encoding="utf-8")
"""
            maya_commandport.send_python(
                args.port,
                readiness_command,
                label="<maya-commandport-readiness>",
            )
            wait_for_maya_python_ready(
                readiness_marker,
                timeout=maya_python_ready_timeout(args.maya_version),
            )
            logger.info("Maya Python commandPort readiness marker found: %s", readiness_marker)
        timing_report["phases"]["startup"] = {
            "status": "passed",
            "elapsed_seconds": max(0.0, time.perf_counter() - startup_started),
        }
        write_timing_report(timing_report_path, timing_report)
        write_timing_report(maya_timing_report_path, timing_report)

        # 2. Prepare and send the test execution command
        if batch_cases is not None:
            test_command = f"""
import sys
from pathlib import Path
project_root = Path({str(project_root)!r})
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.common.gui_test_base import GuiTestRunner
GuiTestRunner.run_batch_from_command(
    {str(log_file_path)!r},
    {batch_cases!r},
    {str(maya_timing_report_path)!r},
    new_scene={not args.attach_existing!r},
)
"""
        else:
            test_command = f"""
import sys
from pathlib import Path
project_root = Path({str(project_root)!r})
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.common.gui_test_base import GuiTestRunner
log_path = {str(log_file_path)!r}
test_dir = {args.test_path!r}
test_filter = {args.test_filter!r}
timing_report_path = {str(maya_timing_report_path)!r}
GuiTestRunner.run_tests_from_command(
    log_path,
    test_dir,
    test_filter,
    timing_report_path,
    preserve_attached_scene={args.attach_existing!r},
)
"""
        tests_dispatched_started = time.perf_counter()
        maya_commandport.send_python(args.port, test_command, label="<gui-test-runner-command>")
        tests_dispatched = True

        # 3. Monitor the log file for results
        status = monitor_log_file(log_file_path, TEST_EXECUTION_TIMEOUT)
        completion_status = status
        if status != "PASS":
            logger.error("GUI tests completed with status %s", status)
            return 1

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        if isinstance(e, TimeoutError) and tests_dispatched:
            completion_status = "TIMEOUT"
            execution_timed_out = True
        if timing_report["phases"]["startup"]["status"] == "blocked" and startup_started is not None:
            timing_report["phases"]["startup"] = {
                "status": "failed",
                "elapsed_seconds": max(0.0, time.perf_counter() - startup_started),
            }
            # Persist before ``finally`` reloads any partial report written by
            # Maya.  Startup failures happen before the in-Maya runner can
            # update this file.
            write_timing_report(timing_report_path, timing_report)
        return 1
    finally:
        # 4. Clean up
        if maya_launched:
            shutdown_started = time.perf_counter()
        if command_port_ready and not args.attach_existing:
            # Explorer launches are intentionally detached and have no process
            # handle.  Quit only the Maya instance on this runner's commandPort;
            # never enumerate or kill unrelated Maya processes.
            logger.info("Requesting Maya quit through commandPort...")
            try:
                maya_commandport.quit_maya(args.port)
                if maya_process:
                    maya_process.wait(timeout=30)
                elif maya_process_id is not None:
                    maya_commandport.wait_for_port_close(args.port, timeout=30)
                    maya_exited = maya_commandport.wait_for_maya_process_exit(
                        maya_process_id,
                        commandport_script,
                        timeout=30,
                    )
                    if not maya_exited:
                        maya_exited = maya_commandport.terminate_maya_process(
                            maya_process_id,
                            commandport_script,
                        )
                else:
                    maya_commandport.wait_for_port_close(args.port, timeout=30)
                    maya_exited = True
                if maya_process is not None:
                    maya_exited = True
            except Exception as e:
                shutdown_failed = True
                if maya_process:
                    logger.warning("Failed to quit owned Maya process gracefully, killing it: %s", e)
                    maya_process.kill()
                    maya_process.wait(timeout=30)
                    maya_exited = True
                elif maya_process_id is not None:
                    logger.warning("Failed to quit owned Explorer-launched Maya gracefully: %s", e)
                    maya_exited = maya_commandport.terminate_maya_process(
                        maya_process_id,
                        commandport_script,
                    )
                else:
                    logger.warning("Failed to request Explorer-launched Maya quit: %s", e)
        elif maya_process:
            if maya_process.poll() is None:
                logger.warning("Maya never opened its commandPort; terminating the owned test process.")
                maya_process.kill()
                maya_process.wait(timeout=30)
            maya_exited = True
        maya_commandport.close_process_logs(maya_process)
        try:
            readiness_marker.unlink()
        except FileNotFoundError:
            pass
        try:
            attach_marker.unlink()
        except FileNotFoundError:
            pass
        if maya_app_dir is not None and (maya_exited or not maya_launched):
            shutil.rmtree(maya_app_dir, ignore_errors=True)
        elif maya_app_dir is not None:
            logger.warning("Keeping isolated Maya profile for the still-running test process: %s", maya_app_dir)

        report_source = maya_timing_report_path if tests_dispatched else timing_report_path
        if batch_cases is not None:
            timing_report = read_batch_report(report_source, timing_report)
        else:
            timing_report = read_timing_report(report_source, timing_report)
        if execution_timed_out:
            if batch_cases is not None:
                host_elapsed = (
                    max(0.0, time.perf_counter() - tests_dispatched_started)
                    if tests_dispatched_started is not None
                    else None
                )
                finalize_batch_timeout(timing_report, host_elapsed)
            elif timing_report["phases"]["discovery"]["status"] in {"blocked", "running"}:
                timing_report["phases"]["discovery"] = {
                    "status": "timed_out",
                    "elapsed_seconds": timing_report["phases"]["discovery"].get("elapsed_seconds"),
                }
            elif timing_report["phases"]["tests"]["status"] in {"blocked", "running"}:
                timing_report["phases"]["tests"] = {
                    "status": "timed_out",
                    "elapsed_seconds": timing_report["phases"]["tests"].get("elapsed_seconds"),
                }
        timing_report["status"] = completion_status
        if args.attach_existing:
            timing_report["phases"]["shutdown"] = {
                "status": "skipped",
                "elapsed_seconds": 0.0,
            }
        elif shutdown_started is not None:
            shutdown_failed = shutdown_failed or (maya_launched and not maya_exited)
            timing_report["phases"]["shutdown"] = {
                "status": "failed" if shutdown_failed else "passed",
                "elapsed_seconds": max(0.0, time.perf_counter() - shutdown_started),
            }
        if batch_cases is None:
            finalize_timing_report(timing_report)
        else:
            finalize_batch_report(timing_report)
        write_timing_report(timing_report_path, timing_report)
        if maya_exited or not maya_launched:
            try:
                maya_timing_report_path.unlink()
            except FileNotFoundError:
                pass
        logger.info("GUI timing report: %s", timing_report_path)

    logger.info("GUI test run finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
