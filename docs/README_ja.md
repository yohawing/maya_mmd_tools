# Maya MMD Tools ユーザードキュメント

Maya MMD Toolsは、Autodesk MayaでMikuMikuDance (MMD) のPMD/PMXモデルとVMDモーションを読み込むためのツールです。

現在は `0.x` の早期リリースです。一部の機能が不安定な場合があり、すべてのMMDファイルに対応していない可能性があります。重要なプロジェクトで使用する前に必ずバックアップを取ってください。

## 対応機能

- PMD/PMXモデルインポート
- VMDアニメーションインポート（ボーン、モーフ、カメラ、照明）
- 基本UI（Info、Material、Morph、Boneタブ）
- 日本語/英語UI
- Namespace対応
- ログビューア

## 既知の制限

- PMD/PMX/VMDエクスポートは未実装です。
- 物理演算対応は未完成です。
- VMDモーションは読み込み後に新しいモーションが正しく再生されない未修正の問題があります。`0.1.0`では、VMDの読み込み・解析は利用できますが、モーション再生は未完成として扱ってください。
- 大規模モデルでは動作が重くなる場合があります。
- 一部のPMXファイルで読み込みに失敗する場合があります。

## システム要件

### 必須要件

- **Maya**: 2024以降
- **OS**: Windows 11 / macOS 15.6
- **Python**: 3.7以降（Mayaに含まれています）

### 推奨スペック

- **RAM**: 16GB以上
- **GPU**: DirectX 11対応
- **ストレージ**: SSD推奨

## インストール

### ファイルのダウンロード

