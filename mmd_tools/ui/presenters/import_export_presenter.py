from ..qt_compat import QObject, QFileDialog
from ...actions.export_model_action import ExportModelAction, ExportModelRequest
from ...actions.export_vmd_action import ExportVmdAction, ExportVmdRequest
from ...actions.import_model_action import ImportModelAction, ImportModelRequest
from ...actions.import_vmd_action import (
    ImportVmdAction,
    ImportVmdRequest,
    VMD_TARGET_AUTO,
    VMD_TARGET_CAMERA,
)
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core import maya_attribute_utils, maya_material_utils, settings_keys as setting_keys
from ...core.constants import (
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_TEXTURE_CACHE_PATH,
)
from ...core.logger import get_logger
from ...services.settings_service import SettingsService
from ..model_readme_dialog import ModelReadmeDialogAdapter, read_model_readme
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
        model_readme_adapter=None,
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
        self.model_readme_adapter = model_readme_adapter or ModelReadmeDialogAdapter(
            development_mode_getter=self.settings_service.is_development_mode,
        )
        self._vmd_model_roots = []
        self.view.presenter = self
        self.connect_signals()
        self.refresh_model_list(restore_selection=True)

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
        choice = self.view.target_model_combo.itemData(current_index)
        if choice == VMD_TARGET_CAMERA:
            return VMD_TARGET_CAMERA
        if choice not in (None, VMD_TARGET_AUTO):
            return choice if choice in self._vmd_model_roots else None
        current_model = getattr(self.app_state, "current_model_root", None)
        if current_model in self._vmd_model_roots:
            target_model = self.app_state.current_model_root
            logger.debug(f"Auto-selected current model root for VMD import: {target_model}")
            return target_model
        if len(self._vmd_model_roots) == 1:
            target_model = self._vmd_model_roots[0]
            logger.debug(f"Auto-selected sole model root for VMD import: {target_model}")
            return target_model
        return None

    def _build_vmd_import_options(self, target_model):
        """VMD import用のオプションをUI設定から組み立てる。"""
        options = self.settings_service.build_vmd_import_options(
            None if target_model == VMD_TARGET_CAMERA else target_model
        )
        if target_model == VMD_TARGET_CAMERA:
            options.pop("target_model", None)
            options["scene_animation_only"] = True
        return options

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
        self._vmd_model_roots = [model_root for model_root, _display_name in model_items]
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

    def _maybe_show_model_readme(self, root_node, file_path):
        """Show the imported model readme after other import modals complete."""

        if not root_node:
            return
        scene_model_service = getattr(self.app_state, "scene_model_service", None)
        readme = read_model_readme(scene_model_service, root_node)
        if readme is None:
            return
        try:
            self.model_readme_adapter.show(readme, model_path=file_path, parent=self.view)
        except Exception as exc:
            # Import already succeeded; a UI adapter failure must not turn it
            # into a fatal result or suppress the existing status/history.
            logger.error("Failed to show model readme: %s", exc, exc_info=True)

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
                    cache_path = maya_attribute_utils.get_attribute(file_node, ATTR_MMD_TEXTURE_CACHE_PATH) or ""
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
            classification = maya_material_utils.classify_mmd_texture_file_node(file_node)
            if not classification or classification.status == "ok":
                continue
            issues.append(self._texture_resolution_to_issue(file_node, classification))
        return issues

    def _current_model_texture_file_nodes(self):
        """Return MMD texture file nodes used by the currently selected model."""

        model_root = getattr(self.app_state, "current_model_root", None)
        if not model_root or not self.maya_adapter.object_exists(model_root):
            return []
        shapes = self.maya_adapter.list_relatives(model_root, allDescendents=True, type="mesh") or []
        shading_groups = self.maya_adapter.list_connections(shapes, type="shadingEngine") or []
        materials = []
        for shading_group in set(shading_groups):
            materials.extend(self.maya_adapter.ls(self.maya_adapter.list_connections(shading_group) or [], materials=True))
        file_nodes = []
        for material in set(materials):
            file_nodes.extend(
                self.maya_adapter.list_connections(material, source=True, destination=False, type="file") or []
            )

        # Imported file nodes carry a message owner so failed/disconnected
        # shader binds remain instance-scoped even when the same PMX is loaded
        # more than once. Legacy disconnected nodes without ownership are
        # intentionally excluded because source-path provenance is ambiguous.
        file_nodes.extend(
            self.maya_adapter.list_connections(
                f"{model_root}.message",
                source=False,
                destination=True,
                type="file",
            )
            or []
        )
        return sorted(
            {
                node
                for node in file_nodes
                if self.maya_adapter.attribute_exists(ATTR_MMD_ORIGINAL_TEXTURE_PATH, node=node)
            }
        )

    def fix_texture_paths(self):
        """Repair texture paths for the current model and publish a concise result."""

        translator = UITranslator.instance()
        if not getattr(self.app_state, "current_model_root", None):
            self.app_state.emit_status(translator.translate("status_select_model", "texture_issues"))
            return {"resolved": 0, "unresolved": 0}
        try:
            file_nodes = self._current_model_texture_file_nodes()
            if not file_nodes:
                self.app_state.emit_status(translator.translate("status_no_issues", "texture_issues"))
                return {"resolved": 0, "unresolved": 0}
            results = maya_material_utils.resolve_scene_mmd_textures(file_nodes=file_nodes)
        except Exception as exc:
            logger.error("Failed to repair model texture paths: %s", exc, exc_info=True)
            self.app_state.emit_status(translator.translate("status_scan_failed", "texture_issues"))
            return {"resolved": 0, "unresolved": 0}

        resolved = sum(
            getattr(result, "status", "") == "resolved"
            and getattr(result, "rebind_status", "") != "failed"
            for result in results or []
        )
        unresolved = sum(
            getattr(result, "status", "") not in ("ok", "resolved")
            or getattr(result, "rebind_status", "") == "failed"
            for result in results or []
        )
        if not resolved and not unresolved:
            message = translator.translate("status_no_issues", "texture_issues")
        else:
            message = translator.translate("status_repair_summary", "texture_issues").format(
                resolved=resolved,
                unresolved=unresolved,
            )
        self.app_state.emit_status(message)
        return {"resolved": resolved, "unresolved": unresolved}

    def _build_export_options(self):
        """PMX/PMD export用の基本オプションを設定から組み立てる。"""
        return self.settings_service.build_export_options(self.view.export_path_edit.text().strip())

    def _resolve_import_outcome(self, result):
        """Resolve success / partial / fatal from an import action result.

        Prefer the explicit ``outcome`` field when present so presenters do not
        treat fatal errors as success. Fall back to succeeded/error/warnings for
        older result objects that omit ``outcome``.
        """
        outcome = getattr(result, "outcome", None)
        if outcome in ("success", "partial", "fatal"):
            return outcome
        if getattr(result, "error", None) is not None or not getattr(result, "succeeded", False):
            return "fatal"
        if getattr(result, "warnings", None):
            return "partial"
        return "success"

    def _is_texture_issue_warning(self, warning):
        """Return True when a structured warning is a texture-issue record.

        Texture issue / unresolved texture records always carry ``file_node``.
        Other structured warnings (e.g. bone morph ``node_type_unavailable``) do not.
        """
        return isinstance(warning, dict) and "file_node" in warning

    def _partial_warnings_are_texture_only(self, warnings):
        """Return True when every partial warning is a texture issue record."""
        if not warnings:
            return False
        return all(self._is_texture_issue_warning(item) for item in warnings)

    def _show_import_partial_warning(self, title, message, warnings=None):
        """Show one operation-level warning dialog for a partial import.

        Intentionally a single dialog for the whole import, not one modal per
        low-level warning. Headless unit tests can mock this helper.
        """
        del warnings  # reserved for future detail panes; one summary dialog only
        try:
            from ..qt_compat import QMessageBox

            QMessageBox.warning(self.view, title, message)
        except Exception as exc:
            logger.debug("Import partial warning dialog unavailable: %s", exc, exc_info=True)

    def _present_import_partial_outcome(
        self,
        warnings,
        *,
        file_path,
        root_node=None,
        kind="model",
        show_dialog=True,
    ):
        """Present exactly one operation-level partial import outcome.

        Emits a warning status and optionally opens a single generic dialog.
        Does not emit the normal success-complete status. Returns the status
        message text. Callers choose ``show_dialog=False`` when a more specific
        single modal (e.g. texture repair) will be shown instead.
        """
        warning_count = len(warnings or [])
        if kind == "vmd":
            message = tr_message_format(
                "vmd_import_partial",
                file_path=file_path,
                warning_count=warning_count,
            )
            title = tr_message("vmd_import_partial_title")
        else:
            message = tr_message_format(
                "import_partial",
                root_node=root_node if root_node is not None else file_path,
                warning_count=warning_count,
            )
            title = tr_message("import_partial_title")
        self.app_state.emit_status(message)
        if show_dialog:
            self._show_import_partial_warning(title, message, warnings)
        return message

    def import_file(self):
        file_path = self.view.import_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status(tr_message("enter_file_path"))
            return

        create_new_scene = hasattr(self.view, "new_file_check") and self.view.new_file_check.isChecked()
        is_vmd = file_path.lower().endswith(".vmd")
        vmd_target = self._get_vmd_target_model() if is_vmd else None
        if is_vmd and vmd_target is None:
            self.app_state.emit_status(tr_message("select_vmd_target_model"))
            return

        logger.info(f"Importing file: {file_path}")

        # 進捗開始
        self.app_state.emit_progress(0)
        self.app_state.emit_status(tr_message_format("importing_file", file_path=file_path))

        import_options = self._build_pmx_import_options()
        import_profile = {}
        if is_vmd:
            import_options.update(self._build_vmd_import_options(vmd_target))
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
            else:
                request = ImportModelRequest(
                    file_path=file_path,
                    options=import_options,
                    create_new_scene=create_new_scene,
                    progress_callback=self.app_state.emit_progress,
                )
                result = self.import_model_action.execute(request)

            if create_new_scene:
                logger.info("Created new file before import")

            outcome = self._resolve_import_outcome(result)
            if outcome == "fatal":
                # Fatal must not update current model, history, or success status.
                if result.error is not None:
                    logger.error("Import failed: %s", result.error)
                    self.app_state.emit_status(tr_message_format("import_error", error=str(result.error)))
                else:
                    logger.error("Import failed.")
                    self.app_state.emit_status(tr_message("import_failed"))
                self.app_state.emit_progress(0)
                return

            root_node = result.root_node
            # VMD never creates a model root; only model imports update selection.
            if not is_vmd:
                self.app_state.refresh_model_list()
                self.app_state.current_model_root = root_node
            self.app_state.emit_progress(100)
            if not is_vmd:
                self.refresh_model_list()
            self.view.add_import_path_to_history(file_path)

            if outcome == "partial":
                logger.warning("Import completed with warnings.")
                # Exactly one modal per import operation:
                # - texture-only partial → texture repair dialog (no generic modal)
                # - mixed warnings → generic summary, then actionable texture repair
                texture_only = (not is_vmd) and self._partial_warnings_are_texture_only(
                    getattr(result, "warnings", None)
                )
                self._present_import_partial_outcome(
                    result.warnings,
                    file_path=file_path,
                    root_node=root_node,
                    kind="vmd" if is_vmd else "model",
                    show_dialog=not texture_only,
                )
                if texture_only:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
                elif not is_vmd and any(
                    self._is_texture_issue_warning(item) for item in (getattr(result, "warnings", None) or [])
                ):
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            else:
                logger.info("Import successful.")
                self.app_state.emit_status(tr_message_format("import_complete_node", root_node=root_node))
                if not is_vmd:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            if not is_vmd:
                self._maybe_show_model_readme(root_node, file_path)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("import_error", error=str(e)))
            self.app_state.emit_progress(0)

    def export_file(self):
        if not self.settings_service.is_development_mode():
            # Export is intentionally develop-mode only; UI is also hidden in normal mode.
            self.app_state.emit_status(tr_message("export_dev_mode_required"))
            return

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
        if target_model is None:
            self.app_state.emit_status(tr_message("select_vmd_target_model"))
            return

        logger.info(f"Importing VMD file: {file_path}")
        if target_model:
            logger.debug(f"Target model: {target_model}")

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
            outcome = self._resolve_import_outcome(result)

            if outcome == "fatal":
                # Fatal must not add history or emit a success status.
                if result.error is not None:
                    logger.error("VMD import failed: %s", result.error)
                    self.app_state.emit_status(tr_message_format("vmd_import_error", error=str(result.error)))
                else:
                    logger.error("VMD import failed.")
                    self.app_state.emit_status(tr_message("vmd_import_failed"))
                self.app_state.emit_progress(0)
                return

            self.app_state.emit_progress(100)
            self.view.add_vmd_path_to_history(file_path)
            if outcome == "partial":
                logger.warning("VMD import completed with warnings.")
                self._present_import_partial_outcome(
                    result.warnings,
                    file_path=file_path,
                    root_node=result.root_node,
                    kind="vmd",
                )
            else:
                logger.info("VMD import successful.")
                self.app_state.emit_status(tr_message_format("vmd_import_complete", file_path=file_path))
        except Exception as e:
            logger.error(f"VMD import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("vmd_import_error", error=str(e)))
            self.app_state.emit_progress(0)
