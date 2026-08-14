"""GUI テスト用のベースクラスとユーティリティ。"""

import unittest
import functools
import time
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

    def __init__(self, *args, timing_recorder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._unfinished_test_ids = set()
        self._timing_recorder = timing_recorder

    def _write_lifecycle(self, test, phase, outcome=None):
        message = f"[GUI TEST] {phase} {test.id()}"
        if outcome is not None:
            message += f" outcome={outcome}"
        self.stream.write(message + "\n")
        self.stream.flush()

    def startTest(self, test):
        super().startTest(test)
        self._unfinished_test_ids.add(id(test))
        if self._timing_recorder is not None:
            self._timing_recorder.start_test(test)
        self._write_lifecycle(test, "START")

    def _write_test_end(self, test, outcome):
        self._unfinished_test_ids.discard(id(test))
        if self._timing_recorder is not None:
            self._timing_recorder.record_outcome(test, outcome)
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
        parent_test = getattr(test, "test_case", None)
        if parent_test is not None:
            if self._timing_recorder is not None:
                self._timing_recorder.record_outcome(parent_test, "skipped")
            return
        self._write_test_end(test, "skipped")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._write_test_end(test, "expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._write_test_end(test, "unexpected_success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None and self._timing_recorder is not None:
            outcome = (
                "failure"
                if issubclass(err[0], test.failureException)
                else "error"
            )
            self._timing_recorder.record_outcome(test, outcome)

    def stopTest(self, test):
        if id(test) in self._unfinished_test_ids:
            known_outcome = (
                self._timing_recorder.outcome_for(test)
                if self._timing_recorder is not None
                else None
            )
            self._write_test_end(test, known_outcome or "unknown")
        if self._timing_recorder is not None:
            # unittest calls stopTest only after tearDown and cleanup hooks.
            self._timing_recorder.finish_test(test)
        super().stopTest(test)


class _TestTimingRecorder:
    """Collect per-test elapsed time without running tests a second time."""

    _OUTCOME_PRIORITY = {
        "not_run": 0,
        "unknown": 1,
        "success": 1,
        "skipped": 2,
        "expected_failure": 2,
        "unexpected_success": 3,
        "failure": 4,
        "error": 5,
    }

    def __init__(self, test_ids):
        self.tests = [
            {"id": test_id, "status": "not_run", "elapsed_seconds": None}
            for test_id in test_ids
        ]
        self._by_id = {entry["id"]: entry for entry in self.tests}
        self._started = {}

    def start_test(self, test):
        self._started[id(test)] = time.perf_counter()

    def record_outcome(self, test, outcome):
        entry = self._by_id.get(test.id())
        if entry is None:
            entry = {"id": test.id(), "status": "not_run", "elapsed_seconds": None}
            self.tests.append(entry)
            self._by_id[test.id()] = entry
        current = entry["status"]
        if self._OUTCOME_PRIORITY.get(outcome, 1) >= self._OUTCOME_PRIORITY.get(current, 1):
            entry["status"] = outcome

    def outcome_for(self, test):
        entry = self._by_id.get(test.id())
        if entry is None or entry["status"] == "not_run":
            return None
        return entry["status"]

    def finish_test(self, test, outcome=None):
        if outcome is not None:
            self.record_outcome(test, outcome)
        started = self._started.pop(id(test), None)
        entry = self._by_id.get(test.id())
        if started is not None:
            entry["elapsed_seconds"] = max(0.0, time.perf_counter() - started)


def _iter_tests(suite):
    """Yield leaf tests from a nested unittest suite."""
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _iter_tests(test)
        else:
            yield test


class GuiTestRunner:
    """
    A static class to run GUI tests from an external command.
    It redirects all output to a specified log file.
    """

    @staticmethod
    def run_tests_from_command(log_file_path, test_dir_str, test_filter=None, timing_report_path=None):
        """
        Discovers and runs tests, redirecting output to a log file.

        Args:
            log_file_path (str): The absolute path to the log file.
            test_dir_str (str): The relative path to the test directory.
            test_filter (str | None): Optional substring matched against test IDs.
            timing_report_path (str | None): Optional JSON timing report path.
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
        timing_report = None
        timing_recorder = None
        timing_helpers = None
        discovery_started = None
        tests_started = None

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
            discovery_started = time.perf_counter()
            discovered_suite = loader.discover(str(test_dir), pattern="guitest_*.py", top_level_dir=str(project_root))
            if test_filter:
                discovered_suite = GuiTestRunner._filter_suite(discovered_suite, test_filter)
            suite.addTest(discovered_suite)
            discovery_elapsed = max(0.0, time.perf_counter() - discovery_started)
            timing_recorder = _TestTimingRecorder(test.id() for test in _iter_tests(suite))

            if timing_report_path:
                from tests import run_gui_tests as timing_helpers

                fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                timing_report["phases"]["discovery"] = {
                    "status": "passed",
                    "elapsed_seconds": discovery_elapsed,
                }
                timing_report["phases"]["tests"] = {
                    "status": "running",
                    "elapsed_seconds": None,
                }
                timing_report["tests"] = timing_recorder.tests
                timing_helpers.write_timing_report(timing_report_path, timing_report)

            if suite.countTestCases() == 0:
                print("No tests found.")
                status = "NO_TESTS"
                if timing_report is not None:
                    timing_report["phases"]["tests"] = {
                        "status": "no_tests",
                        "elapsed_seconds": 0.0,
                    }
                return status

            # Run tests
            print(f"Found {suite.countTestCases()} tests to run.")
            def result_factory(*args, **kwargs):
                return _LifecycleTextTestResult(
                    *args,
                    timing_recorder=timing_recorder,
                    **kwargs,
                )

            tests_started = time.perf_counter()
            runner = unittest.TextTestRunner(
                stream=log_file,
                verbosity=2,
                resultclass=result_factory,
            )
            result = runner.run(suite)
            tests_elapsed = max(0.0, time.perf_counter() - tests_started)
            status = "PASS" if result.wasSuccessful() else "FAIL"
            if timing_report is not None:
                timing_report["phases"]["tests"] = {
                    "status": "passed" if status == "PASS" else "failed",
                    "elapsed_seconds": tests_elapsed,
                }
            return status

        except Exception:
            logging.error("An unexpected error occurred during test execution.", exc_info=True)
            if timing_report_path:
                if timing_report is None:
                    from tests import run_gui_tests as timing_helpers

                    fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                    timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                if timing_report["phases"]["discovery"]["status"] == "blocked":
                    timing_report["phases"]["discovery"] = {
                        "status": "failed",
                        "elapsed_seconds": (
                            max(0.0, time.perf_counter() - discovery_started)
                            if discovery_started is not None
                            else None
                        ),
                    }
                elif timing_report["phases"]["tests"]["status"] in {"blocked", "running"}:
                    timing_report["phases"]["tests"] = {
                        "status": "failed",
                        "elapsed_seconds": (
                            max(0.0, time.perf_counter() - tests_started)
                            if tests_started is not None
                            else None
                        ),
                    }
            return status
        finally:
            if timing_report_path:
                if timing_report is None:
                    from tests import run_gui_tests as timing_helpers

                    fallback = timing_helpers.new_timing_report("unknown", test_dir_str, test_filter)
                    timing_report = timing_helpers.read_timing_report(timing_report_path, fallback)
                if timing_recorder is not None:
                    timing_report["tests"] = timing_recorder.tests
                timing_report["status"] = status
                timing_helpers.write_timing_report(timing_report_path, timing_report)
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
