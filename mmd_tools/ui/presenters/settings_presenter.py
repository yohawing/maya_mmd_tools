from ..qt_compat import QFileDialog, QMessageBox
from ...core import settings_keys as setting_keys
from ...core.logger import get_logger
from ...services.settings_service import SettingsService
from ..translations import UITranslator

logger = get_logger(__name__)


class SettingsPresenter:
    _COMMAND_PORT_BUTTON_FALLBACKS = {
        "open_command_port": {
            "ja": "開く",
            "en": "Open",
            "zh-TW": "開啟",
            "zh-CN": "打开",
        },
        "close_command_port": {
            "ja": "閉じる",
            "en": "Close",
            "zh-TW": "關閉",
            "zh-CN": "关闭",
        },
    }

    def __init__(self, view, app_state, settings_service=None, maya_cmds=None):
        self.view = view
        self.app_state = app_state
        self.settings_service = settings_service or SettingsService()
        self._maya_cmds = maya_cmds
        self._loading = False

        # ウィジェットの存在を確認
        logger.debug("SettingsPresenter initialization started")
        logger.debug(f"View object: {view}")
        logger.debug(f"View type: {type(view)}")

        # デバッグ：viewの属性をリスト
        if hasattr(view, "__dict__"):
            logger.debug(f"View attributes: {list(view.__dict__.keys())}")

        try:
            self.connect_signals()
            self.load_settings()
            logger.debug("SettingsPresenter initialization completed successfully")
        except AttributeError as e:
            logger.error(f"Widget not found in SettingsTab: {e}")
            import traceback

            traceback.print_exc()
            # 最小限の初期化のみ行う

    def connect_signals(self):
        """すべてのシグナルを接続"""
        try:
            # ボタンシグナル
            self.view.save_settings_btn.clicked.connect(self.save_all_settings)
            self.view.reset_settings_btn.clicked.connect(self.reset_to_defaults)
            self.view.export_settings_btn.clicked.connect(self.export_settings)
            self.view.import_settings_btn.clicked.connect(self.import_settings)

            # 全般設定
            self.view.development_mode_check.stateChanged.connect(self.on_development_mode_changed)
            self.view.logging_enabled_check.stateChanged.connect(self.on_setting_changed)
            self.view.log_level_combo.currentTextChanged.connect(self.on_log_level_changed)
            self.view.log_file_browse_btn.clicked.connect(self.browse_log_file)
            if hasattr(self.view, "open_command_port_btn"):
                self.view.open_command_port_btn.clicked.connect(self.toggle_command_port)
            if hasattr(self.view, "command_port_spin") and hasattr(self.view.command_port_spin, "valueChanged"):
                self.view.command_port_spin.valueChanged.connect(lambda *_args: self.refresh_command_port_status())

            # 言語設定
            if hasattr(self.view, "language_combo"):
                self.view.language_combo.currentIndexChanged.connect(self.on_language_changed)

        except AttributeError as e:
            logger.error(f"Failed to connect signal: {e}")
            import traceback

            traceback.print_exc()
            raise

    def tr(self, key: str) -> str:
        """Translate a settings presenter message key."""
        return UITranslator.instance().translate(key, "messages")

    def tr_format(self, key: str, **kwargs) -> str:
        """Translate and format a settings presenter message template."""
        return self.tr(key).format(**kwargs)

    def load_settings(self):
        """設定をロード"""
        self._loading = True

        try:
            state = self.settings_service.load_settings_tab_state()
            # UI設定
            self.view.development_mode_check.setChecked(state["development_mode"])
            if hasattr(self.view, "command_port_spin"):
                self.view.command_port_spin.setValue(int(state.get("command_port", 3939)))
            self._refresh_dev_tools_visibility(state["development_mode"])
            if state["development_mode"]:
                self._auto_open_command_port_if_needed(emit_status=False)

            # ログ設定
            self.view.logging_enabled_check.setChecked(state["logging_enabled"])
            log_level = state["logging_level"]
            index = self.view.log_level_combo.findText(log_level)
            if index >= 0:
                self.view.log_level_combo.setCurrentIndex(index)
            self.view.log_file_path_edit.setText(state["log_file_path"])

            # 言語設定
            if hasattr(self.view, "language_combo"):
                current_language = state["language"]
                for i in range(self.view.language_combo.count()):
                    if self.view.language_combo.itemData(i) == current_language:
                        self.view.language_combo.setCurrentIndex(i)
                        break

        except Exception as e:
            logger.error(f"Failed to load settings: {e}", exc_info=True)

        finally:
            self._loading = False

    def on_development_mode_changed(self):
        """Development Mode チェックボックス変更時の処理。

        Dev ON → logging.level を INFO に設定。
        Dev OFF → WARNING に設定。コンボボックス UI も同期する。
        """
        if self._loading:
            return
        import logging

        dev_on = self.view.development_mode_check.isChecked()
        level_str = self.settings_service.set_development_mode_log_levels(dev_on)
        self._refresh_dev_tools_visibility(dev_on)
        if dev_on:
            self._auto_open_command_port_if_needed(emit_status=True)
        else:
            self.close_command_port(emit_status=False)

        # コンボボックスの表示を更新
        idx = self.view.log_level_combo.findText(level_str)
        if idx >= 0:
            self.view.log_level_combo.setCurrentIndex(idx)

        # ロガーに即座に適用
        level = getattr(logging, level_str, logging.WARNING)
        logger.set_level(level)
        logger.info(f"Development Mode {'enabled' if dev_on else 'disabled'}: log level set to {level_str}")

    def on_setting_changed(self):
        """設定が変更されたときの処理"""
        if not self._loading:
            pass  # 必要に応じて自動保存などを実装

    def _refresh_dev_tools_visibility(self, enabled):
        """Show development-only tools only while Development Mode is enabled."""
        if hasattr(self.view, "dev_tools_group"):
            self.view.dev_tools_group.setVisible(bool(enabled))
        self.refresh_command_port_status()

    def _get_maya_cmds(self):
        """Return maya.cmds, allowing tests to inject a fake command module."""
        if self._maya_cmds is not None:
            return self._maya_cmds
        from maya import cmds

        return cmds

    def _command_port_value(self):
        """Return the current commandPort value from the settings UI."""
        if hasattr(self.view, "command_port_spin"):
            return int(self.view.command_port_spin.value())
        return int(self.settings_service.get(setting_keys.UI_DEV_COMMAND_PORT, 3939))

    def _command_port_name(self):
        """Return Maya commandPort name syntax for the selected port."""
        return f":{self._command_port_value()}"

    def _is_command_port_open(self):
        """Return whether the currently selected commandPort is open in Maya."""
        try:
            cmds = self._get_maya_cmds()
            return bool(cmds.commandPort(self._command_port_name(), query=True))
        except Exception:
            logger.debug("Failed to query commandPort state", exc_info=True)
            return False

    def _command_port_button_text(self, key):
        """Return translated commandPort button text without warning on stale caches."""
        translator = UITranslator.instance()
        translations = getattr(translator, "_translations", {})
        current_language = translator.get_language()

        for language in (current_language, "en"):
            buttons = translations.get(language, {}).get("buttons", {})
            if key in buttons:
                return buttons[key]

        fallback = self._COMMAND_PORT_BUTTON_FALLBACKS.get(key, {})
        return fallback.get(current_language, fallback.get("en", key))

    def refresh_command_port_status(self):
        """Synchronize the commandPort button label with Maya state."""
        if not hasattr(self.view, "open_command_port_btn"):
            return
        key = "close_command_port" if self._is_command_port_open() else "open_command_port"
        self.view.open_command_port_btn.setText(self._command_port_button_text(key))

    def _auto_open_command_port_if_needed(self, emit_status=False):
        """Open the configured commandPort automatically for Development Mode."""
        if not hasattr(self.view, "command_port_spin"):
            return
        if self._is_command_port_open():
            self.refresh_command_port_status()
            return
        self.open_command_port(emit_status=emit_status)

    def on_log_level_changed(self):
        """ログレベルが変更されたときの処理"""
        if not self._loading:
            new_level = self.view.log_level_combo.currentText()
            # ログレベルを即座に更新
            import logging

            level = getattr(logging, new_level, logging.INFO)
            logger.set_level(level)
            logger.info(f"Changed log level to {new_level}")
            # 設定も同時に保存
            self.on_setting_changed()

    def _refresh_development_mode_visibility(self):
        """現在のメインウィンドウに Development Mode 表示を再適用する。"""
        main_window = self.view.window()
        if hasattr(main_window, "refresh_development_mode_visibility"):
            main_window.refresh_development_mode_visibility()
            return

        # Unit tests and older host windows may only expose the Import/Export tab.
        import_export_tab = getattr(main_window, "import_export_tab", None)
        if hasattr(import_export_tab, "_apply_dev_mode_visibility"):
            import_export_tab._apply_dev_mode_visibility()

    def save_all_settings(self):
        """すべての設定を保存"""
        try:
            state = {
                "development_mode": self.view.development_mode_check.isChecked(),
                "logging_enabled": self.view.logging_enabled_check.isChecked(),
                "logging_level": self.view.log_level_combo.currentText(),
                "log_file_path": self.view.log_file_path_edit.text(),
            }
            if hasattr(self.view, "command_port_spin"):
                state["command_port"] = self.view.command_port_spin.value()
            if hasattr(self.view, "language_combo"):
                state["language"] = self.view.language_combo.currentData()

            self.settings_service.save_settings_tab_state(state)
            self._refresh_development_mode_visibility()
            logger.info("Settings saved")
            self.app_state.emit_status(self.tr("settings_saved"))

        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            self.app_state.emit_status(self.tr_format("settings_save_failed", error=str(e)))

    def reset_to_defaults(self):
        """デフォルト設定に戻す"""
        reply = QMessageBox.question(
            self.view,
            self.tr("settings_reset_confirm_title"),
            self.tr("settings_reset_confirm_message"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # JSON のデフォルト値で optionVar を上書きしてから UI に反映する。
            # （以前は load_settings() のみで、実際にはリセットされていなかった）
            self.settings_service.reset()
            self.load_settings()
            self._refresh_development_mode_visibility()
            self.app_state.emit_status(self.tr("settings_reset_to_defaults"))

    def export_settings(self):
        """設定をファイルにエクスポート"""
        file_path, _ = QFileDialog.getSaveFileName(
            self.view,
            "Export Settings",
            "mmd_tools_settings.json",
            "JSON Files (*.json)",
        )

        if file_path:
            try:
                self.settings_service.write_settings_json(file_path)

                logger.info(f"Exported settings: {file_path}")
                self.app_state.emit_status(self.tr("settings_exported"))

            except Exception as e:
                logger.error(f"Failed to export settings: {e}", exc_info=True)
                self.app_state.emit_status(self.tr_format("settings_export_failed", error=str(e)))

    def import_settings(self):
        """設定をファイルからインポート"""
        file_path, _ = QFileDialog.getOpenFileName(self.view, "Import Settings", "", "JSON Files (*.json)")

        if file_path:
            try:
                self.settings_service.import_settings_json(file_path)

                # UIを更新
                self.load_settings()
                self._refresh_development_mode_visibility()

                logger.info(f"Imported settings: {file_path}")
                self.app_state.emit_status(self.tr("settings_imported"))

            except Exception as e:
                logger.error(f"Failed to import settings: {e}", exc_info=True)
                self.app_state.emit_status(self.tr_format("settings_import_failed", error=str(e)))

    def browse_log_file(self):
        """ログファイルのパスを選択"""
        file_path, _ = QFileDialog.getSaveFileName(
            self.view,
            "Select Log File",
            self.view.log_file_path_edit.text() or "mmd_tools.log",
            "Log Files (*.log);;All Files (*.*)",
        )

        if file_path:
            self.view.log_file_path_edit.setText(file_path)

    def toggle_command_port(self):
        """Open or close the configured commandPort depending on current state."""
        if self._is_command_port_open():
            self.close_command_port()
        else:
            self.open_command_port()

    def open_command_port(self, emit_status=True):
        """Open a Python commandPort for external development diagnostics."""
        if not self.view.development_mode_check.isChecked():
            if emit_status:
                self.app_state.emit_status(self.tr("command_port_dev_mode_required"))
            self.refresh_command_port_status()
            return

        port = self._command_port_value()
        port_name = f":{port}"
        try:
            cmds = self._get_maya_cmds()

            if cmds.commandPort(port_name, query=True):
                self.refresh_command_port_status()
                if emit_status:
                    self.app_state.emit_status(self.tr_format("command_port_already_open", port=port))
                return
            cmds.commandPort(name=port_name, sourceType="python")
            self.settings_service.set(setting_keys.UI_DEV_COMMAND_PORT, port)
            self.settings_service.save()
            self.refresh_command_port_status()
            if emit_status:
                self.app_state.emit_status(self.tr_format("command_port_opened", port=port))
        except Exception as e:
            logger.error(f"Failed to open commandPort {port_name}: {e}", exc_info=True)
            self.refresh_command_port_status()
            if emit_status:
                self.app_state.emit_status(self.tr_format("command_port_open_failed", port=port, error=str(e)))

    def close_command_port(self, emit_status=True):
        """Close the configured commandPort when it is open."""
        port = self._command_port_value()
        port_name = f":{port}"
        try:
            cmds = self._get_maya_cmds()
            if not cmds.commandPort(port_name, query=True):
                self.refresh_command_port_status()
                if emit_status:
                    self.app_state.emit_status(self.tr_format("command_port_already_closed", port=port))
                return
            cmds.commandPort(name=port_name, close=True)
            self.refresh_command_port_status()
            if emit_status:
                self.app_state.emit_status(self.tr_format("command_port_closed", port=port))
        except Exception as e:
            logger.error(f"Failed to close commandPort {port_name}: {e}", exc_info=True)
            self.refresh_command_port_status()
            if emit_status:
                self.app_state.emit_status(self.tr_format("command_port_close_failed", port=port, error=str(e)))

    def on_language_changed(self):
        """言語が変更されたときの処理"""
        if self._loading:
            return

        # 選択された言語を取得
        selected_language = self.view.language_combo.currentData()

        # 設定に保存（即座に永続化）
        self.settings_service.set(setting_keys.UI_GENERAL_LANGUAGE, selected_language)

        # UITranslatorに言語を設定
        from ...ui.translations import UITranslator

        translator = UITranslator.instance()
        translator.set_language(selected_language)

        # メインウィンドウに言語変更を通知
        main_window = self.view.window()
        if hasattr(main_window, "retranslate_all_tabs"):
            main_window.retranslate_all_tabs()
        self.refresh_command_port_status()

        # ステータスメッセージ
        self.app_state.emit_status(
            self.tr_format("language_changed", language=self.view.language_combo.currentText())
        )
