from ..qt_compat import QObject, Signal, QFileDialog
from ...core.logger import get_logger
from ...io.mmd_importer import import_mmd_file
from ...io.pmx_exporter import PmxExporter
from ...settings import settings

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
            "MMD Files (*.pmd *.pmx *.vmd);;All Files (*)"
        )
        if file_path:
            self.view.import_path_edit.setText(file_path)

    def select_export_file(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self.view,
            "Save PMX File",
            "",
            "PMX Files (*.pmx);;All Files (*)"
        )
        if file_path:
            self.view.export_path_edit.setText(file_path)
    
    def select_vmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.view,
            "Select VMD File",
            "",
            "VMD Files (*.vmd);;All Files (*)"
        )
        if file_path:
            self.view.vmd_path_edit.setText(file_path)

    def import_file(self):
        file_path = self.view.import_path_edit.text()
        if not file_path:
            self.app_state.emit_status("ファイルパスを入力してください")
            return
        
        # Check if new file is requested
        if hasattr(self.view, 'new_file_check') and self.view.new_file_check.isChecked():
            from maya import cmds
            cmds.file(new=True, force=True)
            logger.info("Created new file before import")
            
        logger.info(f"Importing file: {file_path}")
        
        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"インポート中: {file_path}")
        
        # 設定を収集
        import_options = {
            'scale': settings.get("import.general.scale_factor", 1.0),
            'use_namespace': settings.get("import.general.use_namespace", False),
            'import_models': settings.get("import.model.import_models", True),
            'create_mmd_shaders': settings.get("import.model.create_mmd_shaders", True),
            'separate_meshes_by_material': settings.get("import.model.separate_meshes_by_material", False),
            'hide_hidden_geometry': settings.get("import.model.hide_hidden_geometry", True),
            'import_physics': settings.get("import.physics.import_physics", False),
            'import_morphs': settings.get("import.morph.import_morphs", True),
        }
        
        try:
            root_node = import_mmd_file(file_path, options=import_options)
            if root_node:
                logger.info("Import successful.")
                # ApplicationStateを更新
                self.app_state.refresh_model_list()
                self.app_state.current_model_root = root_node
                self.app_state.emit_status(f"インポート完了: {root_node}")
                self.app_state.emit_progress(100)
                # モデルリストを更新
                self.view.refresh_model_list()
            else:
                logger.error("Import failed.")
                self.app_state.emit_status("インポートに失敗しました")
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            self.app_state.emit_status(f"インポートエラー: {str(e)}")
            self.app_state.emit_progress(0)

    def export_file(self):
        file_path = self.view.export_path_edit.text()
        if not file_path:
            self.app_state.emit_status("ファイルパスを入力してください")
            return
            
        logger.info(f"Exporting file: {file_path}")
        
        # エクスポート設定を収集
        export_options = {
            'format': settings.get("export.general.export_format", "pmx"),
            'apply_scale': settings.get("export.general.apply_scale", True),
        }
        
        try:
            exporter = PmxExporter()
            # TODO: Get maya_data from the scene
            maya_data = {}
            exporter.export_pmx_model(file_path, maya_data)
            logger.info("Export successful.")
            self.app_state.emit_status(f"エクスポート完了: {file_path}")
        except Exception as e:
            logger.error(f"Export failed: {e}", exc_info=True)
            self.app_state.emit_status(f"エクスポートエラー: {str(e)}")
    
    def import_vmd_file(self):
        """VMDファイルのインポート"""
        file_path = self.view.vmd_path_edit.text()
        if not file_path:
            self.app_state.emit_status("VMDファイルパスを入力してください")
            return
        
        # ターゲットモデルを取得
        target_model = None
        current_index = self.view.target_model_combo.currentIndex()
        if current_index > 0:  # "<Auto Detect>"以外が選択されている場合
            target_model = self.view.target_model_combo.itemData(current_index)
        
        logger.info(f"Importing VMD file: {file_path}")
        if target_model:
            logger.info(f"Target model: {target_model}")
        
        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"VMDインポート中: {file_path}")
        
        # アニメーション設定を収集
        animation_options = {
            'start_frame': settings.get("import.animation.animation_start_frame", 1),
            'import_bone_animation': settings.get("import.animation.import_animations", True),
            'import_morph_animation': settings.get("import.animation.import_morph_animation", True),
            'import_camera_animation': settings.get("import.animation.import_camera_animation", True),
            'import_light_animation': settings.get("import.animation.import_light_animation", True),
            'resample_curves': settings.get("import.animation.resample_curves", False),
            'target_model': target_model,
        }
        
        try:
            # VMDファイルもimport_mmd_fileで処理される
            success = import_mmd_file(file_path, options=animation_options)
            if success:
                logger.info("VMD import successful.")
                self.app_state.emit_status(f"VMDインポート完了: {file_path}")
                self.app_state.emit_progress(100)
            else:
                logger.error("VMD import failed.")
                self.app_state.emit_status("VMDインポートに失敗しました")
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"VMD import failed: {e}", exc_info=True)
            self.app_state.emit_status(f"VMDインポートエラー: {str(e)}")
            self.app_state.emit_progress(0)
