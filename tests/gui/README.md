# GUIテスト

このディレクトリには、Maya GUI環境でのみ実行可能なUIテストが含まれています。

## 概要

GUIテストは、実際のQtウィジェットを作成し、ウィンドウの表示やUIイベントをテストします。
これらのテストは、Maya standalone環境では実行できず、Maya GUIアプリケーション内でスクリプトとして実行する必要があります。

## テストの実行方法

### 1. Script Editorから実行

Maya Script Editorで以下のコードを実行：

```python
import sys
# プロジェクトのパスを追加（環境に合わせて変更）
sys.path.append(r'F:\Develop\maya_mmd_tools')

from tests.gui import run_gui_tests

# すべてのGUIテストを実行
run_gui_tests.run()

# 特定のテストクラスを実行
run_gui_tests.run_specific_test("TestMainWindow")

# 特定のテストメソッドを実行
run_gui_tests.run_specific_test("test_window_creation")
```

### 2. シェルフボタンの作成

1. Mayaのシェルフを右クリック → "Shelf Editor"
2. "New Shelf"または既存のシェルフを選択
3. "New Button"をクリック
4. 以下の設定を行う：
   - **Label**: "Run GUI Tests"
   - **Command Type**: Python
   - **Command**: `scripts/run_ui_tests_gui.py`の内容をコピー

### 3. ホットキーの設定

1. Windows → Settings/Preferences → Hotkey Editor
2. "Custom Scripts"カテゴリを作成
3. 新しいコマンドを追加：
   - **Name**: runMMDToolsGUITests
   - **Language**: Python
   - **Command**: `scripts/run_ui_tests_gui.py`の内容

## テストの作成

### GuiTestBaseクラスの使用

すべてのGUIテストは`GuiTestBase`クラスを継承します：

```python
from tests.ui.gui.gui_test_base import GuiTestBase, requires_gui

@requires_gui
class TestMyWidget(GuiTestBase):
    def test_widget_creation(self):
        """ウィジェットの作成テスト"""
        from mmd_tools.ui.widgets import MyWidget
        widget = MyWidget()
        self.assertTrue(widget.isVisible())
```

### @requires_guiデコレーター

このデコレーターは、GUI環境でない場合にテストをスキップします：

```python
@requires_gui
class TestWindowFeatures(GuiTestBase):
    # このクラスのすべてのテストがGUI環境を要求
    pass
```

## 注意事項

1. **実行環境**: これらのテストはMaya GUIセッション内でのみ動作します
2. **CI/CD**: 自動化されたCI/CDパイプラインでは実行できません
3. **クリーンアップ**: `GuiTestBase`が自動的にウィンドウをクリーンアップします
4. **モーダルダイアログ**: モーダルダイアログのテストは避けてください（実行がブロックされます）

## トラブルシューティング

### "GUI environment required"エラー

このエラーは、Maya standalone環境でGUIテストを実行しようとした場合に発生します。
必ずMaya GUIアプリケーション内で実行してください。

### ウィンドウが残る問題

テスト後にウィンドウが残る場合は、以下を確認：
- `GuiTestBase`を継承しているか
- `tearDown`メソッドで`super().tearDown()`を呼んでいるか

### パスの問題

プロジェクトのパスが見つからない場合：
```python
import sys
import os
# 現在のファイルからプロジェクトルートを特定
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, project_root)
```