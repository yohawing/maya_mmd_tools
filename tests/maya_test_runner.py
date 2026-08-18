#!/usr/bin/env python
"""Maya環境内でテストを実行するメインランナー。

このスクリプトはmayapy経由で実行され、ユニットテストと統合テストの両方を処理します。
すべてのテストはMaya環境内で実行されるため、Maya APIを使用するテストも問題なく動作します。
"""

import argparse
import ast
import json
import os
import sys
import time
import unittest
from pathlib import Path

# プロジェクトルートをsys.pathに追加
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import maya.cmds as cmds  # noqa: E402
import maya.standalone  # noqa: E402

from tests.common.custom_test_runner import (  # noqa: E402
    CustomTestRunner,
    enable_windows_ansi_support,
)
from tests.common.output_hygiene import summarize_unittest_result  # noqa: E402


def _load_global_test_plugin() -> str | None:
    """Load the repo plugin and return its name only when this runner owns it."""
    plugin_path = str(ROOT_DIR / "mmd_tools" / "plugin_main.py")
    if cmds.pluginInfo(plugin_path, query=True, loaded=True):
        return None

    loaded_plugins = cmds.loadPlugin(plugin_path, quiet=True) or []
    return str(loaded_plugins[0]) if loaded_plugins else plugin_path


def _unload_global_test_plugin(plugin_name: str | None) -> None:
    """Reset the standalone scene and release the plugin owned by this runner."""
    if plugin_name is None:
        return

    cmds.file(new=True, force=True)
    cmds.unloadPlugin(plugin_name, force=True)


def initialize_maya():
    """Maya環境を初期化します。"""
    maya.standalone.initialize()

    # PYTHONPATHのパスをsys.pathに追加
    # Mayaモジュールが読み込まれたとき、scriptsフォルダがPYTHONPATHに追加されますが、
    # sys.pathには追加されないようです。そのため、手動で追加します。
    realsyspath = [os.path.realpath(p) for p in sys.path]
    pythonpath = os.environ.get("PYTHONPATH", "")
    for p in pythonpath.split(os.pathsep):
        p = os.path.realpath(p)  # シンボリックリンクを解決
        if p not in realsyspath:
            sys.path.insert(0, p)


def uninitialize_maya():
    """Maya環境を終了します。"""
    maya_version = cmds.about(v=True)
    if maya_version and float(maya_version) >= 2016.0:
        try:
            maya.standalone.uninitialize()
        except AttributeError:
            # Mayaバージョンによってはuninitializeが存在しない場合がある
            pass


def get_all_tests(suite_to_flatten):
    """TestSuiteから全てのテストケースを取得します。

    Args:
        suite_to_flatten: フラット化するTestSuite

    Returns:
        テストケースのリスト
    """
    tests = []
    for test in suite_to_flatten:
        if isinstance(test, unittest.TestSuite):
            tests.extend(get_all_tests(test))
        else:
            tests.append(test)
    return tests


