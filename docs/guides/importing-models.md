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

# ネームスペースを指定してインポート
cmds.file("C:/Models/character.pmx", i=True, type="MMD Model", namespace="character1")
```

## インポートオプション

### インポート時の設定

インポート実行前に、以下の設定を調整できます：

```python
from mmd_tools.core import settings

# スケール調整（MMDは通常cm単位）
settings.set("import.scale", 1.0)  # デフォルト: 1.0

# テクスチャパスの解決
settings.set("import.auto_find_textures", True)  # デフォルト: True

# マテリアル作成
settings.set("import.create_materials", True)  # デフォルト: True
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

```python
# 複数のモデルを異なるネームスペースでインポート
models = [
    ("character1.pmx", "char1"),
    ("character2.pmx", "char2"),
    ("stage.pmx", "stage")
]

for model_path, namespace in models:
    cmds.file(model_path, i=True, type="MMD Model", namespace=namespace)
```

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