
import unittest
import sys
from pathlib import Path

# プロジェクトルートをsys.pathに追加
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.integration.test_main_window_flow import TestMainWindowFlow
from tests.common.custom_test_runner import CustomTestRunner, enable_windows_ansi_support

def run_specific_test():
    """
    特定のテストメソッドを実行します。
    """
    suite = unittest.TestSuite()
    suite.addTest(TestMainWindowFlow('test_ui_elements_main_window'))

    # Windows環境でもANSIカラーコードを有効化
    enable_windows_ansi_support()

    runner = CustomTestRunner(verbosity=2)
    runner.run(suite)

# Mayaのスクリプトエディタで実行する場合
if __name__ == "__main__":
    print("Running single test: test_ui_elements_main_window")
    run_specific_test()
