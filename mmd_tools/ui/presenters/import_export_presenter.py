import math

from ..qt_compat import QObject, QFileDialog
from ...actions.create_model_action import (
    CreateModelActionError,
    CreateModelRequest,
    normalize_create_model_request,
)
from ...actions.import_model_action import ImportModelAction, ImportModelRequest
from ...actions.import_vmd_action import ImportVmdAction, ImportVmdRequest
from ...adapters.maya_cmds_adapter import MayaCmdsAdapter
from ...core import maya_attribute_utils, maya_material_utils
from ...core.constants import (
    ATTR_MMD_ORIGINAL_TEXTURE_PATH,
    ATTR_MMD_TEXTURE_CACHE_PATH,
)
from ...core.logger import get_logger
from ...core.model_registry import (
    REGISTRY_CATEGORY_TEXTURE,
    list_model_registry_members_from_adapter,
)
from ...core.model_template import list_model_templates
from ...services.settings_service import SettingsService
from ..create_model_dialog import CreateModelDialog
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
        settings_service=None,
        maya_adapter=None,
        model_readme_adapter=None,
        create_model_action=None,
        model_template_loader=None,
        create_model_dialog_factory=None,
    ):
        super().__init__()
        self.view = view
        self.app_state = app_state
        self.import_model_action = import_model_action or ImportModelAction()
        self.import_vmd_action = import_vmd_action or ImportVmdAction()
        self.settings_service = settings_service or SettingsService()
        self.maya_adapter = maya_adapter or MayaCmdsAdapter()
        self.model_readme_adapter = model_readme_adapter or ModelReadmeDialogAdapter(
            development_mode_getter=self.settings_service.is_development_mode,
        )
        self.create_model_action = create_model_action
        self.model_template_loader = model_template_loader or list_model_templates
        self.create_model_dialog_factory = create_model_dialog_factory or CreateModelDialog
        self._create_model_templates = ()
        self.view.presenter = self
        self.connect_signals()
        self._populate_create_model_templates()

    def connect_signals(self):
        self.view.import_path_button.clicked.connect(self.select_import_file)
        self.view.import_button.clicked.connect(self.import_file)
        # VMD import signals
        self.view.vmd_path_button.clicked.connect(self.select_vmd_file)
        self.view.import_vmd_button.clicked.connect(self.import_vmd_file)
        self.view.new_model_button.clicked.connect(self.open_create_model_dialog)

    def _populate_create_model_templates(self):
        """Load curated options and gate the New MMD Model button."""
        try:
            templates = []
            for template in tuple(self.model_template_loader() or ()):
                template_id = getattr(template, "template_id", None)
                label = getattr(template, "label", None)
                if isinstance(template_id, str) and template_id and isinstance(label, str):
                    templates.append(template)
        except Exception:
            logger.error("Failed to load packaged model templates", exc_info=True)
            templates = []
        self._create_model_templates = tuple(templates)
        action_available = callable(getattr(self.create_model_action, "execute", None))
        self.view.new_model_button.setEnabled(action_available and bool(self._create_model_templates))

    def _make_create_model_dialog(self):
        """Construct the injected dialog factory without loading scene data."""
        return self.create_model_dialog_factory(self._create_model_templates, self.view)

    def open_create_model_dialog(self):
        """Open the modal form and create only after the user accepts it."""
        if not callable(getattr(self.create_model_action, "execute", None)):
            return False
        if not self._create_model_templates:
            return False
        dialog = self._make_create_model_dialog()
        if not dialog.exec_modal():
            return False
        request = dialog.get_request()
        if request is None:
            self.app_state.emit_status(tr_message("create_model_template_required"))
            return False
        return self._execute_create_model_request(request)

    def _execute_create_model_request(self, request):
        """Execute one validated Create Model request and publish its new root."""
        action = self.create_model_action
        if not callable(getattr(action, "execute", None)):
            self.app_state.emit_status(tr_message("create_model_unavailable"))
            return False
        observed_type = type(request)
        try:
            request = normalize_create_model_request(request)
        except CreateModelActionError as exc:
            self.app_state.emit_status(
                tr_message_format("create_model_request_invalid", error=str(exc))
            )
            return False
        if observed_type is not CreateModelRequest:
            logger.warning(
                "Rehydrated CreateModelRequest after module reload: actual=%s.%s "
                "actual_class_id=%s current_class_id=%s template_id=%r",
                observed_type.__module__,
                observed_type.__qualname__,
                id(observed_type),
                id(CreateModelRequest),
                request.template_id,
            )
        template_id = request.template_id
        if not isinstance(template_id, str) or not template_id:
            self.app_state.emit_status(tr_message("create_model_template_required"))
            return False
        valid_template_ids = {
            template.template_id
            for template in self._create_model_templates
            if isinstance(getattr(template, "template_id", None), str)
        }
        if template_id not in valid_template_ids:
            self.app_state.emit_status(
                tr_message_format("create_model_template_unknown", template_id=template_id)
            )
            return False
        try:
            result = action.execute(request)
            root = getattr(result, "root", None)
            if not isinstance(root, str) or not root:
                raise RuntimeError("Create Model returned no model root")
            refresh_models = getattr(self.app_state, "refresh_model_list", None)
            if callable(refresh_models):
                refresh_models()
            self.app_state.current_model_root = root
            self.app_state.emit_status(
                tr_message_format("create_model_succeeded", root=root)
            )
            return True
        except Exception as exc:
            logger.error("Create Model failed", exc_info=True)
            self.app_state.emit_status(
                tr_message_format("create_model_failed", error=str(exc))
            )
            return False

    def select_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.view,
            "Select MMD File",
            "",
            "MMD Files (*.pmd *.pmx *.vmd);;All Files (*)",
        )
        if file_path:
            self.view.import_path_edit.setText(file_path)

    def select_vmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self.view, "Select VMD File", "", "VMD Files (*.vmd);;All Files (*)")
        if file_path:
            self.view.vmd_path_edit.setText(file_path)

    def _get_vmd_target_model(self):
        """VMD model motionの対象としてManagerの現在モデルを返す。"""
        target_model = getattr(self.app_state, "current_model_root", None)
        if not target_model:
            return None

        # ``current_model_root`` can outlive a deleted/replaced Maya scene.
        # Validate it against the scene's current model list before options
        # are built; a stale target must not be handed to VMD conversion.
        scene_service = getattr(self.app_state, "scene_model_service", None)
        list_models = getattr(scene_service, "list_mmd_models", None)
        if callable(list_models):
            try:
                # The cached ApplicationState list can describe the previous
                # scene when Maya reuses the same DAG path after replacement.
                # Always query the live scene service when available.
                available_models = list_models()
            except Exception:
                logger.debug("Could not validate VMD target model list", exc_info=True)
                return None
        else:
            available_models = getattr(self.app_state, "available_models", None)
        if available_models is not None and target_model not in available_models:
            return None

        object_exists = getattr(scene_service, "object_exists", None)
        if callable(object_exists):
            try:
                if not object_exists(target_model):
                    return None
            except Exception:
                logger.debug("Could not validate VMD target model", exc_info=True)
                return None
        return target_model

    def _build_vmd_import_options(self, target_model):
        """VMD import用のオプションを現在モデルから組み立てる。"""
        options = self.settings_service.build_vmd_import_options(target_model)
        if target_model is None:
            options.pop("target_model", None)
        return options

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

        registry_file_nodes = list_model_registry_members_from_adapter(
            self.maya_adapter,
            model_root,
            REGISTRY_CATEGORY_TEXTURE,
        )
        if registry_file_nodes is None:
            # Old scenes use an explicit root message owner so failed/
            # disconnected shader binds remain instance-scoped.  Do not use
            # this broad destination scan when a registry exists: new scenes
            # keep those links off the DAG root for selection performance.
            file_nodes.extend(
                self.maya_adapter.list_connections(
                    f"{model_root}.message",
                    source=False,
                    destination=True,
                    type="file",
                )
                or []
            )
        else:
            file_nodes.extend(registry_file_nodes)
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

    def _vmd_reduction_summary(self, profile):
        """Return a localized reduced-key summary when runtime reduction succeeded."""
        if not isinstance(profile, dict):
            return ""
        converter_profile = profile.get("vmd_converter")
        if not isinstance(converter_profile, dict):
            return ""
        reduction = converter_profile.get("reduced_bake_keys")
        if not isinstance(reduction, dict) or not reduction.get("used"):
            return ""
        try:
            source_key_count = int(reduction["source_key_count"])
            reduced_key_count = int(reduction["reduced_key_count"])
            reduction_ratio = float(reduction.get("reduction_ratio"))
        except (KeyError, TypeError, ValueError, OverflowError):
            return ""
        if source_key_count <= 0 or reduced_key_count < 0 or not math.isfinite(reduction_ratio):
            return ""
        reduction_percent = max(0.0, min(100.0, reduction_ratio * 100.0))
        return tr_message_format(
            "vmd_reduction_summary",
            source_key_count=source_key_count,
            reduced_key_count=reduced_key_count,
            reduction_percent=f"{reduction_percent:.1f}",
        )

    def _vmd_import_success_status(self, file_path, profile):
        """Build the normal VMD success status with an optional reduction summary."""
        message = tr_message_format("vmd_import_complete", file_path=file_path)
        summary = self._vmd_reduction_summary(profile)
        return f"{message} — {summary}" if summary else message

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
        profile=None,
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
        if kind == "vmd":
            summary = self._vmd_reduction_summary(profile)
            if summary:
                message = f"{message} — {summary}"
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
        # A new scene removes the Manager's current model before parsing. Leave
        # the target unset so camera/light-only VMD can proceed and model VMD
        # fails explicitly after content classification.
        vmd_target = self._get_vmd_target_model() if is_vmd and not create_new_scene else None

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
            if is_vmd:
                self.view.add_vmd_path_to_history(file_path)
            else:
                self.view.add_import_path_to_history(file_path)

            if outcome == "partial":
                logger.warning("Import completed with warnings.")
                # Exactly one modal per import operation:
                # - texture-only partial → texture repair dialog (no generic modal)
                # - mixed warnings → generic summary, then actionable texture repair
                texture_only = (not is_vmd) and self._partial_warnings_are_texture_only(
                    getattr(result, "warnings", None)
                )
                partial_kwargs = {
                    "file_path": file_path,
                    "root_node": root_node,
                    "kind": "vmd" if is_vmd else "model",
                    "show_dialog": not texture_only,
                }
                if is_vmd and import_options.get("profile") is not None:
                    partial_kwargs["profile"] = import_options.get("profile")
                self._present_import_partial_outcome(result.warnings, **partial_kwargs)
                if texture_only:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
                elif not is_vmd and any(
                    self._is_texture_issue_warning(item) for item in (getattr(result, "warnings", None) or [])
                ):
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            else:
                logger.info("Import successful.")
                if is_vmd:
                    self.app_state.emit_status(
                        self._vmd_import_success_status(file_path, import_options.get("profile"))
                    )
                else:
                    self.app_state.emit_status(tr_message_format("import_complete_node", root_node=root_node))
                if not is_vmd:
                    self._maybe_show_texture_issue_dialog(import_profile, file_path)
            if not is_vmd:
                self._maybe_show_model_readme(root_node, file_path)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("import_error", error=str(e)))
            self.app_state.emit_progress(0)

    def import_vmd_file(self):
        """VMDファイルのインポート"""
        file_path = self.view.vmd_path_edit.text().strip()
        if not file_path:
            self.app_state.emit_status(tr_message("enter_vmd_file_path"))
            return

        target_model = self._get_vmd_target_model()

        logger.info(f"Importing VMD file: {file_path}")
        if target_model:
            logger.debug(f"Current model: {target_model}")

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
                partial_kwargs = {
                    "file_path": file_path,
                    "root_node": result.root_node,
                    "kind": "vmd",
                }
                if animation_options.get("profile") is not None:
                    partial_kwargs["profile"] = animation_options.get("profile")
                self._present_import_partial_outcome(result.warnings, **partial_kwargs)
            else:
                logger.info("VMD import successful.")
                self.app_state.emit_status(
                    self._vmd_import_success_status(file_path, animation_options.get("profile"))
                )
        except Exception as e:
            logger.error(f"VMD import failed: {e}", exc_info=True)
            self.app_state.emit_status(tr_message_format("vmd_import_error", error=str(e)))
            self.app_state.emit_progress(0)
