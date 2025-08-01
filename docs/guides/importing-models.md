# モデルのインポート

このガイドでは、PMD/PMXモデルファイルをMayaにインポートする詳細な方法を説明します。

## 対応フォーマット

Maya MMD Toolsは以下のモデルフォーマットに対応しています：

- **PMX形式** (.pmx) - 推奨フォーマット
  - PMX 2.0
  - PMX 2.1
- **PMD形式** (.pmd) - レガシーフォーマット

## 基本的なインポート

### メニューからインポート

1. Mayaで `File > Import...` を選択
2. ファイルタイプドロップダウンで `MMD Model (*.pmx;*.pmd)` を選択
3. モデルファイルを選択
4. `Import` ボタンをクリック

### スクリプトでインポート

```python
import maya.cmds as cmds

# PMXファイルをインポート
cmds.file("C:/Models/character.pmx", i=True, type="MMD Model")

# オプション付きでインポート
from mmd_tools.io.mmd_importer import import_mmd_file

# Namespace機能を有効にしてインポート
options = {"use_namespace": True}
import_mmd_file("C:/Models/character.pmx", options=options)
```

## インポートオプション

### インポート時の設定

インポート実行前に、以下の設定を調整できます：

```python
from mmd_tools.core import settings

# スケール調整（MMDは通常cm単位）
settings.set("import.general.scale_factor", 1.0)  # デフォルト: 1.0

# Namespace使用（複数モデル対応）
settings.set("import.general.use_namespace", True)  # デフォルト: False

# マテリアル作成
settings.set("import.model.create_mmd_shaders", True)  # デフォルト: True

# 物理演算のインポート
settings.set("import.physics.import_physics", False)  # デフォルト: False
```

## インポート後の構造

### シーン階層

インポート後、以下のような階層が作成されます：

```
model_root                    # ルートグループ
├── mesh_root                # メッシュグループ
│   └── model_mesh          # 実際のメッシュ
├── bone_root               # ボーングループ
│   ├── センター            # ルートボーン
│   ├── 上半身              # 上半身ボーン
│   └── ...                 # その他のボーン
└── morph_root              # モーフ（表情）グループ
    └── blendShapes         # ブレンドシェイプノード
```

### カスタムアトリビュート

モデルには以下のカスタムアトリビュートが追加されます：

- `mmd_model`: モデル識別用
- `mmd_model_name`: モデル名（日本語）
- `mmd_model_name_en`: モデル名（英語）
- `mmd_comment`: コメント

## 高度な使い方

### 複数モデルのインポート

#### Namespace機能を使用（推奨）

```python
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core import settings

# Namespace機能を有効化
settings.set("import.general.use_namespace", True)

# 複数のモデルをインポート（自動的に異なるNamespaceが割り当てられる）
models = ["character1.pmx", "character2.pmx", "stage.pmx"]

for model_path in models:
    root_node = import_mmd_file(model_path)
    print(f"インポート完了: {root_node}")
    # 例: Hatsune_Miku:model_root, Hatsune_Miku_2:model_root, stage:model_root
```

#### Namespace機能の利点

1. **名前の衝突回避**: 同じ名前のボーンやメッシュが衝突しない
2. **自動連番付与**: 同じモデルを複数回インポートすると自動的に連番が付く
3. **日本語名対応**: 日本語のモデル名を英数字に自動変換
4. **VMD適用が簡単**: Namespace単位でアニメーションを適用可能

### インポート後の調整

```python
# モデルのスケール調整
cmds.select("model_root")
cmds.scale(0.1, 0.1, 0.1)  # 10分の1にスケール

# 位置調整
cmds.move(0, 0, 100)  # Z方向に100単位移動
```

## トラブルシューティング

### テクスチャが見つからない

**症状**: モデルが白や灰色で表示される

**解決方法**:
1. テクスチャファイルがモデルと同じフォルダにあるか確認
2. テクスチャパスを手動で設定：
   ```python
   # Hypershadeでマテリアルを選択して
   cmds.setAttr("file1.fileTextureName", "C:/Textures/texture.png", type="string")
   ```

### 文字化けする

**症状**: モデル名やボーン名が文字化けする

**解決方法**:
- ファイルパスに日本語が含まれていないか確認
- システムロケールがUTF-8対応か確認

### メモリ不足エラー

**症状**: 大きなモデルでメモリエラーが発生

**解決方法**:
1. Mayaのメモリ上限を増やす
2. モデルを部分的にインポート
3. テクスチャサイズを縮小

## ベストプラクティス

### 1. ファイル整理
```
ProjectFolder/
├── Models/
│   ├── character.pmx
│   └── textures/        # テクスチャは同じフォルダに
│       ├── body.png
│       └── face.png
└── Motions/
    └── dance.vmd
```

### 2. ネーミング規則
- モデルごとに明確なネームスペースを使用
- 日本語名は避け、英数字を使用
- バージョン番号を含める（例: `character_v2`）

### 3. パフォーマンス最適化
- 不要なモーフを削除
- 高解像度テクスチャは必要に応じて縮小
- 表示レイヤーを活用して表示/非表示を管理

## 次のステップ

- [アニメーションのインポート](importing-motions.md) - VMDファイルの読み込み
- [マテリアル設定](materials.md) - マテリアルの調整方法
- [モーフ設定](morphs.md) - 表情の設定方法