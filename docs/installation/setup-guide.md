# インストールガイド

このガイドでは、Maya MMD Toolsのインストール方法を説明します。

## システム要件

### 必須要件
- **Maya**: 2024以降
- **OS**: Windows 11 / macOS 15.6
- **Python**: 3.7以降（Mayaに含まれています）

### 推奨スペック
- **RAM**: 16GB以上
- **GPU**: DirectX 11対応
- **ストレージ**: SSD推奨

## インストール手順

### ステップ1: ファイルのダウンロード

1. [GitHubリリースページ](https://github.com/yohawing/maya_mmd_tools/releases)から最新版をダウンロード
2. ZIPファイルを任意の場所に解凍

### ステップ2: モジュールファイルの配置

#### Windows
1. 以下のフォルダを開きます：
   ```
   C:\Users\<ユーザー名>\Documents\maya\2024\modules
   ```
   （フォルダがない場合は作成してください）

2. 解凍したフォルダから `maya_mmd_tools.mod` ファイルをコピー

3. `maya_mmd_tools.mod` をテキストエディタで開き、パスを修正：
   ```
   + maya_mmd_tools 0.1.0 <解凍したフォルダのパス>
   scripts: scripts
   ```

#### macOS
1. 以下のフォルダを開きます：
   ```
   ~/Documents/maya/2024/modules
   ```

2. 同様に `maya_mmd_tools.mod` を配置してパスを修正

### ステップ3: スクリプトファイルの配置

1. Mayaのスクリプトフォルダを開きます：
   - Windows: `C:\Users\<ユーザー名>\Documents\maya\2024\scripts`
   - macOS: `~/Documents/maya/2024/scripts`

2. 解凍したフォルダから `userSetup.py` をコピー

### ステップ4: Mayaでプラグインを有効化

1. Mayaを起動（既に起動している場合は再起動）
2. メニューから `Window > Settings/Preferences > Plug-in Manager` を選択
3. リストから `mmd_importer.py` を探す
4. `Loaded` にチェックを入れる
5. 自動読み込みしたい場合は `Auto load` にもチェック

## インストールの確認

### 方法1: メニューの確認
- `File > Import` のファイルタイプに以下が追加されていることを確認：
  - `MMD Model (*.pmx;*.pmd)`
  - `MMD Motion (*.vmd)`

### 方法2: コマンドで確認
Mayaのスクリプトエディタで以下を実行：
```python
import mmd_tools
print(mmd_tools.__version__)
# 出力: 0.1.0-alpha.1
```

## トラブルシューティング

### プラグインが表示されない

1. モジュールパスを確認：
   ```python
   import maya.cmds as cmds
   print(cmds.getModulePath(moduleName="maya_mmd_tools"))
   ```

2. 環境変数を確認：
   ```python
   import os
   print(os.environ.get("MAYA_MODULE_PATH"))
   ```

### エラーが発生する

1. Mayaのバージョンを確認（2024以降が必要）
2. Pythonのバージョンを確認：
   ```python
   import sys
   print(sys.version)
   ```

### 日本語が文字化けする

システムのロケール設定を確認してください。UTF-8エンコーディングが推奨されます。

## アンインストール

1. Mayaを終了
2. 以下のファイルを削除：
   - `modules/maya_mmd_tools.mod`
   - `scripts/userSetup.py`
   - インストールしたmmd_toolsフォルダ

## 次のステップ

インストールが完了したら、[クイックスタート](../getting-started/quick-start.md)で実際に使ってみましょう！