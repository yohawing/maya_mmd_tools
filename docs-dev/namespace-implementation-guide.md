# Namespace実装方針ドキュメント

## 概要

本ドキュメントは、Maya MMD ToolsにNamespace機能を実装するための技術的な方針と実装手順を定義します。

## 実装アーキテクチャ

### 新規モジュール構成

```
mmd_tools/
├── core/
│   └── namespace_utils.py  # 新規: Namespace管理ユーティリティ
├── io/
│   ├── pmx_importer.py    # 更新: Namespace対応
│   ├── pmd_importer.py    # 更新: Namespace対応
│   └── vmd_importer.py    # 更新: 既存namespace検出の改善
```

### NamespaceUtilsクラス

```python
class NamespaceUtils:
    """Namespace管理のためのユーティリティクラス"""
    
    @staticmethod
    def generate_namespace(model_name: str) -> str:
        """モデル名からnamespaceを生成"""
        
    @staticmethod
    def ensure_unique_namespace(base_name: str) -> str:
        """重複しないnamespaceを確保"""
        
    @staticmethod
    def create_namespace(namespace_name: str) -> bool:
        """namespaceを作成"""
        
    @staticmethod
    def set_namespace_context(namespace_name: str):
        """contextマネージャーでnamespace内で作業"""
        
    @staticmethod
    def cleanup_namespace(namespace_name: str):
        """エラー時のnamespaceクリーンアップ"""
```

## 実装手順

### Step 1: NamespaceUtilsの実装

1. **基本機能の実装**
   ```python
   # namespace_utils.py
   import re
   from contextlib import contextmanager
   from maya import cmds
   from ..core.logger import get_logger
   from ..core.utils import sanitize_text
   
   logger = get_logger(__name__)
   
   class NamespaceUtils:
       @staticmethod
       def generate_namespace(model_name: str) -> str:
           """モデル名からnamespaceを生成"""
           # 日本語を英数字に変換
           sanitized = sanitize_text(model_name)
           
           # 空の場合のデフォルト
           if not sanitized:
               sanitized = "MMDModel"
           
           # Maya namespace制約に合わせて調整
           # 数字で始まる場合は接頭辞を追加
           if sanitized[0].isdigit():
               sanitized = f"Model_{sanitized}"
           
           # 特殊文字を除去
           sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', sanitized)
           
           return sanitized
   ```

2. **重複チェックと連番付与**
   ```python
   @staticmethod
   def ensure_unique_namespace(base_name: str) -> str:
       """重複しないnamespaceを確保"""
       if not cmds.namespace(exists=base_name):
           return base_name
       
       # 連番を付与
       counter = 2
       while True:
           candidate = f"{base_name}_{counter}"
           if not cmds.namespace(exists=candidate):
               return candidate
           counter += 1
   ```

3. **Context Manager実装**
   ```python
   @staticmethod
   @contextmanager
   def namespace_context(namespace_name: str):
       """namespace内で作業するためのcontext manager"""
       # 現在のnamespaceを保存
       current_ns = cmds.namespaceInfo(currentNamespace=True)
       
       try:
           # namespaceが存在しない場合は作成
           if not cmds.namespace(exists=namespace_name):
               cmds.namespace(add=namespace_name)
           
           # namespaceに切り替え
           cmds.namespace(set=namespace_name)
           yield namespace_name
           
       finally:
           # 元のnamespaceに戻す
           cmds.namespace(set=current_ns)
   ```

### Step 2: インポーターの更新

1. **PMXインポーターの更新**
   ```python
   # pmx_importer.py の更新
   def import_pmx_file(parser, filepath, scale=1.0, options=None):
       # ... 既存のコード ...
       
       # Namespace対応
       use_namespace = options.get("use_namespace", False)
       namespace = None
       
       if use_namespace:
           # Namespace生成
           base_ns = NamespaceUtils.generate_namespace(model_name)
           namespace = NamespaceUtils.ensure_unique_namespace(base_ns)
           logger.info(f"Using namespace: {namespace}")
       
       try:
           with NamespaceUtils.namespace_context(namespace) if namespace else nullcontext():
               # ルートグループを作成
               root_group = cmds.group(empty=True, name=f"{model_name}{SCENE_ROOT_SUFFIX}")
               
               # ... 既存のモデル構築処理 ...
               
       except Exception as e:
           # エラー時のクリーンアップ
           if namespace:
               NamespaceUtils.cleanup_namespace(namespace)
           raise
   ```

2. **VMDインポーターの改善**
   ```python
   # vmd_importer.py の更新
   def _detect_target_namespace(target_model):
       """ターゲットモデルのnamespaceを検出（改善版）"""
       if ":" in target_model:
           # 明示的なnamespace指定
           return target_model.split(":")[0]
       
       # モデルルートノードからnamespace検出
       if cmds.objExists(target_model):
           node_namespace = cmds.namespaceInfo(target_model, namespace=True)
           if node_namespace and node_namespace != ":":
               return node_namespace
       
       return None
   ```

### Step 3: 設定の統合

1. **設定値の適用**
   - import_options に use_namespace を含める（既に実装済み）
   - インポーター内で options["use_namespace"] を参照

2. **UI更新は不要**
   - 既にUIにチェックボックスが存在
   - 設定の保存・読み込みも実装済み

## テスト実装

### ユニットテスト

```python
# tests/unit/test_namespace_utils.py
import unittest
from mmd_tools.core.namespace_utils import NamespaceUtils

class TestNamespaceUtils(unittest.TestCase):
    def test_generate_namespace(self):
        """namespace生成のテスト"""
        # 日本語
        self.assertEqual(
            NamespaceUtils.generate_namespace("初音ミク"),
            "Hatsune_Miku"
        )
        
        # 数字始まり
        self.assertEqual(
            NamespaceUtils.generate_namespace("01_model"),
            "Model_01_model"
        )
        
        # 特殊文字
        self.assertEqual(
            NamespaceUtils.generate_namespace("model@test"),
            "model_test"
        )
```

### 統合テスト

```python
# tests/integration/test_namespace_import.py
def test_multiple_model_import():
    """複数モデルのnamespace付きインポート"""
    # 同じモデルを2回インポート
    # 1回目: namespace = "Hatsune_Miku"
    # 2回目: namespace = "Hatsune_Miku_2"
```

## エラーハンドリング

1. **Namespace作成失敗**
   - 予約語や無効な文字の場合
   - 代替namespace名を生成

2. **インポート失敗時**
   - 作成したnamespaceを削除
   - namespace内のオブジェクトも含めて削除

3. **既存シーンとの互換性**
   - namespace無しのモデルも正常に扱える
   - VMDインポート時の後方互換性維持

## パフォーマンス考慮事項

1. **Namespace検索の最適化**
   - ワイルドカード検索は最小限に
   - キャッシュの活用

2. **大量モデル対応**
   - namespace一覧の効率的な管理
   - UI更新の最適化

## 今後の拡張

1. **Namespace管理UI**
   - 専用タブの追加検討
   - namespace一覧表示
   - リネーム機能

2. **高度な機能**
   - namespace間のオブジェクト移動
   - namespaceのマージ
   - テンプレートベースの命名

## 実装優先順位

1. **必須機能（フェーズ1）**
   - NamespaceUtilsの基本実装
   - PMX/PMDインポーターの更新
   - 基本的なエラーハンドリング

2. **追加機能（フェーズ2）**
   - Namespace管理UI
   - 高度なエラーリカバリ
   - パフォーマンス最適化