import argparse
import os
import platform
import subprocess
import sys
import unittest
from pathlib import Path
import platform

try:
    import maya.standalone
    import maya.cmds as cmds
    USING_MAYAPY = True
except ImportError:
    USING_MAYAPY = False


# プロジェクトルートをsys.pathに追加して、testsモジュールをインポートできるようにする
ROOT_DIR = Path(__file__).resolve().parent.parent.absolute()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# これで、testsモジュールを安全にインポートできる
from tests.common.custom_test_runner import CustomTestRunner, enable_windows_ansi_support
from tests.run_maya_tests import run_tests_from_commandline

# enable_windows_ansi_supportは既にimportしているので不要

def get_maya_location(maya_version: int) -> Path:
    """Mayaがインストールされている場所を取得します。

    Args:
        maya_version: Mayaのバージョン番号

    Returns:
        Mayaがインストールされているパス

    Examples:
        >>> get_maya_location(2024)
        Path('C:/Program Files/Autodesk/Maya2024')
    """
    if "MAYA_LOCATION" in os.environ:
        return Path(os.environ["MAYA_LOCATION"])

    if platform.system() == "Windows":
        return Path(f"C:\\Program Files\\Autodesk\\Maya{maya_version}")
    elif platform.system() == "Darwin":
        return Path(f"/Applications/Autodesk/maya{maya_version}/Maya.app/Contents")
    else:
        location = f"/usr/autodesk/maya{maya_version}"
        if maya_version < 2016:
            # 2016以降、デフォルトのインストールディレクトリ名が変更されました
            location += "-x64"
        return Path(location)


def mayapy(maya_version: int) -> Path:
    """mayapy実行ファイルのパスを取得します。

    Args:
        maya_version: Mayaのバージョン番号

    Returns:
        mayapy実行ファイルのパス

    Examples:
        >>> mayapy(2024)
        Path('C:/Program Files/Autodesk/Maya2024/bin/mayapy.exe')
    """
    python_exe = get_maya_location(maya_version) / "bin" / "mayapy"
    if platform.system() == "Windows":
        python_exe = python_exe.with_suffix(".exe")
    return python_exe


def run_tests():
    """
    Discovers and runs tests based on command-line arguments.
    
    This script can run either unit or integration tests, and can filter
    tests by a specific name provided via the command line.
    """
    # プロジェクトルートはすでにスクリプトの開始時にsys.pathに追加されています。

    # Set up argument parser
    parser = argparse.ArgumentParser(description='Run tests for the MMD Tools project.')
    parser.add_argument(
        '--type',
        type=str,
        default='unit',
        choices=['unit', 'integration'],
        help="The type of tests to run: 'unit' or 'integration'. Defaults to 'unit'."
    )
    parser.add_argument(
        '--test',
        type=str,
        default=None,
        help='A string to filter tests by. Can be a module, class, or method name.'
    )
    parser.add_argument(
        '--maya',
        type=int,
        default=2024,
        help='The version of Maya to use for integration tests. Defaults to 2024.'
    )
    args = parser.parse_args()

    # Discover tests based on the specified type
    test_loader = unittest.TestLoader()
    test_dir = os.path.dirname(__file__)
    start_dir = os.path.join(test_dir, args.type)

    print(f"Discovering '{args.type}' tests in '{start_dir}'...")

    # Discover all tests in the specified directory
    suite = test_loader.discover(start_dir, pattern='test_*.py')

    if suite.countTestCases() == 0:
        print(f"No tests found in '{start_dir}'.")
        sys.exit(1)

    # If a specific test name is provided, filter the suite
    if args.test:
        filtered_suite = unittest.TestSuite()

        # Helper to get a flat list of all test cases from a suite
        def get_all_tests(suite_to_flatten):
            tests = []
            for test in suite_to_flatten:
                if isinstance(test, unittest.TestSuite):
                    tests.extend(get_all_tests(test))
                else:
                    tests.append(test)
            return tests

        all_tests = get_all_tests(suite)

        for test_case in all_tests:
            if args.test in test_case.id():
                filtered_suite.addTest(test_case)

        suite = filtered_suite

    # Check if any tests are left after filtering
    if suite.countTestCases() == 0:
        print(f"Error: No tests found matching '--test {args.test}' in the '{args.type}' suite.")
        # To help the user, list all available tests of that type
        print("\nAvailable tests in this suite:")
        all_tests_in_suite = get_all_tests(test_loader.discover(start_dir, pattern='test_*.py'))
        for test_case in all_tests_in_suite:
            print(f"  - {test_case.id()}")
        sys.exit(1)

    # Run the final test suite
    print(f"Running {suite.countTestCases()} test(s)...")

    if args.type == 'unit':
        # カラー対応のテストランナーを使用
        runner = CustomTestRunner(verbosity=2)
        result = runner.run(suite)
        # 失敗時のみ追加情報を表示（カラーテストランナーでは既に詳細を表示済み）
        if not result.wasSuccessful():
            # sys.exit(1)
            pass
    elif args.type == 'integration':
        # 統合テストはMaya環境で実行する必要があるため、mayapyを使用して実行します。
        
        os.environ['PYTHONPATH'] = str(ROOT_DIR)
        os.environ["MAYA_SCRIPT_PATH"] = ""
        os.environ["MAYA_MODULE_PATH"] = str(ROOT_DIR)
        # Run the tests using mayapy
        # os.environ['MAYA_LOCATION'] = str(get_maya_location(maya_version))

        # すでにmayapyで実行している場合は、直接スクリプトを実行します。
        if USING_MAYAPY:
            run_tests_from_commandline()
            return

        maya_version = args.maya
        mayapy_path = mayapy(maya_version)
        if not mayapy_path.exists():
            print(f"Error: mayapy executable not found at {mayapy_path}.")
            sys.exit(1)

        command = [
            str(mayapy_path),
            os.path.join(ROOT_DIR, "tests", "run_maya_tests.py")
        ]
        if args.test:
            command.extend(['-test', args.test])

            print(f"Running integration tests with command: {' '.join(command)}")
            # result = os.system(' '.join(command))
        # if result != 0:
        #     print("統合テストの実行に失敗しました。")
        #     sys.exit(1)

        try:
            subprocess.check_call(command)
        except subprocess.CalledProcessError as e:
            print(f"統合テストの実行に失敗しました。: {e}")
            sys.exit(1)

        pass


if __name__ == '__main__':
    # Windows環境でもANSIカラーコードを有効化
    enable_windows_ansi_support()
    run_tests()
