# よくある問題と解決方法

このページでは、Maya MMD Toolsを使用する際によく発生する問題とその解決方法を説明します。

## インストール関連

### プラグインがPlug-in Managerに表示されない

**原因**: モジュールファイルのパスが正しくない

**解決方法**:
1. `maya_mmd_tools.mod` ファイルを開く
2. パスが正しいか確認：
   ```
   + maya_mmd_tools 0.1.0 C:/Tools/maya_mmd_tools
   ```
3. パスにスペースが含まれる場合は引用符で囲む：
   ```
   + maya_mmd_tools 0.1.0 "C:/Program Files/maya_mmd_tools"
   ```

### プラグインロード時にエラーが発生する

**原因**: Pythonモジュールが見つからない

**解決方法**:
```python
# スクリプトエディタで実行
import sys
sys.path.append("C:/path/to/maya_mmd_tools")
import mmd_tools
```

## インポート関連

### 「ファイルが見つかりません」エラー

**原因**: ファイルパスに問題がある

**解決方法**:
- パスに日本語が含まれていないか確認
- バックスラッシュをスラッシュに変更：
  ```python
  # 誤: "C:\Models\model.pmx"
  # 正: "C:/Models/model.pmx"
  ```

### モデルが真っ白/真っ黒で表示される

**原因**: テクスチャが読み込まれていない

**解決方法**:
1. ビューポートを`Textured`モードに変更（数字の`6`キー）
2. テクスチャファイルの存在を確認
3. Hypershadeでファイルノードのパスを確認：
   ```python
   # すべてのファイルノードのパスを表示
   import maya.cmds as cmds
   for node in cmds.ls(type="file"):
       path = cmds.getAttr(f"{node}.fileTextureName")
       print(f"{node}: {path}")
   ```

### ボーンが表示されない

**原因**: ジョイントの表示がオフになっている

**解決方法**:
1. ビューポートメニュー: `Show > Joints` を有効化
2. またはホットキー: `Shift + J`

## パフォーマンス関連

### モデル表示が重い

**原因**: ポリゴン数が多い、テクスチャが大きい

**解決方法**:
1. ビューポートの表示品質を下げる：
   - `Renderer > Viewport 2.0 > Anti-aliasing` をオフ
2. 不要なモーフを削除：
   ```python
   # 使用していないブレンドシェイプターゲットを削除
   cmds.blendShape("blendShape1", edit=True, remove=True, target=["unused_morph"])
   ```
3. プロキシモデルを使用

### メモリ不足エラー

**原因**: 大きなモデルまたは多数のモデル

**解決方法**:
1. Maya.envファイルでメモリ上限を増やす：
   ```
   MAYA_MEMORY_LIMIT=4096
   ```
2. 不要なヒストリを削除：
   ```python
   cmds.delete(all=True, constructionHistory=True)
   ```

## アニメーション関連

### VMDインポート後、動きがおかしい

**原因**: ボーン名のマッピングが正しくない

**解決方法**:
1. MMD Tools UIでボーン名を確認
2. 手動でボーンをマッピング：
   ```python
   # ボーン名の対応を確認
   from mmd_tools.core import bone_mapper
   mapper = bone_mapper.BoneMapper()
   mapper.print_mapping()
   ```

### モーフアニメーションが動作しない

**原因**: ブレンドシェイプの接続が切れている

**解決方法**:
1. MMD Tools UIの`Morph`タブで接続を確認
2. 自動接続機能を使用：
   - `Morph`タブで`Auto Connect`ボタンをクリック

## UI関連

### MMD Tools UIが開かない

**原因**: UIファイルが見つからない

**解決方法**:
```python
# 手動でUIを開く
from mmd_tools.ui import main_window
window = main_window.MMDToolsWindow()
window.show()
```

### 日本語が文字化けする

**原因**: フォントまたはエンコーディングの問題

**解決方法**:
1. MMD Tools UIの設定で言語を切り替える
2. システムのロケール設定を確認
3. Mayaの言語設定を確認

## その他の問題

### エラーログの確認方法

```python
# スクリプトエディタでログレベルを上げる
import logging
logging.basicConfig(level=logging.DEBUG)

# MMD Toolsのログを有効化
from mmd_tools.core import logger
log = logger.get_logger("mmd_tools")
log.setLevel(logging.DEBUG)
```

### 問題が解決しない場合

1. エラーメッセージ全文をコピー
2. 使用環境の情報を収集：
   - Mayaバージョン
   - OS
   - Maya MMD Toolsバージョン
3. [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues)で報告

### デバッグモード

詳細なデバッグ情報を取得：
```python
from mmd_tools.core import settings
settings.set("debug_mode", True)
```

## 関連ページ

- [インポートエラー](import-errors.md) - インポート特有のエラー
- [パフォーマンス問題](performance.md) - パフォーマンス最適化
- [インストールガイド](../installation/setup-guide.md) - インストール手順