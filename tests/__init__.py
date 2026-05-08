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
        print(f"Warning: Maya modules not available for test discovery: {e}")
        # Maya環境がない場合でもテスト探索は続行
    except Exception as e:
        print(f"Warning: Failed to initialize Maya environment: {e}")


# テストパッケージがインポートされた時点でMaya環境を初期化
_initialize_maya_if_needed()
