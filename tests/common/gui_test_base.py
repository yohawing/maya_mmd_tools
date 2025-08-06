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
                except:
                    pass


class GuiTestRunner:
    """
    A static class to run GUI tests from an external command.
    It redirects all output to a specified log file.
    """

    @staticmethod
    def run_tests_from_command(log_file_path, test_dir_str):
        """
        Discovers and runs tests, redirecting output to a log file.

        Args:
            log_file_path (str): The absolute path to the log file.
            test_dir_str (str): The relative path to the test directory.
        """
        import logging
        import sys
        from pathlib import Path

        # Get project root from this file's location
        project_root = Path(__file__).resolve().parent.parent.parent
        test_dir = project_root / test_dir_str

        # Configure logging to file
        # This will capture logs from the test runner and the application itself
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            filename=log_file_path,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        )

        # Redirect stdout and stderr to the log file
        log_file = open(log_file_path, "a", encoding="utf-8")
        sys.stdout = log_file
        sys.stderr = log_file

        try:
            print(f"Starting GUI tests. Project root: {project_root}")
            print(f"Test directory: {test_dir}")
            print(f"Log file: {log_file_path}")

            # Discover tests
            suite = unittest.TestSuite()
            loader = unittest.TestLoader()

            # Use discover to find all test modules in the specified directory
            discovered_suite = loader.discover(str(test_dir), pattern="guitest_*.py", top_level_dir=str(project_root))
            suite.addTest(discovered_suite)

            if suite.countTestCases() == 0:
                print("No tests found.")
                return

            # Run tests
            print(f"Found {suite.countTestCases()} tests to run.")
            runner = unittest.TextTestRunner(stream=log_file, verbosity=2)
            runner.run(suite)

        except Exception:
            logging.error("An unexpected error occurred during test execution.", exc_info=True)
        finally:
            print("\n//-- GUI TEST FINISHED --//")
            log_file.close()
            # Restore original stdout/stderr
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
