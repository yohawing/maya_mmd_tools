# クイックスタート

このガイドでは、Maya MMD Toolsを使って最初のMMDモデルをインポートする方法を説明します。

## 前提条件

始める前に、以下が準備できていることを確認してください：

- Maya 2024がインストールされている
- Maya MMD Toolsがインストールされている（[インストールガイド](../installation/setup-guide.md)参照）
- PMXまたはPMDファイル（MMDモデル）
- VMDファイル（オプション：アニメーションデータ）

## ステップ1: プラグインの有効化

1. Mayaを起動します
2. メニューから `Window > Settings/Preferences > Plug-in Manager` を選択
3. `mmd_importer.py` を探して `Loaded` にチェック
4. 自動読み込みしたい場合は `Auto load` にもチェック

## ステップ2: 最初のモデルをインポート

### 方法1: メニューから

1. `File > Import...` を選択
2. ファイルタイプで `MMD Model (*.pmx;*.pmd)` を選択
3. インポートしたいモデルファイルを選択
4. `Import` をクリック

### 方法2: スクリプトから

```python
import maya.cmds as cmds
cmds.file("path/to/your/model.pmx", i=True, type="MMD Model")
```

## ステップ3: モデルの確認

インポートが成功すると：

- アウトライナーに `model_root` グループが作成されます
- ビューポートにモデルが表示されます
- マテリアルとテクスチャが自動的に適用されます

## ステップ4: MMD Tools UIを開く

1. メニューから `MMD Tools > Open MMD Tools` を選択
2. MMD Tools UIウィンドウが開きます
3. 各タブで設定を確認・調整できます：
   - **Info**: モデル情報の確認
   - **Material**: マテリアル設定
   - **Morph**: 表情の調整
   - **Bone**: ボーン情報

## ステップ5: アニメーションをインポート（オプション）

VMDファイルがある場合：

1. `File > Import...` を選択
2. ファイルタイプで `MMD Motion (*.vmd)` を選択
3. VMDファイルを選択して `Import`
4. アニメーションが自動的に適用されます

## よくある質問

### Q: モデルが表示されない
A: ビューポートで `F` キーを押してモデルにフォーカスしてみてください。

### Q: テクスチャが表示されない
A: ビューポートシェーディングが `Textured` モードになっているか確認してください（数字の `6` キー）。

### Q: 日本語が文字化けする
A: MMD Tools UIの設定で言語を切り替えてみてください。

## 次のステップ

- [基本的なワークフロー](basic-workflow.md) - より詳しい使い方
- [モデルのインポート](../guides/importing-models.md) - 高度なインポートオプション
- [UI操作ガイド](../guides/ui-overview.md) - UIの詳細な説明

## トラブルシューティング

問題が発生した場合は、[よくある問題](../troubleshooting/common-issues.md)を参照してください。