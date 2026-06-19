from ..qt_compat import QObject, QFileDialog
from ...actions.import_model_action import ImportModelAction, ImportModelRequest
from ...actions.import_vmd_action import ImportVmdAction, ImportVmdRequest
from ...core.logger import get_logger
from ...io.mmd_importer import import_mmd_file
from ...core.settings import settings

logger = get_logger(__name__)

# Dev-only import keys: forced to these values in normal mode (development_mode=False).
# In dev mode the saved setting is used instead.
_NORMAL_MODE_IMPORT_OVERRIDES = {
    "import_models": True,
    "import_physics": False,
    "separate_meshes_by_material": False,
    "split_meshes_by_morph_groups": False,
    "hide_hidden_geometry": False,
    "auto_classify_transparency": False,
    "disable_backface_culling": True,
    "uv_set_name": "map#",
    "texture_search_path": "",
    "add_semi_standard_bones": False,
    "translate_names": True,
}


class ImportExportPresenter(QObject):
    def __init__(self, view, app_state, import_model_action=None, import_vmd_action=None):
        super().__init__()
        self.view = view
        self.app_state = app_state
        self.import_model_action = import_model_action or ImportModelAction()
        self.import_vmd_action = import_vmd_action or ImportVmdAction()
        self.connect_signals()

    def connect_signals(self):
        self.view.import_path_button.clicked.connect(self.select_import_file)
        self.view.export_path_button.clicked.connect(self.select_export_file)
        self.view.import_button.clicked.connect(self.import_file)
        self.view.export_button.clicked.connect(self.export_file)

        # VMD import signals
        self.view.vmd_path_button.clicked.connect(self.select_vmd_file)
        self.view.import_vmd_button.clicked.connect(self.import_vmd_file)

    def select_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.view,
            "Select MMD File",
            "",
            "MMD Files (*.pmd *.pmx *.vmd);;All Files (*)",
        )
        if file_path:
            self.view.import_path_edit.setText(file_path)

    def select_export_file(self):
        file_path, _ = QFileDialog.getSaveFileName(self.view, "Save PMX File", "", "PMX Files (*.pmx);;All Files (*)")
        if file_path:
            self.view.export_path_edit.setText(file_path)

    def select_vmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self.view, "Select VMD File", "", "VMD Files (*.vmd);;All Files (*)")
        if file_path:
            self.view.vmd_path_edit.setText(file_path)

    def _get_vmd_target_model(self):
        """VMD import用の対象モデルをUI選択または現在モデルから取得する。"""
        current_index = self.view.target_model_combo.currentIndex()
        if current_index > 0:  # "<Auto Detect>"以外が選択されている場合
            return self.view.target_model_combo.itemData(current_index)
        if getattr(self.app_state, "current_model_root", None):
            target_model = self.app_state.current_model_root
            logger.info(f"Auto-selected current model root for VMD import: {target_model}")
            return target_model
        return None

    def _build_vmd_import_options(self, target_model=None):
        """VMD import用のオプションをUI設定から組み立てる。"""
        if target_model is None:
            target_model = self._get_vmd_target_model()
        is_dev = settings.get("ui.general.development_mode", False)
        return {
            "start_frame": settings.get("import.animation.animation_start_frame", 1),
            "vmd_fps": settings.get("import.animation.vmd_fps", 30),
            "import_bone_animation": settings.get("import.animation.import_animations", True),
            "import_morph_animation": settings.get("import.animation.import_morph_animation", True),
            "import_camera_animation": settings.get("import.animation.import_camera_animation", True),
            "import_light_animation": settings.get("import.animation.import_light_animation", True),
            "resample_curves": settings.get("import.animation.resample_curves", False) if is_dev else False,
            "target_model": target_model,
        }

    def _build_pmx_import_options(self):
        """PMX/PMD import用のオプションを組み立てる。

        通常モード（development_mode=False）では dev-only 設定を強制デフォルトに上書きする。
        """
        is_dev = settings.get("ui.general.development_mode", False)
        opts = {
            "scale": settings.get("import.general.scale_factor", 1.0),
            "use_namespace": settings.get("import.general.use_namespace", False),
            "custom_namespace": self.view.get_custom_namespace(),
            "import_models": settings.get("import.model.import_models", True),
            "create_mmd_shaders": settings.get("import.model.create_mmd_shaders", True),
            "separate_meshes_by_material": settings.get("import.model.separate_meshes_by_material", False),
            "split_meshes_by_morph_groups": settings.get("import.model.split_meshes_by_morph_groups", False),
            "hide_hidden_geometry": settings.get("import.model.hide_hidden_geometry", False),
            "auto_classify_transparency": settings.get("import.model.auto_classify_transparency", False),
            "disable_backface_culling": settings.get("import.model.disable_backface_culling", True),
            "uv_set_name": settings.get("import.model.uv_set_name", "map#"),
            "texture_search_path": settings.get("import.model.texture_search_path", ""),
            "import_physics": settings.get("import.physics.import_physics", False),
            "import_morphs": settings.get("import.morph.import_morphs", True),
            "add_semi_standard_bones": settings.get("import.rig.add_semi_standard_bones", False),
            "translate_names": settings.get("import.naming.translate_names", True),
        }
        if not is_dev:
            opts.update(_NORMAL_MODE_IMPORT_OVERRIDES)
        bake_mode = settings.get("import.rig.bake_mode", True) if is_dev else True
        if bake_mode:
            opts["setup_rig"] = False
            opts["setup_bone_orientation"] = False
        opts["use_cpp_fast_load"] = settings.get("import.native.use_cpp_fast_load", False)
        opts["cpp_fast_load_mesh_only"] = settings.get("import.native.cpp_fast_load_mesh_only", True)
        return opts

    def _maybe_show_texture_issue_dialog(self, profile, file_path):
        """Show post-import texture issues for UI-triggered PMX/PMD imports."""

        issues = profile.get("texture_issues") or profile.get("mesh_converter", {}).get("unresolved_textures") or []
        if not issues or not settings.get("import.model.show_texture_issue_dialog", True):
            return
        try:
            from ..texture_issue_dialog import TextureIssueDialog

            dialog = TextureIssueDialog(issues, model_path=file_path, app_state=self.app_state, parent=self.view)
            if hasattr(dialog, "exec"):
                dialog.exec()
            else:
                dialog.exec_()
        except Exception as exc:
            logger.error("Failed to show texture issue dialog: %s", exc, exc_info=True)

    def _build_export_options(self):
        """PMX/PMD export用の基本オプションを設定から組み立てる。"""
        return {
            "file_path": self.view.export_path_edit.text().strip(),
            "export_format": settings.get("export.general.export_format", "pmx"),
            "apply_scale": settings.get("export.general.apply_scale", True),
        }

    def import_file(self):
        file_path = self.view.import_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status("Please enter a file path")
            return

        create_new_scene = hasattr(self.view, "new_file_check") and self.view.new_file_check.isChecked()
        is_vmd = file_path.lower().endswith(".vmd")

        logger.info(f"Importing file: {file_path}")

        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"Importing: {file_path}")

        import_options = self._build_pmx_import_options()
        import_profile = {}
        if is_vmd:
            import_options.update(self._build_vmd_import_options())
        else:
            import_options["profile"] = import_profile

        try:
            if is_vmd:
                request = ImportVmdRequest(
                    file_path=file_path,
                    options=import_options,
                    create_new_scene=create_new_scene,
                )
                result = self.import_vmd_action.execute(request)
                if result.error:
                    raise result.error
                root_node = result.root_node if result.succeeded else None
                if create_new_scene:
                    logger.info("Created new file before import")
            else:
                request = ImportModelRequest(
                    file_path=file_path,
                    options=import_options,
                    create_new_scene=create_new_scene,
                )
                result = self.import_model_action.execute(request)
                if result.error:
                    raise result.error
                root_node = result.root_node if result.succeeded else None
                if create_new_scene:
                    logger.info("Created new file before import")
            if root_node:
                logger.info("Import successful.")
                # ApplicationStateを更新
                self.app_state.refresh_model_list()
                self.app_state.current_model_root = root_node
                self.app_state.emit_status(f"Import complete: {root_node}")
                self.app_state.emit_progress(100)
                # モデルリストを更新
                self.view.refresh_model_list()
                # 成功したパスを履歴に追加
                self.view.add_import_path_to_history(file_path)
                if not is_vmd:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            else:
                logger.error("Import failed.")
                self.app_state.emit_status("Import failed")
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            self.app_state.emit_status(f"Import error: {str(e)}")
            self.app_state.emit_progress(0)

    def export_file(self):
        file_path = self.view.export_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status("Please enter a file path")
            return

        export_options = self._build_export_options()
        logger.debug(f"Export options: {export_options}")

        # NOTE: Maya シーンから PMX 用データ（頂点/面/材質/ボーン等）を収集する処理が
        # 未実装。収集なしで PmxExporter を呼ぶと必ず ValueError になり、ユーザーに
        # 紛らわしいエラーを見せてしまうため、現時点では未実装であることを明示する。
        # シーン収集（collect_*_from_scene_for_export 等）を実装したら有効化する。
        logger.warning("PMX export is not implemented yet (scene data collection missing)")
        self.app_state.emit_status("PMX export is not implemented yet (scene data collection is unsupported)")

    def import_vmd_file(self):
        """VMDファイルのインポート"""
        file_path = self.view.vmd_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status("Please enter a VMD file path")
            return

        # ターゲットモデルを取得
        target_model = self._get_vmd_target_model()

        logger.info(f"Importing VMD file: {file_path}")
        if target_model:
            logger.info(f"Target model: {target_model}")

        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"Importing VMD: {file_path}")

        # アニメーション設定を収集
        animation_options = self._build_vmd_import_options(target_model)

        try:
            # VMDファイルもimport_mmd_fileで処理される
            success = import_mmd_file(file_path, options=animation_options)
            if success:
                logger.info("VMD import successful.")
                self.app_state.emit_status(f"VMD import complete: {file_path}")
                self.app_state.emit_progress(100)
                # 成功したパスを履歴に追加
                self.view.add_vmd_path_to_history(file_path)
            else:
                logger.error("VMD import failed.")
                self.app_state.emit_status("VMD import failed")
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"VMD import failed: {e}", exc_info=True)
            self.app_state.emit_status(f"VMD import error: {str(e)}")
            self.app_state.emit_progress(0)