def _test_module_imports_pytest(path: Path) -> bool:
    """Return whether a test module imports pytest anywhere in its source."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pytest" or alias.name.startswith("pytest.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pytest" or module.startswith("pytest."):
                return True
    return False


def _pytest_dependent_test_modules(test_dir: Path) -> set[Path]:
    """Find pytest-owned test modules that the unittest mayapy runner cannot load.

    The repository runs pytest modules through the CPython ``ci_unit`` session.
    The Maya runner uses ``unittest`` and must not import those modules: mayapy
    intentionally does not carry a separate pytest installation.  Test modules
    that only import helpers from a pytest-owned module are excluded as well,
    otherwise unittest reports them as synthetic ``_FailedTest`` errors.
    """
    paths = sorted(test_dir.glob("test_*.py"))
    package_parts = test_dir.relative_to(ROOT_DIR).parts
    package_prefix = ".".join(package_parts)
    module_paths = {
        alias: path
        for path in paths
        for alias in (path.stem, f"{package_prefix}.{path.stem}")
    }
    dependencies: dict[Path, set[Path]] = {path: set() for path in paths}
    pytest_owned = {path for path in paths if _test_module_imports_pytest(path)}

    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            imported_names: list[str] = []
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 1:
                    module = f"{package_prefix}.{module}" if module else package_prefix
                elif node.level:
                    continue
                imported_names.append(module)
                imported_names.extend(
                    f"{module}.{alias.name}" for alias in node.names if module
                )
            for imported_name in imported_names:
                dependency = module_paths.get(imported_name)
                if dependency is not None and dependency is not path:
                    dependencies[path].add(dependency)

    changed = True
    while changed:
        changed = False
        for path, imported_paths in dependencies.items():
            if path in pytest_owned:
                continue
            if imported_paths & pytest_owned:
                pytest_owned.add(path)
                changed = True
    return pytest_owned


def discover_tests(test_type, test_filter=None):
    """テストを探索します。

    Args:
        test_type: 'unit', 'integration', 'gui'
        test_filter: テストをフィルタリングする文字列（オプション）

    Returns:
        テストが含まれるTestSuite
    """
    test_dir = SCRIPT_DIR / test_type

    print(f"Discovering '{test_type}' tests in '{test_dir}'...")

    # pytest-only modules are executed by the CPython ci_unit session.  Loading
    # them here would create unittest _FailedTest entries because mayapy does
    # not install pytest.
    pytest_only = _pytest_dependent_test_modules(test_dir)
    if pytest_only:
        skipped = ", ".join(path.name for path in sorted(pytest_only))
        print(f"Skipping {len(pytest_only)} pytest-owned module(s) in Maya unittest runner: {skipped}")

    # テストを探索
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_dir_string = str(test_dir)
    if test_dir_string not in sys.path:
        sys.path.insert(0, test_dir_string)
    for test_path in sorted(test_dir.glob("test_*.py")):
        if test_path in pytest_only:
            continue
        suite.addTests(loader.loadTestsFromName(test_path.stem))

    if suite.countTestCases() == 0:
        print(f"No tests found in '{test_dir}'.")
        return suite

    # フィルタリングが指定されている場合
    if test_filter:
        filtered_suite = unittest.TestSuite()
        all_tests = get_all_tests(suite)

        for test_case in all_tests:
            if test_filter in test_case.id():
                filtered_suite.addTest(test_case)

        if filtered_suite.countTestCases() == 0:
            print(f"Error: No tests found matching '--test {test_filter}' in the '{test_type}' suite.")
            print("\nAvailable tests in this suite:")
            for test_case in all_tests:
                print(f"  - {test_case.id()}")
            return filtered_suite

        suite = filtered_suite

    return suite


def _result_payload(test_type, result, duration_sec):
    """Build the stable result contract consumed by the outer runner."""
    summary, failed_tests = summarize_unittest_result(result)
    return {
        "gate": test_type,
        "status": "pass" if result.wasSuccessful() else "fail",
        "summary": summary,
        "duration_sec": round(duration_sec, 3),
        "first_failure": failed_tests[0] if failed_tests else None,
        "failed_tests": failed_tests,
    }


def run_tests(
    test_type,
    test_filter=None,
    *,
    verbose=False,
    capture_details=False,
    report_path=None,
):
    """テストを実行します。

    Args:
        test_type: 'unit' または 'integration'
        test_filter: テストをフィルタリングする文字列（オプション）
    """
    plugin_name = _load_global_test_plugin() if test_type in {"unit", "integration"} else None
    try:
        # テストを探索
        suite = discover_tests(test_type, test_filter)

        if suite.countTestCases() == 0:
            sys.exit(1)

        print(f"Running {suite.countTestCases()} test(s)...")

        # Windows環境でもANSIカラーコードを有効化
        enable_windows_ansi_support()

        # カラー対応のテストランナーを使用
        runner = CustomTestRunner(
            verbosity=2 if verbose else 1,
            show_error_details=verbose or capture_details,
        )
        runner.failfast = False
        # The outer runner owns terminal suppression. Keep successful-test stdout
        # and warnings in its complete transcript instead of discarding them here.
        runner.buffer = False
        started = time.perf_counter()
        result = runner.run(suite)

        if report_path:
            report = Path(report_path)
            report.parent.mkdir(parents=True, exist_ok=True)
            payload = _result_payload(test_type, result, time.perf_counter() - started)
            report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return result
    finally:
        _unload_global_test_plugin(plugin_name)


def main():
    """メイン関数。"""
    # 引数解析
    parser = argparse.ArgumentParser(description="Run Maya tests.")
    parser.add_argument(
        "--type",
        type=str,
        required=True,
        choices=["unit", "integration", "gui"],
        help="The type of tests to run: 'unit', 'integration', or 'gui'.",
    )
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="A string to filter tests by. Can be a module, class, or method name.",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Write a compact JSON result report for the outer runner.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show each test and its full diagnostic output.",
    )
    parser.add_argument(
        "--capture-details",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    # Maya環境を初期化
    initialize_maya()

    try:
        # テストを実行
        result = run_tests(
            args.type,
            args.test,
            verbose=args.verbose,
            capture_details=args.capture_details,
            report_path=args.report,
        )
    finally:
        # Maya環境を終了
        uninitialize_maya()

    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()
