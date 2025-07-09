# Maya対応汎用ロガー設計書

## 概要

Maya上で動作する汎用的なロガーシステムの設計文書です。既存のmaya_mmd_toolsプロジェクトに統合可能な形で設計されており、Maya環境と非Maya環境の両方に対応します。

## 設計目標

- Maya環境に最適化されたログ出力システム
- 既存のsettingsシステムとの統合
- 日本語を含む多言語対応
- シンプルで軽量な設計
- 保守性の高い実装

## アーキテクチャ設計

### ファイル構成

```
mmd_tools/
├── core/
│   ├── logger.py          # メインロガークラス
│   ├── log_handlers.py    # 各種ログハンドラー
│   └── log_formatters.py  # ログフォーマッター
└── config/
    └── logging_config.json # ロガー設定ファイル
```

### クラス設計

#### 1. MayaLogger (core/logger.py)

```python
class MayaLogger:
    """Maya環境対応の汎用ログクラス"""
    
    def __init__(self, name: str, level: int = logging.INFO)
    def debug(self, message: str, *args, **kwargs)
    def info(self, message: str, *args, **kwargs)
    def warning(self, message: str, *args, **kwargs)
    def error(self, message: str, *args, **kwargs)
    def critical(self, message: str, *args, **kwargs)
    def set_level(self, level: int)
    def add_handler(self, handler: logging.Handler)
    def remove_handler(self, handler: logging.Handler)
```

#### 2. LogHandlers (core/log_handlers.py)

```python
class MayaScriptEditorHandler(logging.Handler):
    """Maya Script Editorへの出力ハンドラー"""
    
class MayaOutputWindowHandler(logging.Handler):
    """Maya Output Windowへの出力ハンドラー"""
    
class UTF8FileHandler(logging.FileHandler):
    """UTF-8対応ファイルハンドラー"""
    
class MayaDialogHandler(logging.Handler):
    """Maya UIダイアログ表示ハンドラー（ERROR/CRITICALレベル用）"""
```

#### 3. LogFormatters (core/log_formatters.py)

```python
class MayaFormatter(logging.Formatter):
    """Maya環境用カスタムフォーマッター"""
    
class CompactFormatter(logging.Formatter):
    """コンパクト表示用フォーマッター"""
```

## 機能仕様

### 1. ログレベル管理

| レベル | 数値 | 用途 |
|--------|------|------|
| DEBUG | 10 | 詳細なデバッグ情報 |
| INFO | 20 | 一般的な情報 |
| WARNING | 30 | 警告メッセージ |
| ERROR | 40 | エラー情報 |
| CRITICAL | 50 | 重大なエラー |

### 2. 出力先対応

#### Maya環境
- **Maya Script Editor**: `cmds.scriptEditor` APIを使用
- **Maya Output Window**: `sys.stdout`, `sys.stderr`への出力
- **Maya Dialog**: `cmds.confirmDialog` APIを使用（ERROR/CRITICALレベル用）

#### 共通
- **ファイル出力**: UTF-8エンコーディング対応
- **コンソール出力**: 標準出力・エラー出力

### 3. 設定システム統合

既存の`settings.py`システムと統合し、以下の設定項目を追加：

```json
{
  "logging": {
    "enabled": true,
    "level": "INFO",
    "log_file_path": "logs/mmd_tools.log"
  }
}
```

#### ハードコード設定項目

以下の設定項目はハードコードして、ユーザーが変更できないようにします：

```python
# 設定項目のハードコード値
LOGGING_CONFIG = {
    "file": {
        "max_size": 10485760,  # 10MB
        "backup_count": 5
    },
    "formatters": {
        "standard": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "compact": "%(levelname)s: %(message)s"
    },
    "handlers": {
        "console": {"enabled": True},
        "file": {"enabled": True}, 
        "maya_script_editor": {"enabled": True}
    }
}
```

### 4. 日本語対応

- **文字エンコーディング**: UTF-8統一
- **ファイル出力**: BOM付きUTF-8対応
- **Maya Script Editor**: 日本語表示確認
- **エラーメッセージ**: 日本語・英語両対応

### 5. パフォーマンス最適化

#### フィルタリング
- モジュール名による絞り込み
- 時間範囲による絞り込み
- レベル別フィルタ

#### キャッシュ
- フォーマッター結果のキャッシュ
- ハンドラー設定のキャッシュ

## 実装詳細

### 1. Maya環境検出

```python
def is_maya_environment():
    """Maya環境かどうかを判定"""
    try:
        import maya.cmds
        return True
    except ImportError:
        return False
```

### 2. ログファイル管理

```python
class RotatingFileHandler:
    """ログローテーション対応ファイルハンドラー"""
    
    def __init__(self, filename, max_bytes=10*1024*1024, backup_count=5):
        self.filename = filename
        self.max_bytes = max_bytes
        self.backup_count = backup_count
```

