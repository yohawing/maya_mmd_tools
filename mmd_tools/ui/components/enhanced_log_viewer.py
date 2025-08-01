"""
拡張版ログビューアウィジェット

ログレベル別の色分け、フィルタリング、検索機能を提供します。
"""

from ..qt_compat import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QToolBar,
    QAction, QCheckBox, QLineEdit, QPushButton, QLabel,
    QTextCursor, QTextCharFormat, QColor, Qt, Signal,
    QFileDialog, QMessageBox, QComboBox
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
    
    # ログレベルごとの色定義
    LOG_COLORS = {
        "DEBUG": "#808080",    # グレー
        "INFO": "#000000",     # 黒
        "WARNING": "#FFA500",  # オレンジ
        "ERROR": "#FF0000",    # 赤
        "CRITICAL": "#800080"  # 紫
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
        self.search_pattern = ""
        self.current_search_index = -1
        self.search_results: List[int] = []
        
        # ログレベルフィルタの状態
        self.level_filters = {
            "DEBUG": True,
            "INFO": True,
            "WARNING": True,
            "ERROR": True,
            "CRITICAL": True
        }
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        """UIをセットアップ"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # ツールバー
        self.toolbar = self._create_toolbar()
        layout.addWidget(self.toolbar)
        
        # フィルタウィジェット
        self.filter_widget = self._create_filter_widget()
        layout.addWidget(self.filter_widget)
        
        # 検索ウィジェット
        self.search_widget = self._create_search_widget()
        self.search_widget.setVisible(False)
        layout.addWidget(self.search_widget)
        
        # ログ表示エリア
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setFont(self.font())  # 等幅フォントを使用
        layout.addWidget(self.log_text_edit)
        
        # ステータスバー
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        self._update_status()
    
    def _create_toolbar(self) -> QToolBar:
        """ツールバーを作成"""
        toolbar = QToolBar()
        translator = UITranslator.instance()
        
        # クリアアクション
        clear_action = QAction(translator.translate("clear", "buttons"), self)
        clear_action.triggered.connect(self.clear_logs)
        toolbar.addAction(clear_action)
        
        # 保存アクション
        save_action = QAction(translator.translate("save", "buttons"), self)
        save_action.triggered.connect(self.save_logs)
        toolbar.addAction(save_action)
        
        toolbar.addSeparator()
        
        # 自動スクロール切り替え
        auto_scroll_action = QAction(translator.translate("auto_scroll", "buttons"), self)
        auto_scroll_action.setCheckable(True)
        auto_scroll_action.setChecked(self.auto_scroll)
        auto_scroll_action.toggled.connect(self.set_auto_scroll)
        toolbar.addAction(auto_scroll_action)
        
        # タイムスタンプ表示切り替え
        timestamp_action = QAction(translator.translate("timestamp", "buttons"), self)
        timestamp_action.setCheckable(True)
        timestamp_action.setChecked(self.show_timestamp)
        timestamp_action.toggled.connect(self.set_show_timestamp)
        toolbar.addAction(timestamp_action)
        
        toolbar.addSeparator()
        
        # 検索表示切り替え
        search_action = QAction(translator.translate("search", "buttons"), self)
        search_action.setCheckable(True)
        search_action.toggled.connect(self.toggle_search)
        toolbar.addAction(search_action)
        
        return toolbar
    
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
            checkbox.setChecked(True)
            checkbox.stateChanged.connect(self._on_filter_changed)
            self.level_checkboxes[level] = checkbox
            
            # レベルに応じた色でラベルを装飾
            color = self.LOG_COLORS[level]
            checkbox.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            
            layout.addWidget(checkbox)
        
        layout.addStretch()
        
        return widget
    
    def _create_search_widget(self) -> QWidget:
        """検索ウィジェットを作成"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 検索入力
        translator = UITranslator.instance()
        layout.addWidget(QLabel(translator.translate("search", "buttons") + ":"))
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self.find_next)
        layout.addWidget(self.search_input)
        
        # 前へ/次へボタン
        self.prev_button = QPushButton(translator.translate("previous", "buttons"))
        self.prev_button.clicked.connect(self.find_previous)
        layout.addWidget(self.prev_button)
        
        self.next_button = QPushButton(translator.translate("next", "buttons"))
        self.next_button.clicked.connect(self.find_next)
        layout.addWidget(self.next_button)
        
        # 検索結果ラベル
        self.search_result_label = QLabel()
        layout.addWidget(self.search_result_label)
        
        layout.addStretch()
        
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
        # フォーマット例: "[MMD] 2024-01-01 12:00:00 - module.name - INFO - message"
        pattern = r'\[MMD\].*?-\s*(\w+)\s*-\s*(.+)$'
        match = re.search(pattern, message)
        
        if match:
            level = match.group(1).upper()
            content = match.group(2)
            if level in self.LOG_COLORS:
                return level, content
        
        # パターンにマッチしない場合はINFOとして扱う
        return "INFO", message
    
    def _append_to_display(self, entry: LogEntry):
        """ログエントリを表示エリアに追加"""
        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        # テキストフォーマットを設定
        format = QTextCharFormat()
        format.setForeground(QColor(self.LOG_COLORS[entry.level]))
        
        # タイムスタンプを表示
        if self.show_timestamp:
            timestamp_text = f"[{entry.timestamp.strftime(self.timestamp_format)}] "
            cursor.insertText(timestamp_text, format)
        
        # ログレベルとメッセージを表示
        cursor.insertText(f"[{entry.level}] {entry.message}\n", format)
        
        # 自動スクロール
        if self.auto_scroll:
            self.log_text_edit.setTextCursor(cursor)
            self.log_text_edit.ensureCursorVisible()
    
    def _on_filter_changed(self):
        """フィルタ設定が変更された時の処理"""
        # フィルタ状態を更新
        for level, checkbox in self.level_checkboxes.items():
            self.level_filters[level] = checkbox.isChecked()
        
        # 表示を再構築
        self._rebuild_display()
    
    def _rebuild_display(self):
        """フィルタに基づいて表示を再構築"""
        self.log_text_edit.clear()
        
        for entry in self.log_entries:
            if self.level_filters[entry.level]:
                self._append_to_display(entry)
    
    def _update_status(self):
        """ステータスラベルを更新"""
        total = len(self.log_entries)
        visible = sum(1 for entry in self.log_entries if self.level_filters[entry.level])
        translator = UITranslator.instance()
        self.status_label.setText(f"{translator.translate('log_entries', 'labels')} {visible}/{total} {translator.translate('search_results', 'labels')}")
    
    def clear_logs(self):
        """ログをクリア"""
        self.log_entries.clear()
        self.log_text_edit.clear()
        self.search_results.clear()
        self._update_status()
    
    def save_logs(self):
        """ログをファイルに保存"""
        translator = UITranslator.instance()
        file_path, _ = QFileDialog.getSaveFileName(
            self, translator.translate("save", "buttons") + " " + translator.translate("log_entries", "labels"), "", "Text Files (*.txt);;All Files (*.*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    for entry in self.log_entries:
                        if self.level_filters[entry.level]:
                            f.write(entry.full_text + '\n')
                
                QMessageBox.information(self, translator.translate("save", "buttons"), f"{translator.translate('log_entries', 'labels')} saved to:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save logs:\n{str(e)}")
    
    def set_auto_scroll(self, enabled: bool):
        """自動スクロールの有効/無効を設定"""
        self.auto_scroll = enabled
    
    def set_show_timestamp(self, enabled: bool):
        """タイムスタンプ表示の有効/無効を設定"""
        self.show_timestamp = enabled
        self._rebuild_display()
    
    def toggle_search(self, checked: bool):
        """検索ウィジェットの表示/非表示を切り替え"""
        self.search_widget.setVisible(checked)
        if checked:
            self.search_input.setFocus()
    
    def _on_search_text_changed(self, text: str):
        """検索テキストが変更された時の処理"""
        self.search_pattern = text
        self.search_results.clear()
        self.current_search_index = -1
        
        if text:
            # 検索を実行
            self._perform_search()
        else:
            # ハイライトをクリア
            self._clear_highlights()
        
        self._update_search_status()
    
    def _perform_search(self):
        """検索を実行"""
        document = self.log_text_edit.document()
        cursor = QTextCursor(document)
        
        # 正規表現フラグを設定
        flags = Qt.CaseInsensitive
        
        while cursor.movePosition(QTextCursor.NextBlock):
            block_text = cursor.block().text()
            if self.search_pattern.lower() in block_text.lower():
                self.search_results.append(cursor.position())
    
    def _clear_highlights(self):
        """ハイライトをクリア"""
        cursor = self.log_text_edit.textCursor()
        cursor.select(QTextCursor.Document)
        format = QTextCharFormat()
        format.setBackground(QColor())
        cursor.mergeCharFormat(format)
    
    def find_next(self):
        """次の検索結果に移動"""
        if not self.search_results:
            return
        
        self.current_search_index = (self.current_search_index + 1) % len(self.search_results)
        self._highlight_current_result()
    
    def find_previous(self):
        """前の検索結果に移動"""
        if not self.search_results:
            return
        
        self.current_search_index = (self.current_search_index - 1) % len(self.search_results)
        self._highlight_current_result()
    
    def _highlight_current_result(self):
        """現在の検索結果をハイライト"""
        if self.current_search_index < 0 or self.current_search_index >= len(self.search_results):
            return
        
        # 現在の位置にカーソルを移動
        position = self.search_results[self.current_search_index]
        cursor = self.log_text_edit.textCursor()
        cursor.setPosition(position)
        
        # 検索パターンを選択
        cursor.movePosition(QTextCursor.StartOfBlock)
        block_text = cursor.block().text()
        index = block_text.lower().find(self.search_pattern.lower())
        if index >= 0:
            cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, index)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, len(self.search_pattern))
            
            # ハイライト
            format = QTextCharFormat()
            format.setBackground(QColor("#FFFF00"))  # 黄色
            cursor.mergeCharFormat(format)
            
            # カーソルを設定して表示
            self.log_text_edit.setTextCursor(cursor)
            self.log_text_edit.ensureCursorVisible()
        
        self._update_search_status()
    
    def _update_search_status(self):
        """検索ステータスを更新"""
        translator = UITranslator.instance()
        if self.search_results:
            self.search_result_label.setText(
                f"{self.current_search_index + 1}/{len(self.search_results)} {translator.translate('search_results', 'labels')}"
            )
        else:
            self.search_result_label.setText(translator.translate("not_found", "labels"))