"""GUI テスト用のベースクラスとユーティリティ。"""

import unittest
import functools
import maya.cmds as cmds


def skip_if_no_gui(func):
    """GUIが利用できない場合はテストをスキップするデコレーター"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required")
        return func(*args, **kwargs)

    return wrapper


def requires_gui(cls):
    """クラス全体にGUI要求を適用するデコレーター"""
    for attr_name in dir(cls):
        attr = getattr(cls, attr_name)
        if callable(attr) and attr_name.startswith("test_"):
            setattr(cls, attr_name, skip_if_no_gui(attr))
    return cls


class GuiTestBase(unittest.TestCase):
    """GUI テスト用のベースクラス"""

    @classmethod
    def setUpClass(cls):
        """クラスレベルのセットアップ"""
        if cmds.about(batch=True):
            raise unittest.SkipTest("GUI environment required for this test class")
        super().setUpClass()

    def setUp(self):
        """各テストの前処理"""
        # 既存のウィンドウをクリーンアップ
        self._cleanup_windows()

    def tearDown(self):
        """各テストの後処理"""
        # ウィンドウをクリーンアップ
        self._cleanup_windows()

    def _cleanup_windows(self):
        """開いているウィンドウをクリーンアップ"""
        # Maya MMD Toolsのウィンドウを探して削除
        all_windows = cmds.lsUI(windows=True)
        for window in all_windows:
            if window.startswith("MayaMMDTools") or window.startswith("mmdTools"):
                try:
                    cmds.deleteUI(window, window=True)
                except Exception:
                    pass


class _LifecycleTextTestResult(unittest.TextTestResult):
    """Text result that flushes per-test lifecycle messages to its stream."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._unfinished_test_ids = set()

    def _write_lifecycle(self, test, phase, outcome=None):
        message = f"[GUI TEST] {phase} {test.id()}"
        if outcome is not None:
            message += f" outcome={outcome}"
        self.stream.write(message + "\n")
        self.stream.flush()

    def startTest(self, test):
        super().startTest(test)
        self._unfinished_test_ids.add(id(test))
        self._write_lifecycle(test, "START")

    def _write_test_end(self, test, outcome):
        self._unfinished_test_ids.discard(id(test))
        self._write_lifecycle(test, "END", outcome)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._write_test_end(test, "success")

    def addError(self, test, err):
        super().addError(test, err)
        self._write_test_end(test, "error")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._write_test_end(test, "failure")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._write_test_end(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._write_test_end(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._write_test_end(test, "unexpected_success")

    def stopTest(self, test):
        if id(test) in self._unfinished_test_ids:
            self._write_test_end(test, "unknown")
        super().stopTest(test)


class GuiTestRunner:
    """
    A static class to run GUI tests from an external command.
    It redirects all output to a specified log file.
    """

    @staticmethod
    def run_tests_from_command(log_file_path, test_dir_str, test_filter=None):
        """
        Discovers and runs tests, redirecting output to a log file.

        Args:
            log_file_path (str): The absolute path to the log file.
            test_dir_str (str): The relative path to the test directory.
            test_filter (str | None): Optional substring matched against test IDs.
        """
        import logging
        import sys
        from pathlib import Path

        # Get project root from this file's location
        project_root = Path(__file__).resolve().parent.parent.parent
        test_dir = project_root / test_dir_str

        # Configure logging to file
        # This will capture logs from the test runner and the application itself
        original_handlers = logging.root.handlers[:]
        original_log_level = logging.root.level
        for handler in original_handlers:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            filename=log_file_path,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            encoding="utf-8",
            errors="backslashreplace",
        )

        # Redirect stdout and stderr to the log file.  Preserve the live Maya
        # streams so they can always be restored after a test-side exception.
        log_file = open(log_file_path, "a", encoding="utf-8")
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = log_file
        sys.stderr = log_file
        status = "ERROR"

        try:
            print(f"Starting GUI tests. Project root: {project_root}")
            print(f"Test directory: {test_dir}")
            if test_filter:
                print(f"Test filter: {test_filter}")
            print(f"Log file: {log_file_path}")

            # Discover tests
            suite = unittest.TestSuite()
            loader = unittest.TestLoader()

            # Use discover to find all test modules in the specified directory
            discovered_suite = loader.discover(str(test_dir), pattern="guitest_*.py", top_level_dir=str(project_root))
            if test_filter:
                discovered_suite = GuiTestRunner._filter_suite(discovered_suite, test_filter)
            suite.addTest(discovered_suite)

            if suite.countTestCases() == 0:
                print("No tests found.")
                status = "NO_TESTS"
                return status

            # Run tests
            print(f"Found {suite.countTestCases()} tests to run.")
            runner = unittest.TextTestRunner(
                stream=log_file,
                verbosity=2,
                resultclass=_LifecycleTextTestResult,
            )
            result = runner.run(suite)
            status = "PASS" if result.wasSuccessful() else "FAIL"
            return status

        except Exception:
            logging.error("An unexpected error occurred during test execution.", exc_info=True)
            return status
        finally:
            print(f"\n//-- GUI TEST FINISHED --// status={status}")
            log_file.flush()
            log_file.close()
            # Restore original stdout/stderr
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            # Do not leave a FileHandler holding the log open on Windows.  Maya
            # logging is restored to the state it had before this test command.
            for handler in logging.root.handlers[:]:
                logging.root.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logging.root.addHandler(handler)
            logging.root.setLevel(original_log_level)

    @staticmethod
    def _filter_suite(suite, test_filter):
        """Return the discovered tests whose IDs contain ``test_filter``."""
        filtered_suite = unittest.TestSuite()
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                nested_suite = GuiTestRunner._filter_suite(test, test_filter)
                if nested_suite.countTestCases():
                    filtered_suite.addTest(nested_suite)
            elif test_filter in test.id():
                filtered_suite.addTest(test)
        return filtered_suite
