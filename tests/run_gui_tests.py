"""
Maya GUI Test Runner using commandPort.

This script launches a Maya instance, executes GUI tests via a commandPort,
and streams the results from a log file.
"""

import sys
import time
import argparse
import logging
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

# --- Logger Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def monitor_log_file(log_path, timeout):
    """
    Monitors the log file for test output and a completion marker.
    """
    logger.info(f"Monitoring log file: {log_path}")
    if not log_path.is_file():
        log_path.touch()

    start_time = time.time()
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        # Move to the end of the file
        f.seek(0, 2)
        while time.time() - start_time < timeout:
            line = f.readline()
            if line:
                print(line, end="")
                if "//-- GUI TEST FINISHED --//" in line:
                    logger.info("Test completion marker found in log.")
                    return True
            else:
                time.sleep(LOG_POLL_INTERVAL)

    raise TimeoutError("Timed out waiting for test completion.")


def main():
    """
    Main function to orchestrate the test run.
    """
    # Reconfigure stdout to handle UTF-8 for printing log content
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

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
    try:
        # 1. Find and launch Maya
        maya_exe = maya_commandport.maya_exe(args.maya_version)
        logger.info("Maya executable: %s", maya_exe)
        maya_process = maya_commandport.launch_maya(
            version=args.maya_version,
            project_root=project_root,
            output_dir=log_dir,
            port=COMMAND_PORT,
            launch_mode="direct",
        )
        maya_commandport.wait_for_port(COMMAND_PORT, MAYA_START_TIMEOUT, maya_process)

        # 2. Prepare and send the test execution command
        test_command = f"""
import sys
from pathlib import Path
project_root = Path(r'{project_root}')
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.common.gui_test_base import GuiTestRunner
log_path = r'{log_file_path.as_posix()}'
test_dir = r'{args.test_path}'
GuiTestRunner.run_tests_from_command(log_path, test_dir)
"""
        maya_commandport.send_python(COMMAND_PORT, test_command, label="<gui-test-runner-command>")

        # 3. Monitor the log file for results
        monitor_log_file(log_file_path, TEST_EXECUTION_TIMEOUT)

    except (FileNotFoundError, TimeoutError, Exception) as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        return 1
    finally:
        # 4. Clean up
        if maya_process:
            logger.info("Terminating Maya process...")
            try:
                maya_commandport.quit_maya(COMMAND_PORT)
                maya_process.wait(timeout=30)
            except Exception as e:
                logger.warning(f"Failed to quit Maya gracefully, killing process: {e}")
                maya_process.kill()
            maya_commandport.close_process_logs(maya_process)
            logger.info("Maya process terminated.")

    logger.info("GUI test run finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
