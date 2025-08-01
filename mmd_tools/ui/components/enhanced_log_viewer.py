"""
拡張版ログビューアウィジェット

ログレベル別の色分け、フィルタリング機能を提供します。
"""

from ..qt_compat import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QCheckBox, QPushButton, QLabel,
    QTextCursor, QTextCharFormat, QColor, Qt, QSettings
)
from ..translations import UITranslator
import re
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Tuple


class LogEntry:
    """ログエントリのデータクラス"""
    def __init__(self, level: str, message: str, timestamp: datetime):
        self.level = level
        self.message = message
        self.timestamp = timestamp
        self.full_text = f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"


class EnhancedLogViewer(QWidget):
    """拡張版ログビューア"""
    
    # ログレベルごとの色定義（ダークテーマ用）
    LOG_COLORS = {
        "DEBUG": "#888888",    # 明るいグレー
        "INFO": "#FFFFFF",     # 白
        "WARNING": "#FFB347",  # 明るいオレンジ
        "ERROR": "#FF6B6B",    # 明るい赤
        "CRITICAL": "#FF00FF"  # マゼンタ
    }
    
    # 最大ログ行数
    MAX_LOG_ENTRIES = 10000
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.log_entries: deque = deque(maxlen=self.MAX_LOG_ENTRIES)
        self.filtered_entries: List[LogEntry] = []
        self.auto_scroll = True
        self.show_timestamp = True
        self.timestamp_format = "%Y-%m-%d %H:%M:%S"
        
        # QSettingsを初期化
        self.settings = QSettings("maya_mmd_tools", "EnhancedLogViewer")
        
        # ログレベルフィルタの状態（保存された設定を読み込み）
        self.level_filters = {
            "DEBUG": self.settings.value("filter_debug", "true").lower() == "true",
            "INFO": self.settings.value("filter_info", "true").lower() == "true",
            "WARNING": self.settings.value("filter_warning", "true").lower() == "true",
            "ERROR": self.settings.value("filter_error", "true").lower() == "true",
            "CRITICAL": self.settings.value("filter_critical", "true").lower() == "true"
        }
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        """UIをセットアップ"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # フィルタウィジェット
        self.filter_widget = self._create_filter_widget()
        layout.addWidget(self.filter_widget)
        
        # ログ表示エリア
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setFont(self.font())  # 等幅フォントを使用
        layout.addWidget(self.log_text_edit)
        
        # ステータスバー
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        self._update_status()
    
    def _create_filter_widget(self) -> QWidget:
        """フィルタウィジェットを作成"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        translator = UITranslator.instance()
        layout.addWidget(QLabel(translator.translate("filter", "buttons") + ":"))
        
        # ログレベルごとのチェックボックス
        self.level_checkboxes: Dict[str, QCheckBox] = {}
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            checkbox = QCheckBox(level)
            checkbox.setChecked(self.level_filters[level])  # 保存された状態を反映
            checkbox.stateChanged.connect(self._on_filter_changed)
            self.level_checkboxes[level] = checkbox
            
            # レベルに応じた色でラベルを装飾
            color = self.LOG_COLORS[level]
            checkbox.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            
            layout.addWidget(checkbox)
        
        layout.addStretch()
        
        # クリアボタン
        self.clear_button = QPushButton(translator.translate("clear", "buttons"))
        self.clear_button.clicked.connect(self.clear_logs)
        layout.addWidget(self.clear_button)
        
        return widget
    
    def _connect_signals(self):
        """シグナルを接続"""
        pass
    
    def append(self, message: str):
        """ログメッセージを追加"""
        # ログレベルとメッセージを解析
        level, content = self._parse_log_message(message)
        timestamp = datetime.now()
        
        # ログエントリを作成
        entry = LogEntry(level, content, timestamp)
        self.log_entries.append(entry)
        
        # フィルタが有効な場合は表示を更新
        if self.level_filters[level]:
            self._append_to_display(entry)
        
        # ステータスを更新
        self._update_status()
    
    def _parse_log_message(self, message: str) -> Tuple[str, str]:
        """ログメッセージからレベルとコンテンツを解析"""
        # フォーマット例: "[MMD] 2024-01-01 12:00:00,123 - module.name - INFO - message"
        # より柔軟なパターンでログレベルを抽出
        pattern = r'-\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+-\s+(.+)$'
        match = re.search(pattern, message)
        
        if match:
            level = match.group(1).upper()
            content = match.group(2)
            return level, content
        
        # パターンに一致しない場合はINFOレベルとして扱う
        return "INFO", message
    
    def _append_to_display(self, entry: LogEntry):
        """ログエントリを表示に追加"""
        # カーソルを末尾に移動
        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # フォーマットを設定
        format = QTextCharFormat()
        color = QColor(self.LOG_COLORS[entry.level])
        format.setForeground(color)
        
        # タイムスタンプを含むメッセージを作成
        if self.show_timestamp:
            display_text = entry.full_text
        else:
            display_text = f"[{entry.level}] {entry.message}"
        
        # テキストを挿入
        cursor.insertText(display_text + "\n", format)
        
        # 自動スクロール
        if self.auto_scroll:
            self.log_text_edit.setTextCursor(cursor)
            self.log_text_edit.ensureCursorVisible()
    
    def clear_logs(self):
        """ログをクリア"""
        self.log_entries.clear()
        self.log_text_edit.clear()
        self._update_status()
    
    def _on_filter_changed(self):
        """フィルタが変更されたときの処理"""
        # フィルタ状態を更新して保存
        for level, checkbox in self.level_checkboxes.items():
            self.level_filters[level] = checkbox.isChecked()
            # 設定を保存
            self.settings.setValue(f"filter_{level.lower()}", str(checkbox.isChecked()))
        
        # 表示を再構築
        self._rebuild_display()
    
    def _rebuild_display(self):
        """フィルタに基づいて表示を再構築"""
        self.log_text_edit.clear()
        
        for entry in self.log_entries:
            if self.level_filters[entry.level]:
                self._append_to_display(entry)
    
    def _update_status(self):
        """ステータスバーを更新"""
        translator = UITranslator.instance()
        total = len(self.log_entries)
        
        # フィルタされた数をカウント
        filtered = sum(1 for entry in self.log_entries if self.level_filters[entry.level])
        
        if total == 0:
            status_text = f"0 {translator.translate('log_entries', 'labels')}"
        elif total == filtered:
            status_text = f"{total} {translator.translate('log_entries', 'labels')}"
        else:
            status_text = f"{filtered}/{total} {translator.translate('log_entries', 'labels')}"
        
        self.status_label.setText(status_text)
    
    def set_auto_scroll(self, enabled: bool):
        """自動スクロールの有効/無効を設定"""
        self.auto_scroll = enabled
    
    def set_show_timestamp(self, enabled: bool):
        """タイムスタンプ表示の有効/無効を設定"""
        self.show_timestamp = enabled
        self._rebuild_display()
    
    def retranslateUi(self):
        """UIテキストを再翻訳"""
        translator = UITranslator.instance()
        
        # フィルタラベル
        filter_label = self.filter_widget.layout().itemAt(0).widget()
        if isinstance(filter_label, QLabel):
            filter_label.setText(translator.translate("filter", "buttons") + ":")
        
        # クリアボタン
        self.clear_button.setText(translator.translate("clear", "buttons"))
        
        # ステータスを更新
        self._update_status()