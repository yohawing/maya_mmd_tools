# Namespace設計仕様書

## 概要

Maya MMD Toolsにおける複数モデル読み込み時のNamespace戦略を定義します。本仕様書は、モデルインポート時の名前衝突を回避し、複数のMMDモデルを効率的に管理するためのガイドラインを提供します。

## 現状分析

### 既存実装の調査結果

1. **設定項目の存在**
   - `use_namespace` 設定が存在（default_settings.json）
   - UIに表示され、設定として保存される
   - しかし、実際のインポート処理では未使用

2. **VMDインポーターでの使用**
   - VMDインポート時にターゲットモデルのnamespaceを自動検出
   - `:` で区切られた最初の部分をnamespaceとして認識
   - アニメーション適用時に使用

3. **PMX/PMDインポーターでの未実装**
   - namespace機能は実装されていない
   - すべてのモデルがルートnamespaceに作成される

### Mayaのnamespace機能

1. **基本機能**
   - 階層的な名前空間管理
   - オブジェクト名の衝突回避
   - `namespace:objectName` 形式でのアクセス

2. **制約事項**
   - 使用不可文字: スペース、特殊文字（@#$%&など）
   - 数字で始まる名前は不可
   - 予約namespace: UI, shared
   - 空のnamespaceは自動削除される

3. **ベストプラクティス**
   - 深いネストは避ける（パフォーマンスへの影響）
   - 明確で一貫性のある命名規則
   - リファレンスファイルは自動的にnamespaceを持つ

## 設計方針

### Namespace命名規則

1. **基本ルール**
   - モデル名をベースにnamespaceを生成
   - 日本語名は英数字に変換（sanitize処理）
   - 重複時は連番を付与（例: model1, model2）

2. **命名パターン**
   ```
   {sanitized_model_name}[_{number}]
   
   例:
   - 初音ミク → Hatsune_Miku
   - 初音ミク（2体目）→ Hatsune_Miku_2
   ```

3. **特殊ケース**
   - モデル名が空: `MMDModel_{number}`
   - 変換後が空: `Model_{number}`

### 実装戦略

1. **use_namespace設定の動作**
   - `True`: モデルごとに独立したnamespaceを作成
   - `False`: すべてルートnamespace（デフォルト動作）

2. **Namespace作成タイミング**
   - インポート開始時にnamespaceを作成
   - namespace内でモデル構築を実行
   - エラー時はnamespace削除（クリーンアップ）

3. **オブジェクト階層**
   ```
   namespace:ModelRoot
   ├── namespace:Mesh_Group
   ├── namespace:Joint_Group
   └── namespace:Physics_Group
   ```

### ネストしたNamespaceの扱い

1. **基本方針**
   - 1階層のみ使用（深いネストは避ける）
   - サブコンポーネントは通常のグループで管理

2. **将来の拡張性**
   - プロジェクト単位のnamespace（オプション）
   - 例: `ProjectA:Character1:*`

## 実装計画

### フェーズ1: 基本実装

1. **Namespaceユーティリティの作成**
   - namespace生成・管理関数
   - 名前の重複チェック
   - sanitize処理の統合

2. **インポーター更新**
   - PMX/PMDインポーターにnamespace対応追加
   - use_namespace設定の反映

3. **エラーハンドリング**
   - namespace作成失敗時の処理
   - クリーンアップ機能

### フェーズ2: 高度な機能

1. **Namespace管理UI**
   - 既存namespaceの一覧表示
   - namespace間のオブジェクト移動
   - namespace名の変更

2. **VMDインポーター連携**
   - namespace対応モデルの自動検出強化
   - 複数モデルへの同時アニメーション適用

## テスト計画

1. **単体テスト**
   - namespace生成ロジック
   - 名前重複処理
   - エラーケース

2. **統合テスト**
   - 複数モデルの連続インポート
   - namespace有効/無効の切り替え
   - VMDアニメーション適用

## 今後の検討事項

1. **パフォーマンス最適化**
   - 大量モデル時の処理速度
   - namespace検索の効率化

2. **ユーザビリティ向上**
   - namespace自動提案
   - カスタム命名規則のサポート

3. **互換性**
   - 既存シーンとの互換性維持
   - 他のMayaプラグインとの連携