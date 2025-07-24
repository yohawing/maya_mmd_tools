"""Maya GUI環境でUIテストを実行するためのスクリプト

このスクリプトはMayaのScript Editorまたはシェルフから実行してください。

使用例:
    # Maya Script Editorで実行
    import sys
    sys.path.append(r'F:\Develop\maya_mmd_tools')
    from tests.gui import run_gui_tests
    run_gui_tests.run()
"""

import sys
import unittest
from pathlib import Path

# プロジェクトルートをsys.pathに追加
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.common.custom_test_runner import (
    CustomTestRunner,
    enable_windows_ansi_support,
)


def run(test_filter=None):
    """GUIテストを実行

    Args:
        test_filter: 実行するテストをフィルタリングする文字列（オプション）

    Returns:
        unittest.TestResult: テストの実行結果
    """
    print("=" * 70)
    print("Running Maya MMD Tools GUI Tests")
    print("=" * 70)

    # テストを探索
    loader = unittest.TestLoader()
    suite = loader.discover(str(SCRIPT_DIR), pattern="guitest_*.py")

    # フィルタリングが指定されている場合
    if test_filter:
        filtered_suite = unittest.TestSuite()
        all_tests = _get_all_tests(suite)

        for test_case in all_tests:
            if test_filter in test_case.id():
                filtered_suite.addTest(test_case)

        if filtered_suite.countTestCases() == 0:
            print(f"No tests found matching '{test_filter}'")
            return None

        suite = filtered_suite

    # テスト数を表示
    test_count = suite.countTestCases()
    if test_count == 0:
        print("No tests found.")
        return None

    print(f"Found {test_count} test(s)")
    print("-" * 70)

    # Windows環境でもANSIカラーコードを有効化
    enable_windows_ansi_support()

    # テストランナーを作成して実行
    runner = CustomTestRunner(verbosity=2)
    result = runner.run(suite)

    # 結果のサマリーを表示
    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print("All tests passed!")
    else:
        print(f"FAILED (failures={len(result.failures)}, errors={len(result.errors)})")

    return result


def run_specific_test(test_name):
    """特定のテストクラスまたはメソッドを実行

    Args:
        test_name: テストクラス名またはメソッド名

    Examples:
        run_specific_test("TestMainWindow")
        run_specific_test("test_window_creation")
    """
    return run(test_name)


def _get_all_tests(suite):
    """TestSuiteから全てのテストケースを取得"""
    tests = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            tests.extend(_get_all_tests(test))
        else:
            tests.append(test)
    return tests


# Maya Script Editorから実行された場合
if __name__ == "__main__":
    # 引数がある場合はそれをフィルタとして使用
    import maya.cmds as cmds

    # 実行例を表示
    cmds.warning("""
GUI Tests Runner loaded. Usage:
    run()                          # Run all GUI tests
    run_specific_test("TestMainWindow")  # Run specific test class
    run_specific_test("test_window_creation")  # Run specific test method
""")
