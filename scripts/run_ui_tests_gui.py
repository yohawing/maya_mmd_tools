"""Maya GUI環境でUIテストを実行するPythonスクリプト

このスクリプトはMayaのシェルフやホットキーから実行できます。

シェルフボタンの作成方法:
1. このファイルの内容をコピー
2. Mayaのシェルフを右クリック → "New Shelf Button"
3. "Command"タブでPythonを選択し、このスクリプトを貼り付け
4. アイコンとラベルを設定
"""

import sys
import os
from pathlib import Path

def run_ui_tests():
    """Maya GUI環境でUIテストを実行"""
    
    # スクリプトのパスからプロジェクトルートを特定
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    
    # プロジェクトルートをPythonパスに追加
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    # テストランナーをインポート
    try:
        from tests.gui import run_gui_tests
    except ImportError as e:
        import maya.cmds as cmds
        cmds.error(f"Failed to import test runner: {e}")
        return
    
    # テストを実行
    print("\n" + "="*70)
    print("Maya MMD Tools - Running GUI Tests")
    print("="*70 + "\n")
    
    result = run_gui_tests.run()
    
    # 結果をMayaのUIで表示
    import maya.cmds as cmds
    if result:
        if result.wasSuccessful():
            cmds.confirmDialog(
                title='GUI Test Results',
                message='All GUI tests passed successfully!',
                button=['OK'],
                defaultButton='OK',
                icon='information'
            )
        else:
            failed_count = len(result.failures) + len(result.errors)
            cmds.confirmDialog(
                title='GUI Test Results',
                message=f'{failed_count} test(s) failed.\nCheck the Script Editor for details.',
                button=['OK'],
                defaultButton='OK',
                icon='warning'
            )
    else:
        cmds.warning("No tests were found or run.")

# このスクリプトが直接実行された場合
if __name__ == "__main__":
    run_ui_tests()