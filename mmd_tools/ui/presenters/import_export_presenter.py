from ..qt_compat import QObject, Signal, QFileDialog
from ...core.logger import get_logger
from ...io.mmd_importer import import_mmd_file
from ...io.pmx_exporter import PmxExporter

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

    def import_file(self):
        file_path = self.view.import_path_edit.text()
        scale = float(self.view.scale_edit.text())
        logger.info(f"Importing file: {file_path} with scale: {scale}")
        
        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(f"インポート中: {file_path}")
        
        try:
            root_node = import_mmd_file(file_path, scale)
            if root_node:
                logger.info("Import successful.")
                # ApplicationStateを更新
                self.app_state.refresh_model_list()
                self.app_state.current_model_root = root_node
                self.app_state.emit_status(f"インポート完了: {root_node}")
                self.app_state.emit_progress(100)
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
        logger.info(f"Exporting file: {file_path}")
        try:
            exporter = PmxExporter()
            # TODO: Get maya_data from the scene
            maya_data = {}
            exporter.export_pmx_model(file_path, maya_data)
            logger.info("Export successful.")
        except Exception as e:
            logger.error(f"Export failed: {e}", exc_info=True)