### 3. Mayaダイアログハンドラー実装

```python
class MayaDialogHandler(logging.Handler):
    """Maya UIダイアログ表示ハンドラー"""
    
    def __init__(self, level=logging.ERROR):
        super().__init__(level)
        
    def emit(self, record):
        """ログレコードをMayaダイアログとして表示"""
        if record.levelno >= logging.ERROR:
            title = "エラー" if record.levelno == logging.ERROR else "重大なエラー"
            message = self.format(record)
            
            # Maya環境でのみダイアログを表示
            if is_maya_environment():
                cmds.confirmDialog(
                    title=title,
                    message=message,
                    button=['OK'],
                    defaultButton='OK'
                )
```

### 4. エラーハンドリング

```python
class LoggerException(Exception):
    """ロガー固有の例外クラス"""
    pass

class HandlerException(LoggerException):
    """ハンドラー関連の例外"""
    pass
```

## 使用例

### 基本的な使用方法

```python
from mmd_tools.core.logger import get_logger

# ロガー取得
logger = get_logger('mmd_tools.importer')

# ログ出力
logger.info("PMXファイルをインポート開始")
logger.debug("ファイルパス: %s", filepath)
logger.warning("テクスチャファイルが見つかりません")
logger.error("インポートに失敗しました: %s", error_message)
```

### インポート失敗時のダイアログ表示

```python
from mmd_tools.core.logger import get_logger

logger = get_logger('mmd_tools.importer')

try:
    # PMXファイルのインポート処理
    import_pmx_file(filepath)
    logger.info("PMXファイルのインポートが完了しました")
except FileNotFoundError as e:
    # ERROR レベルでログ出力 -> ダイアログが自動表示される
    logger.error(f"ファイルが見つかりません: {filepath}")
except Exception as e:
    # CRITICAL レベルでログ出力 -> ダイアログが自動表示される
    logger.critical(f"予期しないエラーが発生しました: {str(e)}")
```

### 簡易的な進捗表示

```python
from mmd_tools.core.logger import get_logger

logger = get_logger('mmd_tools.converter')

for i, vertex in enumerate(vertices):
    # 処理
    if i % 100 == 0:  # 100個ごとに進捗を表示
        logger.info(f"頂点処理中: {i}/{len(vertices)}")
```

### 設定変更

```python
from mmd_tools.core.logger import get_logger
from mmd_tools.settings import settings

# ログ機能の有効/無効切り替え
settings.set('logging.enabled', False)

# ログレベル変更
settings.set('logging.level', 'DEBUG')

# ログファイルパス変更
settings.set('logging.log_file_path', 'custom/path/my_log.log')
```

## テスト計画

### 1. 単体テスト

```python
# tests/unit/test_logger.py
class TestMayaLogger:
    def test_basic_logging(self):
        """基本的なログ出力テスト"""
        
    def test_level_filtering(self):
        """ログレベルフィルタリングテスト"""
        
    def test_japanese_support(self):
        """日本語対応テスト"""
```

### 2. 統合テスト

```python
# tests/integration/test_logger_integration.py
class TestLoggerIntegration:
    def test_maya_environment(self):
        """Maya環境でのテスト"""
        
    def test_file_output(self):
        """ファイル出力テスト"""
        
    def test_settings_integration(self):
        """設定システム統合テスト"""
```

### 3. パフォーマンステスト

```python
# tests/performance/test_logger_performance.py
class TestLoggerPerformance:
    def test_bulk_logging(self):
        """大量ログ出力テスト"""
        
    def test_concurrent_logging(self):
        """並行ログ出力テスト"""
```

## 実装フェーズ

### フェーズ1: 基本機能
- MayaLoggerクラスの実装
- 基本的なハンドラー（コンソール、ファイル）
- 設定システム統合

### フェーズ2: Maya特化機能
- Maya Script Editorハンドラー
- Maya環境検出

### フェーズ3: 高度な機能
- ログローテーション
- フィルタリング

### フェーズ4: 最適化・安定化
- パフォーマンス最適化
- エラーハンドリング強化
- ドキュメント整備

## 既存コードとの統合

### 1. 既存のsetup_logger関数の置き換え

```python
# 既存のmaya_utils.py内の関数を置き換え
def setup_logger(logger_name):
    """既存関数の互換性維持"""
    return get_logger(logger_name)
```

### 2. 段階的移行

1. 新しいロガーシステムを並行実装
2. 既存コードの段階的移行
3. 旧システムの削除

### 3. 設定の移行

```python
# 既存設定の新システムへの移行
def migrate_logger_settings():
    """既存のログ設定を新システムに移行"""
    pass
```

## 参考資料

- [Python logging documentation](https://docs.python.org/3/library/logging.html)
- [Maya Python API documentation](https://help.autodesk.com/view/MAYAUL/2024/ENU/?guid=MAYA_API_REF_py_ref_index_html)
