"""GUIテストパッケージ。

Maya GUIアプリケーション環境でのみ実行可能なテストを含みます。
実際のQtウィジェットを作成するテストなど。

注意: これらのテストはMaya standalone環境では実行できません。
Maya GUIアプリケーション内でスクリプトとして実行してください。
"""

import os
import sys

def is_gui_available():
    """Maya GUI環境が利用可能かチェック"""
    try:
        import maya.cmds as cmds
        # バッチモードかどうかをチェック
        return not cmds.about(batch=True)
    except:
        return False

# GUI環境でない場合は警告を出力
if not is_gui_available():
    print("WARNING: GUI tests require Maya GUI environment. These tests will be skipped in standalone mode.")