#!/usr/bin/env python
"""テスト実行のメインエントリーポイント。

このスクリプトは引数解析を行い、mayapyを起動してmaya_test_runner.pyに処理を委譲します。
ただし、すでにmayapy環境内で実行されている場合は、maya_test_runnerを直接実行します。
すべてのテスト（ユニット/統合）はMaya環境内で実行されます。
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.absolute()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# This controller initializes Maya explicitly inside its captured output scope.
os.environ.setdefault("MMD_TEST_DEFER_MAYA_INIT", "1")
os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")

from tests.common.maya_location import maya_location  # noqa: E402
from tests.common.maya_location import mayapy as resolve_mayapy  # noqa: E402
from tests.common.output_hygiene import (  # noqa: E402
    format_summary,
    run_logged_subprocess,
)


def is_running_in_mayapy() -> bool:
    """現在mayapy環境内で実行されているかどうかを判定します。

    Returns:
        mayapy環境内で実行されている場合True、そうでなければFalse
    """
    try:
        return importlib.util.find_spec("maya.cmds") is not None
    except (ImportError, ModuleNotFoundError):
        return False


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
    return maya_location(maya_version)


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
    return resolve_mayapy(maya_version)


def wsl_to_windows_path(wsl_path: Path) -> str:
    """WSLパスをWindowsパスに変換します。

    Args:
        wsl_path: 変換するWSLパス

    Returns:
        Windows形式のパス文字列

    Examples:
        >>> wsl_to_windows_path(Path("/mnt/c/folder"))
        'C:\\folder'
    """
    path_str = str(wsl_path)
    # /mnt/c/ -> C:/, /mnt/d/ -> D:/, etc.
    match = re.match(r"/mnt/([a-z])/", path_str)
    if match:
        drive_letter = match.group(1).upper()
        return path_str.replace(f"/mnt/{match.group(1)}/", f"{drive_letter}:/").replace("/", "\\")
    return path_str


def _maya_version_from_executable(executable: str | Path, fallback: int) -> int:
    """Infer a direct mayapy's Maya version for correctly separated artifacts."""
    match = re.search(r"Maya(\d{4})", str(executable), re.IGNORECASE)
    return int(match.group(1)) if match else fallback


def _finish_run(
    *,
    test_type: str,
    report_path: Path,
    log_path: Path,
    returncode: int,
    started: float,
    repeated_warnings: int,
    verbose: bool = False,
) -> None:
    """Persist a full transcript and emit the shared compact result contract."""
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        summary = dict(payload["summary"])
    except (OSError, ValueError, KeyError, TypeError):
        payload = {
            "first_failure": "test runner did not produce a valid result report",
            "failed_tests": [],
        }
        summary = {"tests": 1, "pass": 0, "skip": 0, "fail": 1}
        duration_sec = time.perf_counter() - started
    else:
        duration_sec = float(payload.get("duration_sec", time.perf_counter() - started))

    if returncode != 0 and int(summary["fail"]) == 0:
        summary["tests"] = max(1, int(summary["tests"]))
        summary["fail"] = 1
        summary["pass"] = max(0, int(summary["tests"]) - int(summary["skip"]) - 1)
        payload["first_failure"] = f"test runner exited with code {returncode}"

    print(
        format_summary(
            test_type,
            total=int(summary["tests"]),
            passed=int(summary["pass"]),
            skipped=int(summary["skip"]),
            failed=int(summary["fail"]),
            duration_sec=duration_sec,
        )
    )
    if repeated_warnings and not verbose:
        print(f"[{test_type}] repeated warnings suppressed from terminal: {repeated_warnings}")

    if returncode != 0 or int(summary["fail"]):
        first_failure = payload.get("first_failure") or "unknown failure"
        print(f"[{test_type}] first failure: {first_failure}")
        failed_tests = [str(name) for name in payload.get("failed_tests") or []]
        if failed_tests:
            print(f"[{test_type}] failed tests: {', '.join(failed_tests)}")
        print(f"[{test_type}] full log: {log_path.resolve()}")
        raise SystemExit(returncode or 1)


