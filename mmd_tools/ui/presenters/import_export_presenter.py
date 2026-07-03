from ..qt_compat import QObject, QFileDialog
from ...actions.export_model_action import ExportModelAction, ExportModelRequest
from ...actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from ...actions.import_model_action import ImportModelAction, ImportModelRequest
from ...actions.import_vmd_action import ImportVmdAction, ImportVmdRequest
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core import maya_utils, settings_keys as setting_keys
from ...core.constants import ATTR_MMD_ORIGINAL_TEXTURE_PATH, ATTR_MMD_TEXTURE_CACHE_PATH
from ...core.logger import get_logger
from ...services.settings_service import SettingsService
from .list_presenter_helpers import tr_message, tr_message_format
from ..translations.translator import UITranslator

logger = get_logger(__name__)


class ImportExportPresenter(QObject):
    def __init__(
        self,
        view,
        app_state,
        import_model_action=None,
        import_vmd_action=None,
        export_model_action=None,
        export_vmd_action=None,
        settings_service=None,
        maya_adapter=None,
    ):
        super().__init__()
        self.view = view
        self.app_state = app_state
        self.import_model_action = import_model_action or ImportModelAction()
        self.import_vmd_action = import_vmd_action or ImportVmdAction()
        self.export_model_action = export_model_action or ExportModelAction()
        self.export_vmd_action = export_vmd_action or ExportVmdAction()
        self.settings_service = settings_service or SettingsService()
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.view.presenter = self
        self.connect_signals()
        self.refresh_model_list(restore_selection=True)

    def connect_signals(self):
        self.view.import_path_button.clicked.connect(self.select_import_file)
        self.view.export_path_button.clicked.connect(self.select_export_file)
        self.view.import_button.clicked.connect(self.import_file)
        self.view.export_button.clicked.connect(self.export_file)
        if hasattr(self.view, "fix_texture_path_button"):
            self.view.fix_texture_path_button.clicked.connect(self.fix_texture_paths)

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
        export_format = self.settings_service.get(setting_keys.EXPORT_GENERAL_EXPORT_FORMAT, "pmx")
        if export_format == "vmd":
            title = "Save VMD File"
            filter_text = "VMD Files (*.vmd);;All Files (*)"
        else:
            title = "Save PMX File"
            filter_text = "PMX Files (*.pmx);;All Files (*)"
        file_path, _ = QFileDialog.getSaveFileName(self.view, title, "", filter_text)
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
        return self.settings_service.build_vmd_import_options(target_model)

    def refresh_model_list(self, restore_selection=False):
        """VMD import target model candidates を Presenter 経由で更新する。"""
        if not hasattr(self.view, "set_target_model_items"):
            return
        scene_model_service = getattr(self.app_state, "scene_model_service", None)
        if scene_model_service is None:
            self.view.set_target_model_items([], restore_selection=restore_selection)
            return

        try:
            model_items = [
                (model_root, scene_model_service.get_model_display_name(model_root))
                for model_root in scene_model_service.list_mmd_models()
            ]
        except Exception:
            logger.debug("Failed to refresh VMD target model list", exc_info=True)
            model_items = []
        self.view.set_target_model_items(model_items, restore_selection=restore_selection)

    def _build_pmx_import_options(self):
        """PMX/PMD import用のオプションを組み立てる。

        通常モード（development_mode=False）では dev-only 設定を強制デフォルトに上書きする。
        """
        return self.settings_service.build_pmx_import_options(self.view.get_custom_namespace())

    def _maybe_show_texture_issue_dialog(self, profile, file_path):
        """Show post-import texture issues for UI-triggered PMX/PMD imports."""

        issues = self._extract_texture_issues(profile)
        if not issues or not self.settings_service.should_show_texture_issue_dialog():
            return
        self._show_texture_issue_dialog(issues, model_path=file_path)

    def _extract_texture_issues(self, profile):
        """Extract texture issue records from an import profile."""

        profile = profile or {}
        return profile.get("texture_issues") or profile.get("mesh_converter", {}).get("unresolved_textures") or []

    def _show_texture_issue_dialog(self, issues, model_path=""):
        """Show the shared texture issue dialog."""

        if not issues:
            return
        try:
            from ..texture_issue_dialog import TextureIssueDialog

            dialog = TextureIssueDialog(issues, model_path=model_path, app_state=self.app_state, parent=self.view)
            if hasattr(dialog, "exec"):
                dialog.exec()
            else:
                dialog.exec_()
        except Exception as exc:
            logger.error("Failed to show texture issue dialog: %s", exc, exc_info=True)

    def _material_name_for_file_node(self, file_node):
        """Return a connected shader/material name, falling back to the file node."""

        try:
            connections = self.maya_adapter.list_connections(file_node, destination=True) or []
            for node in connections:
                if node:
                    return node
        except Exception:
            logger.debug("Failed to find material connection for file node %s", file_node, exc_info=True)
        return file_node

    def _texture_resolution_to_issue(self, file_node, resolution):
        """Convert a texture resolution classification into a dialog issue."""

        material_name = self._material_name_for_file_node(file_node)
        cache_path = getattr(resolution, "cache_path", "") or ""
        if not cache_path:
            try:
                if self.maya_adapter.attribute_exists(ATTR_MMD_TEXTURE_CACHE_PATH, node=file_node):
                    cache_path = maya_utils.get_attribute(file_node, ATTR_MMD_TEXTURE_CACHE_PATH) or ""
            except Exception:
                logger.debug("Failed to read texture cache path for file node %s", file_node, exc_info=True)
                cache_path = ""
        current_path = cache_path or getattr(resolution, "file_texture_path", "") or ""
        return {
            "file_node": file_node,
            "material": material_name,
            "material_name": material_name,
            "reason": getattr(resolution, "reason", ""),
            "original_path": getattr(resolution, "original_path", "") or "",
            "current_path": current_path,
            "resolvable": getattr(resolution, "status", "") == "resolvable",
            "source_path": getattr(resolution, "source_path", "") or "",
            "source_reason": getattr(resolution, "reason", ""),
        }

    def _collect_scene_texture_issues(self):
        """Collect non-ok MMD texture file-node issues from the current scene."""

        issues = []
        for file_node in self.maya_adapter.ls(type="file") or []:
            if not self.maya_adapter.attribute_exists(ATTR_MMD_ORIGINAL_TEXTURE_PATH, node=file_node):
                continue
            classification = maya_utils.classify_mmd_texture_file_node(file_node)
            if not classification or classification.status == "ok":
                continue
            issues.append(self._texture_resolution_to_issue(file_node, classification))
        return issues

    def fix_texture_paths(self):
        """Show scene texture issues for explicit user-triggered fixing."""

        try:
            issues = self._collect_scene_texture_issues()
        except Exception as exc:
            logger.error("Failed to scan scene texture issues: %s", exc, exc_info=True)
            message = UITranslator.instance().translate("status_scan_failed", "texture_issues")
            self.app_state.emit_status(message)
            return
        if issues:
            # Manual button presses are explicit user actions, so they intentionally
            # ignore import.model.show_texture_issue_dialog.
            self._show_texture_issue_dialog(issues, model_path="")
        else:
            message = UITranslator.instance().translate("status_no_issues", "texture_issues")
            self.app_state.emit_status(message)

    def _build_export_options(self):
        """PMX/PMD export用の基本オプションを設定から組み立てる。"""
        return self.settings_service.build_export_options(self.view.export_path_edit.text().strip())

    def import_file(self):
        file_path = self.view.import_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status(tr_message("enter_file_path"))
            return

        create_new_scene = hasattr(self.view, "new_file_check") and self.view.new_file_check.isChecked()
        is_vmd = file_path.lower().endswith(".vmd")

        logger.info(f"Importing file: {file_path}")

        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(tr_message_format("importing_file", file_path=file_path))

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
                    progress_callback=self.app_state.emit_progress,
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
                    progress_callback=self.app_state.emit_progress,
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
                self.app_state.emit_status(tr_message_format("import_complete_node", root_node=root_node))
                self.app_state.emit_progress(100)
                # モデルリストを更新
                self.refresh_model_list()
                # 成功したパスを履歴に追加
                self.view.add_import_path_to_history(file_path)
                if not is_vmd:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            else:
                logger.error("Import failed.")
                self.app_state.emit_status(tr_message("import_failed"))
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("import_error", error=str(e)))
            self.app_state.emit_progress(0)

    def export_file(self):
        file_path = self.view.export_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status(tr_message("enter_file_path"))
            return

        export_options = self._build_export_options()
        logger.debug(f"Export options: {export_options}")

        if export_options.get("export_format") == "vmd":
            request = ExportVmdRequest(file_path=file_path, options=export_options)
            result = self.export_vmd_action.execute(request)
        else:
            request = ExportModelRequest(file_path=file_path, options=export_options)
            result = self.export_model_action.execute(request)
        if result.error:
            logger.error(f"Export failed: {result.error}")
            self.app_state.emit_status(tr_message_format("export_error", error=str(result.error)))
            return
        if getattr(result, "succeeded", False):
            if hasattr(self.view, "add_export_path_to_history"):
                self.view.add_export_path_to_history(file_path)
            self.app_state.emit_status(tr_message_format("export_complete_file", file_path=file_path))
            return
        if result.status_message:
            self.app_state.emit_status(result.status_message)

    def import_vmd_file(self):
        """VMDファイルのインポート"""
        file_path = self.view.vmd_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status(tr_message("enter_vmd_file_path"))
            return

        # ターゲットモデルを取得
        target_model = self._get_vmd_target_model()

        logger.info(f"Importing VMD file: {file_path}")
        if target_model:
            logger.info(f"Target model: {target_model}")

        self.app_state.emit_progress(0)
        self.app_state.emit_status(tr_message_format("importing_vmd", file_path=file_path))

        # アニメーション設定を収集
        animation_options = self._build_vmd_import_options(target_model)

        try:
            request = ImportVmdRequest(
                file_path=file_path,
                options=animation_options,
                create_new_scene=False,
                progress_callback=self.app_state.emit_progress,
            )
            result = self.import_vmd_action.execute(request)
            if result.error:
                raise result.error
            success = result.succeeded
            if success:
                logger.info("VMD import successful.")
                self.app_state.emit_status(tr_message_format("vmd_import_complete", file_path=file_path))
                self.app_state.emit_progress(100)
                # 成功したパスを履歴に追加
                self.view.add_vmd_path_to_history(file_path)
            else:
                logger.error("VMD import failed.")
                self.app_state.emit_status(tr_message("vmd_import_failed"))
                self.app_state.emit_progress(0)
        except Exception as e:
            logger.error(f"VMD import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("vmd_import_error", error=str(e)))
            self.app_state.emit_progress(0)
