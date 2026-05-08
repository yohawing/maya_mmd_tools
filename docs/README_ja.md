# Maya MMD Tools ユーザードキュメント

Maya MMD Toolsは、Autodesk MayaでMikuMikuDance (MMD) のPMD/PMXモデルとVMDモーションを読み込むためのツールです。

現在は アルファ版のの早期リリースです。一部の機能が未実装もしくは不安定な場合があります。

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

## インストール

### ファイルのダウンロード

1. [GitHubリリースページ](https://github.com/yohawing/maya_mmd_tools/releases)から最新版をダウンロードします。
2. ZIPファイルを任意の場所に解凍します。

### `.mod` ファイルの配置

Maya MMD Tools本体は任意の場所に配置できます。Mayaの `modules` フォルダには、`maya_mmd_tools.mod` だけを配置します。

Windowsでは以下のフォルダを開きます。

```text
C:\Users\<ユーザー名>\Documents\maya\modules
```

macOSでは以下のフォルダを開きます。

```text
~/Documents/maya/modules
```

フォルダがない場合は作成してください。

次に、解凍したフォルダ内の `maya_mmd_tools.mod` をテキストエディタで開き、先頭行の末尾をMaya MMD Tools本体のフォルダパスに変更します。
2026部分を使用するMayaのバージョンに書き換えてください。

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 <解凍したフォルダのパス>
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

例:

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 C:/Tools/maya_mmd_tools
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

パスにスペースが含まれる場合は引用符で囲みます。

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 "C:/Program Files/maya_mmd_tools"
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

修正した `maya_mmd_tools.mod` をMayaの `modules` フォルダへコピーします。

`userSetup.py` をMayaの scripts フォルダへ別途コピーする必要はありません。`.mod` の `scripts: .` 設定により、Maya MMD Tools本体フォルダ内の `userSetup.py` が参照されます。

### プラグインの有効化

1. Mayaを起動します。既に起動している場合は再起動します。
2. `Window > Settings/Preferences > Plug-in Manager` を開きます。
3. `mmd_tools_plugin.py` を探します。
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
