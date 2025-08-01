from ...core.logger import get_logger
from ... import settings
from ...core.settings import get_settings
from ..qt_compat import QFileDialog, QTableWidgetItem, QMessageBox
import json
import os

logger = get_logger(__name__)


class SettingsPresenter:
    def __init__(self, view, app_state):
        self.view = view
        self.app_state = app_state
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
            self.view.show_advanced_options_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.ui_log_level_combo.currentTextChanged.connect(
                self.on_setting_changed
            )
            self.view.logging_enabled_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.log_level_combo.currentTextChanged.connect(
                self.on_log_level_changed
            )
            self.view.log_file_browse_btn.clicked.connect(self.browse_log_file)
            
            # 言語設定
            if hasattr(self.view, 'language_combo'):
                self.view.language_combo.currentIndexChanged.connect(self.on_language_changed)

            # インポート設定
            self.view.scale_factor_spin.valueChanged.connect(self.on_setting_changed)
            self.view.use_namespace_check.stateChanged.connect(self.on_setting_changed)
            self.view.import_models_check.stateChanged.connect(self.on_setting_changed)
            self.view.separate_meshes_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.create_mmd_shaders_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.hide_hidden_geometry_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.joint_name_conversion_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.disable_backface_culling_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.import_physics_check.stateChanged.connect(self.on_setting_changed)
            self.view.create_rigid_bodies_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.create_physics_joints_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.group_physics_objects_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.import_morphs_check.stateChanged.connect(self.on_setting_changed)
            self.view.add_semi_standard_bones_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.translate_names_check.stateChanged.connect(
                self.on_setting_changed
            )
            self.view.texture_path_browse_btn.clicked.connect(self.browse_texture_path)

            # エクスポート設定
            self.view.export_format_combo.currentTextChanged.connect(
                self.on_setting_changed
            )
            self.view.apply_scale_check.stateChanged.connect(self.on_setting_changed)

        except AttributeError as e:
            logger.error(f"Failed to connect signal: {e}")
            import traceback

            traceback.print_exc()
            raise

    def load_settings(self):
        """設定をロード"""
        self._loading = True

        try:
            # UI設定
            self.view.show_advanced_options_check.setChecked(
                settings.get("ui.general.show_advanced_options", False)
            )
            ui_log_level = settings.get("ui.general.log_level", "INFO")
            index = self.view.ui_log_level_combo.findText(ui_log_level)
            if index >= 0:
                self.view.ui_log_level_combo.setCurrentIndex(index)

            # ログ設定
            self.view.logging_enabled_check.setChecked(
                settings.get("logging.enabled", True)
            )
            log_level = settings.get("logging.level", "DEBUG")
            index = self.view.log_level_combo.findText(log_level)
            if index >= 0:
                self.view.log_level_combo.setCurrentIndex(index)
            self.view.log_file_path_edit.setText(
                settings.get("logging.log_file_path", "logs/mmd_tools.log")
            )
            
            # 言語設定
            if hasattr(self.view, 'language_combo'):
                current_language = settings.get("ui.general.language", "ja")
                for i in range(self.view.language_combo.count()):
                    if self.view.language_combo.itemData(i) == current_language:
                        self.view.language_combo.setCurrentIndex(i)
                        break

            # インポート設定
            self.view.scale_factor_spin.setValue(
                settings.get("import.general.scale_factor", 1.0)
            )
            self.view.use_namespace_check.setChecked(
                settings.get("import.general.use_namespace", False)
            )
            self.view.root_bone_name_edit.setText(
                settings.get("import.general.root_bone_name", "master")
            )

            self.view.import_models_check.setChecked(
                settings.get("import.model.import_models", True)
            )
            self.view.separate_meshes_check.setChecked(
                settings.get("import.model.separate_meshes_by_material", False)
            )
            self.view.create_mmd_shaders_check.setChecked(
                settings.get("import.model.create_mmd_shaders", True)
            )
            self.view.texture_search_path_edit.setText(
                settings.get("import.model.texture_search_path", "")
            )
            self.view.hide_hidden_geometry_check.setChecked(
                settings.get("import.model.hide_hidden_geometry", True)
            )
            self.view.joint_name_conversion_check.setChecked(
                settings.get("import.model.joint_name_conversion_with_english", False)
            )
            self.view.uv_set_name_edit.setText(
                settings.get("import.model.uv_set_name", "map#")
            )
            self.view.disable_backface_culling_check.setChecked(
                settings.get("import.model.disable_backface_culling", True)
            )

            self.view.import_physics_check.setChecked(
                settings.get("import.physics.import_physics", False)
            )
            self.view.create_rigid_bodies_check.setChecked(
                settings.get("import.physics.create_rigid_bodies", True)
            )
            self.view.create_physics_joints_check.setChecked(
                settings.get("import.physics.create_physics_joints", True)
            )
            self.view.group_physics_objects_check.setChecked(
                settings.get("import.physics.group_physics_objects", True)
            )

            self.view.import_morphs_check.setChecked(
                settings.get("import.morph.import_morphs", True)
            )
            self.view.add_semi_standard_bones_check.setChecked(
                settings.get("import.rig.add_semi_standard_bones", False)
            )
            self.view.translate_names_check.setChecked(
                settings.get("import.naming.translate_names", True)
            )

            # エクスポート設定
            export_format = settings.get("export.general.export_format", "pmx")
            index = self.view.export_format_combo.findText(export_format)
            if index >= 0:
                self.view.export_format_combo.setCurrentIndex(index)
            self.view.apply_scale_check.setChecked(
                settings.get("export.general.apply_scale", True)
            )

        except Exception as e:
            logger.error(f"Failed to load settings: {e}", exc_info=True)

        finally:
            self._loading = False

    def on_setting_changed(self):
        """設定が変更されたときの処理"""
        if not self._loading:
            pass  # 必要に応じて自動保存などを実装
    
    def on_log_level_changed(self):
        """ログレベルが変更されたときの処理"""
        if not self._loading:
            new_level = self.view.log_level_combo.currentText()
            # ログレベルを即座に更新
            import logging
            level = getattr(logging, new_level, logging.INFO)
            logger.set_level(level)
            logger.info(f"ログレベルを {new_level} に変更しました")
            # 設定も同時に保存
            self.on_setting_changed()

    def save_all_settings(self):
        """すべての設定を保存"""
        try:
            # UI設定
            settings.set(
                "ui.general.show_advanced_options",
                self.view.show_advanced_options_check.isChecked(),
            )
            settings.set(
                "ui.general.log_level", self.view.ui_log_level_combo.currentText()
            )
            
            # 言語設定
            if hasattr(self.view, 'language_combo'):
                settings.set(
                    "ui.general.language", 
                    self.view.language_combo.currentData()
                )

            # ログ設定
            settings.set("logging.enabled", self.view.logging_enabled_check.isChecked())
            settings.set("logging.level", self.view.log_level_combo.currentText())
            settings.set("logging.log_file_path", self.view.log_file_path_edit.text())

            # インポート設定
            settings.set(
                "import.general.scale_factor", self.view.scale_factor_spin.value()
            )
            settings.set(
                "import.general.use_namespace",
                self.view.use_namespace_check.isChecked(),
            )
            settings.set(
                "import.general.root_bone_name", self.view.root_bone_name_edit.text()
            )

            settings.set(
                "import.model.import_models", self.view.import_models_check.isChecked()
            )
            settings.set(
                "import.model.separate_meshes_by_material",
                self.view.separate_meshes_check.isChecked(),
            )
            settings.set(
                "import.model.create_mmd_shaders",
                self.view.create_mmd_shaders_check.isChecked(),
            )
            settings.set(
                "import.model.texture_search_path",
                self.view.texture_search_path_edit.text(),
            )
            settings.set(
                "import.model.hide_hidden_geometry",
                self.view.hide_hidden_geometry_check.isChecked(),
            )
            settings.set(
                "import.model.joint_name_conversion_with_english",
                self.view.joint_name_conversion_check.isChecked(),
            )
            settings.set("import.model.uv_set_name", self.view.uv_set_name_edit.text())
            settings.set(
                "import.model.disable_backface_culling",
                self.view.disable_backface_culling_check.isChecked(),
            )

            settings.set(
                "import.physics.import_physics",
                self.view.import_physics_check.isChecked(),
            )
            settings.set(
                "import.physics.create_rigid_bodies",
                self.view.create_rigid_bodies_check.isChecked(),
            )
            settings.set(
                "import.physics.create_physics_joints",
                self.view.create_physics_joints_check.isChecked(),
            )
            settings.set(
                "import.physics.group_physics_objects",
                self.view.group_physics_objects_check.isChecked(),
            )

            settings.set(
                "import.morph.import_morphs", self.view.import_morphs_check.isChecked()
            )
            settings.set(
                "import.rig.add_semi_standard_bones",
                self.view.add_semi_standard_bones_check.isChecked(),
            )
            settings.set(
                "import.naming.translate_names",
                self.view.translate_names_check.isChecked(),
            )

            # エクスポート設定
            settings.set(
                "export.general.export_format",
                self.view.export_format_combo.currentText(),
            )
            settings.set(
                "export.general.apply_scale", self.view.apply_scale_check.isChecked()
            )

            settings.save()
            logger.info("設定を保存しました")
            self.app_state.emit_status("設定を保存しました")

        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            self.app_state.emit_status(f"設定の保存に失敗しました: {str(e)}")

    def reset_to_defaults(self):
        """デフォルト設定に戻す"""
        reply = QMessageBox.question(
            self.view,
            "確認",
            "すべての設定をデフォルトに戻しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # TODO: デフォルト値の定義
            self.load_settings()
            self.app_state.emit_status("設定をデフォルトに戻しました")

    def export_settings(self):
        """設定をファイルにエクスポート"""
        file_path, _ = QFileDialog.getSaveFileName(
            self.view,
            "設定をエクスポート",
            "mmd_tools_settings.json",
            "JSON Files (*.json)",
        )

        if file_path:
            try:
                # 現在の設定を収集
                # settings.dataにアクセスして全設定を取得
                all_settings = get_settings().data
                export_data = {
                    "import": all_settings.get("import", {}),
                    "export": all_settings.get("export", {}),
                    "logging": all_settings.get("logging", {}),
                    "ui": all_settings.get("ui", {}),
                }

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(export_data, f, ensure_ascii=False, indent=2)

                logger.info(f"設定をエクスポートしました: {file_path}")
                self.app_state.emit_status("設定をエクスポートしました")

            except Exception as e:
                logger.error(f"Failed to export settings: {e}", exc_info=True)
                self.app_state.emit_status(
                    f"設定のエクスポートに失敗しました: {str(e)}"
                )

    def import_settings(self):
        """設定をファイルからインポート"""
        file_path, _ = QFileDialog.getOpenFileName(
            self.view, "設定をインポート", "", "JSON Files (*.json)"
        )

        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    import_data = json.load(f)

                # 設定を適用
                for category in ["import", "export", "logging", "ui"]:
                    if category in import_data:
                        for key, value in import_data[category].items():
                            settings.set(f"{category}.{key}", value)

                # UIを更新
                self.load_settings()

                logger.info(f"設定をインポートしました: {file_path}")
                self.app_state.emit_status("設定をインポートしました")

            except Exception as e:
                logger.error(f"Failed to import settings: {e}", exc_info=True)
                self.app_state.emit_status(f"設定のインポートに失敗しました: {str(e)}")

    def browse_log_file(self):
        """ログファイルのパスを選択"""
        file_path, _ = QFileDialog.getSaveFileName(
            self.view,
            "ログファイルを選択",
            self.view.log_file_path_edit.text() or "mmd_tools.log",
            "Log Files (*.log);;All Files (*.*)",
        )

        if file_path:
            self.view.log_file_path_edit.setText(file_path)

    def browse_texture_path(self):
        """テクスチャ検索パスを選択"""
        folder = QFileDialog.getExistingDirectory(
            self.view,
            "テクスチャ検索パスを選択",
            self.view.texture_search_path_edit.text(),
        )

        if folder:
            self.view.texture_search_path_edit.setText(folder)
    
    def on_language_changed(self):
        """言語が変更されたときの処理"""
        if self._loading:
            return
            
        # 選択された言語を取得
        selected_language = self.view.language_combo.currentData()
        
        # 設定に保存（即座に永続化）
        settings.set("ui.general.language", selected_language)
        
        # UITranslatorに言語を設定
        from ...ui.translations import UITranslator
        translator = UITranslator.instance()
        translator.set_language(selected_language)
        
        # メインウィンドウに言語変更を通知
        main_window = self.view.window()
        if hasattr(main_window, 'retranslate_all_tabs'):
            main_window.retranslate_all_tabs()
        
        # ステータスメッセージ
        self.app_state.emit_status(f"言語を変更しました: {self.view.language_combo.currentText()}")
