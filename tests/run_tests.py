#!/usr/bin/env python
"""テスト実行のメインエントリーポイント。

このスクリプトは引数解析を行い、mayapyを起動してmaya_test_runner.pyに処理を委譲します。
ただし、すでにmayapy環境内で実行されている場合は、maya_test_runnerを直接実行します。
すべてのテスト（ユニット/統合）はMaya環境内で実行されます。
"""

import argparse
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path


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
    if "MAYA_LOCATION" in os.environ:
        return Path(os.environ["MAYA_LOCATION"])

    if platform.system() == "Windows":
        return Path(f"C:\\Program Files\\Autodesk\\Maya{maya_version}")
    elif platform.system() == "Darwin":
        return Path(f"/Applications/Autodesk/maya{maya_version}/Maya.app/Contents")
    else:
        # WSL環境からWindows側のMayaを使用する場合
        wsl_maya_path = Path(f"/mnt/c/Program Files/Autodesk/Maya{maya_version}")
        if wsl_maya_path.exists():
            return wsl_maya_path

        # 通常のLinux環境
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
    maya_location = get_maya_location(maya_version)
    python_exe = maya_location / "bin" / "mayapy"

    # Windowsまたは WSL環境でWindows版Mayaを使用する場合は.exeを追加
    if platform.system() == "Windows" or str(maya_location).startswith("/mnt/"):
        python_exe = python_exe.with_suffix(".exe")

    return python_exe


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
    import re

    match = re.match(r"/mnt/([a-z])/", path_str)
    if match:
        drive_letter = match.group(1).upper()
        return path_str.replace(f"/mnt/{match.group(1)}/", f"{drive_letter}:/").replace(
            "/", "\\"
        )
    return path_str


def main():
    """メイン関数。引数を解析してmayapyを起動します。"""
    # プロジェクトルートを取得
    ROOT_DIR = Path(__file__).resolve().parent.parent.absolute()

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
    args = parser.parse_args()

    # mayapy環境内で実行されている場合は、maya_test_runnerを直接実行
    if is_running_in_mayapy():
        print(f"Running {args.type} tests directly in Maya environment...")

        # プロジェクトルートをsys.pathに追加
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))

        # maya_test_runnerを直接インポートして実行
        from tests.maya_test_runner import run_tests, initialize_maya, uninitialize_maya

        # Maya環境を初期化
        initialize_maya()

        try:
            # テストを実行
            run_tests(args.type, args.test)
        finally:
            # Maya環境を終了
            uninitialize_maya()

        return

    # 通常の処理：mayapyを起動してmaya_test_runnerに処理を委譲
    mayapy_path = mayapy(args.maya)
    if not mayapy_path.exists():
        print(f"Error: mayapy executable not found at {mayapy_path}.")
        sys.exit(1)

    # 環境変数を設定
    env = os.environ.copy()
    env["MAYA_SCRIPT_PATH"] = ""
    env["MAYA_MODULE_PATH"] = str(ROOT_DIR)

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
    ]
    if args.test:
        command.extend(["--test", args.test])

    print(f"Running {args.type} tests with Maya {args.maya}...")
    print(f"Command: {' '.join(command)}")

    # mayapyでテストランナーを実行
    try:
        subprocess.check_call(command, env=env)
    except subprocess.CalledProcessError as e:
        print(f"テストの実行に失敗しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
