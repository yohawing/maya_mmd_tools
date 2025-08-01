# ログビューア実装詳細

## 概要

ログビューア機能は、MMD Toolsのデバッグと問題診断を支援するための重要なコンポーネントです。
この文書では、ログビューアの実装詳細と今後の拡張方法について説明します。

## アーキテクチャ

### コンポーネント構成

```
mmd_tools/
├── core/
│   ├── logger.py          # ロガーシステムの中核
│   └── log_handlers.py    # QtLogHandler実装
└── ui/
    └── components/
        └── enhanced_log_viewer.py  # 拡張ログビューアUI
```

### クラス設計

#### EnhancedLogViewer

メインのログビューアウィジェット。以下の機能を提供：

- ログエントリの管理（LogEntryクラス）
- フィルタリング機能
- 検索機能
- ログの永続化

```python
class EnhancedLogViewer(QWidget):
    # ログレベルごとの色定義
    LOG_COLORS = {
        "DEBUG": "#808080",
        "INFO": "#000000",
        "WARNING": "#FFA500",
        "ERROR": "#FF0000",
        "CRITICAL": "#800080"
    }
    
    # 最大ログエントリ数
    MAX_LOG_ENTRIES = 10000
```

#### QtLogHandler

PythonのloggingモジュールとQt UIを接続：

```python
class QtLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._qt_bridge = QtLogBridge()
        formatter = logging.Formatter('[MMD] %(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.setFormatter(formatter)
```

## 実装詳細

### ログメッセージの処理フロー

1. **ログ生成**: `logger.info("message")` などでログ生成
2. **フォーマット**: QtLogHandlerでフォーマット適用
3. **シグナル送信**: QtLogBridgeを通じてQt Signalを発行
4. **UI更新**: EnhancedLogViewerがシグナルを受信し表示更新

### ログレベルの解析

```python
def _parse_log_message(self, message: str) -> Tuple[str, str]:
    """ログメッセージからレベルとコンテンツを解析"""
    pattern = r'\[MMD\].*?-\s*(\w+)\s*-\s*(.+)$'
    match = re.search(pattern, message)
    
    if match:
        level = match.group(1).upper()
        content = match.group(2)
        if level in self.LOG_COLORS:
            return level, content
    
    return "INFO", message
```

### メモリ管理

`collections.deque`を使用して効率的なメモリ管理を実現：

```python
self.log_entries: deque = deque(maxlen=self.MAX_LOG_ENTRIES)
```

### フィルタリング実装

各ログレベルの表示/非表示状態を辞書で管理：

```python
self.level_filters = {
    "DEBUG": True,
    "INFO": True,
    "WARNING": True,
    "ERROR": True,
    "CRITICAL": True
}
```

## 設定との連携

### リアルタイムログレベル変更

SettingsPresenterで設定変更を検知し、即座に反映：

```python
def on_log_level_changed(self):
    """ログレベルが変更されたときの処理"""
    if not self._loading:
        new_level = self.view.log_level_combo.currentText()
        level = getattr(logging, new_level, logging.INFO)
        logger.set_level(level)
```

## 国際化対応

UITranslatorを使用した多言語対応：

```python
translator = UITranslator.instance()
clear_action = QAction(translator.translate("clear", "buttons"), self)
```

## パフォーマンス考慮事項

### 最適化のポイント

1. **遅延レンダリング**: 表示されていないログはレンダリングしない
2. **バッチ更新**: 複数のログを一度に処理
3. **メモリ制限**: dequeによる自動的な古いログの削除

### ボトルネック

- 大量のログが短時間に生成される場合のUI更新
- 検索機能での全文検索

## 拡張ポイント

### 将来の機能追加候補

1. **ログのエクスポート形式追加**
   - JSON形式
   - CSV形式
   - HTML形式（色付き）

2. **高度なフィルタリング**
   - 正規表現フィルタ
   - モジュール名フィルタ
   - 時間範囲フィルタ

3. **ログ分析機能**
   - エラー頻度グラフ
   - パフォーマンス統計
   - 自動問題検出

4. **外部ツール連携**
   - ログ監視ツール
   - バグトラッキングシステム

### 実装時の注意点

1. **スレッドセーフティ**: Maya APIとの並行処理に注意
2. **メモリリーク**: 長時間実行時のメモリ使用量監視
3. **パフォーマンス**: UIの応答性を維持

## テスト方法

### 単体テスト

```python
# tests/integration/test_log_viewer.py
class TestEnhancedLogViewer(unittest.TestCase):
    def test_log_level_parsing(self):
        # ログレベル解析のテスト
    
    def test_filter_functionality(self):
        # フィルタ機能のテスト
```

### 統合テスト

Maya環境での動作確認：

```bash
python tests/run_tests.py --type integration --test test_log_viewer
```

### 手動テスト

1. Mayaを起動
2. MMD Toolsウィンドウを開く
3. 各種操作を実行してログを確認
4. フィルタ、検索、保存機能をテスト

## 既知の問題と回避策

### 文字エンコーディング

日本語環境での文字化けを防ぐため、UTF-8エンコーディングを明示的に指定：

```python
with open(file_path, 'w', encoding='utf-8') as f:
    # ログ保存処理
```

### Qt互換性

PySide2/PySide6の両方に対応するため、qt_compatモジュールを使用。

## リファレンス

- [Python logging documentation](https://docs.python.org/3/library/logging.html)
- [Qt Documentation - QTextEdit](https://doc.qt.io/qt-6/qtextedit.html)
- [Maya Python API - Logging](https://help.autodesk.com/view/MAYAUL/2024/ENU/)