1. [GitHubリリースページ](https://github.com/yohawing/maya_mmd_tools/releases)から最新版をダウンロードします。
2. ZIPファイルを任意の場所に解凍します。

### `.mod` ファイルの配置

Maya MMD Tools本体は任意の場所に配置できます。Mayaの `modules` フォルダには、`maya_mmd_tools.mod` だけを配置します。

Windowsでは以下のフォルダを開きます。

```text
C:\Users\<ユーザー名>\Documents\maya\2024\modules
```

macOSでは以下のフォルダを開きます。

```text
~/Documents/maya/2024/modules
```

フォルダがない場合は作成してください。

次に、解凍したフォルダ内の `maya_mmd_tools.mod` をテキストエディタで開き、先頭行の末尾をMaya MMD Tools本体のフォルダパスに変更します。

```text
+ MAYAVERSION:2024 maya_mmd_tools 1.0 <解凍したフォルダのパス>
scripts:= .
MMD_TOOLS_ROOT:= .
MAYA_PLUG_IN_PATH:= ./mmd_tools
PYTHONPATH +:= .
```

例:

```text
+ MAYAVERSION:2024 maya_mmd_tools 1.0 C:/Tools/maya_mmd_tools
scripts:= .
MMD_TOOLS_ROOT:= .
MAYA_PLUG_IN_PATH:= ./mmd_tools
PYTHONPATH +:= .
```

パスにスペースが含まれる場合は引用符で囲みます。

```text
+ MAYAVERSION:2024 maya_mmd_tools 1.0 "C:/Program Files/maya_mmd_tools"
scripts:= .
MMD_TOOLS_ROOT:= .
MAYA_PLUG_IN_PATH:= ./mmd_tools
PYTHONPATH +:= .
```

修正した `maya_mmd_tools.mod` をMayaの `modules` フォルダへコピーします。

`userSetup.py` をMayaの scripts フォルダへ別途コピーする必要はありません。`.mod` の `scripts:= .` 設定により、Maya MMD Tools本体フォルダ内の `userSetup.py` が参照されます。

### プラグインの有効化

1. Mayaを起動します。既に起動している場合は再起動します。
2. `Window > Settings/Preferences > Plug-in Manager` を開きます。
3. `plugin_main.py` を探します。
4. `Loaded` にチェックを入れます。
5. 自動読み込みしたい場合は `Auto load` にもチェックを入れます。

## インストールの確認

### メニューで確認

Mayaのメニューバーに `MMD > MMD Tools` が追加されていることを確認します。

### コマンドで確認

MayaのScript Editorで以下を実行します。

```python
import mmd_tools
print(mmd_tools.__version__)
```

期待される出力:

```text
0.1.0
```

## クイックスタート

### モデルをインポートする

1. `MMD > MMD Tools` を選択します。
2. Import/ExportタブでPMXまたはPMDファイルを選択します。
3. `Import Model` をクリックします。

スクリプトから読み込む場合:

```python
from mmd_tools.io.mmd_importer import import_mmd_file

import_mmd_file("path/to/your/model.pmx")
```

インポートが成功すると、アウトライナーに `model_root` グループが作成され、ビューポートにモデルが表示されます。マテリアルとテクスチャも自動的に適用されます。

### MMD Tools UIを開く

1. `MMD > MMD Tools` を選択します。
2. MMD Tools UIウィンドウが開きます。
3. 各タブで設定を確認・調整できます。

主なタブ:

- **Info**: モデル情報の確認
- **Material**: マテリアル設定
- **Morph**: 表情の調整
- **Bone**: ボーン情報

### アニメーションをインポートする

VMDファイルがある場合:

1. `MMD > MMD Tools` を選択します。
2. Import/ExportタブでVMDファイルを選択します。
3. `Import Animation` をクリックします。
4. シーン内の対応するモデルにアニメーションが適用されます。

## モデルのインポート

### 対応フォーマット

- **PMX形式** (`.pmx`) - 推奨フォーマット
  - PMX 2.0
  - PMX 2.1
- **PMD形式** (`.pmd`) - レガシーフォーマット

### 基本的なインポート

```python
from mmd_tools.io.mmd_importer import import_mmd_file

import_mmd_file("C:/Models/character.pmx")
```

Namespace機能を有効にして読み込む場合:

```python
from mmd_tools.io.mmd_importer import import_mmd_file

options = {"use_namespace": True}
import_mmd_file("C:/Models/character.pmx", options=options)
```

### インポート設定

```python
from mmd_tools.core import settings

# スケール調整（MMDは通常cm単位）
settings.set("import.general.scale_factor", 1.0)

# Namespace使用（複数モデル対応）
settings.set("import.general.use_namespace", True)

# マテリアル作成
settings.set("import.model.create_mmd_shaders", True)

# 物理演算のインポート（早期リリースでは実験的）
settings.set("import.physics.import_physics", False)
```

### インポート後の構造

```text
model_root
├── mesh_root
│   └── model_mesh
├── bone_root
│   ├── センター
│   ├── 上半身
│   └── ...
└── morph_root
    └── blendShapes
```

モデルには以下のカスタムアトリビュートが追加されます。

- `mmd_model`: モデル識別用
- `mmd_model_name`: モデル名（日本語）
- `mmd_model_name_en`: モデル名（英語）
- `mmd_comment`: コメント

### 複数モデルのインポート

Namespace機能を使うと、同じ名前のボーンやメッシュの衝突を避けられます。同じモデルを複数回インポートした場合は、自動的に連番が付与されます。

```python
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core import settings

settings.set("import.general.use_namespace", True)

models = ["character1.pmx", "character2.pmx", "stage.pmx"]

for model_path in models:
    root_node = import_mmd_file(model_path)
    print(f"インポート完了: {root_node}")
```

### インポート後の調整

```python
import maya.cmds as cmds

cmds.select("model_root")
cmds.scale(0.1, 0.1, 0.1)
cmds.move(0, 0, 100)
```

## ログビューア

ログビューアは、プラグインの動作状況を確認し、問題の診断を支援するための機能です。ログレベル別の色分け表示、フィルタリング、検索、保存、自動スクロールに対応しています。

### ログレベル

- **DEBUG**: デバッグ情報
- **INFO**: 一般的な情報
- **WARNING**: 警告メッセージ
- **ERROR**: エラーメッセージ
- **CRITICAL**: 重大なエラー

### フィルタリングと検索

各ログレベルのチェックボックスで表示対象を絞り込めます。検索バーではキーワード検索ができ、検索結果を前後に移動できます。

### ログ管理

- **クリア**: 表示中のログを削除
- **保存**: 現在のログをテキストファイルに保存
- **自動スクロール**: 新しいログ追加時に最下部へ移動
- **タイムスタンプ**: 各ログメッセージに時刻を表示

ログエントリは最大10,000件まで保持されます。これを超えると古いログから削除されます。

### 問題診断での使い方

1. ログレベルを `DEBUG` に設定します。
2. PMX/PMD/VMDファイルをインポートします。
3. エラーや警告メッセージを確認します。
4. 必要に応じてログを保存し、バグレポートに添付します。

## トラブルシューティング

### プラグインがPlug-in Managerに表示されない

**原因**: モジュールファイルのパスが正しくない可能性があります。

**解決方法**:

1. `maya_mmd_tools.mod` を開きます。
2. 解凍先パスが正しいか確認します。
3. パスにスペースが含まれる場合は引用符で囲みます。

```text
+ MAYAVERSION:2024 maya_mmd_tools 1.0 "C:/Program Files/maya_mmd_tools"
```

### プラグインロード時にエラーが発生する

**原因**: Pythonモジュールが見つからない可能性があります。

**解決方法**:

```python
import sys
sys.path.append("C:/path/to/maya_mmd_tools")
import mmd_tools
```

### ファイルが見つからない

**原因**: ファイルパスに問題がある可能性があります。

**解決方法**:

- パスに日本語が含まれていないか確認します。
- バックスラッシュをスラッシュに変更します。

```python
# 誤
"C:\Models\model.pmx"

# 正
"C:/Models/model.pmx"
```

### モデルが白や灰色で表示される

**原因**: テクスチャが読み込まれていない可能性があります。

**解決方法**:

1. ビューポートを `Textured` モードに変更します（数字の `6` キー）。
2. テクスチャファイルがモデルと同じフォルダ、または参照先パスに存在するか確認します。
3. Hypershadeでファイルノードのパスを確認します。

```python
import maya.cmds as cmds

for node in cmds.ls(type="file"):
    path = cmds.getAttr(f"{node}.fileTextureName")
    print(f"{node}: {path}")
```

### ボーンが表示されない

**原因**: ジョイントの表示がオフになっている可能性があります。

**解決方法**:

- ビューポートメニューの `Show > Joints` を有効化します。
- または `Shift + J` を押します。

### モデル表示が重い

**原因**: ポリゴン数が多い、テクスチャが大きい、モーフが多いなどの可能性があります。

**解決方法**:

1. ビューポートの表示品質を下げます。
2. 不要なモーフを削除します。
3. 高解像度テクスチャを必要に応じて縮小します。
4. 表示レイヤーを活用して表示/非表示を管理します。

### メモリ不足エラー

**原因**: 大きなモデル、または多数のモデルを読み込んでいる可能性があります。

**解決方法**:

1. Mayaのメモリ上限を増やします。
2. モデルを部分的にインポートします。
3. 不要なヒストリを削除します。

```python
import maya.cmds as cmds

cmds.delete(all=True, constructionHistory=True)
```

### VMDインポート後の動きがおかしい

**原因**: ボーン名のマッピングや対象モデルの選択が正しくない可能性があります。

**解決方法**:

1. MMD Tools UIでボーン名を確認します。
2. Namespaceを使っている場合は、対象モデルのNamespaceが正しく検出されているか確認します。
3. VMDを適用したいモデルのルートやボーンを選択してから再実行します。

### モーフアニメーションが動作しない

**原因**: ブレンドシェイプの接続が切れている、またはVMD側のモーフ名とモデル側のモーフ名が一致していない可能性があります。

**解決方法**:

1. MMD Tools UIの `Morph` タブで接続を確認します。
2. モデルに対象モーフが存在するか確認します。
3. ログビューアでモーフ名関連の警告を確認します。

### MMD Tools UIが開かない

**原因**: プラグインがロードされていない、またはUI初期化に失敗している可能性があります。

**解決方法**:

```python
from mmd_tools.plugin_main import open_main_window

open_main_window()
```

### 日本語が文字化けする

**原因**: フォントまたはエンコーディングの問題の可能性があります。

**解決方法**:

1. MMD Tools UIの設定で言語を切り替えます。
2. システムのロケール設定を確認します。
3. ファイルパスに日本語が含まれていないか確認します。

### ログが表示されない

**解決方法**:

- 設定タブでログが有効になっているか確認します。
- ログレベルが適切に設定されているか確認します。
- フィルタですべてのレベルが非表示になっていないか確認します。

### ログファイルが保存できない

**解決方法**:

- 保存先のディレクトリに書き込み権限があるか確認します。
- ディスクの空き容量を確認します。

## ベストプラクティス

### ファイル整理

```text
ProjectFolder/
├── Models/
│   ├── character.pmx
│   └── textures/
│       ├── body.png
│       └── face.png
└── Motions/
    └── dance.vmd
```

### ネーミング

- 複数モデルを扱う場合はNamespaceを使用します。
- 日本語名は必要に応じて英数字へ変換します。
- バージョン番号を含めると管理しやすくなります（例: `character_v2`）。

### パフォーマンス

- 不要なモーフを削除します。
- 高解像度テクスチャは必要に応じて縮小します。
- 表示レイヤーを活用して表示/非表示を管理します。
- DEBUGログは問題診断時だけ有効化します。

## アンインストール

1. Mayaを終了します。
2. `modules/maya_mmd_tools.mod` を削除します。
3. インストールしたMaya MMD Toolsフォルダを削除します。

## サポート

問題が解決しない場合は、以下の情報を添えて [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) で報告してください。

- エラーメッセージ全文
- Mayaバージョン
- OS
- Maya MMD Toolsバージョン
- 使用したPMD/PMX/VMDファイルの種類
- 再現手順

開発者向けドキュメントは [docs-dev](../docs-dev/README.md) を参照してください。
