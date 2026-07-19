"""テストパッケージの初期化モジュール。

VS Codeのテスト探索時にMaya環境を自動的に初期化します。
このモジュールはテストディスカバリー時に最初にインポートされるため、
ここでMaya standaloneモードを起動することで、テスト探索中にMaya APIを使用できます。
"""

import os
import sys
from pathlib import Path

# プロジェクトルートをsys.pathに追加
_TESTS_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _TESTS_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

# Maya環境の初期化フラグ
_maya_initialized = False


def _initialize_maya_if_needed():
    """必要に応じてMaya環境を初期化します。"""
    global _maya_initialized

    if _maya_initialized:
        return

    try:
        import maya.standalone
        import maya.cmds

        # Maya standaloneモードを初期化
        maya.standalone.initialize()

        # PYTHONPATHのパスをsys.pathに追加
        realsyspath = [os.path.realpath(p) for p in sys.path]
        pythonpath = os.environ.get("PYTHONPATH", "")
        for p in pythonpath.split(os.pathsep):
            if p:  # 空の文字列をスキップ
                p = os.path.realpath(p)
                if p not in realsyspath:
                    sys.path.insert(0, p)

        _maya_initialized = True

    except ImportError as e:
        # CPython側のcontrollerやeditor discoveryではMaya不在が正常なので、
        # 明示的な診断要求がある場合だけ既知の警告を表示する。
        if os.environ.get("MMD_TEST_DISCOVERY_VERBOSE") == "1":
            print(f"Warning: Maya modules not available for test discovery: {e}")
    except Exception as e:
        print(f"Warning: Failed to initialize Maya environment: {e}")


# 通常のeditor discoveryはimport時に初期化する。公式runnerは出力を完全ログへ
# 捕捉するため、明示的な初期化まで遅延する。
if os.environ.get("MMD_TEST_DEFER_MAYA_INIT") != "1":
    _initialize_maya_if_needed()
