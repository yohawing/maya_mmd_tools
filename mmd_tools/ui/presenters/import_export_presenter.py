from ..qt_compat import QObject, QFileDialog
from ...core.logger import get_logger
from ...io.mmd_importer import import_mmd_file
from ...core.settings import settings

logger = get_logger(__name__)


class ImportExportPresenter(QObject):
    def __init__(self, view, app_state):
        super().__init__()
        self.view = view
        self.app_state = app_state
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
        return {
            "start_frame": settings.get("import.animation.animation_start_frame", 1),
            "vmd_fps": settings.get("import.animation.vmd_fps", 30),
            "import_bone_animation": settings.get("import.animation.import_animations", True),
            "import_morph_animation": settings.get("import.animation.import_morph_animation", True),
            "import_camera_animation": settings.get("import.animation.import_camera_animation", True),
            "import_light_animation": settings.get("import.animation.import_light_animation", True),
            "resample_curves": settings.get("import.animation.resample_curves", False),
            "target_model": target_model,
        }

    def import_file(self):
        file_path = self.view.import_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status("Please enter a file path")
            return

        # Check if new file is requested
        if hasattr(self.view, "new_file_check") and self.view.new_file_check.isChecked():
            from maya import cmds

            cmds.file(new=True, force=True)
            logger.info("Created new file before import")

        logger.info(f"Importing file: {file_path}")

        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"Importing: {file_path}")

        # 設定を収集
        import_options = {
            "scale": settings.get("import.general.scale_factor", 1.0),
            "use_namespace": settings.get("import.general.use_namespace", False),
            "custom_namespace": self.view.get_custom_namespace(),  # カスタムnamespace名
            "import_models": settings.get("import.model.import_models", True),
            "create_mmd_shaders": settings.get("import.model.create_mmd_shaders", True),
            "separate_meshes_by_material": settings.get("import.model.separate_meshes_by_material", False),
            "hide_hidden_geometry": settings.get("import.model.hide_hidden_geometry", True),
            "import_physics": settings.get("import.physics.import_physics", False),
            "import_morphs": settings.get("import.morph.import_morphs", True),
        }
        if settings.get("import.rig.bake_mode", False):
            import_options["setup_rig"] = False
            import_options["setup_bone_orientation"] = False
        import_options["use_cpp_fast_load"] = settings.get(
            "import.native.use_cpp_fast_load", False
        )
        import_options["cpp_fast_load_mesh_only"] = settings.get(
            "import.native.cpp_fast_load_mesh_only", True
        )
        if file_path.lower().endswith(".vmd"):
            import_options.update(self._build_vmd_import_options())

        try:
            root_node = import_mmd_file(file_path, options=import_options)
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
