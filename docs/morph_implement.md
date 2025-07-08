# MorphConverter 実装計画（簡略版）

## 概要

このドキュメントでは、MMDのモーフデータをMayaのblendShapeノードに変換する`MorphConverter`クラスのシンプルな実装について説明します。

## 設計方針

1. **シンプルさを優先**: 過度な抽象化を避け、実装をシンプルに保つ
2. **単一ファイル構成**: すべての機能を1つのファイルに統合
3. **頂点モーフに集中**: 最も重要な頂点モーフ機能のみを実装
4. **最小限の設定**: 必要最小限の設定項目のみを使用

## アーキテクチャ

### ファイル構成

```
mmd_tools/converters/morph_converter.py  # すべての機能を含む単一ファイル
```

### クラス構成

```python
class MorphConverter:
    """MMDのモーフデータをMayaのblendShapeに変換するクラス"""
    
    def __init__(self)
    def convert_pmd_morphs(self, pmd_data, mesh_node: str) -> Dict[str, Any]
    def convert_pmx_morphs(self, pmx_data, mesh_node: str) -> Dict[str, Any]
    def _convert_vertex_morph_pmd(self, morph, mesh_node: str) -> Dict[str, Any]
    def _convert_vertex_morph_pmx(self, morph, mesh_node: str) -> Dict[str, Any]
    def _apply_vertex_offsets_pmd(self, mesh_node: str, morph)
    def _apply_vertex_offsets_pmx(self, mesh_node: str, morph)
```

## 実装詳細

### 主要機能

1. **PMD/PMXモーフの変換**
   - 頂点モーフのみをサポート
   - エラーが発生しても処理を継続
   - 変換結果のサマリーを返す

2. **頂点オフセットの適用**
   - OpenMaya API 2.0を使用
   - 効率的な頂点位置の更新

3. **BlendShapeノードの管理**
   - 既存のblendShapeノードを再利用
   - ターゲットの自動追加

### 処理フロー

1. モーフデータをループ処理
2. ベースモーフはスキップ（PMDの場合）
3. メッシュを複製してターゲットを作成
4. 頂点オフセットを適用
5. blendShapeノードに追加
6. エラーは無視して次のモーフへ

## 設定

### 設定項目

```json
{
  "import": {
    "morph": {
      "import_morphs": true
    }
  }
}
```

- `import_morphs`: モーフをインポートするかどうか（デフォルト: true）

## エラーハンドリング

- 個別のモーフ変換でエラーが発生しても、処理全体は継続
- エラーは単純に無視され、次のモーフの処理に移る
- 最終的に成功したモーフ数を返す

## 将来の拡張

現在の実装では頂点モーフのみをサポートしていますが、将来的に以下の機能を追加可能：

- UVモーフ
- マテリアルモーフ
- ボーンモーフ
- グループモーフ

ただし、これらは必要になった時点で実装することとし、現時点では実装しません。

## 実装状況

### 完了済み

- ✅ MorphConverterクラスの実装
- ✅ PMD頂点モーフの変換
- ✅ PMX頂点モーフの変換
- ✅ 設定の簡略化
- ✅ 不要なコンポーネントの削除

### 削除された機能

- ❌ 個別のモーフハンドラークラス
- ❌ ファクトリーパターン
- ❌ バリデーター
- ❌ 詳細なロギング
- ❌ 複雑なエラーハンドリング
- ❌ 未実装のモーフタイプ

## まとめ

この簡略化された実装により、コードの保守性が大幅に向上し、理解しやすくなりました。必要最小限の機能に集中することで、安定性も向上しています。
