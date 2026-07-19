"""
Maya GUI Test Runner using commandPort.

This script launches a Maya instance, executes GUI tests via a commandPort,
and streams the results from a log file.
"""

import sys
import time
import argparse
import logging
import re
import shutil
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
TEST_EXECUTION_TIMEOUT = 600  # seconds
LOG_POLL_INTERVAL = 1  # second
GUI_TEST_FINISHED_MARKER = "//-- GUI TEST FINISHED --//"
GUI_TEST_STATUSES = frozenset(("PASS", "FAIL", "NO_TESTS", "ERROR"))
_COMPLETION_RE = re.compile(
    rf"^{re.escape(GUI_TEST_FINISHED_MARKER)}\s+status=(?P<status>[A-Z_]+)\s*$"
)

# --- Logger Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


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
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file_path = log_dir / LOG_FILE_NAME

    # Clean up old log file
    if log_file_path.exists():
        log_file_path.unlink()

    maya_process = None
    command_port_ready = False
    maya_app_dir = Path(tempfile.mkdtemp(prefix=f"maya_mmd_tools_gui_{args.maya_version}_"))
    maya_launched = False
    maya_exited = False
    try:
        # 1. Find and launch Maya
        maya_exe = maya_commandport.maya_exe(args.maya_version)
        logger.info("Maya executable: %s", maya_exe)
        maya_process = maya_commandport.launch_maya(
            version=args.maya_version,
            project_root=project_root,
            output_dir=log_dir,
            port=COMMAND_PORT,
            # On Windows this deliberately uses launch_maya's Explorer route:
            # direct child startup is unreliable for Autodesk license checkout.
            launch_mode="explorer" if sys.platform == "win32" else "direct",
            # Never let automated Maya startup read or rewrite the user's
            # Documents/maya preferences (pluginPrefs.mel, userPrefs.mel, etc.).
            env_overrides={
                "MAYA_APP_DIR": str(maya_app_dir),
                "MAYA_PLUG_IN_PATH": str(project_root / "mmd_tools"),
            },
        )
        maya_launched = True
        maya_commandport.wait_for_port(COMMAND_PORT, MAYA_START_TIMEOUT, maya_process)
        command_port_ready = True

        # 2. Prepare and send the test execution command
        test_command = f"""
import sys
from pathlib import Path
project_root = Path({str(project_root)!r})
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.common.gui_test_base import GuiTestRunner
log_path = {str(log_file_path)!r}
test_dir = {args.test_path!r}
GuiTestRunner.run_tests_from_command(log_path, test_dir)
"""
        maya_commandport.send_python(COMMAND_PORT, test_command, label="<gui-test-runner-command>")

        # 3. Monitor the log file for results
        status = monitor_log_file(log_file_path, TEST_EXECUTION_TIMEOUT)
        if status != "PASS":
            logger.error("GUI tests completed with status %s", status)
            return 1

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return 1
    finally:
        # 4. Clean up
        if command_port_ready:
            # Explorer launches are intentionally detached and have no process
            # handle.  Quit only the Maya instance on this runner's commandPort;
            # never enumerate or kill unrelated Maya processes.
            logger.info("Requesting Maya quit through commandPort...")
            try:
                maya_commandport.quit_maya(COMMAND_PORT)
                if maya_process:
                    maya_process.wait(timeout=30)
                else:
                    maya_commandport.wait_for_port_close(COMMAND_PORT, timeout=30)
                maya_exited = True
            except Exception as e:
                if maya_process:
                    logger.warning("Failed to quit owned Maya process gracefully, killing it: %s", e)
                    maya_process.kill()
                    maya_process.wait(timeout=30)
                    maya_exited = True
                else:
                    logger.warning("Failed to request Explorer-launched Maya quit: %s", e)
        elif maya_process:
            if maya_process.poll() is None:
                logger.warning("Maya never opened its commandPort; terminating the owned test process.")
                maya_process.kill()
                maya_process.wait(timeout=30)
            maya_exited = True
        maya_commandport.close_process_logs(maya_process)
        if maya_exited or not maya_launched:
            shutil.rmtree(maya_app_dir, ignore_errors=True)
        else:
            logger.warning("Keeping isolated Maya profile for the still-running test process: %s", maya_app_dir)

    logger.info("GUI test run finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
