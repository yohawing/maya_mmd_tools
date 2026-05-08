"""
Maya GUI Test Runner using commandPort.

This script launches a Maya instance, executes GUI tests via a commandPort,
and streams the results from a log file.
"""

import os
import sys
import socket
import subprocess
import time
import argparse
import logging
from pathlib import Path

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


def find_maya_executable(maya_version):
    """
    Finds the path to the Maya executable.
    Checks common installation directories and the MAYA_LOCATION environment variable.
    """
    maya_location = os.environ.get(f"MAYA_LOCATION_{maya_version}") or os.environ.get("MAYA_LOCATION")
    if maya_location:
        maya_exe = Path(maya_location) / "bin" / "maya.exe"
        if maya_exe.is_file():
            logger.info(f"Found Maya executable at: {maya_exe}")
            return str(maya_exe)

    # Check standard Program Files locations
    for path in [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / f"Autodesk/Maya{maya_version}",
        Path(os.environ.get("ProgramW6432", "C:/Program Files")) / f"Autodesk/Maya{maya_version}",
    ]:
        maya_exe = path / "bin" / "maya.exe"
        if maya_exe.is_file():
            logger.info(f"Found Maya executable at: {maya_exe}")
            return str(maya_exe)

    raise FileNotFoundError(f"Could not find Maya {maya_version}. Set the MAYA_LOCATION environment variable.")


def launch_maya(maya_path, project_root):
    """
    Launches Maya as a subprocess with a commandPort.
    """
    logger.info("Launching Maya...")
    command = [maya_path, "-command", f'commandPort -name ":{COMMAND_PORT}" -sourceType "python";']

    # Add project root to PYTHONPATH for Maya
    env = os.environ.copy()
    python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{project_root};{python_path}"

    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    logger.info(f"Maya process started with PID: {process.pid}")
    return process


def wait_for_maya(timeout):
    """
    Waits for the Maya commandPort to become available.
    """
    logger.info(f"Waiting for Maya commandPort :{COMMAND_PORT} to open...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(("127.0.0.1", COMMAND_PORT), timeout=1):
                logger.info("Maya commandPort is open.")
                return True
        except (socket.timeout, ConnectionRefusedError):
            time.sleep(1)
    raise TimeoutError("Timed out waiting for Maya commandPort to open.")


def send_command_to_maya(command):
    """
    Sends a Python command to Maya via the commandPort.
    """
    logger.info("Sending command to Maya...")
    try:
        with socket.create_connection(("127.0.0.1", COMMAND_PORT), timeout=10) as sock:
            sock.sendall(command.encode("utf-8"))
        logger.info("Command sent successfully.")
    except Exception as e:
        logger.error(f"Failed to send command to Maya: {e}")
        raise


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
        maya_exe = find_maya_executable(args.maya_version)
        maya_process = launch_maya(maya_exe, project_root)
        wait_for_maya(MAYA_START_TIMEOUT)

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
        send_command_to_maya(test_command)

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
                send_command_to_maya("import maya.cmds as cmds; cmds.quit(force=True)")
                maya_process.wait(timeout=30)
            except Exception as e:
                logger.warning(f"Failed to quit Maya gracefully, killing process: {e}")
                maya_process.kill()
            logger.info("Maya process terminated.")

    logger.info("GUI test run finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