def main():
    """メイン関数。引数を解析してmayapyを起動します。"""
    # 引数解析
    parser = argparse.ArgumentParser(description="Run tests for the MMD Tools project.")
    parser.add_argument(
        "--type",
        type=str,
        default="unit",
        choices=["unit", "integration", "gui"],
        help="The type of tests to run: 'unit', 'integration', or 'gui'. Defaults to 'unit'.",
    )
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        help="A string to filter tests by. Can be a module, class, or method name.",
    )
    parser.add_argument(
        "--maya",
        type=int,
        default=2024,
        help="The version of Maya to use. Defaults to 2024.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Display the complete mayapy test output in addition to saving it.",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Override the complete log path (default: build/reports/<type>_tests.log).",
    )
    args = parser.parse_args()

    running_in_mayapy = is_running_in_mayapy()
    artifact_maya = (
        _maya_version_from_executable(sys.executable, args.maya)
        if running_in_mayapy
        else args.maya
    )
    report_dir = ROOT_DIR / "build" / "reports"
    artifact_stem = f"{args.type}_maya{artifact_maya}_tests"
    log_path = Path(args.log_file) if args.log_file else report_dir / f"{artifact_stem}.log"
    if not log_path.is_absolute():
        log_path = ROOT_DIR / log_path
    report_path = report_dir / f"{artifact_stem}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()

    # CPython/WSL/mayapy直実行の全経路で、実テストは別mayapy processへ
    # 委譲する。これによりMaya/C++のFD直書きも完全ログへ捕捉できる。
    mayapy_path = Path(sys.executable) if running_in_mayapy else mayapy(args.maya)
    if not mayapy_path.exists():
        print(f"Error: mayapy executable not found at {mayapy_path}.")
        sys.exit(1)

    # 環境変数を設定
    env = os.environ.copy()
    env["MAYA_SCRIPT_PATH"] = ""
    env["MAYA_MODULE_PATH"] = str(ROOT_DIR)
    env["MAYA_NO_CONSOLE_WINDOW"] = "1"
    env["MAYA_SKIP_USERSETUP_PY"] = "1"

    # Explicitly add to MAYA_PLUG_IN_PATH as a workaround for test environment
    plugin_path = str(ROOT_DIR / "mmd_tools")
    existing_plugin_path = env.get("MAYA_PLUG_IN_PATH", "")
    if existing_plugin_path:
        env["MAYA_PLUG_IN_PATH"] = f"{plugin_path}{os.pathsep}{existing_plugin_path}"
    else:
        env["MAYA_PLUG_IN_PATH"] = plugin_path

    # テストランナースクリプトのパス
    test_runner_path = ROOT_DIR / "tests" / "maya_test_runner.py"

    # WSL環境でWindows版Mayaを使用する場合はパスを変換
    if str(mayapy_path).startswith("/mnt/"):
        test_runner_path = wsl_to_windows_path(test_runner_path)
        env["PYTHONPATH"] = wsl_to_windows_path(ROOT_DIR)
    else:
        env["PYTHONPATH"] = str(ROOT_DIR)

    # コマンドを構築
    command = [
        str(mayapy_path),
        str(test_runner_path),
        "--type",
        args.type,
        "--report",
        wsl_to_windows_path(report_path) if str(mayapy_path).startswith("/mnt/") else str(report_path),
    ]
    # Keep detailed child diagnostics in the complete transcript while the
    # outer runner decides whether they should reach the terminal.
    command.append("--capture-details")
    if args.test:
        command.extend(["--test", args.test])
    if args.verbose:
        command.append("--verbose")

    # mayapyの全出力は常にログへ保存し、端末は既定で要約だけにする。
    started = time.perf_counter()
    returncode, resolved_log, (_, repeated_warnings) = run_logged_subprocess(
        command,
        log_path=log_path,
        cwd=ROOT_DIR,
        env=env,
        verbose=args.verbose,
    )
    _finish_run(
        test_type=args.type,
        report_path=report_path,
        log_path=resolved_log,
        returncode=returncode,
        started=started,
        repeated_warnings=repeated_warnings,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
